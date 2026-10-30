"""Stage 3 of the pipeline: chunk the files that are new or changed and store chunks + symbols.

Incremental (LCY-01): a file is re-chunked only when its content hash or the chunker version
changed. Chunks whose content is unchanged keep their row (and therefore their embedding), so
only new or changed chunks need embeddings later.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import psycopg

from stc.indexer.chunking import CHUNKER_VERSION, ChunkedFile, chunk_file
from stc.indexer.redact import REDACTION_VERSION, redact
from stc.sources import Roots, as_roots


@dataclass
class ChunkStats:
    files_chunked: int = 0
    files_as_text: int = 0  # no grammar for the language, or the grammar was unavailable
    files_skipped: int = 0  # unreadable or changed since the scan (picked up on the next run)
    chunks_new: int = 0
    chunks_kept: int = 0
    chunks_removed: int = 0
    symbols: int = 0
    files_redacted: int = 0  # configuration files in which credential values were replaced
    values_redacted: int = 0


# What a file's chunks were made with: the chunker and the redaction rules (new rules mean the
# files are read again, so a value that used to slip through is not kept).
CHUNK_VERSION = f"{CHUNKER_VERSION}.{REDACTION_VERSION}"


def chunk_key(content_sha256: str) -> str:
    return f"{content_sha256}:{CHUNK_VERSION}"


def chunk_pending(
    conn: psycopg.Connection, root: Path | Roots, *, application: str | None = None
) -> ChunkStats:
    stats = ChunkStats()
    roots = as_roots(root)
    rows = conn.execute(
        "SELECT f.id, a.path, f.path, f.language, f.content_sha256, a.source"
        " FROM files f JOIN applications a ON a.id = f.application_id"
        " WHERE f.status IN ('indexed', 'unsupported')"
        " AND f.chunked_key IS DISTINCT FROM f.content_sha256 || ':' || %s"
        " AND (%s::text IS NULL OR a.name = %s) AND a.source = ANY(%s)"
        " ORDER BY f.id",
        (CHUNK_VERSION, application, application, list(roots.paths)),
    ).fetchall()
    for file_id, app_path, file_path, language, sha, source in rows:
        base = roots.get(str(source))
        read = read_verified(base / str(app_path) / str(file_path), str(sha)) if base else None
        if read is None:
            stats.files_skipped += 1
            continue
        text, redacted = read
        if redacted:
            stats.files_redacted += 1
            stats.values_redacted += redacted
        chunked = chunk_file(PurePosixPath(str(file_path)), text, language)
        with conn.transaction():
            _replace(conn, int(file_id), str(sha), chunked, stats)
        stats.files_chunked += 1
        if not chunked.parsed:
            stats.files_as_text += 1
    return stats


def read_verified(path: Path, expected_sha: str) -> tuple[str, int] | None:
    """The text of a file as it was scanned, with the credential values of configuration files
    replaced (and how many were), or None if it cannot be read or changed since the scan. This is
    the only way indexed content leaves the disk, so what is not in the text never reaches a
    chunk, a signal, an embedding, a catalog or an answer."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != expected_sha:
        return None  # changed after the scan; the next `stc index` registers the new content
    redacted = redact(path.name, data.decode("utf-8", errors="replace"))
    return redacted.text, redacted.values


def _read_verified(path: Path, expected_sha: str) -> str | None:
    read = read_verified(path, expected_sha)
    return read[0] if read else None


def _replace(
    conn: psycopg.Connection, file_id: int, sha: str, chunked: ChunkedFile, stats: ChunkStats
) -> None:
    existing: dict[tuple[str, str, str | None], list[int]] = {}
    for chunk_id, digest, kind, name in conn.execute(
        "SELECT id, content_sha256, kind, name FROM chunks WHERE file_id = %s ORDER BY id",
        (file_id,),
    ):
        existing.setdefault((str(digest), str(kind), name), []).append(int(chunk_id))

    chunk_ids: list[int] = []
    for draft in chunked.chunks:
        reusable = existing.get((draft.content_sha256, draft.kind, draft.name))
        if reusable:
            chunk_id = reusable.pop(0)
            conn.execute(
                "UPDATE chunks SET start_line = %s, end_line = %s WHERE id = %s"
                " AND (start_line, end_line) IS DISTINCT FROM (%s, %s)",
                (draft.start_line, draft.end_line, chunk_id, draft.start_line, draft.end_line),
            )
            stats.chunks_kept += 1
        else:
            row = conn.execute(
                "INSERT INTO chunks (file_id, kind, name, start_line, end_line, content,"
                " content_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    file_id,
                    draft.kind,
                    draft.name,
                    draft.start_line,
                    draft.end_line,
                    draft.content,
                    draft.content_sha256,
                ),
            ).fetchone()
            assert row is not None
            chunk_id = int(row[0])
            stats.chunks_new += 1
        chunk_ids.append(chunk_id)

    leftovers = [cid for ids in existing.values() for cid in ids]
    if leftovers:
        conn.execute("DELETE FROM chunks WHERE id = ANY(%s)", (leftovers,))
        stats.chunks_removed += len(leftovers)

    conn.execute("DELETE FROM symbols WHERE file_id = %s", (file_id,))
    if chunked.symbols:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO symbols (file_id, chunk_id, name, kind, is_definition,"
                " start_line, end_line) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        file_id,
                        chunk_ids[s.chunk_index] if s.chunk_index is not None else None,
                        s.name,
                        s.kind,
                        s.is_definition,
                        s.start_line,
                        s.end_line,
                    )
                    for s in chunked.symbols
                ],
            )
        stats.symbols += len(chunked.symbols)
    conn.execute("UPDATE files SET chunked_key = %s WHERE id = %s", (chunk_key(sha), file_id))
