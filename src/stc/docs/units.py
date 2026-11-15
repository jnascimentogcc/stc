"""Code units (functions and methods) and the calls between them, for applications that are not
written in COBOL (the languages tree-sitter reads).

COBOL has programs and CALLs; the other languages have definitions and call sites. A *unit* is a
function or method; its *calls* are the call sites inside it whose name is defined in the same
application. Resolution is by name, so it is a heuristic: a name defined once is resolved, a name
defined in the same file is preferred, and a very common or very ambiguous name is left
unresolved rather than guessed. Everything here is pure (no database), so it is easy to test.
"""

import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

DEFINITION_KINDS = ("function", "method")
CONTAINER_KINDS = frozenset(
    {"class", "interface", "trait", "enum", "struct", "module", "object", "record", "impl", "type"}
)
SYMBOL_KINDS = (*DEFINITION_KINDS, *sorted(CONTAINER_KINDS), "call")

MAX_AMBIGUOUS = 3  # a name defined in more than this many other files is not resolved
MAX_DEPTH = 6  # how far a feature follows the calls
MAX_UNITS = 60  # and how many units one feature may reach

# Names that are called on framework or library objects far more often than they are defined by
# the application: a call to one of them is linked only when the same file defines it.
COMMON_NAMES = frozenset(
    """get set all find first last create update delete destroy save store index show map filter
    push pop add remove count len init handle run execute render load open close read write send
    log info error debug warn json then catch next use exec query insert where select join split
    keys values items append extend format parse dump dumps loads reduce sort sorted min max sum
    abs print range str int float bool list dict tuple type id name has make fire emit on off
    call apply bind view redirect abort back with only except validate""".split()  # noqa: SIM905 - a long word list reads better as text
)

CONSTRUCTORS = frozenset({"__construct", "constructor", "__init__", "init", "new"})

# A unit that nothing calls and that starts something on its own (a console command, a queued job,
# a listener) is an entry point, recognised by its name and where it lives.
ENTRY_METHODS = frozenset(
    {"handle", "execute", "run", "main", "__invoke", "perform", "process", "__call__", "invoke"}
)
ENTRY_SUFFIXES = (
    "command", "job", "task", "worker", "listener", "consumer", "subscriber", "cron", "scheduler",
)  # fmt: skip
ENTRY_DIRECTORIES = frozenset(
    {"commands", "console", "jobs", "tasks", "workers", "cron", "listeners", "consumers"}
)


@dataclass(frozen=True)
class SymbolRow:
    path: str
    name: str
    kind: str
    is_definition: bool
    start: int
    end: int


@dataclass(frozen=True)
class Unit:
    key: str  # `path::Class.method`, unique in the application
    name: str  # the bare name
    label: str  # `Class.method` or `function`
    path: str
    start: int
    end: int
    owner_class: str | None = None
    inline: bool = False  # an anonymous handler: the code that follows a route registration

    @property
    def loc(self) -> str:
        return f"{self.path}:{self.start}-{self.start}"


@dataclass(frozen=True)
class UnitCall:
    caller: str  # unit key
    target: str  # unit key
    path: str
    line: int


@dataclass
class UnitGraph:
    units: dict[str, Unit] = field(default_factory=dict)
    calls: list[UnitCall] = field(default_factory=list)
    by_path: dict[str, list[Unit]] = field(default_factory=dict)  # sorted by start line
    by_name: dict[str, list[Unit]] = field(default_factory=dict)
    classes: dict[str, list[str]] = field(default_factory=dict)  # class name -> paths

    def __bool__(self) -> bool:
        return bool(self.units)

    def label(self, key: str) -> str:
        unit = self.units.get(key)
        return unit.label if unit is not None else key

    def callees(self, key: str) -> list[UnitCall]:
        return [c for c in self.calls if c.caller == key]

    def callers(self, key: str) -> list[UnitCall]:
        return [c for c in self.calls if c.target == key]

    def enclosing(self, path: str, line: int) -> Unit | None:
        """The innermost unit that contains this line."""
        best: Unit | None = None
        for unit in self.by_path.get(path, []):
            if unit.start <= line <= unit.end and (
                best is None or unit.end - unit.start <= best.end - best.start
            ):
                best = unit
        return best

    def reach(self, starts: Iterable[str]) -> list[str]:
        """Units reachable by calls from `starts` (themselves first), breadth first and bounded."""
        seen: dict[str, None] = {}
        queue: deque[tuple[str, int]] = deque((s, 0) for s in starts if s in self.units)
        while queue and len(seen) < MAX_UNITS:
            key, depth = queue.popleft()
            if key in seen:
                continue
            seen[key] = None
            if depth >= MAX_DEPTH:
                continue
            for call in self.callees(key):
                if call.target not in seen:
                    queue.append((call.target, depth + 1))
        return list(seen)


def _qualified(path: str, containers: list[SymbolRow], row: SymbolRow) -> str | None:
    """The smallest class around a definition, or None."""
    best: SymbolRow | None = None
    for c in containers:
        encloses = c.start <= row.start and row.end <= c.end
        smaller = best is None or c.end - c.start < best.end - best.start
        if encloses and (c.start, c.end) != (row.start, row.end) and smaller:
            best = c
    return best.name if best is not None else None


def build_graph(
    rows: Iterable[SymbolRow], *, skip: Callable[[str], bool] | None = None
) -> UnitGraph:
    """Units and resolved calls from the symbols of one application."""
    graph = UnitGraph()
    by_file: dict[str, list[SymbolRow]] = defaultdict(list)
    for row in rows:
        if skip is not None and skip(row.path):
            continue
        by_file[row.path].append(row)

    for path in sorted(by_file):
        file_rows = by_file[path]
        containers = [r for r in file_rows if r.is_definition and r.kind in CONTAINER_KINDS]
        for container in containers:
            graph.classes.setdefault(container.name, []).append(path)
        for row in file_rows:
            if not (row.is_definition and row.kind in DEFINITION_KINDS):
                continue
            cls = _qualified(path, containers, row)
            label = f"{cls}.{row.name}" if cls else row.name
            key = f"{path}::{label}"
            if key in graph.units:
                key = f"{key}#{row.start}"
            unit = Unit(key, row.name, label, path, row.start, row.end, cls)
            graph.units[key] = unit
            graph.by_path.setdefault(path, []).append(unit)
            graph.by_name.setdefault(row.name, []).append(unit)
    for units in graph.by_path.values():
        units.sort(key=lambda u: (u.start, u.end))

    seen: set[tuple[str, str, int]] = set()
    for path in sorted(by_file):
        for row in by_file[path]:
            if row.is_definition or row.kind != "call":
                continue
            caller = graph.enclosing(path, row.start)
            if caller is None:
                continue
            for target in _resolve(graph, row.name, caller):
                marker = (caller.key, target.key, row.start)
                if target.key != caller.key and marker not in seen:
                    seen.add(marker)
                    graph.calls.append(UnitCall(caller.key, target.key, path, row.start))
    return graph


def _resolve(graph: UnitGraph, callee: str, caller: Unit) -> list[Unit]:
    candidates = list(graph.by_name.get(callee, []))
    if callee in graph.classes:  # `new Foo()`: the constructor of the class
        candidates += [
            u for u in graph.units.values() if u.owner_class == callee and u.name in CONSTRUCTORS
        ]
    candidates = [c for c in candidates if c.key != caller.key]
    if not candidates:
        return []
    same_file = [c for c in candidates if c.path == caller.path]
    if same_file:
        same_class = [c for c in same_file if c.owner_class == caller.owner_class]
        return (same_class or same_file)[:MAX_AMBIGUOUS]
    if callee.lower() in COMMON_NAMES or len(candidates) > MAX_AMBIGUOUS:
        return []
    return candidates


# --------------------------------------------------------------------------------------------
# which code handles a route
# --------------------------------------------------------------------------------------------

_LARAVEL_ARRAY = re.compile(r"""\[\s*\\?(?:[\w\\]*\\)?(\w+)::class\s*,\s*['"](\w+)['"]""")
_AT_STRING = re.compile(r"""['"](?:[\w\\]*\\)?(\w+)@(\w+)['"]""")
_JS_HANDLER = re.compile(
    r"""\(\s*['"`][^'"`]*['"`]\s*,\s*(?:[\w.$]+\s*,\s*)*([A-Za-z_$][\w$]*)(?:\.([A-Za-z_$][\w$]*))?\s*\)"""
)
_DJANGO_VIEW = re.compile(
    r"""\bpath\(\s*r?['"][^'"]*['"]\s*,\s*(?:[\w]+\.)*(\w+)(?:\.as_view\()?"""
)


def handler_hint(text: str) -> tuple[str | None, str] | None:
    """(class or object, method or function) a route registration names, if it names one."""
    if m := _LARAVEL_ARRAY.search(text):
        return m[1], m[2]
    if m := _AT_STRING.search(text):
        return m[1], m[2]
    if m := _DJANGO_VIEW.search(text):
        return None, m[1]
    if m := _JS_HANDLER.search(text):
        return (m[1], m[2]) if m[2] else (None, m[1])
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def handlers_by_hint(graph: UnitGraph, hint: tuple[str | None, str]) -> list[Unit]:
    owner, name = hint
    if owner is None:
        named = list(graph.by_name.get(name, []))
        if named:
            return named[:MAX_AMBIGUOUS]
        # a class-based view: every method of the class
        return [u for u in graph.units.values() if u.owner_class == name]
    wanted = _norm(owner)
    exact = [
        u for u in graph.by_name.get(name, []) if u.owner_class and _norm(u.owner_class) == wanted
    ]
    if exact:
        return exact
    loose = [
        u
        for u in graph.by_name.get(name, [])
        if u.owner_class and (wanted in _norm(u.owner_class) or _norm(u.owner_class) in wanted)
    ]
    return loose[:MAX_AMBIGUOUS]


def handlers_near(graph: UnitGraph, path: str, line: int, window: int = 5) -> list[Unit]:
    """The unit around a route marker: the one that contains it (a decorated method) or the one
    that starts right below it (a Python decorator, a PHP attribute)."""
    inside = graph.enclosing(path, line)
    if inside is not None:
        return [inside]
    below = [u for u in graph.by_path.get(path, []) if line < u.start <= line + window]
    return below[:1]


def with_inline_handlers(graph: UnitGraph, path: str, lines: list[int]) -> dict[int, Unit]:
    """For routes with no named handler (an anonymous callback), a pseudo-unit that spans from the
    route registration to the next route or definition, so the calls inside it count."""
    marks = sorted(set(lines))
    starts = [u.start for u in graph.by_path.get(path, [])]
    made: dict[int, Unit] = {}
    for index, line in enumerate(marks):
        later = [n for n in (*marks[index + 1 :], *starts) if n > line]
        end = (min(later) - 1) if later else line + 40
        unit = Unit(f"{path}::@{line}", f"@{line}", f"route at line {line}", path, line, end,
                    inline=True)  # fmt: skip
        made[line] = unit
    return made


def add_inline(graph: UnitGraph, rows: Iterable[SymbolRow], units: Iterable[Unit]) -> None:
    """Register pseudo-units and attach the calls inside their span."""
    added = list(units)
    for unit in added:
        graph.units[unit.key] = unit
        graph.by_path.setdefault(unit.path, []).append(unit)
    seen = {(c.caller, c.target, c.line) for c in graph.calls}
    for row in rows:
        if row.is_definition or row.kind != "call":
            continue
        for unit in added:
            if unit.path == row.path and unit.start <= row.start <= unit.end:
                for target in _resolve(graph, row.name, unit):
                    marker = (unit.key, target.key, row.start)
                    if marker not in seen:
                        seen.add(marker)
                        graph.calls.append(UnitCall(unit.key, target.key, row.path, row.start))


# --------------------------------------------------------------------------------------------
# entry points that are not routes
# --------------------------------------------------------------------------------------------


def background_entries(graph: UnitGraph, excluded: set[str]) -> list[Unit]:
    """Units that nothing calls and that look like a command, job, listener or script entry."""
    called = {c.target for c in graph.calls}
    found: list[Unit] = []
    for unit in graph.units.values():
        if unit.inline or unit.key in called or unit.key in excluded:
            continue
        directories = {p.lower() for p in unit.path.split("/")[:-1]}
        suffix = (unit.owner_class or "").lower().endswith(ENTRY_SUFFIXES)
        in_dir = bool(ENTRY_DIRECTORIES & directories)
        if unit.name in ENTRY_METHODS and (suffix or in_dir):
            found.append(unit)
    return found
