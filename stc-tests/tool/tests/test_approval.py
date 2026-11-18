from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stc_tests import cli
from stc_tests.approval import ApprovalError, approve, parse_blocks, reject
from stc_tests.catalog import read_catalog
from stc_tests.cli import app
from stc_tests.llm import Usage
from stc_tests.reader import read_application
from stc_tests.scenarios import Cases, draft_feature, render

runner = CliRunner()
CATALOG = Path(__file__).resolve().parents[3] / "stc-tests/template/catalog/actions.json"
FR04 = "program-search-example/FR-04"
FR07 = "program-search-example/FR-07"


def _scenario(title: str, covers: list[str], value: str = "0004") -> dict[str, Any]:
    return {
        "title": title,
        "covers": covers,
        "priority": "high",
        "steps": [
            {"keyword": "When", "text": f'I search the keyed table for the id "{value}"'},
            {"keyword": "Then", "text": 'the keyed result is "Item not found."'},
        ],
    }


class FakeLLM:
    model = "fake-model"

    def __init__(self, *scenarios: dict[str, Any]) -> None:
        self.scenarios = list(scenarios)

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]:
        return {"scenarios": self.scenarios, "missing_actions": []}, Usage()


def _drafted(specs: Path, tmp_path: Path, *scenarios: dict[str, Any]) -> tuple[Path, Cases, Any]:
    """A features folder and its cases, written the way the `scenarios` command writes them."""
    feature = read_application(specs, "cobol-bank").features[0]
    hashes = {i.id: i.text_sha256 for i in feature.items}
    cases = Cases(tmp_path / ".stc-tests" / "cases.json")
    result = draft_feature(feature, read_catalog(CATALOG), FakeLLM(*scenarios), cases, hashes)
    features = tmp_path / "features"
    features.mkdir()
    (features / "program-search-example.feature").write_text(
        render(feature, result.drafts, cases.approved_ids()), encoding="utf-8"
    )
    cases.save()
    return features, cases, (feature, hashes)


def _text(features: Path) -> str:
    return (features / "program-search-example.feature").read_text(encoding="utf-8")


def test_blocks_are_found_with_their_steps(specs: Path, tmp_path: Path) -> None:
    features, _, _ = _drafted(specs, tmp_path, _scenario("A", [FR04]), _scenario("B", [FR07]))
    blocks = parse_blocks(_text(features).splitlines())
    assert [b.case_id for b in blocks] == ["TC-0001", "TC-0002"]
    assert blocks[0].steps[0] == ("When", 'I search the keyed table for the id "0004"')


def test_approving_tags_the_scenario_and_records_who(specs: Path, tmp_path: Path) -> None:
    features, cases, _ = _drafted(specs, tmp_path, _scenario("A", [FR04]), _scenario("B", [FR07]))
    approve(features, cases, ["TC-0001"], "Ana")
    text = _text(features)
    assert text.count("@status:approved") == 1
    assert "@TC-0001 @priority:high @status:approved" in text
    entry = cases.entry("TC-0001")
    assert entry is not None
    assert entry["status"] == "approved"
    assert entry["approved_by"] == "Ana"
    assert entry["model"] == "fake-model"
    assert cases.entry("TC-0002") is not None
    assert cases.approved_ids() == frozenset({"TC-0001"})


def test_an_unknown_case_changes_nothing(specs: Path, tmp_path: Path) -> None:
    features, cases, _ = _drafted(specs, tmp_path, _scenario("A", [FR04]))
    before = _text(features)
    with pytest.raises(ApprovalError):
        approve(features, cases, ["TC-0001", "TC-0099"], "Ana")
    assert _text(features) == before
    entry = cases.entry("TC-0001")
    assert entry is not None
    assert entry["status"] == "draft"


def test_rejecting_removes_the_scenario_and_it_is_not_proposed_again(
    specs: Path, tmp_path: Path
) -> None:
    features, cases, (feature, hashes) = _drafted(
        specs, tmp_path, _scenario("A", [FR04]), _scenario("B", [FR07])
    )
    reject(features, cases, ["TC-0001"], "Ana", "duplicates another case")
    text = _text(features)
    assert "TC-0001" not in text
    assert "TC-0002" in text
    entry = cases.entry("TC-0001")
    assert entry is not None
    assert entry["rejected_reason"] == "duplicates another case"

    again = draft_feature(
        feature, read_catalog(CATALOG), FakeLLM(_scenario("A", [FR04])), cases, hashes
    )
    assert again.drafts == []
    assert again.skipped == ["A: rejected in a review before"]


def test_an_approved_case_goes_back_to_review_when_its_text_changes(
    specs: Path, tmp_path: Path
) -> None:
    features, cases, (feature, hashes) = _drafted(specs, tmp_path, _scenario("A", [FR04]))
    approve(features, cases, ["TC-0001"], "Ana")
    rewritten = FakeLLM(_scenario("A", [FR04], value="0005"))
    draft_feature(feature, read_catalog(CATALOG), rewritten, cases, hashes)
    entry = cases.entry("TC-0001")
    assert entry is not None
    assert entry["status"] == "to_review"
    assert cases.approved_ids() == frozenset()


def test_an_approved_case_goes_back_to_review_when_the_spec_changes(
    specs: Path, tmp_path: Path
) -> None:
    features, cases, (feature, hashes) = _drafted(specs, tmp_path, _scenario("A", [FR04]))
    approve(features, cases, ["TC-0001"], "Ana")
    hashes = {**hashes, FR04: "0" * 64}
    draft_feature(feature, read_catalog(CATALOG), FakeLLM(_scenario("A", [FR04])), cases, hashes)
    entry = cases.entry("TC-0001")
    assert entry is not None
    assert entry["status"] == "to_review"


def test_an_unchanged_regeneration_keeps_the_approval(specs: Path, tmp_path: Path) -> None:
    features, cases, (feature, hashes) = _drafted(specs, tmp_path, _scenario("A", [FR04]))
    approve(features, cases, ["TC-0001"], "Ana")
    result = draft_feature(
        feature, read_catalog(CATALOG), FakeLLM(_scenario("A", [FR04])), cases, hashes
    )
    assert cases.approved_ids() == frozenset({"TC-0001"})
    assert "@status:approved" in render(feature, result.drafts, cases.approved_ids())


# --- the commands ------------------------------------------------------------------------------


def test_commands_review_approve_reject(
    specs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM(_scenario("A", [FR04]), _scenario("B", [FR07]), _scenario("C", [FR04], "0009"))
    monkeypatch.setattr(cli, "make_llm", lambda: llm)
    out = tmp_path / "out"
    base = ["--output", str(out)]
    made = runner.invoke(
        app, ["scenarios", "cobol-bank", "--specs", str(specs), *base, "--catalog", str(CATALOG)]
    )
    assert made.exit_code == 0, made.output

    assert runner.invoke(app, ["approve", "cobol-bank", "TC-0001", *base]).exit_code == 2  # no --by
    ok = runner.invoke(app, ["approve", "cobol-bank", "TC-0001", "--by", "Ana", *base])
    assert ok.exit_code == 0, ok.output
    rej = runner.invoke(
        app, ["reject", "cobol-bank", "TC-0002", "--by", "Ana", "--reason", "out of scope", *base]
    )
    assert rej.exit_code == 0, rej.output
    assert (
        runner.invoke(app, ["reject", "cobol-bank", "TC-0003", "--by", "Ana", *base]).exit_code == 2
    )

    listing = runner.invoke(app, ["review", "cobol-bank", "--specs", str(specs), *base])
    assert "TC-0001  approved by Ana" in listing.output
    assert "TC-0002  rejected" in listing.output
    assert "TC-0003  draft" in listing.output

    everything = runner.invoke(app, ["approve", "cobol-bank", "--all-drafts", "--by", "Ana", *base])
    assert "TC-0003: approved by Ana" in everything.output
    features = out / "cobol-bank" / "features" / "program-search-example.feature"
    assert features.read_text(encoding="utf-8").count("@status:approved") == 2


def test_review_needs_cases(tmp_path: Path) -> None:
    assert runner.invoke(app, ["review", "cobol-bank", "--output", str(tmp_path)]).exit_code == 1
