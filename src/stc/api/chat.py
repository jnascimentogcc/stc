"""Chat endpoints: conversations, streamed answers (SSE) and feedback (CHT-01 to CHT-11).

The answer endpoint streams Server-Sent Events: `tool_call` and `tool_result` while the agent
works, `text` pieces of the answer, then `done` (answer, verified citations, warnings, usage,
message id) or `error`. Nothing here searches code: the agent uses the same tools as the MCP
server over the query engine."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from stc.chat import store
from stc.chat.agent import run_agent
from stc.config import Settings
from stc.mcp_server import build_server, to_data
from stc.providers import LLMClient, Message, ProviderError, build_llm_client
from stc.query.engine import QueryEngine
from stc.query.models import QueryError
from stc.query.search import QueryEmbedder

LLMFactory = Callable[[Settings], LLMClient]

_STATUS = {
    "invalid_argument": 422,
    "unknown_application": 422,
    "not_found": 404,
    "forbidden": 403,
    "unavailable": 503,
}


def default_llm_factory(settings: Settings) -> LLMClient:
    return build_llm_client(settings, "chat")


class ConversationCreate(BaseModel):
    applications: list[str] | None = Field(
        default=None, description="Restrict the conversation to these applications (omit for all)."
    )
    profile: str = "technical"


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    applications: list[str] | None = None
    all_applications: bool = False
    profile: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)


class FeedbackCreate(BaseModel):
    rating: str
    correction: str | None = Field(default=None, max_length=4_000)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_chat_router(
    settings: Settings,
    llm_factory: LLMFactory,
    embedder_factory: Callable[[Settings], QueryEmbedder | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def connect() -> psycopg.Connection:
        if settings.db_url is None:
            raise HTTPException(503, "The index database is not configured (STC_DB_URL).")
        try:
            return psycopg.connect(settings.db_url, autocommit=True)
        except psycopg.Error as exc:
            raise HTTPException(503, "The index database is not reachable.") from exc

    async def db[T](call: Callable[[psycopg.Connection], T]) -> T:
        """Run one store call on its own short-lived connection, off the event loop."""

        def work() -> T:
            conn = connect()
            try:
                return call(conn)
            finally:
                conn.close()

        try:
            return await asyncio.to_thread(work)
        except QueryError as exc:
            raise HTTPException(_STATUS.get(exc.code, 400), exc.message) from exc

    def not_found(what: str) -> HTTPException:
        return HTTPException(404, f"{what} not found.")

    @router.get("/applications")
    async def applications() -> list[dict[str, Any]]:
        """Indexed applications, for choosing a conversation scope."""

        def query(conn: psycopg.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT a.name, EXISTS (SELECT 1 FROM catalogs c WHERE c.application_id = a.id)"
                " FROM applications a ORDER BY a.name"
            ).fetchall()
            return [{"name": n, "has_catalog": bool(c)} for n, c in rows]

        return await db(query)

    @router.get("/files/slice")
    async def file_slice(
        application: str,
        path: str,
        start_line: Annotated[int, Query(ge=1)] = 1,
        end_line: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any]:
        """Lines of an indexed file, to show the code behind a citation. Secret and excluded
        files are refused, like in every other read."""

        def read(conn: psycopg.Connection) -> dict[str, Any]:
            engine = QueryEngine(conn, root=settings.root, catalogs=settings.catalogs)
            piece = engine.read_file(application, path, start_line=start_line, end_line=end_line)
            return to_data(piece)  # type: ignore[no-any-return]

        return await db(read)

    @router.post("/conversations", status_code=201)
    async def create_conversation(body: ConversationCreate) -> dict[str, Any]:
        return await db(
            lambda c: store.create_conversation(
                c, applications=body.applications, profile=body.profile
            )
        )

    @router.get("/conversations")
    async def list_conversations(
        limit: Annotated[int, Query(ge=1, le=100)] = 30, offset: Annotated[int, Query(ge=0)] = 0
    ) -> list[dict[str, Any]]:
        return await db(lambda c: store.list_conversations(c, limit=limit, offset=offset))

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        def load(conn: psycopg.Connection) -> dict[str, Any] | None:
            conversation = store.get_conversation(conn, conversation_id)
            if conversation is None:
                return None
            return {**conversation, "messages": store.list_messages(conn, conversation_id)}

        found = await db(load)
        if found is None:
            raise not_found("Conversation")
        return found

    @router.patch("/conversations/{conversation_id}")
    async def update_conversation(conversation_id: str, body: ConversationUpdate) -> dict[str, Any]:
        updated = await db(
            lambda c: store.update_conversation(
                c,
                conversation_id,
                title=body.title,
                applications=body.applications,
                all_applications=body.all_applications,
                profile=body.profile,
            )
        )
        if updated is None:
            raise not_found("Conversation")
        return updated

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> None:
        if not await db(lambda c: store.delete_conversation(c, conversation_id)):
            raise not_found("Conversation")

    @router.post("/conversations/{conversation_id}/messages")
    async def ask(conversation_id: str, body: MessageCreate) -> StreamingResponse:
        """Ask a question; the answer streams back as Server-Sent Events."""
        conversation = await db(lambda c: store.get_conversation(c, conversation_id))
        if conversation is None:
            raise not_found("Conversation")
        try:
            llm = llm_factory(settings)
        except ProviderError as exc:
            raise HTTPException(503, f"The chat model is not available: {exc}") from exc
        scope = store.scope_list(conversation["scope"])

        def start(conn: psycopg.Connection) -> list[Message]:
            history = [
                Message("user" if m["role"] == "user" else "assistant", m["content"])
                for m in store.list_messages(conn, conversation_id, last=store.HISTORY_MESSAGES)
            ]
            store.add_message(conn, conversation_id, "user", body.content)
            return history

        history = await db(start)
        server = (
            build_server(settings, embedder_factory, scope)
            if embedder_factory
            else build_server(settings, scope=scope)
        )

        async def stream() -> AsyncIterator[str]:
            async for event in run_agent(
                llm,
                server,
                history=history,
                question=body.content,
                profile=conversation["profile"],
                language=settings.language,
                scope=scope,
            ):
                if event.type == "done":
                    event.data["message_id"] = await db(
                        partial(
                            store.add_message,
                            conversation_id=conversation_id,
                            role="assistant",
                            content=event.data["answer"],
                            citations=event.data["citations"],
                        )
                    )
                yield _sse(event.type, event.data)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/messages/{message_id}/feedback", status_code=201)
    async def feedback(message_id: int, body: FeedbackCreate) -> dict[str, Any]:
        created = await db(
            lambda c: store.add_feedback(c, message_id, body.rating, body.correction)
        )
        if created is None:
            raise not_found("Message")
        return created

    return router
