"""Corrections to a catalog proposed from the chat and approved by a maintainer (CHT-09, CHT-10).

A user flags an answer and writes what is right. That text is only a *proposal*: it names the
application and the catalog section it would correct and waits. Nothing reaches a catalog until a
maintainer approves it from the command line (the web chat has no authentication, so it cannot be
trusted to approve anything). Approving writes the text into the section's manual block in the
markdown file, where it is authoritative: shown first, kept on regeneration and given to the model
as a correction (CAT-05)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

from stc.catalog.model import SECTION_ORDER
from stc.catalog.render import append_manual
from stc.catalog.store import _write_atomic, catalog_path
from stc.query.models import QueryError

MAX_TEXT = 4_000


@dataclass(frozen=True)
class Correction:
    id: int
    feedback_id: int | None
    application: str
    section: str
    text: str
    status: str  # proposed | approved | rejected
    note: str | None
    created_at: datetime
    decided_at: datetime | None
    question: str | None  # what was asked, when the proposal came from the chat
    answer: str | None


_SELECT = (
    "SELECT c.id, c.feedback_id, a.name, c.section, c.text, c.status, c.decision_note,"
    " c.created_at, c.decided_at,"
    " (SELECT u.content FROM messages u WHERE u.conversation_id = m.conversation_id"
    "  AND u.role = 'user' AND u.id < m.id ORDER BY u.id DESC LIMIT 1), m.content"
    " FROM catalog_corrections c JOIN applications a ON a.id = c.application_id"
    " LEFT JOIN feedback f ON f.id = c.feedback_id LEFT JOIN messages m ON m.id = f.message_id"
)


def _row(r: tuple[object, ...]) -> Correction:
    return Correction(*r)  # type: ignore[arg-type]


def get_correction(conn: psycopg.Connection, correction_id: int) -> Correction | None:
    row = conn.execute(_SELECT + " WHERE c.id = %s", (correction_id,)).fetchone()
    return _row(row) if row else None


def list_corrections(
    conn: psycopg.Connection, status: str | None = None, limit: int = 50
) -> list[Correction]:
    if status is not None and status not in ("proposed", "approved", "rejected"):
        raise QueryError("invalid_argument", "status must be proposed, approved or rejected.")
    rows = conn.execute(
        _SELECT + " WHERE (%s::text IS NULL OR c.status = %s) ORDER BY c.id DESC LIMIT %s",
        (status, status, limit),
    ).fetchall()
    return [_row(r) for r in rows]


def pending_count(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "SELECT count(*) FROM catalog_corrections WHERE status = 'proposed'"
    ).fetchone()
    return int(row[0]) if row else 0


def propose(
    conn: psycopg.Connection,
    feedback_id: int,
    application: str,
    section: str,
    text: str | None = None,
) -> Correction:
    """Turn the correction in a piece of feedback into a proposal for one catalog section."""
    feedback = conn.execute(
        "SELECT correction FROM feedback WHERE id = %s", (feedback_id,)
    ).fetchone()
    if feedback is None:
        raise QueryError("not_found", "That feedback does not exist.")
    if section not in SECTION_ORDER:
        raise QueryError(
            "invalid_argument",
            f"Unknown section {section!r}. Sections: {', '.join(SECTION_ORDER)}.",
        )
    known = [str(r[0]) for r in conn.execute("SELECT name FROM applications ORDER BY name")]
    if application not in known:
        raise QueryError(
            "unknown_application",
            f"Unknown application {application!r}. Indexed applications: "
            + (", ".join(known) or "none"),
        )
    body = (text if text is not None else feedback[0] or "").strip()
    if not body:
        raise QueryError("invalid_argument", "There is no correction text to propose.")
    if "stc:manual" in body:
        raise QueryError("invalid_argument", "The correction may not contain catalog markers.")
    if len(body) > MAX_TEXT:
        raise QueryError(
            "invalid_argument", f"The correction is longer than {MAX_TEXT} characters."
        )
    if conn.execute(
        "SELECT 1 FROM catalog_corrections WHERE feedback_id = %s AND status = 'proposed'",
        (feedback_id,),
    ).fetchone():
        raise QueryError("invalid_argument", "This feedback already has a proposal waiting.")
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO catalog_corrections (feedback_id, application_id, section, text)"
            " VALUES (%s, (SELECT id FROM applications WHERE name = %s), %s, %s) RETURNING id",
            (feedback_id, application, section, body),
        ).fetchone()
        assert row is not None
        conn.execute(
            "UPDATE feedback SET proposal_status = 'proposed' WHERE id = %s", (feedback_id,)
        )
    created = get_correction(conn, int(row[0]))
    assert created is not None
    return created


def _open(conn: psycopg.Connection, correction_id: int) -> Correction:
    found = get_correction(conn, correction_id)
    if found is None:
        raise QueryError("not_found", f"There is no correction #{correction_id}.")
    if found.status != "proposed":
        raise QueryError(
            "invalid_argument", f"Correction #{correction_id} is already {found.status}."
        )
    return found


def _decide(conn: psycopg.Connection, found: Correction, status: str, note: str | None) -> None:
    conn.execute(
        "UPDATE catalog_corrections SET status = %s, decision_note = %s, decided_at = now()"
        " WHERE id = %s",
        (status, (note or "").strip() or None, found.id),
    )
    if found.feedback_id is not None:
        conn.execute(
            "UPDATE feedback SET proposal_status = %s WHERE id = %s", (status, found.feedback_id)
        )


def approve(
    conn: psycopg.Connection, catalogs_dir: Path, correction_id: int, note: str | None = None
) -> Correction:
    """Write the correction into the catalog file and mark it approved. Fails, changing nothing,
    if the application has no catalog yet or the file has no block for the section."""
    found = _open(conn, correction_id)
    path = catalog_path(catalogs_dir, found.application)
    if not path.is_file():
        raise QueryError(
            "not_found",
            f"{found.application} has no catalog file yet (run `stc catalog {found.application}`).",
        )
    try:
        updated = append_manual(path.read_text(encoding="utf-8"), found.section, found.text)
    except KeyError as exc:
        raise QueryError(
            "invalid_argument",
            f"The catalog of {found.application} has no manual block for {found.section!r}; "
            "regenerate it with `stc catalog --force`.",
        ) from exc
    with conn.transaction():
        _write_atomic(path, updated)
        _decide(conn, found, "approved", note)
    approved = get_correction(conn, correction_id)
    assert approved is not None
    return approved


def reject(conn: psycopg.Connection, correction_id: int, note: str | None = None) -> Correction:
    found = _open(conn, correction_id)
    with conn.transaction():
        _decide(conn, found, "rejected", note)
    rejected = get_correction(conn, correction_id)
    assert rejected is not None
    return rejected
