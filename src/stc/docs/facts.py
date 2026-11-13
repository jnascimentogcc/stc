"""What the index knows about one application, as plain data (no LLM).

Everything the documentation states as a fact (which job runs which program, which program calls
which, what each one reads and writes, the paragraphs that perform one another, the endpoints, the
relations with other applications) is read here from the symbols, signals and relations that the
indexer stored, so the tables and diagrams built from it cannot contain anything the code does not.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg

_STATUS = "f.status IN ('indexed', 'unsupported')"
_WRITES = frozenset({"WRITES", "INSERT", "UPDATE", "DELETE", "MERGE"})


@dataclass(frozen=True)
class Loc:
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}-{self.line}"


@dataclass(frozen=True)
class Owner:
    """The unit of code a signal belongs to: a program, or a job (and its step)."""

    kind: str  # program | job
    name: str
    step: str | None = None


@dataclass(frozen=True)
class Call:
    caller: Owner
    target: str
    dynamic: bool
    loc: Loc


@dataclass(frozen=True)
class TableUse:
    owner: Owner
    table: str
    action: str  # SELECT, INSERT, UPDATE, DELETE, ...
    loc: Loc

    @property
    def writes(self) -> bool:
        return self.action in _WRITES


@dataclass(frozen=True)
class DatasetUse:
    owner: Owner
    dataset: str
    writes: bool
    loc: Loc


@dataclass(frozen=True)
class CopyUse:
    owner: Owner
    copybook: str
    loc: Loc


@dataclass(frozen=True)
class Span:
    name: str
    kind: str  # program | job | step | section | paragraph
    start: int
    end: int


@dataclass(frozen=True)
class Perform:
    program: str
    source: str | None  # the paragraph that performs, when known
    target: str
    loc: Loc


@dataclass(frozen=True)
class Relation:
    other: str
    outgoing: bool  # this application -> other
    type: str
    confidence: str
    detail: str
    evidence: str


@dataclass(frozen=True)
class Endpoint:
    detail: str
    loc: Loc


@dataclass(frozen=True)
class Topic:
    role: str
    key: str
    loc: Loc


@dataclass
class AppFacts:
    name: str
    path: str
    code_version: str | None
    languages: list[tuple[str, int]] = field(default_factory=list)
    directories: list[tuple[str, int]] = field(default_factory=list)
    programs: dict[str, Loc] = field(default_factory=dict)  # PROGRAM-ID -> where
    jobs: dict[str, Loc] = field(default_factory=dict)
    steps: dict[str, list[Span]] = field(default_factory=dict)  # job -> steps in order
    calls: list[Call] = field(default_factory=list)
    tables: list[TableUse] = field(default_factory=list)
    datasets: list[DatasetUse] = field(default_factory=list)
    copybook_uses: list[CopyUse] = field(default_factory=list)
    copybooks: dict[str, Loc] = field(default_factory=dict)  # defined in this application
    paragraphs: dict[str, list[Span]] = field(
        default_factory=dict
    )  # program -> sections/paragraphs
    performs: list[Perform] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    consumed: list[Endpoint] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    unresolved: list[Call] = field(default_factory=list)  # dynamic calls
    ddl_files: list[str] = field(default_factory=list)
    file_of_program: dict[str, str] = field(default_factory=dict)
    file_of_job: dict[str, str] = field(default_factory=dict)

    @property
    def is_mainframe(self) -> bool:
        return bool(self.programs or self.jobs)

    def program_calls(self, program: str) -> list[Call]:
        return [c for c in self.calls if c.caller.kind == "program" and c.caller.name == program]

    def job_calls(self, job: str) -> list[Call]:
        return [c for c in self.calls if c.caller.kind == "job" and c.caller.name == job]


def _owner_of(
    path: str,
    line: int,
    programs_in: dict[str, list[tuple[int, str]]],
    job_in: dict[str, str],
    steps_in: dict[str, list[Span]],
) -> Owner | None:
    job = job_in.get(path)
    if job is not None:
        step = next((s.name for s in steps_in.get(path, []) if s.start <= line <= s.end), None)
        return Owner("job", job, step)
    found = programs_in.get(path)
    if found:
        before = [name for start, name in found if start <= line]
        return Owner("program", before[-1] if before else found[0][1])
    return None


def _label(detail: str) -> str:
    """The SQL verb of a table signal (`SQL SELECT CONTRACT` -> `SELECT`)."""
    parts = detail.split()
    return parts[1].upper() if len(parts) >= 3 and parts[0].upper() == "SQL" else "ACCESS"


def build_facts(conn: psycopg.Connection, app_id: int, name: str) -> AppFacts:
    row = conn.execute(
        "SELECT path, indexed_version FROM applications WHERE id = %s", (app_id,)
    ).fetchone()
    assert row is not None
    facts = AppFacts(name, str(row[0]), row[1])
    facts.languages = [
        (str(lang), int(n))
        for lang, n in conn.execute(
            "SELECT COALESCE(language, 'unknown'), count(*) FROM files f WHERE application_id = %s"
            f" AND {_STATUS} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12",
            (app_id,),
        )
    ]
    facts.directories = [
        (str(top), int(n))
        for top, n in conn.execute(
            "SELECT split_part(path, '/', 1), count(*) FROM files f WHERE application_id = %s"
            f" AND {_STATUS} AND position('/' in path) > 0 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25",
            (app_id,),
        )
    ]
    symbols = conn.execute(
        "SELECT f.path, s.name, s.kind, s.is_definition, s.start_line, s.end_line"
        " FROM symbols s JOIN files f ON f.id = s.file_id"
        f" WHERE f.application_id = %s AND {_STATUS} AND s.kind IN"
        " ('program', 'job', 'step', 'section', 'paragraph', 'perform')"
        " ORDER BY f.path, s.start_line, s.name",
        (app_id,),
    ).fetchall()
    signals = conn.execute(
        "SELECT s.kind, s.role, s.key, s.detail, f.path, s.start_line FROM signals s"
        " JOIN files f ON f.id = s.file_id WHERE s.application_id = %s"
        " ORDER BY f.path, s.start_line, s.kind, s.key",
        (app_id,),
    ).fetchall()

    programs_in: dict[str, list[tuple[int, str]]] = defaultdict(list)
    job_in: dict[str, str] = {}
    steps_in: dict[str, list[Span]] = defaultdict(list)
    paragraphs_in: dict[str, list[Span]] = defaultdict(list)
    raw_performs: list[tuple[str, str, int]] = []  # path, target, line
    for path, sname, kind, is_def, start, end in symbols:
        path, sname = str(path), str(sname)
        if kind == "program" and is_def:
            programs_in[path].append((int(start), sname.upper()))
            facts.programs.setdefault(sname.upper(), Loc(path, int(start)))
            facts.file_of_program.setdefault(sname.upper(), path)
        elif kind == "job" and is_def:
            job_in.setdefault(path, sname.upper())
            facts.jobs.setdefault(sname.upper(), Loc(path, int(start)))
            facts.file_of_job.setdefault(sname.upper(), path)
        elif kind == "step" and is_def:
            steps_in[path].append(Span(sname.upper(), "step", int(start), int(end)))
        elif kind in ("section", "paragraph") and is_def:
            paragraphs_in[path].append(Span(sname.upper(), kind, int(start), int(end)))
        elif kind == "perform" and not is_def:
            raw_performs.append((path, sname.upper(), int(start)))
    # A program defined by a signal but not by a symbol (should not happen) is still a program.
    for kind, role, key, _detail, path, line in signals:
        if kind == "program" and role == "provides" and str(key) not in facts.programs:
            programs_in[str(path)].append((int(line), str(key)))
            facts.programs[str(key)] = Loc(str(path), int(line))
            facts.file_of_program[str(key)] = str(path)
    for lst in programs_in.values():
        lst.sort()
    for job_path, job in job_in.items():
        facts.steps[job] = steps_in.get(job_path, [])
    for path, spans in paragraphs_in.items():
        program = next(iter(sorted(programs_in.get(path, []))), (0, None))[1]
        if program is not None:
            facts.paragraphs[program] = spans
    for path, target, line in raw_performs:
        found = programs_in.get(path)
        if not found:
            continue
        program = found[0][1]
        enclosing = [
            s
            for s in paragraphs_in.get(path, [])
            if s.kind == "paragraph" and s.start <= line <= s.end
        ]
        source = min(enclosing, key=lambda s: s.end - s.start).name if enclosing else None
        facts.performs.append(Perform(program, source, target, Loc(path, line)))

    for kind, role, key, detail, path, line in signals:
        path, key, detail, line = str(path), str(key), str(detail), int(line)
        loc = Loc(path, line)
        if kind == "program" and role == "consumes":
            owner = _owner_of(path, line, programs_in, job_in, steps_in)
            if owner is None:
                continue
            dynamic = key.startswith("?")
            call = Call(owner, key.lstrip("?"), dynamic, loc)
            (facts.unresolved if dynamic else facts.calls).append(call)
        elif kind == "table":
            if role == "provides":
                if path not in facts.ddl_files:
                    facts.ddl_files.append(path)
                continue
            owner = _owner_of(path, line, programs_in, job_in, steps_in)
            if owner is not None:
                facts.tables.append(TableUse(owner, key, _label(detail), loc))
        elif kind == "dataset":
            owner = _owner_of(path, line, programs_in, job_in, steps_in)
            if owner is not None:
                facts.datasets.append(DatasetUse(owner, key, role == "provides", loc))
        elif kind == "copybook":
            if role == "provides":
                facts.copybooks.setdefault(key, loc)
            else:
                owner = _owner_of(path, line, programs_in, job_in, steps_in)
                if owner is not None:
                    facts.copybook_uses.append(CopyUse(owner, key, loc))
        elif kind == "http":
            (facts.endpoints if role == "provides" else facts.consumed).append(
                Endpoint(detail, loc)
            )
        elif kind == "messaging":
            facts.topics.append(Topic(role, key, loc))
        elif kind == "database" and key not in facts.databases:
            facts.databases.append(key)

    facts.relations = [
        Relation(
            str(other),
            bool(out),
            str(kind),
            str(conf),
            str(detail or ""),
            _evidence(owner, path, a, b),
        )
        for other, out, kind, conf, detail, owner, path, a, b in conn.execute(
            "SELECT CASE WHEN r.source_app_id = %(a)s THEN t.name ELSE s.name END,"
            " r.source_app_id = %(a)s, r.type, r.confidence, r.detail, fa.name, f.path,"
            " r.evidence_start_line, r.evidence_end_line"
            " FROM active_relations r JOIN applications s ON s.id = r.source_app_id"
            " JOIN applications t ON t.id = r.target_app_id"
            " LEFT JOIN files f ON f.id = r.evidence_file_id"
            " LEFT JOIN applications fa ON fa.id = f.application_id"
            " WHERE r.source_app_id = %(a)s OR r.target_app_id = %(a)s ORDER BY 1, 3, 2",
            {"a": app_id},
        )
    ]
    return facts


def _evidence(owner: object, path: object, start: object, end: object) -> str:
    if path is None:
        return ""
    return f"{owner}/{path}:{start}-{end}"


_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "item"
