from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stc_tests import cli, mapping
from stc_tests.cli import app
from stc_tests.contract import ContractError
from stc_tests.llm import Usage
from stc_tests.reader import read_application

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "mapping"

REPLY = {
    "legacy_kind": "console",
    "inputs": [
        {"name": "keyedId", "prompt": "Enter id-1"},
        {"name": "sequentialId", "prompt": ""},
    ],
    "method": "POST",
    "path": "/program-search-example/runs",
    "fields": {"keyedId": "keyedId", "sequentialId": "sequentialId", "ghost": "ghost"},
    "results": {"keyedMessage": "keyedSearch.message"},
    "compare_mode": "structured",
    "open_questions": ["Is the date format kept?"],
}


class FakeLLM:
    model = "fake-model"

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]:
        return self.reply, Usage(4, 2)


def test_the_valid_fixtures_load() -> None:
    approved = mapping.load_mapping(FIXTURES / "valid" / "search.mapping.yaml")
    assert approved["status"] == "approved"
    assert approved["approved_at"] == "2026-11-18T09:00:00Z"  # a timestamp stays text
    assert mapping.load_mapping(FIXTURES / "valid" / "draft-with-questions.mapping.yaml")


@pytest.mark.parametrize(
    "path", sorted((FIXTURES / "invalid").glob("*.yaml")), ids=lambda p: p.name
)
def test_the_invalid_fixtures_are_refused(path: Path) -> None:
    with pytest.raises(ContractError):
        mapping.load_mapping(path)


def test_a_target_field_without_a_legacy_input_is_refused(tmp_path: Path) -> None:
    text = (FIXTURES / "valid" / "search.mapping.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "bad.mapping.yaml"
    bad.write_text(text.replace("    tripleId3: tripleId3", "    ghost: ghost"), encoding="utf-8")
    with pytest.raises(ContractError, match="ghost"):
        mapping.load_mapping(bad)


def test_a_legacy_request_field_without_a_legacy_input_is_refused(tmp_path: Path) -> None:
    text = (FIXTURES / "valid" / "php-loan.mapping.yaml").read_text(encoding="utf-8")
    assert mapping.load_mapping(FIXTURES / "valid" / "php-loan.mapping.yaml")
    bad = tmp_path / "bad.mapping.yaml"
    bad.write_text(
        text.replace("fields: {amount: amount, months", "fields: {ghost: amount, months", 1),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="ghost"):
        mapping.load_mapping(bad)


def test_the_draft_is_built_by_the_code(specs: Path) -> None:
    feature = read_application(specs, "cobol-bank").features[0]
    document, usage = mapping.draft_mapping("cobol-bank", feature, FakeLLM(REPLY))
    assert usage == Usage(4, 2)
    assert document["status"] == "draft"
    assert document["legacy"]["executable"] is False
    assert document["accepted_differences"] == []  # only people accept a difference
    assert "ghost" not in document["target"]["fields"]
    questions = document["open_questions"]
    assert mapping.STANDING_QUESTION in questions
    assert any("ghost" in q for q in questions)
    assert document["legacy"]["inputs"][1] == {"name": "sequentialId"}  # no empty prompt


def test_the_commands_draft_then_approve(
    specs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "make_llm", lambda: FakeLLM(REPLY))
    out = tmp_path / "out"
    base = ["--output", str(out)]
    made = runner.invoke(app, ["mapping", "cobol-bank", "--specs", str(specs), *base])
    assert made.exit_code == 0, made.output
    path = out / "cobol-bank" / "mapping" / "program-search-example.mapping.yaml"
    assert mapping.load_mapping(path)["status"] == "draft"

    refused = runner.invoke(
        app, ["approve-mapping", "cobol-bank", "program-search-example", "--by", "Ana", *base]
    )
    assert refused.exit_code == 1
    assert "settle the open questions" in refused.output

    # the migration team settles the questions by editing the file (comments survive approval)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index("open_questions:")
    path.write_text(
        "\n".join([*lines[:start], "open_questions: []", "# reviewed"]) + "\n", encoding="utf-8"
    )
    ok = runner.invoke(
        app, ["approve-mapping", "cobol-bank", "program-search-example", "--by", "Ana", *base]
    )
    assert ok.exit_code == 0, ok.output
    approved = mapping.load_mapping(path)
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "Ana"
    assert "# reviewed" in path.read_text(encoding="utf-8")

    again = runner.invoke(app, ["mapping", "cobol-bank", "--specs", str(specs), *base])
    assert again.exit_code == 0
    assert path.with_name("program-search-example.proposed.mapping.yaml").is_file()
    assert mapping.load_mapping(path)["status"] == "approved"  # never overwritten


def test_approve_needs_a_person(tmp_path: Path) -> None:
    result = runner.invoke(app, ["approve-mapping", "a", "b", "--output", str(tmp_path)])
    assert result.exit_code == 2
