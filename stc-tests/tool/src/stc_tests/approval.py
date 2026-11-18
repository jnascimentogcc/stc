"""Human approval of drafted scenarios.

A person decides; the tool records it. Approving a case marks it `@status:approved` in the feature
file and stores who approved it, when, and the fingerprint of the text that was approved in
`cases.json`. If the text or the specification it covers changes later, the case goes back to
"to review". Rejecting removes the scenario from the file and remembers the reason, so the same case
is not proposed again.

Only the tool edits tags and removes scenarios; it never rewrites steps."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stc_tests.scenarios import APPROVED_TAG, Cases, fingerprint

_CASE_TAG = re.compile(r"@(TC-\d{4})\b")


class ApprovalError(Exception):
    """A decision the tool cannot record."""


@dataclass(frozen=True)
class Block:
    case_id: str
    title: str
    steps: tuple[tuple[str, str], ...]
    tag_line: int
    end: int  # exclusive: the line after the last step


def parse_blocks(lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    for i, line in enumerate(lines[:-1]):
        tags = _CASE_TAG.search(line) if line.strip().startswith("@") else None
        if tags is None or not lines[i + 1].strip().startswith("Scenario:"):
            continue
        title = lines[i + 1].strip().removeprefix("Scenario:").strip()
        steps: list[tuple[str, str]] = []
        j = i + 2
        while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("@", "#")):
            keyword, _, text = lines[j].strip().partition(" ")
            steps.append((keyword, text))
            j += 1
        blocks.append(Block(tags.group(1), title, tuple(steps), i, j))
    return blocks


def locate(features: Path, case_id: str) -> tuple[Path, Block] | None:
    """The feature file (not a `.proposed` one) that holds the scenario of `case_id`."""
    for path in sorted(features.glob("*.feature")):
        if path.name.endswith(".proposed.feature"):
            continue
        for block in parse_blocks(path.read_text(encoding="utf-8").splitlines()):
            if block.case_id == case_id:
                return path, block
    return None


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def approve(
    features: Path, cases: Cases, case_ids: list[str], by: str, now: datetime | None = None
) -> list[str]:
    """Approve the cases; returns what was done. Raises, changing nothing, if one is unknown."""
    plan: list[tuple[str, Path, Block]] = []
    for case_id in case_ids:
        entry = cases.entry(case_id)
        if entry is None:
            raise ApprovalError(f"{case_id}: unknown case")
        if entry.get("status") == "rejected":
            raise ApprovalError(f"{case_id}: was rejected; it is not in the feature file")
        found = locate(features, case_id)
        if found is None:
            raise ApprovalError(f"{case_id}: not in a feature file under {features}")
        plan.append((case_id, *found))
    done: list[str] = []
    for case_id, path, block in plan:
        lines = path.read_text(encoding="utf-8").splitlines()
        if APPROVED_TAG not in lines[block.tag_line].split():
            lines[block.tag_line] += f" {APPROVED_TAG}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        entry = cases.entry(case_id)
        assert entry is not None
        entry.update(
            status="approved",
            approved_by=by,
            approved_at=_now(now),
            text_sha256=fingerprint(block.title, list(block.steps)),
        )
        done.append(f"{case_id}: approved by {by}")
    return done


def reject(
    features: Path,
    cases: Cases,
    case_ids: list[str],
    by: str,
    reason: str,
    now: datetime | None = None,
) -> list[str]:
    """Reject the cases: remove them from the feature file and record who and why."""
    done: list[str] = []
    for case_id in case_ids:
        entry = cases.entry(case_id)
        if entry is None:
            raise ApprovalError(f"{case_id}: unknown case")
        found = locate(features, case_id)
        if found is not None:
            path, block = found
            lines = path.read_text(encoding="utf-8").splitlines()
            end = (
                block.end + 1
                if block.end < len(lines) and not lines[block.end].strip()
                else block.end
            )
            del lines[block.tag_line : end]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        entry.update(
            status="rejected", rejected_by=by, rejected_at=_now(now), rejected_reason=reason
        )
        entry.pop("approved_by", None)
        entry.pop("approved_at", None)
        done.append(f"{case_id}: rejected by {by}")
    return done
