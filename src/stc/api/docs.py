"""Documentation endpoints (DOC-11 to DOC-13): read-only.

Nothing here writes: the pages are markdown files that `stc docs` generates and maintainers
correct by hand. A page is served only if the application's `_meta.json` lists it, so a request can
never read another file."""

from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from stc.config import Settings
from stc.docs.store import Meta, docs_path, read_meta


def _outdated(meta: Meta, indexed: str | None) -> bool:
    return bool(meta.code_version and indexed and meta.code_version != indexed)


def build_docs_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/docs")

    def find(application: str) -> tuple[Path, Meta]:
        base = settings.docs.resolve()
        directory = docs_path(settings.docs, application).resolve()
        if not directory.is_relative_to(base):
            raise HTTPException(404, "Documentation not found.")
        meta = read_meta(directory)
        if meta is None or meta.application != application:
            raise HTTPException(404, "This application has no documentation yet.")
        return directory, meta

    def indexed_versions() -> dict[str, str | None]:
        if settings.db_url is None:
            raise HTTPException(503, "The index database is not configured (STC_DB_URL).")
        try:
            with psycopg.connect(settings.db_url, autocommit=True) as conn:
                rows = conn.execute("SELECT name, indexed_version FROM applications ORDER BY name")
                return {str(n): v for n, v in rows.fetchall()}
        except psycopg.Error as exc:
            raise HTTPException(503, "The index database is not reachable.") from exc

    @router.get("")
    def list_documentation() -> list[dict[str, Any]]:
        """Every indexed application, with the state of its documentation."""
        out: list[dict[str, Any]] = []
        for name, indexed in indexed_versions().items():
            meta = read_meta(docs_path(settings.docs, name))
            if meta is None or meta.application != name:
                out.append({"name": name, "has_docs": False})
                continue
            out.append(
                {
                    "name": name,
                    "has_docs": True,
                    "status": meta.status,
                    "generated_at": meta.generated_at,
                    "code_version": meta.code_version,
                    "outdated": _outdated(meta, indexed),
                    "pages": len(meta.pages),
                    "model": f"{meta.provider}/{meta.model}",
                }
            )
        return out

    @router.get("/app")
    def application_documentation(application: str) -> dict[str, Any]:
        """The pages of one application, in navigation order, and how it was generated."""
        _, meta = find(application)
        indexed = indexed_versions().get(application)
        return {
            "name": meta.application,
            "status": meta.status,
            "generated_at": meta.generated_at,
            "code_version": meta.code_version,
            "outdated": _outdated(meta, indexed),
            "model": f"{meta.provider}/{meta.model}",
            "language": meta.language,
            "pages": [{"path": p.path, "title": p.title, "group": p.group} for p in meta.pages],
        }

    @router.get("/page")
    def page(application: str, path: str = Query(min_length=1, max_length=300)) -> dict[str, Any]:
        directory, meta = find(application)
        found = next((p for p in meta.pages if p.path == path), None)
        file = (directory / path).resolve()
        if found is None or not file.is_relative_to(directory) or not file.is_file():
            raise HTTPException(404, "Page not found.")
        return {
            "application": application,
            "path": found.path,
            "title": found.title,
            "group": found.group,
            "content": file.read_text(encoding="utf-8"),
        }

    @router.get("/export")
    def export(application: str) -> Response:
        """All pages as one markdown file, in navigation order (DOC-12)."""
        directory, meta = find(application)
        pages = [
            (directory / p.path).read_text(encoding="utf-8")
            for p in meta.pages
            if (directory / p.path).is_file()
        ]
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in application)
        return Response(
            "\n\n---\n\n".join(pages),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe}-documentation.md"'},
        )

    return router
