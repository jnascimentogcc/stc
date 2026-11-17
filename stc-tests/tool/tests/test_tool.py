import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stc_tests import settings, state
from stc_tests.cli import app
from stc_tests.contract import ContractError, UnsupportedVersion, validate
from stc_tests.reader import read_application

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "valid"


def _sidecar(specs: Path) -> Path:
    return specs / "cobol-bank" / "features" / "program-search-example.spec.json"


def test_reads_the_example(specs: Path) -> None:
    read = read_application(specs, "cobol-bank")
    assert read.name == "cobol-bank"
    assert any(i.id == "program-search-example/FR-04" for i in read.items())
    assert read.retired() == ["program-search-example/FR-06"]


def test_unknown_major_is_refused(specs: Path) -> None:
    path = _sidecar(specs)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema"] = "stc.spec-sidecar/2"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedVersion):
        read_application(specs, "cobol-bank")
    result = runner.invoke(app, ["check", "cobol-bank", "--specs", str(specs)])
    assert result.exit_code == 1


def test_future_minor_is_accepted() -> None:
    document = json.loads((FIXTURES / "minimal-future-minor.spec.json").read_text(encoding="utf-8"))
    validate(document, "stc.spec-sidecar")


def test_invalid_sidecar_is_refused(specs: Path) -> None:
    path = _sidecar(specs)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["items"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ContractError):
        read_application(specs, "cobol-bank")


def test_check_ok(specs: Path) -> None:
    result = runner.invoke(app, ["check", "--all", "--specs", str(specs)])
    assert result.exit_code == 0
    assert "cobol-bank: ok" in result.output


def test_ingest_tracks_drift(specs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    args = ["ingest", "cobol-bank", "--specs", str(specs), "--output", str(out)]
    dry = runner.invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0
    assert "new" in dry.output
    assert not state.state_path(out, "cobol-bank").exists()

    assert runner.invoke(app, args).exit_code == 0
    assert "0 new, 0 changed" in runner.invoke(app, args).output

    path = _sidecar(specs)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][0]["text_sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "1 changed" in runner.invoke(app, args).output


def test_default_folders_come_from_the_environment(
    specs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STC_SPECS", str(specs))
    monkeypatch.setenv("STC_TESTS", str(tmp_path / "t"))
    assert runner.invoke(app, ["ingest", "--all"]).exit_code == 0
    assert state.state_path(tmp_path / "t", "cobol-bank").is_file()


def test_needs_an_application_or_all(specs: Path) -> None:
    assert runner.invoke(app, ["check", "--specs", str(specs)]).exit_code == 2


def test_env_file_sets_what_is_not_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    lines = [
        "# comment",
        "STC_TESTS_X_MODEL=some-model",
        'export STC_TESTS_X_KEY="quoted"',
        "STC_TESTS_X_SET=from-file",
    ]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # setenv registers each name with monkeypatch, so the test leaves the environment as it was
    monkeypatch.setenv("STC_TESTS_X_MODEL", "x")
    monkeypatch.setenv("STC_TESTS_X_KEY", "x")
    monkeypatch.delenv("STC_TESTS_X_MODEL")
    monkeypatch.delenv("STC_TESTS_X_KEY")
    monkeypatch.setenv("STC_TESTS_X_SET", "from-shell")
    names = settings.load_env_file(env)
    assert sorted(names) == ["STC_TESTS_X_KEY", "STC_TESTS_X_MODEL"]
    assert os.environ["STC_TESTS_X_KEY"] == "quoted"
    assert os.environ["STC_TESTS_X_SET"] == "from-shell"


def test_a_missing_env_file_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--env-file", str(tmp_path / "nope"), "check", "--all"])
    assert result.exit_code == 2
