"""Stage 4 of the pipeline: extract signals from new or changed files and derive relations.

Signals are stored per file and re-extracted only when the file content or the extractor version
changes. Relations are then recomputed from all stored signals and synced: new ones are added,
changed ones updated, vanished ones removed (static relations only; LLM and manual ones stay).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import psycopg

from stc.indexer.chunk_store import _read_verified
from stc.indexer.relations import (
    AppRef,
    RelationDraft,
    SignalRow,
    aliases_to_apps,
    derive_relations,
)
from stc.indexer.signals import EXTRACTOR_VERSION, extract_signals, wanted


@dataclass
class SignalStats:
    files_extracted: int = 0
    files_without_signals_support: int = 0
    files_skipped: int = 0  # unreadable or changed since the scan
    signals: int = 0


@dataclass
class RelationStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    total: int = 0


def signals_key(content_sha256: str) -> str:
    return f"{content_sha256}:{EXTRACTOR_VERSION}"


def extract_pending(
    conn: psycopg.Connection, root: Path, *, application: str | None = None
) -> SignalStats:
    stats = SignalStats()
    rows = conn.execute(
        "SELECT f.id, f.application_id, a.path, f.path, f.language, f.content_sha256"
        " FROM files f JOIN applications a ON a.id = f.application_id"
        " WHERE f.status IN ('indexed', 'unsupported')"
        " AND f.signals_key IS DISTINCT FROM f.content_sha256 || ':' || %s"
        " AND (%s::text IS NULL OR a.name = %s) ORDER BY f.id",
        (str(EXTRACTOR_VERSION), application, application),
    ).fetchall()
    for file_id, app_id, app_path, file_path, language, sha in rows:
        path = PurePosixPath(str(file_path))
        if not wanted(path, language):
            conn.execute(
                "UPDATE files SET signals_key = %s WHERE id = %s", (signals_key(str(sha)), file_id)
            )
            stats.files_without_signals_support += 1
            continue
        text = _read_verified(root / str(app_path) / str(file_path), str(sha))
        if text is None:
            stats.files_skipped += 1
            continue
        found = extract_signals(path, text, language)
        with conn.transaction():
            conn.execute("DELETE FROM signals WHERE file_id = %s", (file_id,))
            if found:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO signals (file_id, application_id, kind, role, key, host,"
                        " detail, start_line, end_line)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [
                            (file_id, app_id, s.kind, s.role, s.key, s.host, s.detail,
                             s.start_line, s.end_line)
                            for s in found
                        ],
                    )  # fmt: skip
            conn.execute(
                "UPDATE files SET signals_key = %s WHERE id = %s", (signals_key(str(sha)), file_id)
            )
        stats.files_extracted += 1
        stats.signals += len(found)
    return stats


def rebuild_relations(
    conn: psycopg.Connection, aliases: Mapping[str, Sequence[str]] | None = None
) -> RelationStats:
    apps = aliases_to_apps(
        [(int(r[0]), str(r[1])) for r in conn.execute("SELECT id, name FROM applications")],
        aliases or {},
    )
    signals = [
        SignalRow(*row)
        for row in conn.execute(
            "SELECT id, application_id, file_id, kind, role, key, host, detail, start_line,"
            " end_line FROM signals"
        )
    ]
    return sync_relations(conn, derive_relations(apps, signals))


def sync_relations(conn: psycopg.Connection, drafts: list[RelationDraft]) -> RelationStats:
    stats = RelationStats(total=len(drafts))
    with conn.transaction():
        existing = {
            (int(r[1]), int(r[2]), str(r[3])): (int(r[0]), tuple(r[4:]))
            for r in conn.execute(
                "SELECT id, source_app_id, target_app_id, type, detail, evidence_file_id,"
                " evidence_start_line, evidence_end_line, confidence, evidence_count"
                " FROM relations WHERE origin = 'static'"
            )
        }
        wanted_keys = set()
        for d in drafts:
            key = (d.source_app_id, d.target_app_id, d.type)
            wanted_keys.add(key)
            values = (
                d.detail, d.evidence_file_id, d.evidence_start_line, d.evidence_end_line,
                d.confidence, d.evidence_count,
            )  # fmt: skip
            current = existing.get(key)
            if current is None:
                conn.execute(
                    "INSERT INTO relations (source_app_id, target_app_id, type, detail,"
                    " evidence_file_id, evidence_start_line, evidence_end_line, confidence,"
                    " evidence_count, origin)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'static')",
                    (d.source_app_id, d.target_app_id, d.type, *values),
                )
                stats.added += 1
            elif current[1] != values:
                conn.execute(
                    "UPDATE relations SET detail = %s, evidence_file_id = %s,"
                    " evidence_start_line = %s, evidence_end_line = %s, confidence = %s,"
                    " evidence_count = %s WHERE id = %s",
                    (*values, current[0]),
                )
                stats.updated += 1
        stale = [rid for key, (rid, _) in existing.items() if key not in wanted_keys]
        if stale:
            conn.execute("DELETE FROM relations WHERE id = ANY(%s)", (stale,))
            stats.removed = len(stale)
    return stats


def list_relations(conn: psycopg.Connection) -> list[tuple[str, str, str, str, str, str, int]]:
    """(source, target, type, confidence, detail, evidence 'file:lines', evidence_count)."""
    rows = conn.execute(
        "SELECT s.name, t.name, r.type, r.confidence, r.detail,"
        " COALESCE(sa.name || '/' || f.path || ':' || r.evidence_start_line || '-'"
        " || r.evidence_end_line, ''), r.evidence_count"
        " FROM relations r JOIN applications s ON s.id = r.source_app_id"
        " JOIN applications t ON t.id = r.target_app_id"
        " LEFT JOIN files f ON f.id = r.evidence_file_id"
        " LEFT JOIN applications sa ON sa.id = f.application_id"
        " ORDER BY s.name, t.name, r.type"
    ).fetchall()
    return [
        (str(a), str(b), str(c), str(d), str(e or ""), str(g), int(n))
        for a, b, c, d, e, g, n in rows
    ]


__all__ = [
    "AppRef",
    "RelationStats",
    "SignalStats",
    "extract_pending",
    "list_relations",
    "rebuild_relations",
    "signals_key",
    "sync_relations",
]
