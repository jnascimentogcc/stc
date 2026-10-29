"""Forward-only SQL migration runner.

Migrations are `NNNN_name.sql` files in `stc/db/migrations/`. Each one runs in its own
transaction and is recorded in `schema_migrations` with a checksum, so editing an applied
migration is detected. A session-level advisory lock serializes concurrent runners.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
APP_ROLE = "stc_app"

_LOCK_ID = 0x5354_4300  # "STC\0"
_FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        text = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                sql=text,
                checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise MigrationError("Duplicate migration version numbers")
    return migrations


def migrate(
    url: str,
    *,
    app_password: str | None = None,
    directory: Path = MIGRATIONS_DIR,
) -> list[Migration]:
    """Apply pending migrations and (re)grant privileges to the `stc_app` role.

    Returns the migrations applied by this call. When `app_password` is given the
    `stc_app` role is created or updated with it.
    """
    available = discover(directory)
    applied: list[Migration] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version integer PRIMARY KEY,"
                " name text NOT NULL,"
                " checksum text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {
                int(row[0]): str(row[1])
                for row in conn.execute("SELECT version, checksum FROM schema_migrations")
            }
            for migration in available:
                recorded = done.get(migration.version)
                if recorded is not None:
                    if recorded != migration.checksum:
                        raise MigrationError(
                            f"Migration {migration.version:04d}_{migration.name} was modified "
                            "after being applied"
                        )
                    continue
                with conn.transaction():
                    conn.execute(migration.sql.encode("utf-8"))
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum)"
                        " VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                applied.append(migration)
            if app_password is not None:
                _ensure_app_role(conn, app_password)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied


def _ensure_app_role(conn: psycopg.Connection, password: str) -> None:
    role = sql.Identifier(APP_ROLE)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL(verb), role, sql.Literal(password)
        )
    )
    database = sql.Identifier(_current_database(conn))
    statements = [
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
            role
        ),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role),
        sql.SQL("REVOKE ALL ON TABLE schema_migrations FROM {}").format(role),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
            " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(role),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(role),
    ]
    for statement in statements:
        conn.execute(statement)


def _current_database(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT current_database()").fetchone()
    assert row is not None
    return str(row[0])
