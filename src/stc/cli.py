"""`stc` command line. Commands not yet implemented fail with a clear message."""

from pathlib import Path
from typing import Annotated, NoReturn

import psycopg
import typer

from stc import __version__
from stc.config import get_settings
from stc.db.migrate import MigrationError, migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import detect_root, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.rootconfig import ConfigError

app = typer.Typer(no_args_is_help=True, help="STC — Talk is cheap. Show me the code.")


def _not_implemented(name: str) -> NoReturn:
    typer.echo(f"`stc {name}` is not implemented yet.", err=True)
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the STC version."""
    typer.echo(__version__)


def _resolve_root(root: Path | None) -> Path:
    chosen = root or get_settings().root
    if chosen is None:
        typer.echo("No Root given: use --root or set STC_ROOT.", err=True)
        raise typer.Exit(code=2)
    if not chosen.is_dir():
        typer.echo(f"Root is not a directory: {chosen}", err=True)
        raise typer.Exit(code=2)
    return chosen


RootOption = Annotated[Path | None, typer.Option("--root", help="Folder with the applications.")]


@app.command()
def detect(root: RootOption = None) -> None:
    """List the applications detected in the Root (no database needed)."""
    root = _resolve_root(root)
    try:
        specs = detect_root(root)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Detection failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for spec in specs:
        origin = "manual" if spec.detected_by == "manual" else ", ".join(spec.markers)
        typer.echo(f"{spec.name}	{spec.base.as_posix()}	{origin}")
    typer.echo(f"{len(specs)} application(s) detected.", err=True)


@app.command()
def index(
    root: RootOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Scan without touching the DB.")
    ] = False,
    prune: Annotated[
        bool, typer.Option("--prune", help="Remove applications that are no longer in the Root.")
    ] = False,
) -> None:
    """Detect applications, apply hygiene, register and chunk files (later stages not built)."""
    root = _resolve_root(root)
    try:
        scanned = scan_root(root)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Indexing failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not scanned:
        typer.echo("No applications detected; nothing to do.", err=True)
        raise typer.Exit(code=1)
    for app_scan in scanned:
        counts = ", ".join(
            f"{app_scan.count(s)} {s}"
            for s in ("indexed", "unsupported", "excluded", "secret", "error")
        )
        typer.echo(f"{app_scan.spec.name}: {counts}")
        for f in app_scan.files:
            if f.status in ("secret", "error"):
                typer.echo(f"  {f.status}: {f.path} ({f.exclusion_reason})")
    if dry_run:
        return
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set (use --dry-run to scan without a database).", err=True)
        raise typer.Exit(code=2)
    try:
        with psycopg.connect(settings.db_url) as conn:
            result = sync_registry(conn, scanned, prune=prune)
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    changed = [a for a in result.applications if a.changed]
    typer.echo(
        f"Registry: {len(result.apps_added)} application(s) added, "
        f"{len(changed)} with file changes, {len(result.apps_removed)} removed."
    )
    try:
        with psycopg.connect(settings.db_url) as conn:
            chunks = chunk_pending(conn, root)
    except psycopg.Error as exc:
        typer.echo(f"Database error while chunking: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Chunks: {chunks.files_chunked} file(s) chunked ({chunks.files_as_text} as text), "
        f"{chunks.chunks_new} new, {chunks.chunks_kept} kept, {chunks.chunks_removed} removed; "
        f"{chunks.symbols} symbols."
    )
    if chunks.files_skipped:
        typer.echo(
            f"{chunks.files_skipped} file(s) skipped (unreadable or changed during the run); "
            "run `stc index` again.",
            err=True,
        )
    if result.apps_missing:
        typer.echo(
            "Not found in the Root (kept; use --prune to remove): "
            + ", ".join(result.apps_missing),
            err=True,
        )


@app.command()
def reindex() -> None:
    """Force reindexing of one application or the whole Root."""
    _not_implemented("reindex")


@app.command()
def status() -> None:
    """Show indexing status."""
    _not_implemented("status")


@app.command()
def catalog() -> None:
    """Show or regenerate application catalogs."""
    _not_implemented("catalog")


@app.command()
def evaluate() -> None:
    """Run the reference-question evaluation."""
    _not_implemented("evaluate")


@app.command(name="migrate-db")
def migrate_db() -> None:
    """Apply database migrations and set up the `stc_app` role."""
    settings = get_settings()
    if settings.db_migration_url is None:
        typer.echo("STC_DB_MIGRATION_URL is not set.", err=True)
        raise typer.Exit(code=2)
    password = settings.app_password.get_secret_value() if settings.app_password else None
    try:
        applied = migrate(settings.db_migration_url, app_password=password)
    except (MigrationError, psycopg.Error) as exc:
        typer.echo(f"Migration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not applied:
        typer.echo("Database is up to date.")
    for migration in applied:
        typer.echo(f"Applied {migration.version:04d}_{migration.name}")
    if password is None:
        typer.echo("STC_APP_PASSWORD is not set: the stc_app role was not touched.", err=True)


@app.command(name="serve-mcp")
def serve_mcp() -> None:
    """Run the MCP server."""
    _not_implemented("serve-mcp")
