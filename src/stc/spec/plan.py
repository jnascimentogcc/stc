"""What the migration specification of one application contains (SPC-01..SPC-05).

One specification per feature (the slices the documentation also detects) plus an index with the
target stack, the slices in an order that respects their data dependencies, and the shared data.
The order and the dependencies are computed from the facts, never by the model. Nothing here calls
a model: it builds pages of static markdown and parts the model will write from assembled evidence.
"""

import re
from collections import defaultdict

import psycopg

from stc.catalog.context import SectionContext, _pack, build_context
from stc.docs import context as ctx
from stc.docs import diagrams
from stc.docs.ddl import read_tables
from stc.docs.facts import AppFacts, build_facts
from stc.docs.features import Feature, detect_features
from stc.docs.model import INDEX_PAGE, Page, Part, Plan, Static
from stc.docs.plan import (
    KIND_LABELS,
    _code,
    _data_access_table,
    _datasets_table,
    _extend,
    _feature_code_table,
    _jobs_table,
    _section,
    _static,
    _table,
)
from stc.docs.store import Planner

PART_TITLES = {
    "spec-intent": "Purpose and scope",
    "spec-requirements": "Functional requirements",
    "spec-rules": "Business rules",
    "spec-data": "Data",
    "spec-interfaces": "Interfaces",
    "spec-design": "Proposed design in the target stack",
    "spec-acceptance": "Acceptance scenarios",
    "spec-tasks": "Implementation plan",
    "spec-risks": "Risks and open questions",
    "spec-strategy": "Migration strategy",
}
# Parts that are written for the target technology get its description.
_TARGETED = {"spec-data", "spec-interfaces", "spec-design", "spec-acceptance", "spec-tasks"}

_DATA_LAYOUT = r"(\mPIC\M|\mPICTURE\M|\mCOMP(-[0-9])?\M|\mOCCURS\M|\mREDEFINES\M|\mFD\M)"
MAX_FACTS = ctx.MAX_FACTS
PROJECT_PAGE = "CLAUDE.md"


def _part(part_id: str, context: SectionContext, stack: str, style: str = "paragraphs") -> Part:
    instructions = stack if part_id in _TARGETED else ""
    return Part(part_id, PART_TITLES[part_id], context, style, instructions)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# order and dependencies between features
# --------------------------------------------------------------------------------------------


def feature_io(facts: AppFacts, feature: Feature) -> tuple[set[str], set[str]]:
    """(read, written) resources of a feature: `table X` and `dataset X`."""
    programs, jobs = set(feature.programs), set(feature.jobs)
    read: set[str] = set()
    written: set[str] = set()
    for use in facts.tables:
        if use.owner.kind == "program" and use.owner.name in programs:
            (written if use.writes else read).add(f"table {use.table}")
    for ds in facts.datasets:
        if use_in(ds.owner.kind, ds.owner.name, jobs, programs):
            (written if ds.writes else read).add(f"dataset {ds.dataset}")
    return read, written


def use_in(kind: str, name: str, jobs: set[str], programs: set[str]) -> bool:
    return name in (jobs if kind == "job" else programs)


def dependencies(facts: AppFacts, features: list[Feature]) -> dict[str, list[str]]:
    """For each feature, the others that write what it reads (slug -> slugs), in a stable order."""
    io = {f.slug: feature_io(facts, f) for f in features}
    out: dict[str, list[str]] = {}
    for f in features:
        reads = io[f.slug][0]
        out[f.slug] = [g.slug for g in features if g.slug != f.slug and reads & io[g.slug][1]]
    return out


def migration_order(
    features: list[Feature], deps: dict[str, list[str]]
) -> tuple[list[Feature], set[str]]:
    """Features with what they depend on first (smaller first among equals). The second value holds
    the slugs that are part of a dependency cycle: they are placed last, by name."""
    by_slug = {f.slug: f for f in features}
    done: list[str] = []
    remaining = set(by_slug)
    while remaining:
        ready = [s for s in remaining if all(d in done or d not in remaining for d in deps[s])]
        if not ready:
            break
        ready.sort(key=lambda s: (by_slug[s].size, by_slug[s].title))
        done.append(ready[0])
        remaining.discard(ready[0])
    cyclic = set(remaining)
    done += sorted(remaining, key=lambda s: by_slug[s].title)
    return [by_slug[s] for s in done], cyclic


# --------------------------------------------------------------------------------------------
# evidence per part
# --------------------------------------------------------------------------------------------


def _interface_facts(facts: AppFacts, feature: Feature) -> list[str]:
    jobs, programs = set(feature.jobs), set(feature.programs)
    lines = [
        *ctx.call_facts(facts, jobs, programs),
        *ctx.data_facts(facts, jobs, programs),
    ]
    lines += [f"Endpoint {e.detail} defined at {e.loc}" for e in feature.endpoints]
    lines += [f"Topic {t.key} ({t.role}) at {t.loc}" for t in feature.topics]
    called_by = {
        c.caller.name for c in facts.calls if c.target in programs and c.caller.kind == "program"
    }
    called_by -= programs
    lines += [f"Program {name} calls into this feature" for name in sorted(called_by)]
    for relation in facts.relations:
        arrow = "to" if relation.outgoing else "from"
        lines.append(
            f"Application relation ({relation.type}) {arrow} {relation.other}: {relation.evidence}"
        )
    return lines[:MAX_FACTS]


def spec_contexts(
    conn: psycopg.Connection, app_id: int, facts: AppFacts, feature: Feature
) -> dict[str, SectionContext]:
    base = ctx.feature_contexts(conn, app_id, facts, feature)
    mainframe = feature.kind in ("job", "program")
    headers = ctx._file_chunks(conn, app_id, feature.files, kinds=["program", "job"], limit=12)
    tables = sorted({u.table for u in facts.tables if u.owner.name in set(feature.programs)})
    copybooks = [
        facts.copybooks[c.copybook].path
        for c in facts.copybook_uses
        if c.owner.name in set(feature.programs) and c.copybook in facts.copybooks
    ]
    layout = ctx._file_chunks(
        conn, app_id, list(dict.fromkeys(copybooks)) + (feature.files if mainframe else []),
        regex=_DATA_LAYOUT if mainframe else None, limit=50,
    )  # fmt: skip
    ddl = (
        ctx._file_chunks(
            conn,
            app_id,
            facts.ddl_files,
            regex=r"\m(" + "|".join(re.escape(t) for t in tables) + r")\M",
            limit=30,
        )  # fmt: skip
        if tables
        else []
    )
    shared = ctx.feature_facts(facts, feature)
    data_lines = ctx.data_facts(facts, set(feature.jobs), set(feature.programs))[:MAX_FACTS]
    rules = base["rules"]
    flow = base["flow"]
    return {
        "spec-intent": base["summary"],
        "spec-requirements": flow,
        "spec-rules": SectionContext(
            _pack(ctx._merge(rules.items, flow.items)), rules.facts or shared[:40]
        ),
        "spec-data": SectionContext(_pack(ctx._merge(ddl, layout)), data_lines),
        "spec-interfaces": SectionContext(_pack(headers), _interface_facts(facts, feature)),
        "spec-design": SectionContext(flow.items, shared[:80]),
        "spec-acceptance": SectionContext(_pack(ctx._merge(rules.items, headers)), shared[:40]),
        "spec-tasks": SectionContext(flow.items, shared[:60]),
        "spec-risks": SectionContext(_pack(ctx._merge(layout, rules.items, headers)), shared[:60]),
    }  # noqa: E501


# --------------------------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------------------------


def _link(feature: Feature) -> str:
    return f"[{feature.title}](features/{feature.slug}.md)"


def _feature_page(
    conn: psycopg.Connection,
    app_id: int,
    facts: AppFacts,
    feature: Feature,
    stack: str,
    depends_on: list[Feature],
    position: int,
    total: int,
    cyclic: bool,
) -> Page:
    page = Page(f"features/{feature.slug}.md", f"Specification: {feature.title}", "Features")
    c = spec_contexts(conn, app_id, facts, feature)
    jobs, programs = set(feature.jobs), set(feature.programs)
    needs = (
        ", ".join(f"[{d.title}]({d.slug}.md)" for d in depends_on) or "nothing in this application"
    )
    header = (
        f"{KIND_LABELS[feature.kind]} · entry point `{feature.entry_loc}` · slice {position} of "
        f"{total} in the [migration order](../index.md) · depends on: {needs} · legacy "
        f"application `{facts.name}` ([how to read its code](../CLAUDE.md#reading-the-legacy-code))"
    )
    if cyclic:
        header += " · **part of a dependency cycle with other features: migrate them together**"
    _extend(
        page,
        _static(header),
        *(
            _part(
                pid,
                c[pid],
                stack,
                "steps" if pid in ("spec-requirements", "spec-tasks") else "paragraphs",
            )
            for pid in ("spec-intent", "spec-requirements", "spec-rules")
        ),  # fmt: skip
        _static(_section("Legacy flow", diagrams.feature_diagram(facts, feature))),
        _part("spec-data", c["spec-data"], stack),
        _static(
            _section("Tables", _data_access_table(facts, programs)),
            _section("Datasets", _datasets_table(facts, jobs)),
        ),
        _part("spec-interfaces", c["spec-interfaces"], stack),
        _static(
            _section("Batch steps", _jobs_table(facts, feature.jobs)) if feature.jobs else None,
            _section(
                "Endpoints",
                _table(
                    ["Endpoint", "Evidence"], [[e.detail, f"`{e.loc}`"] for e in feature.endpoints]
                )
                if feature.endpoints
                else None,
            ),
        ),
        _part("spec-design", c["spec-design"], stack),
        _part("spec-acceptance", c["spec-acceptance"], stack),
        _part("spec-tasks", c["spec-tasks"], stack, "steps"),
        _part("spec-risks", c["spec-risks"], stack),
        _static(_section("Legacy code to read", _feature_code_table(facts, feature))),
    )
    return page


def _shared_resources(facts: AppFacts, features: list[Feature]) -> str | None:
    """Tables and datasets used by more than one feature: they must be migrated once, together."""
    users: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"reads": set(), "writes": set()})
    for f in features:
        read, written = feature_io(facts, f)
        for name in read:
            users[name]["reads"].add(f.title)
        for name in written:
            users[name]["writes"].add(f.title)
    rows = [
        [f"`{name}`", _code(*sorted(u["writes"])) or "—", _code(*sorted(u["reads"])) or "—"]
        for name, u in sorted(users.items())
        if len(u["reads"] | u["writes"]) > 1
    ]
    return _table(["Resource", "Written by", "Read by"], rows) if rows else None


def _legacy_access(application: str) -> str:
    return (
        f"The legacy code is the application `{application}`. Every citation in these "
        "specifications, such as `src/PROG.cbl:14-33`, is a file path **relative to that "
        "application** and a line range. The legacy code is read-only: never change it.\n\n"
        "Read it with one of:\n\n"
        f"- **The STC MCP server** (preferred; it marks a file that changed after the "
        f"specification was written): `read_file` with `application` = `{application}`, "
        "`path` = the file and `start_line`/`end_line` = the range. `search_code`, "
        "`find_symbol`, `grep_code` and `get_dependencies` answer questions the specification "
        "leaves open. In Claude Code: `claude mcp add --transport http stc <STC MCP URL> "
        '--header "Authorization: Bearer <STC_MCP_TOKEN>"` (or `claude mcp add stc -- uv run '
        "stc serve-mcp` from the STC checkout).\n"
        f"- **A copy of the application folder** in the project (for example "
        f"`legacy/{application}/`): the same relative paths then apply.\n\n"
        "If neither is available, work only from the specification and treat anything it does "
        "not state as an open question for a person to answer."
    )


def _project_page(application: str, stack: str, rows: list[list[str]]) -> Page:
    """`CLAUDE.md` for the new project: copy it to its root so the assistant starts informed."""
    page = Page(PROJECT_PAGE, f"Migration of {application}", "Overview", plain=True)
    order = "\n".join(f"{r[0]}. `{r[1].split('](')[1].rstrip(')')}`" for r in rows) or "(none)"
    _extend(
        page,
        _static(
            f"You are rebuilding the legacy application `{application}` in a new technology. "
            "The specifications in this folder say what it does today, with evidence, and how to "
            "build it in the target stack. Do not generate code from the legacy language: build "
            "the behavior the specifications describe."
        ),
        _static(_section("Target stack", f"```text\n{stack}\n```")),
        _static(_section("Reading the legacy code", _legacy_access(application))),
        _static(
            _section(
                "How to work",
                "- Work on **one feature at a time**, in the order of `index.md`; the "
                "specification of a feature is `features/<feature>.md`.\n"
                "- Implement the *Functional requirements* and *Business rules* exactly, with the "
                "values they state. Turn the *Acceptance scenarios* into automated tests first.\n"
                "- Follow *Proposed design* and *Implementation plan* unless the target stack or "
                "the code you already have says otherwise.\n"
                "- Open the cited legacy code (`file:start-end`) whenever a statement is unclear.\n"
                "- Statements marked low or medium confidence, *Gaps* and *Risks and open "
                "questions* need a person's decision: ask, do not guess.\n"
                "- Never invent behavior the specification and the legacy code do not show.",
            )
        ),
        _static(_section("Features in migration order", order)),
    )
    return page


_HOW_TO_USE = """\
Each feature has its own specification in `features/`. Give a coding assistant one of them at a \
time, in the order below, together with the legacy code it lists under "Legacy code to read": \
the requirements, rules and acceptance scenarios say what must be true, the proposed design and \
implementation plan say how to build it in the target stack, and every statement cites the \
legacy code it comes from as `file:start-end`. Statements marked low confidence, the gaps and \
the open questions need a person's decision before they are built. `CLAUDE.md` in this folder is \
a starting point for the new project: copy it to the root of the project that will implement \
these specifications."""


def build_spec_plan(
    conn: psycopg.Connection, application: str, max_features: int, stack: str
) -> Plan:
    base = build_context(conn, application)
    facts = build_facts(conn, base.app_id, base.name)
    read_tables(conn, base.app_id, facts.ddl_files)  # fails early if the DDL cannot be read
    features, skipped = detect_features(facts, max_features)
    deps = dependencies(facts, features)
    ordered, cyclic = migration_order(features, deps)
    by_slug = {f.slug: f for f in features}

    pages: list[Page] = []
    rows = [
        [
            str(n),
            _link(f),
            KIND_LABELS[f.kind],
            ", ".join(_link(by_slug[d]) for d in deps[f.slug]) or "—",
            "cycle" if f.slug in cyclic else "",
        ]
        for n, f in enumerate(ordered, 1)
    ]
    dependency_facts = [
        f"{f.title} depends on {', '.join(by_slug[d].title for d in deps[f.slug])} "
        "(it reads what they write)"
        for f in ordered
        if deps[f.slug]
    ]
    dependency_facts += [
        f"{f.title} has no dependency on another feature" for f in ordered if not deps[f.slug]
    ]
    strategy_items = ctx._file_chunks(
        conn,
        base.app_id,
        [p for f in ordered for p in f.files][:40],
        kinds=["program", "job"],
        limit=14,
    )
    index = Page(INDEX_PAGE, f"Migration specification: {facts.name}", "Overview")
    _extend(
        index,
        _static(_section("Target stack", f"```text\n{stack}\n```")),
        _static(_section("How to use this specification", _HOW_TO_USE)),
        _static(_section("Reading the legacy code", _legacy_access(facts.name))),
        _static(
            _section(
                "Features in migration order",
                _table(["Order", "Feature", "Kind", "Depends on", ""], rows) if rows else None,
            ),
            _section("Shared data", _shared_resources(facts, features)),
        ),
        _part(
            "spec-strategy",
            SectionContext(_pack(strategy_items), dependency_facts[:MAX_FACTS]),
            stack,
        ),
    )
    pages.append(index)
    pages.append(_project_page(facts.name, stack, rows))
    for n, f in enumerate(ordered, 1):
        pages.append(
            _feature_page(
                conn,
                base.app_id,
                facts,
                f,
                stack,
                [by_slug[d] for d in deps[f.slug]],
                n,
                len(ordered),
                f.slug in cyclic,
            )  # fmt: skip
        )
    return Plan(base.name, base.code_version, base.files, pages, [f.title for f in skipped])


def spec_planner(stack: str) -> Planner:
    def plan(conn: psycopg.Connection, application: str, max_features: int) -> Plan:
        return build_spec_plan(conn, application, max_features, stack)

    return plan


__all__ = ["Static", "build_spec_plan", "dependencies", "migration_order", "spec_planner"]
