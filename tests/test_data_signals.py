"""Tables read and defined by code that is not COBOL: SQL strings, query builders, models and
migrations (CAT-07), and the tables the functions of a feature read and write (SPC-03)."""

from pathlib import Path
from pathlib import PurePosixPath as P

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.docs.facts import build_facts
from stc.docs.features import detect_features
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.data_signals import data_signals, plural, snake
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending
from stc.indexer.signal_types import Signal
from stc.indexer.signals import extract_signals
from stc.spec.plan import dependencies, feature_io, migration_order


def found(path: str, text: str, language: str) -> list[tuple[str, str, str]]:
    return sorted((s.role, s.key, s.detail) for s in data_signals(P(path), text, language))


def uses(path: str, text: str, language: str) -> set[tuple[str, str]]:
    return {(k, d.split()[1].upper()) for r, k, d in found(path, text, language) if r == "consumes"}


def test_names_follow_the_framework_conventions() -> None:
    assert snake("InvoiceLine") == "invoice_line"
    assert plural("invoice_line") == "invoice_lines"
    assert plural("category") == "categories"
    assert plural("address") == "addresses"


def test_sql_in_php_strings_reads_and_writes() -> None:
    text = (
        "<?php\n"
        "DB::update(\"UPDATE invoices SET status = 'paid' WHERE id = ?\", [$id]);\n"
        'DB::insert("INSERT INTO payments (invoice_id) VALUES (?)", [1]);\n'
        "$q = <<<SQL\nSELECT i.id, c.name FROM invoices i JOIN customers c ON c.id = i.c\nSQL;\n"
    )
    signals = {(s.key, s.detail.split()[1].upper()) for s in data_signals(P("a.php"), text, "php")}
    assert signals == {
        ("INVOICES", "WRITES"),
        ("PAYMENTS", "WRITES"),
        ("INVOICES", "READS"),
        ("CUSTOMERS", "READS"),
    }


def test_prose_and_comments_are_not_sql() -> None:
    text = (
        "// SELECT id FROM secrets\n"
        'label = "Select an option from the list"\n'
        "msg = 'Update the settings and then delete from the cart'\n"
    )
    assert found("a.js", text, "javascript") == []


def test_python_and_js_strings_and_query_builders() -> None:
    assert uses("a.py", 'cur.execute("DELETE FROM carts WHERE id = %s")', "python") == {
        ("CARTS", "WRITES")
    }
    assert uses("a.py", 'q = """\n  SELECT * FROM orders o\n  WHERE o.id = 1\n"""', "python") == {
        ("ORDERS", "READS")
    }
    js = "pool.query('SELECT id, name FROM customers ORDER BY name');\nknex('orders').insert({a: 1});"
    assert uses("a.js", js, "javascript") == {("CUSTOMERS", "READS"), ("ORDERS", "WRITES")}
    assert uses("a.php", "DB::table('audit')->insert(['x' => 1]);", "php") == {("AUDIT", "WRITES")}
    assert uses("a.php", "DB::table('customers')->where('a', 1)->get();", "php") == {
        ("CUSTOMERS", "READS")
    }


def test_interpolated_table_names_give_no_signal() -> None:
    assert found("a.py", 'cur.execute(f"SELECT * FROM {table} WHERE id = 1")', "python") == []
    assert found("a.php", 'DB::select("SELECT * FROM $table")', "php") == []


def test_models_map_their_table() -> None:
    php = (
        "<?php\nclass Invoice extends Model { protected $table = 'billing_invoices'; }\n"
        "class InvoiceLine extends Model {}\n"
    )
    assert found("app/Invoice.php", php, "php") == [
        ("provides", "BILLING_INVOICES", "model Invoice"),
        ("provides", "INVOICE_LINES", "model InvoiceLine"),
    ]
    sqlalchemy = 'class Order(Base):\n    __tablename__ = "orders"\n'
    assert found("m.py", sqlalchemy, "python") == [("provides", "ORDERS", "model Order")]
    django = "from django.db import models\nclass Item(models.Model):\n    pass\n"
    assert found("shop/models.py", django, "python") == [("provides", "SHOP_ITEM", "model Item")]
    entity = "@Entity('invoice')\nexport class Invoice {}\n"
    assert found("a.ts", entity, "typescript") == [("provides", "INVOICE", "model Invoice")]
    jpa = '@Entity\n@Table(name = "invoices")\npublic class Invoice {}\n'
    assert found("Invoice.java", jpa, "java") == [("provides", "INVOICES", "model Invoice")]
    assert found("Order.java", "@Entity\npublic class OrderLine {}\n", "java") == [
        ("provides", "ORDER_LINE", "model OrderLine")
    ]


def test_migrations_provide_the_tables_they_create() -> None:
    assert found("m.php", "<?php Schema::create('invoices', function () {});", "php") == [
        ("provides", "INVOICES", "migration creates invoices")
    ]
    assert found("a.py", "op.create_table('orders', sa.Column('id'))", "python") == [
        ("provides", "ORDERS", "migration creates orders")
    ]
    assert found("a.js", "knex.schema.createTable('users', t => {})", "javascript") == [
        ("provides", "USERS", "migration creates users")
    ]
    assert found("shop/migrations/0001.py", "migrations.CreateModel(name='Item')", "python") == [
        ("provides", "SHOP_ITEM", "migration creates shop_item")
    ]


def test_the_extractor_gives_the_signals_with_the_others() -> None:
    signals = extract_signals(P("app/Pay.php"), "<?php DB::table('payments')->insert([]);", "php")
    assert Signal("table", "consumes", "PAYMENTS", None, "SQL WRITES payments", 1, 1) in signals
    assert (
        extract_signals(P("tests/PayTest.php"), "<?php DB::table('payments')->get();", "php") == []
    )


# --- from the index ------------------------------------------------------------------------------


@pytest.fixture
def shop(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    files = {
        "shop/composer.json": '{"name": "acme/shop", "require": {"php": "^8"}}',
        "shop/routes/web.php": (
            "<?php\nRoute::post('/invoices', [InvoiceController::class, 'store']);\n"
            "Route::get('/reports', [ReportController::class, 'index']);\n"
        ),
        "shop/app/Models/Invoice.php": "<?php\nclass Invoice extends Model {}\n",
        "shop/database/migrations/create_invoices.php": (
            "<?php\nSchema::create('invoices', function ($t) {});\n"
        ),
        "shop/app/InvoiceController.php": (
            "<?php\nclass InvoiceController {\n"
            "    public function store($r) {\n"
            "        return Invoice::create($r->all());\n    }\n}\n"
        ),
        "shop/app/ReportController.php": (
            "<?php\nclass ReportController {\n"
            "    public function index() {\n"
            "        return DB::select(\"SELECT total FROM invoices WHERE status = 'open'\");\n"
            "    }\n}\n"
        ),
        "ui/package.json": '{"name": "ui", "dependencies": {"express": "^4"}}',
        "ui/src/server.js": (
            "const app = require('express')();\n"
            "app.get('/customers', async (req, res) => {\n"
            "  res.json(await pool.query('SELECT id, name FROM customers'));\n"
            "});\n"
        ),
    }
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as connection:
        sync_registry(connection, scan_root(tmp_path))
        chunk_pending(connection, tmp_path)
        extract_pending(connection, tmp_path)
        connection.execute("UPDATE applications SET indexed_version = 'v1'")
        yield connection


def test_functions_use_the_tables_their_models_and_sql_name(shop: psycopg.Connection) -> None:
    row = shop.execute("SELECT id FROM applications WHERE name = 'shop'").fetchone()
    assert row is not None
    facts = build_facts(shop, int(row[0]), "shop")
    by_function = {(facts.graph.label(u.owner.name), u.table, u.action) for u in facts.tables}
    assert by_function == {
        ("InvoiceController.store", "INVOICES", "WRITES"),
        ("ReportController.index", "INVOICES", "READS"),
    }
    assert sorted(facts.table_files["INVOICES"]) == [
        "app/Models/Invoice.php",
        "database/migrations/create_invoices.php",
    ]

    features, _ = detect_features(facts, 10)
    by_title = {f.title: f for f in features}
    writer = by_title["Endpoints of InvoiceController"]
    reader = by_title["Endpoints of ReportController"]
    assert feature_io(facts, writer) == (set(), {"table INVOICES"})
    assert feature_io(facts, reader) == ({"table INVOICES"}, set())
    deps = dependencies(facts, features)
    assert deps[reader.slug] == [writer.slug]  # the report reads what the invoices write
    ordered, cyclic = migration_order(features, deps)
    assert [f.slug for f in ordered] == [writer.slug, reader.slug] and not cyclic


def test_an_anonymous_route_callback_owns_the_tables_its_sql_names(
    shop: psycopg.Connection,
) -> None:
    row = shop.execute("SELECT id FROM applications WHERE name = 'ui'").fetchone()
    assert row is not None
    facts = build_facts(shop, int(row[0]), "ui")  # no named function in the whole application
    assert [(facts.graph.label(u.owner.name), u.table, u.action) for u in facts.tables] == [
        ("inline handler at line 2", "CUSTOMERS", "READS")
    ]
    (feature,) = detect_features(facts, 10)[0]
    assert feature_io(facts, feature) == ({"table CUSTOMERS"}, set())
