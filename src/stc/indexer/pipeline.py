"""Stages 1-2 of the indexing pipeline: detection and hygiene (docs/tech-stack.md, section 4)."""

from dataclasses import replace
from pathlib import Path

from stc.indexer.detect import ApplicationSpec, detect_applications
from stc.indexer.exclusions import Exclusions
from stc.indexer.rootconfig import load_root_config
from stc.indexer.scan import ScannedApplication, scan_application
from stc.sources import DEFAULT_SOURCE, Roots


def detect_root(root: Path, source: str = DEFAULT_SOURCE) -> list[ApplicationSpec]:
    specs = detect_applications(root, load_root_config(root))
    return [replace(s, source=source) for s in specs]


def scan_root(
    root: Path, only: str | None = None, source: str = DEFAULT_SOURCE
) -> list[ScannedApplication]:
    """Scan every detected application, or just `only` (which must be detected)."""
    config = load_root_config(root)
    exclusions = Exclusions(config.exclude.patterns)
    specs = detect_applications(root, config)
    if only is not None:
        specs = [s for s in specs if s.name == only]
        if not specs:
            known = ", ".join(s.name for s in detect_applications(root, config)) or "none"
            raise ValueError(f"No application named {only!r} in the Root. Detected: {known}")
    return [scan_application(root, replace(spec, source=source), exclusions) for spec in specs]


def detect_roots(roots: Roots) -> list[ApplicationSpec]:
    specs = [s for name, path in roots.paths.items() for s in detect_root(path, name)]
    _check_unique(specs)
    return specs


def scan_roots(roots: Roots, only: str | None = None) -> list[ScannedApplication]:
    """Scan every source. With `only`, the application is looked for in all of them."""
    scanned: list[ScannedApplication] = []
    for name, path in roots.paths.items():
        if only is not None and not any(s.name == only for s in detect_root(path, name)):
            continue
        scanned += scan_root(path, only, name)
    if only is not None and not scanned:
        known = ", ".join(s.name for s in detect_roots(roots)) or "none"
        raise ValueError(f"No application named {only!r}. Detected: {known}")
    _check_unique([a.spec for a in scanned])
    return scanned


def _check_unique(specs: list[ApplicationSpec]) -> None:
    where: dict[str, str] = {}
    for spec in specs:
        other = where.setdefault(spec.name, spec.source)
        if other != spec.source:
            raise ValueError(
                f"The application {spec.name!r} exists in the sources {other!r} and "
                f"{spec.source!r}; rename one of them in its stc.toml"
            )


def host_aliases(root: Path | Roots) -> dict[str, tuple[str, ...]]:
    """Application name -> extra hosts/service names declared in stc.toml (`hosts = [...]`)."""
    paths = list(root.paths.values()) if isinstance(root, Roots) else [root]
    aliases: dict[str, tuple[str, ...]] = {}
    for path in paths:
        aliases.update(_host_aliases(path))
    return aliases


def _host_aliases(root: Path) -> dict[str, tuple[str, ...]]:
    config = load_root_config(root)
    specs = detect_applications(root, config)
    found: dict[str, list[str]] = {}
    for entry in config.application:
        if not entry.hosts or entry.exclude:
            continue
        roots = entry.roots()
        spec = next(
            (s for s in specs if (entry.name and s.name == entry.name) or s.base in roots), None
        )
        if spec is not None:
            found.setdefault(spec.name, []).extend(entry.hosts)
    return {name: tuple(hosts) for name, hosts in found.items()}
