"""`stc` command line. Commands not yet implemented fail with a clear message."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, NoReturn

import psycopg
import typer

from stc import __version__
from stc.catalog.context import UnknownApplication
from stc.catalog.globalmap import write_global_map
from stc.catalog.model import LLM_SECTIONS
from stc.catalog.store import Prepared, generate_and_store, prepare
from stc.config import Settings, get_settings
from stc.db.migrate import MigrationError, migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.embed_store import embed_pending, estimate_pending
from stc.indexer.pipeline import detect_root, host_aliases, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.rootconfig import ConfigError
from stc.indexer.signal_store import extract_pending, list_relations, rebuild_relations
from stc.providers import (
    LLMClient,
    Message,
    ProviderError,
    build_embeddings_client,
    build_llm_client,
    closing,
)
from stc.query.cli import query_app

app = typer.Typer(no_args_is_help=True, help="STC — Talk is cheap. Show me the code.")
app.add_typer(query_app, name="query")


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
    _relations_stage(settings, root)
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
            stats = asyncio.run(
                closing(embed_pending(conn, client, concurrency=settings.concurrency), client)
            )
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


def _relations_stage(settings: Settings, root: Path) -> None:
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    try:
        aliases = host_aliases(root)
        with psycopg.connect(settings.db_url) as conn:
            signals = extract_pending(conn, root)
            relations = rebuild_relations(conn, aliases)
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


@app.command()
def relations(
    root: RootOption = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract signals and re-derive before listing.")
    ] = False,
) -> None:
    """List the relations between applications, with confidence and evidence."""
    settings = get_settings()
    if settings.db_url is None:
        typer.echo("STC_DB_URL is not set.", err=True)
        raise typer.Exit(code=2)
    if refresh:
        _relations_stage(settings, _resolve_root(root))
    try:
        with psycopg.connect(settings.db_url) as conn:
            rows = list_relations(conn)
    except psycopg.Error as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for source, target, type_, confidence, detail, evidence, count in rows:
        arrow = "<->" if type_ == "shared_database" else "->"
        typer.echo(f"{source} {arrow} {target}  [{type_}, {confidence}]  {detail}")
        typer.echo(f"    evidence: {evidence} ({count} signal(s))")
    typer.echo(f"{len(rows)} relation(s).", err=True)


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
def reindex() -> None:
    """Force reindexing of one application or the whole Root."""
    _not_implemented("reindex")


@app.command()
def status() -> None:
    """Show indexing status."""
    _not_implemented("status")


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
