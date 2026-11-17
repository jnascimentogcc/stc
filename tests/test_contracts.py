"""The contracts in `contracts/` (SPC-10..SPC-15): the schemas are valid, the valid fixtures pass
and every invalid fixture fails where it is meant to."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
FIXTURES = CONTRACTS / "fixtures"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SIDECAR: dict[str, Any] = _load(CONTRACTS / "spec-sidecar.schema.json")
INDEX: dict[str, Any] = _load(CONTRACTS / "spec-index.schema.json")


def _schema_for(path: Path) -> dict[str, Any]:
    is_index = path.name == "_specs.json" or path.name.startswith("index-")
    return INDEX if is_index else SIDECAR


def _errors(path: Path) -> list[tuple[str, str]]:
    validator = Draft202012Validator(_schema_for(path))
    found = validator.iter_errors(_load(path))
    return [("/".join(str(p) for p in e.absolute_path), e.validator) for e in found]


@pytest.mark.parametrize("schema", [SIDECAR, INDEX], ids=["sidecar", "index"])
def test_schema_is_valid(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", sorted((FIXTURES / "valid").glob("*.json")), ids=lambda p: p.name)
def test_valid_fixture_passes(path: Path) -> None:
    assert _errors(path) == []


# fixture -> where the first error must be (JSON path in the document) and which keyword raises it
EXPECTED = {
    "acceptance-without-scenario.spec.json": ("items/4", "required"),
    "answered-without-ids.spec.json": ("gaps/0/triage/by", "minItems"),
    "bad-hash.spec.json": ("items/0/text_sha256", "pattern"),
    "citation-line-zero.spec.json": ("items/0/citations/0/start", "minimum"),
    "generated-without-citation.spec.json": ("items/0/citations", "minItems"),
    "id-letter-mismatch.spec.json": ("items/0/id", "pattern"),
    "index-bad-sidecar-path.json": ("features/0/sidecar", "pattern"),
    "missing-retired.spec.json": ("", "required"),
    "open-with-ids.spec.json": ("gaps/1/triage/by", "maxItems"),
    "part-kind-mismatch.spec.json": ("items/0/part", "const"),
    "scenario-on-requirement.spec.json": ("items/0", "not"),
    "unknown-major.spec.json": ("schema", "pattern"),
}


def test_every_invalid_fixture_is_covered() -> None:
    names = {p.name for p in (FIXTURES / "invalid").glob("*.json")}
    assert names == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_invalid_fixture_fails_where_expected(name: str) -> None:
    errors = _errors(FIXTURES / "invalid" / name)
    assert errors, f"{name} should not validate"
    assert EXPECTED[name] in errors
