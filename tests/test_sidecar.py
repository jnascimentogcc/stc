"""The sidecar of a feature specification (SPC-10..SPC-12): IDs, matching between generations,
Given/When/Then, and the files the store writes. No database: the plan is built by hand."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from stc.catalog.context import SectionContext
from stc.catalog.model import (
    SCENARIO_JSON_SCHEMA,
    Claim,
    Evidence,
    Scenario,
    SectionResult,
    parse_scenario_output,
)
from stc.docs.model import Page, Part, Plan
from stc.docs.store import Prepared, generate_and_store
from stc.providers import LLMResponse, Message, ProviderError, ToolSpec, Usage
from stc.spec.sidecar import (
    build_sidecar,
    hide_answered_gaps,
    is_spec_page,
    sidecar_path,
    text_sha256,
)
from stc.spec.triage import TRIAGE_SCHEMA, apply_reply

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


def _scenario(text: str, start: int = 1, end: int = 5) -> Claim:
    scenario = Scenario(["a loan"], ["it is paid"], ["it is closed"])
    return Claim(scenario.text(), "high", [Evidence("src/A.cbl", start, end)], scenario)


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
            "spec-acceptance": _result("spec-acceptance", _scenario("x", 9, 9)),
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


def test_an_acceptance_statement_without_fields_becomes_a_gap() -> None:
    page = _page("spec-acceptance")
    document = _build(
        page,
        {"spec-acceptance": _result("spec-acceptance", _claim("It must be fast.", 1, 2))},
    )
    assert document["items"] == []
    assert document["gaps"][0]["text"].startswith("Not given as a scenario")
    assert document["gaps"][0]["part"] == "spec-acceptance"


# --- through the store -----------------------------------------------------------------------


def _prepared(tmp_path: Path, plan: Plan, stale: list[Page]) -> Prepared:
    return Prepared(
        plan, tmp_path / "app", {}, {p.path: f"{i:064x}" for i, p in enumerate(plan.pages)},
        stale, [], {}, None, False, "fake", "fake-model", 0, 0,
    )  # fmt: skip


def _run(tmp_path: Path, plan: Plan, llm: "ScriptLLM | None") -> Prepared:
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
    llm = ScriptLLM(_section(gaps=("Why?",)))
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
    llm = ScriptLLM(_section())
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
    _run(tmp_path, plan, ScriptLLM(_section()))
    folder = tmp_path / "app"
    assert not list(folder.rglob("*.spec.json")) and not (folder / "_specs.json").exists()


# --- scenarios as fields (SPC-13) ------------------------------------------------------------


def test_a_scenario_reads_as_one_sentence() -> None:
    assert Scenario(["a loan", "a rate"], ["it is paid"], ["it closes"]).text() == (
        "Given a loan and a rate, when it is paid, then it closes."
    )
    assert Scenario([], ["the file is empty"], ["nothing is written"]).text() == (
        "When the file is empty, then nothing is written."
    )


def _scenario_reply(*scenarios: dict[str, list[str]]) -> str:
    claims = [
        {
            "scenario": s,
            "confidence": "high",
            "evidence": [{"file": "src/A.cbl", "start_line": 1, "end_line": 2}],
        }
        for s in scenarios
    ]
    return json.dumps({"claims": claims, "gaps": []})


def test_the_scenario_reply_gives_the_fields_and_a_text_built_from_them() -> None:
    raw = _scenario_reply(
        {"given": ["a loan of 100"], "when": ["it is paid in full"], "then": ["the balance is 0"]}
    )
    claims, gaps = parse_scenario_output(raw)
    assert gaps == [] and len(claims) == 1
    assert claims[0].text == "Given a loan of 100, when it is paid in full, then the balance is 0."
    assert claims[0].scenario == Scenario(
        ["a loan of 100"], ["it is paid in full"], ["the balance is 0"]
    )


@pytest.mark.parametrize(
    "scenario",
    [{"given": [], "when": [], "then": ["x"]}, {"given": [], "when": ["x"], "then": []}, {}],
)
def test_a_scenario_needs_a_when_and_a_then(scenario: dict[str, list[str]]) -> None:
    with pytest.raises(ValueError):
        parse_scenario_output(_scenario_reply(scenario))


def test_the_schema_asks_for_the_three_lists_and_no_free_text() -> None:
    item = SCENARIO_JSON_SCHEMA["properties"]["claims"]["items"]
    assert "text" not in item["properties"] and "scenario" in item["required"]
    assert set(item["properties"]["scenario"]["required"]) == {"given", "when", "then"}


# --- gap cross-check (SPC-14) ----------------------------------------------------------------


def _with_gaps() -> dict[str, Any]:
    page = _page("spec-requirements", "spec-risks")
    return _build(
        page,
        {
            "spec-requirements": _result(
                "spec-requirements", _claim("The system must load three records.", 1, 3)
            ),
            "spec-risks": _result(
                "spec-risks", _claim("Duplicate keys are not defined.", 5, 6),
                gaps=("What records are loaded?", "Which key wins?"),
            ),
        },
    )  # fmt: skip


def test_a_gap_is_answered_only_by_statements_that_exist() -> None:
    document = _with_gaps()
    reply = {
        "gaps": [
            {"id": "job-x/GAP-01", "answered_by": ["job-x/FR-01", "job-x/FR-99"]},
            {"id": "job-x/GAP-02", "answered_by": ["job-x/RK-01"]},  # a risk is not an answer
        ]
    }
    assert apply_reply(document, json.dumps(reply), "m")
    first, second = document["gaps"]
    assert first["triage"] == {"status": "answered_elsewhere", "by": ["job-x/FR-01"], "model": "m"}
    assert second["triage"] == {"status": "open", "by": [], "model": "m"}
    Draft202012Validator(SIDECAR_SCHEMA).validate(document)


def test_a_reply_that_cannot_be_read_changes_nothing() -> None:
    document = _with_gaps()
    before = json.dumps(document)
    assert not apply_reply(document, "not json", "m")
    assert not apply_reply(document, json.dumps({"gaps": [{"id": "x"}]}), "m")
    assert json.dumps(document) == before


def test_a_gap_the_model_did_not_mention_stays_open_but_is_marked_checked() -> None:
    document = _with_gaps()
    assert apply_reply(document, json.dumps({"gaps": []}), "m")
    assert [g["triage"]["status"] for g in document["gaps"]] == ["open", "open"]
    assert {g["triage"]["model"] for g in document["gaps"]} == {"m"}


def test_the_page_lists_only_the_open_gaps() -> None:
    page = _page("spec-risks")
    result = _result("spec-risks", gaps=("What records are loaded?", "Which?"))
    document = _build(page, {"spec-risks": result})
    document["gaps"][0]["triage"] = {"status": "answered_elsewhere", "by": ["job-x/FR-01"]}
    hide_answered_gaps(page, {page.key("spec-risks"): result}, document)
    assert result.gaps == ["Which?"]


# --- the store with a model ------------------------------------------------------------------


class ScriptLLM:
    """Answers each kind of request: a scenario part, the gap cross-check, any other part."""

    provider = "fake"
    model = "fake-model"

    def __init__(
        self, section: str, triage: str = '{"gaps": []}', fail_triage: bool = False
    ) -> None:
        self.section, self.triage, self.fail_triage = section, triage, fail_triage
        self.calls: list[str] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if response_schema is TRIAGE_SCHEMA:
            self.calls.append("triage")
            if self.fail_triage:
                raise ProviderError("fake", "boom", retryable=False)
            return LLMResponse(text=self.triage, usage=Usage(7, 3))
        if response_schema is SCENARIO_JSON_SCHEMA:
            self.calls.append("scenario")
            raw = _scenario_reply({"given": ["data"], "when": ["run"], "then": ["done"]})
            return LLMResponse(text=raw, usage=Usage(10, 5))
        self.calls.append("section")
        return LLMResponse(text=self.section, usage=Usage(10, 5))

    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _section(text: str = "The system must run.", gaps: tuple[str, ...] = ()) -> str:
    claim = {
        "text": text,
        "confidence": "high",
        "evidence": [{"file": "src/A.cbl", "start_line": 3, "end_line": 4}],
    }
    return json.dumps({"claims": [claim], "gaps": list(gaps)})


def _read(tmp_path: Path, name: str) -> Any:
    return json.loads((tmp_path / "app" / "features" / name).read_text("utf-8"))


def test_a_gap_another_section_answers_leaves_the_page_and_keeps_its_answer(
    tmp_path: Path,
) -> None:
    page = _page("spec-requirements", "spec-risks")
    plan = Plan("app", "v9", FILES, [page], stack_sha256="f" * 64)
    triage = json.dumps({"gaps": [{"id": "job-x/GAP-01", "answered_by": ["job-x/FR-01"]}]})
    llm = ScriptLLM(_section(gaps=("What does it load?",)), triage)
    prepared = _prepared(tmp_path, plan, [page])
    outcome = asyncio.run(generate_and_store(llm, prepared, language="English"))
    document = _read(tmp_path, "job-x.spec.json")
    by_part = {g["part"]: g["triage"]["status"] for g in document["gaps"]}
    assert by_part == {"spec-requirements": "answered_elsewhere", "spec-risks": "open"}
    text = (tmp_path / "app" / "features" / "job-x.md").read_text("utf-8")
    assert text.count("What does it load?") == 1  # only the gap nothing answered
    assert llm.calls.count("triage") == 1  # one call per page, not per gap
    assert outcome.input_tokens == 10 * 2 + 7 and outcome.output_tokens == 5 * 2 + 3


def test_a_failed_cross_check_leaves_the_gaps_open_and_unchecked(tmp_path: Path) -> None:
    page = _page("spec-requirements")
    plan = Plan("app", "v9", FILES, [page], stack_sha256="f" * 64)
    llm = ScriptLLM(_section(gaps=("Why?",)), fail_triage=True)
    prepared = _prepared(tmp_path, plan, [page])
    outcome = asyncio.run(generate_and_store(llm, prepared, language="English"))
    assert outcome.status == "complete"
    document = _read(tmp_path, "job-x.spec.json")
    assert document["gaps"][0]["triage"] == {"status": "open", "by": []}


def test_acceptance_scenarios_come_from_the_fields(tmp_path: Path) -> None:
    page = _page("spec-acceptance")
    plan = Plan("app", "v9", FILES, [page], stack_sha256="f" * 64)
    llm = ScriptLLM(_section())
    asyncio.run(generate_and_store(llm, _prepared(tmp_path, plan, [page]), language="English"))
    document = _read(tmp_path, "job-x.spec.json")
    Draft202012Validator(SIDECAR_SCHEMA).validate(document)
    assert document["items"][0]["scenario"] == {
        "given": ["data"],
        "when": ["run"],
        "then": ["done"],
    }
    text = (tmp_path / "app" / "features" / "job-x.md").read_text("utf-8")
    assert "Given data, when run, then done." in text


def test_a_rewritten_statement_citing_mostly_the_same_code_keeps_its_id() -> None:
    page = _page("spec-requirements")
    first = _build(
        page,
        {"spec-requirements": _result(
            "spec-requirements", _claim("The system must update the contract to status D.", 10, 30)
        )},
    )  # fmt: skip
    again = _claim("The system must set the status of the contract to D when it is late.", 12, 32)
    second = _build(page, {"spec-requirements": _result("spec-requirements", again)}, first)
    assert _ids(second, "requirement") == ["job-x/FR-01"] and second["retired"] == []
    assert second["items"][0]["status"] == "changed"


def test_the_same_citations_keep_the_id_whatever_the_new_text() -> None:
    page = _page("spec-requirements")
    first = _build(
        page,
        {"spec-requirements": _result(
            "spec-requirements", _claim("The system must update the contract to status D.", 10, 30)
        )},
    )  # fmt: skip
    other = _claim("Print the page header in uppercase.", 10, 30)
    second = _build(page, {"spec-requirements": _result("spec-requirements", other)}, first)
    assert _ids(second, "requirement") == ["job-x/FR-01"]  # SPC-12: same evidence, same statement
    assert second["items"][0]["status"] == "changed"


def test_a_different_text_over_other_code_is_a_new_statement() -> None:
    page = _page("spec-requirements")
    first = _build(
        page,
        {"spec-requirements": _result(
            "spec-requirements", _claim("The system must update the contract to status D.", 10, 30)
        )},
    )  # fmt: skip
    far = _claim("The system must update the contract to status E.", 100, 120)
    second = _build(page, {"spec-requirements": _result("spec-requirements", far)}, first)
    assert _ids(second, "requirement") == ["job-x/FR-02"]
    assert [r["id"] for r in second["retired"]] == ["job-x/FR-01"]


def test_the_same_text_over_other_lines_is_still_the_same_statement() -> None:
    page = _page("spec-requirements")
    text = "The system must update the contract to status D."
    first = _build(page, {"spec-requirements": _result("spec-requirements", _claim(text, 10, 30))})
    moved = _claim(text, 100, 120)  # the code moved
    second = _build(page, {"spec-requirements": _result("spec-requirements", moved)}, first)
    assert _ids(second, "requirement") == ["job-x/FR-01"]
    assert second["items"][0]["status"] == "unchanged"
