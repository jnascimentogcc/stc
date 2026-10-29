from pathlib import Path

import psycopg

from stc.db.migrate import migrate
from stc.indexer.pipeline import host_aliases, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, list_relations, rebuild_relations


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(conn: psycopg.Connection, root: Path):  # type: ignore[no-untyped-def]
    sync_registry(conn, scan_root(root))
    signals = extract_pending(conn, root)
    return signals, rebuild_relations(conn, host_aliases(root))


def scalar(conn: psycopg.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def make_root(root: Path) -> None:
    write(root, "web/package.json", '{"name": "web", "dependencies": {"acme-core": "1"}}')
    write(
        root,
        "web/src/client.js",
        "const r = await fetch('http://billing/api/invoices/42');\n"
        "const db = 'postgresql://pg.corp:5432/ledger';\n"
        "producer.send({ topic: 'orders.created', messages });\n",
    )
    write(root, "billing/pyproject.toml", '[project]\nname = "billing"\n')
    write(
        root,
        "billing/api.py",
        '@app.get("/api/invoices/{invoice_id}")\ndef one(invoice_id): ...\n'
        'DB = "postgresql://pg.corp:5432/ledger"\n',
    )
    write(root, "core/pyproject.toml", '[project]\nname = "acme-core"\n')
    write(root, "shipping/package.json", '{"name": "shipping"}')
    write(root, "shipping/src/listener.js", "@KafkaListener(topics = 'orders.created')\n")


def test_relations_are_derived_stored_and_idempotent(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        signals, relations = run(conn, tmp_path)
        assert signals.files_extracted >= 4 and signals.signals > 0
        rows = {(s, t, ty): (c, d) for s, t, ty, c, d, _e, _n in list_relations(conn)}
        assert (
            rows[("web", "billing", "http")] == ("high", "http://billing/api/invoices/{}")
            or rows[("web", "billing", "http")][0] == "high"
        )
        assert rows[("web", "core", "library")][0] == "high"
        assert rows[("web", "shipping", "messaging")] == ("medium", "topic orders.created")
        assert rows[("billing", "web", "shared_database")][0] == "medium"  # lower id first
        assert relations.total == 4 and relations.added == 4

        evidence = next(
            e
            for s, t, ty, *_x, e, _n in list_relations(conn)
            if (s, t, ty) == ("web", "billing", "http")
        )
        assert evidence == "web/src/client.js:1-1"  # the call site

        again_signals, again = run(conn, tmp_path)
        assert again_signals.files_extracted == 0  # XCT-04
        assert (again.added, again.updated, again.removed) == (0, 0, 0)


def test_relations_follow_the_code(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        write(tmp_path, "web/src/client.js", "const nothing = 1;\n")  # the calls are gone
        _signals, relations = run(conn, tmp_path)
        remaining = {(s, t, ty) for s, t, ty, *_x in list_relations(conn)}
        assert ("web", "billing", "http") not in remaining
        assert ("web", "shipping", "messaging") not in remaining
        assert ("web", "core", "library") in remaining  # still declared in package.json
        assert relations.removed == 3


def test_a_file_that_becomes_secret_loses_its_signals(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM signals s JOIN files f ON f.id = s.file_id WHERE f.path = 'api.py'",
            )
            > 0
        )
        write(
            tmp_path,
            "billing/api.py",
            'KEY = "AKIAIOSFODNN7EXAMPLE"\n@app.get("/api/invoices/{x}")\n',
        )
        run(conn, tmp_path)
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM signals s JOIN files f ON f.id = s.file_id WHERE f.path = 'api.py'",
            )
            == 0
        )
        assert scalar(conn, "SELECT count(*) FROM relations WHERE type = 'shared_database'") == 0


def test_stc_toml_hosts_link_public_names(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    write(tmp_path, "web/package.json", '{"name": "web"}')
    write(tmp_path, "web/app.js", "fetch('https://pay.mycompany.com/charges');\n")
    write(tmp_path, "billing/pyproject.toml", '[project]\nname = "billing"\n')
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        assert list_relations(conn) == []  # a public name alone proves nothing
        write(
            tmp_path,
            "stc.toml",
            '[[application]]\npath = "billing"\nhosts = ["pay.mycompany.com"]\n',
        )
        run(conn, tmp_path)
        assert [(s, t, ty) for s, t, ty, *_x in list_relations(conn)] == [
            ("web", "billing", "http")
        ]


def test_files_with_credentials_are_excluded_and_leave_no_signals(
    temp_db_url: str, tmp_path: Path
) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    write(
        tmp_path, "web/src/db.js", "const u = 'postgresql://svc:hunter2pw@pg.corp:5432/ledger';\n"
    )
    with psycopg.connect(temp_db_url) as conn:
        run(conn, tmp_path)
        status = conn.execute("SELECT status FROM files WHERE path = 'src/db.js'").fetchone()
        assert status == ("secret",)  # hygiene excludes the whole file before signals exist
        dump = conn.execute("SELECT string_agg(detail || key, ' ') FROM signals").fetchone()
        assert dump is not None and "hunter2pw" not in dump[0] and "svc:" not in dump[0]
