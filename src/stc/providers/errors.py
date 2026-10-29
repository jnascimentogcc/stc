class ProviderError(Exception):
    """A provider call failed. `retryable` tells workers whether trying again can help."""

    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class ProviderConfigError(ProviderError):
    """Missing or invalid configuration (API key, model name, unsupported provider)."""


class ProviderResponseError(ProviderError):
    """The provider answered, but not in a usable way (malformed tool arguments, wrong size)."""
