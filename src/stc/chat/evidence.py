"""Checks the citations in an answer against what the tools actually returned (CHT-03, CHT-04).

The model is told to cite `application/file:start-end`. A citation counts as verified only if a
tool result in this turn contained that file with a line range covering the cited lines, so a
made-up path or a wrong line shows up as unverified instead of looking like proof."""

import re
from dataclasses import dataclass, field
from typing import Any

_REF = re.compile(r"(?<![\w/.-])([\w@.\-]+(?:/[\w@.\-]+)*\.[A-Za-z0-9_]+):(\d+)(?:-(\d+))?")


@dataclass
class Citation:
    ref: str
    verified: bool
    application: str | None = None
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class Evidence:
    """What the tools returned in one turn."""

    # "application/file" -> covered line ranges
    seen: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    where: dict[str, tuple[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def _add(self, key: str, start: int, end: int, application: str | None, file: str) -> None:
        self.seen.setdefault(key, []).append((start, max(start, end)))
        if application:
            self.where[key] = (application, file)

    def observe(self, data: Any, application: str | None = None) -> None:
        """Collect the locations in a tool result and any 'outdated' or 'stale' flags."""
        if isinstance(data, dict):
            app = data.get("application") if isinstance(data.get("application"), str) else None
            file = data.get("file")
            start, end = data.get("start_line"), data.get("end_line")
            if app and isinstance(file, str) and isinstance(start, int) and isinstance(end, int):
                self._add(f"{app}/{file}", start, end, app, file)
            if data.get("outdated") is True and app:
                self._warn(f"The catalog of {app} is older than the indexed code.")
            if data.get("stale") is True:
                self._warn("A file that was read changed on disk after it was indexed.")
            for value in data.values():
                self.observe(value, app or application)
        elif isinstance(data, list):
            for item in data:
                self.observe(item, application)
        elif isinstance(data, str):
            for match in _REF.finditer(data):
                path, first, last = match.group(1), int(match.group(2)), match.group(3)
                end = int(last) if last else first
                self._add(path, first, end, None, path)
                if application:
                    self._add(f"{application}/{path}", first, end, application, path)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def check(self, answer: str) -> list[Citation]:
        """Every distinct `path:lines` in the answer, marked verified or not."""
        found: dict[str, Citation] = {}
        for match in _REF.finditer(answer):
            path, start = match.group(1), int(match.group(2))
            end = int(match.group(3)) if match.group(3) else start
            ref = match.group(0)
            if ref in found:
                continue
            ok = any(lo <= start and end <= hi for lo, hi in self.seen.get(path, []))
            app, file = self.where.get(path, (None, None))
            found[ref] = Citation(ref, ok, app if ok else None, file if ok else None, start, end)
        return list(found.values())
