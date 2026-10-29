"""The MCP server: the query engine exposed as tools and resources (MCP-01 to MCP-07).

Everything here is a thin layer: each tool opens a database connection, calls one engine method
and shapes the result; no search logic lives in this module. All tools are read-only (MCP-05),
list results are paginated and capped by the engine (MCP-06), and engine errors become tool
errors with a stable code (MCP-07). Each tool runs in a worker thread because the engine is
synchronous."""

import hmac
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any

import anyio.to_thread
import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.types import ASGIApp, Receive, Scope, Send

from stc import __version__
from stc.config import Settings
from stc.query.engine import QueryEngine, build_query_embedder
from stc.query.models import QueryError, Source
from stc.query.search import QueryEmbedder

MAX_MAP_CHARS = 60_000

INSTRUCTIONS = """STC answers questions about a collection of undocumented applications from their \
source code. Every result carries evidence (application, file, lines) and the code version it was \
indexed from: cite it, and never state something the evidence does not show.

How to work:
- Cross-cutting questions ("which applications use X", "who calls Y"): start with get_global_map, \
list_applications and get_dependencies, then look at the code of the applications they point to.
- What an application is or does: get_catalog (by section) or search_catalogs. Catalogs are \
generated text; check surprising claims against the code.
- Where something is implemented: search_code (by meaning and words), find_symbol (definitions) \
and grep_code (exact text or regex); then read_file for the surrounding lines.
- If a result is empty or the notes say nothing matched, say that the information was not found \
in the indexed code instead of guessing. A catalog or file marked outdated/stale may no longer \
match the code.
- Files that may contain secrets are never returned."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

Apps = Annotated[
    list[str] | None,
    Field(description="Only these applications (exact names from list_applications)."),
]
Languages = Annotated[list[str] | None, Field(description="Only these languages, e.g. ['python'].")]
Paths = Annotated[
    list[str] | None,
    Field(description="Only paths starting with one of these prefixes, or matching a glob (* ?)."),
]
Limit = Annotated[int, Field(description="Results per page, 1 to 50.", ge=1, le=50)]
Offset = Annotated[int, Field(description="Results to skip, to read the next page.", ge=0)]


def to_data(value: Any) -> Any:
    """Dataclasses to plain JSON data; a `Source` also gets a ready-to-cite `ref`."""
    if isinstance(value, Source):
        return {**asdict(value), "ref": str(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {f: to_data(getattr(value, f)) for f in asdict(value)}
    if isinstance(value, list):
        return [to_data(v) for v in value]
    if isinstance(value, dict):
        return {k: to_data(v) for k, v in value.items()}
    return value


def build_server(
    settings: Settings,
    embedder_factory: Callable[[Settings], QueryEmbedder | None] = build_query_embedder,
    scope: list[str] | None = None,
) -> MCPServer:
    server = MCPServer(
        "stc",
        title="STC",
        description="Query undocumented applications from their source code.",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",  # INFO would print every HTTP request of the provider SDKs
    )
    embedder = embedder_factory(settings)

    async def run[T](call: Callable[[QueryEngine], T]) -> T:
        """Open a connection, run one engine call in a thread, map engine errors."""

        def work() -> T:
            if settings.db_url is None:
                raise QueryError("unavailable", "The index database is not configured.")
            with psycopg.connect(settings.db_url, autocommit=True) as conn:
                engine = QueryEngine(
                    conn,
                    root=settings.root,
                    catalogs=settings.catalogs,
                    embedder=embedder,
                    scope=scope,
                )
                return call(engine)

        try:
            return await anyio.to_thread.run_sync(work)
        except QueryError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc
        except psycopg.Error as exc:
            raise ToolError("unavailable: the index database could not be queried.") from exc

    @server.tool(annotations=_READ_ONLY)
    async def list_applications(
        name: Annotated[str | None, Field(description="Part of the application name.")] = None,
        technology: Annotated[
            str | None, Field(description="A language (e.g. 'python') or a library it uses.")
        ] = None,
        related_to: Annotated[
            str | None,
            Field(description="Only applications related to this one, in any direction."),
        ] = None,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """List the indexed applications with their main languages and whether a catalog exists.
        Use it to discover exact application names and to filter by technology or relation."""
        page = await run(
            lambda e: e.list_applications(
                name=name, technology=technology, related_to=related_to, limit=limit, offset=offset
            )
        )
        return to_data(page)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def get_catalog(
        application: Annotated[str, Field(description="Exact application name.")],
        sections: Annotated[
            list[str] | None,
            Field(
                description="Section ids: purpose, business, stack, modules, data_model, "
                "interfaces, integrations, configuration, flows. Default: all."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get an application's catalog (what it does, stack, modules, data model, interfaces,
        integrations, configuration, flows) with the evidence for each statement. `outdated` is
        true when the code was reindexed after the catalog was generated."""
        view = await run(lambda e: e.get_catalog(application, sections))
        return to_data(view)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def get_global_map() -> dict[str, Any]:
        """Get the global map: all applications, how they relate (calls, messaging, shared
        databases, libraries), shared resources, common technologies and isolated applications.
        Start here for questions that span several applications."""
        view = await run(lambda e: e.get_global_map())
        text = view.text
        truncated = len(text) > MAX_MAP_CHARS
        return {
            "text": text[:MAX_MAP_CHARS],
            "truncated": truncated,
            "from_file": view.from_file,
            **(
                {"note": "The map was cut; use get_dependencies for one application."}
                if truncated
                else {}
            ),
        }

    @server.tool(annotations=_READ_ONLY)
    async def get_dependencies(
        application: Annotated[str, Field(description="Exact application name.")],
        direction: Annotated[
            str,
            Field(description="depends_on (what it calls/uses), used_by (who uses it) or both."),
        ] = "both",
        max_depth: Annotated[int, Field(description="Hops to follow, 1 to 6.", ge=1, le=6)] = 3,
        types: Annotated[
            list[str] | None,
            Field(description="Only these relation types, e.g. ['http', 'messaging']."),
        ] = None,
    ) -> list[dict[str, Any]]:
        """Direct and transitive dependencies of an application, each with its type, confidence
        and the code that proves it. `via` is the path from the application asked about."""
        found = await run(
            lambda e: e.get_dependencies(
                application, direction=direction, max_depth=max_depth, types=types
            )
        )
        return to_data(found)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def search_catalogs(
        query: Annotated[str, Field(description="Words to look for in the catalogs.")],
        applications: Apps = None,
        limit: Limit = 10,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Search the catalogs (functional knowledge: purpose, business, data model...) by
        words. Separate from the code: use search_code for implementation details."""
        page = await run(
            lambda e: e.search_catalogs(
                query, applications=applications, limit=limit, offset=offset
            )
        )
        return to_data(page)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def search_code(
        query: Annotated[
            str, Field(description="A question or keywords, e.g. 'where are invoices validated'.")
        ],
        applications: Apps = None,
        languages: Languages = None,
        paths: Paths = None,
        limit: Limit = 10,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Search the code by meaning and by words together. Each hit is a code chunk with its
        location, snippet and `matched_by` (semantic, lexical, name); hits matched only by
        `semantic` are weak. Read the `notes` for limits such as lexical-only fallback."""
        page = await run(
            lambda e: e.search_code(
                query,
                applications=applications,
                languages=languages,
                paths=paths,
                limit=limit,
                offset=offset,
            )
        )
        return to_data(page)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def grep_code(
        pattern: Annotated[str, Field(description="Text to find, or a regex when regex=true.")],
        regex: Annotated[
            bool, Field(description="Treat the pattern as a regular expression.")
        ] = False,
        case_sensitive: bool = True,
        applications: Apps = None,
        languages: Languages = None,
        paths: Paths = None,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Find exact lines by literal text or regular expression in the indexed code, like
        grep. Use it for identifiers, URLs, config keys and error messages."""
        page = await run(
            lambda e: e.grep(
                pattern,
                regex=regex,
                case_sensitive=case_sensitive,
                applications=applications,
                languages=languages,
                paths=paths,
                limit=limit,
                offset=offset,
            )
        )
        return to_data(page)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def find_symbol(
        name: Annotated[str, Field(description="Function, class, method or variable name.")],
        kind: Annotated[str | None, Field(description="function, class, method...")] = None,
        exact: Annotated[bool, Field(description="false matches part of the name.")] = True,
        include_usages: bool = False,
        applications: Apps = None,
        languages: Languages = None,
        paths: Paths = None,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Find where a symbol is defined (and optionally used), with file and line range."""
        page = await run(
            lambda e: e.find_symbol(
                name,
                kind=kind,
                exact=exact,
                include_usages=include_usages,
                applications=applications,
                languages=languages,
                paths=paths,
                limit=limit,
                offset=offset,
            )
        )
        return to_data(page)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def read_file(
        application: Annotated[str, Field(description="Exact application name.")],
        path: Annotated[str, Field(description="File path relative to the application.")],
        start_line: Annotated[int, Field(description="First line (1-based).", ge=1)] = 1,
        end_line: Annotated[int | None, Field(description="Last line; default the end.")] = None,
    ) -> dict[str, Any]:
        """Read a line range of an indexed file (at most 400 lines per call; follow
        `next_start_line` for more). Secret and excluded files are refused. `stale` is true when
        the file changed after it was indexed."""
        piece = await run(
            lambda e: e.read_file(application, path, start_line=start_line, end_line=end_line)
        )
        return to_data(piece)  # type: ignore[no-any-return]

    @server.tool(annotations=_READ_ONLY)
    async def indexing_status(
        application: Annotated[str | None, Field(description="One application, or all.")] = None,
    ) -> dict[str, Any]:
        """What is indexed: files by status, chunks and embeddings, catalog state and whether it
        is outdated, relations. Use it to judge how far the answers can be trusted."""
        result = await run(lambda e: e.status(application))
        return to_data(result)  # type: ignore[no-any-return]

    @server.resource("stc://global-map", mime_type="text/markdown", title="Global map")
    async def global_map_resource() -> str:
        view = await run(lambda e: e.get_global_map())
        return view.text

    @server.resource(
        "stc://catalog/{+application}", mime_type="text/markdown", title="Application catalog"
    )
    async def catalog_resource(application: str) -> str:
        try:
            view = await run(lambda e: e.get_catalog(application))
        except ToolError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        parts = [f"# {application}"]
        if view.outdated:
            parts.append("> Outdated: the code was reindexed after this catalog was generated.")
        parts += [f"## {key}\n\n{body}" for key, body in view.sections.items()]
        return "\n\n".join(parts)

    if scope is not None:
        server.remove_tool("get_global_map")  # it spans every application
    return server


class BearerTokenMiddleware:
    """Rejects HTTP requests that do not carry `Authorization: Bearer <token>` (MCP over HTTP)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        supplied = headers.get(b"authorization", b"")
        if supplied.lower().startswith(b"bearer ") and hmac.compare_digest(
            supplied[7:].strip(), self._token
        ):
            await self.app(scope, receive, send)
            return
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_http_app(settings: Settings, server: MCPServer) -> ASGIApp:
    """The streamable-HTTP app behind the bearer token. The token is mandatory."""
    if not settings.mcp_token:
        raise ValueError("STC_MCP_TOKEN must be set to serve MCP over HTTP.")
    from mcp.server.transport_security import TransportSecuritySettings

    loopback = settings.mcp_host in ("127.0.0.1", "localhost", "::1")
    # Behind a proxy the Host header is the public name; the token is what protects the server.
    security = (
        None if loopback else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app = server.streamable_http_app(host=settings.mcp_host, transport_security=security)
    return BearerTokenMiddleware(app, settings.mcp_token)
