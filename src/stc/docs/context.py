"""The evidence the model may use for each part of the documentation.

Code, not the model, decides what is relevant: for a feature it is the code of the programs it
runs, in the order the calls reach them; for the data model the DDL and the copybook records; for
operations the JCL and the build files. Everything is read from the database, so the Root need not
be mounted, and every item carries `path:lines` so claims can cite it and be verified."""

import psycopg

from stc.catalog.context import (
    ContextItem,
    SectionContext,
    _chunks,
    _manifest_where,
    _pack,
)
from stc.docs.components import component_links, components, layer_links
from stc.docs.facts import WRITES, AppFacts, Loc
from stc.docs.features import Feature
from stc.docs.units import background_entries

_STATUS = "f.status IN ('indexed', 'unsupported')"
_DECISIONS_MAINFRAME = r"(EVALUATE|\mIF\M|COMPUTE|\mWHEN\M|\mPERFORM\M.*\mUNTIL\M)"
_DECISIONS_GENERIC = (
    r"(\mif\M|\melse\M|\mswitch\M|\mcase\M|\mraise\M|\mthrow\M|\mvalidate|>=|<=|==|\mwhere\M)"
)
MAX_FACTS = 160
# Code that schedules work or queues it, and the files that run an application in production.
_SCHEDULING = (
    r"(\$schedule->|beat_schedule|crontab\(|cron\.schedule|new CronJob|node-cron|add_job\(|"
    r"BackgroundScheduler|ShouldQueue|onQueue\(|queue\.process|new Queue\(|@shared_task|@celery)"
)
_PIPELINE_PATH = (
    r"(^|/)(\.github/workflows/[^/]+|\.gitlab-ci\.yml|Procfile|Jenkinsfile|supervisord[^/]*|"
    r"crontab|\.circleci/config\.yml|azure-pipelines\.yml)$"
)


def _file_chunks(
    conn: psycopg.Connection,
    app_id: int,
    paths: list[str],
    *,
    kinds: list[str] | None = None,
    regex: str | None = None,
    limit: int = 80,
) -> list[ContextItem]:
    """Chunks of the given files, in the order of `paths`, then by line."""
    if not paths:
        return []
    where = ["f.application_id = %(app)s", f"{_STATUS}", "f.path = ANY(%(paths)s)"]
    if kinds:
        where.append("c.kind = ANY(%(kinds)s)")
    if regex:
        where.append("c.content ~* %(regex)s")
    rows = conn.execute(
        "SELECT f.path, c.start_line, c.end_line, c.kind, c.name, c.content"
        " FROM chunks c JOIN files f ON f.id = c.file_id WHERE "
        + " AND ".join(where)
        + " ORDER BY array_position(%(paths)s::text[], f.path), c.start_line LIMIT %(limit)s",
        {"app": app_id, "paths": paths, "kinds": kinds, "regex": regex, "limit": limit},
    ).fetchall()
    return [
        ContextItem(str(p), int(a), int(b), f"{k} {n}" if n else str(k), str(t))
        for p, a, b, k, n, t in rows
    ]


def _merge(*groups: list[ContextItem]) -> list[ContextItem]:
    seen: set[str] = set()
    merged: list[ContextItem] = []
    for item in (i for group in groups for i in group):
        if item.label not in seen:
            seen.add(item.label)
            merged.append(item)
    return merged


def _at(loc: Loc) -> str:
    return f"at {loc.path}:{loc.line}"


def call_facts(facts: AppFacts, jobs: set[str] | None, programs: set[str] | None) -> list[str]:
    out: list[str] = []
    for job in sorted(facts.jobs):
        if jobs is not None and job not in jobs:
            continue
        out.append(f"Job {job} is defined {_at(facts.jobs[job])}")
        for call in facts.job_calls(job):
            kind = "program" if call.target in facts.programs else "utility or external program"
            out.append(
                f"Job {job} step {call.caller.step or '?'} runs {kind} {call.target} "
                f"{_at(call.loc)}"
            )  # noqa: E501
    for call in facts.calls:
        if call.caller.kind == "program" and (programs is None or call.caller.name in programs):
            out.append(f"Program {call.caller.name} calls {call.target} {_at(call.loc)}")
    for call in facts.unresolved:
        if call.caller.kind == "program" and (programs is None or call.caller.name in programs):
            out.append(
                f"Program {call.caller.name} makes a dynamic call ({call.target}) {_at(call.loc)}"
            )  # noqa: E501
    for program in sorted(facts.programs):
        if programs is None or program in programs:
            out.append(f"Program {program} is defined {_at(facts.programs[program])}")
    return out


def unit_facts(facts: AppFacts, units: set[str]) -> list[str]:
    """Which function or method calls which (an application not written in COBOL)."""
    graph = facts.graph
    out: list[str] = []
    for key in sorted(units, key=lambda k: (graph.units[k].path, graph.units[k].start)):
        unit = graph.units[key]
        out.append(f"{unit.label} is defined {_at(Loc(unit.path, unit.start))}")
        for call in graph.callees(key):
            out.append(
                f"{unit.label} calls {graph.label(call.target)} {_at(Loc(call.path, call.line))}"
            )
    return out


def data_facts(
    facts: AppFacts,
    jobs: set[str] | None,
    programs: set[str] | None,
    units: set[str] | None = None,
) -> list[str]:
    out: list[str] = []
    for use in facts.tables:
        if use.owner.kind == "program" and (programs is None or use.owner.name in programs):
            owner, table = use.owner.name, use.table
            out.append(f"Program {owner} does SQL {use.action} on table {table} {_at(use.loc)}")
        elif use.owner.kind == "unit" and (units is None or use.owner.name in units):
            who = facts.graph.label(use.owner.name)
            verb = "writes to" if use.action in WRITES else "reads from"
            out.append(f"{who} {verb} table {use.table} {_at(use.loc)}")
    for ds in facts.datasets:
        if ds.owner.kind == "job" and (jobs is None or ds.owner.name in jobs):
            verb = "writes" if ds.writes else "reads"
            step = ds.owner.step or "?"
            out.append(f"Job {ds.owner.name} step {step} {verb} dataset {ds.dataset} {_at(ds.loc)}")
    for copy in facts.copybook_uses:
        if copy.owner.kind == "program" and (programs is None or copy.owner.name in programs):
            out.append(f"Program {copy.owner.name} uses copybook {copy.copybook} {_at(copy.loc)}")
    return out


def perform_facts(facts: AppFacts, programs: set[str] | None) -> list[str]:
    return [
        f"Paragraph {p.source or '(main)'} of {p.program} performs {p.target} {_at(p.loc)}"
        for p in facts.performs
        if programs is None or p.program in programs
    ]


def feature_facts(facts: AppFacts, feature: Feature) -> list[str]:
    jobs, programs = set(feature.jobs), set(feature.programs)
    out = [
        *call_facts(facts, jobs, programs),
        *unit_facts(facts, set(feature.units)),
        *data_facts(facts, jobs, programs, set(feature.units)),
        *perform_facts(facts, set(feature.programs[:1])),
    ]
    for endpoint in feature.endpoints:
        handlers = ", ".join(
            facts.graph.label(k) for k in feature.handlers.get(str(endpoint.loc), [])
        )
        by = f" and is handled by {handlers}" if handlers else ""
        out.append(f"Endpoint {endpoint.detail} is defined {_at(endpoint.loc)}{by}")
    out += [f"Topic {t.key} ({t.role}) {_at(t.loc)}" for t in feature.topics]
    return out[:MAX_FACTS]


def unit_chunks(
    conn: psycopg.Connection,
    app_id: int,
    facts: AppFacts,
    feature: Feature,
    *,
    kinds: list[str] | None = None,
    limit: int = 80,
) -> list[ContextItem]:
    """The chunks that hold the route registrations and the functions and methods a feature
    reaches, entry points first: not the rest of the files they live in."""
    if not feature.units:
        return []
    by_path: dict[str, list[ContextItem]] = {}
    for item in _file_chunks(conn, app_id, feature.files, kinds=kinds, limit=600):
        by_path.setdefault(item.path, []).append(item)
    out: list[ContextItem] = []
    for endpoint in feature.endpoints:
        loc = endpoint.loc
        out += [i for i in by_path.get(loc.path, []) if i.start_line <= loc.line <= i.end_line]
    for key in feature.units:
        unit = facts.graph.units.get(key)
        if unit is not None:
            out += [
                i
                for i in by_path.get(unit.path, [])
                if i.start_line <= unit.end and i.end_line >= unit.start
            ]
    return _merge(out)[:limit]


def header_kinds(feature: Feature) -> list[str]:
    """The chunks that say what a unit of the feature is: program and job headers in COBOL, the
    classes, functions and methods in the other languages."""
    return (
        ["program", "job"]
        if feature.kind in ("job", "program")
        else ["class", "function", "method"]
    )


def feature_contexts(
    conn: psycopg.Connection, app_id: int, facts: AppFacts, feature: Feature
) -> dict[str, SectionContext]:
    """summary, flow and rules of one feature."""
    paths = feature.files
    shared = feature_facts(facts, feature)
    mainframe = feature.kind in ("job", "program")
    kinds = header_kinds(feature)
    headers = unit_chunks(conn, app_id, facts, feature, kinds=kinds, limit=12) or _file_chunks(
        conn, app_id, paths, kinds=kinds, limit=12
    )
    focus = unit_chunks(conn, app_id, facts, feature)
    everything = _merge(focus, _file_chunks(conn, app_id, paths, limit=120))
    rules = _file_chunks(
        conn, app_id, paths,
        regex=_DECISIONS_MAINFRAME if mainframe else _DECISIONS_GENERIC, limit=60,
    )  # fmt: skip
    if focus:  # the decisions of the code the feature runs, not of its neighbours in the files
        in_focus = {i.label for i in focus}
        rules = [r for r in rules if r.label in in_focus] or rules
    return {
        "summary": SectionContext(_pack(_merge(headers, everything)), shared[:60]),
        "flow": SectionContext(_pack(_merge(headers, everything)), shared),
        "rules": SectionContext(_pack(rules), shared[:40] if rules else []),
    }


def entities_context(
    conn: psycopg.Connection, app_id: int, facts: AppFacts, base: SectionContext
) -> SectionContext:
    ddl = _file_chunks(conn, app_id, facts.ddl_files, limit=40)
    records = _chunks(conn, app_id, "c.kind = 'record'", 30)
    items = _pack(_merge(base.items, ddl, records))
    lines = [
        *data_facts(facts, None, None),
        *(
            f"Copybook {name} is defined {_at(loc)}"
            for name, loc in sorted(facts.copybooks.items())
        ),
    ]
    return SectionContext(items, lines[:MAX_FACTS])


def operations_context(conn: psycopg.Connection, app_id: int, facts: AppFacts) -> SectionContext:
    jcl = _chunks(conn, app_id, "c.kind IN ('job', 'step')", 30)
    build = _chunks(conn, app_id, _manifest_where(), 8, per_file=2)
    scheduling = _chunks(conn, app_id, f"c.content ~ '{_SCHEDULING}'", 12, per_file=2)
    pipelines = _chunks(conn, app_id, f"f.path ~* '{_PIPELINE_PATH}'", 8, per_file=2)
    lines = [*call_facts(facts, set(facts.jobs), set()), *data_facts(facts, set(facts.jobs), set())]
    for unit in background_entries(facts.graph, set()):
        where = _at(Loc(unit.path, unit.start))
        lines.append(f"{unit.label} starts on its own (command, job or listener) {where}")
    return SectionContext(_pack(_merge(jcl, build, scheduling, pipelines)), lines[:MAX_FACTS])


def component_facts(facts: AppFacts) -> list[str]:
    """Which classes call which, and which folders (layers) depend on which."""
    found = components(facts)
    if not found:
        return []
    links = component_links(facts, found)
    out = [
        f"Layer {a} calls layer {b} {n} time{'s' * (n > 1)}"
        for (a, b), n in layer_links(facts, found).most_common(30)
    ]
    for key, comp in sorted(found.items(), key=lambda kv: (kv[1].path, kv[1].line)):
        callees = ", ".join(found[k].name for k in sorted(links.calls[key])[:8])
        where = _at(Loc(comp.path, comp.line))
        out.append(
            f"Component {comp.name} (layer {comp.layer}, {len(comp.units)} functions or methods) "
            f"{where}" + (f" calls {callees}" if callees else "")
        )
    return out


def structure_context(
    conn: psycopg.Connection, app_id: int, facts: AppFacts, base: SectionContext
) -> SectionContext:
    headers = _chunks(conn, app_id, "c.kind IN ('program', 'job', 'class')", 25)
    lines = [
        *base.facts,
        *component_facts(facts),
        *call_facts(facts, None, None),
        *perform_facts(facts, None)[:40],
    ]
    return SectionContext(
        _pack(_merge(base.items, headers)), list(dict.fromkeys(lines))[:MAX_FACTS]
    )
