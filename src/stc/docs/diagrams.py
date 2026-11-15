"""Mermaid diagrams built from facts (DOC-04).

Every node and edge comes from the symbols, signals, relations or DDL of the index, never from a
model, so a diagram cannot show something the code does not contain. Node ids are generated
(`n0`, `n1`, ...) and labels are sanitized, because names come from source code and are untrusted:
nothing in a name can become Mermaid syntax. A diagram over the node limit is truncated and says
so."""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from stc.docs.components import (
    Component,
    component_links,
    components,
    layer_links,
    layers_with_tables,
    tables_by_component,
)
from stc.docs.ddl import Table
from stc.docs.facts import WRITES, AppFacts, Owner
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
        if any(a not in WRITES for a in actions):
            flow.edge(t, p, "reads", "dotted")
        if any(a in WRITES for a in actions):
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


def _add_unit_data(flow: Flow, facts: AppFacts, units: set[str]) -> None:
    """The tables the functions and methods of a feature read and write."""
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for use in facts.tables:
        pair = (use.owner.name, use.table)
        if use.owner.kind == "unit" and use.owner.name in units and use.action not in grouped[pair]:
            grouped[pair].append(use.action)
    for (key, table), actions in sorted(grouped.items()):
        node = flow.node(f"unit:{key}", facts.graph.label(key), "box")
        t = flow.node(f"table:{table}", table, "db")
        if any(a not in WRITES for a in actions):
            flow.edge(t, node, "reads", "dotted")
        if any(a in WRITES for a in actions):
            flow.edge(node, t, "writes")


def _components_diagram(facts: AppFacts, found: dict[str, Component]) -> str | None:
    """With one layer only, the components themselves and the calls between them."""
    links = component_links(facts, found)
    flow = Flow("LR")
    for a in sorted(links.calls):
        for b, count in sorted(links.calls[a].items()):
            suffix = f" ({count})" if count > 1 else ""
            flow.edge(
                flow.node(f"c:{a}", found[a].name),
                flow.node(f"c:{b}", found[b].name),
                f"calls{suffix}",
            )
    for key, tables in sorted(tables_by_component(facts, found).items()):
        for table in sorted(tables)[:6]:
            flow.edge(
                flow.node(f"c:{key}", found[key].name), flow.node(f"t:{table}", table, "db"), "uses"
            )
    return _fenced(flow, "components")


def layers_diagram(facts: AppFacts) -> str | None:
    """The folders of the code as layers, the calls between them and the data they touch."""
    found = components(facts)
    links = layer_links(facts, found)
    if not links:
        return _components_diagram(facts, found)
    flow = Flow("LR")
    for (a, b), count in sorted(links.items(), key=lambda item: (-item[1], item[0])):
        flow.edge(
            flow.node(f"layer:{a}", a),
            flow.node(f"layer:{b}", b),
            f"{count} call{'s' * (count > 1)}",
        )
    data = layers_with_tables(facts, found)
    if data:
        database = facts.databases[0] if len(facts.databases) == 1 else "database"
        store = flow.node("@db", database, "db")
        for layer, count in sorted(data.items()):
            flow.edge(
                flow.node(f"layer:{layer}", layer), store, f"{count} table{'s' * (count > 1)}"
            )
    return _fenced(flow, "layers")


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
    layered = layers_diagram(facts)
    if layered:
        return layered
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
    graph = facts.graph
    entry = graph.label(feature.entry) if feature.kind == "task" else feature.entry
    me = flow.node("@self", entry, "stadium")
    for endpoint in feature.endpoints:
        node = flow.node(f"endpoint:{endpoint.detail}", endpoint.detail, "round")
        flow.edge(me, node, "")
        for key in feature.handlers.get(str(endpoint.loc), []):
            if key in graph.units:
                flow.edge(
                    node, flow.node(f"unit:{key}", graph.units[key].label, "box"), "handled by"
                )
    _add_unit_data(flow, facts, set(feature.units))
    shown = 0
    for key in feature.units:
        for call in graph.callees(key):
            if shown >= 40 or graph.units[call.target].inline:
                continue
            a = flow.node(f"unit:{key}", graph.label(key), "box")
            flow.edge(a, flow.node(f"unit:{call.target}", graph.label(call.target), "box"), "calls")
            shown += 1
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
