from pathlib import Path

import pytest

from stc.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "openai"
    assert settings.embeddings_provider == "openai"
    assert settings.language == "en"
    assert settings.rerank_model is None


def test_reads_stc_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STC_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("STC_CONCURRENCY", "8")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "anthropic"
    assert settings.concurrency == 8


def test_get_settings_reads_the_file_named_by_stc_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stc.config import get_settings

    (tmp_path / ".env").write_text("STC_LANGUAGE=pt\n", encoding="utf-8")
    other = tmp_path / ".env.real"
    other.write_text("STC_LANGUAGE=es\nSTC_CONCURRENCY=2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STC_LANGUAGE", raising=False)
    monkeypatch.delenv("STC_CONCURRENCY", raising=False)
    monkeypatch.delenv("STC_ENV_FILE", raising=False)
    get_settings.cache_clear()
    assert get_settings().language == "pt"
    monkeypatch.setenv("STC_ENV_FILE", str(other))
    get_settings.cache_clear()
    assert (get_settings().language, get_settings().concurrency) == ("es", 2)
    get_settings.cache_clear()
