"""The scenario writer: drafts Gherkin from the specification, bound to the action catalog.

The model proposes; the code decides. A scenario is kept only when every requirement it covers
exists and is active, every step is an instance of an action in the catalog and it has an
assertion. A step with no action is not dropped silently: it becomes a missing-action record for an
automation engineer. Case IDs and the Gherkin text are made here, never by the model."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stc_tests.catalog import Action, matches
from stc_tests.llm import JsonLLM, Usage
from stc_tests.model import Feature, Item

PROMPT_VERSION = "scenarios-1"
APPROVED_TAG = "@status:approved"
TESTABLE = {"requirement", "rule", "data", "interface", "screen", "api", "acceptance"}
KEYWORDS = ("Given", "When", "Then", "And", "But")

SYSTEM_PROMPT = """\
You write acceptance test scenarios in Gherkin from a software specification.

Rules:
- Use only the actions of the catalog. Each step text must be an instance of an action expression
  (replace each {string} with a quoted value, {int} with a number).
- Write at business level: no URLs, selectors or technical values that are not in the specification.
- Take every value (inputs and expected results) from the specification items. Never invent a value.
  If the specification does not give the value a scenario needs, do not write that scenario.
- Each scenario covers one or more specification item ids, copied exactly from the list.
- Every scenario ends with at least one Then step that asserts an observable result.
- Scenarios are independent of each other.
- If a behaviour cannot be expressed with the catalog, add it to missing_actions with the ids it
  would cover; do not bend a step to fit.
- Open questions are listed only so you avoid them: write nothing that depends on one.
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenarios", "missing_actions"],
    "properties": {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "covers", "priority", "steps"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "covers": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "priority": {"enum": ["high", "medium", "low"]},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["keyword", "text"],
                            "properties": {
                                "keyword": {"enum": list(KEYWORDS)},
                                "text": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
        "missing_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "needed_by"],
                "properties": {
                    "description": {"type": "string", "minLength": 1},
                    "needed_by": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class Step:
    keyword: str
    text: str


@dataclass(frozen=True)
class Draft:
    case_id: str
    title: str
    covers: tuple[str, ...]
    priority: str
    steps: tuple[Step, ...]


def fingerprint(title: str, steps: list[tuple[str, str]]) -> str:
    """What identifies the text of a scenario: its title and its steps, whatever the formatting."""
    body = "\n".join(f"{k} {t}" for k, t in steps)
    text = re.sub(r"\s+", " ", title.strip()) + "\n" + body
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Result:
    drafts: list[Draft] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def build_prompt(feature: Feature, actions: list[Action]) -> str:
    lines = [f"Feature: {feature.title}", "", "Specification items:"]
    for item in feature.items:
        if item.kind not in TESTABLE:
            continue
        lines.append(f"- {item.id} [{item.kind}]: {item.text}")
        if item.scenario:
            for part in ("given", "when", "then"):
                for entry in item.scenario.get(part, []):
                    lines.append(f"    {part}: {entry}")
    open_gaps = [g for g in feature.gaps if g.open]
    if open_gaps:
        lines += ["", "Open questions (avoid):"] + [f"- {g.text}" for g in open_gaps]
    lines += ["", "Action catalog:"]
    for action in actions:
        lines.append(f"- {action.keyword}: {action.expression}  ({action.description})")
    return "\n".join(lines)


def _key(title: str, covers: tuple[str, ...]) -> str:
    text = re.sub(r"\s+", " ", title.strip().lower()) + "|" + ",".join(sorted(covers))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Cases:
    """Case IDs and what each case was written from; one file per application.

    An ID (`TC-0001`) is given once and never reused. A case is "to review" when the text of an item
    it covers changed since the case was written."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"next": 1, "cases": {}}
        if path.is_file():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def id_for(
        self, title: str, covers: tuple[str, ...], hashes: dict[str, str], model: str = ""
    ) -> str:
        key = _key(title, covers)
        entry = self.data["cases"].get(key)
        if entry is None:
            entry = {
                "id": f"TC-{self.data['next']:04d}",
                "title": title,
                "status": "draft",
                "model": model,
                "prompt_version": PROMPT_VERSION,
            }
            self.data["next"] += 1
            self.data["cases"][key] = entry
        covered = {i: hashes[i] for i in covers}
        if entry.get("status") == "approved" and entry.get("covers") != covered:
            entry["status"] = "to_review"  # the specification changed under an approved case
        entry["covers"] = covered
        return str(entry["id"])

    def is_rejected(self, title: str, covers: tuple[str, ...]) -> bool:
        entry = self.data["cases"].get(_key(title, covers))
        return entry is not None and entry.get("status") == "rejected"

    def note_text(self, case_id: str, digest: str) -> None:
        """Record the text of a draft; an approved case whose text changed goes back to review."""
        entry = self.entry(case_id)
        if entry is None:
            return
        if entry.get("status") == "approved" and entry.get("text_sha256") != digest:
            entry["status"] = "to_review"
        if entry.get("status") != "approved":
            entry["text_sha256"] = digest

    def entry(self, case_id: str) -> dict[str, Any] | None:
        for entry in self.data["cases"].values():
            if entry["id"] == case_id:
                found: dict[str, Any] = entry
                return found
        return None

    def entries(self) -> list[dict[str, Any]]:
        return sorted(self.data["cases"].values(), key=lambda e: str(e["id"]))

    def approved_ids(self) -> frozenset[str]:
        return frozenset(e["id"] for e in self.entries() if e.get("status") == "approved")

    def to_review(self, hashes: dict[str, str]) -> list[str]:
        """Case IDs whose source items changed or were retired since they were written."""
        return sorted(
            e["id"]
            for e in self.data["cases"].values()
            if any(hashes.get(i) != h for i, h in e.get("covers", {}).items())
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def draft_feature(
    feature: Feature, actions: list[Action], llm: JsonLLM, cases: Cases, hashes: dict[str, str]
) -> Result:
    """Ask the model for scenarios of one feature and keep the ones that pass the checks."""
    result = Result()
    items: dict[str, Item] = {i.id: i for i in feature.items if i.kind in TESTABLE}
    if not items:
        return result
    reply, result.usage = llm.complete_json(SYSTEM_PROMPT, build_prompt(feature, actions), SCHEMA)
    for missing in reply["missing_actions"]:
        result.missing.append(f"{missing['description']} (for {', '.join(missing['needed_by'])})")
    for raw in reply["scenarios"]:
        title = raw["title"]
        covers = tuple(dict.fromkeys(raw["covers"]))
        unknown = [c for c in covers if c not in items]
        if unknown:
            result.rejected.append(
                f"{title}: covers {', '.join(unknown)}, not in the specification"
            )
            continue
        steps = tuple(Step(s["keyword"], s["text"].strip()) for s in raw["steps"])
        no_action = [s.text for s in steps if not matches(actions, s.text)]
        if no_action:
            result.missing += [f"{t} (for {', '.join(covers)})" for t in no_action]
            result.rejected.append(f"{title}: a step matches no action")
            continue
        if not _asserts(steps):
            result.rejected.append(f"{title}: no Then step")
            continue
        if cases.is_rejected(title, covers):
            result.skipped.append(f"{title}: rejected in a review before")
            continue
        case_id = cases.id_for(title, covers, hashes, llm.model)
        cases.note_text(case_id, fingerprint(title, [(st.keyword, st.text) for st in steps]))
        result.drafts.append(Draft(case_id, title, covers, raw["priority"], steps))
    return result


def _asserts(steps: tuple[Step, ...]) -> bool:
    """A `Then`, or an `And`/`But` that follows one."""
    primary = ""
    for step in steps:
        if step.keyword not in ("And", "But"):
            primary = step.keyword
        if primary == "Then":
            return True
    return False


def render(feature: Feature, drafts: list[Draft], approved: frozenset[str] = frozenset()) -> str:
    out = ["# language: en", "# status: draft, written by stc-tests " + PROMPT_VERSION, ""]
    out += [f"Feature: {feature.title}", ""]
    for draft in drafts:
        tags = [f"@{c}" for c in draft.covers] + [
            f"@{draft.case_id}",
            f"@priority:{draft.priority}",
        ]
        if draft.case_id in approved:
            tags.append(APPROVED_TAG)
        out += [f"  {' '.join(tags)}", f"  Scenario: {draft.title}"]
        out += [f"    {s.keyword} {s.text}" for s in draft.steps]
        out.append("")
    return "\n".join(out)
