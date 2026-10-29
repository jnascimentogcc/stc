"""The global map (CAT-06): every application, how they relate, what they share.

Everything here is derived from the database (applications, relations, signals and the purpose
line of each catalog) with no LLM call, so it is free, deterministic and always matches the
relations table. Every relation and shared resource keeps its evidence. The only hand-written part
is the maintainers' notes block at the top, which regeneration preserves (CAT-05)."""

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import psycopg

from stc.catalog.render import parse_manual_blocks
from stc.catalog.store import _cell, _write_atomic

GLOBAL_MAP_FILE = "_global-map.md"
NOTES_SECTION = "notes"
MAX_MERMAID_EDGES = 200
_PURPOSE_LINE = re.compile(r"^- (.*?)(?: _\((?:high|medium|low) confidence\)_)? — evidence:")


def global_map_path(catalogs_dir: Path) -> Path:
    return catalogs_dir / GLOBAL_MAP_FILE


@dataclass(frozen=True)
class _App:
    id: int
    name: str
    path: str
    version: str | None
    purpose: str | None
    has_catalog: bool


@dataclass(frozen=True)
class _Relation:
    source: int
    target: int
    type: str
    confidence: str
    detail: str
    evidence: str


def purpose_line(content: str | None) -> str | None:
    for line in (content or "").splitlines():
        match = _PURPOSE_LINE.match(line)
        if match:
            return match.group(1).strip()
    return None


def _load_apps(conn: psycopg.Connection) -> list[_App]:
    rows = conn.execute(
        "SELECT a.id, a.name, a.path, a.indexed_version, c.id IS NOT NULL, cs.content"
        " FROM applications a LEFT JOIN catalogs c ON c.application_id = a.id"
        " LEFT JOIN catalog_sections cs ON cs.catalog_id = c.id AND cs.section = 'purpose'"
        " ORDER BY a.name"
    ).fetchall()
    return [_App(r[0], r[1], r[2], r[3], purpose_line(r[5]), bool(r[4])) for r in rows]


def _load_relations(conn: psycopg.Connection, names: dict[int, str]) -> list[_Relation]:
    rows = conn.execute(
        "SELECT r.source_app_id, r.target_app_id, r.type, r.confidence, COALESCE(r.detail, ''),"
        " fa.name, f.path, r.evidence_start_line, r.evidence_end_line"
        " FROM active_relations r LEFT JOIN files f ON f.id = r.evidence_file_id"
        " LEFT JOIN applications fa ON fa.id = f.application_id"
        " ORDER BY r.source_app_id, r.target_app_id, r.type"
    ).fetchall()
    relations = []
    for sid, tid, kind, conf, detail, owner, path, start, end in rows:
        evidence = f"`{owner}/{path}:{start}-{end}`" if path is not None else ""
        relations.append(_Relation(sid, tid, kind, conf, detail, evidence))
    return [r for r in relations if r.source in names and r.target in names]


def _shared(
    conn: psycopg.Connection, kind: str, role: str | None = None
) -> dict[str, dict[str, set[str]]]:
    """key -> role -> application names, for the keys used by two or more applications."""
    query = (
        "SELECT s.key, s.role, a.name FROM signals s JOIN applications a ON a.id = s.application_id"
        " WHERE s.kind = %s" + (" AND s.role = %s" if role else "") + " ORDER BY s.key, a.name"
    )
    rows = conn.execute(query, (kind, role) if role else (kind,)).fetchall()
    grouped: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for key, signal_role, name in rows:
        grouped[key][signal_role].add(name)
    return {
        key: dict(roles)
        for key, roles in grouped.items()
        if len({n for names in roles.values() for n in names}) >= 2
    }


def _clusters(apps: list[_App], relations: list[_Relation]) -> list[list[_App]]:
    parent = {a.id: a.id for a in apps}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in relations:
        parent[find(r.source)] = find(r.target)
    groups: dict[int, list[_App]] = defaultdict(list)
    for a in apps:
        groups[find(a.id)].append(a)
    return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0].name))


def _mermaid(apps: list[_App], relations: list[_Relation]) -> list[str]:
    linked = {r.source for r in relations} | {r.target for r in relations}
    ids = {a.id: f"n{i}" for i, a in enumerate(a for a in apps if a.id in linked)}
    names = {a.id: a.name for a in apps}
    lines = ["```mermaid", "graph LR"]
    lines += [f'    {node}["{names[app_id]}"]' for app_id, node in ids.items()]
    drawn: set[tuple[int, int, str]] = set()
    for r in relations[:MAX_MERMAID_EDGES]:
        if r.type == "shared_database":
            key = (min(r.source, r.target), max(r.source, r.target), r.type)
            if key in drawn:
                continue
            drawn.add(key)
            lines.append(f"    {ids[r.source]} ---|{r.type}| {ids[r.target]}")
        else:
            lines.append(f"    {ids[r.source]} -->|{r.type}| {ids[r.target]}")
    lines.append("```")
    if len(relations) > MAX_MERMAID_EDGES:
        lines.append(
            f"_The diagram shows the first {MAX_MERMAID_EDGES} of {len(relations)} relations; "
            "the table below lists all of them._"
        )
    return lines


def _names(names: set[str]) -> str:
    return ", ".join(f"`{n}`" for n in sorted(names))


def build_global_map(conn: psycopg.Connection, manual: dict[str, str] | None = None) -> str:
    apps = _load_apps(conn)
    by_id = {a.id: a for a in apps}
    names = {a.id: a.name for a in apps}
    relations = _load_relations(conn, names)
    notes = (manual or {}).get(NOTES_SECTION, "")

    out = [
        "# Global map",
        "",
        "> Generated by STC from the indexed code: the relations table, the static signals and "
        "the catalogs, with no LLM involved. Relations carry their confidence and evidence "
        "(`application/file:start-end`).",
        "> Write notes between the `stc:manual` markers: they are kept when the map is "
        "regenerated.",
        "",
        "## Notes",
        "",
        f"<!-- stc:manual:begin section={NOTES_SECTION} -->",
        notes,
        "<!-- stc:manual:end -->",
        "",
        f"## Applications ({len(apps)})",
        "",
    ]
    if apps:
        out += ["| Application | Path | Purpose | Code version |", "|---|---|---|---|"]
        for a in apps:
            purpose = a.purpose or (
                "_No purpose in the catalog._" if a.has_catalog else "_No catalog yet._"
            )
            out.append(
                f"| `{a.name}` | `{a.path}` | {_cell(purpose)} | `{a.version or 'unknown'}` |"
            )
    else:
        out.append("_No applications are indexed._")

    out += ["", "## Relationships", ""]
    if relations:
        out += _mermaid(apps, relations)
        out += [
            "",
            "| From | To | Type | Confidence | Detail | Evidence |",
            "|---|---|---|---|---|---|",
        ]
        for r in relations:
            out.append(
                f"| `{by_id[r.source].name}` | `{by_id[r.target].name}` | {r.type} | {r.confidence}"
                f" | {_cell(r.detail)} | {r.evidence} |"
            )
    else:
        out.append("_No relations were detected between applications._")

    clusters = _clusters(apps, relations)
    out += ["", "## Groups of related applications", ""]
    out += (
        [f"{i}. {_names({a.name for a in g})}" for i, g in enumerate(clusters, 1)]
        if clusters
        else ["_No groups: no application is related to another._"]
    )

    out += ["", "## Shared databases", ""]
    databases = _shared(conn, "database")
    out += [
        f"- `{key}` — {_names(set().union(*roles.values()))}" for key, roles in databases.items()
    ] or ["_No database is used by more than one application._"]

    out += ["", "## Shared services (messaging topics)", ""]
    topics = _shared(conn, "messaging")
    out += [
        f"- `{key}` — publishers: {_names(roles.get('publishes', set())) or '_none_'};"
        f" subscribers: {_names(roles.get('subscribes', set())) or '_none_'}"
        for key, roles in topics.items()
    ] or ["_No messaging topic is shared by more than one application._"]

    out += ["", "## Common technologies (libraries used by several applications)", ""]
    libraries = _shared(conn, "library", "consumes")
    ranked = sorted(libraries.items(), key=lambda kv: (-len(kv[1]["consumes"]), kv[0]))
    out += [
        f"- `{key}` ({len(roles['consumes'])}) — {_names(roles['consumes'])}"
        for key, roles in ranked
    ] or ["_No library is used by more than one application._"]

    connected = {r.source for r in relations} | {r.target for r in relations}
    isolated = [a for a in apps if a.id not in connected]
    out += ["", "## Applications with no relationships", ""]
    out += [f"- `{a.name}`" for a in isolated] or ["_Every application is related to another._"]
    return "\n".join(out).rstrip() + "\n"


def write_global_map(conn: psycopg.Connection, catalogs_dir: Path) -> Path:
    path = global_map_path(catalogs_dir)
    manual = parse_manual_blocks(path.read_text(encoding="utf-8")) if path.is_file() else {}
    _write_atomic(path, build_global_map(conn, manual))
    return path
