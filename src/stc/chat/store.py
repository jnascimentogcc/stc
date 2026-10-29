"""Conversations, messages and feedback (CHT-05, CHT-06, CHT-08, CHT-09).

Only the question and the final answer of each turn are stored, with the verified citations; the
tool calls of a turn are not, so a follow-up question sees what was said, not how it was found."""

import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from stc.chat.prompt import PROFILES
from stc.query.common import known_applications
from stc.query.models import QueryError

HISTORY_MESSAGES = 12  # most recent messages given to the model as context
TITLE_CHARS = 60


def scope_json(applications: list[str] | None) -> dict[str, Any]:
    if applications is None:
        return {"type": "root"}
    return {"type": "applications", "applications": applications}


def scope_list(scope: dict[str, Any]) -> list[str] | None:
    return list(scope["applications"]) if scope.get("type") == "applications" else None


def _validate(conn: psycopg.Connection, applications: list[str] | None, profile: str) -> None:
    if profile not in PROFILES:
        raise QueryError("invalid_argument", f"profile must be one of: {', '.join(PROFILES)}.")
    if applications is not None:
        known = known_applications(conn)
        unknown = [a for a in applications if a not in known]
        if not applications or unknown:
            raise QueryError(
                "unknown_application",
                "Scope needs at least one indexed application"
                + (f"; unknown: {', '.join(unknown)}" if unknown else "")
                + f". Indexed applications: {', '.join(known) or 'none'}.",
            )


def _row(r: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(r[0]),
        "title": r[1],
        "scope": r[2],
        "profile": r[3],
        "created_at": r[4].isoformat(),
        "updated_at": r[5].isoformat(),
    }


_COLUMNS = "id, title, scope, profile, created_at, updated_at"


def create_conversation(
    conn: psycopg.Connection, *, applications: list[str] | None = None, profile: str = "technical"
) -> dict[str, Any]:
    _validate(conn, applications, profile)
    row = conn.execute(
        f"INSERT INTO conversations (scope, profile) VALUES (%s, %s) RETURNING {_COLUMNS}",
        (Jsonb(scope_json(applications)), profile),
    ).fetchone()
    assert row is not None
    return _row(row)


def get_conversation(conn: psycopg.Connection, conversation_id: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()
    except psycopg.errors.InvalidTextRepresentation:  # not a UUID
        return None
    return _row(row) if row else None


def list_conversations(
    conn: psycopg.Connection, *, limit: int = 30, offset: int = 0
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM conversations ORDER BY updated_at DESC, id LIMIT %s OFFSET %s",
        (limit, offset),
    ).fetchall()
    return [_row(r) for r in rows]


def update_conversation(
    conn: psycopg.Connection,
    conversation_id: str,
    *,
    title: str | None = None,
    applications: list[str] | None = None,
    all_applications: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Change the title, the scope (`applications`, or `all_applications` for the whole Root)
    or the profile; whatever is not given stays."""
    current = get_conversation(conn, conversation_id)
    if current is None:
        return None
    new_scope = (
        None
        if all_applications
        else applications
        if applications is not None
        else scope_list(current["scope"])
    )
    new_profile = profile or current["profile"]
    _validate(conn, new_scope, new_profile)
    row = conn.execute(
        f"UPDATE conversations SET title = %s, scope = %s, profile = %s, updated_at = now()"
        f" WHERE id = %s RETURNING {_COLUMNS}",
        (
            title if title is not None else current["title"],
            Jsonb(scope_json(new_scope)),
            new_profile,
            conversation_id,
        ),
    ).fetchone()
    assert row is not None
    return _row(row)


def delete_conversation(conn: psycopg.Connection, conversation_id: str) -> bool:
    try:
        cur = conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
    except psycopg.errors.InvalidTextRepresentation:
        return False
    return cur.rowcount > 0


def add_message(
    conn: psycopg.Connection,
    conversation_id: str,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, citations)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (conversation_id, role, content, Jsonb(citations or [])),
    ).fetchone()
    assert row is not None
    conn.execute(
        "UPDATE conversations SET updated_at = now(), title = COALESCE(title, %s) WHERE id = %s",
        (content[:TITLE_CHARS].strip() if role == "user" else None, conversation_id),
    )
    return int(row[0])


def list_messages(
    conn: psycopg.Connection, conversation_id: str, *, last: int | None = None
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, role, content, citations, created_at FROM messages"
        " WHERE conversation_id = %s ORDER BY id",
        (conversation_id,),
    ).fetchall()
    messages = [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "citations": r[3] if isinstance(r[3], list) else json.loads(r[3]),
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]
    return messages[-last:] if last else messages


def add_feedback(
    conn: psycopg.Connection, message_id: int, rating: str, correction: str | None
) -> dict[str, Any] | None:
    """Flag an assistant answer as incorrect or incomplete, with an optional correction
    (CHT-09). Turning a correction into a catalog change is a separate, approved step."""
    if rating not in ("incorrect", "incomplete"):
        raise QueryError("invalid_argument", "rating must be 'incorrect' or 'incomplete'.")
    message = conn.execute("SELECT role FROM messages WHERE id = %s", (message_id,)).fetchone()
    if message is None:
        return None
    if message[0] != "assistant":
        raise QueryError("invalid_argument", "Only answers can be flagged.")
    row = conn.execute(
        "INSERT INTO feedback (message_id, rating, correction) VALUES (%s, %s, %s)"
        " RETURNING id, message_id, rating, correction, proposal_status, created_at",
        (message_id, rating, (correction or "").strip() or None),
    ).fetchone()
    assert row is not None
    return {
        "id": row[0],
        "message_id": row[1],
        "rating": row[2],
        "correction": row[3],
        "proposal_status": row[4],
        "created_at": row[5].isoformat(),
    }
