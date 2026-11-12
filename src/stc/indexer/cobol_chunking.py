"""COBOL and copybook chunks and symbols, line based (IDX-08).

A program is cut at its structure: a header chunk (IDENTIFICATION DIVISION), one chunk per section
of the ENVIRONMENT and DATA divisions, and one per paragraph or section of the PROCEDURE DIVISION,
because that is where a business rule lives and what a citation should point at. A run of very
short neighbouring paragraphs shares one chunk (their symbols stay separate). A copybook is cut by
01-level record; one without any is a single chunk. A file with no recognizable structure comes back
as plain text chunks, never as an error.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from stc.indexer.chunking import ChunkDraft, ChunkedFile, SymbolDraft, _windows
from stc.indexer.cobol_source import SourceLine, mask_literals, read_source

TINY_PARAGRAPH_LINES = 5  # a paragraph shorter than this joins the tiny paragraphs next to it

_DIVISION = re.compile(r"^(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b", re.I)
_SECTION = re.compile(r"^([A-Z0-9][A-Z0-9-]*)\s+SECTION\s*\.", re.I)
_PARAGRAPH = re.compile(r"^([A-Z0-9][A-Z0-9-]*)\s*\.$", re.I)
_PROGRAM_ID = re.compile(r"^PROGRAM-ID\s*\.\s*([A-Z0-9][A-Z0-9-]*)", re.I)
_RECORD = re.compile(r"^01\s+([A-Z0-9][A-Z0-9-]*)", re.I)
_CALL = re.compile(r"(?<![\w-])CALL\s+(['\"])", re.I)
_COPY = re.compile(r"(?<![\w-])COPY\s+([A-Z0-9][A-Z0-9-]*)", re.I)
_PERFORM = re.compile(r"(?<![\w-])PERFORM\s+([A-Z0-9][A-Z0-9-]*)", re.I)
_NOT_PARAGRAPHS = frozenset({"EXIT", "GOBACK", "CONTINUE", "STOP", "NEXT"})


@dataclass
class _Mark:
    header: int  # the line of the paragraph, section, record... (where its symbol is)
    kind: str  # paragraph | section | record
    name: str | None
    start: int = 0  # where its chunk starts: the header, or the comments right above it
    in_procedure: bool = False
    chunk: bool = True  # False: a symbol only (a record inside a DATA DIVISION section)


def chunk_cobol(path: PurePosixPath, lines: list[str], language: str) -> ChunkedFile:
    source = read_source(lines)
    if language == "cobol-copybook":
        result = _copybook(path, lines, source)
    else:
        result = _program(lines, source)
    return result if result is not None else ChunkedFile(_text(lines), [], parsed=False)


def _text(lines: list[str]) -> list[ChunkDraft]:
    return _windows(lines, 1, len(lines), "text", None)


# --------------------------------------------------------------------------------------------
# programs
# --------------------------------------------------------------------------------------------


def _program(lines: list[str], source: list[SourceLine]) -> ChunkedFile | None:
    division = ""
    program_id: str | None = None
    saw_division = False
    marks: list[_Mark] = []
    procedure_line = 0
    for line in source:
        code = line.code
        if not code or line.is_comment:
            continue
        if match := _DIVISION.match(code):
            saw_division = True
            division = match.group(1).upper()
            if division == "PROCEDURE":
                procedure_line = line.number
                marks.append(_Mark(line.number, "paragraph", None, in_procedure=True))
        elif division in ("", "IDENTIFICATION", "ID") and (match := _PROGRAM_ID.match(code)):
            program_id = match.group(1)
        elif division == "PROCEDURE":
            if match := _SECTION.match(code):
                marks.append(_Mark(line.number, "section", match.group(1), in_procedure=True))
            elif (match := _PARAGRAPH.match(code)) and line.in_area_a:
                name = match.group(1)
                if name.upper() not in _NOT_PARAGRAPHS and not name.upper().startswith("END-"):
                    marks.append(_Mark(line.number, "paragraph", name, in_procedure=True))
        elif division in ("ENVIRONMENT", "DATA"):
            if match := _SECTION.match(code):
                marks.append(_Mark(line.number, "section", match.group(1)))
            elif (
                division == "DATA"
                and (match := _RECORD.match(code))
                and match.group(1).upper() != "FILLER"
            ):
                marks.append(_Mark(line.number, "record", match.group(1), chunk=False))
    if not saw_division and not any(m.kind != "record" for m in marks):
        return None
    marks = _absorb_procedure_division(marks, source, procedure_line)
    spans = _spans(marks, source, len(lines))
    chunks, symbols = _build(lines, source, spans, marks, program_id)
    return ChunkedFile(chunks, symbols, parsed=True)


def _absorb_procedure_division(
    marks: list[_Mark], source: list[SourceLine], procedure_line: int
) -> list[_Mark]:
    """The `PROCEDURE DIVISION.` line starts the first paragraph's chunk when nothing but comments
    sits between them; otherwise it opens an unnamed chunk for the statements that come first."""
    for i, mark in enumerate(marks):
        if mark.header != procedure_line or mark.name is not None or i + 1 >= len(marks):
            continue
        following = marks[i + 1]
        between = [s for s in source if procedure_line < s.number < following.header and s.code]
        if following.in_procedure and not between:
            following.start = -procedure_line  # negative: "start at the division line"
            return marks[:i] + marks[i + 1 :]
    return marks


def _comment_start(source: list[SourceLine], header: int, floor: int) -> int:
    start = header
    while start - 1 > floor and source[start - 2].is_comment:
        start -= 1
    return start


def _spans(
    marks: list[_Mark], source: list[SourceLine], total: int
) -> list[tuple[_Mark, int, int]]:
    """`(mark, first_line, last_line)` for the marks that own a chunk, in file order."""
    owners = [m for m in marks if m.chunk]
    floor = 0
    for mark in owners:
        header = -mark.start if mark.start < 0 else mark.header
        mark.start = _comment_start(source, header, floor)
        floor = mark.header
    result = []
    for i, mark in enumerate(owners):
        end = owners[i + 1].start - 1 if i + 1 < len(owners) else total
        result.append((mark, mark.start, end))
    return _merge_tiny(result, source)


def _trimmed_length(source: list[SourceLine], first: int, last: int) -> int:
    while last > first and not source[last - 1].code and not source[last - 1].is_comment:
        last -= 1
    return last - first + 1


def _merge_tiny(
    spans: list[tuple[_Mark, int, int]], source: list[SourceLine]
) -> list[tuple[_Mark, int, int]]:
    def tiny(item: tuple[_Mark, int, int]) -> bool:
        mark, first, last = item
        short = _trimmed_length(source, first, last) < TINY_PARAGRAPH_LINES
        return mark.kind == "paragraph" and mark.name is not None and short

    merged: list[tuple[_Mark, int, int]] = []
    run: list[tuple[_Mark, int, int]] = []

    def close() -> None:
        if len(run) > 1:
            merged.append((run[0][0], run[0][1], run[-1][2]))
        else:
            merged.extend(run)
        run.clear()

    for item in spans:
        if tiny(item):
            run.append(item)
        else:
            close()
            merged.append(item)
    close()
    return merged


def _build(
    lines: list[str],
    source: list[SourceLine],
    spans: list[tuple[_Mark, int, int]],
    marks: list[_Mark],
    program_id: str | None,
) -> tuple[list[ChunkDraft], list[SymbolDraft]]:
    chunks: list[ChunkDraft] = []
    first_start = spans[0][1] if spans else len(lines) + 1
    if first_start > 1:
        chunks += _windows(lines, 1, first_start - 1, "program", program_id)
    for mark, first, last in spans:
        chunks += _windows(lines, first, last, mark.kind, mark.name)
    symbols = _definitions(source, marks, program_id, len(lines))
    names = {s.name.upper() for s in symbols if s.kind in ("paragraph", "section")}
    symbols += _uses(source, names)
    return chunks, [_with_chunk(s, chunks) for s in symbols]


def _definitions(
    source: list[SourceLine], marks: list[_Mark], program_id: str | None, total: int
) -> list[SymbolDraft]:
    symbols: list[SymbolDraft] = []
    if program_id is not None:
        line = next((s.number for s in source if s.code and _PROGRAM_ID.match(s.code)), 1)
        symbols.append(SymbolDraft(program_id, "program", True, line, line, None))
    every = sorted(m.header for m in marks)
    owners = sorted(m.header for m in marks if m.kind != "record")  # sections and paragraphs
    for mark in marks:
        if mark.name is None:
            continue
        headers = every if mark.kind == "record" else owners
        later = [h for h in headers if h > mark.header]
        limit = later[0] - 1 if later else total
        end = _last_code_line(source, mark.header, limit)
        kind = mark.kind
        symbols.append(SymbolDraft(mark.name, kind, True, mark.header, end, None))
    return symbols


def _last_code_line(source: list[SourceLine], first: int, last: int) -> int:
    for number in range(min(last, len(source)), first, -1):
        if source[number - 1].code:
            return number
    return first


def _uses(source: list[SourceLine], paragraph_names: set[str]) -> list[SymbolDraft]:
    symbols: list[SymbolDraft] = []
    for line in source:
        if line.is_comment or not line.code:
            continue
        masked = mask_literals(line.code)
        for match in _CALL.finditer(masked):
            quote = match.group(1)
            end = line.code.find(quote, match.end())
            if end > match.end():
                name = line.code[match.end() : end]
                symbols.append(SymbolDraft(name, "call", False, line.number, line.number, None))
        for match in _COPY.finditer(masked):
            symbols.append(
                SymbolDraft(match.group(1), "copy", False, line.number, line.number, None)
            )
        for match in _PERFORM.finditer(masked):
            if match.group(1).upper() in paragraph_names:
                symbols.append(
                    SymbolDraft(match.group(1), "perform", False, line.number, line.number, None)
                )
    return symbols


def _with_chunk(symbol: SymbolDraft, chunks: list[ChunkDraft]) -> SymbolDraft:
    index = next(
        (i for i, c in enumerate(chunks) if c.start_line <= symbol.start_line <= c.end_line),
        None,
    )
    return SymbolDraft(
        symbol.name,
        symbol.kind,
        symbol.is_definition,
        symbol.start_line,
        symbol.end_line,
        index,
    )


# --------------------------------------------------------------------------------------------
# copybooks
# --------------------------------------------------------------------------------------------


def _copybook(
    path: PurePosixPath, lines: list[str], source: list[SourceLine]
) -> ChunkedFile | None:
    stem = path.stem.upper()
    codes = [s for s in source if s.code and not s.is_comment]
    if not codes:
        return None
    records: list[_Mark] = []
    for i, line in enumerate(codes):
        match = _RECORD.match(line.code)
        if not match or match.group(1).upper() == "FILLER":
            continue
        following = codes[i + 1].code if i + 1 < len(codes) else ""
        redefines = "REDEFINES" in line.code.upper() or following.upper().startswith("REDEFINES")
        records.append(_Mark(line.number, "record", match.group(1), chunk=not redefines))
    spans = _spans(records, source, len(lines))
    chunks: list[ChunkDraft] = []
    if spans:
        # the first record's chunk also holds whatever sits above it
        spans[0] = (spans[0][0], 1, spans[0][2])
        for mark, first, last in spans:
            chunks += _windows(lines, first, last, mark.kind, mark.name)
    else:
        chunks = _windows(lines, 1, len(lines), "copybook", stem)
    last_line = codes[-1].number
    symbols = [SymbolDraft(stem, "copybook", True, 1, last_line, None)]
    symbols += _definitions(source, records, None, len(lines))
    symbols += _uses(source, set())
    return ChunkedFile(chunks, [_with_chunk(s, chunks) for s in symbols], parsed=True)
