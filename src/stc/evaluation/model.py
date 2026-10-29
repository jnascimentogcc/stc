"""The reference questions (XCT-08) and how they are loaded.

A question says what a good answer must contain, in terms that can be checked without a human:
the applications it names, the code that proves it (file and lines), words it must or must not
contain. An unanswerable question has no evidence: its answer must admit that the code does not
say (QRY-10)."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Phrases that count as admitting that the information was not found, when a question does not
# give its own `contains_any`.
DEFAULT_ADMISSIONS = (
    "could not find",
    "couldn't find",
    "not found",
    "no evidence",
    "does not show",
    "do not show",
    "not shown",
    "cannot determine",
    "can't determine",
    "unable to find",
    "no indication",
    "not possible to determine",
    "does not appear",
    "not present",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedEvidence(_Strict):
    application: str
    file: str
    lines: tuple[int, int] | None = None  # omitted: any line of the file counts

    @field_validator("lines")
    @classmethod
    def _ordered(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and not 1 <= value[0] <= value[1]:
            raise ValueError("lines must be [first, last] with 1 <= first <= last")
        return value


class Expect(_Strict):
    applications: list[str] = Field(default_factory=list)  # must be named in the answer
    evidence: list[ExpectedEvidence] = Field(default_factory=list)
    contains: list[str] = Field(default_factory=list)  # all, case-insensitive
    contains_any: list[str] = Field(default_factory=list)  # at least one
    not_contains: list[str] = Field(default_factory=list)  # none (e.g. a secret value)


class Question(_Strict):
    id: str
    question: str
    kind: Literal["answerable", "unanswerable"] = "answerable"
    scope: list[str] | None = None
    profile: Literal["technical", "functional"] = "technical"
    expect: Expect = Field(default_factory=Expect)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("id may only contain letters, digits, '.', '_' and '-'")
        return value

    @property
    def admissions(self) -> list[str]:
        if self.kind != "unanswerable":
            return []
        return self.expect.contains_any or list(DEFAULT_ADMISSIONS)


class QuestionSet(_Strict):
    version: Literal[1]
    questions: list[Question]

    @field_validator("questions")
    @classmethod
    def _unique(cls, value: list[Question]) -> list[Question]:
        ids = [q.id for q in value]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f"duplicate question id(s): {', '.join(duplicated)}")
        if not value:
            raise ValueError("there are no questions")
        return value


def load_questions(path: Path) -> QuestionSet:
    """Read and validate a questions file; raises `ValueError` with a readable message."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    try:
        return QuestionSet.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ValueError(f"{path} is not a valid question set: {problems}") from exc
