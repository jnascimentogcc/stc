"""What the tool knows about each requirement between runs, and what changed since.

One file per application, `<output>/<application>/.stc-tests/requirements.json`. A requirement
that is no longer in the specification is marked retired and never comes back under the same id."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from stc_tests.model import Application

STATE_FILE = ".stc-tests/requirements.json"

State = dict[str, dict[str, str]]


@dataclass
class Drift:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unchanged: int = 0


def state_path(output: Path, folder: str) -> Path:
    return output / folder / STATE_FILE


def load(path: Path) -> State:
    if not path.is_file():
        return {}
    data: State = json.loads(path.read_text(encoding="utf-8"))
    return data


def compare(application: Application, known: State) -> Drift:
    """Requirements new, changed (by text hash) or retired against what was known."""
    drift = Drift()
    current = {item.id: item.text_sha256 for item in application.items()}
    for item_id, digest in sorted(current.items()):
        before = known.get(item_id)
        if before is None or before["status"] == "retired":
            drift.new.append(item_id)
        elif before["text_sha256"] != digest:
            drift.changed.append(item_id)
        else:
            drift.unchanged += 1
    gone = set(application.retired()) | {
        i for i, v in known.items() if v["status"] == "active" and i not in current
    }
    drift.retired = sorted(i for i in gone if known.get(i, {}).get("status") != "retired")
    return drift


def updated(application: Application, known: State) -> State:
    """The state after taking `application` in."""
    drift = compare(application, known)
    result = {k: dict(v) for k, v in known.items()}
    for item in application.items():
        result[item.id] = {"text_sha256": item.text_sha256, "status": "active"}
    for item_id in drift.retired:
        entry = result.setdefault(item_id, {"text_sha256": ""})
        entry["status"] = "retired"
    return result


def save(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
