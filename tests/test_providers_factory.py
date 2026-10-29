import pytest

from stc.config import Settings
from stc.providers import ProviderConfigError, build_embeddings_client, build_llm_client
from stc.providers.openai_adapter import OpenAIEmbeddings, OpenAILLM


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_api_key_is_read_from_the_standard_openai_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    key = Settings(_env_file=None).openai_api_key
    assert key is not None and key.get_secret_value() == "sk-from-env"
    assert "sk-from-env" not in repr(Settings(_env_file=None))


def test_builds_openai_clients_for_each_purpose() -> None:
    cfg = settings(
        openai_api_key="k", llm_catalog_model="cat-model", llm_chat_model="chat-model",
        embeddings_model="emb-model",
    )  # fmt: skip
    catalog = build_llm_client(cfg, "catalog")
    chat = build_llm_client(cfg, "chat")
    assert isinstance(catalog, OpenAILLM) and catalog.model == "cat-model"
    assert chat.model == "chat-model"
    embeddings = build_embeddings_client(cfg)
    assert isinstance(embeddings, OpenAIEmbeddings)
    assert (embeddings.model, embeddings.dimensions) == ("emb-model", 1536)


def test_reasoning_effort_is_passed_per_purpose() -> None:
    cfg = settings(
        openai_api_key="k", llm_catalog_model="c", llm_chat_model="m",
        llm_chat_reasoning_effort="none",
    )  # fmt: skip
    assert build_llm_client(cfg, "chat")._reasoning_effort == "none"  # type: ignore[attr-defined]
    assert build_llm_client(cfg, "catalog")._reasoning_effort is None  # type: ignore[attr-defined]


def test_missing_configuration_names_the_variable() -> None:
    with pytest.raises(ProviderConfigError, match="STC_LLM_CHAT_MODEL"):
        build_llm_client(settings(openai_api_key="k"), "chat")
    with pytest.raises(ProviderConfigError, match="STC_EMBEDDINGS_MODEL"):
        build_embeddings_client(settings(openai_api_key="k"))
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        build_llm_client(settings(llm_chat_model="m"), "chat")


def test_providers_not_implemented_yet_fail_clearly() -> None:
    with pytest.raises(ProviderConfigError, match="not implemented"):
        build_llm_client(settings(llm_provider="anthropic", llm_chat_model="m"), "chat")
    with pytest.raises(ProviderConfigError, match="not implemented"):
        build_embeddings_client(settings(embeddings_provider="voyage", embeddings_model="m"))
