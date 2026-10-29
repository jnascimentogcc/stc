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


def test_unimplemented_command_fails_clearly() -> None:
    result = runner.invoke(app, ["reindex"])
    assert result.exit_code == 1


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
