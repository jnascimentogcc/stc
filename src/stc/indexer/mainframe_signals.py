"""Cross-application signals from COBOL, copybooks, JCL and DDL (CAT-07).

What links two mainframe applications is not an HTTP call but a program one calls (`CALL`, CICS
`LINK`/`XCTL`, `EXEC PGM=`), a copybook it shares, a DB2 table it reads or writes, a dataset one
job writes and another reads. These are found line by line, with comments and string literals left
out, and never executed. A name is upper-cased (COBOL is case-insensitive); a name that is only
known at run time (`CALL WS-PROG`) becomes an unresolved key that starts with `?`.

| kind | provides | consumes |
|---|---|---|
| program | `PROGRAM-ID` | `CALL`, `LINK`, `XCTL`, `EXEC PGM=`, `RUN PROGRAM()` |
| copybook | the copybook file | `COPY`, `EXEC SQL INCLUDE` |
| table | `CREATE TABLE` | `EXEC SQL` |
| dataset | `DD` with `DISP=NEW/MOD` | `DD` with `DISP=SHR/OLD` |
"""

import re
from pathlib import PurePosixPath

from stc.indexer.cobol_source import mask_literals, read_source, statements
from stc.indexer.jcl import (
    exec_program,
    instream_lines,
    keyword_operands,
    parse_jcl,
    run_programs,
)
from stc.indexer.signal_types import Signal

MAINFRAME_LANGUAGES = frozenset({"cobol", "cobol-copybook", "jcl", "sql"})

_NAME = r"[A-Z0-9][A-Z0-9-]*"
_PROGRAM_ID = re.compile(rf"^PROGRAM-ID\s*\.\s*({_NAME})", re.I)
_CALL = re.compile(rf"(?<![\w-])CALL\s+(?:(['\"])|({_NAME}))", re.I)
_COPY = re.compile(rf"(?<![\w-])COPY\s+({_NAME})", re.I)
_EXEC_BLOCK = re.compile(r"EXEC\s+(SQL|CICS)\b", re.I)
_CICS_VERB = re.compile(r"(?<![\w-])(LINK|XCTL)\b", re.I)
_CICS_PROGRAM = re.compile(rf"PROGRAM\s*\(\s*(?:(['\"])|({_NAME}))", re.I)
_SQL_INCLUDE = re.compile(rf"(?<![\w-])INCLUDE\s+({_NAME})", re.I)
_SQL_SKIP_INCLUDES = frozenset({"SQLCA", "SQLDA"})

_IDENT = r"[A-Z][A-Z0-9_#@$]*(?:\.[A-Z][A-Z0-9_#@$]*){0,2}"
_FROM = re.compile(r"(?<![\w-])(DELETE\s+)?FROM\s+", re.I)
_WRITE_TARGET = re.compile(rf"(?<![\w-])(?:UPDATE|INSERT\s+INTO|MERGE\s+INTO)\s+({_IDENT})", re.I)
_JOIN = re.compile(rf"(?<![\w-])JOIN\s+({_IDENT})", re.I)
_TOKEN = re.compile(rf"\s*(,|\(|{_IDENT}|\S)", re.I)
_SQL_WORDS = frozenset(
    [
        "WHERE",
        "GROUP",
        "ORDER",
        "HAVING",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "FETCH",
        "FOR",
        "WITH",
        "INTO",
        "SET",
        "ON",
        "JOIN",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "CROSS",
        "OUTER",
        "USING",
        "WHEN",
        "AND",
        "OR",
        "AS",
        "SELECT",
        "VALUES",
        "OPTIMIZE",
        "QUERY",
        "CURRENT",
        "SKIP",
        "SYSIBM",
    ]
)  # SYSIBM is a schema prefix of catalog tables, which are not part of an application

_CREATE_TABLE = re.compile(rf'(?is)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?({_IDENT})"?')

_LIBRARY_DDS = frozenset({"STEPLIB", "JOBLIB", "SYSLIB", "SYSLMOD", "ISPLLIB", "DFHRPL"})
_GDG = re.compile(r"\((?:[+-]?\d+)\)$")


def mainframe_signals(path: PurePosixPath, text: str, language: str) -> list[Signal]:
    lines = text.replace("\r\n", "\n").split("\n")
    if language in ("cobol", "cobol-copybook"):
        found = _cobol(lines, language)
        if language == "cobol-copybook":
            stem = path.stem.upper()
            found.append(Signal("copybook", "provides", stem, None, f"copybook {stem}", 1, 1))
        return found
    if language == "jcl":
        return _jcl(lines)
    return _ddl(text)


# --------------------------------------------------------------------------------------------
# COBOL
# --------------------------------------------------------------------------------------------


def _signal(kind: str, role: str, key: str, detail: str, first: int, last: int) -> Signal:
    return Signal(kind, role, key.upper(), None, detail, first, last)


def _cobol(lines: list[str], language: str) -> list[Signal]:
    source = read_source(lines)
    found: list[Signal] = []
    in_exec: set[int] = set()
    for first, last, statement in statements(source):
        masked = mask_literals(statement)
        block = _EXEC_BLOCK.match(masked)
        if block is None:
            continue
        in_exec.update(range(first, last + 1))
        if block.group(1).upper() == "CICS":
            found += _cics(statement, masked, first, last)
        else:
            found += _sql(masked, first, last)
    for line in source:
        if line.is_comment or not line.code or line.number in in_exec:
            continue
        code, masked = line.code, mask_literals(line.code)
        if language == "cobol" and (match := _PROGRAM_ID.match(code)):
            name = match.group(1)
            found.append(
                _signal("program", "provides", name, f"program {name}", line.number, line.number)
            )
        for call in _CALL.finditer(masked):
            if call.group(1):
                end = code.find(call.group(1), call.end())
                name = code[call.end() : end] if end > call.end() else ""
                if name.strip():
                    found.append(
                        _signal(
                            "program",
                            "consumes",
                            name.strip(),
                            f"CALL {name.strip()}",
                            line.number,
                            line.number,
                        )
                    )
            else:
                name = call.group(2)
                found.append(
                    _signal(
                        "program",
                        "consumes",
                        f"?{name}",
                        f"dynamic CALL {name}",
                        line.number,
                        line.number,
                    )
                )
        for copy in _COPY.finditer(masked):
            name = copy.group(1)
            found.append(
                _signal("copybook", "consumes", name, f"COPY {name}", line.number, line.number)
            )
    return found


def _cics(statement: str, masked: str, first: int, last: int) -> list[Signal]:
    verb = _CICS_VERB.search(masked)
    target = _CICS_PROGRAM.search(masked)
    if verb is None or target is None:
        return []
    word = verb.group(1).upper()
    if target.group(1):
        end = statement.find(target.group(1), target.end())
        name = statement[target.end() : end].strip() if end > target.end() else ""
        if not name:
            return []
        return [_signal("program", "consumes", name, f"CICS {word} {name}", first, last)]
    name = target.group(2)
    return [_signal("program", "consumes", f"?{name}", f"dynamic CICS {word} {name}", first, last)]


def _sql(masked: str, first: int, last: int) -> list[Signal]:
    found: list[Signal] = []
    for include in _SQL_INCLUDE.finditer(masked):
        name = include.group(1)
        if name.upper() not in _SQL_SKIP_INCLUDES:
            found.append(
                _signal("copybook", "consumes", name, f"EXEC SQL INCLUDE {name}", first, last)
            )
    for name, action in _sql_tables(masked):
        found.append(_signal("table", "consumes", name, f"SQL {action} {name}", first, last))
    return found


def _is_name(token: str) -> bool:
    return bool(re.fullmatch(_IDENT, token, re.I)) and token.upper() not in _SQL_WORDS


def _sql_tables(masked: str) -> list[tuple[str, str]]:
    """`(table, "reads" | "writes")` named by one SQL statement, with the aliases left out."""
    found: list[tuple[str, str]] = []
    for match in _FROM.finditer(masked):
        action = "writes" if match.group(1) else "reads"
        found += [(name, action) for name in _from_list(masked, match.end())]
    found += [
        (m.group(1), "writes") for m in _WRITE_TARGET.finditer(masked) if _is_name(m.group(1))
    ]
    found += [(m.group(1), "reads") for m in _JOIN.finditer(masked) if _is_name(m.group(1))]
    return list(dict.fromkeys(found))


def _from_list(masked: str, pos: int) -> list[str]:
    """`FROM A a, B AS b WHERE ...` -> `[A, B]`; a sub-select is left to its own `FROM`."""
    tables: list[str] = []
    while (token := _TOKEN.match(masked, pos)) is not None and _is_name(token.group(1)):
        tables.append(token.group(1))
        pos = token.end()
        alias = _TOKEN.match(masked, pos)
        if alias is not None and alias.group(1).upper() == "AS":
            pos = alias.end()
            alias = _TOKEN.match(masked, pos)
            if alias is not None and _is_name(alias.group(1)):
                pos = alias.end()
        elif alias is not None and _is_name(alias.group(1)):
            pos = alias.end()
        comma = _TOKEN.match(masked, pos)
        if comma is None or comma.group(1) != ",":
            break
        pos = comma.end()
    return tables


# --------------------------------------------------------------------------------------------
# JCL
# --------------------------------------------------------------------------------------------


def _jcl(lines: list[str]) -> list[Signal]:
    found: list[Signal] = []
    dd_name = ""
    for statement in parse_jcl(lines):
        first, last = statement.first_line, statement.last_line
        if statement.verb == "EXEC":
            dd_name = ""
            if (program := exec_program(statement.operands)) is not None:
                found.append(
                    _signal("program", "consumes", program, f"runs {program}", first, last)
                )
        elif statement.verb == "DD":
            dd_name = statement.name.upper() or dd_name
            if dd_name not in _LIBRARY_DDS:
                found += _dataset(statement.operands, first, last)
    for number, text in instream_lines(lines):
        for program in run_programs(text):
            found.append(_signal("program", "consumes", program, f"runs {program}", number, number))
    return found


def _dataset(operands: str, first: int, last: int) -> list[Signal]:
    keywords = keyword_operands(operands)
    name = (keywords.get("DSN") or keywords.get("DSNAME") or "").strip().upper()
    disp = keywords.get("DISP")
    if not name or disp is None:
        return []
    name = _GDG.sub("", name)
    if "&" in name or name.startswith("*") or name.endswith("LOADLIB") or name == "NULLFILE":
        return []
    status = disp.strip().strip("()").split(",")[0].strip().upper() or "NEW"
    if status in ("NEW", "MOD"):
        return [_signal("dataset", "provides", name, f"writes {name}", first, last)]
    if status in ("SHR", "OLD"):
        return [_signal("dataset", "consumes", name, f"reads {name}", first, last)]
    return []


# --------------------------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------------------------


def _ddl(text: str) -> list[Signal]:
    cleaned = "\n".join(
        re.sub(r"--.*", "", line) for line in text.replace("\r\n", "\n").split("\n")
    )
    found: list[Signal] = []
    for match in _CREATE_TABLE.finditer(cleaned):
        number = cleaned.count("\n", 0, match.start(1)) + 1
        name = match.group(1)
        found.append(_signal("table", "provides", name, f"table {name}", number, number))
    return found
