"""Reads COBOL source lines the way a compiler's first pass does (fixed format, columns 7-72).

Columns 1-6 are a sequence area, column 7 an indicator (`*` or `/` comment, `-` continuation,
`D` debugging line), columns 8-72 hold the code (Area A is columns 8-11) and columns 73-80 are
ignored. Free format (`>>SOURCE FORMAT FREE`) has no columns. Nothing here raises on odd input:
a line that does not fit the format is simply a line with no code. Line numbers are always the
physical ones, so citations stay exact (XCT-01, QRY-07).
"""

import re
from dataclasses import dataclass

_FREE_DIRECTIVE = re.compile(r"^\s*>>\s*SOURCE\s+(?:FORMAT\s+)?(?:IS\s+)?FREE\b", re.IGNORECASE)
_EXEC = re.compile(r"(?<![\w-])EXEC\b", re.IGNORECASE)  # not the tail of END-EXEC
_END_EXEC = re.compile(r"(?<![\w-])END-EXEC\b", re.IGNORECASE)
_FIXED_CODE = slice(7, 72)


@dataclass(frozen=True)
class SourceLine:
    number: int  # 1-based physical line
    code: str  # the statement text, stripped; empty for blank and comment lines
    is_comment: bool
    in_area_a: bool  # the code starts in column 8 (fixed) or is any code (free)
    continued: bool  # fixed format: `-` in column 7, the text continues the previous line


def is_free_format(lines: list[str]) -> bool:
    return any(_FREE_DIRECTIVE.match(line) for line in lines[:20])


def mask_literals(code: str) -> str:
    """The same text with the contents of `'...'` and `"..."` literals replaced by spaces, so a
    word inside a string is never mistaken for a statement. The quotes stay; an unterminated
    literal (continued on the next line) is masked to the end."""
    out: list[str] = []
    quote = ""
    for char in code:
        if quote:
            if char == quote:
                quote = ""
                out.append(char)
            else:
                out.append(" ")
        else:
            if char in "'\"":
                quote = char
            out.append(char)
    return "".join(out)


def _strip_inline_comment(code: str) -> str:
    at = mask_literals(code).find("*>")
    return code if at < 0 else code[:at]


def read_source(lines: list[str]) -> list[SourceLine]:
    free = is_free_format(lines)
    return [
        _read_free(number, line) if free else _read_fixed(number, line)
        for number, line in enumerate(lines, start=1)
    ]


def _read_free(number: int, line: str) -> SourceLine:
    text = line.rstrip("\r\n").strip()
    if not text or text.startswith(("*>", ">>")):
        return SourceLine(number, "", bool(text), False, False)
    code = _strip_inline_comment(text).strip()
    return SourceLine(number, code, False, bool(code), False)


def _read_fixed(number: int, line: str) -> SourceLine:
    raw = line.rstrip("\r\n")
    if len(raw) < 7:
        return SourceLine(number, "", False, False, False)
    indicator = raw[6]
    if indicator in "*/Dd":
        return SourceLine(number, "", True, False, False)
    code = _strip_inline_comment(raw[_FIXED_CODE]).strip()
    in_area_a = bool(code) and len(raw) > 7 and raw[7] not in " \t"
    return SourceLine(number, code, False, in_area_a, indicator == "-")


def statements(source: list[SourceLine]) -> list[tuple[int, int, str]]:
    """Logical statements as `(first_line, last_line, text)`: text ends at a period followed by a
    space or the end of the line, or at `END-EXEC`; an `EXEC ... END-EXEC` block is one statement
    even over many lines, and `EXEC` always starts a new one. Comments are skipped and
    continuation lines are joined to the line before."""
    result: list[tuple[int, int, str]] = []
    first = last = 0
    buffer = ""
    in_exec = False

    def flush() -> None:
        nonlocal buffer, first, last
        if buffer.strip():
            result.append((first, last, buffer.strip()))
        buffer = ""

    for line in source:
        if line.is_comment or not line.code:
            continue
        if not in_exec and _EXEC.match(line.code) and buffer:
            flush()
        if not buffer:
            first = line.number
        buffer += ("" if not buffer else " ") + line.code
        last = line.number
        masked = mask_literals(buffer)
        in_exec = len(_EXEC.findall(masked)) > len(_END_EXEC.findall(masked))
        if not in_exec and (_END_EXEC.search(masked) or _ends_with_period(masked)):
            flush()
    flush()
    return _split_inner_periods(result)


def _ends_with_period(masked: str) -> bool:
    return masked.rstrip().endswith(".")


def _split_inner_periods(found: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """A line can hold several sentences (`MOVE A TO B. MOVE C TO D.`): cut them apart (a period
    inside a literal or an `EXEC` block never cuts)."""
    out: list[tuple[int, int, str]] = []
    for first, last, text in found:
        masked = mask_literals(text)
        if _EXEC.search(masked):
            out.append((first, last, text))
            continue
        start = 0
        for match in re.finditer(r"\.(?=\s)", masked):
            piece = text[start : match.end()].strip()
            if piece:
                out.append((first, last, piece))
            start = match.end()
        rest = text[start:].strip()
        if rest:
            out.append((first, last, rest))
    return out
