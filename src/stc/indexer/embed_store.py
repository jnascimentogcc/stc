"""Stage 5 of the pipeline: embeddings for the chunks that have none (or a stale one).

Only new or changed chunks are embedded: a chunk keeps its row (and its vector) across
re-chunking while its content is unchanged. A vector is stale when it came from another
provider, model or input format, which is how a model change triggers a re-embedding.

Progress is committed after every group of requests, so an interrupted or failed run resumes
where it stopped.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import psycopg

from stc.providers import EmbeddingsClient, ProviderError

# Bump when `embedding_text` changes: existing vectors then count as stale.
EMBED_TEXT_VERSION = "1"
MAX_EMBED_CHARS = 8_000  # keeps one pathological line under the model's input limit
_PAGE = 2_000
_HEADER_CHARS = 60  # rough size of the context header, for the estimate
_CHARS_PER_TOKEN = 4  # rough; real counts depend on the tokenizer

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class EmbedEstimate:
    chunks: int
    tokens: int  # estimated input tokens


@dataclass
class EmbedStats:
    chunks_embedded: int = 0
    chunks_replaced: int = 0  # had a stale vector from another provider/model/format
    tokens_estimated: int = 0
    requests: int = 0
    retries: int = 0


def embedding_text(application: str, path: str, kind: str, name: str | None, content: str) -> str:
    """What is sent to the embedding model: the code plus where it lives, so that a query about
    `billing` or `OrderService.cancel` matches by context as well as by content."""
    label = f"{kind} {name}" if name else kind
    return f"{application}/{path}\n{label}\n\n{content}"[:MAX_EMBED_CHARS]


_PENDING = (
    "FROM chunks c JOIN files f ON f.id = c.file_id JOIN applications a ON a.id = f.application_id"
    " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
    " WHERE f.status IN ('indexed', 'unsupported')"
    " AND (e.chunk_id IS NULL OR (e.provider, e.model, e.model_version)"
    "      IS DISTINCT FROM (%(provider)s, %(model)s, %(version)s))"
    " AND (%(app)s::text IS NULL OR a.name = %(app)s)"
)


def _params(provider: str, model: str, application: str | None) -> dict[str, str | None]:
    return {
        "provider": provider,
        "model": model,
        "version": EMBED_TEXT_VERSION,
        "app": application,
    }


def estimate_pending(
    conn: psycopg.Connection, provider: str, model: str, *, application: str | None = None
) -> EmbedEstimate:
    """Chunks that need a vector and a rough token count (LCY-07); makes no external call."""
    row = conn.execute(
        "SELECT count(*), COALESCE(sum(least(length(c.content) + %(header)s, %(cap)s)), 0) "
        + _PENDING,
        {**_params(provider, model, application), "header": _HEADER_CHARS, "cap": MAX_EMBED_CHARS},
    ).fetchone()
    assert row is not None
    return EmbedEstimate(int(row[0]), math.ceil(int(row[1]) / _CHARS_PER_TOKEN))


async def embed_pending(
    conn: psycopg.Connection,
    client: EmbeddingsClient,
    *,
    application: str | None = None,
    per_request: int = 128,
    concurrency: int = 4,
    attempts: int = 4,
    sleep: Sleep = asyncio.sleep,
) -> EmbedStats:
    """Embed every pending chunk. Raises `ProviderError` on a failure that retries do not fix;
    chunks embedded before that stay stored."""
    stats = EmbedStats()
    params = _params(client.provider, client.model, application)
    last_id = 0
    while True:
        rows = conn.execute(
            "SELECT c.id, a.name, f.path, c.kind, c.name, c.content, e.chunk_id IS NOT NULL "
            + _PENDING
            + " AND c.id > %(last)s ORDER BY c.id LIMIT %(limit)s",
            {**params, "last": last_id, "limit": _PAGE},
        ).fetchall()
        if not rows:
            return stats
        last_id = int(rows[-1][0])
        step = per_request * concurrency
        for start in range(0, len(rows), step):
            await _embed_group(
                conn, client, rows[start : start + step], per_request, attempts, sleep, stats
            )


async def _embed_group(
    conn: psycopg.Connection,
    client: EmbeddingsClient,
    rows: list[tuple[object, ...]],
    per_request: int,
    attempts: int,
    sleep: Sleep,
    stats: EmbedStats,
) -> None:
    texts = [
        embedding_text(
            str(r[1]), str(r[2]), str(r[3]), r[4] if r[4] is None else str(r[4]), str(r[5])
        )
        for r in rows
    ]
    batches = [texts[i : i + per_request] for i in range(0, len(texts), per_request)]
    results = await asyncio.gather(
        *(_embed_with_retry(client, batch, attempts, sleep, stats) for batch in batches),
        return_exceptions=True,
    )
    # Store every batch that succeeded, even if another one failed: what is pending is decided
    # by the query, not by order, so the next run simply picks up the rest.
    failure: BaseException | None = None
    vectors: list[list[float]] = []
    stored_rows: list[tuple[object, ...]] = []
    stored_texts: list[str] = []
    offset = 0
    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            failure = failure or result
        else:
            vectors.extend(result)
            stored_rows.extend(rows[offset : offset + len(batch)])
            stored_texts.extend(batch)
        offset += len(batch)

    if stored_rows:
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunk_embeddings (chunk_id, provider, model, model_version, embedding)"
                " VALUES (%s, %s, %s, %s, %s::vector)"
                " ON CONFLICT (chunk_id) DO UPDATE SET provider = EXCLUDED.provider,"
                " model = EXCLUDED.model, model_version = EXCLUDED.model_version,"
                " embedding = EXCLUDED.embedding, created_at = now()",
                [
                    (
                        row[0],
                        client.provider,
                        client.model,
                        EMBED_TEXT_VERSION,
                        "[" + ",".join(repr(x) for x in vector) + "]",
                    )
                    for row, vector in zip(stored_rows, vectors, strict=True)
                ],
            )
        stats.chunks_embedded += len(stored_rows)
        stats.chunks_replaced += sum(1 for row in stored_rows if row[6])
        stats.tokens_estimated += math.ceil(sum(len(t) for t in stored_texts) / _CHARS_PER_TOKEN)
    if failure is not None:
        raise failure


async def _embed_with_retry(
    client: EmbeddingsClient,
    batch: list[str],
    attempts: int,
    sleep: Sleep,
    stats: EmbedStats,
) -> list[list[float]]:
    for attempt in range(1, attempts + 1):
        stats.requests += 1
        try:
            return await client.embed(batch)
        except ProviderError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            stats.retries += 1
            await sleep(2.0 ** (attempt - 1))
    raise AssertionError("unreachable")
