"""Persists detected applications and their files (IDX-07). Idempotent: syncing an unchanged Root
changes nothing (XCT-04); files that disappeared are removed (LCY-04)."""

from dataclasses import dataclass, field

import psycopg

from stc.indexer.scan import FileRecord, ScannedApplication


@dataclass
class AppSyncResult:
    name: str
    files_added: int = 0
    files_changed: int = 0
    files_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.files_added or self.files_changed or self.files_removed)


@dataclass
class SyncResult:
    applications: list[AppSyncResult] = field(default_factory=list)
    apps_added: list[str] = field(default_factory=list)
    apps_missing: list[str] = field(default_factory=list)  # in the DB, not found in the Root
    apps_removed: list[str] = field(default_factory=list)


def sync_registry(
    conn: psycopg.Connection,
    scanned: list[ScannedApplication],
    *,
    prune: bool = False,
) -> SyncResult:
    """Make the DB reflect `scanned`. Applications missing from the scan are only removed with
    `prune`, so pointing at the wrong folder cannot wipe the knowledge base by accident."""
    result = SyncResult()
    with conn.transaction():
        known = {
            str(row[1]): int(row[0]) for row in conn.execute("SELECT id, name FROM applications")
        }
        seen: set[str] = set()
        for app in scanned:
            name = app.spec.name
            seen.add(name)
            app_id = known.get(name)
            if app_id is None:
                row = conn.execute(
                    "INSERT INTO applications (name, path, detected_by, indexed_version)"
                    " VALUES (%s, %s, %s, %s) RETURNING id",
                    (name, app.spec.base.as_posix(), app.spec.detected_by, app.code_version),
                ).fetchone()
                assert row is not None
                app_id = int(row[0])
                result.apps_added.append(name)
            else:
                conn.execute(
                    "UPDATE applications SET path = %s, detected_by = %s, indexed_version = %s,"
                    " updated_at = now() WHERE id = %s AND (path, detected_by,"
                    " indexed_version) IS DISTINCT FROM (%s, %s, %s)",
                    (
                        app.spec.base.as_posix(),
                        app.spec.detected_by,
                        app.code_version,
                        app_id,
                        app.spec.base.as_posix(),
                        app.spec.detected_by,
                        app.code_version,
                    ),
                )
            result.applications.append(_sync_files(conn, app_id, app))

        missing = sorted(set(known) - seen)
        if prune:
            for name in missing:
                conn.execute("DELETE FROM applications WHERE id = %s", (known[name],))
            result.apps_removed = missing
        else:
            result.apps_missing = missing
    return result


def _sync_files(conn: psycopg.Connection, app_id: int, app: ScannedApplication) -> AppSyncResult:
    out = AppSyncResult(app.spec.name)
    existing = {
        str(r[1]): (int(r[0]), r[2:])
        for r in conn.execute(
            "SELECT id, path, language, size_bytes, status, exclusion_reason, content_sha256"
            " FROM files WHERE application_id = %s",
            (app_id,),
        )
    }
    wanted = {f.path: f for f in app.files}

    removed = [fid for path, (fid, _) in existing.items() if path not in wanted]
    if removed:
        conn.execute("DELETE FROM files WHERE id = ANY(%s)", (removed,))
        out.files_removed = len(removed)

    with conn.cursor() as cur:
        for path, record in wanted.items():
            current = existing.get(path)
            if current is None:
                cur.execute(
                    "INSERT INTO files (application_id, path, language, size_bytes, status,"
                    " exclusion_reason, content_sha256, indexed_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, now())",
                    (app_id, *_row(record)),
                )
                out.files_added += 1
            elif tuple(current[1]) != _row(record)[1:]:
                cur.execute(
                    "UPDATE files SET language = %s, size_bytes = %s, status = %s,"
                    " exclusion_reason = %s, content_sha256 = %s, indexed_at = now()"
                    " WHERE id = %s",
                    (*_row(record)[1:], current[0]),
                )
                if record.status not in ("indexed", "unsupported"):
                    # A file that became a secret (or is otherwise excluded) must leave no
                    # derived knowledge behind (IDX-06, XCT-01).
                    cur.execute("DELETE FROM chunks WHERE file_id = %s", (current[0],))
                    cur.execute("DELETE FROM symbols WHERE file_id = %s", (current[0],))
                    cur.execute("DELETE FROM signals WHERE file_id = %s", (current[0],))
                    cur.execute(
                        "UPDATE files SET chunked_key = NULL, signals_key = NULL WHERE id = %s",
                        (current[0],),
                    )
                out.files_changed += 1
    return out


def _row(r: FileRecord) -> tuple[str, str | None, int, str, str | None, str | None]:
    return (r.path, r.language, r.size_bytes, r.status, r.exclusion_reason, r.content_sha256)
