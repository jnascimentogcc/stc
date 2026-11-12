"""COBOL and copybook chunks and symbols, on the sample Root and on synthetic snippets."""

from pathlib import Path, PurePosixPath

from stc.indexer.chunking import CHUNKER_VERSION, ChunkedFile, chunk_file

SAMPLE = Path(__file__).parents[1] / "examples" / "cobol-bank-root"


def sample(rel: str) -> tuple[PurePosixPath, str]:
    return PurePosixPath(rel), (SAMPLE / rel).read_text(encoding="utf-8")


def chunk_sample(rel: str, language: str = "cobol") -> ChunkedFile:
    path, text = sample(rel)
    return chunk_file(path, text, language)


def cbl(*lines: str) -> str:
    return "\n".join("       " + line for line in lines) + "\n"


def span_of(chunked: ChunkedFile, name: str) -> tuple[int, int]:
    (found,) = [c for c in chunked.chunks if c.name == name]
    return found.start_line, found.end_line


def inside_one_chunk(chunked: ChunkedFile, first: int, last: int) -> bool:
    return any(c.start_line <= first and last <= c.end_line for c in chunked.chunks)


def assert_covers(chunked: ChunkedFile, text: str) -> None:
    """No overlaps, and every line with text belongs to exactly one chunk."""
    lines = text.split("\n")
    for number, line in enumerate(lines, start=1):
        if line.strip():
            owners = [c for c in chunked.chunks if c.start_line <= number <= c.end_line]
            assert len(owners) == 1, f"line {number} is in {len(owners)} chunks"


def test_lnelig_paragraph_chunks() -> None:
    chunked = chunk_sample("loans-core/src/LNELIG.cbl")
    assert chunked.parsed
    kinds = {(c.kind, c.name) for c in chunked.chunks}
    for name in ("0000-MAIN", "1000-CHK-AGE", "2000-CHK-INC", "4000-CALC-INST", "5000-CHK-CMT"):
        assert ("paragraph", name) in kinds
    assert span_of(chunked, "1000-CHK-AGE") == (28, 32)
    assert span_of(chunked, "4000-CALC-INST") == (41, 49)
    assert span_of(chunked, "5000-CHK-CMT") == (50, 60)


def test_tiny_adjacent_paragraphs_share_a_chunk() -> None:
    path, text = sample("loans-core/src/LNELIG.cbl")
    chunked = chunk_file(path, text, "cobol")
    assert span_of(chunked, "2000-CHK-INC") == (33, 40)
    assert "3000-CHK-SCORE" not in {c.name for c in chunked.chunks}
    paragraphs = {s.name for s in chunked.symbols if s.kind == "paragraph" and s.is_definition}
    assert {"2000-CHK-INC", "3000-CHK-SCORE"} <= paragraphs
    assert_covers(chunked, text)


def test_evaluation_evidence_lines_fall_inside_one_chunk() -> None:
    chunked = chunk_sample("loans-core/src/LNELIG.cbl")
    for first, last in [(28, 32), (33, 36), (37, 40), (50, 60)]:
        assert inside_one_chunk(chunked, first, last), (first, last)


def test_symbols() -> None:
    chunked = chunk_sample("loans-core/src/LNELIG.cbl")
    definitions = {(s.name, s.kind) for s in chunked.symbols if s.is_definition}
    assert ("LNELIG", "program") in definitions
    for name in ("0000-MAIN", "1000-CHK-AGE", "2000-CHK-INC", "3000-CHK-SCORE", "5000-CHK-CMT"):
        assert (name, "paragraph") in definitions
    uses = {(s.name, s.kind) for s in chunked.symbols if not s.is_definition}
    assert {("LNCALC", "call"), ("LNCALCP", "copy"), ("LNSIMCA", "copy")} <= uses
    assert ("1000-CHK-AGE", "perform") in uses
    assert all(s.chunk_index is not None for s in chunked.symbols)


def test_section_and_record_symbols_cover_their_lines() -> None:
    chunked = chunk_sample("loans-core/src/LNELIG.cbl")
    spans = {
        (s.name, s.kind): (s.start_line, s.end_line) for s in chunked.symbols if s.is_definition
    }
    assert spans[("WORKING-STORAGE", "section")] == (4, 8)  # the records inside do not cut it
    assert spans[("WS-END-AGE", "record")] == (5, 5)


def test_exec_sql_block_stays_in_one_chunk() -> None:
    chunked = chunk_sample("loans-core/src/LNBATCH.cbl")
    assert inside_one_chunk(chunked, 24, 33)  # the cursor declaration
    first, last = span_of(chunked, "2300-SET-DFLT")
    assert first <= 78 and last >= 88  # the UPDATE block is whole


def test_copybook_chunks() -> None:
    rate = chunk_sample("loans-core/copybooks/RATETAB.cpy", "cobol-copybook")
    assert len(rate.chunks) == 1 and (rate.chunks[0].start_line, rate.chunks[0].end_line) == (1, 15)
    assert inside_one_chunk(rate, 4, 10)  # a table and its REDEFINES stay together
    contract = chunk_sample("loans-core/copybooks/CONTRREC.cpy", "cobol-copybook")
    assert ("record", "CONTRACT-REC") in {(c.kind, c.name) for c in contract.chunks}
    definitions = {(s.name, s.kind) for s in contract.symbols if s.is_definition}
    assert ("CONTRREC", "copybook") in definitions and ("CONTRACT-REC", "record") in definitions
    commarea = chunk_sample("loans-core/copybooks/LNSIMCA.cpy", "cobol-copybook")
    assert [c.kind for c in commarea.chunks] == ["copybook"]  # no 01 level: the whole file


def test_two_unrelated_records_are_two_chunks() -> None:
    text = (
        "       01  A-REC.\n           05  X PIC 9.\n       01  B-REC.\n           05  Y PIC 9.\n"
    )
    chunked = chunk_file(PurePosixPath("AB.cpy"), text, "cobol-copybook")
    assert [(c.kind, c.name) for c in chunked.chunks] == [("record", "A-REC"), ("record", "B-REC")]


def test_huge_paragraph_is_cut_at_blank_lines() -> None:
    body = []
    for block in range(60):
        body += [f"MOVE {block} TO X", "MOVE 1 TO Y", "MOVE 2 TO Z", "MOVE 3 TO W", ""]
    text = cbl("PROCEDURE DIVISION.", "BIG-PARA.", *body, "STOP RUN.")
    chunked = chunk_file(PurePosixPath("BIG.cbl"), text, "cobol")
    big = [c for c in chunked.chunks if c.name == "BIG-PARA"]
    assert len(big) > 1
    assert all(c.end_line - c.start_line + 1 <= 120 for c in big)


def test_garbage_degrades_to_text() -> None:
    for text in ("�" * 500 + "\n", "The quick brown fox jumps over the lazy dog.\nAgain.\n"):
        chunked = chunk_file(PurePosixPath("X.cbl"), text, "cobol")
        assert not chunked.parsed and chunked.symbols == []
        assert chunked.chunks and all(c.kind == "text" for c in chunked.chunks)


def test_comment_text_makes_no_symbols() -> None:
    text = "      * CALL 'GHOST'\n" + cbl(
        "PROCEDURE DIVISION.", "MAIN-PARA.", "DISPLAY 'COPY GHOST'.", "STOP RUN."
    )
    chunked = chunk_file(PurePosixPath("X.cbl"), text, "cobol")
    assert not [s for s in chunked.symbols if s.kind in ("call", "copy")]


def test_comment_lines_above_a_paragraph_belong_to_it() -> None:
    lines = cbl("PROCEDURE DIVISION.", "A-PARA.", "MOVE 1 TO X.", "MOVE 2 TO X.", "MOVE 3 TO X.")
    text = (
        lines
        + "      * what B does\n"
        + cbl("B-PARA.", "MOVE 1 TO Y.", "MOVE 2 TO Y.", "MOVE 3 TO Y.")
    )
    chunked = chunk_file(PurePosixPath("X.cbl"), text, "cobol")
    assert span_of(chunked, "B-PARA")[0] == 6


def test_chunker_version_was_bumped() -> None:
    assert CHUNKER_VERSION == 2
