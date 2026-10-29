import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stc import __version__
from stc.cli import app
from stc.config import get_settings

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_check_providers_reports_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "STC_EMBEDDINGS_MODEL", "STC_LLM_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path_without_env())
    get_settings.cache_clear()
    result = runner.invoke(app, ["check-providers"])
    assert result.exit_code == 1
    assert "STC_EMBEDDINGS_MODEL" in result.output or "OPENAI_API_KEY" in result.output


def tmp_path_without_env() -> Path:
    # A directory with no .env, so a developer's local settings cannot leak into the test.
    return Path(tempfile.mkdtemp())


def test_catalog_needs_exactly_one_of_application_or_all() -> None:
    for args in (["catalog"], ["catalog", "svc", "--all"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2


def test_env_file_option_selects_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STC_ENV_FILE", raising=False)
    missing = runner.invoke(app, ["--env-file", "nope.env", "version"])
    assert missing.exit_code == 2 and "does not exist" in missing.output

    monkeypatch.delenv("STC_DB_URL", raising=False)
    (tmp_path / "other.env").write_text("STC_LANGUAGE=es\n", encoding="utf-8")  # no database
    ok = runner.invoke(app, ["--env-file", "other.env", "status"])
    assert ok.exit_code == 2 and "STC_DB_URL is not set" in ok.output
    monkeypatch.delenv("STC_ENV_FILE", raising=False)
    get_settings.cache_clear()
