"""Embeddings of the catalog sections, so the functional knowledge can be searched by meaning
(QRY-05) and not only by words.

A section needs a vector when it has none or when its vector came from another provider, model
or text format; the text sent is the application, the section title and the section body (the
verified statements, never the code itself)."""

import asyncio
import math
from dataclasses import dataclass

import psycopg

from stc.catalog.model import SECTION_TITLES
from stc.indexer.embed_store import EmbedStats, Sleep, _embed_with_retry
from stc.providers import EmbeddingsClient

# Bump when `section_text` changes: existing vectors then count as stale.
SECTION_TEXT_VERSION = "1"
MAX_SECTION_CHARS = 8_000
PER_REQUEST = 32
_CHARS_PER_TOKEN = 4

_PENDING = (
    "FROM catalog_sections cs JOIN catalogs c ON c.id = cs.catalog_id"
    " JOIN applications a ON a.id = c.application_id"
    " LEFT JOIN catalog_section_embeddings e ON e.section_id = cs.id"
    " WHERE length(btrim(cs.content)) > 0"
    " AND (e.section_id IS NULL OR (e.provider, e.model, e.model_version)"
    "      IS DISTINCT FROM (%(provider)s, %(model)s, %(version)s))"
    " AND (%(app)s::text IS NULL OR a.name = %(app)s)"
)


@dataclass(frozen=True)
class SectionEstimate:
    sections: int
    tokens: int


def section_text(application: str, section: str, content: str) -> str:
    title = SECTION_TITLES.get(section, section)
    return f"{application} - {title}\n\n{content}"[:MAX_SECTION_CHARS]


def _params(provider: str, model: str, application: str | None) -> dict[str, str | None]:
    return {
        "provider": provider,
        "model": model,
        "version": SECTION_TEXT_VERSION,
        "app": application,
    }


def estimate_sections(
    conn: psycopg.Connection, provider: str, model: str, *, application: str | None = None
) -> SectionEstimate:
    row = conn.execute(
        "SELECT count(*), COALESCE(sum(least(length(cs.content) + 60, %(cap)s)), 0) " + _PENDING,
        {**_params(provider, model, application), "cap": MAX_SECTION_CHARS},
    ).fetchone()
    assert row is not None
    return SectionEstimate(int(row[0]), math.ceil(int(row[1]) / _CHARS_PER_TOKEN))


async def embed_sections(
    conn: psycopg.Connection,
    client: EmbeddingsClient,
    *,
    application: str | None = None,
    attempts: int = 4,
    sleep: Sleep = asyncio.sleep,
) -> EmbedStats:
    """Embed every pending section. Raises `ProviderError` on a failure that retries do not fix;
    sections embedded before that stay stored."""
    stats = EmbedStats()
    params = _params(client.provider, client.model, application)
    while True:
        rows = conn.execute(
            "SELECT cs.id, a.name, cs.section, cs.content, e.section_id IS NOT NULL "
            + _PENDING
            + " ORDER BY cs.id LIMIT %(limit)s",
            {**params, "limit": PER_REQUEST},
        ).fetchall()
        if not rows:
            return stats
        texts = [section_text(str(r[1]), str(r[2]), str(r[3])) for r in rows]
        vectors = await _embed_with_retry(client, texts, attempts, sleep, stats)
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalog_section_embeddings"
                " (section_id, provider, model, model_version, embedding)"
                " VALUES (%s, %s, %s, %s, %s::vector)"
                " ON CONFLICT (section_id) DO UPDATE SET provider = EXCLUDED.provider,"
                " model = EXCLUDED.model, model_version = EXCLUDED.model_version,"
                " embedding = EXCLUDED.embedding, created_at = now()",
                [
                    (
                        row[0],
                        client.provider,
                        client.model,
                        SECTION_TEXT_VERSION,
                        "[" + ",".join(repr(x) for x in vector) + "]",
                    )
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
        stats.chunks_embedded += len(rows)  # here: sections
        stats.chunks_replaced += sum(1 for row in rows if row[4])
        stats.tokens_estimated += math.ceil(sum(len(t) for t in texts) / _CHARS_PER_TOKEN)
