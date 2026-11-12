"""Signals from COBOL, copybooks, JCL and DDL, on the sample Root and on synthetic snippets."""

from pathlib import Path, PurePosixPath

from stc.indexer.signal_types import Signal
from stc.indexer.signals import EXTRACTOR_VERSION, extract_signals, wanted

SAMPLE = Path(__file__).parents[1] / "examples" / "cobol-bank-root"
LANGUAGES = {".cbl": "cobol", ".cpy": "cobol-copybook", ".jcl": "jcl", ".sql": "sql"}


def sample_signals(rel: str) -> list[Signal]:
    path = PurePosixPath(rel)
    return extract_signals(path, (SAMPLE / rel).read_text(encoding="utf-8"), LANGUAGES[path.suffix])


def triples(found: list[Signal]) -> set[tuple[str, str, str]]:
    return {(s.kind, s.role, s.key) for s in found}


def jcl(*lines: str) -> set[tuple[str, str, str]]:
    text = "\n".join(["//J1 JOB", "//S1 EXEC PGM=X", *lines]) + "\n"
    return triples(extract_signals(PurePosixPath("a.jcl"), text, "jcl")) - {
        ("program", "consumes", "X")
    }


def test_lnelig_signals() -> None:
    found = triples(sample_signals("loans-core/src/LNELIG.cbl"))
    assert {
        ("program", "provides", "LNELIG"),
        ("program", "consumes", "LNCALC"),
        ("copybook", "consumes", "LNCALCP"),
        ("copybook", "consumes", "LNSIMCA"),
    } <= found


def test_lnsimul_cics_and_sql() -> None:
    found = triples(sample_signals("loans-core/src/LNSIMUL.cbl"))
    assert ("program", "consumes", "LNRATE") in found
    assert ("program", "consumes", "LNELIG") in found
    assert ("table", "consumes", "CUSTOMER") in found
    assert ("copybook", "consumes", "CUSTREC") in found
    assert not [t for t in found if "SQLCA" in t[2]]


def test_lnbatch_tables_from_a_comma_list() -> None:
    text = (SAMPLE / "loans-core/src/LNBATCH.cbl").read_text(encoding="utf-8")
    found = sample_signals("loans-core/src/LNBATCH.cbl")
    tables = {(s.key, s.start_line, s.end_line) for s in found if s.kind == "table"}
    assert {key for key, _, _ in tables} == {"CONTRACT", "INSTALLMENT"}
    update = next(i for i, line in enumerate(text.split("\n"), 1) if "UPDATE CONTRACT" in line)
    assert any(k == "CONTRACT" and a <= update <= b for k, a, b in tables)
    assert not [s for s in found if s.kind == "table" and s.key in ("C", "I")]  # aliases


def test_ddl_tables_are_provided() -> None:
    found = triples(sample_signals("loans-core/ddl/tables.sql"))
    assert found == {
        ("table", "provides", "CUSTOMER"),
        ("table", "provides", "CONTRACT"),
        ("table", "provides", "INSTALLMENT"),
    }


def test_copybook_file_is_provided() -> None:
    found = triples(sample_signals("loans-core/copybooks/LATEREC.cpy"))
    assert ("copybook", "provides", "LATEREC") in found


def test_jcl_programs_and_datasets() -> None:
    assert triples(sample_signals("loans-core/jcl/LNNIGHT.jcl")) == {
        ("program", "consumes", "IKJEFT01"),
        ("program", "consumes", "LNBATCH"),  # RUN PROGRAM(LNBATCH) in the in-stream data
        ("program", "consumes", "SORT"),
        ("dataset", "provides", "BANK.LOANS.LATE.DAILY"),
        ("dataset", "consumes", "BANK.LOANS.LATE.DAILY"),
        ("dataset", "provides", "BANK.LOANS.LATE.SORTED"),
    }
    assert triples(sample_signals("collections/jcl/COLLJOB.jcl")) == {
        ("program", "consumes", "COLLDUN"),
        ("dataset", "consumes", "BANK.LOANS.LATE.SORTED"),
        ("dataset", "provides", "BANK.COLL.DUNNING.DAILY"),
    }  # nothing for the load library on STEPLIB


def test_dataset_normalization() -> None:
    found = jcl(
        "//D1 DD DSN=BANK.X.GDG(+1),DISP=(NEW,CATLG)",
        "//D2 DD DSN=&&TEMP,DISP=(NEW,PASS)",
        "//D3 DD DSN=&HLQ..X,DISP=SHR",
        "//D4 DD DSN=*.STEP1.DD1,DISP=SHR",
        "//D5 DD DSN=bank.lower.case,DISP=OLD",
        "//D6 DD DSN=BANK.NODISP",
        "//D7 DD DSN=BANK.EMPTY,DISP=(,CATLG)",
        "//D8 DD DSN=BANK.APPEND,DISP=MOD",
        "//JOBLIB DD DSN=BANK.ANYTHING,DISP=SHR",
        "//STEPLIB DD DSN=BANK.A.LOADLIB,DISP=SHR",
        "//     DD DSN=BANK.B.LOADLIB,DISP=SHR",
    )
    assert found == {
        ("dataset", "provides", "BANK.X.GDG"),
        ("dataset", "consumes", "BANK.LOWER.CASE"),
        ("dataset", "provides", "BANK.EMPTY"),
        ("dataset", "provides", "BANK.APPEND"),
    }


def test_dynamic_call_is_unresolved() -> None:
    text = "       PROCEDURE DIVISION.\n       MAIN-PARA.\n           CALL WS-PROG USING X.\n"
    found = extract_signals(PurePosixPath("a.cbl"), text, "cobol")
    assert ("program", "consumes", "?WS-PROG") in triples(found)


def test_cics_link_with_a_variable_is_unresolved() -> None:
    text = "       PROCEDURE DIVISION.\n       P.\n           EXEC CICS XCTL PROGRAM(WS-NEXT) END-EXEC.\n"
    found = extract_signals(PurePosixPath("a.cbl"), text, "cobol")
    assert triples(found) == {("program", "consumes", "?WS-NEXT")}


def test_comments_and_literals_make_no_signals() -> None:
    text = (
        "      * CALL 'GHOST'.\n"
        "      * COPY GHOST.\n"
        "       PROCEDURE DIVISION.\n"
        "       MAIN-PARA.\n"
        "           DISPLAY 'CALL GHOST'.\n"
        "           DISPLAY 'COPY GHOST'.\n"
    )
    assert extract_signals(PurePosixPath("a.cbl"), text, "cobol") == []


def test_garbage_gives_no_signals() -> None:
    for text in ("The quick brown fox\njumps.\n", "�" * 80 + "\n", "//\n//*\n/*\n"):
        for name, language in (("a.cbl", "cobol"), ("a.jcl", "jcl"), ("a.cpy", "cobol-copybook")):
            found = extract_signals(PurePosixPath(name), text, language)
            assert [s for s in found if s.kind != "copybook"] == []


def test_sql_data_manipulation_inside_cobol() -> None:
    text = (
        "       PROCEDURE DIVISION.\n"
        "       P.\n"
        "           EXEC SQL INSERT INTO BANK.AUDIT (A) VALUES (:X) END-EXEC.\n"
        "           EXEC SQL DELETE FROM OLD_ROWS WHERE A = 1 END-EXEC.\n"
        "           EXEC SQL SELECT A INTO :X FROM (SELECT A FROM INNER_T) AS D END-EXEC.\n"
        "           EXEC SQL SELECT A INTO :X FROM T1 JOIN T2 ON T1.K = T2.K END-EXEC.\n"
    )
    found = {
        s.key for s in extract_signals(PurePosixPath("a.cbl"), text, "cobol") if s.kind == "table"
    }
    assert found == {"BANK.AUDIT", "OLD_ROWS", "INNER_T", "T1", "T2"}


def test_existing_languages_are_unchanged() -> None:
    py = extract_signals(PurePosixPath("a.py"), '@app.get("/orders")\ndef f(): ...\n', "python")
    assert ("http", "provides", "/orders") in triples(py)
    sql = "SELECT 'http://x/y/z'; -- kafka topic 'a.b'\n"
    assert extract_signals(PurePosixPath("a.sql"), sql, "sql") == []


def test_mainframe_files_are_wanted_and_tests_are_skipped() -> None:
    for name, language in (("a.cbl", "cobol"), ("a.cpy", "cobol-copybook"), ("a.jcl", "jcl")):
        assert wanted(PurePosixPath(name), language)
    assert wanted(PurePosixPath("a.sql"), "sql")
    text = "       PROGRAM-ID. T1.\n"
    assert extract_signals(PurePosixPath("tests/T1.cbl"), text, "cobol") == []


def test_extractor_version_was_bumped() -> None:
    assert EXTRACTOR_VERSION == 4
