"""Runtime configuration, read from `STC_*` environment variables (and `.env`)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STC_",
        env_file=".env",
        extra="ignore",
    )

    root: Path | None = None
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
