"""Searching the code and the catalogs (QRY-04, QRY-05, QRY-07, QRY-11).

`search_code` fuses three rankings with reciprocal rank fusion: semantic (embedding distance),
lexical (full-text over the chunk) and by name (trigram similarity of the chunk's symbol name).
Without an embedder the semantic leg is skipped and the result says so. `grep` and `find_symbol`
are exact. Only chunks of searchable files exist in the index, so secrets can never be returned."""

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Protocol

import psycopg

from stc.indexer.signals import is_test_path
from stc.query.common import (
    LINE_CHARS,
    SEARCHABLE,
    page_args,
    scope_sql,
    snippet,
)
from stc.query.models import (
    CatalogHit,
    CodeHit,
    LineHit,
    Page,
    QueryError,
    Source,
    SymbolHit,
)

RRF_K = 60
_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
NO_MATCH_NOTE = "No matches. The indexed code may simply not contain this; do not assume it exists."
TEST_PENALTY = 0.5  # test files keep half of their score
_ABOUT_TESTS = re.compile(
    r"\b(?:tests?|testing|specs?|e2e|fixtures?|mocks?|testes?)\b", re.IGNORECASE
)
TESTS_NOTE = (
    "Test files are ranked below the code they test; mention tests in the query to rank them "
    "like any other file."
)


class QueryEmbedder(Protocol):
    """Embeds one query text with the model the chunks were embedded with."""

    provider: str
    model: str

    def __call__(self, text: str) -> list[float]: ...


def query_terms(query: str) -> list[str]:
    """Distinct lowercase words of the query, plus the parts of camelCase and snake_case words."""
    terms: dict[str, None] = {}
    for word in _WORD.findall(query):
        terms[word.lower()] = None
        for part in _CAMEL.findall(word.replace("_", " ")):
            if len(part) >= 2:
                terms[part.lower()] = None
    return list(terms)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vector) + "]"


def _semantic_ids(
    conn: psycopg.Connection,
    embedder: QueryEmbedder,
    query: str,
    scope: str,
    scope_params: list[object],
    depth: int,
) -> list[int]:
    vector = _vector_literal(embedder(query))
    sql = (
        "SELECT c.id FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id"
        " JOIN files f ON f.id = c.file_id JOIN applications a ON a.id = f.application_id"
        f" WHERE e.provider = %s AND e.model = %s AND {SEARCHABLE}{scope}"
        " ORDER BY e.embedding <=> %s::vector LIMIT %s"
    )
    with conn.transaction():
        try:
            with conn.transaction():  # a savepoint: older pgvector lacks this setting
                conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        except psycopg.Error:
            pass
        rows = conn.execute(
            sql, [embedder.provider, embedder.model, *scope_params, vector, depth]
        ).fetchall()
    return [int(r[0]) for r in rows]


def _lexical_ids(
    conn: psycopg.Connection, terms: list[str], scope: str, scope_params: list[object], depth: int
) -> list[int]:
    if not terms:
        return []
    tsquery = " | ".join(terms)
    sql = (
        "SELECT c.id FROM chunks c JOIN files f ON f.id = c.file_id"
        " JOIN applications a ON a.id = f.application_id,"
        " to_tsquery('simple', %s) q"
        f" WHERE c.tsv @@ q AND {SEARCHABLE}{scope}"
        " ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id LIMIT %s"
    )
    return [int(r[0]) for r in conn.execute(sql, [tsquery, *scope_params, depth]).fetchall()]


def _name_ids(
    conn: psycopg.Connection, terms: list[str], scope: str, scope_params: list[object], depth: int
) -> list[int]:
    terms = [t for t in terms if len(t) >= 3]
    if not terms:
        return []
    sql = (
        "SELECT c.id FROM unnest(%s::text[]) AS t(term)"
        " JOIN chunks c ON c.name %% t.term"
        " JOIN files f ON f.id = c.file_id JOIN applications a ON a.id = f.application_id"
        f" WHERE {SEARCHABLE}{scope}"
        " GROUP BY c.id ORDER BY max(similarity(c.name, t.term)) DESC, c.id LIMIT %s"
    )
    return [int(r[0]) for r in conn.execute(sql, [terms, *scope_params, depth]).fetchall()]


def _demote_tests(conn: psycopg.Connection, query: str, score: dict[int, float]) -> set[int]:
    """Test files rank lower: they repeat the words of the code they test, and the code is
    usually what is being asked about. They are not hidden, and a query that is about tests
    ranks them as any other file. Returns the chunks that were demoted."""
    if not score or _ABOUT_TESTS.search(query):
        return set()
    rows = conn.execute(
        "SELECT c.id, f.path FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ANY(%s)",
        (list(score),),
    ).fetchall()
    demoted = {int(i) for i, path in rows if is_test_path(PurePosixPath(str(path)))}
    for chunk_id in demoted:
        score[chunk_id] *= TEST_PENALTY
    return demoted


def search_code(
    conn: psycopg.Connection,
    embedder: QueryEmbedder | None,
    query: str,
    *,
    applications: list[str] | None = None,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> Page[CodeHit]:
    if not query.strip():
        raise QueryError("invalid_argument", "The query is empty.")
    limit, offset = page_args(limit, offset)
    scope, scope_params = scope_sql(conn, applications, languages, paths)
    depth = min(300, max(60, (offset + limit) * 4))
    terms = query_terms(query)
    notes: list[str] = []

    legs: dict[str, list[int]] = {}
    if embedder is not None:
        try:
            legs["semantic"] = _semantic_ids(conn, embedder, query, scope, scope_params, depth)
        except QueryError as exc:
            legs["semantic"] = []
            notes.append(f"{exc.message} Falling back to lexical search only.")
        else:
            if not legs["semantic"]:
                notes.append(
                    f"No chunk is embedded with {embedder.provider}/{embedder.model} in this "
                    "scope: the semantic part found nothing (run `stc embed`)."
                )
    else:
        notes.append("Semantic search is not available (no embeddings client): lexical only.")
    legs["lexical"] = _lexical_ids(conn, terms, scope, scope_params, depth)
    legs["name"] = _name_ids(conn, terms, scope, scope_params, depth)

    score: dict[int, float] = defaultdict(float)
    matched: dict[int, list[str]] = defaultdict(list)
    for leg, ids in legs.items():
        for rank, chunk_id in enumerate(ids, start=1):
            score[chunk_id] += 1.0 / (RRF_K + rank)
            matched[chunk_id].append(leg)
    penalised = _demote_tests(conn, query, score)
    ordered = sorted(score, key=lambda i: (-score[i], i))
    window = ordered[offset : offset + limit]
    has_more = len(ordered) > offset + limit
    if not ordered:
        notes.append(NO_MATCH_NOTE)
        return Page([], offset, limit, False, notes)
    if penalised.intersection(window):
        notes.append(TESTS_NOTE)

    rows = conn.execute(
        "SELECT c.id, a.name, f.path, c.start_line, c.end_line, a.indexed_version, c.kind,"
        " c.name, c.content FROM chunks c JOIN files f ON f.id = c.file_id"
        " JOIN applications a ON a.id = f.application_id WHERE c.id = ANY(%s)",
        (window,),
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    hits = []
    for chunk_id in window:
        _id, app, path, start, end, version, kind, name, content = by_id[chunk_id]
        hits.append(
            CodeHit(
                Source(app, path, start, end, version),
                kind,
                name,
                snippet(content),
                round(score[chunk_id], 6),
                matched[chunk_id],
            )
        )
    return Page(hits, offset, limit, has_more, notes)


def grep(
    conn: psycopg.Connection,
    pattern: str,
    *,
    regex: bool = False,
    case_sensitive: bool = True,
    applications: list[str] | None = None,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[LineHit]:
    """Lines matching a literal text or a regular expression, in file order."""
    if not pattern:
        raise QueryError("invalid_argument", "The pattern is empty.")
    limit, offset = page_args(limit, offset)
    scope, scope_params = scope_sql(conn, applications, languages, paths)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        line_re = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as exc:
        raise QueryError("invalid_argument", f"Invalid regular expression: {exc}") from exc

    if regex:
        operator = "~" if case_sensitive else "~*"
        condition, value = f"c.content {operator} %s", pattern
    else:
        escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condition = "c.content " + ("LIKE" if case_sensitive else "ILIKE") + " %s"
        value = f"%{escaped}%"
    sql = (
        "SELECT a.name, f.path, c.start_line, a.indexed_version, c.content FROM chunks c"
        " JOIN files f ON f.id = c.file_id JOIN applications a ON a.id = f.application_id"
        f" WHERE {condition} AND {SEARCHABLE}{scope}"
        " ORDER BY a.name, f.path, c.start_line, c.id"
    )
    wanted = offset + limit + 1
    hits: list[LineHit] = []
    seen: set[tuple[str, str, int]] = set()
    try:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '30s'")
            with conn.cursor(name="stc_grep") as cursor:
                cursor.execute(sql, [value, *scope_params])
                for app, path, first, version, content in cursor:
                    for number, line in enumerate(content.splitlines(), start=first):
                        if (app, path, number) in seen or not line_re.search(line):
                            continue
                        seen.add((app, path, number))
                        hits.append(
                            LineHit(Source(app, path, number, number, version), line[:LINE_CHARS])
                        )
                    if len(hits) >= wanted:
                        break
    except psycopg.errors.QueryCanceled as exc:
        raise QueryError(
            "invalid_argument", "The search took too long; narrow it with applications or paths."
        ) from exc
    except psycopg.errors.DataError as exc:
        raise QueryError("invalid_argument", f"Invalid pattern: {exc}") from exc
    notes = [] if hits else [NO_MATCH_NOTE]
    return Page(hits[offset : offset + limit], offset, limit, len(hits) > offset + limit, notes)


def find_symbol(
    conn: psycopg.Connection,
    name: str,
    *,
    kind: str | None = None,
    exact: bool = True,
    include_usages: bool = False,
    applications: list[str] | None = None,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[SymbolHit]:
    """Definitions (and optionally usages) of a symbol, definitions first."""
    if not name.strip():
        raise QueryError("invalid_argument", "The symbol name is empty.")
    limit, offset = page_args(limit, offset)
    scope, scope_params = scope_sql(conn, applications, languages, paths)
    clauses = ["s.name = %s"] if exact else ["s.name ILIKE %s"]
    params: list[object] = [name if exact else f"%{name}%"]
    if kind:
        clauses.append("s.kind = %s")
        params.append(kind)
    if not include_usages:
        clauses.append("s.is_definition")
    rows = conn.execute(
        "SELECT a.name, f.path, s.start_line, s.end_line, a.indexed_version, s.name, s.kind,"
        " s.is_definition FROM symbols s JOIN files f ON f.id = s.file_id"
        " JOIN applications a ON a.id = f.application_id"
        f" WHERE {' AND '.join(clauses)} AND {SEARCHABLE}{scope}"
        " ORDER BY NOT s.is_definition, a.name, f.path, s.start_line LIMIT %s OFFSET %s",
        [*params, *scope_params, limit + 1, offset],
    ).fetchall()
    hits = [
        SymbolHit(Source(app, path, start, end, version), sym, sym_kind, bool(is_def))
        for app, path, start, end, version, sym, sym_kind, is_def in rows[:limit]
    ]
    return Page(hits, offset, limit, len(rows) > limit, [] if hits else [NO_MATCH_NOTE])


def _catalog_semantic_ids(
    conn: psycopg.Connection,
    embedder: QueryEmbedder,
    query: str,
    scope: str,
    scope_params: list[object],
    depth: int,
) -> list[int]:
    vector = _vector_literal(embedder(query))
    sql = (
        "SELECT cs.id FROM catalog_section_embeddings e"
        " JOIN catalog_sections cs ON cs.id = e.section_id"
        " JOIN catalogs c ON c.id = cs.catalog_id JOIN applications a ON a.id = c.application_id"
        f" WHERE e.provider = %s AND e.model = %s{scope}"
        " ORDER BY e.embedding <=> %s::vector LIMIT %s"
    )
    rows = conn.execute(
        sql, [embedder.provider, embedder.model, *scope_params, vector, depth]
    ).fetchall()
    return [int(r[0]) for r in rows]


def search_catalogs(
    conn: psycopg.Connection,
    embedder: QueryEmbedder | None,
    query: str,
    *,
    applications: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> Page[CatalogHit]:
    """Search the catalog sections (functional knowledge) by meaning and by words together,
    apart from the code."""
    if not query.strip():
        raise QueryError("invalid_argument", "The query is empty.")
    limit, offset = page_args(limit, offset)
    scope, scope_params = scope_sql(conn, applications, None, None)
    terms = query_terms(query)
    if not terms:
        raise QueryError("invalid_argument", "The query has no searchable words.")
    depth = min(300, max(60, (offset + limit) * 4))
    notes: list[str] = []

    legs: dict[str, list[int]] = {}
    if embedder is not None:
        try:
            legs["semantic"] = _catalog_semantic_ids(
                conn, embedder, query, scope, scope_params, depth
            )
        except QueryError as exc:
            legs["semantic"] = []
            notes.append(f"{exc.message} Falling back to word search only.")
        else:
            if not legs["semantic"]:
                notes.append(
                    "No catalog section is embedded with "
                    f"{embedder.provider}/{embedder.model} in this scope: the semantic part "
                    "found nothing (run `stc embed`)."
                )
    else:
        notes.append("Semantic search is not available (no embeddings client): words only.")
    legs["lexical"] = [
        int(r[0])
        for r in conn.execute(
            "SELECT cs.id FROM catalog_sections cs JOIN catalogs c ON c.id = cs.catalog_id"
            " JOIN applications a ON a.id = c.application_id, to_tsquery('simple', %s) q"
            f" WHERE cs.tsv @@ q{scope}"
            " ORDER BY ts_rank_cd(cs.tsv, q) DESC, cs.id LIMIT %s",
            [" | ".join(terms), *scope_params, depth],
        ).fetchall()
    ]

    score: dict[int, float] = defaultdict(float)
    matched: dict[int, list[str]] = defaultdict(list)
    for leg, ids in legs.items():
        for rank, section_id in enumerate(ids, start=1):
            score[section_id] += 1.0 / (RRF_K + rank)
            matched[section_id].append(leg)
    ordered = sorted(score, key=lambda i: (-score[i], i))
    window = ordered[offset : offset + limit]
    if not ordered:
        notes.append(NO_MATCH_NOTE)
        return Page([], offset, limit, False, notes)
    rows = conn.execute(
        "SELECT cs.id, a.name, cs.section, cs.content, c.code_version FROM catalog_sections cs"
        " JOIN catalogs c ON c.id = cs.catalog_id JOIN applications a ON a.id = c.application_id"
        " WHERE cs.id = ANY(%s)",
        (window,),
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    hits = [
        CatalogHit(
            by_id[i][1],
            by_id[i][2],
            snippet(by_id[i][3]),
            round(score[i], 6),
            by_id[i][4],
            matched[i],
        )
        for i in window
    ]
    return Page(hits, offset, limit, len(ordered) > offset + limit, notes)


__all__ = [
    "QueryEmbedder",
    "find_symbol",
    "grep",
    "query_terms",
    "search_catalogs",
    "search_code",
]
