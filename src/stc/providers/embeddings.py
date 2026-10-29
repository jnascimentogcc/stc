from collections.abc import Sequence
from typing import Protocol


class EmbeddingsClient(Protocol):
    """Turns texts into vectors. Each provider is an adapter behind this interface."""

    provider: str
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
