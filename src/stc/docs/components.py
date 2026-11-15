"""Components and layers of an application that is not written in COBOL, from its call graph.

A *component* is a class (or, for functions with no class, a file); a *layer* is the folder it
lives in (`Http/Controllers`, `Services`, `Models`). Both come from the units and calls of the
index, so a table or a diagram built from them says nothing the code does not contain."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from stc.docs.facts import AppFacts

ROOT_LAYER = "(root)"


def layer_of(path: str) -> str:
    """The two nearest folders of a file: `app/Http/Controllers/A.php` -> `Http/Controllers`."""
    folders = path.split("/")[:-1]
    return "/".join(folders[-2:]) if folders else ROOT_LAYER


@dataclass
class Component:
    key: str  # `path::Class`, or `path` for the functions of a file
    name: str
    path: str
    layer: str
    line: int
    units: list[str] = field(default_factory=list)


def components(facts: AppFacts) -> dict[str, Component]:
    """Every component, by key, in file order."""
    found: dict[str, Component] = {}
    for unit in sorted(facts.graph.units.values(), key=lambda u: (u.path, u.start)):
        if unit.inline:
            continue
        key = f"{unit.path}::{unit.owner_class}" if unit.owner_class else unit.path
        if key not in found:
            name = unit.owner_class or unit.path.rsplit("/", 1)[-1]
            found[key] = Component(key, name, unit.path, layer_of(unit.path), unit.start)
        found[key].units.append(unit.key)
    return found


def component_of(found: dict[str, Component]) -> dict[str, str]:
    """Unit key -> component key."""
    return {u: c.key for c in found.values() for u in c.units}


@dataclass
class Links:
    calls: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    called_by: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))


def component_links(facts: AppFacts, found: dict[str, Component]) -> Links:
    """How many calls go from each component to each other one."""
    owner = component_of(found)
    links = Links()
    for call in facts.graph.calls:
        a, b = owner.get(call.caller), owner.get(call.target)
        if a is not None and b is not None and a != b:
            links.calls[a][b] += 1
            links.called_by[b][a] += 1
    return links


def layer_links(facts: AppFacts, found: dict[str, Component]) -> Counter[tuple[str, str]]:
    """How many calls go from each layer to each other one."""
    layer = {c.key: c.layer for c in found.values()}
    owner = component_of(found)
    counts: Counter[tuple[str, str]] = Counter()
    for call in facts.graph.calls:
        a, b = owner.get(call.caller), owner.get(call.target)
        if a is not None and b is not None and layer[a] != layer[b]:
            counts[(layer[a], layer[b])] += 1
    return counts


def tables_by_component(facts: AppFacts, found: dict[str, Component]) -> dict[str, set[str]]:
    owner = component_of(found)
    used: dict[str, set[str]] = defaultdict(set)
    for use in facts.tables:
        if use.owner.kind == "unit" and use.owner.name in owner:
            used[owner[use.owner.name]].add(use.table)
    return used


def layers_with_tables(facts: AppFacts, found: dict[str, Component]) -> dict[str, int]:
    """Layer -> how many distinct tables its code reads or writes."""
    per_component = tables_by_component(facts, found)
    layers: dict[str, set[str]] = defaultdict(set)
    for key, tables in per_component.items():
        layers[found[key].layer] |= tables
    return {layer: len(tables) for layer, tables in layers.items()}
