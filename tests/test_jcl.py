"""JCL statements, chunks and symbols, on the sample Root and on synthetic snippets."""

from pathlib import Path, PurePosixPath

from stc.indexer.chunking import ChunkedFile, chunk_file
from stc.indexer.jcl import parse_jcl

SAMPLE = Path(__file__).parents[1] / "examples" / "cobol-bank-root"


def sample(rel: str) -> str:
    return (SAMPLE / rel).read_text(encoding="utf-8")


def chunk_sample(rel: str) -> ChunkedFile:
    return chunk_file(PurePosixPath(rel), sample(rel), "jcl")


def test_lnnight_chunks() -> None:
    chunked = chunk_sample("loans-core/jcl/LNNIGHT.jcl")
    assert chunked.parsed
    found = [(c.kind, c.name, c.start_line, c.end_line) for c in chunked.chunks]
    assert found == [
        ("job", "LNNIGHT", 1, 4),  # the banner comments right after the JOB statement
        ("step", "STEP010", 5, 17),  # its DD statements and the in-stream data
        ("step", "STEP020", 18, 27),
    ]


def test_collections_job_chunks() -> None:
    chunked = chunk_sample("collections/jcl/COLLJOB.jcl")
    assert [(c.kind, c.name, c.start_line, c.end_line) for c in chunked.chunks] == [
        ("job", "COLLJOB", 1, 4),
        ("step", "STEP010", 5, 12),
    ]


def test_symbols() -> None:
    chunked = chunk_sample("loans-core/jcl/LNNIGHT.jcl")
    definitions = {(s.name, s.kind) for s in chunked.symbols if s.is_definition}
    assert definitions == {("LNNIGHT", "job"), ("STEP010", "step"), ("STEP020", "step")}
    uses = {(s.name, s.kind) for s in chunked.symbols if not s.is_definition}
    assert uses == {("IKJEFT01", "call"), ("LNBATCH", "call"), ("SORT", "call")}
    assert all(s.chunk_index is not None for s in chunked.symbols)


def test_parse_joins_continuation_lines() -> None:
    statements = parse_jcl(sample("loans-core/jcl/LNNIGHT.jcl").split("\n"))
    (lateRpt,) = [s for s in statements if s.name == "LATERPT"]
    assert (lateRpt.first_line, lateRpt.last_line, lateRpt.verb) == (9, 12, "DD")
    assert "DSN=BANK.LOANS.LATE.DAILY" in lateRpt.operands
    assert "DISP=(NEW,CATLG,DELETE)" in lateRpt.operands
    assert "DCB=(RECFM=FB,LRECL=59)" in lateRpt.operands


def test_comments_and_instream_data_are_not_statements() -> None:
    text = "\n".join(
        [
            "//J1 JOB",
            "//* text",
            "//S1 EXEC PGM=X",
            "//SYSIN DD *",
            "//NOT-A-STATEMENT-LOOKING-LINE IS DATA",  # JCL ends `DD *` data at the next `//`
            "/*",
            "//S2 EXEC PGM=Y",
            "//SYSIN DD DATA",
            "//INSIDE EXEC PGM=GHOST",  # `DD DATA` ends only at `/*`
            "/*",
        ]
    )
    names = [(s.name, s.verb) for s in parse_jcl(text.split("\n"))]
    assert ("S1", "EXEC") in names and ("S2", "EXEC") in names
    assert ("INSIDE", "EXEC") not in names


def test_in_stream_run_program_is_a_call() -> None:
    chunked = chunk_sample("loans-core/jcl/LNNIGHT.jcl")
    (run,) = [s for s in chunked.symbols if s.name == "LNBATCH"]
    assert run.start_line == 15 and not run.is_definition


def test_garbage_degrades_to_text() -> None:
    chunked = chunk_file(PurePosixPath("X.jcl"), "nothing here\nat all\n", "jcl")
    assert not chunked.parsed and chunked.symbols == []
    assert chunked.chunks and all(c.kind == "text" for c in chunked.chunks)


def test_a_procedure_without_a_job_statement_still_chunks() -> None:
    text = "//MYPROC PROC\n//S1 EXEC PGM=A\n//S2 EXEC PGM=B\n//  PEND\n"
    chunked = chunk_file(PurePosixPath("P.proc"), text, "jcl")
    assert [(c.kind, c.name) for c in chunked.chunks][:1] == [("proc", "MYPROC")]
    assert {(s.name, s.kind) for s in chunked.symbols if not s.is_definition} == {
        ("A", "call"),
        ("B", "call"),
    }
