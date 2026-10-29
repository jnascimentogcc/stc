"""Walks an application's files and decides, per file, what happens to it (IDX-05..IDX-07).

Order of checks: exclusions -> secret file name -> size -> binary -> generated -> secret content.
Anything excluded keeps its reason (never a secret value) so it can be reported (IDX-06).
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from stc.indexer.detect import ApplicationSpec
from stc.indexer.exclusions import Exclusions
from stc.indexer.languages import language_of
from stc.indexer.secrets import secret_content_reason, secret_filename_reason

MAX_FILE_BYTES = 1_000_000
_HEAD_BYTES = 8_000

FileStatus = Literal["indexed", "excluded", "secret", "unsupported", "error"]


@dataclass(frozen=True)
class FileRecord:
    path: str  # relative to the application's base, POSIX separators
    language: str | None
    size_bytes: int
    status: FileStatus
    exclusion_reason: str | None
    content_sha256: str | None


@dataclass(frozen=True)
class ScannedApplication:
    spec: ApplicationSpec
    files: tuple[FileRecord, ...]
    code_version: str | None

    def count(self, status: FileStatus) -> int:
        return sum(1 for f in self.files if f.status == status)


def scan_application(
    root: Path, spec: ApplicationSpec, exclusions: Exclusions | None = None
) -> ScannedApplication:
    exclusions = exclusions or Exclusions()
    records: dict[str, FileRecord] = {}
    for app_root in spec.roots:
        for current, dirnames, filenames in os.walk(root / app_root.as_posix(), followlinks=False):
            rel_dir = PurePosixPath(Path(current).relative_to(root).as_posix())
            dirnames[:] = sorted(
                d for d in dirnames if exclusions.dir_excluded(rel_dir / d) is None
            )
            for filename in sorted(filenames):
                root_rel = rel_dir / filename
                record = _classify(root / root_rel.as_posix(), root_rel, spec.base, exclusions)
                records[record.path] = record
    return ScannedApplication(
        spec=spec,
        files=tuple(records[k] for k in sorted(records)),
        code_version=code_version(root / spec.base.as_posix()),
    )


def _classify(
    absolute: Path, root_rel: PurePosixPath, base: PurePosixPath, exclusions: Exclusions
) -> FileRecord:
    rel = (
        root_rel.relative_to(base).as_posix() if base != PurePosixPath(".") else root_rel.as_posix()
    )

    def record(
        status: FileStatus,
        reason: str | None = None,
        *,
        size: int = 0,
        language: str | None = None,
        sha: str | None = None,
    ) -> FileRecord:
        return FileRecord(rel, language, size, status, reason, sha)

    try:
        if absolute.is_symlink():
            return record("excluded", "symbolic link")
        size = absolute.stat().st_size
        if reason := exclusions.file_excluded(root_rel):
            return record("excluded", reason, size=size)
        if reason := secret_filename_reason(root_rel):
            return record("secret", reason, size=size)
        if size > MAX_FILE_BYTES:
            return record("excluded", "file too large", size=size)
        data = absolute.read_bytes()
        if b"\x00" in data[:_HEAD_BYTES]:
            return record("excluded", "binary or asset", size=size)
        if exclusions.looks_generated(data[:_HEAD_BYTES]):
            return record("excluded", "generated file", size=size)
        if reason := secret_content_reason(absolute):
            return record("secret", reason, size=size)
        language = language_of(root_rel)
        sha = hashlib.sha256(data).hexdigest()
        return record(
            "indexed" if language else "unsupported", size=size, language=language, sha=sha
        )
    except OSError as exc:
        return record("error", f"{type(exc).__name__}: {exc.strerror or exc}")


def code_version(directory: Path) -> str | None:
    """Current git commit of the directory, when it is inside a repository (LCY-08)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and len(sha) == 40 else None
