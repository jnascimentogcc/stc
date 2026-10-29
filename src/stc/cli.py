"""`stc` command line. Commands other than `version` are placeholders until implemented."""

from typing import NoReturn

import typer

from stc import __version__

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
    """Apply database migrations."""
    _not_implemented("migrate-db")


@app.command(name="serve-mcp")
def serve_mcp() -> None:
    """Run the MCP server."""
    _not_implemented("serve-mcp")
