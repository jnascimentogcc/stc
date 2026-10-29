"""Signal extraction: what one file says about other applications. Synthetic code only."""

from pathlib import PurePosixPath

import pytest

from stc.indexer.signal_types import Signal
from stc.indexer.signals import extract_signals, is_generic_path, normalize_path


def signals(name: str, text: str, language: str) -> list[Signal]:
    return extract_signals(PurePosixPath(name), text, language)


def pick(found: list[Signal], kind: str, role: str) -> list[tuple[str, str | None, str]]:
    return [(s.key, s.host, s.detail) for s in found if s.kind == kind and s.role == role]


# --- paths -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/Orders/{id}/", "/orders/{}"),
        ("/orders/:id", "/orders/{}"),
        ("/orders/<int:pk>/items", "/orders/{}/items"),
        ("/orders/42?x=1", "/orders/{}"),
        ("/orders/${orderId}", "/orders/{}"),
        ("/", "/"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_generic_paths() -> None:
    assert is_generic_path("/health") and is_generic_path("/") and is_generic_path("/api")
    assert not is_generic_path("/orders") and not is_generic_path("/api/orders")


# --- endpoints a file provides -----------------------------------------------------------------


def test_python_routes() -> None:
    code = (
        '@app.get("/orders/{order_id}")\ndef a(): ...\n'
        '@router.post("/orders")\ndef b(): ...\n'
        '@bp.route("/invoices/<int:pk>", methods=["GET"])\ndef c(): ...\n'
    )
    found = pick(signals("api.py", code, "python"), "http", "provides")
    assert [(k, d) for k, _h, d in found] == [
        ("/orders/{}", "GET /orders/{order_id}"),
        ("/orders", "POST /orders"),
        ("/invoices/{}", "ANY /invoices/<int:pk>"),
    ]


def test_django_urls_only_in_urls_files() -> None:
    code = 'urlpatterns = [path("orders/<int:pk>/", views.o), re_path(r"^items/$", v)]\n'
    assert [k for k, *_ in pick(signals("urls.py", code, "python"), "http", "provides")] == [
        "/orders/{}",
        "/items",
    ]
    assert pick(signals("views.py", code, "python"), "http", "provides") == []


def test_express_routes_but_not_client_calls() -> None:
    code = (
        "app.get('/orders/:id', handler);\n"
        "router.post('/orders', create);\n"
        "app.route('/invoices').get(list);\n"
        "axios.get('/remote/thing', { params });\n"
    )
    keys = [k for k, *_ in pick(signals("srv.js", code, "javascript"), "http", "provides")]
    assert keys == ["/orders/{}", "/orders", "/invoices"]


def test_nestjs_controller_prefix() -> None:
    code = "@Controller('orders')\nexport class C {\n  @Get(':id')\n  one() {}\n  @Post()\n  add() {}\n}\n"
    keys = [k for k, *_ in pick(signals("c.ts", code, "typescript"), "http", "provides")]
    assert keys == ["/orders/{}", "/orders"]


def test_spring_class_prefix_and_methods() -> None:
    code = (
        '@RestController\n@RequestMapping("/api/orders")\npublic class OrderController {\n'
        '    @GetMapping("/{id}")\n    public Order one() {}\n'
        "    @PostMapping\n    public Order add() {}\n}\n"
    )
    keys = [k for k, *_ in pick(signals("O.java", code, "java"), "http", "provides")]
    assert keys == ["/api/orders/{}", "/api/orders"]


def test_dotnet_controller_route_and_minimal_api() -> None:
    code = (
        '[ApiController]\n[Route("api/[controller]")]\npublic class OrdersController : ControllerBase\n{\n'
        '    [HttpGet("{id}")]\n    public IActionResult One() {}\n'
        "    [HttpPost]\n    public IActionResult Add() {}\n}\n"
        'app.MapGet("/ping-pong/{x}", () => 1);\n'
    )
    keys = [k for k, *_ in pick(signals("O.cs", code, "csharp"), "http", "provides")]
    assert keys == ["/api/orders/{}", "/api/orders", "/ping-pong/{}"]


def test_go_routes() -> None:
    code = 'mux.HandleFunc("/orders", h)\nmux.HandleFunc("POST /items/{id}", h)\nr.GET("/users/:id", h)\n'
    keys = [k for k, *_ in pick(signals("m.go", code, "go"), "http", "provides")]
    assert keys == ["/orders", "/items/{}", "/users/{}"]


def test_rails_and_laravel_routes() -> None:
    assert [k for k, *_ in pick(signals("routes.rb", "get '/orders/:id', to: 'o#show'\nresources :invoices\n", "ruby"), "http", "provides")] == [
        "/orders/{}", "/invoices", "/invoices/{}",
    ]  # fmt: skip
    assert [
        k
        for k, *_ in pick(
            signals("web.php", "Route::get('/orders/{id}', fn);\n", "php"), "http", "provides"
        )
    ] == ["/orders/{}"]


def test_openapi_yaml_and_json() -> None:
    yaml_spec = "openapi: 3.0.0\npaths:\n  /orders:\n    get: {}\n  /orders/{id}:\n    get: {}\ncomponents: {}\n"
    assert [k for k, *_ in pick(signals("spec.yaml", yaml_spec, "yaml"), "http", "provides")] == [
        "/orders",
        "/orders/{}",
    ]
    json_spec = '{"openapi": "3.0.0", "paths": {"/orders": {}, "/items/{id}": {}}}'
    assert [k for k, *_ in pick(signals("spec.json", json_spec, "json"), "http", "provides")] == [
        "/orders",
        "/items/{}",
    ]
    assert signals("config.yaml", "paths:\n  /orders:\n    a: 1\n", "yaml") == []  # not an API spec


# --- http calls --------------------------------------------------------------------------------


def test_absolute_urls_keep_host_and_drop_credentials_and_noise() -> None:
    code = (
        'r = requests.get("http://billing-service:8080/api/invoices/42")\n'
        'u = "https://user:secret@orders.internal/v1/orders?x=1"\n'
        'x = "http://localhost:3000/local/thing"\n'
        'ns = "http://www.w3.org/2001/XMLSchema"\n'
    )
    found = pick(signals("c.py", code, "python"), "http", "consumes")
    assert found == [
        ("/api/invoices/{}", "billing-service", "http://billing-service/api/invoices/42"),
        ("/v1/orders", "orders.internal", "https://orders.internal/v1/orders"),
        ("/local/thing", None, "http://localhost/local/thing"),
    ]
    assert all("secret" not in d for *_, d in found)


def test_relative_client_calls_and_interpolated_bases() -> None:
    code = (
        "fetch('/api/orders/' + id);\n"
        "axios.post(`/api/invoices`, body);\n"
        "const r = await http.get(`${BILLING_URL}/invoices/${id}`);\n"
        'resp = requests.get(f"{BILLING_API_URL}/charges")\n'
    )
    found = pick(signals("c.ts", code, "typescript"), "http", "consumes")
    keys = {(k, h) for k, h, _ in found}
    assert ("/api/orders", None) in keys
    assert ("/api/invoices", None) in keys
    assert ("/invoices/{}", "billing") in keys
    assert ("/charges", "billing-api") in keys


def test_env_var_names_hint_at_a_service() -> None:
    code = "BILLING_SERVICE_URL = os.environ['BILLING_SERVICE_URL']\nDATABASE_HOST = 'x'\n"
    hosts = {h for _k, h, _d in pick(signals("settings.py", code, "python"), "http", "consumes")}
    assert "billing-service" in hosts


# --- messaging ---------------------------------------------------------------------------------


def test_messaging_publishers_and_subscribers() -> None:
    code = (
        'kafkaTemplate.send("orders.created", payload);\n'
        'producer.send({ topic: "invoices.issued", messages });\n'
        '@KafkaListener(topics = "payments.settled")\n'
        '@RabbitListener(queues = "email-queue")\n'
        'channel.basic_publish(exchange="", routing_key="email-queue", body=x)\n'
        'socket.send("hello")\n'
    )
    found = signals("m.java", code, "java")
    assert sorted(k for k, *_ in pick(found, "messaging", "publishes")) == [
        "email-queue", "invoices.issued", "orders.created",
    ]  # fmt: skip
    assert sorted(k for k, *_ in pick(found, "messaging", "subscribes")) == [
        "email-queue", "payments.settled",
    ]  # fmt: skip


# --- databases ---------------------------------------------------------------------------------


def test_database_urls_are_reduced_to_engine_host_port_db() -> None:
    code = (
        'url = "jdbc:postgresql://pg.prod.corp:5432/billing?ssl=true"\n'
        'c = "postgres://app:s3cret@pg.prod.corp/billing"\n'
        'm = "mongodb+srv://u:p@cluster.example.net/orders"\n'
        'r = "redis://cache.corp:6379/0"\n'
        'a = "Server=sql.corp;Database=Ledger;User Id=sa;Password=pw"\n'
        'skip = "postgres://${USER}:${PASS}@${HOST}/db"\n'
    )
    keys = [k for k, *_ in pick(signals("db.py", code, "python"), "database", "uses")]
    assert keys == [
        "postgresql://pg.prod.corp:5432/billing",
        "postgresql://pg.prod.corp:5432/billing",
        "mongodb://cluster.example.net:27017/orders",
        "redis://cache.corp:6379/0",
        "sqlserver://sql.corp:1433/ledger",
    ]
    assert all(
        "s3cret" not in d and "pw" not in d
        for *_, d in pick(signals("db.py", code, "python"), "database", "uses")
    )


# --- manifests ---------------------------------------------------------------------------------


def lib(name: str, text: str) -> tuple[set[str], set[str]]:
    found = extract_signals(PurePosixPath(name), text, None)
    return (
        {s.key for s in found if s.role == "provides"},
        {s.key for s in found if s.role == "consumes"},
    )


def test_manifest_libraries() -> None:
    assert lib("package.json", '{"name": "@acme/billing-client", "dependencies": {"react": "1", "@acme/core": "1"}, "devDependencies": {"vitest": "1"}}') == (
        {"@acme/billing-client"}, {"react", "@acme/core", "vitest"},
    )  # fmt: skip
    assert lib("pyproject.toml", '[project]\nname = "Acme_Billing"\ndependencies = ["acme-core>=1", "fastapi[all]"]\n') == (
        {"acme-billing"}, {"acme-core", "fastapi"},
    )  # fmt: skip
    assert lib("requirements-dev.txt", "# c\nacme_core==1.0\n-r other.txt\npytest>=8\n")[1] == {
        "acme-core",
        "pytest",
    }
    pom = '<project xmlns="http://maven.apache.org/POM/4.0.0"><parent><artifactId>acme-parent</artifactId></parent><artifactId>billing</artifactId><dependencies><dependency><artifactId>acme-core</artifactId></dependency></dependencies></project>'
    assert lib("pom.xml", pom) == ({"billing"}, {"acme-parent", "acme-core"})
    csproj = '<Project><PropertyGroup><PackageId>Acme.Billing</PackageId></PropertyGroup><ItemGroup><PackageReference Include="Acme.Core" /><ProjectReference Include="..\\Shared\\Acme.Shared.csproj" /></ItemGroup></Project>'
    assert lib("Billing.csproj", csproj) == (
        {"billing", "acme.billing"},
        {"acme.core", "acme.shared"},
    )
    assert lib("go.mod", "module github.com/acme/billing\n\nrequire (\n\tgithub.com/acme/core v1.2.3\n\tgolang.org/x/text v0.3.0\n)\n") == (
        {"github.com/acme/billing"}, {"github.com/acme/core", "golang.org/x/text"},
    )  # fmt: skip
    assert lib("Cargo.toml", '[package]\nname = "billing"\n[dependencies]\nacme-core = "1"\n') == (
        {"billing"},
        {"acme-core"},
    )
    assert lib(
        "composer.json",
        '{"name": "acme/billing", "require": {"php": "^8", "ext-json": "*", "acme/core": "1"}}',
    ) == ({"acme/billing"}, {"acme/core"})


def test_broken_or_hostile_manifests_yield_nothing() -> None:
    assert lib("package.json", "{not json") == (set(), set())
    assert lib("pom.xml", "<project><unclosed>") == (set(), set())
    bomb = '<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><project><artifactId>&b;</artifactId></project>'
    assert lib("pom.xml", bomb) == (set(), set())  # entity expansion is refused


def test_irrelevant_files_have_no_signals() -> None:
    assert signals("README.md", "see http://billing/api/x", "markdown") == []
    assert signals("a.py", "x = 1\n", "python") == []


def test_fastify_routes_with_a_generic_or_the_path_on_the_next_line_are_found() -> None:
    from pathlib import PurePosixPath

    from stc.indexer.signals import extract_signals

    source = """import Fastify from "fastify";
export function build(app: FastifyInstance) {
  app.get("/health", async () => ({ ok: true }));
  app.post<{ Body: BookBody }>("/appointments", { schema }, async (req) => ok(req));
  app.post<{ Params: { id: string }; Body: { start: string } }>(
    "/appointments/:id/reschedule",
    { schema },
    async (req) => ok(req),
  );
  app.patch<{ Params: { id: string } }>("/appointments/:id", handler);
  return app;
}
"""
    found = [
        (s.detail, s.start_line)
        for s in extract_signals(PurePosixPath("src/api/server.ts"), source, "typescript")
        if s.role == "provides"
    ]
    assert found == [
        ("GET /health", 3),
        ("POST /appointments", 4),
        ("POST /appointments/:id/reschedule", 5),  # the line of the call, not of the path
        ("PATCH /appointments/:id", 10),
    ]
