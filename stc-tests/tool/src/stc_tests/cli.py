"""The `stc-tests` command line."""

from pathlib import Path
from typing import Annotated

import typer

from stc_tests import __version__, settings, state
from stc_tests.contract import ContractError, UnsupportedVersion
from stc_tests.reader import applications, read_application

app = typer.Typer(help="Prepare tests from the specifications STC writes.", no_args_is_help=True)

SpecsOption = Annotated[
    Path | None, typer.Option("--specs", help="Folder `stc spec` writes (default STC_SPECS).")
]
OutputOption = Annotated[
    Path | None, typer.Option("--output", help="Where the work goes (default STC_TESTS).")
]


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True)] = False,
) -> None:
    """Prepare tests from the specifications STC writes."""


def _targets(specs: Path, application: str | None, all_: bool) -> list[str]:
    if application and all_:
        typer.echo("Give an application or --all, not both.", err=True)
        raise typer.Exit(2)
    if application:
        return [application]
    if all_:
        found = applications(specs)
        if not found:
            typer.echo(f"No application with a _specs.json in {specs}.", err=True)
            raise typer.Exit(1)
        return found
    typer.echo("Give an application or --all.", err=True)
    raise typer.Exit(2)


@app.command()
def check(
    application: Annotated[str | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all")] = False,
    specs: SpecsOption = None,
) -> None:
    """Validate the specifications against the contract; refuse a major version it does not know."""
    root = settings.specs_dir(specs)
    failed = False
    for folder in _targets(root, application, all_):
        try:
            read = read_application(root, folder)
        except UnsupportedVersion as error:
            typer.echo(f"{folder}: REFUSED: {error}", err=True)
            failed = True
        except (ContractError, KeyError) as error:
            typer.echo(f"{folder}: INVALID: {error}", err=True)
            failed = True
        else:
            typer.echo(f"{folder}: ok ({len(read.features)} features, {len(read.items())} items)")
    raise typer.Exit(1 if failed else 0)


@app.command()
def ingest(
    application: Annotated[str | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all")] = False,
    specs: SpecsOption = None,
    output: OutputOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the drift, write nothing.")
    ] = False,
) -> None:
    """Take the specifications in and report what is new, changed or retired since the last run."""
    root, out = settings.specs_dir(specs), settings.output_dir(output)
    failed = False
    for folder in _targets(root, application, all_):
        try:
            read = read_application(root, folder)
        except (ContractError, KeyError) as error:
            typer.echo(f"{folder}: REFUSED: {error}", err=True)
            failed = True
            continue
        path = state.state_path(out, folder)
        known = state.load(path)
        drift = state.compare(read, known)
        typer.echo(
            f"{folder}: {len(drift.new)} new, {len(drift.changed)} changed, "
            f"{len(drift.retired)} retired, {drift.unchanged} unchanged"
        )
        for label, ids in (
            ("new", drift.new),
            ("changed", drift.changed),
            ("retired", drift.retired),
        ):
            for item_id in ids:
                typer.echo(f"  {label}: {item_id}")
        open_gaps = sum(g.open for f in read.features for g in f.gaps)
        if open_gaps:
            typer.echo(f"  {open_gaps} open gaps: ask before writing tests that depend on them")
        if not dry_run:
            state.save(path, state.updated(read, known))
    raise typer.Exit(1 if failed else 0)
