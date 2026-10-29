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
