"""Migration specifications (SPC-*): order and dependencies come from facts, the stack from a file,
and the pages are written with the same verified-evidence machinery as the documentation."""

import asyncio
from pathlib import Path

import psycopg
import pytest

import test_docs
from stc.docs.facts import AppFacts, Call, Loc, Owner, TableUse
from stc.docs.features import Feature, detect_features
from stc.docs.generate import SPEC_SYSTEM_PROMPT, SYSTEM_PROMPT, build_messages
from stc.docs.model import Page, Part
from stc.docs.store import generate_and_store, prepare, read_meta
from stc.spec.plan import dependencies, migration_order, spec_planner
from stc.spec.stack import DEFAULT_STACK, StackError, load_stack
from test_docs import SAMPLE, FakeLLM, reply

# --- pure ----------------------------------------------------------------------------------------


def _feature(slug: str, size_programs: int = 1) -> Feature:
    return Feature(
        slug, slug.upper(), "program", slug, Loc("a.cbl", 1),
        programs=[slug.upper()], files=["a.cbl"] * size_programs,
    )  # fmt: skip


def _facts_with_data() -> AppFacts:
    facts = AppFacts("app", "app", "v1")
    loc = Loc("a.cbl", 1)
    facts.tables = [
        TableUse(Owner("program", "LOAD"), "T", "INSERT", loc),
        TableUse(Owner("program", "REPORT"), "T", "SELECT", loc),
        TableUse(Owner("program", "REPORT"), "U", "INSERT", loc),
        TableUse(Owner("program", "AUDIT"), "U", "SELECT", loc),
    ]
    facts.calls = [Call(Owner("program", "LOAD"), "X", False, loc)]
    return facts


def test_a_feature_depends_on_the_ones_that_write_what_it_reads() -> None:
    features = [_feature("report"), _feature("load"), _feature("audit")]
    deps = dependencies(_facts_with_data(), features)
    assert deps == {"report": ["load"], "load": [], "audit": ["report"]}


def test_migration_order_puts_dependencies_first_and_flags_cycles() -> None:
    features = [_feature("audit"), _feature("report"), _feature("load")]
    deps = {"audit": ["report"], "report": ["load"], "load": []}
    ordered, cyclic = migration_order(features, deps)
    assert [f.slug for f in ordered] == ["load", "report", "audit"] and not cyclic

    loop = {"audit": ["report"], "report": ["audit"], "load": []}
    ordered, cyclic = migration_order(features, loop)
    assert [f.slug for f in ordered] == ["load", "audit", "report"]
    assert cyclic == {"audit", "report"}


def test_detected_features_still_work_with_the_spec_helpers() -> None:
    facts = _facts_with_data()
    facts.programs["LOAD"] = Loc("a.cbl", 1)
    facts.file_of_program["LOAD"] = "a.cbl"
    assert [f.entry for f in detect_features(facts, 5)[0]] == ["LOAD"]


def test_stack_defaults_and_reads_a_file(tmp_path: Path) -> None:
    assert load_stack(None) == DEFAULT_STACK and "Spring Boot" in DEFAULT_STACK
    stack = tmp_path / "stack.md"
    stack.write_text("  Kotlin and Ktor.\n", encoding="utf-8")
    assert load_stack(stack) == "Kotlin and Ktor."
    with pytest.raises(StackError):
        load_stack(tmp_path / "missing.md")
    stack.write_text("   \n", encoding="utf-8")
    with pytest.raises(StackError):
        load_stack(stack)


def test_spec_parts_get_the_spec_prompt_and_the_stack_only_where_it_matters() -> None:
    from stc.catalog.context import SectionContext

    page = Page("features/x.md", "X", "Features")
    plain = Part("spec-rules", "Rules", SectionContext([], ["a fact"]))
    targeted = Part("spec-design", "Design", SectionContext([], ["a fact"]), "paragraphs", "Kotlin")
    docs_part = Part("summary", "Summary", SectionContext([], ["a fact"]))
    assert "<target-stack>" not in build_messages(page, plain, "English", "", "app")[1].content
    design = build_messages(page, targeted, "English", "", "app")
    assert "<target-stack>\nKotlin\n</target-stack>" in design[1].content
    assert design[0].content.startswith(SPEC_SYSTEM_PROMPT[:40])
    assert build_messages(page, docs_part, "English", "", "app")[0].content.startswith(
        SYSTEM_PROMPT[:40]
    )


# --- with a database -------------------------------------------------------------------------

# The COBOL sample indexed in a throwaway database (the fixture of the documentation tests).
conn = pytest.fixture(test_docs.conn.__wrapped__)  # type: ignore[attr-defined]


def run(conn: psycopg.Connection, base: Path, llm: FakeLLM | None, stack: str = DEFAULT_STACK):  # type: ignore[no-untyped-def]
    prepared = prepare(
        conn, "loans-core", base,
        provider=llm.provider if llm else "x", model=llm.model if llm else "y",
        language="English", static_only=llm is None, planner=spec_planner(stack),
    )  # fmt: skip
    outcome = asyncio.run(generate_and_store(llm, prepared, language="English"))
    return prepared, outcome


def test_spec_has_an_index_and_one_page_per_feature(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    llm = FakeLLM(reply(("The system must run.", "high", [("src/LNBATCH.cbl", 24, 24)])))
    _, outcome = run(conn, tmp_path, llm)
    assert outcome.status == "complete" and outcome.failed_parts == []
    directory = tmp_path / "loans-core"
    meta = read_meta(directory)
    assert meta is not None
    assert meta.pages[0].path == "index.md"
    assert {p.path for p in meta.pages[1:]} == {
        "CLAUDE.md",
        "features/job-lnnight.md",
        "features/program-lnsimul.md",
    }
    index = (directory / "index.md").read_text(encoding="utf-8")
    assert "## Target stack" in index and "Spring Boot" in index
    assert "| 1 | [" in index and "Features in migration order" in index
    project = (directory / "CLAUDE.md").read_text(encoding="utf-8")
    assert project.startswith("# Migration of loans-core") and "Generated by STC" not in project
    assert "`read_file` with `application` = `loans-core`" in project
    assert "stc:manual" not in project and "1. `features/job-lnnight.md`" in project
    assert "Reading the legacy code" in index and "CLAUDE.md" in index
    page = (directory / "features" / "job-lnnight.md").read_text(encoding="utf-8")
    for title in (
        "Functional requirements",
        "Business rules",
        "Acceptance scenarios",
        "Implementation plan",
    ):
        assert f"## {title}" in page
    assert "The system must run. (evidence: `src/LNBATCH.cbl:24-24`)" in page
    assert "slice 1 of 2" in page or "slice 2 of 2" in page
    parts = llm.parts()
    assert "features/job-lnnight:spec-requirements" in parts and "index:spec-strategy" in parts
    # the target stack reaches the design part and not the plain description of the rules
    sent = {key: messages[1].content for key, messages in llm.calls}
    assert "<target-stack>" in sent["features/job-lnnight:spec-design"]
    assert "<target-stack>" not in sent["features/job-lnnight:spec-rules"]


def test_changing_the_stack_rewrites_the_specifications(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    run(conn, tmp_path, FakeLLM())
    again = FakeLLM()
    prepared, _ = run(conn, tmp_path, again)
    assert prepared.up_to_date is False or again.calls == []  # nothing changed: no model call
    assert again.calls == []
    other = FakeLLM()
    run(conn, tmp_path, other, stack="Kotlin and Ktor.")
    assert other.calls  # the stack is an input of every page


def test_static_spec_needs_no_model(conn: psycopg.Connection, tmp_path: Path) -> None:
    _, outcome = run(conn, tmp_path, None)
    assert outcome.status == "static" and outcome.claims == 0
    text = (tmp_path / "loans-core" / "index.md").read_text(encoding="utf-8")
    assert "Features in migration order" in text and "Not generated yet" in text
    assert SAMPLE.is_dir()
