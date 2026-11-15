"""Tables and columns that code defines, for the entity-relationship diagram: Laravel migrations,
Alembic `op.create_table` and SQLAlchemy models.

Like `ddl.py` it is a forgiving reader of text, never an interpreter: a column it does not
understand is left out, and nothing is executed or imported. Line numbers are those of the file."""

import re

from stc.docs.ddl import Column, Table, _split_top_level
from stc.indexer.data_signals import plural

_QUOTED = re.compile(r"""^\s*['"]([^'"]*)['"]""")
_FIRST_STRING = re.compile(r"""['"]([\w.]+)['"]""")


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _balanced(text: str, start: int, open_: str = "(", close: str = ")") -> int:
    """Index just after the bracket that closes the one at `start`, or -1."""
    depth, quote = 0, ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == open_:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _string(argument: str) -> str | None:
    match = _QUOTED.match(argument)
    return match.group(1) if match else None


# --------------------------------------------------------------------------------------------
# Laravel migrations
# --------------------------------------------------------------------------------------------

_SCHEMA_CREATE = re.compile(r"""Schema::create\(\s*['"](\w+)['"]""")
_SKIPPED = re.compile(
    r"^(?:index|unique|fullText|spatialIndex|drop\w*|rename\w*|comment|engine|charset|collation|"
    r"temporary|after|dropIfExists)$"
)
_ARITY = {"string", "char", "decimal", "double", "float", "unsignedDecimal"}


def _statements(body: str) -> list[str]:
    parts: list[str] = []
    depth, quote = 0, ""
    current: list[str] = []
    for char in body:
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == ";" and depth == 0 and not quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    return [p.strip() for p in parts if p.strip()]


def _calls(statement: str) -> list[tuple[str, list[str]]]:
    """`$t->string('a', 10)->nullable()` -> [("string", ["'a'", "10"]), ("nullable", [])]."""
    calls: list[tuple[str, list[str]]] = []
    position = statement.find("->")
    while position != -1:
        match = re.compile(r"->\s*(\w+)\s*\(").match(statement, position)
        if match is None:
            break
        end = _balanced(statement, match.end() - 1)
        if end == -1:
            break
        inner = statement[match.end() : end - 1]
        calls.append((match.group(1), _split_top_level(inner) if inner.strip() else []))
        position = statement.find("->", end)
    return calls


def _php_column(
    name: str, kind: str, args: list[str], chain: list[tuple[str, list[str]]]
) -> Column:
    chain_names = {c for c, _ in chain}
    precision = [a.strip() for a in args[1:3] if a.strip().lstrip("-").isdigit()]
    if kind in _ARITY and precision:
        kind_text = f"{kind.upper()}({','.join(precision)})"
    else:
        kind_text = kind.upper()
    column = Column(
        name,
        kind_text,
        primary="primary" in chain_names,
        required="nullable" not in chain_names,
    )
    for call, call_args in chain:
        if call == "constrained":
            target = _string(call_args[0]) if call_args else None
            column.references = (target or plural(name.removesuffix("_id"))).upper()
        elif call == "on" and call_args and (target := _string(call_args[0])):
            column.references = target.upper()
    return column


def parse_php_tables(path: str, text: str) -> list[Table]:
    tables: list[Table] = []
    for match in _SCHEMA_CREATE.finditer(text):
        brace = text.find("{", match.end())
        end = _balanced(text, brace, "{", "}") if brace != -1 else -1
        if end == -1:
            continue
        table = Table(match.group(1).upper(), path, _line(text, match.start()), _line(text, end))
        by_name: dict[str, Column] = {}

        def add(column: Column, into: Table = table, index: dict[str, Column] = by_name) -> None:
            if column.name not in index:
                index[column.name] = column
                into.columns.append(column)

        for statement in _statements(text[brace + 1 : end - 1]):
            calls = _calls(statement)
            if not calls:
                continue
            kind, args = calls[0]
            chain = calls[1:]
            name = _string(args[0]) if args else None
            if _SKIPPED.match(kind):
                continue
            if kind == "id":
                add(Column(name or "id", "BIGINT", primary=True, required=True))
            elif kind.endswith("ncrements"):
                add(Column(name or "id", "INTEGER", primary=True, required=True))
            elif kind in ("timestamps", "timestampsTz", "nullableTimestamps"):
                add(Column("created_at", "TIMESTAMP"))
                add(Column("updated_at", "TIMESTAMP"))
            elif kind in ("softDeletes", "softDeletesTz"):
                add(Column(name or "deleted_at", "TIMESTAMP"))
            elif kind == "rememberToken":
                add(Column("remember_token", "STRING"))
            elif kind == "primary":
                for key in _FIRST_STRING.findall(" ".join(args)):
                    if key in by_name:
                        by_name[key].primary = True
            elif kind == "foreign":
                target = next((_string(a[0]) for c, a in chain if c == "on" and a), None)
                if name in by_name and target:
                    by_name[name].references = target.upper()
            elif kind in ("foreignId", "foreignIdFor", "foreignUuid") and name:
                add(_php_column(name, "BIGINT" if kind == "foreignId" else "UUID", args, chain))
            elif name:
                add(_php_column(name, kind, args, chain))
        if table.columns:
            tables.append(table)
    return tables


# --------------------------------------------------------------------------------------------
# Alembic and SQLAlchemy
# --------------------------------------------------------------------------------------------

_OP_CREATE = re.compile(r"""\bop\.create_table\(""")
_TABLENAME = re.compile(r"""^\s*__tablename__\s*=\s*['"](\w+)['"]""", re.M)
_CLASS = re.compile(r"^class\s+\w+", re.M)
_MODEL_COLUMN = re.compile(
    r"^[ \t]+(\w+)\s*(?::\s*Mapped\[([^\]\n=]+)\])?\s*=\s*(?:\w+\.)*(?:mapped_column|Column)\(",
    re.M,
)
_FK = re.compile(r"""ForeignKey\(\s*['"]([\w.]+)['"]""")


def _sa_type(text: str) -> str:
    """`sa.String(length=50)` -> `STRING(50)`, `sa.Integer()` -> `INTEGER`."""
    text = text.strip()
    match = re.match(r"(?:[\w]+\.)*(\w+)\s*(?:\((.*)\))?$", text, re.S)
    if match is None:
        return text.upper()[:30]
    numbers = re.findall(r"\d+", match.group(2) or "")
    base = match.group(1).upper()
    return f"{base}({','.join(numbers[:2])})" if numbers else base


def _sa_column(args: list[str], annotation: str | None = None, name: str | None = None) -> Column:
    """Column from the arguments of `Column(...)` / `mapped_column(...)`."""
    positional = [a for a in args if not re.match(r"\w+\s*=", a)]
    keywords = {m[1]: m[2].strip() for a in args if (m := re.match(r"(\w+)\s*=\s*(.*)", a, re.S))}
    if positional and _string(positional[0]) is not None:  # Column("db_name", Integer)
        name, positional = _string(positional[0]), positional[1:]
    kind = next(
        (a for a in positional if not a.strip().startswith(("ForeignKey", "sa.ForeignKey"))), ""
    )
    column = Column(
        name or "",
        _sa_type(kind) if kind else (annotation or "").strip().upper(),
        primary=keywords.get("primary_key") == "True",
        required=keywords.get("nullable") == "False" or keywords.get("primary_key") == "True",
    )
    for argument in args:
        if fk := _FK.search(argument):
            column.references = fk.group(1).split(".")[0].upper()
    return column


def _alembic_tables(path: str, text: str) -> list[Table]:
    tables: list[Table] = []
    for match in _OP_CREATE.finditer(text):
        end = _balanced(text, match.end() - 1)
        if end == -1:
            continue
        parts = _split_top_level(text[match.end() : end - 1])
        name = _string(parts[0]) if parts else None
        if not name:
            continue
        table = Table(name.upper(), path, _line(text, match.start()), _line(text, end))
        keys: list[str] = []
        foreign: dict[str, str] = {}
        for part in parts[1:]:
            call = re.match(
                r"(?:\w+\.)*(Column|PrimaryKeyConstraint|ForeignKeyConstraint)\((.*)\)$", part, re.S
            )
            if call is None:
                continue
            inner = _split_top_level(call.group(2))
            if call.group(1) == "Column":
                column = _sa_column(inner)
                if column.name:
                    table.columns.append(column)
            elif call.group(1) == "PrimaryKeyConstraint":
                keys += [k for a in inner if (k := _string(a))]
            else:
                columns = _FIRST_STRING.findall(inner[0]) if inner else []
                targets = _FIRST_STRING.findall(inner[1]) if len(inner) > 1 else []
                if columns and targets:
                    foreign[columns[0]] = targets[0].split(".")[0].upper()
        for column in table.columns:
            column.primary = column.primary or column.name in keys
            column.references = column.references or foreign.get(column.name)
        if table.columns:
            tables.append(table)
    return tables


def _model_tables(path: str, text: str) -> list[Table]:
    tables: list[Table] = []
    starts = [m.start() for m in _CLASS.finditer(text)] + [len(text)]
    for begin, stop in zip(starts, starts[1:], strict=False):
        body = text[begin:stop]
        declared = _TABLENAME.search(body)
        if declared is None:
            continue
        table = Table(
            declared.group(1).upper(),
            path,
            _line(text, begin + declared.start()),
            _line(text, stop),
        )
        for column in _MODEL_COLUMN.finditer(body):
            end = _balanced(body, column.end() - 1)
            if end == -1:
                continue
            parsed = _sa_column(
                _split_top_level(body[column.end() : end - 1]), column.group(2), column.group(1)
            )
            if parsed.name:
                table.columns.append(parsed)
        if table.columns:
            tables.append(table)
    return tables


def parse_python_tables(path: str, text: str) -> list[Table]:
    return [*_alembic_tables(path, text), *_model_tables(path, text)]


def parse_code_tables(path: str, text: str) -> list[Table]:
    """Tables a source file defines, by its extension."""
    if path.endswith(".php"):
        return parse_php_tables(path, text)
    if path.endswith(".py"):
        return parse_python_tables(path, text)
    return []
