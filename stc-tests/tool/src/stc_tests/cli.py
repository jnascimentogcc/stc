"""The `stc-tests` command line."""

from pathlib import Path
from typing import Annotated

import typer

from stc_tests import __version__, approval, mapping, scenarios, settings, state
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
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            envvar="STC_ENV_FILE",
            help="Read settings (STC_TESTS_LLM_MODEL, OPENAI_API_KEY, ...) from this file.",
        ),
    ] = None,
) -> None:
    """Prepare tests from the specifications STC writes."""
    if env_file is not None:
        if not env_file.is_file():
            typer.echo(f"{env_file}: no such file", err=True)
            raise typer.Exit(2)
        settings.load_env_file(env_file)


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
                target.write_text(
                    scenarios.render(feature, result.drafts, cases.approved_ids()), encoding="utf-8"
                )
            typer.echo(
                f"{folder}/{feature.slug}: {len(result.drafts)} drafted"
                + (f" in {target}" if result.drafts else "")
                + f", {len(result.rejected)} rejected, {len(result.missing)} missing actions"
            )
            for line in result.rejected:
                typer.echo(f"  rejected: {line}")
            for line in result.skipped:
                typer.echo(f"  skipped: {line}")
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


def _case_files(output: Path | None, application: str) -> tuple[Path, scenarios.Cases]:
    base = settings.output_dir(output) / application
    cases_path = base / ".stc-tests" / "cases.json"
    if not cases_path.is_file():
        typer.echo(f"{application}: no cases yet in {base} (run `scenarios` first).", err=True)
        raise typer.Exit(1)
    return base / "features", scenarios.Cases(cases_path)


@app.command()
def review(
    application: Annotated[str, typer.Argument()],
    specs: SpecsOption = None,
    output: OutputOption = None,
) -> None:
    """List the cases with their status; with the specs, flag those whose source changed."""
    _, cases = _case_files(output, application)
    changed: set[str] = set()
    root = settings.specs_dir(specs)
    if (root / application / "_specs.json").is_file():
        read = read_application(root, application)
        changed = set(cases.to_review({i.id: i.text_sha256 for i in read.items()}))
    counts: dict[str, int] = {}
    for entry in cases.entries():
        status = "to_review" if entry["id"] in changed else str(entry.get("status", "draft"))
        counts[status] = counts.get(status, 0) + 1
        who = f" by {entry['approved_by']}" if status == "approved" else ""
        typer.echo(f"{entry['id']}  {status}{who}  {entry['title']}")
    typer.echo(", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no cases")


@app.command()
def approve(
    application: Annotated[str, typer.Argument()],
    case_ids: Annotated[list[str] | None, typer.Argument(help="Case IDs, e.g. TC-0001.")] = None,
    by: Annotated[str, typer.Option("--by", help="Who approves (a person).")] = "",
    all_drafts: Annotated[bool, typer.Option("--all-drafts", help="Every case in draft.")] = False,
    output: OutputOption = None,
) -> None:
    """Record the approval of drafted cases and mark them @status:approved in the feature file."""
    if not by.strip():
        typer.echo("Say who approves with --by.", err=True)
        raise typer.Exit(2)
    features, cases = _case_files(output, application)
    ids = list(case_ids or [])
    if all_drafts:
        ids += [e["id"] for e in cases.entries() if e.get("status", "draft") == "draft"]
    if not ids:
        typer.echo("Give case IDs or --all-drafts.", err=True)
        raise typer.Exit(2)
    try:
        for line in approval.approve(features, cases, ids, by.strip()):
            typer.echo(line)
    except approval.ApprovalError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    cases.save()


@app.command()
def reject(
    application: Annotated[str, typer.Argument()],
    case_ids: Annotated[list[str], typer.Argument(help="Case IDs, e.g. TC-0003.")],
    by: Annotated[str, typer.Option("--by", help="Who rejects (a person).")] = "",
    reason: Annotated[str, typer.Option("--reason", help="Why: kept for the metrics.")] = "",
    output: OutputOption = None,
) -> None:
    """Reject cases: remove them from the feature file and do not propose them again."""
    if not by.strip() or not reason.strip():
        typer.echo("Say who rejects (--by) and why (--reason).", err=True)
        raise typer.Exit(2)
    features, cases = _case_files(output, application)
    try:
        for line in approval.reject(features, cases, case_ids, by.strip(), reason.strip()):
            typer.echo(line)
    except approval.ApprovalError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    cases.save()


@app.command(name="mapping")
def draft_mappings(
    application: Annotated[str | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all")] = False,
    specs: SpecsOption = None,
    output: OutputOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite a mapping file that already exists.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the work, call no model.")
    ] = False,
) -> None:
    """Draft the interface mapping of each program, for the migration team to settle and approve.

    Files go to OUTPUT/<application>/mapping/<program>.mapping.yaml. An existing file is never
    overwritten: the new draft goes to <program>.proposed.mapping.yaml unless --force is given."""
    root, out = settings.specs_dir(specs), settings.output_dir(output)
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
        for feature in read.features:
            if llm is None:
                prompt = mapping.build_prompt(feature)
                typer.echo(f"{folder}/{feature.slug}: prompt of {len(prompt)} characters (dry run)")
                continue
            try:
                document, used = mapping.draft_mapping(read.name, feature, llm)
            except (LLMError, ContractError) as error:
                typer.echo(f"{folder}/{feature.slug}: FAILED: {error}", err=True)
                failed = True
                continue
            total += used
            target = out / folder / "mapping" / f"{feature.slug}.mapping.yaml"
            if target.exists() and not force:
                target = target.with_name(f"{feature.slug}.proposed.mapping.yaml")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mapping.dump(document), encoding="utf-8")
            questions = len(document["open_questions"])
            typer.echo(f"{folder}/{feature.slug}: drafted in {target}, {questions} open questions")
    if llm is not None:
        typer.echo(f"tokens: {total.input_tokens} in, {total.output_tokens} out")
    raise typer.Exit(1 if failed else 0)


@app.command(name="approve-mapping")
def approve_mapping(
    application: Annotated[str, typer.Argument()],
    program: Annotated[str, typer.Argument(help="The feature slug, e.g. program-search-example.")],
    by: Annotated[str, typer.Option("--by", help="Who approves (a person).")] = "",
    output: OutputOption = None,
) -> None:
    """Approve a mapping once its open questions are settled (edit the file to remove them)."""
    if not by.strip():
        typer.echo("Say who approves with --by.", err=True)
        raise typer.Exit(2)
    path = settings.output_dir(output) / application / "mapping" / f"{program}.mapping.yaml"
    try:
        document = mapping.load_mapping(path)
        if document["status"] == "approved":
            typer.echo(f"{path}: already approved by {document['approved_by']}")
            raise typer.Exit(0)
        if document["open_questions"]:
            typer.echo(f"{path}: settle the open questions first:", err=True)
            for question in document["open_questions"]:
                typer.echo(f"  - {question}", err=True)
            raise typer.Exit(1)
        text = mapping.approve_text(path.read_text(encoding="utf-8"), by.strip())
        path.write_text(text, encoding="utf-8")
        mapping.load_mapping(path)  # what was written must still follow the contract
    except ContractError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"{path}: approved by {by.strip()}")


def _llm() -> JsonLLM:
    try:
        return make_llm()
    except LLMError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
