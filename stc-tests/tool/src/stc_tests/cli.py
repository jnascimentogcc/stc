"""The `stc-tests` command line."""

from pathlib import Path
from typing import Annotated

import typer

from stc_tests import __version__, scenarios, settings, state
from stc_tests.catalog import CatalogError, read_catalog
from stc_tests.contract import ContractError, UnsupportedVersion
from stc_tests.llm import JsonLLM, LLMError, Usage, make_llm
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


CatalogOption = Annotated[
    Path | None,
    typer.Option(
        "--catalog", help="catalog/actions.json of the test repository (STC_TESTS_CATALOG)."
    ),
]


@app.command(name="scenarios")
def write_scenarios(
    application: Annotated[str | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all")] = False,
    specs: SpecsOption = None,
    output: OutputOption = None,
    catalog: CatalogOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite a feature file that already exists.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the work, call no model.")
    ] = False,
) -> None:
    """Draft Gherkin scenarios from the specification, bound to the action catalog.

    Drafts go to OUTPUT/<application>/features/<feature>.feature. A file that already exists is
    never overwritten: the new draft goes to <feature>.proposed.feature unless --force is given."""
    root, out = settings.specs_dir(specs), settings.output_dir(output)
    catalog_path = catalog or settings.catalog_path()
    if catalog_path is None:
        typer.echo("Give --catalog or set STC_TESTS_CATALOG.", err=True)
        raise typer.Exit(2)
    try:
        actions = read_catalog(catalog_path)
    except CatalogError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    llm = None if dry_run else _llm()
    failed = False
    total = Usage()
    for folder in _targets(root, application, all_):
        try:
            read = read_application(root, folder)
        except (ContractError, KeyError) as error:
            typer.echo(f"{folder}: REFUSED: {error}", err=True)
            failed = True
            continue
        hashes = {i.id: i.text_sha256 for i in read.items()}
        cases = scenarios.Cases(out / folder / ".stc-tests" / "cases.json")
        for feature in read.features:
            if llm is None:
                prompt = scenarios.build_prompt(feature, actions)
                typer.echo(f"{folder}/{feature.slug}: prompt of {len(prompt)} characters (dry run)")
                continue
            try:
                result = scenarios.draft_feature(feature, actions, llm, cases, hashes)
            except LLMError as error:
                typer.echo(f"{folder}/{feature.slug}: FAILED: {error}", err=True)
                failed = True
                continue
            total += result.usage
            target = out / folder / "features" / f"{feature.slug}.feature"
            if result.drafts:
                if target.exists() and not force:
                    target = target.with_suffix(".proposed.feature")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(scenarios.render(feature, result.drafts), encoding="utf-8")
            typer.echo(
                f"{folder}/{feature.slug}: {len(result.drafts)} drafted"
                + (f" in {target}" if result.drafts else "")
                + f", {len(result.rejected)} rejected, {len(result.missing)} missing actions"
            )
            for line in result.rejected:
                typer.echo(f"  rejected: {line}")
            for line in result.missing:
                typer.echo(f"  missing action: {line}")
        if llm is not None:
            cases.save()
            review = cases.to_review(hashes)
            if review:
                typer.echo(f"  to review (source changed): {', '.join(review)}")
    if llm is not None:
        typer.echo(f"tokens: {total.input_tokens} in, {total.output_tokens} out")
    raise typer.Exit(1 if failed else 0)


def _llm() -> JsonLLM:
    try:
        return make_llm()
    except LLMError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
