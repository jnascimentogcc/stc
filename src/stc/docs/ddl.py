"""Reads `CREATE TABLE` statements from the indexed SQL, for the entity-relationship diagram.

A small, forgiving parser: it handles the common column, `PRIMARY KEY`, `FOREIGN KEY` and
`REFERENCES` forms of DB2, PostgreSQL, MySQL and the like, and ignores whatever it does not
understand instead of failing. It never executes anything."""

import re
from dataclasses import dataclass, field

import psycopg

_CREATE = re.compile(
    r'(?is)\bCREATE\s+(?:\w+\s+)*?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?([\w.$#@]+)[`"\]]?\s*\('
)
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_REFERENCES = re.compile(r'(?is)\bREFERENCES\s+[`"\[]?([\w.$#@]+)[`"\]]?')
_FOREIGN = re.compile(
    r'(?is)^(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+[`"\[]?([\w.$#@]+)'
)
_PRIMARY = re.compile(r"(?is)^(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*\(([^)]*)\)")
_TYPE = re.compile(
    r"(?is)^([A-Za-z_][\w ]*?(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)"
    r"(?=\s+(?:NOT|NULL|PRIMARY|REFERENCES|DEFAULT|WITH|UNIQUE|CHECK|GENERATED|CONSTRAINT)\b"
    r"|\s*$)"
)
_TABLE_LEVEL = ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "INDEX", "KEY", "LIKE")


@dataclass
class Column:
    name: str
    type: str
    primary: bool = False
    references: str | None = None  # the table this column points to
    required: bool = False


@dataclass
class Table:
    name: str
    path: str
    start_line: int
    end_line: int
    columns: list[Column] = field(default_factory=list)


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    quote = ""
    for char in body:
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0 and not quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [" ".join(p.split()) for p in parts if p.strip()]


def _names(text: str) -> list[str]:
    return [n.strip().strip('`"[]').upper() for n in text.split(",") if n.strip()]


def parse_tables(path: str, text: str) -> list[Table]:
    """Tables of one SQL file. `text` keeps the file's line numbering."""
    cleaned = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    tables: list[Table] = []
    for match in _CREATE.finditer(cleaned):
        depth, end = 1, match.end()
        while end < len(cleaned) and depth:
            depth += {"(": 1, ")": -1}.get(cleaned[end], 0)
            end += 1
        body = cleaned[match.end() : end - 1]
        table = Table(
            match.group(1).upper(),
            path,
            cleaned.count("\n", 0, match.start()) + 1,
            cleaned.count("\n", 0, end) + 1,
        )
        keys: list[str] = []
        foreign: dict[str, str] = {}
        for part in _split_top_level(body):
            head = part.split(" ", 1)[0].upper()
            if head in _TABLE_LEVEL:
                if (pk := _PRIMARY.match(part)) is not None:
                    keys += _names(pk.group(1))
                elif (fk := _FOREIGN.match(part)) is not None:
                    for column in _names(fk.group(1)):
                        foreign[column] = fk.group(2).upper()
                continue
            name = part.split(" ", 1)[0].strip('`"[]').upper()
            rest = part[len(part.split(" ", 1)[0]) :].strip()
            kind = _TYPE.match(rest)
            reference = _REFERENCES.search(rest)
            table.columns.append(
                Column(
                    name,
                    " ".join((kind.group(1) if kind else rest.split(" ")[0]).split()).upper(),
                    primary=bool(re.search(r"(?i)\bPRIMARY\s+KEY\b", rest)),
                    references=reference.group(1).upper() if reference else None,
                    required=bool(re.search(r"(?i)\bNOT\s+NULL\b", rest)),
                )
            )
        for col in table.columns:
            if col.name in keys:
                col.primary = True
            if col.name in foreign:
                col.references = foreign[col.name]
        tables.append(table)
    return tables


def read_tables(conn: psycopg.Connection, app_id: int, paths: list[str]) -> list[Table]:
    """Tables created by the application's SQL files, rebuilt from the indexed chunks so the
    line numbers are those of the file."""
    tables: list[Table] = []
    for path in sorted(paths):
        rows = conn.execute(
            "SELECT c.start_line, c.content FROM chunks c JOIN files f ON f.id = c.file_id"
            " WHERE f.application_id = %s AND f.path = %s ORDER BY c.start_line",
            (app_id, path),
        ).fetchall()
        lines: list[str] = []
        for start, content in rows:
            while len(lines) < int(start) - 1:
                lines.append("")
            lines[int(start) - 1 :] = str(content).split("\n")
        tables += parse_tables(path, "\n".join(lines))
    return tables
