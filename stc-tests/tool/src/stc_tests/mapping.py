"""The interface mapping: what corresponds to what between the legacy and the migrated interface.

The AI drafts it from the specification; the migration team approves it. The draft never decides
what it cannot know: the field names and result paths of the target come from the target's contract,
which the client provides, so a draft always carries that as an open question until a person
settles it. An approved mapping has no open questions (the schema enforces it)."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from stc_tests.contract import MAPPING, ContractError, validate
from stc_tests.llm import JsonLLM, Usage
from stc_tests.model import Feature
from stc_tests.scenarios import TESTABLE

PROMPT_VERSION = "mapping-1"
STANDING_QUESTION = (
    "Confirm the target field names, the request path and the result paths against the "
    "target's contract (for example its OpenAPI)."
)

SYSTEM_PROMPT = """\
You draft the interface mapping of one program of a system being migrated: how the legacy program
is fed and observed, and which call on the migrated system corresponds to it.

Rules:
- Use only what the specification says. Never invent a prompt, a field or a result.
- `inputs` are the values the legacy program is fed, in order. `name` is a short camelCase logical
  name; `prompt` is the text of the prompt as the specification gives it, if it does.
- `fields` maps each input name to a property of the JSON request body; `results` maps a logical
  result name to a JSON path in the response. Propose names; the real ones come from the target's
  contract, which you do not have.
- The legacy `kind` is console, http, batch or args. A COBOL program is never executed.
- `compare_mode` is `structured` when the specification describes results as fields, and
  `transcript` when it describes the full console output.
- Put everything the specification does not settle in `open_questions`. Decide nothing silently.
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "legacy_kind",
        "inputs",
        "method",
        "path",
        "fields",
        "results",
        "compare_mode",
        "open_questions",
    ],
    "properties": {
        "legacy_kind": {"enum": ["console", "http", "batch", "args"]},
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "prompt"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]*$"},
                    "prompt": {"type": "string"},
                },
            },
        },
        "method": {"enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {"type": "string", "pattern": "^/"},
        "fields": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1}},
        "results": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1}},
        "compare_mode": {"enum": ["structured", "transcript"]},
        "open_questions": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
}


class _Loader(yaml.SafeLoader):
    """YAML where a timestamp stays text, as the contract says (`approved_at` is a string)."""


_Loader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_mapping(path: Path) -> dict[str, Any]:
    """Read and validate a mapping file; `ContractError` if it does not follow the contract."""
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506
    except (OSError, yaml.YAMLError) as error:
        raise ContractError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{path}: not a YAML mapping")
    try:
        validate(document, MAPPING)
        _cross_checks(document)
    except ContractError as error:
        raise type(error)(f"{path}: {error}") from error
    return document


def _cross_checks(document: dict[str, Any]) -> None:
    """What a schema cannot say: the target fields refer to inputs the legacy has."""
    names = [i["name"] for i in document["legacy"]["inputs"]]
    if len(names) != len(set(names)):
        raise ContractError("legacy inputs have a repeated name")
    unknown = sorted(set(document["target"]["fields"]) - set(names))
    if unknown:
        raise ContractError(f"target fields without a legacy input: {', '.join(unknown)}")
    request = document["legacy"].get("request")
    if request:
        unknown = sorted(set(request["fields"]) - set(names))
        if unknown:
            raise ContractError(
                f"legacy request fields without a legacy input: {', '.join(unknown)}"
            )
    ids = [d["id"] for d in document["accepted_differences"]]
    if len(ids) != len(set(ids)):
        raise ContractError("accepted differences have a repeated id")


def build_prompt(feature: Feature) -> str:
    lines = [f"Program: {feature.slug} ({feature.title})", "", "Specification items:"]
    lines += [f"- {i.id} [{i.kind}]: {i.text}" for i in feature.items if i.kind in TESTABLE]
    return "\n".join(lines)


def draft_mapping(application: str, feature: Feature, llm: JsonLLM) -> tuple[dict[str, Any], Usage]:
    """The model proposes the pieces; the code builds a file that follows the contract."""
    reply, usage = llm.complete_json(SYSTEM_PROMPT, build_prompt(feature), SCHEMA)
    inputs = [
        {"name": i["name"], **({"prompt": i["prompt"]} if i["prompt"] else {})}
        for i in reply["inputs"]
    ]
    names = {i["name"] for i in inputs}
    fields = {k: v for k, v in reply["fields"].items() if k in names}
    questions = list(reply["open_questions"])
    dropped = sorted(set(reply["fields"]) - names)
    if dropped:
        questions.append(f"The draft mapped fields with no legacy input ({', '.join(dropped)}).")
    if STANDING_QUESTION not in questions:
        questions.append(STANDING_QUESTION)
    document: dict[str, Any] = {
        "schema": "stc.interface-mapping/1",
        "application": application,
        "program": feature.slug,
        "status": "draft",
        "proposed_by": {"model": llm.model, "prompt_version": PROMPT_VERSION},
        "spec_code_version": feature.code_version,
        "legacy": {"kind": reply["legacy_kind"], "executable": False, "inputs": inputs},
        "target": {
            "kind": "http",
            "request": {"method": reply["method"], "path": reply["path"]},
            "fields": fields,
            "results": reply["results"],
        },
        "compare": {"mode": reply["compare_mode"]},
        "accepted_differences": [],  # only people decide a difference is acceptable
        "open_questions": questions,
    }
    validate(document, MAPPING)
    return document, usage


def dump(document: dict[str, Any]) -> str:
    header = "# Interface mapping, drafted by stc-tests. A person must settle the open questions\n"
    header += "# and approve it (stc-tests approve-mapping) before contract-level tests run.\n"
    return header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


def approve_text(text: str, by: str, now: datetime | None = None) -> str:
    """The file text with `status: approved`, who and when; comments and layout stay as they are."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^status:\s*\S", line):
            lines[i : i + 1] = [
                "status: approved",
                f"approved_by: {json.dumps(by, ensure_ascii=False)}",
                f'approved_at: "{stamp}"',
            ]
            return "\n".join(lines) + "\n"
    raise ContractError("no top-level `status:` line to change")
