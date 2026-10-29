"""The record of indexing runs: what was indexed, when, from which version, with what result
(XCT-05). One row in `indexing_runs` per `stc index` / `stc reindex`."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

STALE_AFTER = "1 hour"  # a run still 'running' after this long was cut short (process killed)
SUMMARY_ERROR_CHARS = 300


@dataclass(frozen=True)
class RunRecord:
    id: int
    kind: str  # full | incremental | application
    application: str | None
    status: str  # running | succeeded | failed | partial
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]
    error: str | None

    @property
    def seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


def has_finished_run(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM indexing_runs WHERE status IN ('succeeded', 'partial') LIMIT 1"
    ).fetchone()
    return row is not None


def start_run(conn: psycopg.Connection, kind: str, application: str | None = None) -> int:
    """Open a run. Runs left 'running' by a process that died are closed as failed first."""
    conn.execute(
        "UPDATE indexing_runs SET status = 'failed', finished_at = now(),"
        " error = 'the process ended before the run finished'"
        f" WHERE status = 'running' AND started_at < now() - interval '{STALE_AFTER}'"
    )
    row = conn.execute(
        "INSERT INTO indexing_runs (kind, application_id, status, summary)"
        " VALUES (%s, (SELECT id FROM applications WHERE name = %s), 'running', %s)"
        " RETURNING id",
        (kind, application, Jsonb({"application": application} if application else {})),
    ).fetchone()
    assert row is not None
    return int(row[0])


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: str,
    summary: dict[str, Any],
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE indexing_runs SET status = %s, finished_at = now(), summary = %s, error = %s"
        " WHERE id = %s",
        (status, Jsonb(summary), (error or "")[:SUMMARY_ERROR_CHARS] or None, run_id),
    )


def recent_runs(conn: psycopg.Connection, limit: int = 5) -> list[RunRecord]:
    rows = conn.execute(
        "SELECT r.id, r.kind, a.name, r.status, r.started_at, r.finished_at, r.summary, r.error"
        " FROM indexing_runs r LEFT JOIN applications a ON a.id = r.application_id"
        " ORDER BY r.id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        RunRecord(
            int(r[0]),
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            r[6] if isinstance(r[6], dict) else json.loads(r[6]),
            r[7],
        )
        for r in rows
    ]
