"""Matching and scoring: pure functions, no database and no model."""

import re
from dataclasses import dataclass, field
from typing import Any

from stc.evaluation.model import ExpectedEvidence, Question


def location_matches(
    expected: ExpectedEvidence, application: str, file: str, start: int, end: int
) -> bool:
    """The same file, and (if the question names lines) overlapping lines."""
    if expected.application != application or expected.file != file:
        return False
    if expected.lines is None:
        return True
    return start <= expected.lines[1] and expected.lines[0] <= end


def first_rank(
    expected: ExpectedEvidence, locations: list[tuple[str, str, int, int]]
) -> int | None:
    """1-based position of the first retrieved location that matches, or None."""
    for rank, (application, file, start, end) in enumerate(locations, start=1):
        if location_matches(expected, application, file, start, end):
            return rank
    return None


@dataclass
class RetrievalScore:
    ranks: list[int | None]  # one per expected evidence, in order
    first_rank: int | None  # the best (lowest) of them

    def recall_at(self, k: int) -> float:
        if not self.ranks:
            return 1.0
        return sum(1 for r in self.ranks if r is not None and r <= k) / len(self.ranks)

    def reciprocal_rank(self) -> float:
        return 1.0 / self.first_rank if self.first_rank else 0.0


def score_retrieval(
    question: Question, locations: list[tuple[str, str, int, int]]
) -> RetrievalScore:
    ranks = [first_rank(e, locations) for e in question.expect.evidence]
    found = [r for r in ranks if r is not None]
    return RetrievalScore(ranks, min(found) if found else None)


def names_application(answer: str, application: str) -> bool:
    return (
        re.search(rf"(?<![\w-]){re.escape(application)}(?![\w-])", answer, re.IGNORECASE)
        is not None
    )


@dataclass
class AnswerScore:
    checks: dict[str, bool] = field(default_factory=dict)
    evidence_found: int = 0
    evidence_expected: int = 0
    citations_verified: int = 0
    citations_total: int = 0

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    @property
    def has_verified_evidence(self) -> bool:
        return self.citations_verified > 0


def score_answer(question: Question, answer: str, citations: list[dict[str, Any]]) -> AnswerScore:
    """Checks of one answer. `citations` are the agent's, with `verified` and the location."""
    lowered = answer.lower()
    expect = question.expect
    score = AnswerScore(
        citations_verified=sum(1 for c in citations if c["verified"]),
        citations_total=len(citations),
        evidence_expected=len(expect.evidence),
    )
    checks = score.checks

    if expect.applications:
        checks["applications"] = all(names_application(answer, a) for a in expect.applications)
    if expect.evidence:
        verified = [
            (c["application"], c["file"], c["start_line"], c["end_line"])
            for c in citations
            if c["verified"] and c["application"] and c["file"]
        ]
        score.evidence_found = sum(1 for e in expect.evidence if first_rank(e, verified))
        checks["evidence"] = score.evidence_found == len(expect.evidence)
    if expect.contains:
        checks["contains"] = all(s.lower() in lowered for s in expect.contains)
    if question.admissions:
        checks["admits"] = any(s.lower() in lowered for s in question.admissions)
    elif expect.contains_any:
        checks["contains_any"] = any(s.lower() in lowered for s in expect.contains_any)
    if expect.not_contains:
        checks["not_contains"] = not any(s.lower() in lowered for s in expect.not_contains)
    if question.kind == "answerable" and not expect.evidence:
        # Nothing specific is expected, so at least some verified evidence must back the answer.
        checks["evidence"] = score.citations_verified > 0
    return score
