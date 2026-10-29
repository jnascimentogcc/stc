"""Application detection from project markers, regardless of language (IDX-02, IDX-03).

Rules (the edge cases of functional-spec open question 1):
- a directory with a project marker is an application, and its subtree belongs to it
  (nested markers do not split it);
- workspace/aggregator roots (npm workspaces, Maven parent POMs, Cargo/Gradle workspaces) are
  not applications themselves: detection descends into them (solution files are not markers);
- `stc.toml` can include, exclude, rename, merge and split (see `rootconfig`).
"""

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from stc.indexer.exclusions import Exclusions
from stc.indexer.rootconfig import ApplicationConfig, RootConfig

# Marker file name patterns that make a directory an application.
_APP_MARKERS = (
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "*.csproj", "*.fsproj",
    "*.vbproj", "pyproject.toml", "setup.py", "Pipfile", "go.mod", "Cargo.toml",
    "composer.json", "Gemfile", "mix.exs", "pubspec.yaml", "Package.swift",
)  # fmt: skip
_GRADLE_SETTINGS = ("settings.gradle", "settings.gradle.kts")

DetectedBy = Literal["auto", "manual"]


@dataclass(frozen=True)
class ApplicationSpec:
    """`base` is the directory the application's file paths are relative to; `roots` are the
    directories actually scanned (several when applications were merged in stc.toml)."""

    name: str
    base: PurePosixPath
    roots: tuple[PurePosixPath, ...]
    detected_by: DetectedBy
    markers: tuple[str, ...] = field(default=())


def detect_applications(root: Path, config: RootConfig | None = None) -> list[ApplicationSpec]:
    config = config or RootConfig()
    exclusions = Exclusions(config.exclude.patterns)
    found = _walk_for_applications(root, exclusions)
    specs = _apply_config(root, found, config)
    return _assign_unique_names(specs)


def _walk_for_applications(
    root: Path, exclusions: Exclusions
) -> dict[PurePosixPath, tuple[str, ...]]:
    apps: dict[PurePosixPath, tuple[str, ...]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        rel = PurePosixPath(Path(current).relative_to(root).as_posix())
        markers = _app_markers(Path(current), filenames)
        if markers:
            apps[rel] = tuple(markers)
            dirnames.clear()  # the application owns its subtree
            continue
        dirnames[:] = sorted(d for d in dirnames if exclusions.dir_excluded(rel / d) is None)
    return apps


def _app_markers(directory: Path, filenames: list[str]) -> list[str]:
    markers = [n for n in filenames if any(fnmatch.fnmatchcase(n, p) for p in _APP_MARKERS)]
    return [m for m in markers if not _is_aggregator(directory, m, filenames)]


def _is_aggregator(directory: Path, marker: str, siblings: list[str]) -> bool:
    text = _read_small(directory / marker)
    if marker == "package.json":
        try:
            return "workspaces" in json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
    if marker == "pom.xml":
        return bool(re.search(r"<packaging>\s*pom\s*</packaging>", text)) and "<modules>" in text
    if marker == "Cargo.toml":
        return "[workspace]" in text and "[package]" not in text
    if marker.startswith("build.gradle"):
        for settings in _GRADLE_SETTINGS:
            if settings in siblings and re.search(
                r"\binclude\b", _read_small(directory / settings)
            ):
                return True
    return False


def _read_small(path: Path, limit: int = 256_000) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _apply_config(
    root: Path,
    found: dict[PurePosixPath, tuple[str, ...]],
    config: RootConfig,
) -> list[ApplicationSpec]:
    auto: dict[PurePosixPath, ApplicationSpec] = {
        path: ApplicationSpec(
            name=_default_name(root, path), base=path, roots=(path,), detected_by="auto", markers=m
        )
        for path, m in found.items()
    }
    manual: list[ApplicationSpec] = []
    splits: dict[PurePosixPath, list[ApplicationSpec]] = {}

    for entry in config.application:
        roots = entry.roots()
        for r in roots:
            if not (root / r.as_posix()).is_dir():
                raise ValueError(f"stc.toml: {r.as_posix()!r} is not a directory under the Root")
        if entry.exclude:
            for r in roots:
                for path in [p for p in auto if p == r or _inside(p, r)]:
                    del auto[path]
            continue
        if entry.paths:
            for r in roots:
                for path in [p for p in auto if p == r or _inside(p, r)]:
                    del auto[path]
            manual.append(_spec(root, entry, roots))
            continue
        (only,) = roots
        if only in auto:
            current = auto[only]
            auto[only] = ApplicationSpec(
                name=entry.name or current.name,
                base=current.base,
                roots=current.roots,
                detected_by=current.detected_by,
                markers=current.markers,
            )
            continue
        container = next((p for p in auto if _inside(only, p)), None)
        if container is not None:
            splits.setdefault(container, []).append(_spec(root, entry, roots))
            continue
        manual.append(_spec(root, entry, roots))

    for container, parts in splits.items():
        del auto[container]
        manual.extend(parts)
    return sorted([*auto.values(), *manual], key=lambda s: s.base.as_posix())


def _spec(root: Path, entry: ApplicationConfig, roots: list[PurePosixPath]) -> ApplicationSpec:
    base = PurePosixPath(os.path.commonpath([r.as_posix() for r in roots]))
    name = entry.name or _default_name(root, roots[0])
    return ApplicationSpec(name=name, base=base, roots=tuple(roots), detected_by="manual")


def _inside(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return parent != path and (parent == PurePosixPath(".") or parent in path.parents)


def _default_name(root: Path, path: PurePosixPath) -> str:
    return path.name if path != PurePosixPath(".") else root.resolve().name


def _assign_unique_names(specs: list[ApplicationSpec]) -> list[ApplicationSpec]:
    """Names are unique: on a clash the colliding applications use their relative path."""
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.name] = counts.get(spec.name, 0) + 1
    result: list[ApplicationSpec] = []
    taken: set[str] = set()
    for spec in specs:
        name = spec.name
        if counts[name] > 1 and spec.base != PurePosixPath("."):
            name = spec.base.as_posix()
        if name in taken:
            raise ValueError(f"Duplicate application name {name!r}; set one in stc.toml")
        taken.add(name)
        result.append(ApplicationSpec(name, spec.base, spec.roots, spec.detected_by, spec.markers))
    return result
