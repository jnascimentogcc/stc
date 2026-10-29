"""Provider-agnostic interfaces; concrete adapters (OpenAI, Anthropic, Voyage) live here."""

from stc.providers.embeddings import EmbeddingsClient
from stc.providers.llm import LLMClient

__all__ = ["EmbeddingsClient", "LLMClient"]
