import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.embed_store import (
    EMBED_TEXT_VERSION,
    MAX_EMBED_CHARS,
    embed_pending,
    embedding_text,
    estimate_pending,
)
from stc.indexer.pipeline import scan_root
from stc.indexer.registry import sync_registry
from stc.providers import ProviderError


class FakeEmbeddings:
    """Deterministic 1536-d vectors derived from the text; records every request."""

    dimensions = 1536

    def __init__(self, model: str = "fake-model", *, fail: list[ProviderError] | None = None):
        self.provider = "fake"
        self.model = model
        self.requests: list[list[str]] = []
        self._fail = fail or []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.requests.append(list(texts))
        if self._fail:
            raise self._fail.pop(0)
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()[0] / 255.0
        return [seed] * self.dimensions

    @property
    def texts(self) -> list[str]:
        return [t for batch in self.requests for t in batch]


async def no_sleep(_seconds: float) -> None:
    return None


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def setup(conn: psycopg.Connection, root: Path) -> None:
    sync_registry(conn, scan_root(root))
    chunk_pending(conn, root)


def scalar(conn: psycopg.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def run(conn: psycopg.Connection, client: FakeEmbeddings, **kwargs: object):  # type: ignore[no-untyped-def]
    return asyncio.run(embed_pending(conn, client, sleep=no_sleep, **kwargs))  # type: ignore[arg-type]


def make_root(root: Path) -> None:
    write(root, "app/pyproject.toml", "[project]\n")
    write(root, "app/svc.py", "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")


def test_embedding_text_has_context_and_is_capped() -> None:
    text = embedding_text("billing", "src/a.py", "method", "Svc.run", "return 1")
    assert text == "billing/src/a.py\nmethod Svc.run\n\nreturn 1"
    assert embedding_text("a", "b", "module", None, "x")[:20] == "a/b\nmodule\n\nx"
    assert len(embedding_text("a", "b", "text", None, "x" * 50_000)) == MAX_EMBED_CHARS


def test_embeds_only_pending_chunks_and_stores_vectors(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        total = scalar(conn, "SELECT count(*) FROM chunks")
        estimate = estimate_pending(conn, "fake", "fake-model")
        assert estimate.chunks == total and estimate.tokens > 0

        client = FakeEmbeddings()
        stats = run(conn, client)
        assert stats.chunks_embedded == total and stats.chunks_replaced == 0
        assert len(client.texts) == total
        assert any(t.startswith("app/svc.py\nfunction alpha") for t in client.texts)
        row = conn.execute(
            "SELECT provider, model, model_version, vector_dims(embedding) FROM chunk_embeddings"
            " LIMIT 1"
        ).fetchone()
        assert row == ("fake", "fake-model", EMBED_TEXT_VERSION, 1536)

        again = FakeEmbeddings()
        assert run(conn, again).chunks_embedded == 0 and not again.requests
        assert estimate_pending(conn, "fake", "fake-model").chunks == 0


def test_only_changed_chunks_are_re_embedded(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        run(conn, FakeEmbeddings())
        write(
            tmp_path,
            "app/svc.py",
            "def alpha():\n    return 1\n\n\ndef beta():\n    return 3\n",  # only beta changes
        )
        setup(conn, tmp_path)
        client = FakeEmbeddings()
        stats = run(conn, client)
        assert stats.chunks_embedded == 1
        assert "return 3" in client.texts[0] and len(client.texts) == 1


def test_changing_the_model_replaces_stale_vectors(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        run(conn, FakeEmbeddings("model-a"))
        total = scalar(conn, "SELECT count(*) FROM chunks")
        assert estimate_pending(conn, "fake", "model-b").chunks == total

        stats = run(conn, FakeEmbeddings("model-b"))
        assert (stats.chunks_embedded, stats.chunks_replaced) == (total, total)
        assert (
            scalar(conn, "SELECT count(*) FROM chunk_embeddings WHERE model = 'model-b'") == total
        )
        assert scalar(conn, "SELECT count(*) FROM chunk_embeddings") == total  # replaced, not added


def test_secret_files_are_never_embedded(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    write(tmp_path, "app/.env", "TOKEN=abc123\n")
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        client = FakeEmbeddings()
        run(conn, client)
        assert not any("abc123" in t for t in client.texts)


def test_requests_are_batched(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/m.py", "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(5)))
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        client = FakeEmbeddings()
        stats = run(conn, client, per_request=2, concurrency=2)
        assert stats.chunks_embedded == 6  # 5 functions + pyproject
        assert sorted(len(r) for r in client.requests) == [2, 2, 2]


def test_retryable_errors_are_retried_then_succeed(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    make_root(tmp_path)
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        flaky = FakeEmbeddings(fail=[ProviderError("fake", "429", retryable=True)])
        stats = run(conn, flaky)
        assert stats.retries == 1 and stats.chunks_embedded > 0


def test_permanent_failure_keeps_progress_and_resumes(temp_db_url: str, tmp_path: Path) -> None:
    migrate(temp_db_url)
    write(tmp_path, "app/pyproject.toml", "[project]\n")
    write(tmp_path, "app/m.py", "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(4)))
    with psycopg.connect(temp_db_url) as conn:
        setup(conn, tmp_path)
        total = scalar(conn, "SELECT count(*) FROM chunks")

        class FailsSecondRequest(FakeEmbeddings):
            async def embed(self, texts: Sequence[str]) -> list[list[float]]:
                if len(self.requests) == 1:
                    self.requests.append(list(texts))
                    raise ProviderError("fake", "bad request", retryable=False)
                return await super().embed(texts)

        with pytest.raises(ProviderError, match="bad request"):
            run(conn, FailsSecondRequest(), per_request=2, concurrency=1)
        done = scalar(conn, "SELECT count(*) FROM chunk_embeddings")
        assert 0 < done < total  # the first request was stored

        stats = run(conn, FakeEmbeddings(), per_request=2)
        assert stats.chunks_embedded == total - done
        assert scalar(conn, "SELECT count(*) FROM chunk_embeddings") == total
