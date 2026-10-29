from pathlib import Path

import psycopg

from stc.db.migrate import migrate
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry


def make_root(root: Path) -> None:
    for rel, content in {
        "alpha/package.json": "{}",
        "alpha/src/a.js": "a",
        "alpha/.env": "X=1\n",
        "beta/pyproject.toml": "[project]",
        "beta/b.py": "b",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, count(*) FROM files GROUP BY status").fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def test_sync_registers_applications_and_files(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        result = sync_registry(conn, scan_root(tmp_path))
        assert sorted(result.apps_added) == ["alpha", "beta"]
        names = [r[0] for r in conn.execute("SELECT name FROM applications ORDER BY name")]
        assert names == ["alpha", "beta"]
        assert counts(conn) == {"indexed": 4, "secret": 1}
        secret = conn.execute("SELECT path, exclusion_reason FROM files WHERE status='secret'")
        assert secret.fetchone() == (".env", "secret by file name")


def test_sync_is_idempotent_and_tracks_changes(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        sync_registry(conn, scan_root(tmp_path))
        again = sync_registry(conn, scan_root(tmp_path))
        assert not any(a.changed for a in again.applications)  # XCT-04
        assert again.apps_added == []

        (tmp_path / "beta" / "b.py").write_text("changed", encoding="utf-8")
        (tmp_path / "alpha" / "src" / "a.js").unlink()
        (tmp_path / "beta" / "new.py").write_text("n", encoding="utf-8")
        result = {a.name: a for a in sync_registry(conn, scan_root(tmp_path)).applications}
        assert (result["alpha"].files_removed, result["alpha"].files_changed) == (1, 0)
        assert (result["beta"].files_added, result["beta"].files_changed) == (1, 1)  # LCY-04


def test_missing_application_is_kept_unless_pruned(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        sync_registry(conn, scan_root(tmp_path))
        (tmp_path / "beta" / "pyproject.toml").unlink()
        kept = sync_registry(conn, scan_root(tmp_path))
        assert kept.apps_missing == ["beta"] and kept.apps_removed == []
        gone = sync_registry(conn, scan_root(tmp_path), prune=True)
        assert gone.apps_removed == ["beta"]
        row = conn.execute("SELECT count(*) FROM applications").fetchone()
        assert row is not None and row[0] == 1
