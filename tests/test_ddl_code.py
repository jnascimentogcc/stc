"""Columns read from Laravel migrations, Alembic and SQLAlchemy (docs, Phase 5)."""

from stc.catalog.context import _ENV_PATTERNS
from stc.docs.components import layer_of
from stc.docs.ddl import Table, _merged
from stc.docs.ddl_code import parse_code_tables

PHP = """<?php
Schema::create('orders', function (Blueprint $table) {
    $table->id();
    $table->foreignId('customer_id')->constrained();
    $table->string('code', 20)->unique();
    $table->decimal('total', 10, 2)->nullable();
    $table->timestamps();
});
"""

PY = """
class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    note = Column(String(50))


def upgrade():
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id")),
        sa.PrimaryKeyConstraint("id"),
    )
"""


def test_laravel_migration_columns() -> None:
    (table,) = parse_code_tables("database/migrations/1_orders.php", PHP)
    assert (table.name, table.start_line) == ("ORDERS", 2)
    columns = {c.name: c for c in table.columns}
    assert columns["id"].primary
    assert columns["customer_id"].references == "CUSTOMERS"
    assert columns["code"].type == "STRING(20)"
    assert columns["total"].type == "DECIMAL(10,2)" and not columns["total"].required
    assert {"created_at", "updated_at"} <= set(columns)


def test_sqlalchemy_model_and_alembic_table() -> None:
    tables = {t.name: t for t in parse_code_tables("app/models.py", PY)}
    invoices = {c.name: c for c in tables["INVOICES"].columns}
    assert invoices["id"].primary and invoices["order_id"].references == "ORDERS"
    assert invoices["order_id"].required
    payments = {c.name: c for c in tables["PAYMENTS"].columns}
    assert payments["id"].primary and payments["invoice_id"].references == "INVOICES"


def test_other_files_define_no_tables() -> None:
    assert parse_code_tables("a.js", PHP) == []


def test_merge_keeps_the_fuller_definition() -> None:
    small = Table("T", "a", 1, 1)
    full = Table("T", "b", 1, 1, columns=parse_code_tables("a.php", PHP)[0].columns)
    assert _merged([small, full])[0].path == "b"


def test_layer_is_the_two_nearest_folders() -> None:
    assert layer_of("app/Http/Controllers/A.php") == "Http/Controllers"
    assert layer_of("main.py") == "(root)"


def test_env_patterns_cover_laravel_and_php_globals() -> None:
    def found(line: str) -> set[str]:
        return {m.group(1) for p in _ENV_PATTERNS for m in p.finditer(line)}

    assert found("$k = env('STRIPE_KEY', 'x');") == {"STRIPE_KEY"}
    assert found("$h = $_ENV['DB_HOST'];") == {"DB_HOST"}
    assert found("$this->env('NOT_ONE')") == set()
