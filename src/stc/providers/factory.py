"""Builds provider clients from `Settings`. The rest of the code only sees the interfaces."""

from typing import Literal

from stc.config import Settings
from stc.providers.embeddings import EmbeddingsClient
from stc.providers.errors import ProviderConfigError
from stc.providers.llm import LLMClient
from stc.providers.openai_adapter import OpenAIEmbeddings, OpenAILLM
from stc.providers.ratelimit import RateLimiter

Purpose = Literal["catalog", "chat"]


def build_llm_client(
    settings: Settings, purpose: Purpose, *, limiter: RateLimiter | None = None
) -> LLMClient:
    """`limiter` lets several clients share one request budget; by default each gets its own
    limiter from STC_LLM_REQUESTS_PER_MIN."""
    model = settings.llm_catalog_model if purpose == "catalog" else settings.llm_chat_model
    variable = "STC_LLM_CATALOG_MODEL" if purpose == "catalog" else "STC_LLM_CHAT_MODEL"
    if settings.llm_provider == "openai":
        if not model:
            raise ProviderConfigError("openai", f"{variable} is not set")
        return OpenAILLM(
            model=model,
            api_key=_secret(settings.openai_api_key),
            base_url=settings.openai_base_url,
            limiter=limiter or RateLimiter(settings.llm_requests_per_min),
        )
    raise ProviderConfigError(
        settings.llm_provider,
        "this LLM provider is not implemented yet (use STC_LLM_PROVIDER=openai)",
    )


def build_embeddings_client(
    settings: Settings, *, limiter: RateLimiter | None = None
) -> EmbeddingsClient:
    if settings.embeddings_provider == "openai":
        if not settings.embeddings_model:
            raise ProviderConfigError("openai", "STC_EMBEDDINGS_MODEL is not set")
        return OpenAIEmbeddings(
            model=settings.embeddings_model,
            dimensions=settings.embeddings_dimensions,
            api_key=_secret(settings.openai_api_key),
            base_url=settings.openai_base_url,
            limiter=limiter or RateLimiter(settings.llm_requests_per_min),
        )
    raise ProviderConfigError(
        settings.embeddings_provider,
        "this embeddings provider is not implemented yet (use STC_EMBEDDINGS_PROVIDER=openai)",
    )


def _secret(value: object) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    return getter() if getter else None
