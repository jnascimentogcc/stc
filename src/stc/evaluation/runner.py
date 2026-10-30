"""Runs the reference questions against the index: retrieval (free) and answers (uses the model)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg

from stc.chat.agent import run_agent
from stc.config import Settings
from stc.evaluation.metrics import RetrievalScore, score_retrieval
from stc.evaluation.model import Question
from stc.mcp_server import build_server
from stc.providers import LLMClient
from stc.query.engine import QueryEngine
from stc.query.models import QueryError
from stc.query.search import QueryEmbedder

MAX_K = 50


@dataclass
class RetrievalOutcome:
    hybrid: RetrievalScore | None  # None without an embedder
    lexical: RetrievalScore
    top: list[str] = field(default_factory=list)  # the first refs retrieved, for debugging
    error: str | None = None


@dataclass
class AnswerOutcome:
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


def _locations(hits: list[Any]) -> list[tuple[str, str, int, int]]:
    return [
        (h.source.application, h.source.file, h.source.start_line, h.source.end_line) for h in hits
    ]


def run_retrieval(
    conn: psycopg.Connection,
    settings: Settings,
    questions: list[Question],
    k: int,
    embedder: QueryEmbedder | None,
) -> dict[str, RetrievalOutcome]:
    """Search each question with the full hybrid search and with words only, so the effect of the
    embeddings on finding the evidence is visible. Only questions with expected evidence."""
    k = min(k, MAX_K)
    hybrid = QueryEngine(conn, root=settings.roots(), catalogs=settings.catalogs, embedder=embedder)
    lexical = QueryEngine(conn, root=settings.roots(), catalogs=settings.catalogs, embedder=None)
    out: dict[str, RetrievalOutcome] = {}
    for question in questions:
        if not question.expect.evidence:
            continue
        try:
            plain = lexical.search_code(question.question, applications=question.scope, limit=k)
            mixed = (
                hybrid.search_code(question.question, applications=question.scope, limit=k)
                if embedder is not None
                else None
            )
        except QueryError as exc:
            empty = RetrievalScore([None] * len(question.expect.evidence), None)
            out[question.id] = RetrievalOutcome(None, empty, error=exc.message)
            continue
        best = mixed or plain
        out[question.id] = RetrievalOutcome(
            score_retrieval(question, _locations(mixed.items)) if mixed else None,
            score_retrieval(question, _locations(plain.items)),
            [str(h.source) for h in best.items[:5]],
        )
    return out


async def run_answers(
    settings: Settings,
    llm: LLMClient,
    questions: list[Question],
    *,
    embedder_factory: Callable[[Settings], QueryEmbedder | None] | None = None,
    on_progress: Callable[[Question, AnswerOutcome], None] | None = None,
) -> dict[str, AnswerOutcome]:
    """Ask each question through the chat agent, one after another, with its own scope."""
    out: dict[str, AnswerOutcome] = {}
    for question in questions:
        server = (
            build_server(settings, embedder_factory, question.scope)
            if embedder_factory
            else build_server(settings, scope=question.scope)
        )
        outcome = AnswerOutcome()
        async for event in run_agent(
            llm,
            server,
            history=[],
            question=question.question,
            profile=question.profile,
            language=settings.language,
            scope=question.scope,
        ):
            if event.type == "error":
                outcome.error = str(event.data["message"])
            elif event.type == "done":
                data = event.data
                outcome.answer = data["answer"]
                outcome.citations = data["citations"]
                outcome.warnings = data["warnings"]
                outcome.steps = data["steps"]
                outcome.input_tokens = data["usage"]["input_tokens"]
                outcome.output_tokens = data["usage"]["output_tokens"]
        out[question.id] = outcome
        if on_progress:
            on_progress(question, outcome)
    return out
