"""Running the system: indexing runs, reindex, status, change detection, catalog embeddings."""

import asyncio
import tempfile
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from stc.catalog.embed import embed_sections, estimate_sections, section_text
from stc.catalog.store import generate_and_store, prepare
from stc.cli import app
from stc.config import get_settings
from stc.db.migrate import migrate
from stc.indexer.changes import pending_changes, removed_applications
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.runs import finish_run, has_finished_run, recent_runs, start_run
from stc.query import QueryEngine
from test_catalog import FakeLLM, index, make_root, reply, write
from test_query import KeywordEmbeddings, KeywordQueryEmbedder

runner = CliRunner()


@pytest.fixture
def db(temp_db_url: str):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        yield conn


# --- indexing runs -------------------------------------------------------------------------------


def test_a_run_is_recorded_with_its_outcome(db: psycopg.Connection) -> None:
    assert not has_finished_run(db)
    run_id = start_run(db, "full")
    assert recent_runs(db)[0].status == "running" and recent_runs(db)[0].finished_at is None
    finish_run(db, run_id, "succeeded", {"applications": 2})
    (run,) = recent_runs(db)
    assert (run.kind, run.status, run.summary) == ("full", "succeeded", {"applications": 2})
    assert run.seconds is not None and run.seconds >= 0 and has_finished_run(db)

    failed = start_run(db, "incremental")
    finish_run(db, failed, "failed", {}, "x" * 1000)
    assert recent_runs(db)[0].error is not None and len(recent_runs(db)[0].error or "") == 300
    assert [r.id for r in recent_runs(db, limit=1)] == [failed]


def test_a_run_for_an_application_names_it_and_old_unfinished_runs_are_closed(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    make_root(root)
    sync_registry(db, scan_root(root))
    stuck = start_run(db, "full")
    db.execute(
        "UPDATE indexing_runs SET started_at = now() - interval '3 hours' WHERE id = %s", (stuck,)
    )
    fresh = start_run(db, "application", "svc")
    runs = {r.id: r for r in recent_runs(db)}
    assert runs[stuck].status == "failed" and "ended before" in (runs[stuck].error or "")
    assert runs[fresh].application == "svc" and runs[fresh].status == "running"


# --- scanning one application, and what changed --------------------------------------------------


def test_scan_one_application_and_reject_an_unknown_one(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_root(root)
    assert [a.spec.name for a in scan_root(root, only="web")] == ["web"]
    with pytest.raises(ValueError, match="No application named 'nope'.*svc"):
        scan_root(root, only="nope")


def test_a_partial_sync_leaves_the_other_applications_alone(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    make_root(root)
    sync_registry(db, scan_root(root))
    result = sync_registry(db, scan_root(root, only="svc"), prune=True, partial=True)
    assert result.apps_missing == [] and result.apps_removed == []
    assert {str(r[0]) for r in db.execute("SELECT name FROM applications")} == {"svc", "web"}
    full = sync_registry(db, scan_root(root, only="svc"))
    assert full.apps_missing == ["web"]  # without `partial` it is reported, as before


def test_pending_changes_without_writing(db: psycopg.Connection, tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_root(root)
    first = scan_root(root)
    assert all(c.new_application for c in pending_changes(db, first))
    sync_registry(db, first)
    assert not any(c.any for c in pending_changes(db, scan_root(root)))

    write(root, "svc/app.py", "print('changed')\n")
    write(root, "svc/extra.py", "x = 1\n")
    (root / "svc" / "settings.py").unlink()
    write(root, "newapp/package.json", '{"name": "newapp"}')
    changes = {c.name: c for c in pending_changes(db, scan_root(root))}
    assert (changes["svc"].added, changes["svc"].changed, changes["svc"].removed) == (1, 1, 1)
    assert not changes["web"].any and changes["newapp"].new_application
    # Nothing was written by looking.
    assert {str(r[0]) for r in db.execute("SELECT name FROM applications")} == {"svc", "web"}

    (root / "web").rename(root / "moved")
    assert removed_applications(db, scan_root(root)) == ["web"]


# --- catalog embeddings --------------------------------------------------------------------------


def test_catalog_sections_are_embedded_once_and_again_when_the_model_changes(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    make_root(root)
    index(db, root)
    llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
    prepared = prepare(db, "svc", tmp_path / "c", provider="fake", model="m", language="English")
    asyncio.run(generate_and_store(db, llm, prepared, language="English"))

    estimate = estimate_sections(db, "fake", "keywords")
    assert estimate.sections == 9 and estimate.tokens > 0
    client = KeywordEmbeddings()
    stats = asyncio.run(embed_sections(db, client))
    assert stats.chunks_embedded == 9 and stats.requests == 1
    assert estimate_sections(db, "fake", "keywords").sections == 0
    assert asyncio.run(embed_sections(db, client)).chunks_embedded == 0  # idempotent

    other = KeywordEmbeddings()
    other.model = "other"
    assert estimate_sections(db, "fake", "other").sections == 9  # another model: stale
    assert asyncio.run(embed_sections(db, other)).chunks_replaced == 9
    assert section_text("svc", "purpose", "x").startswith("svc - Functional purpose")


def test_catalog_search_is_hybrid_once_sections_are_embedded(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    make_root(root)
    index(db, root)
    llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
    prepared = prepare(db, "svc", tmp_path / "c", provider="fake", model="m", language="English")
    asyncio.run(generate_and_store(db, llm, prepared, language="English"))
    engine = QueryEngine(db, root=root, catalogs=tmp_path / "c", embedder=KeywordQueryEmbedder())

    before = engine.search_catalogs("invoices")
    assert before.items and all(h.matched_by == ["lexical"] for h in before.items)
    assert any("run `stc embed`" in n for n in before.notes)

    asyncio.run(embed_sections(db, KeywordEmbeddings()))
    after = engine.search_catalogs("invoices")
    assert any("semantic" in h.matched_by and "lexical" in h.matched_by for h in after.items)
    assert after.notes == []
    words_only = QueryEngine(db, root=root, catalogs=tmp_path / "c").search_catalogs("invoices")
    assert any("words only" in n for n in words_only.notes)


# --- command line --------------------------------------------------------------------------------


@pytest.fixture
def cli_env(temp_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    for name in ("OPENAI_API_KEY", "STC_EMBEDDINGS_MODEL", "STC_LLM_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(tempfile.mkdtemp()))  # no .env, so local settings cannot leak in
    monkeypatch.setenv("STC_DB_URL", temp_db_url)
    monkeypatch.setenv("STC_ROOT", str(root))
    get_settings.cache_clear()
    yield root, temp_db_url
    get_settings.cache_clear()


def test_index_records_runs_and_reindex_reprocesses_without_changes(cli_env) -> None:  # type: ignore[no-untyped-def]
    root, url = cli_env
    first = runner.invoke(app, ["index", "--skip-embeddings"])
    assert first.exit_code == 0, first.output
    assert "(full): succeeded" in first.output

    second = runner.invoke(app, ["index", "--skip-embeddings"])
    assert "(incremental): succeeded" in second.output
    assert "0 file(s) chunked" in second.output  # nothing changed: nothing reprocessed (XCT-04)

    forced = runner.invoke(app, ["reindex", "svc", "--skip-embeddings"])
    assert forced.exit_code == 0, forced.output
    assert "(application): succeeded" in forced.output
    assert "0 file(s) chunked" not in forced.output  # unchanged files were derived again
    assert "0 new" in forced.output  # but their chunks (and vectors) were kept

    whole = runner.invoke(app, ["reindex", "--skip-embeddings"])
    assert "(full): succeeded" in whole.output

    with psycopg.connect(url, autocommit=True) as conn:
        runs = recent_runs(conn)
    assert [(r.kind, r.application, r.status) for r in runs] == [
        ("full", None, "succeeded"),
        ("application", "svc", "succeeded"),
        ("incremental", None, "succeeded"),
        ("full", None, "succeeded"),
    ]
    assert runs[1].summary["forced"] is True and runs[2].summary["forced"] is False


def test_reindex_an_unknown_application_is_an_error(cli_env) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["reindex", "ghost", "--skip-embeddings"])
    assert result.exit_code == 1 and "No application named 'ghost'" in result.output


def test_a_run_that_could_not_embed_is_partial(cli_env) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["index"])  # no embeddings model or key configured
    assert result.exit_code == 0 and "Embeddings skipped" in result.output
    assert "(full): partial" in result.output


def test_status_shows_state_pending_work_and_runs(cli_env) -> None:  # type: ignore[no-untyped-def]
    empty = runner.invoke(app, ["status"])
    assert empty.exit_code == 0 and "No indexing run recorded yet" in empty.output

    runner.invoke(app, ["index", "--skip-embeddings"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "svc  [" in result.output and "files: " in result.output
    assert "no catalog yet: svc, web" in result.output
    assert "Last runs:" in result.output and "#1 full succeeded" in result.output

    one = runner.invoke(app, ["status", "web"])
    assert "svc  [" not in one.output and "web  [" in one.output
    assert runner.invoke(app, ["status", "ghost"]).exit_code == 2


def test_status_check_lists_what_changed_in_the_root(cli_env) -> None:  # type: ignore[no-untyped-def]
    root, _url = cli_env
    runner.invoke(app, ["index", "--skip-embeddings"])
    clean = runner.invoke(app, ["status", "--check"])
    assert "The Root has no changes since the last run." in clean.output

    write(root, "svc/app.py", "print('changed')\n")
    write(root, "brandnew/package.json", '{"name": "brandnew"}')
    (root / "web" / "client.js").unlink()
    changed = runner.invoke(app, ["status", "--check"])
    assert "svc: 0 new, 1 changed, 0 removed file(s) in the Root (`stc index`)" in changed.output
    assert "brandnew: new application in the Root" in changed.output
    assert "web: 0 new, 0 changed, 1 removed file(s)" in changed.output
    # Looking changed nothing: the run is still the only one.
    assert "#2" not in runner.invoke(app, ["status"]).output
