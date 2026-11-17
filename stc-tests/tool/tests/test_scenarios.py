import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from stc_tests import cli
from stc_tests.catalog import CatalogError, matches, read_catalog
from stc_tests.cli import app
from stc_tests.llm import LLMError, OpenAIJsonLLM, Usage
from stc_tests.reader import read_application
from stc_tests.scenarios import SCHEMA, Cases, build_prompt, draft_feature

runner = CliRunner()
CATALOG = Path(__file__).resolve().parents[3] / "stc-tests/template/catalog/actions.json"
FR04 = "program-search-example/FR-04"
FR07 = "program-search-example/FR-07"

GOOD = {
    "title": "A search on an id that is not in the table finds nothing",
    "covers": [FR04],
    "priority": "high",
    "steps": [
        {"keyword": "When", "text": 'I search the keyed table for the id "0004"'},
        {"keyword": "Then", "text": 'the keyed result is "Item not found."'},
    ],
}


class FakeLLM:
    model = "fake"

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]:
        self.prompts.append(user)
        return self.reply, Usage(10, 5)


def _reply(
    *scenarios: dict[str, Any], missing: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"scenarios": list(scenarios), "missing_actions": missing or []}


def _feature(specs: Path) -> Any:
    return read_application(specs, "cobol-bank").features[0]


# --- catalog ---------------------------------------------------------------------------------


def test_the_catalog_of_the_template_is_readable_and_matches_steps() -> None:
    actions = read_catalog(CATALOG)
    assert matches(actions, 'I search the keyed table for the id "0002"')
    assert matches(actions, 'I search the keyed table for the ids "1", "2" and "3"')
    assert not matches(actions, "I click the red button")
    assert not matches(
        actions, "I search the keyed table for the id 0002"
    )  # the value needs quotes


def test_a_catalog_of_another_format_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "actions.json"
    bad.write_text(json.dumps({"schema": "other/1", "actions": []}), encoding="utf-8")
    with pytest.raises(CatalogError):
        read_catalog(bad)


# --- the model call --------------------------------------------------------------------------


class _Completions:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    def create(self, **_: Any) -> Any:
        text = self.texts[self.calls]
        self.calls += 1
        message = SimpleNamespace(content=text)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )


def _client(*texts: str) -> tuple[Any, _Completions]:
    completions = _Completions(list(texts))
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_a_reply_that_follows_the_schema_is_returned() -> None:
    client, _ = _client(json.dumps(_reply()))
    reply, usage = OpenAIJsonLLM(model="m", client=client).complete_json("s", "u", SCHEMA)
    assert reply == _reply()
    assert usage == Usage(7, 3)


def test_a_bad_reply_gets_one_repair_attempt() -> None:
    client, completions = _client("not json", json.dumps(_reply()))
    reply, usage = OpenAIJsonLLM(model="m", client=client).complete_json("s", "u", SCHEMA)
    assert reply == _reply()
    assert completions.calls == 2
    assert usage == Usage(14, 6)


def test_a_reply_that_never_follows_the_schema_fails() -> None:
    client, _ = _client('{"scenarios": 1}', '{"scenarios": 1}')
    with pytest.raises(LLMError):
        OpenAIJsonLLM(model="m", client=client).complete_json("s", "u", SCHEMA)


def test_no_key_and_no_model_are_refused() -> None:
    with pytest.raises(LLMError):
        OpenAIJsonLLM(model="m", api_key=None)
    with pytest.raises(LLMError):
        OpenAIJsonLLM(model="")


# --- drafting --------------------------------------------------------------------------------


def test_the_prompt_has_items_open_gaps_and_actions(specs: Path) -> None:
    prompt = build_prompt(_feature(specs), read_catalog(CATALOG))
    assert FR04 in prompt
    assert "given: the table has the test data" in prompt  # the acceptance scenario of the sidecar
    assert "I search the keyed table for the id {string}" in prompt


def test_a_valid_scenario_is_kept_with_a_case_id(specs: Path, tmp_path: Path) -> None:
    feature = _feature(specs)
    hashes = {i.id: i.text_sha256 for i in feature.items}
    cases = Cases(tmp_path / "cases.json")
    result = draft_feature(feature, read_catalog(CATALOG), FakeLLM(_reply(GOOD)), cases, hashes)
    assert [d.case_id for d in result.drafts] == ["TC-0001"]
    assert result.rejected == []
    assert result.usage == Usage(10, 5)


def test_the_checks_are_made_by_the_code(specs: Path, tmp_path: Path) -> None:
    feature = _feature(specs)
    hashes = {i.id: i.text_sha256 for i in feature.items}
    unknown_item = {**GOOD, "title": "unknown", "covers": ["program-search-example/FR-99"]}
    no_action = {
        **GOOD,
        "title": "no action",
        "steps": [
            {"keyword": "When", "text": "I click the red button"},
            {"keyword": "Then", "text": 'the keyed result is "x"'},
        ],
    }
    no_then = {**GOOD, "title": "no then", "steps": GOOD["steps"][:1]}
    reply = _reply(GOOD, unknown_item, no_action, no_then)
    result = draft_feature(
        feature, read_catalog(CATALOG), FakeLLM(reply), Cases(tmp_path / "c.json"), hashes
    )
    assert len(result.drafts) == 1
    assert len(result.rejected) == 3
    assert result.missing == ["I click the red button (for program-search-example/FR-04)"]


def test_case_ids_are_stable_and_never_reused(specs: Path, tmp_path: Path) -> None:
    feature = _feature(specs)
    hashes = {i.id: i.text_sha256 for i in feature.items}
    path = tmp_path / "cases.json"
    other = {**GOOD, "title": "Another", "covers": [FR07]}

    saved = Cases(path)
    first = draft_feature(feature, read_catalog(CATALOG), FakeLLM(_reply(GOOD)), saved, hashes)
    saved.save()
    cases = Cases(path)
    again = draft_feature(
        feature, read_catalog(CATALOG), FakeLLM(_reply(other, GOOD)), cases, hashes
    )
    assert first.drafts[0].case_id == "TC-0001"
    assert [d.case_id for d in again.drafts] == ["TC-0002", "TC-0001"]

    hashes[FR04] = "0" * 64  # the source item changed
    assert cases.to_review(hashes) == ["TC-0001"]


# --- the command -----------------------------------------------------------------------------


def _run(specs: Path, out: Path, *extra: str) -> Any:
    return runner.invoke(
        app,
        ["scenarios", "cobol-bank", "--specs", str(specs), "--output", str(out), *extra],
    )


def test_scenarios_writes_a_draft_and_never_overwrites(
    specs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "make_llm", lambda: FakeLLM(_reply(GOOD)))
    out = tmp_path / "out"
    first = _run(specs, out, "--catalog", str(CATALOG))
    assert first.exit_code == 0, first.output
    written = out / "cobol-bank" / "features" / "program-search-example.feature"
    text = written.read_text(encoding="utf-8")
    assert "@TC-0001" in text
    assert f"@{FR04}" in text
    assert "Scenario: A search on an id that is not in the table finds nothing" in text

    written.write_text("approved by hand\n", encoding="utf-8")
    second = _run(specs, out, "--catalog", str(CATALOG))
    assert second.exit_code == 0
    assert written.read_text(encoding="utf-8") == "approved by hand\n"
    assert written.with_suffix(".proposed.feature").is_file()

    third = _run(specs, out, "--catalog", str(CATALOG), "--force")
    assert third.exit_code == 0
    assert "@TC-0001" in written.read_text(encoding="utf-8")


def test_scenarios_dry_run_calls_no_model(
    specs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> Any:
        raise AssertionError("no model in a dry run")

    monkeypatch.setattr(cli, "make_llm", boom)
    result = _run(specs, tmp_path / "out", "--catalog", str(CATALOG), "--dry-run")
    assert result.exit_code == 0
    assert "dry run" in result.output
    assert not (tmp_path / "out").exists()


def test_scenarios_needs_a_catalog(specs: Path, tmp_path: Path) -> None:
    assert _run(specs, tmp_path / "out").exit_code == 2
