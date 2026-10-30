"""Runtime configuration, read from `STC_*` environment variables (and `.env`)."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from stc.sources import Roots, build_roots


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STC_",
        env_file=".env",
        extra="ignore",
    )

    root: Path | None = None
    sources: Path | None = None  # sources.toml with more folders (default: ./sources.toml)
    catalogs: Path = Path("./catalogs")
    data: Path = Path("./data")

    db_url: str | None = None
    db_migration_url: str | None = None
    app_password: SecretStr | None = None

    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "STC_OPENAI_API_KEY")
    )
    openai_base_url: str | None = None  # Azure or any OpenAI-compatible gateway

    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_catalog_model: str | None = None
    llm_chat_model: str | None = None
    # Sent as `reasoning_effort` when set. Reasoning models reject tool calls on the Chat
    # Completions API unless it is "none" (the chat needs tools); models that do not know the
    # parameter reject it, so it is not sent by default.
    llm_chat_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    llm_catalog_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None

    embeddings_provider: Literal["openai", "voyage", "local"] = "openai"
    embeddings_model: str | None = None
    embeddings_dimensions: int = Field(default=1536, ge=1)  # must match the vector(1536) columns
    rerank_model: str | None = None

    language: str = "en"
    concurrency: int = Field(default=4, ge=1)
    llm_requests_per_min: int = Field(default=60, ge=1)

    mcp_token: str | None = None
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8765, ge=1, le=65535)
    environment: Literal["development", "production"] = "development"
    public_url: str = "http://localhost:8000"

    def roots(self) -> Roots:
        """The folders the applications are read from, by source."""
        return build_roots(self.root, self.sources)


@lru_cache
def get_settings() -> Settings:
    """Settings from the environment and from `STC_ENV_FILE` (default `.env`), which lets one
    checkout work with several setups, e.g. `stc --env-file .env.real index`."""
    return Settings(_env_file=os.environ.get("STC_ENV_FILE") or ".env")
