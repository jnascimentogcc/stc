"""Provider-agnostic interfaces and the concrete adapters (OpenAI now; Anthropic later)."""

from stc.providers.embeddings import EmbeddingsClient
from stc.providers.errors import ProviderConfigError, ProviderError, ProviderResponseError
from stc.providers.factory import build_embeddings_client, build_llm_client
from stc.providers.llm import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, Usage

__all__ = [
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
