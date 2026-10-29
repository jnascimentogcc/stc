"""Assembles, for each catalog section, the evidence the LLM may use.

Code, not the model, decides what is relevant: README/docs, manifests, entry points, entity
definitions, configuration, endpoint handlers, plus facts from the static analysis. Everything is
read from the database (chunks, symbols, signals, relations), so the Root need not be mounted.
Each item carries `path:lines` so claims can cite it and the citation can be verified.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

import psycopg

MAX_CONTEXT_CHARS = 36_000  # per section, about 9k tokens
MAX_ITEM_CHARS = 3_000

_DOC_LANGUAGES = "('markdown', 'restructuredtext', 'text')"
_MANIFEST_NAMES = (
    "package.json", "pyproject.toml", "setup.py", "requirements.txt", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "go.mod", "Cargo.toml", "composer.json", "Gemfile",
    "global.json", "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".nvmrc",
    ".tool-versions", "Makefile",
)  # fmt: skip
_ENTRY_POINT = (
    r"(^|/)(main|app|server|index|program|startup|application|manage|wsgi|asgi|cli|run)\.[a-z]+$"
    r"|(^|/)[A-Za-z]*Application\.(java|kt)$"
)
_ENTITY_PATH = r"(model|entit|schema|domain|migration|dto|orm|prisma|tables?)"
_ENTITY_CONTENT = (
    r"(@Entity|@Table|CREATE TABLE|db\.Model|models\.Model|\(Base\)|DbSet<|@Document|\[Table"
    r"|mongoose\.Schema|sequelize\.define|@Schema\(|BaseModel\))"
)
_CONFIG_LANGUAGES = "('yaml', 'json', 'toml', 'ini', 'properties', 'dotenv', 'xml')"
_CONFIG_PATH = r"(config|settings|application|appsettings|\.env|docker-compose|values)"
_ENV_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"""os\.environ(?:\.get)?\s*[\(\[]\s*['"]([A-Z][A-Z0-9_]+)""",
        r"""\b(?:os\.getenv|getenv|os\.Getenv)\(\s*['"]([A-Z][A-Z0-9_]+)""",
        r"""process\.env\.([A-Z][A-Z0-9_]+)""",
        r"""process\.env\[\s*['"]([A-Z][A-Z0-9_]+)""",
        r"""System\.getenv\(\s*"([A-Z][A-Z0-9_]+)""",
        r"""Environment\.GetEnvironmentVariable\(\s*"([A-Z][A-Z0-9_]+)""",
        r"""\bENV\[\s*['"]([A-Z][A-Z0-9_]+)""",
        r"""\$\{([A-Z][A-Z0-9_]+)(?::[^}]*)?\}""",
    )
)


@dataclass(frozen=True)
class ContextItem:
    path: str
    start_line: int
    end_line: int
    note: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass
class SectionContext:
    items: list[ContextItem] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.items and not self.facts

    @property
    def size(self) -> int:
        return sum(len(i.text) for i in self.items) + sum(len(f) for f in self.facts)


@dataclass
class AppContext:
    app_id: int
    name: str
    path: str
    code_version: str | None
    files: dict[str, int]  # citable files -> number of lines
    sections: dict[str, SectionContext]


class UnknownApplication(Exception):
    pass


def build_context(conn: psycopg.Connection, application: str) -> AppContext:
    row = conn.execute(
        "SELECT id, name, path, indexed_version FROM applications WHERE name = %s", (application,)
    ).fetchone()
    if row is None:
        raise UnknownApplication(application)
    app_id = int(row[0])
    files = {
        str(p): int(n)
        for p, n in conn.execute(
            "SELECT f.path, max(c.end_line) FROM files f JOIN chunks c ON c.file_id = f.id"
            " WHERE f.application_id = %s AND f.status IN ('indexed', 'unsupported')"
            " GROUP BY f.path",
            (app_id,),
        )
    }
    endpoints = _endpoint_facts(conn, app_id)
    docs = _chunks(
        conn, app_id,
        f"f.language IN {_DOC_LANGUAGES} AND f.path ~* '(readme|overview|docs?/|architecture|about)'",  # noqa: E501
        10,
        order="(f.path ~* 'readme') DESC, f.path, c.start_line",
    )  # fmt: skip
    entries = _chunks(conn, app_id, f"f.path ~* '{_ENTRY_POINT}'", 4)
    purpose = SectionContext(
        _pack(docs + entries),
        [*_language_facts(conn, app_id), *_directory_facts(conn, app_id), *endpoints[:30]],
    )
    sections = {
        "purpose": purpose,
        "business": purpose,
        "stack": SectionContext(
            _pack(_chunks(conn, app_id, _manifest_where(), 14, per_file=2)),
            _language_facts(conn, app_id),
        ),
        "modules": SectionContext(
            _pack(entries), [*_directory_facts(conn, app_id), *_symbol_facts(conn, app_id)]
        ),
        "data_model": SectionContext(
            _pack(
                _chunks(
                    conn,
                    app_id,
                    f"(f.path ~* '{_ENTITY_PATH}' AND c.kind <> 'module') OR c.content ~ '{_ENTITY_CONTENT}'",  # noqa: E501
                    25,
                )  # fmt: skip
            ),
            [],
        ),
        "configuration": _configuration(conn, app_id),
        "flows": SectionContext(
            _pack(entries + _handler_chunks(conn, app_id)),
            [*endpoints[:40], *_messaging_facts(conn, app_id), *_relation_facts(conn, app_id)],
        ),
    }
    return AppContext(app_id, str(row[1]), str(row[2]), row[3], files, sections)


# --------------------------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------------------------


def _chunks(
    conn: psycopg.Connection,
    app_id: int,
    where: str,
    limit: int,
    *,
    order: str = "f.path, c.start_line",
    per_file: int | None = None,
) -> list[ContextItem]:
    rows = conn.execute(
        (
            "SELECT f.path, c.start_line, c.end_line, c.kind, c.name, c.content"
            " FROM chunks c JOIN files f ON f.id = c.file_id"
            " WHERE f.application_id = %s AND f.status IN ('indexed', 'unsupported')"
            f" AND ({where}) ORDER BY {order} LIMIT %s"
        ),
        (app_id, limit * (per_file or 1) * 3 if per_file else limit),
    ).fetchall()
    items: list[ContextItem] = []
    seen: Counter[str] = Counter()
    for path, start, end, kind, name, content in rows:
        if per_file and seen[str(path)] >= per_file:
            continue
        seen[str(path)] += 1
        note = f"{kind} {name}" if name else str(kind)
        items.append(ContextItem(str(path), int(start), int(end), note, str(content)))
        if len(items) >= limit:
            break
    return items


def _manifest_where() -> str:
    names = ", ".join(f"'{n}'" for n in _MANIFEST_NAMES)
    return f"regexp_replace(f.path, '^.*/', '') IN ({names}) OR f.path ~* '\\.(csproj|fsproj)$'"


def _pack(items: list[ContextItem]) -> list[ContextItem]:
    """Keep items in priority order until the section budget is used up."""
    packed: list[ContextItem] = []
    used = 0
    for item in items:
        text = item.text[:MAX_ITEM_CHARS]
        if used + len(text) > MAX_CONTEXT_CHARS:
            break
        used += len(text)
        packed.append(ContextItem(item.path, item.start_line, item.end_line, item.note, text))
    return packed


def _language_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT COALESCE(language, 'unknown'), count(*) FROM files WHERE application_id = %s"
        " AND status IN ('indexed', 'unsupported') GROUP BY 1 ORDER BY 2 DESC LIMIT 12",
        (app_id,),
    ).fetchall()
    return (
        ["Languages by file count: " + ", ".join(f"{lang} {n}" for lang, n in rows)] if rows else []
    )


def _directory_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT split_part(path, '/', 1) AS top, count(*) FROM files WHERE application_id = %s"
        " AND status IN ('indexed', 'unsupported') AND position('/' in path) > 0"
        " GROUP BY 1 ORDER BY 2 DESC LIMIT 25",
        (app_id,),
    ).fetchall()
    return [f"Top-level directory {top}/ has {n} file(s)" for top, n in rows]


def _symbol_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT f.path, s.kind, s.name, s.start_line FROM symbols s JOIN files f ON f.id = s.file_id"  # noqa: E501
        " WHERE f.application_id = %s AND s.is_definition AND s.kind IN"
        " ('class', 'interface', 'struct', 'enum', 'trait', 'module', 'type', 'object', 'record')"
        " ORDER BY f.path, s.start_line LIMIT 150",
        (app_id,),
    ).fetchall()
    return [f"Definition {kind} {name} at {path}:{line}" for path, kind, name, line in rows]


def _endpoint_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT s.detail, f.path, s.start_line FROM signals s JOIN files f ON f.id = s.file_id"
        " WHERE s.application_id = %s AND s.kind = 'http' AND s.role = 'provides'"
        " ORDER BY f.path, s.start_line",
        (app_id,),
    ).fetchall()
    return [f"Endpoint {detail} defined at {path}:{line}" for detail, path, line in rows]


def _messaging_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT s.role, s.detail, f.path, s.start_line FROM signals s JOIN files f ON f.id = s.file_id"  # noqa: E501
        " WHERE s.application_id = %s AND s.kind = 'messaging' ORDER BY f.path, s.start_line",
        (app_id,),
    ).fetchall()
    return [f"Topic {detail} ({role}) at {path}:{line}" for role, detail, path, line in rows]


def _relation_facts(conn: psycopg.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT s.name, t.name, r.type, r.confidence FROM active_relations r"
        " JOIN applications s ON s.id = r.source_app_id JOIN applications t ON t.id = r.target_app_id"  # noqa: E501
        " WHERE r.source_app_id = %s OR r.target_app_id = %s ORDER BY 1, 2",
        (app_id, app_id),
    ).fetchall()
    return [f"Relation {s} -> {t} ({kind}, {conf} confidence)" for s, t, kind, conf in rows]


def _handler_chunks(conn: psycopg.Connection, app_id: int) -> list[ContextItem]:
    rows = conn.execute(
        "SELECT DISTINCT ON (c.id) f.path, c.start_line, c.end_line, c.kind, c.name, c.content"
        " FROM signals s JOIN files f ON f.id = s.file_id JOIN chunks c ON c.file_id = f.id"
        " AND s.start_line BETWEEN c.start_line AND c.end_line"
        " WHERE s.application_id = %s AND s.kind IN ('http', 'messaging')"
        " AND s.role IN ('provides', 'subscribes') ORDER BY c.id LIMIT 15",
        (app_id,),
    ).fetchall()
    return [
        ContextItem(str(p), int(a), int(b), f"{k} {n}" if n else str(k), str(t))
        for p, a, b, k, n, t in rows
    ]


def _configuration(conn: psycopg.Connection, app_id: int) -> SectionContext:
    items = _chunks(
        conn, app_id,
        f"f.language IN {_CONFIG_LANGUAGES} AND f.path ~* '{_CONFIG_PATH}'", 15,
    )  # fmt: skip
    rows = conn.execute(
        "SELECT f.path, c.start_line, c.content FROM chunks c JOIN files f ON f.id = c.file_id"
        " WHERE f.application_id = %s AND f.status IN ('indexed', 'unsupported')"
        " AND c.content ~* '(environ|getenv|process\\.env|Environment\\.|ENV\\[|\\$\\{)'"
        " ORDER BY f.path, c.start_line LIMIT 400",
        (app_id,),
    ).fetchall()
    found: dict[str, str] = {}
    for path, start, content in rows:
        for number, line in enumerate(str(content).split("\n")):
            for pattern in _ENV_PATTERNS:
                for match in pattern.finditer(line):
                    found.setdefault(match.group(1), f"{path}:{int(start) + number}")
    facts = [
        f"Environment variable {name} read at {where}" for name, where in sorted(found.items())
    ]
    return SectionContext(_pack(items), facts[:150])
