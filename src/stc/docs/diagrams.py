"""Mermaid diagrams built from facts (DOC-04).

Every node and edge comes from the symbols, signals, relations or DDL of the index, never from a
model, so a diagram cannot show something the code does not contain. Node ids are generated
(`n0`, `n1`, ...) and labels are sanitized, because names come from source code and are untrusted:
nothing in a name can become Mermaid syntax. A diagram over the node limit is truncated and says
so."""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from stc.docs.ddl import Table
from stc.docs.facts import _WRITES, AppFacts, Owner
from stc.docs.features import Feature

MAX_NODES = 40
MAX_LABEL = 48
_UNSAFE = re.compile(r"[^\w .,:/()+#@$%&'-]")
_SHAPES = {
    "box": ('["', '"]'),
    "round": ('("', '")'),
    "stadium": ('(["', '"])'),
    "db": ('[("', '")]'),
    "ext": ('{{"', '"}}'),
    "file": ('[/"', '"/]'),
}


def label(text: str) -> str:
    """A label that cannot break out of its quotes or carry markup."""
    clean = _UNSAFE.sub(" ", " ".join(text.split())).replace('"', "'").strip()
    return clean if len(clean) <= MAX_LABEL else clean[: MAX_LABEL - 1] + "…"


@dataclass
class Flow:
    direction: str = "LR"
    nodes: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # key -> id, label, shape
    edges: list[tuple[str, str, str, str]] = field(default_factory=list)  # a, b, text, style

    def node(self, key: str, text: str, shape: str = "box") -> str:
        if key not in self.nodes:
            self.nodes[key] = (f"n{len(self.nodes)}", label(text), shape)
        return key

    def edge(self, a: str, b: str, text: str = "", style: str = "arrow") -> None:
        if (a, b, text, style) not in self.edges:
            self.edges.append((a, b, text, style))

    def render(self, max_nodes: int = MAX_NODES) -> tuple[str, int]:
        """The fenced diagram and how many nodes were left out."""
        kept = dict(list(self.nodes.items())[:max_nodes])
        lines = ["```mermaid", f"flowchart {self.direction}"]
        for node_id, text, shape in kept.values():
            left, right = _SHAPES[shape]
            lines.append(f"    {node_id}{left}{text}{right}")
        for a, b, text, style in self.edges:
            if a not in kept or b not in kept:
                continue
            arrow = {"arrow": "-->", "dotted": "-.->", "plain": "---"}[style]
            middle = f'|"{label(text)}"|' if text else ""
            lines.append(f"    {kept[a][0]} {arrow}{middle} {kept[b][0]}")
        external = [k for k, v in kept.items() if v[2] == "ext"]
        if external:
            lines.append("    classDef external stroke-dasharray: 4 3")
            lines.append(f"    class {','.join(kept[k][0] for k in external)} external")
        lines.append("```")
        return "\n".join(lines), len(self.nodes) - len(kept)


def _fenced(flow: Flow, what: str) -> str | None:
    if not flow.edges:
        return None
    text, left_out = flow.render()
    if left_out:
        text += f"\n\n_The diagram shows the first {MAX_NODES} of {len(flow.nodes)} {what}; the tables list the rest._"  # noqa: E501
    return text


# --------------------------------------------------------------------------------------------
# application level
# --------------------------------------------------------------------------------------------


def context_diagram(facts: AppFacts) -> str | None:
    """The application and the applications it calls or is used by."""
    flow = Flow("LR")
    me = flow.node("@self", facts.name, "stadium")
    for relation in facts.relations:
        other = flow.node(f"app:{relation.other}", relation.other)
        if relation.type == "shared_database":
            flow.edge(me, other, relation.type, "plain")
        elif relation.outgoing:
            flow.edge(me, other, relation.type)
        else:
            flow.edge(other, me, relation.type)
    return _fenced(flow, "applications")


def _owner_key(owner: Owner) -> str:
    return f"{owner.kind}:{owner.name}"


def _program_node(flow: Flow, facts: AppFacts, name: str) -> str:
    return flow.node(f"program:{name}", name, "box" if name in facts.programs else "ext")


def _add_calls(
    flow: Flow, facts: AppFacts, jobs: set[str] | None, programs: set[str] | None
) -> dict[str, list[str]]:
    """Job steps and calls; returns the program nodes each (job, step) runs."""
    by_step: dict[str, list[str]] = defaultdict(list)
    for job in sorted(facts.jobs):
        if jobs is not None and job not in jobs:
            continue
        flow.node(f"job:{job}", job, "stadium")
        for call in facts.job_calls(job):
            target = _program_node(flow, facts, call.target)
            flow.edge(f"job:{job}", target, call.caller.step or "", "arrow")
            by_step[f"{job}/{call.caller.step}"].append(target)
    for call in facts.calls:
        if call.caller.kind != "program":
            continue
        if programs is not None and call.caller.name not in programs:
            continue
        a = _program_node(flow, facts, call.caller.name)
        flow.edge(a, _program_node(flow, facts, call.target), "CALL")
    return by_step


def _add_data(
    flow: Flow,
    facts: AppFacts,
    by_step: dict[str, list[str]],
    jobs: set[str] | None,
    programs: set[str] | None,
) -> None:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for use in facts.tables:
        if use.owner.kind != "program" or (programs is not None and use.owner.name not in programs):
            continue
        key = (use.owner.name, use.table)
        if use.action not in grouped[key]:
            grouped[key].append(use.action)
    for (program, table), actions in sorted(grouped.items()):
        p = _program_node(flow, facts, program)
        t = flow.node(f"table:{table}", table, "db")
        if any(a not in _WRITES for a in actions):
            flow.edge(t, p, "reads", "dotted")
        if any(a in _WRITES for a in actions):
            flow.edge(p, t, "writes")
    for ds in facts.datasets:
        if ds.owner.kind != "job" or (jobs is not None and ds.owner.name not in jobs):
            continue
        runners = by_step.get(f"{ds.owner.name}/{ds.owner.step}", [])
        internal = [r for r in runners if flow.nodes[r][2] != "ext"]
        d = flow.node(f"dataset:{ds.dataset}", ds.dataset, "file")
        for runner in internal or runners:
            if ds.writes:
                flow.edge(runner, d, "writes")
            else:
                flow.edge(d, runner, "reads", "dotted")


def architecture_diagram(facts: AppFacts) -> str | None:
    """How the application is organized: jobs, programs and the data they touch (mainframe), or
    the top-level folders and the stores it uses (anything else)."""
    flow = Flow("LR")
    if facts.is_mainframe:
        by_step = _add_calls(flow, facts, None, None)
        for program in sorted(facts.programs):
            _program_node(flow, facts, program)
        _add_data(flow, facts, by_step, None, None)
        return _fenced(flow, "components")
    me = flow.node("@self", facts.name, "stadium")
    for directory, count in facts.directories[:12]:
        flow.edge(me, flow.node(f"dir:{directory}", f"{directory}/ ({count} files)"), "")
    for database in facts.databases:
        flow.edge(me, flow.node(f"db:{database}", database, "db"), "uses")
    for topic in facts.topics:
        node = flow.node(f"topic:{topic.key}", topic.key, "file")
        if topic.role == "publishes":
            flow.edge(me, node, "publishes")
        else:
            flow.edge(node, me, "subscribes", "dotted")
    return _fenced(flow, "components")


# --------------------------------------------------------------------------------------------
# feature level
# --------------------------------------------------------------------------------------------


def feature_diagram(facts: AppFacts, feature: Feature) -> str | None:
    """What one feature runs and the data it reads and writes."""
    flow = Flow("LR")
    if feature.kind in ("job", "program"):
        jobs = set(feature.jobs)
        programs = set(feature.programs)
        by_step = _add_calls(flow, facts, jobs, programs)
        if not jobs:
            for program in feature.programs:
                _program_node(flow, facts, program)
        _add_data(flow, facts, by_step, jobs, programs)
        return _fenced(flow, "components")
    me = flow.node("@self", feature.entry, "stadium")
    for endpoint in feature.endpoints:
        flow.edge(me, flow.node(f"endpoint:{endpoint.detail}", endpoint.detail, "round"), "")
    for topic in feature.topics:
        flow.edge(flow.node(f"topic:{topic.key}", topic.key, "file"), me, "subscribes", "dotted")
    return _fenced(flow, "endpoints")


def perform_diagram(facts: AppFacts, program: str) -> str | None:
    """The paragraphs of a program and which ones PERFORM which (its control flow)."""
    performs = [p for p in facts.performs if p.program == program]
    if not performs:
        return None
    names = {s.name for s in facts.paragraphs.get(program, [])}
    links = [(p.source or "(main)", p.target) for p in performs if p.target in names]
    used = {n for link in links for n in link}
    flow = Flow("TD")
    for name in [s.name for s in facts.paragraphs.get(program, []) if s.name in used]:
        flow.node(f"para:{name}", name)
    for source, target in links:
        flow.node(f"para:{source}", source)
        flow.edge(f"para:{source}", flow.node(f"para:{target}", target), "PERFORM")
    return _fenced(flow, "paragraphs")


# --------------------------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------------------------

_WORD = re.compile(r"\W+")


def _ident(text: str) -> str:
    return _WORD.sub("_", text).strip("_") or "X"


def er_diagram(tables: list[Table]) -> str | None:
    if not tables:
        return None
    known = {t.name for t in tables}
    lines = ["```mermaid", "erDiagram"]
    for table in tables[:MAX_NODES]:
        lines.append(f"    {_ident(table.name)} {{")
        for column in table.columns[:30]:
            keys = [k for k, on in (("PK", column.primary), ("FK", column.references)) if on]
            kind = _ident(column.type.split("(")[0] if column.type else "TYPE")
            lines.append(
                f"        {kind} {_ident(column.name)}" + (f" {','.join(keys)}" if keys else "")
            )
        lines.append("    }")
    for table in tables[:MAX_NODES]:
        for column in table.columns:
            if column.references and column.references in known:
                lines.append(
                    f"    {_ident(table.name)} }}o--|| {_ident(column.references)} : "
                    f'"{label(column.name)}"'
                )
    lines.append("```")
    return "\n".join(lines)
