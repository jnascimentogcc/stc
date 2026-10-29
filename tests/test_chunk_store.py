from pathlib import Path

import psycopg

from stc.db.migrate import migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry

SERVICE = "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(conn: psycopg.Connection, root: Path):  # type: ignore[no-untyped-def]
    sync_registry(conn, scan_root(root))
    return chunk_pending(conn, root)


def scalar(conn: psycopg.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def test_chunks_and_symbols_are_stored(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/svc.py", SERVICE)
    write(tmp_path, "app/README.md", "# Title\n\nbody\n\n## Usage\n\nrun it\n")
    with psycopg.connect(temp_db_url) as conn:
        stats = run(conn, tmp_path)
        assert stats.files_chunked == 3 and stats.files_as_text == 2  # toml + markdown
        rows = conn.execute(
            "SELECT c.kind, c.name, c.start_line, c.end_line FROM chunks c"
            " JOIN files f ON f.id = c.file_id WHERE f.path = 'svc.py' ORDER BY c.start_line"
        ).fetchall()
        assert rows == [("function", "alpha", 1, 2), ("function", "beta", 5, 6)]
        symbol = conn.execute(
            "SELECT s.name, s.kind, s.is_definition, c.name FROM symbols s"
            " JOIN chunks c ON c.id = s.chunk_id WHERE s.name = 'beta' ORDER BY s.is_definition"
        ).fetchall()
        assert symbol == [("beta", "call", False, "alpha"), ("beta", "function", True, "beta")]


def test_rechunking_is_incremental_and_keeps_unchanged_chunks(
    temp_db_url: str, tmp_path: Path
) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/svc.py", SERVICE)
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        alpha_id = scalar(conn, "SELECT id FROM chunks WHERE name = 'alpha'")

        again = run(conn, tmp_path)
        assert again.files_chunked == 0 and again.chunks_new == 0  # XCT-04

        # Insert lines above and edit only `beta`: `alpha` keeps its row (and embedding).
        write(
            tmp_path,
            "app/svc.py",
            "import os\nimport sys\n\n\n" + SERVICE.replace("return 1", "return 2"),
        )
        changed = run(conn, tmp_path)
        assert changed.files_chunked == 1
        assert (changed.chunks_new, changed.chunks_kept, changed.chunks_removed) == (2, 1, 1)
        assert scalar(conn, "SELECT id FROM chunks WHERE name = 'alpha'") == alpha_id
        assert scalar(conn, "SELECT start_line FROM chunks WHERE name = 'alpha'") == 5


def test_file_that_becomes_secret_loses_its_chunks(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/config.py", "def load():\n    return 1\n")
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM chunks c JOIN files f ON f.id=c.file_id"
                " WHERE f.path = 'config.py'",
            )
            == 1
        )
        write(tmp_path, "app/config.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        run(conn, tmp_path)
        assert (
            scalar(conn, "SELECT count(*) FROM files WHERE path='config.py' AND status='secret'")
            == 1
        )
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM chunks c JOIN files f ON f.id=c.file_id"
                " WHERE f.path = 'config.py'",
            )
            == 0
        )
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM symbols s JOIN files f ON f.id=s.file_id"
                " WHERE f.path = 'config.py'",
            )
            == 0
        )


def test_file_changed_after_scan_is_skipped_not_misindexed(
    temp_db_url: str, tmp_path: Path
) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/svc.py", SERVICE)
    with psycopg.connect(temp_db_url) as conn:
        sync_registry(conn, scan_root(tmp_path))
        write(tmp_path, "app/svc.py", "def other():\n    pass\n")  # changes after the scan
        stats = chunk_pending(conn, tmp_path)
        assert stats.files_skipped == 1
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM chunks c JOIN files f ON f.id=c.file_id"
                " WHERE f.path = 'svc.py'",
            )
            == 0
        )
