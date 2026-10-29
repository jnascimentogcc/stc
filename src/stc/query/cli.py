"""`stc query ...`: the query engine from the command line, for checking what the chat and the MCP
server will see. Text output by default, `--json` for the structured result."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any

import psycopg
import typer

from stc.config import get_settings
from stc.query.engine import QueryEngine, build_query_embedder
from stc.query.models import Page, QueryError

query_app = typer.Typer(no_args_is_help=True, help="Ask the query engine (read-only).")

JsonOption = Annotated[bool, typer.Option("--json", help="Print the structured result.")]
AppsOption = Annotated[
    list[str] | None, typer.Option("--app", help="Only this application (repeatable).")
]
LangOption = Annotated[
    list[str] | None, typer.Option("--language", help="Only this language (repeatable).")
]
PathOption = Annotated[
    list[str] | None, typer.Option("--path", help="Path prefix or glob (repeatable).")
]
LimitOption = Annotated[int, typer.Option("--limit", help="Results per page (max 50).")]
OffsetOption = Annotated[int, typer.Option("--offset", help="Results to skip.")]


@contextmanager
def _engine(*, semantic: bool = False) -> Iterator[QueryEngine]:
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            embedder = build_query_embedder(settings) if semantic else None
            yield QueryEngine(
                conn, root=settings.root, catalogs=settings.catalogs, embedder=embedder
            )
    except QueryError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(
            code=2 if exc.code in ("invalid_argument", "unknown_application") else 1
        ) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _json(value: Any) -> str:
    def convert(obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return {k: convert(v) for k, v in asdict(obj).items()}
        return obj

    return json.dumps(convert(value), indent=2, ensure_ascii=False, default=str)


def _emit(value: Any, as_json: bool, render: Any) -> None:
    if as_json:
        typer.echo(_json(value))
    else:
        render(value)


def _page_footer(page: Page[Any]) -> None:
    for note in page.notes:
        typer.echo(f"note: {note}", err=True)
    if page.has_more:
        typer.echo(f"more results: use --offset {page.offset + page.limit}", err=True)


@query_app.command("apps")
def apps(
    name: Annotated[str | None, typer.Option(help="Part of the name.")] = None,
    technology: Annotated[str | None, typer.Option(help="A language or library.")] = None,
    related_to: Annotated[str | None, typer.Option(help="Related to this application.")] = None,
    limit: LimitOption = 20,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """List applications (QRY-01)."""
    with _engine() as eng:
        page = eng.list_applications(
            name=name, technology=technology, related_to=related_to, limit=limit, offset=offset
        )

    def render(p: Page[Any]) -> None:
        for a in p.items:
            langs = ", ".join(f"{k}:{v}" for k, v in a.languages.items())
            catalog = "catalog" if a.has_catalog else "no catalog"
            typer.echo(f"{a.name}\t{a.files} files\t{langs}\t{catalog}\t{a.code_version or '-'}")
        _page_footer(p)

    _emit(page, as_json, render)


@query_app.command("catalog")
def catalog(
    application: str,
    section: Annotated[list[str] | None, typer.Option("--section", help="Section id.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Show an application's catalog, whole or by section (QRY-02)."""
    with _engine() as eng:
        view = eng.get_catalog(application, section)

    def render(v: Any) -> None:
        if v.outdated:
            typer.echo(
                "warning: the code was reindexed after this catalog was generated.", err=True
            )
        for key, body in v.sections.items():
            typer.echo(f"## {key}\n\n{body}\n")

    _emit(view, as_json, render)


@query_app.command("map")
def global_map(as_json: JsonOption = False) -> None:
    """Show the global map (QRY-03)."""
    with _engine() as eng:
        view = eng.get_global_map()
    _emit(view, as_json, lambda v: typer.echo(v.text))


@query_app.command("deps")
def deps(
    application: str,
    direction: Annotated[str, typer.Option(help="depends_on | used_by | both")] = "both",
    depth: Annotated[int, typer.Option(help="How many hops (1-6).")] = 3,
    as_json: JsonOption = False,
) -> None:
    """Direct and transitive dependencies of an application (QRY-03)."""
    with _engine() as eng:
        found = eng.get_dependencies(application, direction=direction, max_depth=depth)

    def render(items: list[Any]) -> None:
        for d in items:
            ev = f"  [{d.evidence}]" if d.evidence else ""
            typer.echo(
                f"{d.direction}\t{' > '.join([*d.via, d.application])}\t{d.type}\t{d.confidence}"
                f"\t{d.detail}{ev}"
            )
        if not items:
            typer.echo("note: no dependencies were found.", err=True)

    _emit(found, as_json, render)


@query_app.command("search")
def search(
    query: str,
    app: AppsOption = None,
    language: LangOption = None,
    path: PathOption = None,
    limit: LimitOption = 10,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """Search the code by meaning and by exact terms (QRY-04)."""
    with _engine(semantic=True) as eng:
        page = eng.search_code(
            query, applications=app, languages=language, paths=path, limit=limit, offset=offset
        )

    def render(p: Page[Any]) -> None:
        for h in p.items:
            typer.echo(f"{h.source}  [{h.kind} {h.name or ''}] ({'+'.join(h.matched_by)})")
            typer.echo("    " + h.snippet.splitlines()[0][:120] if h.snippet else "")
        _page_footer(p)

    _emit(page, as_json, render)


@query_app.command("catalogs")
def catalogs(
    query: str,
    app: AppsOption = None,
    limit: LimitOption = 10,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """Search the catalogs (functional knowledge) (QRY-05)."""
    with _engine() as eng:
        page = eng.search_catalogs(query, applications=app, limit=limit, offset=offset)

    def render(p: Page[Any]) -> None:
        for h in p.items:
            first = next((x for x in h.snippet.splitlines() if x.strip()), "")
            typer.echo(f"{h.application}\t{h.section}\t{first[:120]}")
        _page_footer(p)

    _emit(page, as_json, render)


@query_app.command("grep")
def grep(
    pattern: str,
    regex: Annotated[bool, typer.Option("--regex", help="Treat the pattern as a regex.")] = False,
    ignore_case: Annotated[bool, typer.Option("-i", "--ignore-case")] = False,
    app: AppsOption = None,
    language: LangOption = None,
    path: PathOption = None,
    limit: LimitOption = 20,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """Find lines by literal text or regex in the indexed code (QRY-07)."""
    with _engine() as eng:
        page = eng.grep(
            pattern,
            regex=regex,
            case_sensitive=not ignore_case,
            applications=app,
            languages=language,
            paths=path,
            limit=limit,
            offset=offset,
        )

    def render(p: Page[Any]) -> None:
        for h in p.items:
            typer.echo(f"{h.source}: {h.text.strip()}")
        _page_footer(p)

    _emit(page, as_json, render)


@query_app.command("symbol")
def symbol(
    name: str,
    kind: Annotated[str | None, typer.Option(help="function, class, ...")] = None,
    contains: Annotated[bool, typer.Option("--contains", help="Match part of the name.")] = False,
    usages: Annotated[bool, typer.Option("--usages", help="Include usages.")] = False,
    app: AppsOption = None,
    limit: LimitOption = 20,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """Find where a symbol is defined (QRY-07)."""
    with _engine() as eng:
        page = eng.find_symbol(
            name,
            kind=kind,
            exact=not contains,
            include_usages=usages,
            applications=app,
            limit=limit,
            offset=offset,
        )

    def render(p: Page[Any]) -> None:
        for h in p.items:
            typer.echo(f"{h.source}  {h.kind} {h.name}{'' if h.is_definition else ' (usage)'}")
        _page_footer(p)

    _emit(page, as_json, render)


@query_app.command("read")
def read(
    application: str,
    path: str,
    start: Annotated[int, typer.Option("--start", help="First line.")] = 1,
    end: Annotated[int | None, typer.Option("--end", help="Last line.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Read a line range of an indexed file (QRY-06); secrets are refused."""
    with _engine() as eng:
        piece = eng.read_file(application, path, start_line=start, end_line=end)

    def render(s: Any) -> None:
        if s.stale:
            typer.echo("warning: the file changed after it was indexed.", err=True)
        typer.echo(s.text)
        if s.truncated:
            typer.echo(f"truncated: continue with --start {s.next_start_line}", err=True)

    _emit(piece, as_json, render)


@query_app.command("status")
def status(application: str | None = None, as_json: JsonOption = False) -> None:
    """Indexing status per application."""
    with _engine() as eng:
        result = eng.status(application)

    def render(s: Any) -> None:
        for a in s.applications:
            files = ", ".join(f"{k}:{v}" for k, v in sorted(a.files.items()))
            stale = " (catalog outdated)" if a.catalog_outdated else ""
            typer.echo(
                f"{a.name}\t{files}\tchunks {a.embedded_chunks}/{a.chunks} embedded"
                f"\tcatalog {a.catalog_status or '-'}{stale}\trelations {a.relations}"
            )
        for note in s.notes:
            typer.echo(f"note: {note}", err=True)

    _emit(result, as_json, render)
