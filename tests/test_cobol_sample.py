"""The COBOL sample Root end to end: detection, chunks, evaluation evidence and relations."""

from pathlib import Path, PurePosixPath

import psycopg

from stc.db.migrate import migrate
from stc.evaluation.model import load_questions
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.chunking import ChunkedFile, chunk_file
from stc.indexer.detect import detect_applications
from stc.indexer.languages import language_of
from stc.indexer.pipeline import host_aliases, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, list_relations, rebuild_relations

REPO = Path(__file__).parents[1]
SAMPLE = REPO / "examples" / "cobol-bank-root"
MAINFRAME = ("cobol", "cobol-copybook", "jcl")


def chunked(rel: str) -> ChunkedFile:
    path = PurePosixPath(rel)
    language = language_of(path)
    assert language in MAINFRAME
    return chunk_file(path, (SAMPLE / rel).read_text(encoding="utf-8"), language)


def test_sample_root_is_detected_without_stc_toml() -> None:
    found = detect_applications(SAMPLE)
    assert [s.name for s in found] == ["collections", "loans-core"]
    assert {s.detected_by for s in found} == {"auto"}


def test_demo_root_is_unaffected() -> None:
    names = {s.name for s in detect_applications(REPO / "examples" / "demo-root")}
    assert {"demo-api", "demo-web"} <= names


def test_every_paragraph_is_a_symbol_inside_one_chunk() -> None:
    programs = sorted(SAMPLE.rglob("*.cbl"))
    assert programs
    for program in programs:
        rel = program.relative_to(SAMPLE).as_posix()
        result = chunked(rel)
        assert result.parsed, rel
        ordered = sorted(result.chunks, key=lambda c: c.start_line)
        for before, after in zip(ordered, ordered[1:], strict=False):
            assert after.start_line == before.end_line + 1, f"{rel}: gap or overlap"
        paragraphs = [s for s in result.symbols if s.kind == "paragraph" and s.is_definition]
        assert paragraphs, rel
        for symbol in paragraphs:
            inside = [c for c in result.chunks if c.start_line <= symbol.start_line <= c.end_line]
            assert len(inside) == 1, f"{rel}: {symbol.name}"
            assert symbol.end_line <= inside[0].end_line, f"{rel}: {symbol.name}"


def test_evaluation_evidence_lines_point_at_a_few_chunks_not_the_whole_file() -> None:
    """Evidence is a rule's paragraphs: the evaluator matches by overlap, so one chunk is the
    norm, and a range over several adjacent paragraphs is fine while it is not the whole file."""
    questions = load_questions(REPO / "evaluation" / "cobol-bank-questions.yaml")
    checked = 0
    for question in questions.questions:
        for item in question.expect.evidence:
            rel = f"{item.application}/{item.file}"
            if item.lines is None or language_of(PurePosixPath(rel)) not in MAINFRAME:
                continue
            first, last = item.lines
            chunks = chunked(rel).chunks
            hit = [c for c in chunks if c.start_line <= last and first <= c.end_line]
            where = f"{question.id}: {rel}:{first}-{last}"
            assert hit, where
            assert hit[0].start_line <= first and last <= hit[-1].end_line, where
            assert len(chunks) == 1 or len(hit) < len(chunks), f"{where} is the whole file"
            assert len(hit) <= 3, where
            checked += 1
    assert checked > 10


def test_applications_are_linked_and_indexing_twice_changes_nothing(
    temp_db_url: str,
) -> None:
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as conn:

        def index() -> tuple[int, int]:
            sync_registry(conn, scan_root(SAMPLE))
            chunk_pending(conn, SAMPLE)
            extract_pending(conn, SAMPLE)
            rebuild_relations(conn, host_aliases(SAMPLE))
            counts = [
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
                for table in ("symbols", "chunks")
            ]
            return (int(counts[0][0]), int(counts[1][0]))  # type: ignore[index]

        first = index()
        assert first[0] > 0 and first[1] > 0
        relations = {(s, t, ty): e for s, t, ty, _c, _d, e, _n in list_relations(conn)}
        assert "collections/jcl/COLLJOB.jcl" in relations[("collections", "loans-core", "batch")]
        assert "collections/src/COLLDUN.cbl" in relations[("collections", "loans-core", "copybook")]
        assert index() == first
        assert {(s, t, ty): e for s, t, ty, _c, _d, e, _n in list_relations(conn)} == relations
