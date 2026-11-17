"""Reads the specifications of one application from the folder `stc spec` wrote."""

import json
from pathlib import Path
from typing import Any

from stc_tests.contract import INDEX, SIDECAR, ContractError, validate
from stc_tests.model import Application, Feature, Gap, Item


def _load(path: Path, contract: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{path}: not a JSON object")
    try:
        validate(document, contract)
    except ContractError as error:
        # keep the class (UnsupportedVersion stays one), add where it happened
        raise type(error)(f"{path}: {error}") from error
    return document


def applications(specs: Path) -> list[str]:
    """Application folders in `specs` that have an index."""
    if not specs.is_dir():
        return []
    return sorted(p.name for p in specs.iterdir() if (p / "_specs.json").is_file())


def read_application(specs: Path, folder: str) -> Application:
    """Read and validate the index and every sidecar of the application in `specs/folder`."""
    root = specs / folder
    index = _load(root / "_specs.json", INDEX)
    features: list[Feature] = []
    for entry in index["features"]:
        sidecar = _load(root / entry["sidecar"], SIDECAR)
        features.append(
            Feature(
                slug=sidecar["feature"]["slug"],
                title=sidecar["feature"]["title"],
                code_version=sidecar["code_version"],
                items=tuple(
                    Item(i["id"], i["kind"], i["text"], i["text_sha256"], i["confidence"])
                    for i in sidecar["items"]
                ),
                retired=tuple(r["id"] for r in sidecar.get("retired", [])),
                gaps=tuple(
                    Gap(g["id"], g["text"], g.get("triage", {}).get("status", "open") == "open")
                    for g in sidecar.get("gaps", [])
                ),
            )
        )
    return Application(index["application"], index["code_version"], tuple(features))
