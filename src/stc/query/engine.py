"""The query engine: the single place where STC answers questions about the indexed code.

The chat API and the MCP server are thin layers over this class; neither implements search of
its own. Every method returns results with evidence (application, file, lines, code version)
and says explicitly when nothing was found (QRY-09, QRY-10)."""

import asyncio
from pathlib import Path

import psycopg

from stc.config import Settings
from stc.providers import ProviderError, build_embeddings_client, closing
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
from stc.sources import Roots


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
            return asyncio.run(closing(client.embed([text]), client))[0]
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
        root: Path | Roots | None,
        catalogs: Path,
        embedder: QueryEmbedder | None = None,
        scope: list[str] | None = None,
    ) -> None:
        self.conn = conn
        self.root = root
        self.catalogs = catalogs
        self.embedder = embedder
        # A conversation can be restricted to some applications (CHT-06): nothing outside them
        # can be listed, searched, read or described through this engine.
        self.scope = scope

    def _applications(self, applications: list[str] | None) -> list[str] | None:
        if self.scope is None:
            return applications
        if not applications:
            return list(self.scope)
        for name in applications:
            self._check(name)
        return applications

    def _check(self, application: str) -> None:
        if self.scope is not None and application not in self.scope:
            raise QueryError(
                "forbidden",
                f"{application!r} is outside the scope of this conversation "
                f"({', '.join(self.scope) or 'no applications'}).",
            )

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
            only=self.scope,
            limit=limit,
            offset=offset,
        )

    def get_catalog(self, application: str, sections: list[str] | None = None) -> CatalogView:
        self._check(application)
        return browse.get_catalog(self.conn, self.catalogs, application, sections)

    def get_global_map(self) -> GlobalMapView:
        if self.scope is not None:
            raise QueryError(
                "forbidden",
                "The global map covers every application and is not available in a scoped "
                "conversation; use get_dependencies for the applications in scope.",
            )
        return browse.get_global_map(self.conn, self.catalogs)

    def get_dependencies(
        self,
        application: str,
        *,
        direction: str = "both",
        max_depth: int = 3,
        types: list[str] | None = None,
    ) -> list[Dependency]:
        self._check(application)
        return browse.get_dependencies(
            self.conn, application, direction=direction, max_depth=max_depth, types=types
        )

    def status(self, application: str | None = None) -> IndexStatus:
        if application:
            self._check(application)
        return browse.index_status(self.conn, application, self.scope)

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
            applications=self._applications(applications),
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
            self.conn,
            self.embedder,
            query,
            applications=self._applications(applications),
            limit=limit,
            offset=offset,
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
            applications=self._applications(applications),
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
            applications=self._applications(applications),
            languages=languages,
            paths=paths,
            limit=limit,
            offset=offset,
        )

    # --- files ------------------------------------------------------------------------------

    def read_file(
        self, application: str, path: str, *, start_line: int = 1, end_line: int | None = None
    ) -> FileSlice:
        self._check(application)
        return browse.read_file(
            self.conn, self.root, application, path, start_line=start_line, end_line=end_line
        )


__all__ = ["QueryEngine", "build_query_embedder"]
