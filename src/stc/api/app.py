from collections.abc import Callable

from fastapi import FastAPI

from stc import __version__
from stc.api.chat import LLMFactory, build_chat_router, default_llm_factory
from stc.config import Settings, get_settings
from stc.query.search import QueryEmbedder


def create_app(
    settings: Settings | None = None,
    *,
    llm_factory: LLMFactory | None = None,
    embedder_factory: Callable[[Settings], QueryEmbedder | None] | None = None,
) -> FastAPI:
    """The API. Settings are read lazily, so importing this module needs no configuration."""
    application = FastAPI(title="STC", version=__version__)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    application.include_router(
        build_chat_router(
            settings or get_settings(),
            llm_factory or default_llm_factory,
            embedder_factory,
        )
    )
    return application


app = create_app()
