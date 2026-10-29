"""Pieces shared by the query modules: paging, scope filters and application lookup."""

import psycopg

from stc.query.models import QueryError

DEFAULT_LIMIT = 10
MAX_LIMIT = 50  # keeps a response small enough for a model's context (MCP-06)
SNIPPET_CHARS = 1_200
LINE_CHARS = 300
# Chunks of files that are searchable: secret, excluded and failed files never are.
SEARCHABLE = "f.status IN ('indexed', 'unsupported')"


def page_args(limit: int, offset: int) -> tuple[int, int]:
    if offset < 0:
        raise QueryError("invalid_argument", "offset must be 0 or more.")
    if limit < 1:
        raise QueryError("invalid_argument", "limit must be 1 or more.")
    return min(limit, MAX_LIMIT), offset


def known_applications(conn: psycopg.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute("SELECT name FROM applications ORDER BY name")]


def require_application(conn: psycopg.Connection, name: str) -> None:
    known = known_applications(conn)
    if name not in known:
        raise QueryError(
            "unknown_application",
            f"Unknown application {name!r}. Indexed applications: " + (", ".join(known) or "none"),
        )


def like_pattern(path: str) -> str:
    """A path prefix or a glob (`*`, `?`) as a LIKE pattern."""
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if "*" not in path and "?" not in path:
        return escaped + "%"
    return escaped.replace("*", "%").replace("?", "_")


def scope_sql(
    conn: psycopg.Connection,
    applications: list[str] | None,
    languages: list[str] | None,
    paths: list[str] | None,
) -> tuple[str, list[object]]:
    """AND-ed filters over the aliases `a` (applications) and `f` (files)."""
    clauses: list[str] = []
    params: list[object] = []
    if applications:
        known = known_applications(conn)
        unknown = [n for n in applications if n not in known]
        if unknown:
            raise QueryError(
                "unknown_application",
                f"Unknown application(s) {', '.join(map(repr, unknown))}. Indexed applications: "
                + (", ".join(known) or "none"),
            )
        clauses.append("a.name = ANY(%s)")
        params.append(applications)
    if languages:
        clauses.append("lower(f.language) = ANY(%s)")
        params.append([x.lower() for x in languages])
    if paths:
        clauses.append("f.path LIKE ANY(%s)")
        params.append([like_pattern(p) for p in paths])
    return "".join(f" AND {c}" for c in clauses), params


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…"
