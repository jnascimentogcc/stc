"""The MCP server, driven in-process through a real MCP client against a real database."""

import asyncio
from pathlib import Path
from typing import Any

import psycopg
import pytest
from mcp import Client

from stc.catalog.store import generate_and_store, prepare
from stc.config import Settings
from stc.db.migrate import migrate
from stc.indexer.embed_store import embed_pending
from stc.mcp_server import BearerTokenMiddleware, build_http_app, build_server
from test_catalog import FakeLLM, index, make_root, reply, write
from test_query import KeywordEmbeddings, KeywordQueryEmbedder

TOOLS = {
    "list_applications",
    "get_catalog",
    "get_global_map",
    "get_dependencies",
    "search_catalogs",
    "search_code",
    "grep_code",
    "find_symbol",
    "read_file",
    "indexing_status",
}


@pytest.fixture
def server(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    write(root, "svc/billing.py", "def charge_customer(customer):\n    return 'payment ok'\n")
    catalogs = tmp_path / "catalogs"
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
        conn.execute("UPDATE applications SET indexed_version = 'v1'")
        asyncio.run(embed_pending(conn, KeywordEmbeddings()))
        llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
        prepared = prepare(
            conn, "svc", catalogs, provider="fake", model="fake-model", language="English"
        )
        asyncio.run(generate_and_store(conn, llm, prepared, language="English"))
    settings = Settings(
        _env_file=None, db_url=temp_db_url, root=root, catalogs=catalogs, mcp_token="secret-token"
    )
    return build_server(settings, embedder_factory=lambda _s: KeywordQueryEmbedder())


def call(server, name: str, arguments: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    async def go() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(go())


def test_every_capability_is_a_described_read_only_tool(server) -> None:  # type: ignore[no-untyped-def]
    async def go() -> Any:
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(go()).tools
    assert {t.name for t in tools} == TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.annotations is not None and tool.annotations.read_only_hint is True


def test_list_applications_and_dependencies_return_structured_data(server) -> None:  # type: ignore[no-untyped-def]
    apps = call(server, "list_applications", {"technology": "python"})
    assert not apps.is_error
    assert [a["name"] for a in apps.structured_content["items"]] == ["svc"]
    deps = call(server, "get_dependencies", {"application": "web", "direction": "depends_on"})
    (dep,) = deps.structured_content["result"]
    assert dep["application"] == "svc" and dep["evidence"]["ref"] == "web/client.js:1-1"


def test_search_code_hits_carry_a_citable_reference(server) -> None:  # type: ignore[no-untyped-def]
    result = call(server, "search_code", {"query": "charge customer payment", "limit": 3})
    hit = result.structured_content["items"][0]
    assert (
        hit["source"]["ref"].startswith("svc/billing.py:") and hit["source"]["code_version"] == "v1"
    )
    assert "semantic" in hit["matched_by"]
    exact = call(server, "grep_code", {"pattern": "payment ok"})
    assert exact.structured_content["items"][0]["source"]["ref"] == "svc/billing.py:2-2"
    symbol = call(server, "find_symbol", {"name": "charge_customer"})
    assert symbol.structured_content["items"][0]["kind"] == "function"


def test_catalog_and_read_file(server) -> None:  # type: ignore[no-untyped-def]
    catalog = call(server, "get_catalog", {"application": "svc", "sections": ["purpose"]})
    assert "It issues invoices." in catalog.structured_content["sections"]["purpose"]
    piece = call(server, "read_file", {"application": "svc", "path": "billing.py", "end_line": 1})
    assert piece.structured_content["text"] == "def charge_customer(customer):"
    found = call(server, "search_catalogs", {"query": "invoices"})
    assert found.structured_content["items"]
    status = call(server, "indexing_status", {})
    assert {a["name"] for a in status.structured_content["applications"]} == {"svc", "web"}


def test_global_map_tool(server) -> None:  # type: ignore[no-untyped-def]
    result = call(server, "get_global_map")
    assert "## Relationships" in result.structured_content["text"]
    assert result.structured_content["truncated"] is False


def test_errors_are_tool_errors_with_a_stable_code(server) -> None:  # type: ignore[no-untyped-def]
    secret = call(server, "read_file", {"application": "svc", "path": "credentials.json"})
    assert secret.is_error and "forbidden" in secret.content[0].text
    unknown = call(server, "get_catalog", {"application": "ghost"})
    assert unknown.is_error and "unknown_application" in unknown.content[0].text
    assert "svc" in unknown.content[0].text
    bad = call(server, "get_dependencies", {"application": "svc", "direction": "sideways"})
    assert bad.is_error and "invalid_argument" in bad.content[0].text


def test_resources_expose_the_map_and_each_catalog(server) -> None:  # type: ignore[no-untyped-def]
    async def go() -> Any:
        async with Client(server) as client:
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            map_ = await client.read_resource("stc://global-map")
            catalog = await client.read_resource("stc://catalog/svc")
            return resources, templates, map_, catalog

    resources, templates, map_, catalog = asyncio.run(go())
    assert [str(r.uri) for r in resources.resources] == ["stc://global-map"]
    assert [t.uri_template for t in templates.resource_templates] == [
        "stc://catalog/{+application}"
    ]
    assert "Global map" in map_.contents[0].text
    assert "It issues invoices." in catalog.contents[0].text


def test_search_degrades_to_lexical_when_the_query_cannot_be_embedded(
    temp_db_url: str, tmp_path: Path
) -> None:
    from stc.query.models import QueryError

    class Broken:
        provider = "fake"
        model = "keywords"

        def __call__(self, text: str) -> list[float]:
            raise QueryError("unavailable", "Embedding the query failed: no key.")

    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
    settings = Settings(_env_file=None, db_url=temp_db_url, root=root, catalogs=tmp_path / "c")
    broken = build_server(settings, embedder_factory=lambda _s: Broken())
    result = call(broken, "search_code", {"query": "invoices"})
    assert not result.is_error and result.structured_content["items"]
    assert any("lexical" in n for n in result.structured_content["notes"])


# --- HTTP ----------------------------------------------------------------------------------------


async def _request(app, headers: list[tuple[bytes, bytes]]):  # type: ignore[no-untyped-def]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app({"type": "http", "headers": headers, "path": "/mcp"}, receive, send)
    return sent[0]["status"]


def test_bearer_token_guards_http_requests() -> None:
    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = BearerTokenMiddleware(inner, "secret-token")

    async def go() -> list[int]:
        return [
            await _request(guarded, []),
            await _request(guarded, [(b"authorization", b"Bearer wrong")]),
            await _request(guarded, [(b"authorization", b"secret-token")]),
            await _request(guarded, [(b"authorization", b"Bearer secret-token")]),
        ]

    assert asyncio.run(go()) == [401, 401, 401, 200]


def test_http_needs_a_token(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_url="postgresql://x/y", catalogs=tmp_path)
    with pytest.raises(ValueError, match="STC_MCP_TOKEN"):
        build_http_app(settings, build_server(settings, embedder_factory=lambda _s: None))
