import copy
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

# Bump when sections, prompts or the output format change: catalogs are then regenerated.
SCHEMA_VERSION = 1

Confidence = Literal["high", "medium", "low"]

# Sections written by the LLM, from context assembled by code.
LLM_SECTIONS = ("purpose", "business", "stack", "modules", "data_model", "configuration", "flows")
# Sections built deterministically from static analysis (no LLM).
STATIC_SECTIONS = ("interfaces", "integrations")
SECTION_ORDER = (
    "purpose", "business", "stack", "modules", "data_model",
    "interfaces", "integrations", "configuration", "flows",
)  # fmt: skip
SECTION_TITLES = {
    "purpose": "Functional purpose",
    "business": "Business description",
    "stack": "Stack and versions",
    "modules": "Module structure",
    "data_model": "Entities and data model",
    "interfaces": "Exposed and consumed interfaces",
    "integrations": "Integrations and relations",
    "configuration": "Configuration and environment variables",
    "flows": "Main flows and entry points",
}


@dataclass(frozen=True)
class Evidence:
    file: str  # relative to the application, as in the `files` table
    start_line: int
    end_line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class Scenario:
    """An acceptance scenario: preconditions, actions and observable results (SPC-13)."""

    given: list[str]
    when: list[str]
    then: list[str]

    def text(self) -> str:
        """The scenario as one sentence: "Given a and b, when c, then d."."""
        clauses = [
            f"{word} {' and '.join(lines)}"
            for word, lines in (("given", self.given), ("when", self.when), ("then", self.then))
            if lines
        ]
        sentence = ", ".join(clauses)
        return sentence[:1].upper() + sentence[1:] + "."


@dataclass
class Claim:
    text: str
    confidence: Confidence
    evidence: list[Evidence]
    scenario: Scenario | None = None


@dataclass
class SectionResult:
    id: str
    claims: list[Claim] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    failed: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


class _EvidenceModel(BaseModel):
    file: str
    start_line: int
    end_line: int


class _ClaimModel(BaseModel):
    text: str = Field(min_length=1)
    confidence: Confidence
    evidence: list[_EvidenceModel]


class _SectionModel(BaseModel):
    claims: list[_ClaimModel]
    gaps: list[str] = Field(default_factory=list)


def _lines(values: list[str]) -> list[str]:
    return [" ".join(v.split()) for v in values if v.strip()]


class _ScenarioModel(BaseModel):
    given: list[str] = Field(default_factory=list)
    when: list[str] = Field(min_length=1)
    then: list[str] = Field(min_length=1)


class _ScenarioClaimModel(BaseModel):
    scenario: _ScenarioModel
    confidence: Confidence
    evidence: list[_EvidenceModel]


class _ScenarioSectionModel(BaseModel):
    claims: list[_ScenarioClaimModel]
    gaps: list[str] = Field(default_factory=list)


# JSON Schema sent to the LLM (kept flat, no $ref, for the widest provider support).
SECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                            },
                            "required": ["file", "start_line", "end_line"],
                        },
                    },
                },
                "required": ["text", "confidence", "evidence"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "gaps"],
}


def _scenario_schema() -> dict[str, Any]:
    """The same reply, but each statement is a scenario with its three lists instead of a text."""
    schema = copy.deepcopy(SECTION_JSON_SCHEMA)
    item = schema["properties"]["claims"]["items"]
    lines = {"type": "array", "items": {"type": "string"}}
    item["properties"].pop("text")
    item["properties"]["scenario"] = {
        "type": "object",
        "properties": {
            "given": lines,
            "when": {**lines, "minItems": 1},
            "then": {**lines, "minItems": 1},
        },
        "required": ["given", "when", "then"],
    }
    item["required"] = ["scenario", "confidence", "evidence"]
    return schema


SCENARIO_JSON_SCHEMA: dict[str, Any] = _scenario_schema()


def parse_scenario_output(raw: str) -> tuple[list[Claim], list[str]]:
    """Like `parse_section_output` for acceptance scenarios (SPC-13): every statement carries
    `given`, `when` and `then`, and its text is built from them, so the two cannot disagree."""
    try:
        model = _ScenarioSectionModel.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(str(exc).splitlines()[0] if str(exc) else "invalid JSON") from exc
    claims: list[Claim] = []
    for c in model.claims:
        scenario = Scenario(
            _lines(c.scenario.given), _lines(c.scenario.when), _lines(c.scenario.then)
        )
        if not scenario.when or not scenario.then:
            continue
        evidence = [Evidence(e.file, e.start_line, e.end_line) for e in c.evidence]
        claims.append(Claim(scenario.text(), c.confidence, evidence, scenario))
    return claims, [" ".join(g.split()) for g in model.gaps if g.strip()]


def parse_section_output(raw: str) -> tuple[list[Claim], list[str]]:
    """Parse and structurally validate the model's JSON; raises ValueError if unusable."""
    try:
        model = _SectionModel.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(str(exc).splitlines()[0] if str(exc) else "invalid JSON") from exc
    claims = [
        Claim(
            " ".join(c.text.split()).lstrip("#- ").strip(),
            c.confidence,
            [Evidence(e.file, e.start_line, e.end_line) for e in c.evidence],
        )
        for c in model.claims
    ]
    return [c for c in claims if c.text], [" ".join(g.split()) for g in model.gaps if g.strip()]
