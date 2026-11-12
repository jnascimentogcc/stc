"""JCL statements, chunks and symbols, line based (IDX-08, IDX-09).

A job is a header chunk (the JOB statement and the comments that describe it) plus one chunk per
step (`EXEC`, with its `DD` statements and in-stream data). JCL is where batch programs and the
datasets that tie applications together are named, so the statements are also parsed for the
signal extractor. Nothing here raises on odd input: text with no JCL comes back as plain text.
"""

import re
from dataclasses import dataclass

from stc.indexer.chunking import ChunkDraft, ChunkedFile, SymbolDraft, _windows

_RUN_PROGRAM = re.compile(r"\bRUN\s+PROGRAM\(\s*([A-Z0-9#@$]+)\s*\)", re.I)
_PGM = re.compile(r"(?:^|,)PGM=([A-Z0-9#@$]+)", re.I)
_KNOWN_VERBS = frozenset({"JOB", "EXEC", "DD", "PROC", "PEND"})


@dataclass(frozen=True)
class JclStatement:
    first_line: int
    last_line: int
    name: str  # the label (`//STEP010`), empty when there is none
    verb: str  # JOB | EXEC | DD | PROC | PEND | SET | ...
    operands: str  # the operand field with continuation lines joined, never in-stream data


def _operand_field(text: str) -> str:
    """The operands end at the first blank outside quotes; what follows is a comment."""
    quoted = False
    for i, char in enumerate(text):
        if char == "'":
            quoted = not quoted
        elif char.isspace() and not quoted:
            return text[:i]
    return text


def _is_comment(line: str) -> bool:
    return line.startswith("//*")


def _scan(lines: list[str]) -> tuple[list[JclStatement], list[tuple[int, str]]]:
    """Statements, and the in-stream data lines as `(line number, text)`. Data after `DD *` ends
    at `/*` or at the next `//` line; after `DD DATA` only at `/*`."""
    statements: list[JclStatement] = []
    instream: list[tuple[int, str]] = []
    mode = ""  # "" | "star" | "data"
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        if mode:
            if line.startswith("/*"):
                mode = ""
                i += 1
                continue
            if not (mode == "star" and line.startswith("//")):
                instream.append((i + 1, line))
                i += 1
                continue
            mode = ""
        if not line.startswith("//") or _is_comment(line) or not line[2:72].strip():
            i += 1
            continue
        text = line[2:72].rstrip()
        if text[0].isspace():
            name, rest = "", text.strip()
        else:
            name, _, rest = text.partition(" ")
            rest = rest.strip()
        verb, _, tail = rest.partition(" ")
        operands = _operand_field(tail.strip())
        first = last = i + 1
        while (
            operands.endswith(",")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("//")
            and not _is_comment(lines[i + 1])
        ):
            i += 1
            operands += _operand_field(lines[i][2:72].strip())
            last = i + 1
        statements.append(JclStatement(first, last, name, verb.upper(), operands))
        upper = operands.upper()
        if verb.upper() == "DD":
            if upper == "*" or upper.startswith("*,"):
                mode = "star"
            elif upper == "DATA" or upper.startswith("DATA,"):
                mode = "data"
        i += 1
    return statements, instream


def parse_jcl(lines: list[str]) -> list[JclStatement]:
    return _scan(lines)[0]


def exec_program(operands: str) -> str | None:
    """`X` of `EXEC PGM=X` (a procedure call or a referback gives None)."""
    match = _PGM.search(operands)
    return match.group(1) if match else None


def run_programs(text: str) -> list[str]:
    """Programs run by `RUN PROGRAM(X)` in the in-stream input of a TSO/DB2 step."""
    return [m.group(1) for m in _RUN_PROGRAM.finditer(text)]


def keyword_operands(operands: str) -> dict[str, str]:
    """`DSN=A,DISP=(NEW,CATLG)` -> `{"DSN": "A", "DISP": "(NEW,CATLG)"}`: split at the commas that
    are outside parentheses and quotes, keywords upper-cased, positional operands left out."""
    pieces: list[str] = []
    depth, quoted, current = 0, False, ""
    for char in operands:
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        if char == "," and depth <= 0 and not quoted:
            pieces.append(current)
            current = ""
        else:
            current += char
    pieces.append(current)
    found: dict[str, str] = {}
    for piece in pieces:
        key, equals, value = piece.partition("=")
        if equals:
            found.setdefault(key.strip().upper(), value.strip())
    return found


def instream_lines(lines: list[str]) -> list[tuple[int, str]]:
    return _scan(lines)[1]


def chunk_jcl(lines: list[str]) -> ChunkedFile:
    statements, instream = _scan(lines)
    if not any(s.verb in _KNOWN_VERBS for s in statements):
        return ChunkedFile(_windows(lines, 1, len(lines), "text", None), [], parsed=False)
    steps = [s for s in statements if s.verb == "EXEC"]
    job = next((s for s in statements if s.verb == "JOB"), None)
    proc = next((s for s in statements if s.verb == "PROC"), None)
    starts = [_step_start(lines, statements, step) for step in steps]
    chunks: list[ChunkDraft] = []
    header_end = (starts[0] - 1) if starts else len(lines)
    header = (
        ("job", job.name or None) if job else ("proc", proc.name or None) if proc else ("jcl", None)
    )
    chunks += _windows(lines, 1, header_end, *header)
    spans: list[tuple[int, int]] = []
    for k, step in enumerate(steps):
        end = starts[k + 1] - 1 if k + 1 < len(steps) else len(lines)
        spans.append((starts[k], end))
        chunks += _windows(lines, starts[k], end, "step", step.name or None)
    symbols: list[SymbolDraft] = []
    if job and job.name:
        symbols.append(
            SymbolDraft(
                job.name, "job", True, job.first_line, _last_text(lines, 1, header_end), None
            )
        )
    for step, (first, last) in zip(steps, spans, strict=True):
        if step.name:
            symbols.append(
                SymbolDraft(
                    step.name, "step", True, step.first_line, _last_text(lines, first, last), None
                )
            )
        if (program := exec_program(step.operands)) is not None:
            symbols.append(
                SymbolDraft(program, "call", False, step.first_line, step.last_line, None)
            )
    for number, text in instream:
        for program in run_programs(text):
            symbols.append(SymbolDraft(program, "call", False, number, number, None))
    return ChunkedFile(chunks, [_with_chunk(s, chunks) for s in symbols], parsed=True)


def _step_start(lines: list[str], statements: list[JclStatement], step: JclStatement) -> int:
    """A step starts at its `EXEC`, or at the comments right above it, except the banner that
    follows the JOB statement, which describes the job."""
    previous = [s for s in statements if s.last_line < step.first_line]
    floor = previous[-1].last_line if previous else 0
    if previous and previous[-1].verb == "JOB":
        return step.first_line
    start = step.first_line
    while start - 1 > floor and _is_comment(lines[start - 2]):
        start -= 1
    return start


def _last_text(lines: list[str], first: int, last: int) -> int:
    for number in range(min(last, len(lines)), first, -1):
        if lines[number - 1].strip():
            return number
    return first


def _with_chunk(symbol: SymbolDraft, chunks: list[ChunkDraft]) -> SymbolDraft:
    index = next(
        (i for i, c in enumerate(chunks) if c.start_line <= symbol.start_line <= c.end_line), None
    )
    return SymbolDraft(
        symbol.name, symbol.kind, symbol.is_definition, symbol.start_line, symbol.end_line, index
    )


__all__ = [
    "JclStatement",
    "chunk_jcl",
    "exec_program",
    "instream_lines",
    "keyword_operands",
    "parse_jcl",
    "run_programs",
]
