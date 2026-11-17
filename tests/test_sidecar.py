"""The sidecar of a feature specification (SPC-10..SPC-12): IDs, matching between generations,
Given/When/Then, and the files the store writes. No database: the plan is built by hand."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from stc.catalog.context import SectionContext
from stc.catalog.model import Claim, Evidence, SectionResult
from stc.docs.model import Page, Part, Plan
from stc.docs.store import Prepared, generate_and_store
from stc.spec.sidecar import (
    build_sidecar,
    is_spec_page,
    parse_scenario,
    sidecar_path,
    text_sha256,
)
from test_docs import FakeLLM, reply

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
SIDECAR_SCHEMA = json.loads((CONTRACTS / "spec-sidecar.schema.json").read_text(encoding="utf-8"))
INDEX_SCHEMA = json.loads((CONTRACTS / "spec-index.schema.json").read_text(encoding="utf-8"))
FILES = {"src/A.cbl": 200}


def _part(part_id: str, style: str = "paragraphs") -> Part:
    return Part(part_id, part_id, SectionContext(facts=["a fact"]), style)  # type: ignore[arg-type]


def _page(*part_ids: str, slug: str = "job-x") -> Page:
    steps = ("spec-requirements", "spec-tasks")
    blocks: list[Any] = [_part(p, "steps" if p in steps else "paragraphs") for p in part_ids]
    return Page(f"features/{slug}.md", "Specification: Job X", "Features", blocks)


def _claim(text: str, start: int = 1, end: int = 5, confidence: str = "high") -> Claim:
    return Claim(text, confidence, [Evidence("src/A.cbl", start, end)])  # type: ignore[arg-type]


def _result(part: str, *claims: Claim, gaps: tuple[str, ...] = ()) -> SectionResult:
    return SectionResult(part, list(claims), list(gaps))


def _build(
    page: Page,
    results: dict[str, SectionResult],
    previous: dict[str, Any] | None = None,
    manual: dict[str, str] | None = None,
) -> dict[str, Any]:
    document = build_sidecar(
        application="app", page=page,
        results={page.key(k): v for k, v in results.items()},
        previous=previous, manual=manual or {}, code_version="v1",
        generated_at="2026-11-17T10:00:00Z", stack_sha256="0" * 64, inputs_sha256="1" * 64,
    )  # fmt: skip
    assert document is not None
    Draft202012Validator(SIDECAR_SCHEMA).validate(document)
    return document


def _ids(document: dict[str, Any], kind: str) -> list[str]:
    return [i["id"] for i in document["items"] if i["kind"] == kind]


# --- helpers ---------------------------------------------------------------------------------


def test_hash_ignores_case_and_spacing() -> None:
    assert text_sha256("The system  MUST run.") == text_sha256(" the system must run. ")
    assert text_sha256("a") != text_sha256("b")


def test_only_specification_feature_pages_have_a_sidecar() -> None:
    assert is_spec_page(_page("spec-requirements"))
    assert not is_spec_page(_page("summary", "flow"))
    assert not is_spec_page(Page("index.md", "Index", "Overview", [_part("spec-strategy")]))
    assert sidecar_path("features/job-x.md") == "features/job-x.spec.json"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Given the table has data, when the ids 0001, 0101 and 0499 are searched, then the "
            'result is "Item not found."',
            ("the table has data", "the ids 0001, 0101 and 0499 are searched",
             'the result is "Item not found."'),
        ),
        (
            "Given a loan of 100. When it is paid in full. Then the balance is 0.",
            ("a loan of 100", "it is paid in full", "the balance is 0"),
        ),
        ("When the file is empty, then no record is written.", (None, "the file is empty",
                                                               "no record is written")),
    ],
)  # fmt: skip
def test_scenarios_are_split_into_given_when_then(
    text: str, expected: tuple[str | None, str, str]
) -> None:
    scenario = parse_scenario(text)
    assert scenario is not None
    assert scenario["given"] == ([expected[0]] if expected[0] else [])
    assert scenario["when"] == [expected[1]] and scenario["then"] == [expected[2]]


@pytest.mark.parametrize(
    "text",
    [
        "The system rejects an empty id.",
        "Given a record, then it is shown.",
        "Then it is shown, when the id is found.",
        "When the id is found, then",
    ],
)
def test_text_that_is_not_a_scenario_is_not_split(text: str) -> None:
    assert parse_scenario(text) is None


# --- first generation ------------------------------------------------------------------------


def test_first_generation_numbers_each_kind_and_validates() -> None:
    page = _page("spec-requirements", "spec-rules", "spec-acceptance")
    document = _build(
        page,
        {
            "spec-requirements": _result(
                "spec-requirements", _claim("1. The system must read.", 1, 3),
                _claim("The system must write.", 4, 6),
            ),
            "spec-rules": _result("spec-rules", _claim("Interest is 2%.", 7, 8)),
            "spec-acceptance": _result(
                "spec-acceptance", _claim("Given a loan, when it is paid, then it is closed.", 9, 9)
            ),
        },
    )  # fmt: skip
    assert _ids(document, "requirement") == ["job-x/FR-01", "job-x/FR-02"]
    assert _ids(document, "rule") == ["job-x/BR-01"] and _ids(document, "acceptance") == [
        "job-x/AC-01"
    ]
    assert {i["status"] for i in document["items"]} == {"new"}
    first = document["items"][0]
    assert first["text"] == "The system must read."  # the model's own numbering is dropped
    assert first["citations"] == [{"file": "src/A.cbl", "start": 1, "end": 3}]
    assert document["feature"] == {"slug": "job-x", "title": "Job X"}
    assert document["counters"] == {"AC": 1, "BR": 1, "FR": 2}
    scenario = document["items"][3]["scenario"]
    assert scenario == {"given": ["a loan"], "when": ["it is paid"], "then": ["it is closed"]}


def test_nothing_is_written_when_nothing_ran_and_nothing_existed() -> None:
    page = _page("spec-requirements")
    assert (
        build_sidecar(
            application="app", page=page, results={}, previous=None, manual={}, code_version=None,
            generated_at="2026-11-17T10:00:00Z", stack_sha256="0" * 64, inputs_sha256="1" * 64,
        )
        is None
    )  # fmt: skip


def test_manual_blocks_are_listed_by_key_and_hash() -> None:
    page = _page("spec-requirements")
    manual = {page.key("spec-requirements"): "Always round up.", page.key("spec-rules"): "  "}
    document = _build(page, {"spec-requirements": _result("spec-requirements")}, manual=manual)
    assert document["manual_blocks"] == [
        {"key": "features/job-x:spec-requirements", "text_sha256": text_sha256("Always round up.")}
    ]


# --- IDs across generations (SPC-12) ---------------------------------------------------------


def _first() -> tuple[Page, dict[str, Any]]:
    page = _page("spec-requirements")
    claims = [
        _claim("The system must read.", 1, 3),
        _claim("The system must validate.", 4, 6),
        _claim("The system must write.", 7, 9),
    ]
    return page, _build(page, {"spec-requirements": _result("spec-requirements", *claims)})


def test_the_same_statements_keep_their_ids_in_any_order() -> None:
    page, before = _first()
    claims = [  # reordered
        _claim("The system must write.", 7, 9),
        _claim("The system must read.", 1, 3),
        _claim("The system must validate.", 4, 6),
    ]
    after = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, before)
    assert {i["text"]: i["id"] for i in after["items"]} == {
        i["text"]: i["id"] for i in before["items"]
    }
    assert {i["status"] for i in after["items"]} == {"unchanged"} and after["retired"] == []


def test_a_new_text_over_the_same_code_keeps_the_id_and_is_marked_changed() -> None:
    page, before = _first()
    claims = [
        _claim("The system must read the loan file.", 1, 3),
        _claim("The system must validate.", 4, 6),
        _claim("The system must write.", 7, 9),
    ]
    after = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, before)
    changed = [i for i in after["items"] if i["status"] == "changed"]
    assert [(i["id"], i["text"]) for i in changed] == [
        ("job-x/FR-01", "The system must read the loan file.")
    ]


def test_a_removed_statement_is_retired_and_its_number_is_never_reused() -> None:
    page, before = _first()
    claims = [_claim("The system must read.", 1, 3), _claim("The system must write.", 7, 9)]
    second = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, before)
    assert second["retired"] == [{"id": "job-x/FR-02", "retired_in": "v1"}]
    claims.append(_claim("The system must audit.", 20, 22))
    third = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, second)
    assert _ids(third, "requirement") == ["job-x/FR-01", "job-x/FR-03", "job-x/FR-04"]
    assert [r["id"] for r in third["retired"]] == ["job-x/FR-02"]  # and it stays retired
    new = [i for i in third["items"] if i["status"] == "new"]
    assert [i["id"] for i in new] == ["job-x/FR-04"]


def test_a_number_is_not_reused_even_when_the_last_statement_goes_away() -> None:
    page, before = _first()  # FR-01..03
    claims = [_claim("The system must read.", 1, 3)]
    second = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, before)
    claims.append(_claim("Something else entirely.", 40, 41))
    third = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, second)
    assert "job-x/FR-04" in _ids(third, "requirement")  # not FR-02: 2 and 3 were used


def test_a_statement_that_matches_nothing_gets_a_new_id() -> None:
    page, before = _first()
    claims = [_claim("The system must do something unrelated.", 90, 95)]
    after = _build(page, {"spec-requirements": _result("spec-requirements", *claims)}, before)
    assert _ids(after, "requirement") == ["job-x/FR-04"]
    assert [r["id"] for r in after["retired"]] == ["job-x/FR-01", "job-x/FR-02", "job-x/FR-03"]


def test_a_part_that_failed_keeps_what_it_had() -> None:
    page, before = _first()
    failed = SectionResult("spec-requirements", failed=True)
    after = _build(page, {"spec-requirements": failed}, before)
    assert _ids(after, "requirement") == _ids(before, "requirement") and after["retired"] == []
    again = _build(page, {}, after)  # not written at all (static run)
    assert _ids(again, "requirement") == _ids(before, "requirement")


def test_a_part_the_page_no_longer_has_is_retired() -> None:
    page, before = _first()
    smaller = _page("spec-rules")
    after = _build(
        smaller, {"spec-rules": _result("spec-rules", _claim("A rule.", 50, 51))}, before
    )
    assert [r["id"] for r in after["retired"]] == ["job-x/FR-01", "job-x/FR-02", "job-x/FR-03"]
    assert _ids(after, "rule") == ["job-x/BR-01"]


# --- gaps ------------------------------------------------------------------------------------


def test_gaps_keep_their_ids_and_resolved_ones_disappear_without_reuse() -> None:
    page = _page("spec-risks")
    first = _build(
        page, {"spec-risks": _result("spec-risks", gaps=("Which key wins?", "What is the unit?"))}
    )
    assert [g["id"] for g in first["gaps"]] == ["job-x/GAP-01", "job-x/GAP-02"]
    assert first["gaps"][0]["triage"] == {"status": "open", "by": []}
    second = _build(
        page, {"spec-risks": _result("spec-risks", gaps=("what is the  unit?",))}, first
    )
    assert [(g["id"], g["text"]) for g in second["gaps"]] == [("job-x/GAP-02", "What is the unit?")]
    third = _build(
        page,
        {"spec-risks": _result("spec-risks", gaps=("What is the unit?", "A new one."))},
        second,
    )
    assert [g["id"] for g in third["gaps"]] == ["job-x/GAP-02", "job-x/GAP-03"]


def test_an_acceptance_text_that_is_not_a_scenario_becomes_a_gap() -> None:
    page = _page("spec-acceptance")
    document = _build(
        page,
        {"spec-acceptance": _result("spec-acceptance", _claim("It must be fast.", 1, 2))},
    )
    assert document["items"] == []
    assert document["gaps"][0]["text"].startswith("Not in Given/When/Then form")
    assert document["gaps"][0]["part"] == "spec-acceptance"


# --- through the store -----------------------------------------------------------------------


def _prepared(tmp_path: Path, plan: Plan, stale: list[Page]) -> Prepared:
    return Prepared(
        plan, tmp_path / "app", {}, {p.path: f"{i:064x}" for i, p in enumerate(plan.pages)},
        stale, [], {}, None, False, "fake", "fake-model", 0, 0,
    )  # fmt: skip


def _run(tmp_path: Path, plan: Plan, llm: FakeLLM | None) -> Prepared:
    prepared = _prepared(tmp_path, plan, list(plan.pages))
    asyncio.run(generate_and_store(llm, prepared, language="English"))
    return prepared


def test_the_store_writes_a_sidecar_per_feature_and_an_index(tmp_path: Path) -> None:
    pages = [
        _page("spec-requirements", "spec-risks", slug="job-b"),
        _page("spec-requirements", slug="job-a"),
        Page("index.md", "Index", "Overview", [_part("spec-strategy")]),
    ]
    plan = Plan("app", "v9", FILES, pages, stack_sha256="f" * 64)
    llm = FakeLLM(reply(("The system must run.", "high", [("src/A.cbl", 3, 4)]), gaps=["Why?"]))
    _run(tmp_path, plan, llm)
    folder = tmp_path / "app"
    for slug in ("job-a", "job-b"):
        document = json.loads((folder / "features" / f"{slug}.spec.json").read_text("utf-8"))
        Draft202012Validator(SIDECAR_SCHEMA).validate(document)
        assert document["code_version"] == "v9" and document["stack_sha256"] == "f" * 64
        assert [i["text"] for i in document["items"]][0] == "The system must run."
    assert not (folder / "index.spec.json").exists()
    index = json.loads((folder / "_specs.json").read_text("utf-8"))
    Draft202012Validator(INDEX_SCHEMA).validate(index)
    assert [f["slug"] for f in index["features"]] == ["job-b", "job-a"]  # the plan's order


def test_a_second_run_with_the_same_answers_keeps_the_ids(tmp_path: Path) -> None:
    plan = Plan("app", "v9", FILES, [_page("spec-requirements")], stack_sha256="f" * 64)
    llm = FakeLLM(reply(("The system must run.", "high", [("src/A.cbl", 3, 4)])))
    _run(tmp_path, plan, llm)
    path = tmp_path / "app" / "features" / "job-x.spec.json"
    first = json.loads(path.read_text("utf-8"))
    _run(tmp_path, plan, llm)
    second = json.loads(path.read_text("utf-8"))
    assert [i["id"] for i in second["items"]] == [i["id"] for i in first["items"]]
    assert second["items"][0]["status"] == "unchanged"


def test_a_static_run_does_not_invent_a_sidecar(tmp_path: Path) -> None:
    plan = Plan("app", "v9", FILES, [_page("spec-requirements")], stack_sha256="f" * 64)
    _run(tmp_path, plan, None)
    assert not (tmp_path / "app" / "features" / "job-x.spec.json").exists()
    index = json.loads((tmp_path / "app" / "_specs.json").read_text("utf-8"))
    assert index["features"] == []


def test_documentation_pages_get_no_sidecar(tmp_path: Path) -> None:
    plan = Plan("app", "v9", FILES, [_page("summary", "flow")])  # no stack: not a specification
    _run(tmp_path, plan, FakeLLM())
    folder = tmp_path / "app"
    assert not list(folder.rglob("*.spec.json")) and not (folder / "_specs.json").exists()
