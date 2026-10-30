"""`stc` command line."""

import asyncio
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from stc import __version__
from stc.catalog.context import UnknownApplication
from stc.catalog.corrections import Correction, list_corrections
from stc.catalog.corrections import approve as approve_correction
from stc.catalog.corrections import pending_count as pending_corrections
from stc.catalog.corrections import reject as reject_correction
from stc.catalog.embed import embed_sections, estimate_sections
from stc.catalog.globalmap import write_global_map
from stc.catalog.model import LLM_SECTIONS
from stc.catalog.store import Prepared, generate_and_store, prepare
from stc.config import Settings, get_settings
from stc.db.migrate import MigrationError, migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.confirm import candidates, confirm_relations
from stc.indexer.confirm import estimate_tokens as estimate_confirm
from stc.indexer.embed_store import EmbedStats, embed_pending, estimate_pending
from stc.indexer.pipeline import detect_roots, host_aliases, scan_roots
from stc.indexer.registry import sync_registry
from stc.indexer.rootconfig import ConfigError
from stc.indexer.runs import finish_run, has_finished_run, recent_runs, start_run
from stc.indexer.scan import ScannedApplication
from stc.indexer.signal_store import (
    RelationStats,
    SignalStats,
    extract_pending,
    rebuild_relations,
    relation_report,
)
from stc.providers import (
    LLMClient,
    Message,
    ProviderError,
    build_embeddings_client,
    build_llm_client,
    closing,
)
from stc.query.cli import query_app
from stc.query.models import QueryError
from stc.sources import Roots, SourcesError

app = typer.Typer(no_args_is_help=True, help="STC — Talk is cheap. Show me the code.")
app.add_typer(query_app, name="query")


@app.callback()
def main(
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Read the settings from this file instead of .env (also STC_ENV_FILE).",
        ),
    ] = None,
) -> None:
    if env_file is not None:
        if not env_file.is_file():
            typer.echo(f"The settings file does not exist: {env_file}", err=True)
            raise typer.Exit(code=2)
        os.environ["STC_ENV_FILE"] = str(env_file)
        get_settings.cache_clear()


@app.command()
def version() -> None:
    """Print the STC version."""
    typer.echo(__version__)


def _resolve_roots(root: Path | None, source: str | None = None) -> Roots:
    """The folders to read: `--root` alone, or STC_ROOT plus the sources of sources.toml,
    optionally narrowed to one source."""
    try:
        roots = Roots.single(root) if root is not None else get_settings().roots()
        if source is not None:
            roots = roots.only(source)
    except SourcesError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    if not roots:
        typer.echo("No Root given: use --root, set STC_ROOT or declare sources.toml.", err=True)
        raise typer.Exit(code=2)
    for name, path in roots.paths.items():
        if not path.is_dir():
            typer.echo(f"The source {name!r} is not a directory: {path}", err=True)
            raise typer.Exit(code=2)
    return roots


RootOption = Annotated[Path | None, typer.Option("--root", help="Folder with the applications.")]
SourceOption = Annotated[
    str | None, typer.Option("--source", help="Only this source (see sources.toml).")
]

YesOption = Annotated[
    bool, typer.Option("--yes", help="Do not ask for confirmation of a large embedding run.")
]
# Above this many estimated tokens the embedding run asks for confirmation (LCY-07).
CONFIRM_TOKENS = 1_000_000


@app.command()
def detect(root: RootOption = None, source: SourceOption = None) -> None:
    """List the applications detected in the Root (no database needed)."""
    roots = _resolve_roots(root, source)
    try:
        specs = detect_roots(roots)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Detection failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for spec in specs:
        origin = "manual" if spec.detected_by == "manual" else ", ".join(spec.markers)
        where = spec.base.as_posix() if len(roots.paths) == 1 else f"{spec.source}:{spec.base}"
        typer.echo(f"{spec.name}	{where}	{origin}")
    typer.echo(f"{len(specs)} application(s) detected.", err=True)


@app.command()
def index(
    root: RootOption = None,
    source: SourceOption = None,
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
    """Index the Root: detect, apply hygiene, register, chunk, derive relations and embed.

    Incremental: only files that changed since the last run are reprocessed (LCY-01)."""
    _run_index(
        root,
        source,
        only=None,
        force=False,
        dry_run=dry_run,
        prune=prune,
        skip_embeddings=skip_embeddings,
        yes=yes,
    )


@app.command()
def reindex(
    application: Annotated[
        str | None, typer.Argument(help="One application (default: the whole Root).")
    ] = None,
    root: RootOption = None,
    source: SourceOption = None,
    skip_embeddings: Annotated[
        bool, typer.Option("--skip-embeddings", help="Stop after chunking.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """Reprocess one application, or the whole Root, from its files on demand (LCY-05).

    Like `stc index`, but chunks and signals are derived again even for unchanged files. Vectors
    of chunks whose content did not change are kept, so this costs no embedding calls unless the
    code changed."""
    _run_index(
        root,
        source,
        only=application,
        force=True,
        dry_run=False,
        prune=False,
        skip_embeddings=skip_embeddings,
        yes=yes,
    )


def _run_index(
    root_option: Path | None,
    source: str | None,
    *,
    only: str | None,
    force: bool,
    dry_run: bool,
    prune: bool,
    skip_embeddings: bool,
    yes: bool,
) -> None:
    root = _resolve_roots(root_option, source)
    try:
        scanned = scan_roots(root, only=only)
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

    summary: dict[str, object] = {"applications": len(scanned), "forced": force}
    try:
        control = psycopg.connect(settings.db_url, autocommit=True)
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    with control:
        kind = (
            "application"
            if only
            else "full"
            if force or not has_finished_run(control)
            else "incremental"
        )
        run_id = start_run(control, kind, only)
        try:
            status = _index_stages(
                settings, root, scanned, summary, only, force, prune, skip_embeddings, yes
            )
        except BaseException as exc:
            finish_run(control, run_id, "failed", summary, str(exc) or type(exc).__name__)
            raise
        finish_run(control, run_id, status, summary)
    typer.echo(f"Run {run_id} ({kind}): {status}.", err=True)


def _index_stages(
    settings: Settings,
    root: Roots,
    scanned: list[ScannedApplication],
    summary: dict[str, object],
    only: str | None,
    force: bool,
    prune: bool,
    skip_embeddings: bool,
    yes: bool,
) -> str:
    """Registry, chunks, relations and embeddings; fills `summary`, returns the run status."""
    assert settings.db_url is not None
    status = "succeeded"
    try:
        with psycopg.connect(settings.db_url) as conn:
            result = sync_registry(
                conn,
                scanned,
                prune=prune,
                partial=only is not None,
                sources=set(root.paths),
            )
            if force:
                conn.execute(
                    "UPDATE files SET chunked_key = NULL, signals_key = NULL"
                    " WHERE application_id IN (SELECT id FROM applications WHERE name = ANY(%s))",
                    ([a.spec.name for a in scanned],),
                )
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    changed = [a for a in result.applications if a.changed]
    summary["registry"] = {
        "apps_added": len(result.apps_added),
        "apps_removed": len(result.apps_removed),
        "apps_with_changes": len(changed),
        "files_added": sum(a.files_added for a in result.applications),
        "files_changed": sum(a.files_changed for a in result.applications),
        "files_removed": sum(a.files_removed for a in result.applications),
    }
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
    summary["chunks"] = {
        "files_chunked": chunks.files_chunked,
        "new": chunks.chunks_new,
        "kept": chunks.chunks_kept,
        "removed": chunks.chunks_removed,
        "symbols": chunks.symbols,
        "files_redacted": chunks.files_redacted,
        "values_redacted": chunks.values_redacted,
    }
    typer.echo(
        f"Chunks: {chunks.files_chunked} file(s) chunked ({chunks.files_as_text} as text), "
        f"{chunks.chunks_new} new, {chunks.chunks_kept} kept, {chunks.chunks_removed} removed; "
        f"{chunks.symbols} symbols."
    )
    if chunks.values_redacted:
        typer.echo(
            f"Redacted {chunks.values_redacted} credential value(s) in {chunks.files_redacted} "
            "configuration file(s): the values are not stored, embedded or sent anywhere.",
            err=True,
        )
    if chunks.files_skipped:
        status = "partial"
        typer.echo(
            f"{chunks.files_skipped} file(s) skipped (unreadable or changed during the run); "
            "run `stc index` again.",
            err=True,
        )
    signals, relations = _relations_stage(settings, root)
    summary["relations"] = {
        "files_read": signals.files_extracted,
        "signals": signals.signals,
        "total": relations.total,
        "added": relations.added,
        "removed": relations.removed,
    }
    if signals.files_skipped:
        status = "partial"
    if skip_embeddings:
        summary["embeddings"] = "skipped"
    else:
        embedded = _embed_stage(settings, dry_run=False, yes=yes, soft=True, application=only)
        if embedded is None:
            status = "partial"
            summary["embeddings"] = "not run"
        else:
            summary["embeddings"] = {
                "chunks": embedded[0].chunks_embedded,
                "sections": embedded[1].chunks_embedded,
            }
    if result.apps_missing:
        typer.echo(
            "Not found in the Root (kept; use --prune to remove): "
            + ", ".join(result.apps_missing),
            err=True,
        )
    return status


def _embed_stage(
    settings: Settings,
    *,
    dry_run: bool,
    yes: bool,
    soft: bool = False,
    application: str | None = None,
) -> tuple[EmbedStats, EmbedStats] | None:
    """Embed pending chunks and catalog sections. `soft` (used by `stc index`) turns missing
    provider configuration into a warning, since everything before this stage already succeeded.
    Returns the stats (chunks, sections), or None when nothing was embedded because of a dry run
    or a soft skip."""
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    provider, model = settings.embeddings_provider, settings.embeddings_model or "(not set)"
    try:
        with psycopg.connect(settings.db_url) as conn:
            chunk_estimate = estimate_pending(conn, provider, model, application=application)
            section_estimate = estimate_sections(conn, provider, model, application=application)
            tokens = chunk_estimate.tokens + section_estimate.tokens
            typer.echo(
                f"Embeddings: {chunk_estimate.chunks} chunk(s) and {section_estimate.sections} "
                f"catalog section(s) to embed with {provider}/{model}, about {tokens:,} tokens "
                "(rough estimate)."
            )
            if dry_run:
                return None
            if chunk_estimate.chunks == 0 and section_estimate.sections == 0:
                return EmbedStats(), EmbedStats()
            try:
                client = build_embeddings_client(settings)
            except ProviderError as exc:
                if soft:
                    typer.echo(f"Embeddings skipped: {exc}", err=True)
                    return None
                raise
            if tokens > CONFIRM_TOKENS and not yes:
                typer.confirm("Continue?", abort=True)

            async def work() -> tuple[EmbedStats, EmbedStats]:
                chunks = await embed_pending(
                    conn, client, application=application, concurrency=settings.concurrency
                )
                sections = await embed_sections(conn, client, application=application)
                return chunks, sections

            stats = asyncio.run(closing(work(), client))
    except ProviderError as exc:
        typer.echo(f"Embedding failed: {exc} (progress is kept; run again to resume)", err=True)
        raise typer.Exit(code=1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error while embedding: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Embedded {stats[0].chunks_embedded} chunk(s) ({stats[0].chunks_replaced} replaced stale "
        f"vectors) and {stats[1].chunks_embedded} catalog section(s) in "
        f"{stats[0].requests + stats[1].requests} request(s), "
        f"{stats[0].retries + stats[1].retries} retried."
    )
    return stats


def _relations_stage(settings: Settings, root: Roots) -> tuple[SignalStats, RelationStats]:
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        aliases = host_aliases(root)
        with psycopg.connect(settings.db_url) as conn:
            signals = extract_pending(conn, root)
            relations = rebuild_relations(conn, aliases, root)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Relations failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error while deriving relations: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Signals: {signals.files_extracted} file(s) read, {signals.signals} signal(s). "
        f"Relations: {relations.total} ({relations.added} new, {relations.updated} updated, "
        f"{relations.removed} removed)."
    )
    if signals.files_skipped:
        typer.echo(
            f"{signals.files_skipped} file(s) skipped (unreadable or changed during the run); "
            "run `stc index` again.",
            err=True,
        )
    return signals, relations


@app.command()
def relations(
    root: RootOption = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract signals and re-derive before listing.")
    ] = False,
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Ask the LLM to confirm or reject the low and medium confidence relations.",
        ),
    ] = False,
    recheck: Annotated[
        bool, typer.Option("--recheck", help="With --confirm: also judge the ones left 'unsure'.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="With --confirm: only show what would be sent.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """List the relations between applications, with confidence, evidence and LLM verdicts."""
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    if refresh:
        _relations_stage(settings, _resolve_roots(root))
    try:
        if confirm:
            _confirm_stage(settings, recheck=recheck, dry_run=dry_run, yes=yes)
        with psycopg.connect(settings.db_url) as conn:
            rows = relation_report(conn)
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for row in rows:
        arrow = "<->" if row.type == "shared_database" else "->"
        marks = {"confirmed": ", confirmed by the LLM", "rejected": ", REJECTED by the LLM"}
        typer.echo(
            f"{row.source} {arrow} {row.target}  [{row.type}, {row.confidence}"
            f"{marks.get(row.verdict or '', '')}]  {row.detail}"
        )
        typer.echo(f"    evidence: {row.evidence} ({row.evidence_count} signal(s))")
        if row.verdict and row.reason:
            typer.echo(f"    LLM ({row.verdict}): {row.reason}")
    hidden = sum(1 for r in rows if r.verdict == "rejected")
    typer.echo(
        f"{len(rows)} relation(s)"
        + (f", {hidden} rejected (hidden elsewhere)." if hidden else "."),
        err=True,
    )


def _confirm_stage(settings: Settings, *, recheck: bool, dry_run: bool, yes: bool) -> None:
    """LLM confirmation of the uncertain relations; only shows the cost with `dry_run`."""
    assert settings.db_url is not None
    provider = settings.llm_provider
    model = settings.llm_catalog_model or "(not set)"
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            items = candidates(conn, recheck=recheck)
            tokens_in, tokens_out = estimate_confirm(items)
            typer.echo(
                f"{len(items)} relation(s) of low or medium confidence to check with "
                f"{provider}/{model}; about {tokens_in:,} input and {tokens_out:,} output tokens "
                "(rough estimate).",
                err=True,
            )
            if dry_run or not items:
                return
            try:
                llm = build_llm_client(settings, "catalog")
            except ProviderError as exc:
                typer.echo(f"Cannot confirm relations: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            if tokens_in > CONFIRM_TOKENS and not yes:
                typer.confirm("Continue?", abort=True)
            stats = asyncio.run(
                closing(confirm_relations(conn, llm, items, concurrency=settings.concurrency), llm)
            )
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Checked {stats.checked}: {stats.confirmed} confirmed, {stats.rejected} rejected, "
        f"{stats.unsure} unsure; {stats.failed} could not be judged (offered again next time). "
        f"{stats.input_tokens:,} input and {stats.output_tokens:,} output tokens used.",
        err=True,
    )
    if stats.rejected:
        typer.echo(
            "Rejected relations are hidden from the map, the catalogs and the queries; run "
            "`stc catalog --all` to refresh the catalogs that mentioned them.",
            err=True,
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
        vectors = await closing(embeddings.embed(["STC provider check"]), embeddings)
        typer.echo(
            f"embeddings: {embeddings.provider}/{embeddings.model} -> {len(vectors[0])} dimensions"
        )
        for purpose in ("catalog", "chat"):
            llm = build_llm_client(settings, purpose)
            reply = await closing(
                llm.complete([Message("user", "Reply with the single word OK.")]), llm
            )
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
def status(
    application: Annotated[
        str | None, typer.Argument(help="One application (default: all).")
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="Also scan the Root and list what changed since the last run."
        ),
    ] = False,
    root: RootOption = None,
    source: SourceOption = None,
) -> None:
    """Show what is indexed, what is pending and how the last runs went (XCT-05)."""
    from stc.indexer.changes import pending_changes, removed_applications
    from stc.query.browse import index_status
    from stc.query.models import QueryError

    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    scanned = None
    if check:
        chosen = _resolve_roots(root, source)
        try:
            scanned = scan_roots(chosen, only=application)
        except (ConfigError, ValueError) as exc:
            typer.echo(f"Scan failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            try:
                state = index_status(conn, application)
            except QueryError as exc:
                typer.echo(exc.message, err=True)
                raise typer.Exit(code=2) from exc
            runs = recent_runs(conn, 5)
            provider, model = settings.embeddings_provider, settings.embeddings_model
            pending_chunks = pending_sections = 0
            if model:
                pending_chunks = estimate_pending(
                    conn, provider, model, application=application
                ).chunks
                pending_sections = estimate_sections(
                    conn, provider, model, application=application
                ).sections
            unjudged_row = conn.execute(
                "SELECT count(*) FROM relations WHERE origin = 'static' AND llm_verdict IS NULL"
                " AND confidence IN ('low', 'medium')"
            ).fetchone()
            unjudged = int(unjudged_row[0]) if unjudged_row else 0
            proposals = pending_corrections(conn)
            changes = pending_changes(conn, scanned) if scanned is not None else None
            gone = (
                removed_applications(conn, scanned, chosen.paths)
                if scanned and not application
                else []
            )
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for note in state.notes:
        typer.echo(note)
    for item in state.applications:
        files = ", ".join(f"{n} {s}" for s, n in sorted(item.files.items()))
        catalog = item.catalog_status or "none"
        if item.catalog_outdated:
            catalog += " (outdated)"
        version = (item.code_version or "unknown")[:7]
        typer.echo(
            f"{item.name}  [{version}]  files: {files or 'none'}  "
            f"chunks: {item.embedded_chunks}/{item.chunks} embedded  catalog: {catalog}  "
            f"relations: {item.relations}"
        )
    missing = [a.name for a in state.applications if a.catalog_status is None]
    partial = [a.name for a in state.applications if a.catalog_status == "partial"]
    outdated = [a.name for a in state.applications if a.catalog_outdated]
    todo = []
    if pending_chunks or pending_sections:
        todo.append(
            f"{pending_chunks} chunk(s) and {pending_sections} catalog section(s) not embedded "
            "with the configured model (`stc embed`)"
        )
    if missing:
        todo.append(f"no catalog yet: {', '.join(missing)} (`stc catalog --all`)")
    if partial:
        todo.append(f"incomplete catalog: {', '.join(partial)} (run `stc catalog` again)")
    if outdated:
        todo.append(f"catalog older than the code: {', '.join(outdated)} (`stc catalog`)")
    if unjudged:
        todo.append(
            f"{unjudged} uncertain relation(s) not checked by the LLM (`stc relations --confirm`)"
        )
    if proposals:
        todo.append(
            f"{proposals} catalog correction(s) proposed from the chat wait for you "
            "(`stc corrections list`)"
        )
    if changes is not None:
        for change in changes:
            if change.any:
                what = "new application" if change.new_application else (
                    f"{change.added} new, {change.changed} changed, "
                    f"{change.removed} removed file(s)"
                )  # fmt: skip
                todo.append(f"{change.name}: {what} in the Root (`stc index`)")
        if gone:
            todo.append(f"no longer in the Root: {', '.join(gone)} (`stc index --prune`)")
        if not any(c.any for c in changes) and not gone:
            typer.echo("The Root has no changes since the last run.")
    if todo:
        typer.echo("To do:")
        for line in todo:
            typer.echo(f"  - {line}")
    elif state.applications:
        typer.echo("Nothing pending.")

    if runs:
        typer.echo("Last runs:")
        for run in runs:
            when = run.started_at.strftime("%Y-%m-%d %H:%M UTC")
            took = f", {run.seconds:.0f}s" if run.seconds is not None else ""
            target = f" {run.application}" if run.application else ""
            registry = run.summary.get("registry")
            files = (
                f"; {registry['files_added']} added, {registry['files_changed']} changed, "
                f"{registry['files_removed']} removed file(s)"
                if isinstance(registry, dict)
                else ""
            )
            problem = f" - {run.error}" if run.error else ""
            typer.echo(f"  #{run.id} {run.kind}{target} {run.status} {when}{took}{files}{problem}")
    else:
        typer.echo("No indexing run recorded yet (`stc index`).")


async def _generate_all(
    conn: psycopg.Connection, llm: LLMClient, plan: list[Prepared], settings: Settings
) -> list[str]:
    """Generate every planned catalog in ONE event loop: the provider client keeps pooled
    connections that belong to the loop that created them. Returns the incomplete ones."""
    failed: list[str] = []
    for prepared in plan:
        name = prepared.context.name
        try:
            outcome = await generate_and_store(
                conn, llm, prepared, language=settings.language, concurrency=settings.concurrency
            )
        except ProviderError as exc:
            typer.echo(f"{name}: failed: {exc}", err=True)
            failed.append(name)
            continue
        typer.echo(
            f"{name}: wrote {outcome.path} ({outcome.status}): {outcome.claims} statement(s) "
            f"with verified evidence; {outcome.input_tokens:,} input and "
            f"{outcome.output_tokens:,} output tokens used."
        )
        if outcome.failed_sections:
            typer.echo(
                f"{name}: sections that failed: {', '.join(outcome.failed_sections)}"
                " (run again to retry).",
                err=True,
            )
            failed.append(name)
    return failed


@app.command()
def catalog(
    application: Annotated[
        str | None, typer.Argument(help="Application name (see `stc detect`).")
    ] = None,
    all_applications: Annotated[
        bool, typer.Option("--all", help="Generate the catalog of every indexed application.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only show what would be sent and the estimate.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Regenerate even if the inputs did not change.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """Generate the catalog of one application (or --all) and refresh the global map."""
    if (application is not None) == all_applications:
        typer.echo("Give an application name or --all (not both).", err=True)
        raise typer.Exit(code=2)
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    provider = settings.llm_provider
    model = settings.llm_catalog_model or "(not set)"
    failed: list[str] = []
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            known = [str(r[0]) for r in conn.execute("SELECT name FROM applications ORDER BY 1")]
            if all_applications:
                names = known
            else:
                assert application is not None
                if application not in known:
                    typer.echo(
                        f"Unknown application {application!r}. Indexed applications: "
                        + (", ".join(known) or "(none; run `stc index`)"),
                        err=True,
                    )
                    raise typer.Exit(code=2)
                names = [application]
            plan = []
            for name in names:
                prepared = prepare(
                    conn, name, settings.catalogs, provider=provider, model=model,
                    language=settings.language, force=force,
                )  # fmt: skip
                sections = [s for s in LLM_SECTIONS if not prepared.context.sections[s].empty]
                state = "up to date" if prepared.up_to_date else (
                    f"{len(sections)} of {len(LLM_SECTIONS)} sections have evidence; about "
                    f"{prepared.estimate_input_tokens:,} input tokens"
                )  # fmt: skip
                typer.echo(f"{name}: {state}")
                if not prepared.up_to_date:
                    plan.append(prepared)
            tokens_in = sum(p.estimate_input_tokens for p in plan)
            tokens_out = sum(p.estimate_output_tokens for p in plan)
            typer.echo(
                f"{len(plan)} of {len(names)} catalog(s) to generate with {provider}/{model}; "
                f"about {tokens_in:,} input and {tokens_out:,} output tokens (rough estimate)."
            )
            if dry_run:
                return
            if plan:
                llm = build_llm_client(settings, "catalog")
                if tokens_in > CONFIRM_TOKENS and not yes:
                    typer.confirm("Continue?", abort=True)
                failed = asyncio.run(closing(_generate_all(conn, llm, plan, settings), llm))
            if plan:
                _embed_stage(settings, dry_run=False, yes=yes, soft=True)
            typer.echo(f"Wrote {write_global_map(conn, settings.catalogs)}")
    except UnknownApplication as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except ProviderError as exc:
        typer.echo(f"Catalog failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if failed:
        typer.echo(f"{len(failed)} application(s) not complete: {', '.join(failed)}", err=True)
        raise typer.Exit(code=1)


@app.command(name="map")
def global_map() -> None:
    """Rebuild the global map from the indexed relations and catalogs (no LLM, no cost)."""
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            path = write_global_map(conn, settings.catalogs)
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {path}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Your question about the applications.")],
    app_scope: Annotated[
        list[str] | None, typer.Option("--app", help="Restrict to this application (repeatable).")
    ] = None,
    profile: Annotated[str, typer.Option(help="technical or functional.")] = "technical",
) -> None:
    """Ask one question, as the chat would (not saved). Tool calls go to stderr."""
    from stc.chat.agent import run_agent
    from stc.chat.prompt import PROFILES
    from stc.mcp_server import build_server

    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    if profile not in PROFILES:
        typer.echo(f"--profile must be one of: {', '.join(PROFILES)}.", err=True)
        raise typer.Exit(code=2)
    try:
        llm = build_llm_client(settings, "chat")
    except ProviderError as exc:
        typer.echo(f"The chat model is not available: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    server = build_server(settings, scope=app_scope)

    async def run() -> dict[str, object] | None:
        final: dict[str, object] | None = None
        async for event in run_agent(
            llm,
            server,
            history=[],
            question=question,
            profile=profile,
            language=settings.language,
            scope=app_scope,
        ):
            if event.type == "tool_call":
                typer.echo(
                    f"> {event.data['name']} {json.dumps(event.data['arguments'])}", err=True
                )
            elif event.type == "tool_result":
                typer.echo(f"  {event.data['summary']}", err=True)
            elif event.type == "error":
                typer.echo(f"Error: {event.data['message']}", err=True)
                return None
            elif event.type == "done":
                final = event.data
        return final

    done = asyncio.run(closing(run(), llm))
    if done is None:
        raise typer.Exit(code=1)
    typer.echo(str(done["answer"]))
    for warning in done["warnings"]:  # type: ignore[attr-defined]
        typer.echo(f"warning: {warning}", err=True)


corrections_app = typer.Typer(
    no_args_is_help=True, help="Review the catalog corrections proposed from the chat (CHT-10)."
)
app.add_typer(corrections_app, name="corrections")


@contextmanager
def _corrections_db() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            yield conn
    except QueryError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=2 if exc.code != "not_found" else 1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _show_correction(c: Correction, *, full: bool) -> None:
    typer.echo(f"#{c.id} [{c.status}] {c.application} / {c.section}  ({c.created_at:%Y-%m-%d})")
    shown = c.text if full else (c.text[:120] + ("..." if len(c.text) > 120 else ""))
    typer.echo(f"    correction: {shown}")
    if full and c.question:
        typer.echo(f"    asked:      {c.question[:300]}")
    if full and c.answer:
        typer.echo(f"    answered:   {c.answer[:300]}{'...' if len(c.answer) > 300 else ''}")
    if c.note:
        typer.echo(f"    note:       {c.note}")


@corrections_app.command("list")
def corrections_list(
    status: Annotated[
        str | None, typer.Option(help="proposed, approved or rejected (default: all).")
    ] = "proposed",
    all_statuses: Annotated[bool, typer.Option("--all", help="Every status.")] = False,
) -> None:
    """List correction proposals (the ones waiting by default)."""
    with _corrections_db() as conn:
        found = list_corrections(conn, None if all_statuses else status)
    for item in found:
        _show_correction(item, full=True)
    typer.echo(f"{len(found)} correction(s).", err=True)


@corrections_app.command("approve")
def corrections_approve(
    correction_id: Annotated[int, typer.Argument(help="The number shown by `list`.")],
    note: Annotated[str | None, typer.Option(help="Why, for the record.")] = None,
) -> None:
    """Write a proposal into the manual block of its catalog section (it becomes authoritative)."""
    settings = get_settings()
    with _corrections_db() as conn:
        done = approve_correction(conn, settings.catalogs, correction_id, note)
    typer.echo(
        f"Approved #{done.id}: written to the {done.section!r} section of {done.application}."
    )
    typer.echo(
        f"Run `stc catalog {done.application}` to regenerate it with the correction as authority.",
        err=True,
    )


@corrections_app.command("reject")
def corrections_reject(
    correction_id: Annotated[int, typer.Argument(help="The number shown by `list`.")],
    note: Annotated[str | None, typer.Option(help="Why, for the record.")] = None,
) -> None:
    """Discard a proposal; the catalog is not changed."""
    with _corrections_db() as conn:
        done = reject_correction(conn, correction_id, note)
    typer.echo(f"Rejected #{done.id}.")


@app.command()
def evaluate(
    questions: Annotated[
        Path, typer.Option("--questions", help="The reference questions (YAML).")
    ] = Path("evaluation/questions.yaml"),
    mode: Annotated[
        str,
        typer.Option(
            help="retrieval (search only, almost free), answers (asks the chat model) or all."
        ),
    ] = "retrieval",
    only: Annotated[
        list[str] | None, typer.Option("--id", help="Only this question id (repeatable).")
    ] = None,
    k: Annotated[int, typer.Option(help="How many search results count (max 50).")] = 10,
    baseline: Annotated[
        str,
        typer.Option(help="Report to compare with: a file, 'latest' (default) or 'none'."),
    ] = "latest",
    save: Annotated[bool, typer.Option("--save/--no-save", help="Store the report.")] = True,
    fail_under: Annotated[
        float | None,
        typer.Option(help="Exit 1 if answers passed (or search recall@k) falls below this, 0-1."),
    ] = None,
    yes: YesOption = False,
) -> None:
    """Run the reference questions and report search and answer quality (XCT-08)."""
    from stc.evaluation import report as reports
    from stc.evaluation.model import load_questions
    from stc.evaluation.runner import run_answers, run_retrieval
    from stc.query.engine import build_query_embedder

    settings = get_settings()
    if mode not in ("retrieval", "answers", "all"):
        typer.echo("--mode must be retrieval, answers or all.", err=True)
        raise typer.Exit(code=2)
    if not 1 <= k <= 50:
        typer.echo("--k must be between 1 and 50.", err=True)
        raise typer.Exit(code=2)
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        question_set = load_questions(questions)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    selected = [q for q in question_set.questions if not only or q.id in only]
    unknown = sorted(set(only or []) - {q.id for q in question_set.questions})
    if unknown or not selected:
        typer.echo(f"Unknown question id(s): {', '.join(unknown) or '(none selected)'}", err=True)
        raise typer.Exit(code=2)

    retrieval = None
    answers = None
    try:
        with psycopg.connect(settings.db_url, autocommit=True) as conn:
            known = {
                str(r[0]): r[1]
                for r in conn.execute("SELECT name, indexed_version FROM applications")
            }
            missing = sorted(
                {
                    a
                    for q in selected
                    for a in [*(q.scope or []), *q.expect.applications]
                    if a not in known
                }
            )
            if missing:
                typer.echo(
                    f"The questions name applications that are not indexed: {', '.join(missing)}",
                    err=True,
                )
                raise typer.Exit(code=2)
            if mode in ("retrieval", "all"):
                embedder = build_query_embedder(settings)
                typer.echo(
                    f"Search: {len(selected)} question(s), "
                    + ("hybrid and lexical" if embedder else "lexical only (no embeddings client)"),
                    err=True,
                )
                retrieval = run_retrieval(conn, settings, selected, k, embedder)
            if mode in ("answers", "all"):
                llm = build_llm_client(settings, "chat")
                typer.echo(
                    f"Answers: {len(selected)} question(s) go to {llm.provider}/{llm.model}, up to "
                    f"8 model calls each.",
                    err=True,
                )
                if not yes:
                    typer.confirm("Continue?", abort=True)

                def progress(question: object, outcome: object) -> None:
                    typer.echo(f"  answered {getattr(question, 'id', '?')}", err=True)

                answers = asyncio.run(
                    closing(run_answers(settings, llm, selected, on_progress=progress), llm)
                )
    except ProviderError as exc:
        typer.echo(f"Evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    records = [
        reports.question_record(q, (retrieval or {}).get(q.id), (answers or {}).get(q.id), k)
        for q in selected
    ]
    report = reports.build_report(
        records,
        k=k,
        mode=mode,
        settings={
            "llm": f"{settings.llm_provider}/{settings.llm_chat_model}" if answers else None,
            "embeddings": f"{settings.embeddings_provider}/{settings.embeddings_model}",
            "language": settings.language,
            "reasoning_effort": settings.llm_chat_reasoning_effort,
        },
        versions=known,
        questions_file=questions,
    )
    previous = None
    if baseline != "none":
        try:
            path = Path(baseline) if baseline != "latest" else reports.latest_report(settings.data)
            previous = reports.load_report(path) if path else None
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    comparison = reports.compare(previous, report) if previous else None
    for line in reports.render(report, comparison):
        typer.echo(line)
    if save:
        typer.echo(f"Report saved: {reports.save_report(settings.data, report)}", err=True)
    if fail_under is not None:
        summary = report["summary"]
        if answers:
            value = summary["pass_rate"]
        else:
            value = summary.get(f"hybrid_recall_at_{k}") or summary.get(f"lexical_recall_at_{k}")
        if value is None or value < fail_under:
            typer.echo(f"Below the required {fail_under:.2f}: {value}", err=True)
            raise typer.Exit(code=1)


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
def serve_mcp(
    transport: Annotated[
        str, typer.Option(help="stdio (local clients) or http (shared use, needs STC_MCP_TOKEN).")
    ] = "stdio",
    host: Annotated[str | None, typer.Option(help="HTTP host (default STC_MCP_HOST).")] = None,
    port: Annotated[int | None, typer.Option(help="HTTP port (default STC_MCP_PORT).")] = None,
) -> None:
    """Run the MCP server over the query engine (read-only)."""
    from stc.mcp_server import build_http_app, build_server

    settings = get_settings()
    if transport not in ("stdio", "http"):
        typer.echo("--transport must be stdio or http.", err=True)
        raise typer.Exit(code=2)
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    server = build_server(settings)
    if transport == "stdio":
        server.run("stdio")  # stdout belongs to the protocol: nothing else may print there
        return
    if not settings.mcp_token:
        typer.echo("STC_MCP_TOKEN must be set to serve MCP over HTTP.", err=True)
        raise typer.Exit(code=2)
    import uvicorn

    chosen = settings.model_copy(
        update={"mcp_host": host or settings.mcp_host, "mcp_port": port or settings.mcp_port}
    )
    typer.echo(f"MCP over HTTP on http://{chosen.mcp_host}:{chosen.mcp_port}/mcp", err=True)
    uvicorn.run(
        build_http_app(chosen, server),
        host=chosen.mcp_host,
        port=chosen.mcp_port,
        log_level="warning",
    )
