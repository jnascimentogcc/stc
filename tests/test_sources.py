"""Applications can come from several folders (sources), each read from where it lives (IDX-01)."""

import tempfile
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from stc.cli import app
from stc.config import get_settings
from stc.db.migrate import migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import detect_roots, scan_roots
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, rebuild_relations
from stc.query import QueryEngine, QueryError
from stc.sources import DEFAULT_SOURCE, Roots, SourcesError, as_roots, build_roots, load_sources
from test_catalog import write

runner = CliRunner()

PAYMENTS = "def charge(amount):\n    return amount * 2\n"


def make_sources(tmp_path: Path) -> tuple[Path, Path]:
    """`shop` (a web app calling the API) in one folder, `billing` in another."""
    shop = tmp_path / "shop-folder"
    billing = tmp_path / "elsewhere" / "billing-folder"
    write(shop, "web/package.json", '{"name": "web"}')
    write(shop, "web/client.js", "fetch('http://billing/invoices/42');\n")
    write(billing, "billing/pyproject.toml", '[project]\nname = "billing"\nversion = "1.0"\n')
    write(billing, "billing/payments.py", PAYMENTS)
    write(
        billing,
        "billing/api.py",
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        "@app.get('/invoices/{invoice_id}')\ndef invoice(invoice_id: int):\n    return {}\n",
    )
    return shop, billing


# --- configuration -------------------------------------------------------------------------------


def test_sources_are_read_from_the_file_and_relative_paths_follow_it(tmp_path: Path) -> None:
    file = tmp_path / "conf" / "sources.toml"
    write(
        tmp_path / "conf",
        "sources.toml",
        '[[sources]]\nname = "legacy"\npath = "../code/legacy"\n\n'
        f'[[sources]]\nname = "other"\ntype = "local"\npath = "{tmp_path.as_posix()}/abs"\n',
    )
    sources = load_sources(file)
    assert sources["legacy"] == (tmp_path / "conf" / ".." / "code" / "legacy").resolve()
    assert sources["other"] == Path(tmp_path.as_posix() + "/abs")


@pytest.mark.parametrize(
    "text, message",
    [
        ('[[sources]]\nname = "Legacy"\npath = "x"\n', "lower case"),
        ('[[sources]]\nname = "default"\npath = "x"\n', "STC_ROOT"),
        ('[[sources]]\nname = "a"\npath = "x"\n[[sources]]\nname = "a"\npath = "y"\n', "twice"),
        ('[[sources]]\nname = "a"\ntype = "ftp"\npath = "x"\n', "type"),
        ('[[sources]]\nname = "a"\n', "path"),
        ('[[sources]]\nname = "a"\npath = "x"\ntoken = "t"\n', "token"),
        ("[other]\nx = 1\n", "unknown key"),
    ],
)
def test_a_bad_sources_file_says_what_is_wrong(tmp_path: Path, text: str, message: str) -> None:
    write(tmp_path, "sources.toml", text)
    with pytest.raises(SourcesError, match=message):
        load_sources(tmp_path / "sources.toml")


def test_the_root_is_the_default_source_next_to_the_declared_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")  # a sources.toml of the current folder must not leak in
    write(tmp_path, "sources.toml", '[[sources]]\nname = "legacy"\npath = "old"\n')
    roots = build_roots(tmp_path / "root", tmp_path / "sources.toml")
    assert list(roots.paths) == [DEFAULT_SOURCE, "legacy"]
    assert roots.only("legacy").paths == {"legacy": (tmp_path / "old").resolve()}
    with pytest.raises(SourcesError, match="No source named 'ghost'"):
        roots.only("ghost")
    assert not build_roots(None, None) and as_roots(tmp_path).get(DEFAULT_SOURCE) == tmp_path


# --- scanning ------------------------------------------------------------------------------------


def test_every_source_is_scanned_and_each_application_remembers_its_own(tmp_path: Path) -> None:
    shop, billing = make_sources(tmp_path)
    roots = Roots({"shop": shop, "billing": billing})
    assert {(s.name, s.source) for s in detect_roots(roots)} == {
        ("web", "shop"),
        ("billing", "billing"),
    }
    only = scan_roots(roots, only="billing")
    assert [(a.spec.name, a.spec.source) for a in only] == [("billing", "billing")]
    with pytest.raises(ValueError, match="No application named 'ghost'"):
        scan_roots(roots, only="ghost")


def test_the_same_application_name_in_two_sources_is_refused(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    for folder in (one, two):
        write(folder, "api/package.json", '{"name": "api"}')
    with pytest.raises(ValueError, match="exists in the sources 'a' and 'b'"):
        scan_roots(Roots({"a": one, "b": two}))


# --- the index -----------------------------------------------------------------------------------


@pytest.fixture
def indexed(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    shop, billing = make_sources(tmp_path)
    roots = Roots({"shop": shop, "billing": billing})
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        sync_registry(conn, scan_roots(roots), sources=set(roots.paths))
        yield conn, roots, tmp_path


def test_files_are_read_from_the_folder_of_their_source(indexed) -> None:  # type: ignore[no-untyped-def]
    conn, roots, tmp_path = indexed
    assert chunk_pending(conn, roots).files_chunked >= 4
    assert extract_pending(conn, roots).files_skipped == 0
    assert rebuild_relations(conn, {}, roots).total == 1  # web (one folder) -> billing (another)
    sources = dict(conn.execute("SELECT name, source FROM applications").fetchall())
    assert sources == {"web": "shop", "billing": "billing"}

    engine = QueryEngine(conn, root=roots, catalogs=tmp_path / "catalogs")
    assert engine.read_file("billing", "payments.py").text.splitlines()[0] == "def charge(amount):"
    assert "fetch(" in engine.read_file("web", "client.js").text
    assert engine.search_code("charge amount").items


def test_a_source_that_is_not_configured_cannot_be_read_but_nothing_else_breaks(indexed) -> None:  # type: ignore[no-untyped-def]
    conn, roots, tmp_path = indexed
    partial = roots.only("shop")
    stats = chunk_pending(conn, partial)
    # Only the files of the sources being processed are pending: billing's are left for its own
    # run, not counted as unreadable.
    assert stats.files_chunked >= 2 and stats.files_skipped == 0
    assert chunk_pending(conn, roots).files_chunked >= 2  # ...and they are there when asked
    engine = QueryEngine(conn, root=partial, catalogs=tmp_path / "catalogs")
    assert "fetch(" in engine.read_file("web", "client.js").text
    with pytest.raises(QueryError, match="source 'billing' of billing is not configured"):
        engine.read_file("billing", "payments.py")
    with pytest.raises(QueryError, match="No source is configured"):
        QueryEngine(conn, root=Roots({}), catalogs=tmp_path).read_file("web", "client.js")


def test_a_scan_of_one_source_does_not_report_the_others_as_missing(indexed) -> None:  # type: ignore[no-untyped-def]
    conn, roots, _ = indexed
    result = sync_registry(conn, scan_roots(roots.only("shop")), sources={"shop"})
    assert result.apps_missing == []
    shop_only = scan_roots(roots.only("shop"))
    assert sync_registry(conn, shop_only).apps_missing == ["billing"]  # no `sources`: it all counts
    # An application that moves to another source keeps its row (and what is derived from it).
    moved = scan_roots(Roots({"renamed": roots.paths["billing"]}))
    before = conn.execute("SELECT id FROM applications WHERE name = 'billing'").fetchone()
    sync_registry(conn, moved, partial=True)
    after = conn.execute("SELECT id, source FROM applications WHERE name = 'billing'").fetchone()
    assert before is not None and after == (before[0], "renamed")


# --- command line --------------------------------------------------------------------------------


@pytest.fixture
def cli_env(temp_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    shop, billing = make_sources(tmp_path)
    write(
        tmp_path,
        "sources.toml",
        f'[[sources]]\nname = "billing"\npath = "{billing.as_posix()}"\n',
    )
    for name in ("OPENAI_API_KEY", "STC_EMBEDDINGS_MODEL", "STC_LLM_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(tempfile.mkdtemp()))  # no .env, so local settings cannot leak in
    monkeypatch.setenv("STC_DB_URL", temp_db_url)
    monkeypatch.setenv("STC_ROOT", str(shop))
    monkeypatch.setenv("STC_SOURCES", str(tmp_path / "sources.toml"))
    get_settings.cache_clear()
    yield temp_db_url
    get_settings.cache_clear()


def test_detect_lists_the_applications_of_every_source(cli_env) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["detect"])
    assert result.exit_code == 0, result.output
    assert "web\tdefault:web" in result.output and "billing\tbilling:billing" in result.output
    one = runner.invoke(app, ["detect", "--source", "billing"])
    assert "web" not in one.stdout and "billing" in one.stdout
    assert runner.invoke(app, ["detect", "--source", "ghost"]).exit_code == 2


def test_index_covers_all_sources_and_can_be_limited_to_one(cli_env) -> None:  # type: ignore[no-untyped-def]
    only = runner.invoke(app, ["index", "--source", "billing", "--skip-embeddings"])
    assert only.exit_code == 0, only.output
    with psycopg.connect(cli_env, autocommit=True) as conn:
        assert [r[0] for r in conn.execute("SELECT name FROM applications")] == ["billing"]
    everything = runner.invoke(app, ["index", "--skip-embeddings"])
    assert everything.exit_code == 0, everything.output
    assert "1 application(s) added" in everything.output  # web; billing was already there
    assert "Relations: 1" in everything.output
    with psycopg.connect(cli_env, autocommit=True) as conn:
        rows = conn.execute("SELECT name, source FROM applications ORDER BY name").fetchall()
    assert rows == [("billing", "billing"), ("web", "default")]
    status = runner.invoke(app, ["status", "--check"])
    assert status.exit_code == 0, status.output
    assert "no longer" not in status.output.lower()
