"""Evaluation reports: build, save, compare with a previous run, and print.

Each run is a JSON file in `STC_DATA/evaluations/`, so a change to chunking, embeddings, prompts
or models can be judged against the run before it (XCT-08)."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stc.evaluation.metrics import RetrievalScore, score_answer
from stc.evaluation.model import Question
from stc.evaluation.runner import AnswerOutcome, RetrievalOutcome

DIRECTORY = "evaluations"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _retrieval_record(score: RetrievalScore, k: int) -> dict[str, Any]:
    return {
        "ranks": score.ranks,
        "first_rank": score.first_rank,
        "recall_at_k": score.recall_at(k),
    }


def question_record(
    question: Question,
    retrieval: RetrievalOutcome | None,
    answer: AnswerOutcome | None,
    k: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": question.id,
        "kind": question.kind,
        "retrieval": None,
        "answer": None,
    }
    if retrieval:
        record["retrieval"] = {
            "hybrid": _retrieval_record(retrieval.hybrid, k) if retrieval.hybrid else None,
            "lexical": _retrieval_record(retrieval.lexical, k),
            "top": retrieval.top,
            "error": retrieval.error,
        }
    if answer:
        score = score_answer(question, answer.answer, answer.citations)
        passed = answer.error is None and score.passed
        record["answer"] = {
            "passed": passed,
            "checks": score.checks,
            "evidence_found": score.evidence_found,
            "evidence_expected": score.evidence_expected,
            "citations_verified": score.citations_verified,
            "citations_total": score.citations_total,
            "steps": answer.steps,
            "input_tokens": answer.input_tokens,
            "output_tokens": answer.output_tokens,
            "warnings": answer.warnings,
            "error": answer.error,
            "text": answer.answer,
        }
    return record


def summarize(records: list[dict[str, Any]], k: int) -> dict[str, float | int | None]:
    """The numbers a change is judged by. None when there was nothing to measure."""
    summary: dict[str, float | int | None] = {"questions": len(records)}
    for mode in ("hybrid", "lexical"):
        scored = [
            r["retrieval"][mode] for r in records if r["retrieval"] and r["retrieval"].get(mode)
        ]
        summary[f"{mode}_recall_at_1"] = _mean(
            [sum(1 for x in s["ranks"] if x == 1) / len(s["ranks"]) for s in scored if s["ranks"]]
        )
        summary[f"{mode}_recall_at_5"] = _mean(
            [
                sum(1 for x in s["ranks"] if x is not None and x <= 5) / len(s["ranks"])
                for s in scored
                if s["ranks"]
            ]
        )
        summary[f"{mode}_recall_at_{k}"] = _mean([s["recall_at_k"] for s in scored])
        summary[f"{mode}_mrr"] = _mean(
            [1 / s["first_rank"] if s["first_rank"] else 0.0 for s in scored]
        )

    answered = [r for r in records if r["answer"]]
    answerable = [r for r in answered if r["kind"] == "answerable"]
    unanswerable = [r for r in answered if r["kind"] == "unanswerable"]
    summary["answers"] = len(answered)
    summary["pass_rate"] = _mean([1.0 if r["answer"]["passed"] else 0.0 for r in answered])
    expected = sum(r["answer"]["evidence_expected"] for r in answerable)
    summary["evidence_recall"] = (
        sum(r["answer"]["evidence_found"] for r in answerable) / expected if expected else None
    )
    total = sum(r["answer"]["citations_total"] for r in answered)
    summary["citation_precision"] = (
        sum(r["answer"]["citations_verified"] for r in answered) / total if total else None
    )
    summary["no_evidence_rate"] = _mean(
        [1.0 if r["answer"]["citations_verified"] == 0 else 0.0 for r in answerable]
    )
    summary["unanswerable_admitted"] = _mean(
        [1.0 if r["answer"]["checks"].get("admits") else 0.0 for r in unanswerable]
    )
    summary["mean_steps"] = _mean([float(r["answer"]["steps"]) for r in answered])
    summary["input_tokens"] = (
        sum(r["answer"]["input_tokens"] for r in answered) if answered else None
    )
    summary["output_tokens"] = (
        sum(r["answer"]["output_tokens"] for r in answered) if answered else None
    )
    summary["errors"] = sum(1 for r in answered if r["answer"]["error"])
    return summary


def build_report(
    records: list[dict[str, Any]],
    *,
    k: int,
    mode: str,
    settings: dict[str, Any],
    versions: dict[str, str | None],
    questions_file: Path,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "k": k,
        "settings": settings,
        "applications": versions,
        "questions_sha256": hashlib.sha256(questions_file.read_bytes()).hexdigest(),
        "summary": summarize(records, k),
        "questions": records,
    }


def save_report(data_dir: Path, report: dict[str, Any]) -> Path:
    folder = data_dir / DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report["created_at"][:19].replace("-", "").replace(":", "") + "Z"
    counter = 1  # two runs in the same second get consecutive numbers; names sort by time
    path = folder / f"{stamp}-{counter:02d}.json"
    while path.exists():
        counter += 1
        path = folder / f"{stamp}-{counter:02d}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def latest_report(data_dir: Path, *, before: Path | None = None) -> Path | None:
    folder = data_dir / DIRECTORY
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.glob("*.json") if before is None or p.name < before.name)
    return files[-1] if files else None


def load_report(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read the report {path}: {exc}") from exc
    if not isinstance(data, dict) or "summary" not in data or "questions" not in data:
        raise ValueError(f"{path} is not an evaluation report")
    return data


# Metrics where a bigger number is better; the others are costs or defects.
_HIGHER_IS_BETTER = {"no_evidence_rate": False, "errors": False, "mean_steps": False}


@dataclass
class Comparison:
    deltas: dict[str, tuple[float, float]] = field(default_factory=dict)  # name -> (old, new)
    regressions: list[str] = field(default_factory=list)  # question ids that got worse
    improvements: list[str] = field(default_factory=list)
    new_questions: list[str] = field(default_factory=list)
    comparable: bool = True  # same questions file


def _status(record: dict[str, Any]) -> str | None:
    if record["answer"]:
        return "pass" if record["answer"]["passed"] else "fail"
    retrieval = record["retrieval"]
    if retrieval:
        best = retrieval["hybrid"] or retrieval["lexical"]
        return "found" if best["first_rank"] else "missed"
    return None


def compare(old: dict[str, Any], new: dict[str, Any]) -> Comparison:
    comparison = Comparison(comparable=old.get("questions_sha256") == new.get("questions_sha256"))
    for name, value in new["summary"].items():
        before = old["summary"].get(name)
        if isinstance(value, (int, float)) and isinstance(before, (int, float)) and before != value:
            comparison.deltas[name] = (before, value)
    previous = {r["id"]: _status(r) for r in old["questions"]}
    good = {"pass", "found"}
    for record in new["questions"]:
        if record["id"] not in previous:
            comparison.new_questions.append(record["id"])
            continue
        was, now = previous[record["id"]], _status(record)
        if was in good and now not in good:
            comparison.regressions.append(record["id"])
        elif was not in good and now in good:
            comparison.improvements.append(record["id"])
    return comparison


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.2f}"


def render(report: dict[str, Any], comparison: Comparison | None = None) -> list[str]:
    lines = [
        f"Evaluation {report['created_at']} (mode {report['mode']}, k={report['k']}, "
        f"{report['summary']['questions']} question(s))"
    ]
    s = report["summary"]
    rows: list[tuple[str, str]] = []
    k = report["k"]
    for mode in ("hybrid", "lexical"):
        if s.get(f"{mode}_mrr") is None:
            continue
        recall = " / ".join(_fmt(s[f"{mode}_recall_at_{n}"]) for n in (1, 5, k))
        rows.append((f"search {mode}: recall@1 / @5 / @{k}", recall))
        rows.append((f"search {mode}: MRR", _fmt(s[f"{mode}_mrr"])))
    if s.get("answers"):
        rows += [
            ("answers passed", _fmt(s["pass_rate"])),
            ("evidence recall (expected code cited, verified)", _fmt(s["evidence_recall"])),
            ("citation precision (verified / all)", _fmt(s["citation_precision"])),
            ("answerable, answered with no verified evidence", _fmt(s["no_evidence_rate"])),
            ("unanswerable that admitted it", _fmt(s["unanswerable_admitted"])),
            ("tool steps per answer", _fmt(s["mean_steps"])),
            ("tokens in / out", f"{_fmt(s['input_tokens'])} / {_fmt(s['output_tokens'])}"),
            ("errors", _fmt(s["errors"])),
        ]
    width = max((len(label) for label, _ in rows), default=0)
    lines += [f"  {label.ljust(width)}  {value}" for label, value in rows]

    failing = [r for r in report["questions"] if _status(r) in ("fail", "missed")]
    if failing:
        lines.append("Not satisfied:")
        for record in failing:
            detail = ""
            if record["answer"]:
                bad = [name for name, ok in record["answer"]["checks"].items() if not ok]
                detail = ", ".join(bad) or (record["answer"]["error"] or "")
            elif record["retrieval"]:
                detail = "expected code not in the top results; got " + ", ".join(
                    record["retrieval"]["top"][:3]
                )
            lines.append(f"  {record['id']}: {detail}")

    if comparison:
        lines.append("Compared with the previous run:")
        if not comparison.comparable:
            lines.append("  (the question set changed, so the numbers are not strictly comparable)")
        for name, (old, new) in comparison.deltas.items():
            better = (new > old) == _HIGHER_IS_BETTER.get(name, True)
            mark = "better" if better else "worse"
            if name in ("questions", "answers", "input_tokens", "output_tokens"):
                mark = ""
            lines.append(f"  {name}: {_fmt(old)} -> {_fmt(new)} {mark}".rstrip())
        if not comparison.deltas:
            lines.append("  no metric changed")
        if comparison.regressions:
            lines.append("  now failing: " + ", ".join(comparison.regressions))
        if comparison.improvements:
            lines.append("  now passing: " + ", ".join(comparison.improvements))
        if comparison.new_questions:
            lines.append("  new questions: " + ", ".join(comparison.new_questions))
    return lines
