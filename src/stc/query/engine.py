"""The query engine: the single place where STC answers questions about the indexed code.

The chat API and the MCP server are thin layers over this class; neither implements search of
its own. Every method returns results with evidence (application, file, lines, code version)
and says explicitly when nothing was found (QRY-09, QRY-10)."""

import asyncio
from pathlib import Path

import psycopg

from stc.config import Settings
from stc.providers import ProviderError, build_embeddings_client
from stc.query import browse, search
from stc.query.models import (
    AppSummary,
    CatalogHit,
    CatalogView,
    CodeHit,
    Dependency,
    FileSlice,
    GlobalMapView,
    IndexStatus,
    LineHit,
    Page,
    QueryError,
    SymbolHit,
)
from stc.query.search import QueryEmbedder


class OpenAIQueryEmbedder:
    """Embeds a query with a fresh client per call: the SDK keeps pooled connections that belong
    to the event loop that created them, and each call here runs in its own loop."""

    def __init__(self, settings: Settings) -> None:
        probe = build_embeddings_client(settings)
        self._settings = settings
        self.provider = probe.provider
        self.model = probe.model

    def __call__(self, text: str) -> list[float]:
        client = build_embeddings_client(self._settings)
        try:
            return asyncio.run(client.embed([text]))[0]
        except ProviderError as exc:
            raise QueryError("unavailable", f"Embedding the query failed: {exc}") from exc


def build_query_embedder(settings: Settings) -> QueryEmbedder | None:
    """None when embeddings are not configured, so search degrades to lexical."""
    try:
        return OpenAIQueryEmbedder(settings)
    except ProviderError:
        return None


class QueryEngine:
    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        root: Path | None,
        catalogs: Path,
        embedder: QueryEmbedder | None = None,
    ) -> None:
        self.conn = conn
        self.root = root
        self.catalogs = catalogs
        self.embedder = embedder

    # --- applications and knowledge -------------------------------------------------------

    def list_applications(
        self,
        *,
        name: str | None = None,
        technology: str | None = None,
        related_to: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Page[AppSummary]:
        return browse.list_applications(
            self.conn,
            name=name,
            technology=technology,
            related_to=related_to,
            limit=limit,
            offset=offset,
        )

    def get_catalog(self, application: str, sections: list[str] | None = None) -> CatalogView:
        return browse.get_catalog(self.conn, self.catalogs, application, sections)

    def get_global_map(self) -> GlobalMapView:
        return browse.get_global_map(self.conn, self.catalogs)

    def get_dependencies(
        self,
        application: str,
        *,
        direction: str = "both",
        max_depth: int = 3,
        types: list[str] | None = None,
    ) -> list[Dependency]:
        return browse.get_dependencies(
            self.conn, application, direction=direction, max_depth=max_depth, types=types
        )

    def status(self, application: str | None = None) -> IndexStatus:
        return browse.index_status(self.conn, application)

    # --- search -----------------------------------------------------------------------------

    def search_code(
        self,
        query: str,
        *,
        applications: list[str] | None = None,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Page[CodeHit]:
        return search.search_code(
            self.conn,
            self.embedder,
            query,
            applications=applications,
            languages=languages,
            paths=paths,
            limit=limit,
            offset=offset,
        )

    def search_catalogs(
        self,
        query: str,
        *,
        applications: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Page[CatalogHit]:
        return search.search_catalogs(
            self.conn, query, applications=applications, limit=limit, offset=offset
        )

    def grep(
        self,
        pattern: str,
        *,
        regex: bool = False,
        case_sensitive: bool = True,
        applications: list[str] | None = None,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Page[LineHit]:
        return search.grep(
            self.conn,
            pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            applications=applications,
            languages=languages,
            paths=paths,
            limit=limit,
            offset=offset,
        )

    def find_symbol(
        self,
        name: str,
        *,
        kind: str | None = None,
        exact: bool = True,
        include_usages: bool = False,
        applications: list[str] | None = None,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Page[SymbolHit]:
        return search.find_symbol(
            self.conn,
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

    # --- files ------------------------------------------------------------------------------

    def read_file(
        self, application: str, path: str, *, start_line: int = 1, end_line: int | None = None
    ) -> FileSlice:
        return browse.read_file(
            self.conn, self.root, application, path, start_line=start_line, end_line=end_line
        )


__all__ = ["QueryEngine", "build_query_embedder"]
