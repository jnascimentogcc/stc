"""HTTP signals: a path parameter is not a call, and more frameworks define routes (CAT-07)."""

from pathlib import PurePosixPath as P

from stc.docs.units import SymbolRow, build_graph, handler_hint, handlers_by_hint
from stc.indexer.signals import extract_signals


def http(path: str, text: str, language: str, role: str) -> list[tuple[str, str | None, str]]:
    return sorted(
        (s.key, s.host, s.detail)
        for s in extract_signals(P(path), text, language)
        if s.kind == "http" and s.role == role
    )


def test_a_path_parameter_followed_by_a_path_is_not_a_call() -> None:
    php = "<?php\nRoute::post('/invoices/{id}/pay', [C::class, 'pay']);\n"
    assert http("routes/web.php", php, "php", "consumes") == []
    assert http("routes/web.php", php, "php", "provides") == [
        ("/invoices/{}/pay", None, "POST /invoices/{id}/pay")
    ]
    py = '@app.post("/orders/{order_id}/invoice")\ndef f(): ...\n'
    assert http("api.py", py, "python", "consumes") == []
    js = "router.get('/users/:id/orders', list);\napp.get(`/a/${id}/b`, h);\n"
    assert http("r.js", js, "javascript", "consumes") == []


def test_a_variable_that_opens_the_string_is_still_a_base_url() -> None:
    js = "fetch(`${BILLING_URL}/invoices`);\n"
    assert http("a.js", js, "javascript", "consumes") == [
        ("", "billing", "via BILLING_URL"),
        ("/invoices", "billing", "BILLING_URL/invoices"),
    ]
    py = 'requests.get(f"http://{host}/status/ready")\n'
    assert [k for k, _, _ in http("a.py", py, "python", "consumes")] == ["/status/ready"]


def test_flask_and_fastapi_prefixes_apply_to_their_routes() -> None:
    py = (
        'bp = Blueprint("inv", __name__, url_prefix="/api/v1")\n'
        'router = APIRouter(prefix="/orders")\n'
        '@bp.route("/invoices")\ndef a(): ...\n'
        '@router.get("/{id}")\ndef b(): ...\n'
        '@app.get("/health")\ndef c(): ...\n'
    )
    assert [k for k, _, _ in http("a.py", py, "python", "provides")] == [
        "/api/v1/invoices",
        "/health",
        "/orders/{}",
    ]


def test_drf_routers_register_a_resource() -> None:
    py = "router = DefaultRouter()\nrouter.register(r'invoices', InvoiceViewSet)\n"
    assert [k for k, _, _ in http("shop/urls.py", py, "python", "provides")] == [
        "/invoices",
        "/invoices/{}",
    ]


def test_laravel_slim_and_symfony_routes() -> None:
    laravel = (
        "<?php\nRoute::match(['get', 'post'], '/login', [A::class, 'x']);\n"
        "Route::apiResource('invoices', InvoiceController::class);\n"
    )
    assert [k for k, _, _ in http("routes/api.php", laravel, "php", "provides")] == [
        "/invoices",
        "/invoices/{}",
        "/login",
    ]
    slim = (
        "<?php\n$app->get('/invoices', InvoiceAction::class);\n$app->post('/invoices', C::class);\n"
    )
    assert [d for _, _, d in http("index.php", slim, "php", "provides")] == [
        "GET /invoices",
        "POST /invoices",
    ]
    symfony = (
        "<?php\n#[Route('/invoices')]\nclass InvoiceController {\n"
        "    #[Route('/{id}', methods: ['GET'])]\n    public function show() {}\n"
        "    #[Route('/new', name: 'new')]\n    public function create() {}\n}\n"
    )
    assert [k for k, _, _ in http("src/InvoiceController.php", symfony, "php", "provides")] == [
        "/invoices/new",
        "/invoices/{}",
    ]


def test_fastify_route_objects() -> None:
    js = "fastify.route({\n  method: 'GET', url: '/ping2',\n  handler: h,\n});\n"
    assert [d for _, _, d in http("s.js", js, "javascript", "provides")] == ["GET /ping2"]


def test_a_resource_route_is_handled_by_every_method_of_the_controller() -> None:
    assert handler_hint("Route::apiResource('invoices', InvoiceController::class);") == (
        None,
        "InvoiceController",
    )
    assert handler_hint("router.register(r'invoices', views.InvoiceViewSet)") == (
        None,
        "InvoiceViewSet",
    )
    rows = [
        SymbolRow("c.php", "InvoiceController", "class", True, 1, 20),
        SymbolRow("c.php", "index", "method", True, 2, 5),
        SymbolRow("c.php", "store", "method", True, 6, 9),
    ]
    graph = build_graph(rows)
    hint = handler_hint("Route::apiResource('invoices', InvoiceController::class);")
    assert hint is not None
    assert {u.name for u in handlers_by_hint(graph, hint)} == {"index", "store"}
