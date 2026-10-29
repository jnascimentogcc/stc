"""Provider-agnostic interfaces and the concrete adapters (OpenAI now; Anthropic later)."""

from collections.abc import Awaitable

from stc.providers.embeddings import EmbeddingsClient
from stc.providers.errors import ProviderConfigError, ProviderError, ProviderResponseError
from stc.providers.factory import build_embeddings_client, build_llm_client
from stc.providers.llm import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, Usage


async def closing[T](work: Awaitable[T], *clients: object) -> T:
    """Await `work`, then close the provider clients in the same event loop.

    The SDK keeps pooled connections that belong to the loop that opened them: closing them
    later, from another loop (or at garbage collection), raises "Event loop is closed"."""
    try:
        return await work
    finally:
        for client in clients:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


__all__ = [
    "closing",
    "EmbeddingsClient",
    "LLMClient",
    "LLMResponse",
    "Message",
    "ProviderConfigError",
    "ProviderError",
    "ProviderResponseError",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_embeddings_client",
    "build_llm_client",
]
