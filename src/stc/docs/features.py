"""Detects the features of an application from its entry points (DOC-03), without a model.

A feature is what the application does when something starts it: a batch job and the programs its
steps run, a program that nothing calls (an online transaction, an entry called from another
application), the HTTP endpoints one controller or one route file handles, a command or background
job, or the consumer of a topic. The code each feature involves is found by following the calls
(CALL in COBOL, call sites between functions and methods in the other languages), so the model is
only asked to describe it.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field

from stc.docs.facts import AppFacts, Endpoint, Loc, Topic, slugify
from stc.docs.screens import screens_used_by
from stc.docs.units import background_entries


@dataclass
class Feature:
    slug: str
    title: str
    kind: str  # job | program | endpoints | task | consumer
    entry: str  # job or program name, or the file for endpoints
    entry_loc: Loc
    jobs: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)  # reachable from the entry, entry first
    files: list[str] = field(default_factory=list)  # every file the feature involves
    endpoints: list[Endpoint] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    units: list[str] = field(default_factory=list)  # functions/methods reached, entries first
    handlers: dict[str, list[str]] = field(default_factory=dict)  # endpoint -> handler units
    screens: list[str] = field(default_factory=list)  # screens the programs show or read

    @property
    def size(self) -> int:
        return (
            len(self.files) * 2
            + len(self.programs)
            + len(self.units)
            + len(self.endpoints)
            + len(self.topics)
            + len(self.screens)
        )


def _reach(facts: AppFacts, starts: list[str]) -> list[str]:
    """Programs of this application reachable by calls from `starts`, in discovery order."""
    seen: dict[str, None] = {}
    queue = deque(s for s in starts if s in facts.programs)
    while queue:
        program = queue.popleft()
        if program in seen:
            continue
        seen[program] = None
        for call in facts.program_calls(program):
            if call.target in facts.programs and call.target not in seen:
                queue.append(call.target)
    return list(seen)


def _unique(slug: str, taken: set[str]) -> str:
    candidate, n = slug, 2
    while candidate in taken:
        candidate, n = f"{slug}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _unit_files(facts: AppFacts, units: list[str], extra: list[str]) -> list[str]:
    paths = [*extra, *(facts.graph.units[k].path for k in units if k in facts.graph.units)]
    return list(dict.fromkeys(paths))


def _endpoint_features(facts: AppFacts) -> list[Feature]:
    """The endpoints one controller (or, with no class, one route file) handles, and everything
    their handlers call. Endpoints whose handler is not known are grouped by file, as before."""
    graph = facts.graph
    groups: dict[tuple[str, str], list[Endpoint]] = defaultdict(list)
    for endpoint in facts.endpoints:
        handlers = [
            graph.units[k] for k in facts.handlers.get(str(endpoint.loc), []) if k in graph.units
        ]
        owner = next((u.owner_class for u in handlers if u.owner_class), None)
        if owner is not None:
            groups[(owner, handlers[0].path)].append(endpoint)
        else:
            groups[("", endpoint.loc.path)].append(endpoint)

    found: list[Feature] = []
    for (owner, path), endpoints in sorted(groups.items()):
        handled = {
            str(e.loc): facts.handlers[str(e.loc)]
            for e in endpoints
            if str(e.loc) in facts.handlers
        }
        entries = [k for keys in handled.values() for k in keys]
        units = graph.reach(entries)
        route_files = [e.loc.path for e in endpoints]
        title, entry = (f"Endpoints of {owner}", owner) if owner else (f"Endpoints in {path}", path)
        found.append(
            Feature(
                "", title, "endpoints", entry, endpoints[0].loc,
                files=_unit_files(facts, units, route_files) or [path],
                endpoints=endpoints, units=units, handlers=handled,
            )
        )  # fmt: skip
    return found


def _task_features(facts: AppFacts, covered: set[str]) -> list[Feature]:
    """Commands, queued jobs, listeners and scripts: units that nothing calls and that start on
    their own, found by their name and where they live."""
    graph = facts.graph
    found: list[Feature] = []
    for unit in background_entries(graph, covered):
        units = graph.reach([unit.key])
        label = unit.owner_class or unit.label
        found.append(
            Feature(
                "", f"Background task {label}", "task", unit.key, Loc(unit.path, unit.start),
                files=_unit_files(facts, units, []), units=units,
            )
        )  # fmt: skip
    return found


def detect_features(facts: AppFacts, limit: int) -> tuple[list[Feature], list[Feature]]:
    """(documented, skipped): the biggest features up to `limit`, then the rest."""
    found: list[Feature] = []
    covered: set[str] = set()

    for job in sorted(facts.jobs):
        steps = [c.target for c in facts.job_calls(job)]
        programs = _reach(facts, steps)
        covered.update(programs)
        files = [facts.file_of_job[job]] + [facts.file_of_program[p] for p in programs]
        found.append(
            Feature(
                "", f"Batch job {job}", "job", job, facts.jobs[job],
                jobs=[job], programs=programs, files=list(dict.fromkeys(files)),
            )
        )  # fmt: skip

    called = {c.target for c in facts.calls}
    for program in sorted(facts.programs):
        if program in called or program in covered:
            continue
        programs = _reach(facts, [program])
        covered.update(programs)
        files = [facts.file_of_program[p] for p in programs]
        found.append(
            Feature(
                "", f"Program {program}", "program", program, facts.programs[program],
                programs=programs, files=list(dict.fromkeys(files)),
            )
        )  # fmt: skip

    found += _endpoint_features(facts)
    found += _task_features(facts, {k for f in found for k in f.units})

    topics: dict[str, list[Topic]] = defaultdict(list)
    for topic in facts.topics:
        if topic.role == "subscribes":
            topics[topic.loc.path].append(topic)
    for path in sorted(topics):
        found.append(
            Feature(
                "", f"Consumer in {path}", "consumer", path, topics[path][0].loc,
                files=[path], topics=topics[path],
            )
        )  # fmt: skip

    for feature in found:
        if feature.programs:
            feature.screens = screens_used_by(facts, feature.programs)
            defined = [facts.screens[s].loc.path for s in feature.screens]
            feature.files = list(dict.fromkeys([*feature.files, *defined]))

    found.sort(key=lambda f: (-f.size, f.title))
    taken: set[str] = set()
    for feature in found:
        feature.slug = _unique(slugify(f"{feature.kind}-{feature.entry}"), taken)
    cap = max(0, limit)
    return found[:cap], found[cap:]
