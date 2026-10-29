from pathlib import Path

import psycopg
import pytest

from stc.db.migrate import MigrationError, discover, migrate

EXPECTED_TABLES = {
    "applications",
    "files",
    "chunks",
    "chunk_embeddings",
    "symbols",
    "relations",
    "catalogs",
    "catalog_sections",
    "catalog_section_embeddings",
    "indexing_runs",
    "tasks",
    "conversations",
    "messages",
    "feedback",
    "schema_migrations",
}


def test_discover_orders_and_checksums_bundled_migrations() -> None:
    migrations = discover()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert all(len(m.checksum) == 64 for m in migrations)


def test_discover_rejects_bad_filename(tmp_path: Path) -> None:
    (tmp_path / "init.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError):
        discover(tmp_path)


def test_migrate_creates_schema_and_is_idempotent(temp_db_url: str) -> None:
    first = migrate(temp_db_url)
    assert len(first) == len(discover())
    assert migrate(temp_db_url) == []

    with psycopg.connect(temp_db_url) as conn:
        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    assert tables >= EXPECTED_TABLES


def test_migrate_detects_modified_migration(temp_db_url: str, tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE t (id int);")
    migrate(temp_db_url, directory=tmp_path)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE t (id bigint);")
    with pytest.raises(MigrationError):
        migrate(temp_db_url, directory=tmp_path)


def test_failed_migration_is_rolled_back(temp_db_url: str, tmp_path: Path) -> None:
    (tmp_path / "0001_bad.sql").write_text("CREATE TABLE t (id int); SELECT 1/0;")
    with pytest.raises(psycopg.Error):
        migrate(temp_db_url, directory=tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        row = conn.execute("SELECT to_regclass('public.t')").fetchone()
    assert row is not None and row[0] is None


def test_app_role_can_use_data_but_not_own_schema(temp_db_url: str) -> None:
    migrate(temp_db_url, app_password="app-secret")
    app_url = psycopg.conninfo.make_conninfo(temp_db_url, user="stc_app", password="app-secret")
    with psycopg.connect(app_url, autocommit=True) as conn:
        conn.execute("INSERT INTO applications (name, path) VALUES ('demo', 'demo')")
        row = conn.execute("SELECT count(*) FROM applications").fetchone()
        assert row is not None and row[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DROP TABLE applications")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM schema_migrations")


def test_schema_constraints_and_vector_search(temp_db_url: str) -> None:
    migrate(temp_db_url)
    vector = "[" + ",".join(["0.1"] * 1536) + "]"
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO applications (id, name, path) VALUES (1, 'a', 'a'), (2, 'b', 'b')"
        )
        conn.execute(
            "INSERT INTO files (id, application_id, path, size_bytes, status)"
            " VALUES (1, 1, 'main.py', 10, 'indexed')"
        )
        conn.execute(
            "INSERT INTO chunks (id, file_id, kind, name, start_line, end_line, content,"
            " content_sha256) VALUES (1, 1, 'function', 'handle_order', 1, 5,"
            " 'def handle_order(): pass', 'x')"
        )
        conn.execute(
            "INSERT INTO chunk_embeddings (chunk_id, provider, model, embedding)"
            " VALUES (1, 'openai', 'm', %s::vector)",
            (vector,),
        )
        nearest = conn.execute(
            "SELECT chunk_id FROM chunk_embeddings ORDER BY embedding <=> %s::vector LIMIT 1",
            (vector,),
        ).fetchone()
        assert nearest is not None and nearest[0] == 1
        lexical = conn.execute("SELECT id FROM chunks WHERE name % 'handle_ordr'").fetchone()
        assert lexical is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO relations (source_app_id, target_app_id, type) VALUES (1, 1, 'http')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO chunks (file_id, kind, start_line, end_line, content, content_sha256)"
                " VALUES (1, 'text', 5, 1, 'x', 'x')"
            )
        conn.execute("DELETE FROM applications WHERE id = 1")
        left = conn.execute("SELECT count(*) FROM chunks").fetchone()
        assert left is not None and left[0] == 0  # cascade through files
