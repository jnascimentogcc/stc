"""What changed in the Root since the last indexing, without writing anything (LCY-02).

Compares a fresh scan with the files table: the same content hash and classification means
unchanged, exactly as `sync_registry` decides."""

from collections.abc import Collection
from dataclasses import dataclass

import psycopg

from stc.indexer.scan import ScannedApplication


@dataclass
class AppChanges:
    name: str
    new_application: bool = False
    added: int = 0
    changed: int = 0
    removed: int = 0

    @property
    def any(self) -> bool:
        return self.new_application or bool(self.added or self.changed or self.removed)


def pending_changes(
    conn: psycopg.Connection, scanned: list[ScannedApplication]
) -> list[AppChanges]:
    out: list[AppChanges] = []
    known = {str(r[1]): int(r[0]) for r in conn.execute("SELECT id, name FROM applications")}
    for app in scanned:
        changes = AppChanges(app.spec.name)
        app_id = known.get(app.spec.name)
        if app_id is None:
            changes.new_application = True
            changes.added = len(app.files)
            out.append(changes)
            continue
        existing = {
            str(r[0]): (r[1], r[2], r[3], r[4], r[5])
            for r in conn.execute(
                "SELECT path, language, size_bytes, status, exclusion_reason, content_sha256"
                " FROM files WHERE application_id = %s",
                (app_id,),
            )
        }
        wanted = {f.path: f for f in app.files}
        changes.removed = len(set(existing) - set(wanted))
        for path, record in wanted.items():
            current = existing.get(path)
            if current is None:
                changes.added += 1
            elif current != (
                record.language,
                record.size_bytes,
                record.status,
                record.exclusion_reason,
                record.content_sha256,
            ):
                changes.changed += 1
        out.append(changes)
    return out


def removed_applications(
    conn: psycopg.Connection, scanned: list[ScannedApplication], sources: Collection[str]
) -> list[str]:
    """Indexed applications that the scanned sources no longer contain."""
    seen = {a.spec.name for a in scanned}
    rows = conn.execute("SELECT name FROM applications WHERE source = ANY(%s)", (list(sources),))
    return sorted(str(r[0]) for r in rows if r[0] not in seen)
