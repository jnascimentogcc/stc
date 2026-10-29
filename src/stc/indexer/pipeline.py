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
