"""The structured sidecar of a feature specification (SPC-10..SPC-12).

`features/<slug>.spec.json` holds, next to the markdown page, the statements the page is made of
with a stable ID, a hash of the text, the verified citations and a status against the previous
generation. Its format is the contract in `contracts/spec-sidecar.schema.json`. Nothing here
calls a model or touches a file: the store gives the results of the run and the previous sidecar
and writes what comes back.

IDs (SPC-12): a statement keeps its ID when its text is the same as before or, when the text
changed, when it cites the same code; anything else gets the next number of its kind, and a number
is never used twice, even after its statement is gone (the highest numbers are kept in
`counters`). A part that failed or was not written keeps what the previous sidecar had for it."""

import difflib
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stc.catalog.model import SectionResult
from stc.docs.model import Page
from stc.docs.render import NOTES, claim_text

SIDECAR_SCHEMA = "stc.spec-sidecar/1"
INDEX_SCHEMA = "stc.spec-index/1"
INDEX_FILE = "_specs.json"
SIDECAR_SUFFIX = ".spec.json"

# Part of the page -> (kind of statement, letters of its IDs). Intent and design are not listed:
# they are narrative, not statements another application would test or build from.
KINDS: dict[str, tuple[str, str]] = {
    "spec-requirements": ("requirement", "FR"),
    "spec-rules": ("rule", "BR"),
    "spec-data": ("data", "DM"),
    "spec-interfaces": ("interface", "IF"),
    "spec-screens": ("screen", "SC"),
    "spec-api": ("api", "AP"),
    "spec-acceptance": ("acceptance", "AC"),
    "spec-tasks": ("task", "TK"),
    "spec-risks": ("risk", "RK"),
}
GAP_LETTERS = "GAP"
_TITLE_PREFIX = "Specification: "


def is_spec_page(page: Page) -> bool:
    """A feature page of a specification (documentation pages have no `spec-*` parts)."""
    return page.path.startswith("features/") and any(p.id in KINDS for p in page.parts)


def sidecar_path(page_path: str) -> str:
    """`features/job-x.md` -> `features/job-x.spec.json`."""
    return page_path.removesuffix(".md") + SIDECAR_SUFFIX


def normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def text_sha256(text: str) -> str:
    """Hash of a text with whitespace and letter case normalized."""
    return hashlib.sha256(normalize(text).encode()).hexdigest()


# --- matching ----------------------------------------------------------------------------------


@dataclass
class _Draft:
    text: str
    sha: str
    confidence: str
    citations: list[dict[str, int | str]]
    scenario: dict[str, list[str]] | None = None

    @property
    def cites(self) -> frozenset[tuple[str, int, int]]:
        return frozenset((str(c["file"]), int(c["start"]), int(c["end"])) for c in self.citations)


def _cites(item: Mapping[str, Any]) -> frozenset[tuple[str, int, int]]:
    return frozenset((c["file"], c["start"], c["end"]) for c in item.get("citations", []))


# A rewritten statement keeps its ID when it cites mostly the same code and still says something
# similar: the model rarely repeats a line range to the line, so identical citations are too strict.
MIN_OVERLAP = 0.5  # share of the cited lines the two statements have in common
MIN_SIMILARITY = 0.3  # similarity of the two texts, 0 to 1


def _covered(cites: frozenset[tuple[str, int, int]]) -> set[tuple[str, int]]:
    return {(file, n) for file, start, end in cites for n in range(start, end + 1)}


def _overlap(a: set[tuple[str, int]], b: set[tuple[str, int]]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _match(old: Sequence[Mapping[str, Any]], new: Sequence[_Draft]) -> dict[int, tuple[int, str]]:
    """New statement index -> (old statement index, "unchanged" | "changed"). Three passes: the
    same text; the same citations (the most similar text among them); mostly the same cited code
    and a similar text. Each old statement is used once."""
    matched: dict[int, tuple[int, str]] = {}
    used: set[int] = set()
    for j, draft in enumerate(new):
        for i, item in enumerate(old):
            if i not in used and item["text_sha256"] == draft.sha:
                used.add(i)
                matched[j] = (i, "unchanged")
                break
    similarity: dict[tuple[int, int], float] = {}

    def text_score(j: int, i: int) -> float:
        if (j, i) not in similarity:
            similarity[(j, i)] = difflib.SequenceMatcher(
                None, normalize(old[i]["text"]), normalize(new[j].text)
            ).ratio()
        return similarity[(j, i)]

    for j, draft in enumerate(new):  # the same citations
        if j in matched or not draft.cites:
            continue
        best: tuple[float, int] | None = None
        for i, item in enumerate(old):
            if i in used or _cites(item) != draft.cites:
                continue
            score = text_score(j, i)
            if best is None or score > best[0]:
                best = (score, i)
        if best is not None:
            used.add(best[1])
            matched[j] = (best[1], "changed")
    lines_old = {i: _covered(_cites(item)) for i, item in enumerate(old) if i not in used}
    for j, draft in enumerate(new):  # mostly the same code, a similar text
        if j in matched or not draft.cites:
            continue
        lines = _covered(draft.cites)
        best = None
        for i, covered in lines_old.items():
            if i in used or _overlap(lines, covered) < MIN_OVERLAP:
                continue
            score = text_score(j, i)
            if score >= MIN_SIMILARITY and (best is None or score > best[0]):
                best = (score, i)
        if best is not None:
            used.add(best[1])
            matched[j] = (best[1], "changed")
    return matched


# --- the sidecar -------------------------------------------------------------------------------


def _number(item_id: str) -> int:
    return int(item_id.rsplit("-", 1)[1])


def _letters(item_id: str) -> str:
    return item_id.rsplit("/", 1)[1].rsplit("-", 1)[0]


@dataclass
class _Allocator:
    slug: str
    counters: dict[str, int] = field(default_factory=dict)

    def next(self, letters: str) -> str:
        self.counters[letters] = self.counters.get(letters, 0) + 1
        return f"{self.slug}/{letters}-{self.counters[letters]:02d}"


def _counters(previous: Mapping[str, Any]) -> dict[str, int]:
    """High-water marks of the previous sidecar: the stored ones, and every ID it still shows."""
    counters = {k: int(v) for k, v in previous.get("counters", {}).items()}
    seen = (
        [i["id"] for i in previous.get("items", [])]
        + [r["id"] for r in previous.get("retired", [])]
        + [g["id"] for g in previous.get("gaps", [])]
    )
    for item_id in seen:
        letters = _letters(item_id)
        counters[letters] = max(counters.get(letters, 0), _number(item_id))
    return counters


def build_sidecar(
    *,
    application: str,
    page: Page,
    results: Mapping[str, SectionResult],
    previous: Mapping[str, Any] | None,
    manual: Mapping[str, str],
    code_version: str | None,
    generated_at: str,
    stack_sha256: str,
    inputs_sha256: str,
) -> dict[str, Any] | None:
    """The sidecar of `page` after a run, or None when there is nothing to say (no part was written
    and there is no earlier sidecar). `results` is keyed by `page.key(part.id)`."""
    if previous is None and not any(page.key(p.id) in results for p in page.parts):
        return None
    before: Mapping[str, Any] = previous or {}
    slug = Path(page.path).stem
    allocator = _Allocator(slug, _counters(before))
    old_items: dict[str, list[dict[str, Any]]] = {}
    for item in before.get("items", []):
        old_items.setdefault(item["part"], []).append(item)
    old_gaps: dict[str, list[dict[str, Any]]] = {}
    for gap in before.get("gaps", []):
        old_gaps.setdefault(gap["part"], []).append(gap)
    retired: dict[str, dict[str, Any]] = {r["id"]: r for r in before.get("retired", [])}

    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    on_page = {p.id for p in page.parts}
    for part in page.parts:
        result = results.get(page.key(part.id))
        written = result is not None and not result.failed
        if not written:  # keep what we had: a retry then continues the same IDs
            items += [{**i, "status": "unchanged"} for i in old_items.get(part.id, [])]
            gaps += old_gaps.get(part.id, [])
            continue
        assert result is not None
        unreadable: list[str] = []
        if part.id in KINDS:
            kind, letters = KINDS[part.id]
            drafts: list[_Draft] = []
            for claim in result.claims:
                text = claim_text(claim, part.style)
                scenario = None
                if kind == "acceptance":
                    if claim.scenario is None:  # not asked for as fields: not a scenario (SPC-13)
                        unreadable.append(f"Not given as a scenario, so not testable: {text}")
                        continue
                    scenario = {
                        "given": claim.scenario.given,
                        "when": claim.scenario.when,
                        "then": claim.scenario.then,
                    }
                drafts.append(
                    _Draft(
                        text, text_sha256(text), claim.confidence,
                        [{"file": e.file, "start": e.start_line, "end": e.end_line}
                         for e in claim.evidence],
                        scenario,
                    )
                )  # fmt: skip
            old = old_items.get(part.id, [])
            matched = _match(old, drafts)
            for j, draft in enumerate(drafts):
                if j in matched:
                    i, status = matched[j]
                    item_id = old[i]["id"]
                else:
                    item_id, status = allocator.next(letters), "new"
                item = {
                    "id": item_id, "kind": kind, "part": part.id, "text": draft.text,
                    "text_sha256": draft.sha, "confidence": draft.confidence,
                    "citations": draft.citations, "status": status, "origin": "generated",
                }  # fmt: skip
                if draft.scenario is not None:
                    item["scenario"] = draft.scenario
                items.append(item)
            gone = set(range(len(old))) - {i for i, _ in matched.values()}
            for i in sorted(gone):
                retired[old[i]["id"]] = {"id": old[i]["id"], "retired_in": code_version}
        # the gaps are matched by their text only: a resolved gap just disappears
        previous_gaps = {text_sha256(g["text"]): g for g in old_gaps.get(part.id, [])}
        for text in [*result.gaps, *unreadable]:
            known = previous_gaps.pop(text_sha256(text), None)
            gaps.append(
                known
                if known is not None
                else {
                    "id": allocator.next(GAP_LETTERS), "part": part.id, "text": text,
                    "triage": {"status": "open", "by": []},
                }
            )  # fmt: skip
    for part_id, leftovers in old_items.items():  # a part the page no longer has
        if part_id not in on_page:
            for item in leftovers:
                retired[item["id"]] = {"id": item["id"], "retired_in": code_version}

    document = {
        "schema": SIDECAR_SCHEMA,
        "application": application,
        "feature": {"slug": slug, "title": page.title.removeprefix(_TITLE_PREFIX)},
        "code_version": code_version,
        "generated_at": generated_at,
        "stack_sha256": stack_sha256,
        "page": {"path": page.path, "inputs_sha256": inputs_sha256},
        "items": items,
        "retired": sorted(retired.values(), key=lambda r: r["id"]),
        "gaps": gaps,
        "manual_blocks": [
            {"key": key, "text_sha256": text_sha256(manual[key])}
            for key in [*(page.key(p.id) for p in page.parts), page.key(NOTES)]
            if manual.get(key, "").strip()
        ],
        "counters": dict(sorted(allocator.counters.items())),
    }
    return document


def hide_answered_gaps(
    page: Page, results: Mapping[str, SectionResult], document: Mapping[str, Any]
) -> None:
    """The page lists only the gaps still open (SPC-14): the ones the cross-check found answered
    elsewhere stay in the sidecar, with the IDs that answer them, and leave the page."""
    answered = {
        (g["part"], text_sha256(g["text"]))
        for g in document["gaps"]
        if g["triage"]["status"] == "answered_elsewhere"
    }
    for part in page.parts:
        result = results.get(page.key(part.id))
        if result is not None:
            result.gaps = [g for g in result.gaps if (part.id, text_sha256(g)) not in answered]


def build_index(
    application: str,
    code_version: str | None,
    generated_at: str,
    pages: Sequence[Page],
    existing: Sequence[str],
) -> dict[str, Any]:
    """`_specs.json`: the features that have a sidecar (`existing` lists their paths), in the
    order of the plan, which is the migration order (SPC-05)."""
    return {
        "schema": INDEX_SCHEMA,
        "application": application,
        "code_version": code_version,
        "generated_at": generated_at,
        "features": [
            {
                "slug": Path(p.path).stem,
                "title": p.title.removeprefix(_TITLE_PREFIX),
                "page": p.path,
                "sidecar": sidecar_path(p.path),
            }
            for p in pages
            if is_spec_page(p) and sidecar_path(p.path) in existing
        ],
    }
