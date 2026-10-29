from collections.abc import Sequence
from typing import Protocol


class EmbeddingsClient(Protocol):
    """Turns texts into vectors. Each provider is an adapter behind this interface.

    `dimensions` is the vector size the adapter returns; it must match the database column
    (1536, see docs/tech-stack.md section 3)."""

    provider: str
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises `ProviderError` on failure."""
        ...
