"""Code units and the call graph of applications that are not written in COBOL (SPC-01)."""

from pathlib import Path

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.docs.facts import AppFacts, Endpoint, Loc, build_facts
from stc.docs.features import detect_features
from stc.docs.units import (
    SymbolRow,
    add_inline,
    background_entries,
    build_graph,
    handler_hint,
    handlers_by_hint,
    handlers_near,
    with_inline_handlers,
)
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending


def d(path: str, name: str, kind: str, start: int, end: int) -> SymbolRow:
    return SymbolRow(path, name, kind, True, start, end)


def c(path: str, name: str, line: int) -> SymbolRow:
    return SymbolRow(path, name, "call", False, line, line)


PHP = [
    d("app/InvoiceController.php", "InvoiceController", "class", 3, 30),
    d("app/InvoiceController.php", "index", "method", 5, 8),
    d("app/InvoiceController.php", "pay", "method", 10, 20),
    c("app/InvoiceController.php", "markPaid", 12),
    c("app/InvoiceController.php", "get", 6),
    d("app/InvoiceService.php", "InvoiceService", "class", 3, 30),
    d("app/InvoiceService.php", "markPaid", "method", 5, 15),
    d("app/InvoiceService.php", "audit", "method", 17, 25),
    c("app/InvoiceService.php", "audit", 8),
    d("app/Models/Invoice.php", "get", "method", 4, 6),  # a name too common to link across files
]


def test_units_are_qualified_by_their_class_and_calls_resolve_by_name() -> None:
    graph = build_graph(PHP)
    assert set(graph.units) >= {
        "app/InvoiceController.php::InvoiceController.pay",
        "app/InvoiceService.php::InvoiceService.markPaid",
    }
    targets = {(graph.label(x.caller), graph.label(x.target)) for x in graph.calls}
    assert ("InvoiceController.pay", "InvoiceService.markPaid") in targets
    assert ("InvoiceService.markPaid", "InvoiceService.audit") in targets
    # `get` is defined in another file but is far too common to be linked from here
    assert not any(graph.label(x.target) == "Invoice.get" for x in graph.calls)


def test_a_call_outside_any_function_belongs_to_no_unit() -> None:
    graph = build_graph([d("a.py", "f", "function", 5, 9), c("a.py", "f", 1)])
    assert graph.calls == []


def test_a_name_defined_in_many_other_files_is_not_guessed() -> None:
    rows = [d(f"m{i}.py", "render_row", "function", 1, 3) for i in range(5)]
    rows += [d("main.py", "main", "function", 1, 5), c("main.py", "render_row", 2)]
    assert build_graph(rows).calls == []


def test_the_same_file_is_preferred_and_tests_are_skipped() -> None:
    rows = [
        d("a.py", "helper", "function", 1, 3),
        d("b.py", "helper", "function", 1, 3),
        d("a.py", "run", "function", 5, 9),
        c("a.py", "helper", 6),
        d("tests/t.py", "check", "function", 1, 3),
    ]
    graph = build_graph(rows, skip=lambda path: path.startswith("tests/"))
    assert [x.target for x in graph.calls] == ["a.py::helper"]
    assert "tests/t.py::check" not in graph.units


def test_reach_follows_calls_and_terminates_on_cycles() -> None:
    rows = [
        d("a.py", "a", "function", 1, 2), d("a.py", "b", "function", 3, 4),
        c("a.py", "b", 1), c("a.py", "a", 3),
    ]  # fmt: skip
    graph = build_graph(rows)
    assert graph.reach(["a.py::a"]) == ["a.py::a", "a.py::b"]


def test_a_constructor_call_links_to_the_constructor_of_the_class() -> None:
    rows = [
        d("m.php", "Mailer", "class", 1, 9), d("m.php", "__construct", "method", 2, 4),
        d("c.php", "send", "function", 1, 5), c("c.php", "Mailer", 2),
    ]  # fmt: skip
    graph = build_graph(rows)
    assert [graph.label(x.target) for x in graph.calls] == ["Mailer.__construct"]


def test_route_registrations_name_their_handler() -> None:
    assert handler_hint("Route::get('/x', [InvoiceController::class, 'index']);") == (
        "InvoiceController",
        "index",
    )
    assert handler_hint("Route::post('/x', 'App\\Http\\InvoiceController@store');") == (
        "InvoiceController",
        "store",
    )
    assert handler_hint("router.get('/x', invoices.list);") == ("invoices", "list")
    assert handler_hint("app.get('/x', listInvoices);") == (None, "listInvoices")
    assert handler_hint("path('x/', views.invoice_list)") == (None, "invoice_list")
    assert handler_hint("app.get('/x', async (req, res) => {") is None
    assert handler_hint('@app.post("/orders")') is None


def test_handlers_come_from_the_hint_or_from_the_decorated_definition() -> None:
    graph = build_graph(PHP + [d("api.py", "post_order", "function", 11, 14)])
    hint = handlers_by_hint(graph, ("InvoiceController", "pay"))
    assert [u.label for u in hint] == ["InvoiceController.pay"]
    assert [u.label for u in handlers_by_hint(graph, ("invoice_controller", "pay"))] == [
        "InvoiceController.pay"
    ]
    assert [u.name for u in handlers_near(graph, "api.py", 10)] == ["post_order"]  # decorator
    assert handlers_near(graph, "api.py", 1) == []


def test_an_anonymous_callback_gets_a_unit_that_holds_its_calls() -> None:
    rows = [
        d("s.js", "save", "function", 30, 35),
        c("s.js", "save", 5),
        c("s.js", "save", 12),
    ]
    graph = build_graph(rows)
    made = with_inline_handlers(graph, "s.js", [3, 10])
    add_inline(graph, rows, made.values())
    assert made[3].end == 9 and made[10].end == 29
    assert {(x.caller, x.target) for x in graph.calls} == {
        ("s.js::@3", "s.js::save"),
        ("s.js::@10", "s.js::save"),
    }


def test_commands_and_jobs_are_entry_points_when_nothing_calls_them() -> None:
    rows = [
        d("app/Console/SyncCommand.php", "SyncCommand", "class", 1, 20),
        d("app/Console/SyncCommand.php", "handle", "method", 5, 12),
        d("app/Services/Billing.php", "Billing", "class", 1, 20),
        d("app/Services/Billing.php", "handle", "method", 5, 12),  # a service, not an entry
    ]
    graph = build_graph(rows)
    assert [u.label for u in background_entries(graph, set())] == ["SyncCommand.handle"]


def test_features_follow_the_handlers_through_the_call_graph() -> None:
    facts = AppFacts("shop", "shop", None)
    facts.graph = build_graph(PHP)
    pay = Endpoint("POST /invoices/{id}/pay", Loc("routes/web.php", 5))
    index = Endpoint("GET /invoices", Loc("routes/web.php", 3))
    facts.endpoints = [index, pay]
    facts.handlers = {
        str(index.loc): ["app/InvoiceController.php::InvoiceController.index"],
        str(pay.loc): ["app/InvoiceController.php::InvoiceController.pay"],
    }
    features, skipped = detect_features(facts, 10)
    assert skipped == [] and len(features) == 1
    feature = features[0]
    assert feature.title == "Endpoints of InvoiceController"
    assert feature.kind == "endpoints"
    assert "app/InvoiceService.php::InvoiceService.markPaid" in feature.units
    assert feature.files[0] == "routes/web.php"
    assert "app/InvoiceService.php" in feature.files


def test_endpoints_without_a_known_handler_are_still_grouped_by_file() -> None:
    facts = AppFacts("old", "old", None)
    facts.endpoints = [Endpoint("GET /a", Loc("r.py", 1)), Endpoint("GET /b", Loc("r.py", 3))]
    features, _ = detect_features(facts, 10)
    assert [f.title for f in features] == ["Endpoints in r.py"]
    assert features[0].units == []


# --- from the index ------------------------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture
def poly(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    _write(
        tmp_path,
        {
            "shop/composer.json": '{"name": "acme/shop", "require": {"php": "^8"}}',
            "shop/routes/web.php": (
                "<?php\nRoute::get('/invoices', [InvoiceController::class, 'index']);\n"
                "Route::post('/invoices/{id}/pay', [InvoiceController::class, 'pay']);\n"
            ),
            "shop/app/InvoiceController.php": (
                "<?php\nclass InvoiceController {\n"
                "    public function index() { return InvoiceService::open(); }\n"
                "    public function pay($id) { return InvoiceService::markPaid($id); }\n}\n"
            ),
            "shop/app/InvoiceService.php": (
                "<?php\nclass InvoiceService {\n"
                "    public static function open() { return []; }\n"
                "    public static function markPaid($id) { return audit($id); }\n}\n"
                "function audit($id) { return $id; }\n"
            ),
            "orders/pyproject.toml": '[project]\nname = "orders"\n',
            "orders/api.py": (
                "from service import create\n\n"
                '@app.post("/orders")\ndef post_order(body):\n    return register_order(body)\n'
            ),
            "orders/service.py": "def register_order(body):\n    return body\n",
            "orders/tests/test_api.py": "def test_x():\n    register_order(1)\n",
        },
    )
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as connection:
        sync_registry(connection, scan_root(tmp_path))
        chunk_pending(connection, tmp_path)
        extract_pending(connection, tmp_path)
        connection.execute("UPDATE applications SET indexed_version = 'v1'")
        yield connection


def _app_id(conn: psycopg.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM applications WHERE name = %s", (name,)).fetchone()
    assert row is not None
    return int(row[0])


def test_facts_link_routes_to_the_methods_that_handle_them(poly: psycopg.Connection) -> None:
    app_id = _app_id(poly, "shop")
    facts = build_facts(poly, app_id, "shop")
    handled = {
        e.detail: [facts.graph.label(k) for k in facts.handlers[str(e.loc)]]
        for e in facts.endpoints
    }
    assert handled == {
        "GET /invoices": ["InvoiceController.index"],
        "POST /invoices/{id}/pay": ["InvoiceController.pay"],
    }
    labels = {(facts.graph.label(c.caller), facts.graph.label(c.target)) for c in facts.graph.calls}
    assert ("InvoiceController.pay", "InvoiceService.markPaid") in labels
    assert ("InvoiceService.markPaid", "audit") in labels
    features, _ = detect_features(facts, 10)
    assert [f.title for f in features] == ["Endpoints of InvoiceController"]
    assert "app/InvoiceService.php" in features[0].files


def test_python_decorators_and_test_files(poly: psycopg.Connection) -> None:
    app_id = _app_id(poly, "orders")
    facts = build_facts(poly, app_id, "orders")
    assert not any(
        path.startswith("tests/") for path in (u.path for u in facts.graph.units.values())
    )
    features, _ = detect_features(facts, 10)
    assert [f.title for f in features] == ["Endpoints in api.py"]
    assert {facts.graph.label(k) for k in features[0].units} == {"post_order", "register_order"}
