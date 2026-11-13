"""Documentation (DOC-01 to DOC-13): facts, features, diagrams, pages, incremental generation with
a fake model, corrections that survive, and the read-only API. The DB tests index the COBOL sample
Root; the pure ones need no database."""

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from stc.api.app import create_app
from stc.catalog.context import SectionContext
from stc.catalog.model import Claim, Evidence, SectionResult
from stc.config import Settings
from stc.db.migrate import migrate
from stc.docs import diagrams
from stc.docs.ddl import parse_tables
from stc.docs.facts import (
    AppFacts,
    Call,
    Loc,
    Owner,
    Perform,
    Span,
    TableUse,
    build_facts,
    slugify,
)
from stc.docs.features import detect_features
from stc.docs.model import Page, Part, Static
from stc.docs.render import RenderMeta, parse_manual_blocks, render_claims, render_page
from stc.docs.store import generate_and_store, prepare, read_meta
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import host_aliases, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, rebuild_relations
from stc.providers import LLMResponse, Message, ProviderError, ToolSpec, Usage

SAMPLE = Path(__file__).parents[1] / "examples" / "cobol-bank-root"


# --- pure ----------------------------------------------------------------------------------------


def test_labels_cannot_break_out_of_mermaid_quotes() -> None:
    text = diagrams.label('A"]\n  click n0 call evil() <script>')
    assert '"' not in text and "\n" not in text and "<" not in text and "[" not in text
    assert len(diagrams.label("x" * 500)) <= diagrams.MAX_LABEL


def test_flow_uses_generated_ids_and_truncates_with_a_count() -> None:
    flow = diagrams.Flow()
    for i in range(60):
        flow.node(f"k{i}", f'NAME"{i}')
        if i:
            flow.edge(f"k{i - 1}", f"k{i}", "CALL")
    text, left_out = flow.render(max_nodes=10)
    assert left_out == 50
    assert text.startswith("```mermaid\nflowchart LR") and text.endswith("```")
    assert 'NAME"' not in text and "n10[" not in text and "n9[" in text
    assert text.count("-->") == 9  # only edges between kept nodes


def _facts() -> AppFacts:
    facts = AppFacts("app", "app", "v1")
    for name in ("MAIN", "SUB", "ORPHAN", "LEAF"):
        facts.programs[name] = Loc(f"src/{name}.cbl", 2)
        facts.file_of_program[name] = f"src/{name}.cbl"
    facts.jobs["NIGHT"] = Loc("jcl/NIGHT.jcl", 1)
    facts.file_of_job["NIGHT"] = "jcl/NIGHT.jcl"
    loc = Loc("jcl/NIGHT.jcl", 5)
    facts.calls = [
        Call(Owner("job", "NIGHT", "S1"), "MAIN", False, loc),
        Call(Owner("job", "NIGHT", "S2"), "SORT", False, loc),
        Call(Owner("program", "MAIN"), "SUB", False, loc),
        Call(Owner("program", "ORPHAN"), "LEAF", False, loc),
    ]
    facts.tables = [
        TableUse(Owner("program", "SUB"), "CONTRACT", "READS", loc),
        TableUse(Owner("program", "SUB"), "CONTRACT", "WRITES", loc),
    ]
    facts.paragraphs["MAIN"] = [
        Span("0000-MAIN", "paragraph", 5, 9),
        Span("1000-A", "paragraph", 10, 12),
    ]
    facts.performs = [Perform("MAIN", "0000-MAIN", "1000-A", loc)]
    return facts


def test_features_come_from_entry_points_not_from_the_model() -> None:
    documented, skipped = detect_features(_facts(), limit=10)
    by_entry = {f.entry: f for f in documented}
    assert set(by_entry) == {"NIGHT", "ORPHAN"}  # SUB and LEAF are called, MAIN is run by the job
    assert by_entry["NIGHT"].programs == [
        "MAIN",
        "SUB",
    ]  # reached through the job, SORT is external
    assert by_entry["ORPHAN"].programs == ["ORPHAN", "LEAF"]
    assert skipped == []
    assert [f.slug for f in documented] == ["job-night", "program-orphan"]


def test_features_beyond_the_limit_are_reported_not_dropped() -> None:
    documented, skipped = detect_features(_facts(), limit=1)
    assert len(documented) == 1 and len(skipped) == 1
    assert slugify("Job: LN NIGHT!") == "job-ln-night"


def test_feature_diagram_shows_calls_and_data_with_real_names_only() -> None:
    facts = _facts()
    feature = detect_features(facts, 10)[0][0]
    text = diagrams.feature_diagram(facts, feature) or ""
    for expected in ("NIGHT", "MAIN", "SUB", "CONTRACT", "SORT"):
        assert expected in text
    assert "ORPHAN" not in text  # another feature
    assert "classDef external" in text  # SORT is not in the application
    assert text.count("CONTRACT") == 1  # one node, a read edge and a write edge
    assert "reads" in text and "writes" in text


def test_perform_diagram_only_has_paragraphs_that_perform_something() -> None:
    text = diagrams.perform_diagram(_facts(), "MAIN") or ""
    assert "0000-MAIN" in text and "1000-A" in text and "PERFORM" in text
    assert diagrams.perform_diagram(_facts(), "SUB") is None


def test_parse_tables_columns_keys_and_references() -> None:
    sql = """
-- customers
CREATE TABLE CUSTOMER (
    CUST_ID  DECIMAL(10) NOT NULL PRIMARY KEY,
    NAME     VARCHAR(40)
);
CREATE TABLE IF NOT EXISTS "ORDERS" (
    ID        INT NOT NULL,
    CUST_ID   DECIMAL(10) NOT NULL REFERENCES CUSTOMER,
    NOTE      TEXT DEFAULT 'a,b',
    PRIMARY KEY (ID),
    CONSTRAINT fk FOREIGN KEY (CUST_ID) REFERENCES CUSTOMER (CUST_ID)
);
"""
    customer, orders = parse_tables("ddl/t.sql", sql)
    assert customer.name == "CUSTOMER" and customer.start_line == 3 and customer.end_line == 6
    assert [(c.name, c.type, c.primary) for c in customer.columns] == [
        ("CUST_ID", "DECIMAL(10)", True),
        ("NAME", "VARCHAR(40)", False),
    ]
    assert orders.name == "ORDERS"
    by_name = {c.name: c for c in orders.columns}
    assert by_name["ID"].primary and by_name["CUST_ID"].references == "CUSTOMER"
    assert by_name["NOTE"].type == "TEXT" and len(orders.columns) == 3


def test_er_diagram_skips_relationships_to_unknown_tables() -> None:
    tables = parse_tables(
        "t.sql",
        "CREATE TABLE A (ID INT PRIMARY KEY, B_ID INT REFERENCES B, C_ID INT REFERENCES A);",
    )
    text = diagrams.er_diagram(tables) or ""
    assert text.startswith("```mermaid\nerDiagram") and "A }o--|| A" in text
    assert "|| B" not in text
    assert diagrams.er_diagram([]) is None


def _result(*claims: Claim, gaps: Sequence[str] = ()) -> SectionResult:
    return SectionResult("x", list(claims), list(gaps))


def test_claims_render_as_paragraphs_or_numbered_steps_with_evidence_and_gaps() -> None:
    claim = Claim("It runs nightly.", "medium", [Evidence("a.cbl", 3, 9), Evidence("b.jcl", 1, 1)])
    text = render_claims(
        _result(claim, Claim("Done.", "high", [Evidence("a.cbl", 1, 1)]), gaps=["G"]), "paragraphs"
    )
    assert "It runs nightly. _(medium confidence)_ (evidence: `a.cbl:3-9`, `b.jcl:1-1`)" in text
    assert "Done. (evidence: `a.cbl:1-1`)" in text and "**Gaps**\n\n- G" in text
    steps = render_claims(
        _result(claim, Claim("Then.", "high", [Evidence("a.cbl", 1, 1)])), "steps"
    )
    assert steps.startswith("1. It runs nightly.") and "\n2. Then." in steps
    assert render_claims(_result(), "steps") == "_Nothing could be determined._"


def test_page_markdown_keeps_manual_blocks_and_notes_round_trip() -> None:
    page = Page(
        "features/job-x.md",
        "Batch job X",
        "Features",
        [Static("body"), Part("flow", "How it works", SectionContext(), "steps")],
    )
    meta = RenderMeta("v1", "2026-01-01 00:00 UTC", "p", "m", "en")
    manual = {"features/job-x:flow": "Runs at 02:00.", "features/job-x:notes": "Owner: ops"}
    text = render_page(page, meta, "complete", {"flow": "1. step"}, manual)
    assert "## Maintainer notes" in text and "Owner: ops" in text
    assert parse_manual_blocks(text) == manual
    empty = render_page(page, meta, "complete", {}, {})
    assert (
        "## Maintainer notes" not in empty
        and "stc:manual:begin section=features/job-x:notes" in empty
    )
    assert parse_manual_blocks(empty) == {}


# --- with a database -------------------------------------------------------------------------


def reply(*claims: tuple[str, str, list[tuple[str, int, int]]], gaps: Sequence[str] = ()) -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "text": text,
                    "confidence": confidence,
                    "evidence": [{"file": f, "start_line": a, "end_line": b} for f, a, b in ev],
                }
                for text, confidence, ev in claims
            ],
            "gaps": list(gaps),
        }
    )


GOOD = reply(("It does the work.", "high", [("src/LNBATCH.cbl", 24, 24)]))


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, default: str = GOOD) -> None:
        self.default = default
        self.calls: list[tuple[str, list[Message]]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        match = re.search(r"PART: (\S+)", messages[1].content)
        assert match is not None and response_schema is not None
        self.calls.append((match.group(1), list(messages)))
        if self.default == "RAISE":
            raise ProviderError("fake", "boom", retryable=False)
        return LLMResponse(text=self.default, usage=Usage(10, 5))

    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def parts(self) -> list[str]:
        return sorted(p for p, _ in self.calls)


@pytest.fixture
def conn(temp_db_url: str):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as connection:
        sync_registry(connection, scan_root(SAMPLE))
        chunk_pending(connection, SAMPLE)
        extract_pending(connection, SAMPLE)
        rebuild_relations(connection, host_aliases(SAMPLE))
        connection.execute("UPDATE applications SET indexed_version = 'v1'")
        yield connection


def run(conn: psycopg.Connection, docs: Path, llm: FakeLLM | None, **kwargs: Any):  # type: ignore[no-untyped-def]
    prepared = prepare(
        conn, "loans-core", docs,
        provider=llm.provider if llm else "x", model=llm.model if llm else "y",
        language="English", static_only=llm is None, **kwargs,
    )  # fmt: skip
    outcome = asyncio.run(generate_and_store(llm, prepared, language="English"))
    return prepared, outcome


def test_facts_of_the_cobol_sample(conn: psycopg.Connection) -> None:
    app_id = conn.execute("SELECT id FROM applications WHERE name = 'loans-core'").fetchone()[0]  # type: ignore[index]
    facts = build_facts(conn, app_id, "loans-core")
    assert {"LNBATCH", "LNSIMUL", "LNELIG", "LNCALC", "LNRATE"} <= set(facts.programs)
    assert list(facts.jobs) == ["LNNIGHT"] and [s.name for s in facts.steps["LNNIGHT"]] == [
        "STEP010",
        "STEP020",
    ]
    runs = {(c.caller.step, c.target) for c in facts.job_calls("LNNIGHT")}
    assert ("STEP010", "LNBATCH") in runs and ("STEP020", "SORT") in runs
    assert {c.target for c in facts.program_calls("LNSIMUL")} == {"LNELIG", "LNRATE"}
    assert any(
        u.owner.name == "LNBATCH" and u.table == "CONTRACT" and u.writes for u in facts.tables
    )
    written = {d.dataset for d in facts.datasets if d.writes}
    assert {"BANK.LOANS.LATE.DAILY", "BANK.LOANS.LATE.SORTED"} <= written
    assert any(p.program == "LNBATCH" and p.source == "0000-MAIN" for p in facts.performs)
    assert {r.other for r in facts.relations} == {"collections"}


def test_plan_has_a_page_per_feature_and_only_pages_with_a_subject(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    prepared = prepare(conn, "loans-core", tmp_path, provider="p", model="m", language="en")
    paths = [p.path for p in prepared.plan.pages]
    assert paths[:3] == ["index.md", "architecture.md", "features/index.md"]
    assert {"features/job-lnnight.md", "features/program-lnsimul.md"} <= set(paths)
    assert {"data-model.md", "interfaces.md", "dependencies.md"} <= set(paths)
    feature = next(p for p in prepared.plan.pages if p.path == "features/job-lnnight.md")
    assert [p.id for p in feature.parts] == ["summary", "flow", "rules"]
    assert all(not part.context.empty for part in feature.parts)
    assert any("LNBATCH" in item.text for part in feature.parts for item in part.context.items)
    assert prepared.estimate_input_tokens > 0 and len(prepared.stale) == len(paths)


def test_generation_writes_navigable_pages_with_verified_evidence(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    llm = FakeLLM()
    _, outcome = run(conn, tmp_path, llm)
    assert outcome.status == "complete" and outcome.claims > 0 and outcome.failed_parts == []
    directory = tmp_path / "loans-core"
    meta = read_meta(directory)
    assert meta is not None and meta.application == "loans-core" and meta.code_version == "v1"
    assert [p.path for p in meta.pages][:2] == ["index.md", "architecture.md"]
    assert all((directory / p.path).is_file() for p in meta.pages)
    page = (directory / "features" / "job-lnnight.md").read_text(encoding="utf-8")
    assert "It does the work. (evidence: `src/LNBATCH.cbl:24-24`)" in page
    assert "```mermaid\nflowchart LR" in page and "[all features](index.md)" in page
    assert "features/job-lnnight:flow" in llm.parts()[0] or any(
        p == "features/job-lnnight:flow" for p in llm.parts()
    )
    assert (directory / "index.md").read_text(encoding="utf-8").count("](") >= 5  # the page map


def test_a_statement_without_real_evidence_is_dropped_and_reported(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    bad = reply(
        ("Invented.", "high", [("src/NOPE.cbl", 1, 2)]),
        ("Out of range.", "high", [("src/LNBATCH.cbl", 900, 901)]),
        ("Real.", "high", [("src/LNBATCH.cbl", 1, 3)]),
    )
    run(conn, tmp_path, FakeLLM(bad))
    page = (tmp_path / "loans-core" / "features" / "job-lnnight.md").read_text(encoding="utf-8")
    assert "Real." in page and "Invented." not in page and "Out of range." not in page
    assert "2 statement(s) were discarded" in page


def test_second_run_calls_no_model_and_changes_nothing(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    run(conn, tmp_path, FakeLLM())
    before = {p: p.read_bytes() for p in (tmp_path / "loans-core").rglob("*") if p.is_file()}
    again = FakeLLM()
    prepared, outcome = run(conn, tmp_path, again)
    assert prepared.up_to_date and outcome.pages_written == 0 and again.calls == []
    assert before == {
        p: p.read_bytes() for p in (tmp_path / "loans-core").rglob("*") if p.is_file()
    }


def test_changing_the_model_or_forcing_regenerates_everything(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    run(conn, tmp_path, FakeLLM())
    forced = FakeLLM()
    _, outcome = run(conn, tmp_path, forced, force=True)
    assert outcome.pages_written == outcome.pages_total and forced.calls
    other = FakeLLM()
    other.model = "another"
    prepared, _ = run(conn, tmp_path, other)
    assert prepared.stale  # the model is part of every page's inputs


def test_only_the_page_whose_code_changed_is_generated_again(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    run(conn, tmp_path, FakeLLM())
    conn.execute(
        "UPDATE chunks SET content = content || ' ' WHERE file_id IN"
        " (SELECT id FROM files WHERE path = 'src/LNSIMUL.cbl') AND kind = 'paragraph'"
    )
    llm = FakeLLM()
    prepared, outcome = run(conn, tmp_path, llm)
    stale = {p.path for p in prepared.stale}
    assert "features/program-lnsimul.md" in stale
    # What does not depend on that program is not written again, nor sent to the model.
    assert not stale & {"features/job-lnnight.md", "data-model.md", "configuration.md"}
    assert outcome.pages_written == len(stale) < outcome.pages_total
    assert {p.split(":")[0] for p in llm.parts()} == {p.removesuffix(".md") for p in stale}


def test_manual_corrections_are_kept_and_given_to_the_model(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    run(conn, tmp_path, FakeLLM())
    page = tmp_path / "loans-core" / "features" / "job-lnnight.md"
    text = page.read_text(encoding="utf-8")
    marker = "<!-- stc:manual:begin section=features/job-lnnight:rules -->\n"
    page.write_text(text.replace(marker, marker + "Late fee is 2%, not 3%.\n"), encoding="utf-8")
    llm = FakeLLM()
    prepared, _ = run(conn, tmp_path, llm)
    assert [p.path for p in prepared.stale] == ["features/job-lnnight.md"]
    assert llm.parts() == [
        "features/job-lnnight:flow",
        "features/job-lnnight:rules",
        "features/job-lnnight:summary",
    ]
    sent = next(m for p, m in llm.calls if p.endswith(":rules"))[1].content
    assert "Late fee is 2%, not 3%." in sent and "<corrections>" in sent
    other = next(m for p, m in llm.calls if p.endswith(":flow"))[1].content
    assert "Late fee" not in other  # a correction is for its own part only
    assert "Late fee is 2%, not 3%." in page.read_text(encoding="utf-8")


def test_notes_of_pages_that_no_longer_exist_are_kept_as_orphans(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    gone = tmp_path / "loans-core" / "features"
    gone.mkdir(parents=True)
    (gone / "job-gone.md").write_text(
        "<!-- stc:manual:begin section=features/job-gone:flow -->\nKeep me.\n<!-- stc:manual:end -->\n",
        encoding="utf-8",
    )
    run(conn, tmp_path, FakeLLM())
    orphans = (tmp_path / "loans-core" / "_orphaned-notes.md").read_text(encoding="utf-8")
    assert "Keep me." in orphans
    meta = read_meta(tmp_path / "loans-core")
    assert meta is not None and meta.pages[-1].path == "_orphaned-notes.md"
    run(conn, tmp_path, FakeLLM())  # stable on the next run
    assert "Keep me." in (tmp_path / "loans-core" / "_orphaned-notes.md").read_text(
        encoding="utf-8"
    )


def test_a_failing_model_marks_the_pages_partial_and_keeps_the_static_parts(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    _, outcome = run(conn, tmp_path, FakeLLM("RAISE"))
    assert outcome.status == "partial" and outcome.failed_parts
    page = (tmp_path / "loans-core" / "features" / "job-lnnight.md").read_text(encoding="utf-8")
    assert "Generation failed" in page and "```mermaid" in page
    ok = FakeLLM()
    prepared, _ = run(conn, tmp_path, ok)
    assert prepared.stale and ok.calls  # partial pages are retried


def test_static_only_needs_no_model_and_never_overwrites_model_text(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    _, outcome = run(conn, tmp_path, None)
    assert outcome.status == "static"
    page = tmp_path / "loans-core" / "features" / "job-lnnight.md"
    assert "Not generated yet" in page.read_text(encoding="utf-8")
    assert "```mermaid" in page.read_text(encoding="utf-8")
    again, _ = run(conn, tmp_path, None)
    assert again.up_to_date
    run(conn, tmp_path, FakeLLM())
    assert "It does the work." in page.read_text(encoding="utf-8")
    kept, _ = run(conn, tmp_path, None)
    assert kept.up_to_date and "It does the work." in page.read_text(encoding="utf-8")


def test_dry_run_estimate_counts_only_what_would_be_sent(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    first = prepare(conn, "loans-core", tmp_path, provider="p", model="m", language="en")
    assert first.estimate_output_tokens > 0
    run(conn, tmp_path, FakeLLM())
    again = prepare(
        conn, "loans-core", tmp_path, provider="fake", model="fake-model", language="English"
    )
    assert again.estimate_input_tokens == 0 and again.up_to_date


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(conn: psycopg.Connection, temp_db_url: str, tmp_path: Path) -> TestClient:
    run(conn, tmp_path / "docs", FakeLLM())
    settings = Settings(_env_file=None, db_url=temp_db_url, docs=tmp_path / "docs", root=SAMPLE)
    return TestClient(create_app(settings))


def test_api_lists_every_application_with_the_state_of_its_documentation(
    client: TestClient,
) -> None:
    rows = {r["name"]: r for r in client.get("/api/docs").json()}
    assert rows["collections"] == {"name": "collections", "has_docs": False}
    assert rows["loans-core"]["has_docs"] and rows["loans-core"]["status"] == "complete"
    assert rows["loans-core"]["outdated"] is False and rows["loans-core"]["pages"] >= 8


def test_api_flags_documentation_older_than_the_indexed_code(
    client: TestClient, conn: psycopg.Connection
) -> None:
    conn.execute("UPDATE applications SET indexed_version = 'v2' WHERE name = 'loans-core'")
    assert client.get("/api/docs/app", params={"application": "loans-core"}).json()["outdated"]


def test_api_serves_pages_in_navigation_order(client: TestClient) -> None:
    app = client.get("/api/docs/app", params={"application": "loans-core"}).json()
    assert app["pages"][0] == {"path": "index.md", "title": "loans-core", "group": "Overview"}
    page = client.get(
        "/api/docs/page", params={"application": "loans-core", "path": "features/job-lnnight.md"}
    )
    assert page.status_code == 200 and page.json()["content"].startswith("# Batch job LNNIGHT")


def test_api_only_serves_listed_pages_of_known_applications(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "docs" / "secret.md").write_text("no", encoding="utf-8")
    for application, path in (
        ("loans-core", "../secret.md"),
        ("loans-core", "secret.md"),
        ("loans-core", "_meta.json"),
        ("loans-core", "features/missing.md"),
        ("../", "index.md"),
        ("collections", "index.md"),
    ):
        response = client.get("/api/docs/page", params={"application": application, "path": path})
        assert response.status_code == 404, (application, path)
    assert client.get("/api/docs/app", params={"application": "nope"}).status_code == 404


def test_api_exports_one_markdown_file(client: TestClient) -> None:
    response = client.get("/api/docs/export", params={"application": "loans-core"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert 'filename="loans-core-documentation.md"' in response.headers["content-disposition"]
    body = response.text
    assert (
        body.index("# loans-core")
        < body.index("# Architecture")
        < body.index("# Batch job LNNIGHT")
    )
