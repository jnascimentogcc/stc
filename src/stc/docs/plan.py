"""What the documentation of one application contains: its pages, in navigation order (DOC-02).

Each page is a list of blocks: static markdown built from facts (tables with their evidence,
Mermaid diagrams) and parts the model writes from assembled evidence. Nothing here calls a model."""

from collections import defaultdict

import psycopg

from stc.catalog.context import AppContext, SectionContext, build_context
from stc.catalog.store import _cell
from stc.docs import context as ctx
from stc.docs import diagrams
from stc.docs.ddl import Table, read_tables
from stc.docs.facts import AppFacts, build_facts
from stc.docs.features import Feature, detect_features
from stc.docs.model import FEATURES_PAGE, INDEX_PAGE, Page, Part, Plan, Static

PART_TITLES = {
    "overview": "What it is",
    "business": "What it does for the business",
    "stack": "Stack",
    "structure": "Components and how they fit",
    "entities": "Entities and records",
    "configuration": "Configuration",
    "operations": "How it runs",
    "summary": "What it does",
    "flow": "How it works",
    "rules": "Business rules",
}
KIND_LABELS = {
    "job": "Batch job",
    "program": "Program",
    "endpoints": "HTTP endpoints",
    "consumer": "Message consumer",
}


def _part(part_id: str, context: SectionContext, style: str = "paragraphs") -> Part:
    return Part(part_id, PART_TITLES[part_id], context, style)  # type: ignore[arg-type]


def _code(*values: object) -> str:
    return ", ".join(f"`{v}`" for v in values)


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _section(title: str, body: str | None, level: int = 2) -> str | None:
    return f"{'#' * level} {title}\n\n{body}" if body else None


def _static(*sections: str | None) -> Static | None:
    text = "\n\n".join(s for s in sections if s)
    return Static(text) if text else None


def _extend(page: Page, *blocks: Part | Static | None) -> None:
    page.blocks += [b for b in blocks if b is not None]


# --------------------------------------------------------------------------------------------
# tables built from facts
# --------------------------------------------------------------------------------------------


def _programs_table(facts: AppFacts, programs: list[str] | None = None) -> str | None:
    names = programs if programs is not None else sorted(facts.programs)
    if not names:
        return None
    rows = []
    for name in names:
        callers = sorted(
            {
                f"{c.caller.name}" + (f" ({c.caller.step})" if c.caller.step else "")
                for c in facts.calls
                if c.target == name
            }
        )
        calls = sorted({c.target for c in facts.program_calls(name)})
        tables = sorted({u.table for u in facts.tables if u.owner.name == name})
        rows.append(
            [
                f"`{name}`",
                f"`{facts.programs[name]}`" if name in facts.programs else "",
                _code(*callers) or "—",
                _code(*calls) or "—",
                _code(*tables) or "—",
            ]
        )
    return _table(["Program", "Defined at", "Called by", "Calls", "Tables"], rows)


def _jobs_table(facts: AppFacts, jobs: list[str] | None = None) -> str | None:
    rows = []
    for job in jobs if jobs is not None else sorted(facts.jobs):
        calls = facts.job_calls(job)
        if not calls:
            rows.append([f"`{job}`", "—", "—", f"`{facts.jobs[job]}`"])
        for call in calls:
            kind = "" if call.target in facts.programs else " (utility or external)"
            rows.append(
                [
                    f"`{job}`",
                    f"`{call.caller.step or '?'}`",
                    f"`{call.target}`{kind}",
                    f"`{call.loc}`",
                ]
            )
    return _table(["Job", "Step", "Runs", "Evidence"], rows) if rows else None


def _data_access_table(facts: AppFacts, programs: set[str] | None = None) -> str | None:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    where: dict[tuple[str, str], str] = {}
    for use in facts.tables:
        if use.owner.kind != "program" or (programs is not None and use.owner.name not in programs):
            continue
        key = (use.table, use.owner.name)
        if use.action not in grouped[key]:
            grouped[key].append(use.action)
        where.setdefault(key, str(use.loc))
    if not grouped:
        return None
    rows = [
        [f"`{table}`", f"`{program}`", ", ".join(sorted(actions)), f"`{where[(table, program)]}`"]
        for (table, program), actions in sorted(grouped.items())
    ]
    return _table(["Table", "Program", "Access", "Evidence"], rows)


def _datasets_table(facts: AppFacts, jobs: set[str] | None = None) -> str | None:
    written: dict[str, list[str]] = defaultdict(list)
    read: dict[str, list[str]] = defaultdict(list)
    where: dict[str, str] = {}
    for use in facts.datasets:
        if use.owner.kind != "job" or (jobs is not None and use.owner.name not in jobs):
            continue
        who = f"{use.owner.name}/{use.owner.step}" if use.owner.step else use.owner.name
        (written if use.writes else read)[use.dataset].append(who)
        where.setdefault(use.dataset, str(use.loc))
    names = sorted(set(written) | set(read))
    if not names:
        return None
    rows = [
        [
            f"`{n}`",
            _code(*sorted(set(written[n]))) or "—",
            _code(*sorted(set(read[n]))) or "—",
            f"`{where[n]}`",
        ]
        for n in names
    ]
    return _table(["Dataset", "Written by", "Read by", "Evidence"], rows)


def _copybooks_table(facts: AppFacts) -> str | None:
    names = sorted(set(facts.copybooks) | {u.copybook for u in facts.copybook_uses})
    if not names:
        return None
    rows = []
    for name in names:
        users = sorted({u.owner.name for u in facts.copybook_uses if u.copybook == name})
        rows.append(
            [
                f"`{name}`",
                f"`{facts.copybooks[name]}`"
                if name in facts.copybooks
                else "outside this application",  # noqa: E501
                _code(*users) or "—",
            ]
        )
    return _table(["Copybook", "Defined at", "Used by"], rows)


def _tables_detail(tables: list[Table]) -> str | None:
    if not tables:
        return None
    out: list[str] = []
    for table in tables:
        out.append(
            f"### `{table.name}`\n\nDefined at `{table.path}:{table.start_line}-{table.end_line}`."
        )
        rows = [
            [
                f"`{c.name}`",
                f"`{c.type}`" if c.type else "",
                "primary key"
                if c.primary
                else (f"references `{c.references}`" if c.references else ""),  # noqa: E501
                "no" if c.required or c.primary else "yes",
            ]
            for c in table.columns
        ]
        out.append(_table(["Column", "Type", "Key", "Nullable"], rows))
    return "\n\n".join(out)


def _endpoints_table(facts: AppFacts) -> str | None:
    if not facts.endpoints:
        return None
    return _table(["Endpoint", "Evidence"], [[e.detail, f"`{e.loc}`"] for e in facts.endpoints])


def _feature_code_table(facts: AppFacts, feature: Feature) -> str | None:
    rows: list[list[str]] = []
    for job in feature.jobs:
        rows.append(["job", f"`{job}`", f"`{facts.jobs[job]}`"])
    for program in feature.programs:
        rows.append(["program", f"`{program}`", f"`{facts.programs[program]}`"])
    for path in feature.files:
        if feature.kind in ("endpoints", "consumer"):
            rows.append(["file", f"`{path}`", ""])
    return _table(["Kind", "Name", "Defined at"], rows) if rows else None


# --------------------------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------------------------


def _key_facts(facts: AppFacts, tables: list[Table]) -> str:
    items = [("Code version", f"`{facts.code_version or 'unknown'}`")]
    if facts.languages:
        items.append(("Languages", ", ".join(f"{lang} ({n})" for lang, n in facts.languages)))
    counts = [
        ("Programs", len(facts.programs)),
        ("Batch jobs", len(facts.jobs)),
        ("Copybooks", len(facts.copybooks)),
        ("Tables defined", len(tables)),
        ("Exposed endpoints", len(facts.endpoints)),
        ("Related applications", len({r.other for r in facts.relations})),
    ]
    items += [(name, str(n)) for name, n in counts if n]
    return _table(["Item", "Value"], [[k, v] for k, v in items])


def build_plan(conn: psycopg.Connection, application: str, max_features: int) -> Plan:
    base = build_context(conn, application)
    facts = build_facts(conn, base.app_id, base.name)
    tables = read_tables(conn, base.app_id, facts.ddl_files)
    features, skipped = detect_features(facts, max_features)
    pages: list[Page] = []

    architecture = Page("architecture.md", "Architecture", "Architecture")
    _extend(
        architecture,
        _part(
            "structure", ctx.structure_context(conn, base.app_id, facts, base.sections["modules"])
        ),
        _static(
            _section("Diagram", diagrams.architecture_diagram(facts)),
            _section("Programs", _programs_table(facts)),
            _section("Batch jobs", _jobs_table(facts)),
            None
            if facts.is_mainframe
            else _section(
                "Top-level folders",
                _table(["Folder", "Files"], [[f"`{d}/`", str(n)] for d, n in facts.directories])
                if facts.directories
                else None,
            ),
        ),
    )
    pages.append(architecture)

    index_of_features = Page(FEATURES_PAGE, "Features", "Features")
    pages.append(index_of_features)
    rows = [
        [
            f"[{f.title}]({f.slug}.md)",
            KIND_LABELS[f.kind],
            f"`{f.entry_loc}`",
            str(len(f.programs) or len(f.files)),
        ]  # noqa: E501
        for f in features
    ]
    _extend(
        index_of_features,
        _static(
            "Each feature is something the application does when something starts it: a batch job, "
            "a program that nothing calls, a group of endpoints or a message consumer. They are "
            "found from the code, and every page follows what the feature runs and the data it "
            "touches."
            if features
            else "No features were detected: the application has no batch job, entry program, "
            "exposed endpoint or message consumer in the indexed code.",
            _table(["Feature", "Kind", "Entry point", "Programs or files"], rows) if rows else None,
            _section(
                "Not documented in detail",
                "\n".join(f"- {f.title} (`{f.entry_loc}`)" for f in skipped)
                + f"\n\nOnly the {len(features)} largest features have a page of their own.",
            )
            if skipped
            else None,
        ),
    )
    for feature in features:
        pages.append(_feature_page(conn, base.app_id, facts, feature))

    entities = ctx.entities_context(conn, base.app_id, facts, base.sections["data_model"])
    if tables or facts.tables or facts.copybooks or not entities.empty:
        data = Page("data-model.md", "Data model", "Data")
        _extend(
            data,
            _part("entities", entities),
            _static(
                _section("Entity-relationship diagram", diagrams.er_diagram(tables)),
                _section("Tables", _tables_detail(tables)),
                _section("Who reads and writes what", _data_access_table(facts)),
                _section("Copybooks", _copybooks_table(facts)),
            ),
        )
        pages.append(data)

    interface_sections = _interface_sections(facts)
    if interface_sections:
        interfaces = Page("interfaces.md", "Interfaces", "Reference")
        _extend(interfaces, _static(*interface_sections))
        pages.append(interfaces)

    dependencies = Page("dependencies.md", "Dependencies", "Reference")
    _extend(dependencies, _static(*_dependency_sections(facts)))
    pages.append(dependencies)

    configuration = base.sections["configuration"]
    operations = ctx.operations_context(conn, base.app_id, facts)
    if not configuration.empty or not operations.empty:
        run = Page("configuration.md", "Configuration and operations", "Operations")
        _extend(
            run, _part("operations", operations, "steps"), _part("configuration", configuration)
        )
        pages.append(run)

    pages.insert(0, _index_page(base, facts, tables, pages))
    return Plan(base.name, base.code_version, base.files, pages, [f.title for f in skipped])


def _index_page(base: AppContext, facts: AppFacts, tables: list[Table], pages: list[Page]) -> Page:
    page = Page(INDEX_PAGE, facts.name, "Overview")
    listing = "\n".join(
        f"- [{p.title}]({p.path})"
        for p in pages
        if not p.path.startswith("features/") or p.path == FEATURES_PAGE  # noqa: E501
    )
    _extend(
        page,
        _part("overview", base.sections["purpose"]),
        _part("business", base.sections["business"]),
        _static(
            _section("Key facts", _key_facts(facts, tables)),
            _section("Context", diagrams.context_diagram(facts)),
        ),
        _part("stack", base.sections["stack"]),
        _static(_section("In this documentation", listing)),
    )
    return page


def _feature_page(conn: psycopg.Connection, app_id: int, facts: AppFacts, feature: Feature) -> Page:
    page = Page(f"features/{feature.slug}.md", feature.title, "Features")
    contexts = ctx.feature_contexts(conn, app_id, facts, feature)
    entry = feature.programs[0] if feature.programs else None
    jobs, programs = set(feature.jobs), set(feature.programs)
    _extend(
        page,
        _static(
            f"{KIND_LABELS[feature.kind]} · entry point `{feature.entry_loc}` · "
            "[all features](index.md)"
        ),
        _part("summary", contexts["summary"]),
        _static(
            _section("Diagram", diagrams.feature_diagram(facts, feature)),
            _section(
                f"Control flow of {entry}",
                diagrams.perform_diagram(facts, entry) if entry else None,
                3,
            ),
        ),
        _part("flow", contexts["flow"], "steps"),
        _part("rules", contexts["rules"]),
        _static(
            _section("Code involved", _feature_code_table(facts, feature)),
            _section("Batch steps", _jobs_table(facts, feature.jobs)) if feature.jobs else None,
            _section("Tables", _data_access_table(facts, programs)),
            _section("Datasets", _datasets_table(facts, jobs)),
            _section(
                "Endpoints",
                _table(
                    ["Endpoint", "Evidence"], [[e.detail, f"`{e.loc}`"] for e in feature.endpoints]
                )
                if feature.endpoints
                else None,
            ),
        ),
    )
    return page


def _interface_sections(facts: AppFacts) -> list[str | None]:
    called = {c.target for c in facts.calls}
    entries = [p for p in sorted(facts.programs) if p not in called]
    consumed = (
        "\n".join(f"- {e.detail} — evidence: `{e.loc}`" for e in facts.consumed[:200])
        if facts.consumed
        else None
    )
    topics = (
        _table(
            ["Role", "Topic", "Evidence"],
            [[t.role, f"`{t.key}`", f"`{t.loc}`"] for t in facts.topics],
        )  # noqa: E501
        if facts.topics
        else None
    )
    dynamic = (
        "\n".join(
            f"- `{c.caller.name}` calls a program chosen at run time (`{c.target}`)"
            f" — evidence: `{c.loc}`"
            for c in facts.unresolved
        )  # noqa: E501
        if facts.unresolved
        else None
    )
    sections = [
        _section("Batch jobs and steps", _jobs_table(facts)),
        _section(
            "Entry programs (not called by another program or job)",
            "\n".join(f"- `{p}` — `{facts.programs[p]}`" for p in entries)
            if facts.jobs or called
            else None,  # noqa: E501
        ),
        _section("Dynamic calls (target known only at run time)", dynamic),
        _section("Exposed endpoints", _endpoints_table(facts)),
        _section("Outgoing HTTP calls", consumed),
        _section("Messaging", topics),
        _section("Datasets", _datasets_table(facts)),
        _section(
            "Databases", "\n".join(f"- `{d}`" for d in facts.databases) if facts.databases else None
        ),  # noqa: E501
    ]
    return sections if any(sections) else []


def _dependency_sections(facts: AppFacts) -> list[str | None]:
    if not facts.relations:
        return ["No relations with other applications were detected in the indexed code."]
    rows = [
        [
            "calls or depends on"
            if r.outgoing and r.type != "shared_database"
            else ("shares a database with" if r.type == "shared_database" else "used by"),
            f"`{r.other}`",
            r.type,
            r.confidence,
            r.detail,
            f"`{r.evidence}`" if r.evidence else "",
        ]
        for r in facts.relations
    ]
    return [
        _section("Diagram", diagrams.context_diagram(facts)),
        _section(
            "Relations",
            _table(["Direction", "Application", "Type", "Confidence", "Detail", "Evidence"], rows),
        ),  # noqa: E501
    ]
