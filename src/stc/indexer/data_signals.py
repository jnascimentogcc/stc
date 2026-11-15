"""Table signals for applications that are not written in COBOL: the tables their code reads and
writes, and the tables their models and migrations define (CAT-07).

*consumes* is SQL in a string literal and the query builders (`DB::table('x')`, `knex('x')`).
*provides* is a model that maps a table (Eloquent, SQLAlchemy, Django, TypeORM, Sequelize, JPA,
ActiveRecord, EF) or a migration that creates one; its detail starts with `model X` or
`migration`, which the documentation uses to connect the code that uses a model to its table.

Everything is read with patterns, never executed. A name that cannot be known statically (an
interpolated table, a table named by a variable) gives no signal rather than a guess.
"""

import re
from pathlib import PurePosixPath

from stc.indexer.mainframe_signals import _sql_tables
from stc.indexer.signal_types import Signal

DATA_LANGUAGES = frozenset(
    {
        "python", "javascript", "typescript", "java", "kotlin", "scala", "groovy", "csharp",
        "go", "rust", "ruby", "php", "swift", "vue", "svelte",
    }
)  # fmt: skip

MAX_LITERAL = 6_000
_SKIP_TABLES = frozenset(
    {"dual", "migrations", "information_schema", "sqlite_master", "sqlite_sequence", "sysdummy1"}
)


def data_signals(path: PurePosixPath, text: str, language: str | None) -> list[Signal]:
    if language not in DATA_LANGUAGES:
        return []
    text = text.replace("\r\n", "\n")
    found = _sql_strings(text)
    found += _query_builders(text, language)
    found += _models(path, text, language)
    found += _migrations(path, text, language)
    return found


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _signal(role: str, name: str, detail: str, first: int, last: int) -> Signal | None:
    table = name.strip().strip("`\"[]'").split(".")[-1].strip("`\"[]'")
    if not re.fullmatch(r"[A-Za-z_][\w$]*", table) or table.lower() in _SKIP_TABLES:
        return None
    return Signal("table", role, table.upper(), None, detail, first, last)


def _signals(items: list[Signal | None]) -> list[Signal]:
    return [s for s in items if s is not None]


# --------------------------------------------------------------------------------------------
# SQL inside string literals
# --------------------------------------------------------------------------------------------

_COMMENT_LINE = re.compile(r"^[ \t]*(?://|#|\*|/\*|--).*$", re.M)
_LITERAL = re.compile(
    r"""<<<['"]?(?P<tag>\w+)['"]?\n(?P<heredoc>.*?)\n[ \t]*(?P=tag)\b"""  # PHP heredoc
    r'''|"""(?P<triple_d>.*?)"""|\'\'\'(?P<triple_s>.*?)\'\'\''''
    r"""|`(?P<tick>[^`]*)`"""
    r"""|"(?P<dq>(?:\\.|[^"\\\n])*)"|'(?P<sq>(?:\\.|[^'\\\n])*)\'""",
    re.S,
)
_STATEMENT = re.compile(
    r"^\s*\(?\s*(?:"
    r"(?P<select>SELECT\s+(?P<columns>.+?)\s+FROM\s+[`\"\[\w])"
    r"|INSERT\s+(?:IGNORE\s+)?INTO\s+[`\"\[\w]"
    r"|UPDATE\s+[`\"\[\w][^;]*?\sSET\s"
    r"|DELETE\s+FROM\s+[`\"\[\w]"
    r"|(?:MERGE|REPLACE)\s+INTO\s+[`\"\[\w]"
    r")",
    re.I | re.S,
)
_COLUMN = re.compile(r"^(?:DISTINCT\s+)?[\w.*`\"\[\]$:{}?]+(?:\s+AS\s+[\w`\"]+)?$", re.I)
_GROUPS = re.compile(r"\([^()]*\)")
_QUALIFIER = re.compile(r"[`\"\]\[]\s*\.\s*[`\"\[]")


def _is_select_list(columns: str) -> bool:
    """`id, name` or `COUNT(*) AS n`, not the English `an option` of a sentence."""
    flat = columns
    for _ in range(4):
        flat = _GROUPS.sub("x", flat)
    return all(_COLUMN.match(" ".join(part.split())) for part in flat.split(",") if part.strip())


def _sql_strings(text: str) -> list[Signal]:
    masked = _COMMENT_LINE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    found: list[Signal | None] = []
    for match in _LITERAL.finditer(masked):
        content = next(
            (v for k, v in match.groupdict().items() if k != "tag" and v is not None), ""
        )
        if not content or len(content) > MAX_LITERAL:
            continue
        statement = _STATEMENT.match(content)
        if statement is None:
            continue
        if statement.group("select") and not _is_select_list(statement.group("columns")):
            continue
        first = _line(masked, match.start())
        last = _line(masked, match.end())
        cleaned = _QUALIFIER.sub(".", content).replace("`", " ").replace('"', " ")
        cleaned = cleaned.replace("[", " ").replace("]", " ")
        for name, action in _sql_tables(cleaned):
            found.append(_signal("consumes", name, f"SQL {action} {name}", first, last))
    return _signals(found)


# --------------------------------------------------------------------------------------------
# query builders
# --------------------------------------------------------------------------------------------

_BUILDERS = (
    re.compile(r"""\bDB::table\(\s*['"](\w+)['"]\s*\)"""),  # Laravel
    re.compile(r"""\bknex\(\s*['"`](\w+)['"`]\s*\)"""),  # Knex
    re.compile(r"""\.(?:from|into)\(\s*['"`](\w+)['"`]\s*\)"""),  # Knex, Kysely, Supabase
)
_WRITE_CALL = re.compile(
    r"(?:->|\.)\s*(?:insert|insertGetId|insertOrIgnore|update|updateOrInsert|upsert|delete|del|"
    r"truncate|increment|decrement)\s*\(",
    re.I,
)


def _query_builders(text: str, language: str | None) -> list[Signal]:
    if language not in ("php", "javascript", "typescript", "vue", "svelte"):
        return []
    found: list[Signal | None] = []
    for pattern in _BUILDERS:
        for match in pattern.finditer(text):
            window = text[match.end() : match.end() + 240].split(";")[0]
            action = "WRITES" if _WRITE_CALL.search(window) else "READS"
            line = _line(text, match.start())
            found.append(_signal("consumes", match[1], f"SQL {action} {match[1]}", line, line))
    return _signals(found)


# --------------------------------------------------------------------------------------------
# models: the table a class maps
# --------------------------------------------------------------------------------------------


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def plural(word: str) -> str:
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


_PHP_MODEL = re.compile(
    r"\bclass\s+(\w+)\s+extends\s+(?:\\?[\w\\]*\\)?(\w*Model|Pivot|Authenticatable)\b"
)
_PHP_TABLE = re.compile(r"""\$table\s*=\s*['"](\w+)['"]""")
_PY_CLASS = re.compile(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:", re.M)
_PY_TABLENAME = re.compile(r"""^\s+__tablename__\s*=\s*['"](\w+)['"]""", re.M)
_PY_DB_TABLE = re.compile(r"""^\s+db_table\s*=\s*['"](\w+)['"]""", re.M)
_CLASS_NAME = re.compile(r"\b(?:class|interface)\s+(\w+)")
_TS_ENTITY = re.compile(r"""@Entity\(\s*(?:\{\s*name:\s*)?(?:['"`](\w+)['"`])?[^)]*\)""")
_TABLE_NAME = re.compile(r"""\btableName\s*(?::|=)\s*['"`](\w+)['"`]""")
_TABLE_GETTER = re.compile(r"""static\s+get\s+tableName\(\)\s*\{\s*return\s*['"`](\w+)['"`]""")
_JPA_TABLE = re.compile(r'@Table\(\s*(?:name\s*=\s*)?"(\w+)"')
_JPA_ENTITY = re.compile(r"@Entity\b")
_CS_TABLE = re.compile(r'\[Table\(\s*"(\w+)"')
_RB_MODEL = re.compile(r"^\s*class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base)\b", re.M)
_RB_TABLE = re.compile(r"""self\.table_name\s*=\s*['"](\w+)['"]""")


def _model(name: str, table: str, line: int) -> Signal | None:
    return _signal("provides", table, f"model {name}", line, line)


def _class_after(text: str, position: int, window: int = 600) -> tuple[str, int] | None:
    match = _CLASS_NAME.search(text, position, position + window)
    return (match[1], _line(text, match.start())) if match else None


def _class_before(text: str, position: int) -> tuple[str, int] | None:
    last = None
    for match in _CLASS_NAME.finditer(text, 0, position):
        last = match
    return (last[1], _line(text, last.start())) if last else None


def _models(path: PurePosixPath, text: str, language: str | None) -> list[Signal]:
    found: list[Signal | None] = []
    if language == "php":
        for match in _PHP_MODEL.finditer(text):
            name = match[1]
            body = text[match.end() :]
            nxt = _PHP_MODEL.search(body)
            body = body[: nxt.start()] if nxt else body
            declared = _PHP_TABLE.search(body)
            table = declared[1] if declared else plural(snake(name))
            found.append(_model(name, table, _line(text, match.start())))
    elif language == "python":
        found += _python_models(path, text)
    elif language in ("javascript", "typescript"):
        for match in _TS_ENTITY.finditer(text):
            target = _class_after(text, match.end())
            if target:
                found.append(_model(target[0], match[1] or snake(target[0]), target[1]))
        for pattern in (_TABLE_NAME, _TABLE_GETTER):
            for match in pattern.finditer(text):
                owner = _class_before(text, match.start())
                found.append(
                    _model(owner[0] if owner else path.stem, match[1], _line(text, match.start()))
                )
    elif language in ("java", "kotlin"):
        for match in _JPA_ENTITY.finditer(text):
            target = _class_after(text, match.end())
            if target is None:
                continue
            around = text[max(0, match.start() - 300) : match.end() + 300]
            declared = _JPA_TABLE.search(around)
            found.append(
                _model(target[0], declared[1] if declared else snake(target[0]), target[1])
            )
    elif language == "csharp":
        for match in _CS_TABLE.finditer(text):
            target = _class_after(text, match.end())
            if target:
                found.append(_model(target[0], match[1], target[1]))
    elif language == "ruby":
        for match in _RB_MODEL.finditer(text):
            name = match[1]
            declared = _RB_TABLE.search(text, match.end())
            found.append(
                _model(
                    name,
                    declared[1] if declared else plural(snake(name)),
                    _line(text, match.start()),
                )
            )
    return _signals(found)


def _python_models(path: PurePosixPath, text: str) -> list[Signal | None]:
    found: list[Signal | None] = []
    classes = list(_PY_CLASS.finditer(text))
    django = "django" in text
    for index, match in enumerate(classes):
        end = classes[index + 1].start() if index + 1 < len(classes) else len(text)
        body = text[match.end() : end]
        name, bases = match[1], match[2]
        line = _line(text, match.start())
        if declared := _PY_TABLENAME.search(body):
            found.append(_model(name, declared[1], line))
        elif django and re.search(r"\w*Model\w*\s*$|\bmodels\.Model\b", bases.strip()):
            declared = _PY_DB_TABLE.search(body)
            app = path.parent.name
            table = declared[1] if declared else (f"{app}_{name.lower()}" if app else "")
            if table:
                found.append(_model(name, table, line))
    return found


# --------------------------------------------------------------------------------------------
# migrations: the tables they create
# --------------------------------------------------------------------------------------------

_CREATE_CALLS = (
    re.compile(r"""\bSchema::create\(\s*['"](\w+)['"]"""),  # Laravel
    re.compile(r"""\bop\.create_table\(\s*['"](\w+)['"]"""),  # Alembic
    re.compile(r"""\.createTable\(\s*['"`](\w+)['"`]"""),  # Knex, Sequelize
    re.compile(r"""^\s*create_table\s+[:'"](\w+)""", re.M),  # Rails
)
_DJANGO_CREATE = re.compile(r"""migrations\.CreateModel\(\s*name\s*=\s*['"](\w+)['"]""")


def _migrations(path: PurePosixPath, text: str, language: str | None) -> list[Signal]:
    found: list[Signal | None] = []
    for pattern in _CREATE_CALLS:
        for match in pattern.finditer(text):
            line = _line(text, match.start(1))
            found.append(_signal("provides", match[1], f"migration creates {match[1]}", line, line))
    if language == "python":
        app = path.parent.parent.name
        for match in _DJANGO_CREATE.finditer(text):
            if app:
                table = f"{app}_{match[1].lower()}"
                line = _line(text, match.start(1))
                found.append(_signal("provides", table, f"migration creates {table}", line, line))
    return _signals(found)
