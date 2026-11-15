"""Screens of COBOL applications as signals: `SCREEN SECTION`, positioned `ACCEPT`/`DISPLAY ... AT`,
CICS BMS maps and `EXEC CICS SEND/RECEIVE MAP`.

A screen is a fact in the code, like a table: the definition *provides* it and the program that
shows it or reads it *consumes* it. Two signal kinds:

- `screen` provides: key = screen or map name; detail `screen NAME; N fields`.
- `screen` consumes: key = screen name (`?NAME` when known only at run time); detail
  `DISPLAY screen X`, `ACCEPT screen X`, `CICS SEND MAP X` or `CICS RECEIVE MAP X`.
- `screen_field` provides: key `SCREEN.FIELD`; detail
  `field F: input; pic 9(10); at 5,20; label 'Account'; var WS-ACCT; required`.

A field detail is `field NAME` followed by `; `-separated parts (`input`/`output`, `pic`, `len`,
`at row,col`, `label 'text'`, `var NAME`, and the flags `required`, `secure`, `numeric`);
`parse_field_detail` reads it back. Like everything in the indexer this is a forgiving reader of
text: what it does not understand is left out, nothing is executed, and line numbers are physical.
"""

import re
from dataclasses import dataclass, field

from stc.indexer.cobol_source import SourceLine, mask_literals
from stc.indexer.signal_types import Signal

_NAME = r"[A-Z][A-Z0-9-]*"
_LEVEL = re.compile(r"^(\d{1,2})\s+(.*)$", re.S)
_SECTION_END = re.compile(
    r"^(?:PROCEDURE\s+DIVISION|[A-Z][A-Z0-9-]*\s+DIVISION|[A-Z][A-Z0-9-]*\s+SECTION)\b"
)
_CLAUSE_WORDS = frozenset(
    """LINE COL COLUMN PIC PICTURE VALUE BLANK ERASE USING FROM TO FOREGROUND-COLOR
    BACKGROUND-COLOR HIGHLIGHT LOWLIGHT REVERSE-VIDEO UNDERLINE BLINK BELL REQUIRED SECURE AUTO
    FULL JUSTIFIED SIGN OCCURS FILLER GRID LEFTLINE OVERLINE NO-ECHO UPDATE""".split()  # noqa: SIM905 - a word list reads better as text
)
_LINE = re.compile(rf"(?<![\w-])LINE\s+(?:NUMBER\s+)?(?:IS\s+)?(PLUS\s+)?(\d+|{_NAME})", re.I)
_COL = re.compile(rf"(?<![\w-])COL(?:UMN)?\s+(?:NUMBER\s+)?(?:IS\s+)?(PLUS\s+)?(\d+|{_NAME})", re.I)
_PIC = re.compile(r"(?<![\w-])PIC(?:TURE)?\s+(?:IS\s+)?(\S+)", re.I)
_VAR = re.compile(rf"(?<![\w-])(USING|FROM|TO)\s+({_NAME})", re.I)
_VALUE = re.compile(r"(?<![\w-])VALUE\s+(?:IS\s+)?(['\"])(.*?)\1", re.I)
_USE = re.compile(rf"(?<![\w-])(DISPLAY|ACCEPT)\s+({_NAME})(?![\w-])", re.I)
_PROGRAM_ID = re.compile(rf"^PROGRAM-ID\s*\.\s*({_NAME})", re.I)
_AT = re.compile(r"(?<![\w-])AT\s+(?:LINE\s+(\d+)\s+COL(?:UMN)?\s+(\d+)|(\d{2})(\d{2})\b)", re.I)
_ACCEPT_AT = re.compile(rf"(?<![\w-])ACCEPT\s+({_NAME})\s+AT\b", re.I)
_DISPLAY_AT = re.compile(r"(?<![\w-])DISPLAY\s+(['\"])", re.I)
_MAP = re.compile(rf"(?<![\w-])MAP\s*\(\s*(?:(['\"])|({_NAME}))", re.I)
_SEND_RECEIVE = re.compile(r"(?<![\w-])(SEND|RECEIVE)\b", re.I)
MAX_LABEL = 60


@dataclass
class ScreenField:
    name: str
    mode: str  # input | output
    row: int | None = None
    col: int | None = None
    pic: str | None = None
    length: int | None = None
    label: str | None = None
    var: str | None = None
    flags: list[str] = field(default_factory=list)


def field_detail(f: ScreenField) -> str:
    parts = [f.mode]
    if f.pic:
        parts.append(f"pic {f.pic}")
    if f.length:
        parts.append(f"len {f.length}")
    if f.row is not None and f.col is not None:
        parts.append(f"at {f.row},{f.col}")
    if f.label:
        parts.append(f"label '{f.label[:MAX_LABEL]}'")
    if f.var and f.var != f.name:
        parts.append(f"var {f.var}")
    parts += f.flags
    return f"field {f.name}: " + "; ".join(parts)


def parse_field_detail(detail: str) -> dict[str, str]:
    """`field A: input; pic 9(5); at 3,4; label 'X; Y'; required` -> a dict with `name`, `mode`,
    `pic`, `len`, `at`, `label`, `var` and the flags set to `"1"`."""
    head, _, rest = detail.partition(": ")
    out = {"name": head.removeprefix("field ").strip()}
    label = re.search(r"label '(.*)'(?:;|$)", rest)
    if label:
        out["label"] = label.group(1)
        rest = rest[: label.start()] + rest[label.end() :]
    for index, part in enumerate(p.strip() for p in rest.split(";") if p.strip()):
        key, _, value = part.partition(" ")
        if index == 0 and key in ("input", "output"):
            out["mode"] = key
        else:
            out[key] = value or "1"
    return out


def _signal(kind: str, role: str, key: str, detail: str, first: int, last: int) -> Signal:
    return Signal(kind, role, key.upper(), None, detail, first, last)


def _screen_signals(
    name: str, fields: list[ScreenField], first: int, last: int, mapset: str = ""
) -> list[Signal]:
    where = f"; mapset {mapset}" if mapset else ""
    found = [
        _signal(
            "screen", "provides", name, f"screen {name}{where}; {len(fields)} fields", first, last
        )
    ]
    for f in fields:
        found.append(
            _signal("screen_field", "provides", f"{name}.{f.name}", field_detail(f), first, last)
        )
    return found


# --------------------------------------------------------------------------------------------
# COBOL: SCREEN SECTION and positioned ACCEPT/DISPLAY
# --------------------------------------------------------------------------------------------


def cobol_screens(source: list[SourceLine], stmts: list[tuple[int, int, str]]) -> list[Signal]:
    found: list[Signal] = []
    defined, section_lines = _screen_section(stmts, found)
    for line in source:
        if line.is_comment or not line.code or line.number in section_lines:
            continue
        masked = mask_literals(line.code)
        for use in _USE.finditer(masked):
            name = use.group(2).upper()
            if name in defined:
                verb = use.group(1).upper()
                detail = f"{verb} screen {name}"
                found.append(_signal("screen", "consumes", name, detail, line.number, line.number))
    found += _positioned_console(source)
    return found


def _item(text: str, row: int | None) -> tuple[ScreenField | None, str | None, int | None]:
    """One `05` item: `(field, label, row)`; a field has a variable, a label has a VALUE."""
    masked = mask_literals(text)
    level = _LEVEL.match(masked.strip())
    if level is None:
        return None, None, row
    body = masked[level.start(2) :]
    first = body.split(None, 1)[0].upper().rstrip(".") if body.strip() else ""
    name = None if first in _CLAUSE_WORDS or not first or first == "." else first
    line = _LINE.search(body)
    if line:
        number = int(line.group(2)) if line.group(2).isdigit() else None
        row = (row or 0) + (number or 1) if line.group(1) and number is not None else number or row
    col = _COL.search(body)
    column = int(col.group(2)) if col and col.group(2).isdigit() else None
    value = _VALUE.search(text)
    var = _VAR.search(body)
    if value and not var:
        return None, value.group(2).strip(), row
    if var is None:
        return None, None, row
    pic = _PIC.search(body)
    word = var.group(1).upper()
    flags = [
        w.lower() for w in ("REQUIRED", "SECURE") if re.search(rf"(?<![\w-]){w}\b", body, re.I)
    ]
    if pic and re.match(r"^[S9V().,0-9]+$", pic.group(1).rstrip("."), re.I):
        flags.append("numeric")
    return (
        ScreenField(
            name or var.group(2).upper(),
            "output" if word == "FROM" else "input",
            row,
            column,
            pic.group(1).rstrip(".").upper() if pic else None,
            None,
            None,
            var.group(2).upper(),
            flags,
        ),
        None,
        row,
    )


def _screen_section(
    stmts: list[tuple[int, int, str]], found: list[Signal]
) -> tuple[set[str], set[int]]:
    """The screens defined by `SCREEN SECTION` (their signals are appended to `found`), the names
    and the lines that section covers."""
    names: set[str] = set()
    covered: set[int] = set()
    inside = False
    name: str | None = None
    fields: list[ScreenField] = []
    first = last = 0
    labels: dict[int, str] = {}
    row: int | None = None

    def close() -> None:
        nonlocal name, fields
        if name:
            found.extend(_screen_signals(name, fields, first, last))
        name, fields = None, []

    for start, end, text in stmts:
        head = mask_literals(text).strip().rstrip(".").strip().upper()
        if re.fullmatch(r"SCREEN\s+SECTION", head):
            inside = True
            covered.update(range(start, end + 1))
            continue
        if inside and _SECTION_END.match(head):
            close()
            inside = False
        if not inside:
            continue
        covered.update(range(start, end + 1))
        level = _LEVEL.match(text.strip())
        if level is None:
            continue
        if int(level.group(1)) == 1:
            close()
            token = level.group(2).split(None, 1)[0].rstrip(".").upper() if level.group(2) else ""
            name = (
                token if re.fullmatch(_NAME, token, re.I) and token not in _CLAUSE_WORDS else None
            )
            if name:
                names.add(name)
            first, row, labels = start, None, {}
        elif name:
            item, label, row = _item(text, row)
            if label is not None and row is not None:
                labels[row] = label
            if item is not None:
                item.label = labels.get(item.row) if item.row is not None else None
                fields.append(item)
        last = end
    close()
    return names, covered


def _position(match: re.Match[str]) -> tuple[int, int]:
    if match.group(1):
        return int(match.group(1)), int(match.group(2))
    return int(match.group(3)), int(match.group(4))


def _positioned_console(source: list[SourceLine]) -> list[Signal]:
    """`DISPLAY 'Account' AT LINE 5 COL 3` / `ACCEPT ws-acct AT LINE 5 COL 20`: a screen drawn by
    positioned statements, named after the program."""
    program = ""
    labels: dict[int, str] = {}
    fields: list[ScreenField] = []
    first = last = 0
    for line in source:
        if line.is_comment or not line.code:
            continue
        if match := _PROGRAM_ID.match(line.code):
            program = match.group(1).upper()
        masked = mask_literals(line.code)
        at = _AT.search(masked)
        if at is None:
            continue
        row, col = _position(at)
        if display := _DISPLAY_AT.search(masked):
            end = line.code.find(display.group(1), display.end())
            text = line.code[display.end() : end].strip() if end > display.end() else ""
            if text:
                labels[row] = text
        elif accept := _ACCEPT_AT.search(masked):
            var = accept.group(1).upper()
            fields.append(ScreenField(var, "input", row, col, label=labels.get(row), var=var))
        else:
            continue
        first = first or line.number
        last = line.number
    if not (program and fields):
        return []
    detail = f"screen {program}; {len(fields)} fields; positioned console"
    found = [_signal("screen", "provides", program, detail, first, last)]
    found += [
        _signal("screen_field", "provides", f"{program}.{f.name}", field_detail(f), first, last)
        for f in fields
    ]
    found.append(_signal("screen", "consumes", program, f"ACCEPT screen {program}", first, last))
    return found


# --------------------------------------------------------------------------------------------
# CICS: SEND MAP / RECEIVE MAP
# --------------------------------------------------------------------------------------------


def cics_map(statement: str, masked: str, first: int, last: int) -> list[Signal]:
    verb = _SEND_RECEIVE.search(masked)
    target = _MAP.search(masked)
    if verb is None or target is None:
        return []
    word = verb.group(1).upper()
    if target.group(1):
        end = statement.find(target.group(1), target.end())
        name = statement[target.end() : end].strip() if end > target.end() else ""
        if not name:
            return []
        return [_signal("screen", "consumes", name, f"CICS {word} MAP {name}", first, last)]
    name = target.group(2)
    return [
        _signal("screen", "consumes", f"?{name}", f"dynamic CICS {word} MAP {name}", first, last)
    ]


# --------------------------------------------------------------------------------------------
# BMS
# --------------------------------------------------------------------------------------------

_BMS_MACRO = re.compile(r"^(\S+)?\s+(DFHMSD|DFHMDI|DFHMDF)\b\s*(.*)$", re.I)


def _bms_statements(lines: list[str]) -> list[tuple[int, int, str]]:
    """Macros as `(first, last, text)`: a non-blank column 72 continues on the next line, whose
    text starts in column 16."""
    out: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip("\r\n").expandtabs(8)
        if not raw.strip() or raw.startswith("*") or raw.startswith(".*"):
            index += 1
            continue
        first, text = index + 1, raw[:71]
        while len(raw) >= 72 and raw[71] not in " \t" and index + 1 < len(lines):
            index += 1
            raw = lines[index].rstrip("\r\n").expandtabs(8)
            text = text.rstrip() + raw[15:71].strip()
        out.append((first, index + 1, text))
        index += 1
    return out


def _operands(text: str) -> dict[str, str]:
    """`POS=(5,20),LENGTH=10,INITIAL='A B'  comment` -> {POS: "5,20", LENGTH: "10", ...}."""
    depth, quote, current, parts = 0, "", [], []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char == "'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char.isspace() and depth == 0:
            break
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    out: dict[str, str] = {}
    for part in parts:
        key, _, value = part.partition("=")
        value = value.strip()
        if value.startswith("(") and value.endswith(")"):
            value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("''", "'")
        if key.strip():
            out[key.strip().upper()] = value
    return out


def bms_screens(lines: list[str]) -> list[Signal]:
    found: list[Signal] = []
    mapset = ""
    current: tuple[str, int, str] | None = None  # map, first line, mapset
    fields: list[ScreenField] = []
    labels: dict[int, str] = {}
    last = 0

    def close(end: int) -> None:
        nonlocal current, fields, labels
        if current is not None:
            name, start, owner = current
            found.extend(_screen_signals(name, fields, start, max(start, end), owner))
        current, fields, labels = None, [], {}

    for first, end, text in _bms_statements(lines):
        macro = _BMS_MACRO.match(text)
        if macro is None:
            continue
        label, kind, rest = (macro.group(1) or ""), macro.group(2).upper(), macro.group(3)
        if kind == "DFHMSD":
            close(last)
            mapset = label.upper() if rest.strip() and "TYPE=FINAL" not in rest.upper() else mapset
            if "TYPE=FINAL" in rest.upper():
                mapset = ""
        elif kind == "DFHMDI":
            close(last)
            if label:
                current = (label.upper(), first, mapset)
        elif current is not None:
            ops = _operands(rest)
            pos = [p for p in ops.get("POS", "").split(",") if p.strip().isdigit()]
            row, col = (int(pos[0]), int(pos[1])) if len(pos) == 2 else (None, None)
            attrib = {a.strip().upper() for a in ops.get("ATTRB", "").split(",")}
            initial = ops.get("INITIAL")
            if not label:
                if initial and row is not None:
                    labels[row] = initial
            else:
                flags = ["numeric"] if "NUM" in attrib else []
                if "DRK" in attrib:
                    flags.append("secure")
                length = ops.get("LENGTH", "")
                pic = ops.get("PICIN") or ops.get("PICOUT")
                fields.append(
                    ScreenField(
                        label.upper(),
                        "input" if "UNPROT" in attrib else "output",
                        row,
                        col,
                        pic.upper() if pic else None,
                        int(length) if length.isdigit() else None,
                        labels.get(row) if row is not None else None,
                        None,
                        flags,
                    )
                )
        last = end
    close(last)
    return found
