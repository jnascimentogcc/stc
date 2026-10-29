"""Stages 1-2 of the indexing pipeline: detection and hygiene (docs/tech-stack.md, section 4)."""

from pathlib import Path

from stc.indexer.detect import ApplicationSpec, detect_applications
from stc.indexer.exclusions import Exclusions
from stc.indexer.rootconfig import load_root_config
from stc.indexer.scan import ScannedApplication, scan_application


def detect_root(root: Path) -> list[ApplicationSpec]:
    return detect_applications(root, load_root_config(root))


def scan_root(root: Path) -> list[ScannedApplication]:
    config = load_root_config(root)
    exclusions = Exclusions(config.exclude.patterns)
    specs = detect_applications(root, config)
    return [scan_application(root, spec, exclusions) for spec in specs]


def host_aliases(root: Path) -> dict[str, tuple[str, ...]]:
    """Application name -> extra hosts/service names declared in stc.toml (`hosts = [...]`)."""
    config = load_root_config(root)
    specs = detect_applications(root, config)
    aliases: dict[str, list[str]] = {}
    for entry in config.application:
        if not entry.hosts or entry.exclude:
            continue
        roots = entry.roots()
        spec = next(
            (s for s in specs if (entry.name and s.name == entry.name) or s.base in roots), None
        )
        if spec is not None:
            aliases.setdefault(spec.name, []).extend(entry.hosts)
    return {name: tuple(hosts) for name, hosts in aliases.items()}
