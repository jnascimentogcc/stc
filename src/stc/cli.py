"""`stc` command line. Commands other than `version` are placeholders until implemented."""

from typing import NoReturn

import psycopg
import typer

from stc import __version__
from stc.config import get_settings
from stc.db.migrate import MigrationError, migrate

app = typer.Typer(no_args_is_help=True, help="STC — Talk is cheap. Show me the code.")


def _not_implemented(name: str) -> NoReturn:
    typer.echo(f"`stc {name}` is not implemented yet.", err=True)
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the STC version."""
    typer.echo(__version__)


@app.command()
def index() -> None:
    """Index the Root (incremental)."""
    _not_implemented("index")


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
