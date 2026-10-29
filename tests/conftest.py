import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql


@pytest.fixture
def temp_db_url() -> Iterator[str]:
    """URL of a fresh, empty database on the server in STC_TEST_DB_URL (skipped if unset).

    STC_TEST_DB_URL must point at a pgvector-enabled PostgreSQL where the user can create
    databases, e.g. postgresql://postgres:test@localhost:55432/postgres
    """
    admin_url = os.environ.get("STC_TEST_DB_URL")
    if not admin_url:
        pytest.skip("STC_TEST_DB_URL is not set")
    name = f"stc_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield psycopg.conninfo.make_conninfo(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
