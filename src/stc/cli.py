"""`stc` command line. Commands not yet implemented fail with a clear message."""

import asyncio
from pathlib import Path
from typing import Annotated, NoReturn

import psycopg
import typer

from stc import __version__
from stc.config import Settings, get_settings
from stc.db.migrate import MigrationError, migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.embed_store import embed_pending, estimate_pending
from stc.indexer.pipeline import detect_root, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.rootconfig import ConfigError
from stc.providers import (
    Message,
    ProviderError,
    build_embeddings_client,
    build_llm_client,
)

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

YesOption = Annotated[
    bool, typer.Option("--yes", help="Do not ask for confirmation of a large embedding run.")
]
# Above this many estimated tokens the embedding run asks for confirmation (LCY-07).
CONFIRM_TOKENS = 1_000_000


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
    skip_embeddings: Annotated[
        bool, typer.Option("--skip-embeddings", help="Stop after chunking.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """Detect, apply hygiene, register, chunk and embed (catalogs and relations not built yet)."""
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
    if not skip_embeddings:
        _embed_stage(settings, dry_run=False, yes=yes, soft=True)
    if result.apps_missing:
        typer.echo(
            "Not found in the Root (kept; use --prune to remove): "
            + ", ".join(result.apps_missing),
            err=True,
        )


def _embed_stage(settings: Settings, *, dry_run: bool, yes: bool, soft: bool = False) -> None:
    """Embed pending chunks. `soft` (used by `stc index`) turns missing provider configuration
    into a warning, since everything before this stage already succeeded."""
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    provider, model = settings.embeddings_provider, settings.embeddings_model or "(not set)"
    try:
        with psycopg.connect(settings.db_url) as conn:
            estimate = estimate_pending(conn, provider, model)
            typer.echo(
                f"Embeddings: {estimate.chunks} chunk(s) to embed with {provider}/{model}, "
                f"about {estimate.tokens:,} tokens (rough estimate)."
            )
            if dry_run or estimate.chunks == 0:
                return
            try:
                client = build_embeddings_client(settings)
            except ProviderError as exc:
                if soft:
                    typer.echo(f"Embeddings skipped: {exc}", err=True)
                    return
                raise
            if estimate.tokens > CONFIRM_TOKENS and not yes:
                typer.confirm("Continue?", abort=True)
            stats = asyncio.run(embed_pending(conn, client, concurrency=settings.concurrency))
    except ProviderError as exc:
        typer.echo(f"Embedding failed: {exc} (progress is kept; run again to resume)", err=True)
        raise typer.Exit(code=1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error while embedding: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Embedded {stats.chunks_embedded} chunk(s) ({stats.chunks_replaced} replaced stale "
        f"vectors) in {stats.requests} request(s), {stats.retries} retried."
    )


@app.command()
def embed(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only estimate what would be embedded.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """Embed chunks that have no vector yet (or a stale one) using the configured provider."""
    _embed_stage(get_settings(), dry_run=dry_run, yes=yes)


@app.command(name="check-providers")
def check_providers() -> None:
    """Make one tiny embeddings call and one tiny chat call to verify keys, models and sizes.

    This sends two short fixed strings to the configured provider (a few tokens, no code)."""
    settings = get_settings()

    async def run() -> None:
        embeddings = build_embeddings_client(settings)
        vectors = await embeddings.embed(["STC provider check"])
        typer.echo(
            f"embeddings: {embeddings.provider}/{embeddings.model} -> {len(vectors[0])} dimensions"
        )
        for purpose in ("catalog", "chat"):
            llm = build_llm_client(settings, purpose)
            reply = await llm.complete([Message("user", "Reply with the single word OK.")])
            typer.echo(
                f"llm ({purpose}): {llm.provider}/{llm.model} -> {reply.text.strip()[:40]!r} "
                f"({reply.usage.input_tokens} in, {reply.usage.output_tokens} out)"
            )

    try:
        asyncio.run(run())
    except ProviderError as exc:
        typer.echo(f"Provider check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


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
