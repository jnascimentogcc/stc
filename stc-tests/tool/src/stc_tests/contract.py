"""The contract with STC: the version check and the validation of what STC writes.

The schemas live in `contracts/` at the root of the repository and are the only thing the two
programs share. A document of a major version this program does not know is refused before
anything is read from it (SPC-15)."""

import json
import os
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SIDECAR = "stc.spec-sidecar"
INDEX = "stc.spec-index"
SUPPORTED_MAJOR = 1
CONTRACTS_ENV = "STC_TESTS_CONTRACTS"


class ContractError(Exception):
    """A document that does not follow the contract."""


class UnsupportedVersion(ContractError):
    """A major version of the contract this program does not know."""


def contracts_dir() -> Path:
    """`STC_TESTS_CONTRACTS`, else the `contracts/` folder of the repository this file is in."""
    configured = os.environ.get(CONTRACTS_ENV)
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        if (parent / "contracts" / "spec-sidecar.schema.json").is_file():
            return parent / "contracts"
    raise ContractError(f"contracts/ not found: set {CONTRACTS_ENV} to the folder that has it")


@cache
def _validator(name: str) -> Draft202012Validator:
    path = contracts_dir() / f"{name}.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def check_version(document: Mapping[str, Any], contract: str) -> None:
    """Raise if `document` is not `contract` or is a major version other than the supported one."""
    declared = document.get("schema")
    match = re.fullmatch(rf"{re.escape(contract)}/(\d+)(?:\.(\d+))?", str(declared))
    if match is None:
        raise ContractError(f"not a {contract} document (schema: {declared!r})")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        raise UnsupportedVersion(
            f"{declared!r} is a major version this program does not know "
            f"(it reads {contract}/{SUPPORTED_MAJOR})"
        )


def violations(document: Mapping[str, Any], schema: str) -> list[str]:
    """What is wrong with `document` against `schema` ("spec-sidecar" or "spec-index")."""
    found = _validator(schema).iter_errors(document)
    return sorted(
        f"{'/'.join(str(p) for p in e.absolute_path) or '(document)'}: {e.message}" for e in found
    )


def validate(document: Mapping[str, Any], contract: str) -> None:
    """Check the version, then the schema; raise `ContractError` with what is wrong."""
    check_version(document, contract)
    schema = "spec-sidecar" if contract == SIDECAR else "spec-index"
    problems = violations(document, schema)
    if problems:
        raise ContractError("; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
