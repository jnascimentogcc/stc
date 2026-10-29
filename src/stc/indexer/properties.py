"""Spring-style properties, to resolve `${property}` in the paths a controller exposes.

A Java controller often reads the API prefix from configuration, for example
`@RequestMapping("/orders${api.v1}")` with `api.v1=/api/v1` in `application.properties`. The
extractor keeps the placeholder in the endpoint key; when relations are derived, the properties
of the same application give it its real value, so the call `/orders/api/v1/list` of a front end
can be matched to the endpoint that serves it. Only the files the index kept are read (a file
held back as a secret never is), and a value is used only if it looks like a piece of a URL path.
"""

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import psycopg
import yaml

from stc.indexer.chunk_store import _read_verified
from stc.indexer.signals import normalize_path

_NAME = re.compile(r"^(application|bootstrap)([-_.][\w.\-]+)?\.(properties|ya?ml)$", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z][\w.\-]*)(?::([^}]*))?\}")
_PATH_VALUE = re.compile(r"^[\w\-./{}]{0,100}$")
MAX_BYTES = 200_000  # a configuration file larger than this is not Spring configuration


def is_properties_file(path: str) -> bool:
    return _NAME.match(PurePosixPath(path).name) is not None


def parse_properties(text: str) -> dict[str, str]:
    """`key=value`, `key: value` and `key value` lines of a .properties file."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        match = re.match(r"([^=:\s]+)\s*[=:\s]\s*(.*)", line)
        if match:
            out.setdefault(match.group(1).lower(), match.group(2).strip())
    return out


def _flatten(value: object, prefix: str, out: dict[str, str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _flatten(child, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        out.setdefault(prefix.lower(), str(value))


def parse_yaml(text: str) -> dict[str, str]:
    """Dotted keys of a YAML file (every document of it)."""
    out: dict[str, str] = {}
    try:
        for document in yaml.safe_load_all(text):
            _flatten(document, "", out)
    except yaml.YAMLError:
        return {}
    return out


def parse_file(path: str, text: str) -> dict[str, str]:
    if path.lower().endswith(".properties"):
        return parse_properties(text)
    return parse_yaml(text)


def load_properties(conn: psycopg.Connection, root: Path) -> dict[int, dict[str, str]]:
    """application id -> its properties. The base file (`application.*`) wins over a profile
    file (`application-dev.*`), which only fills what is missing."""
    rows = conn.execute(
        "SELECT f.application_id, a.path, f.path, f.content_sha256 FROM files f"
        " JOIN applications a ON a.id = f.application_id"
        " WHERE f.status IN ('indexed', 'unsupported') AND f.size_bytes <= %s",
        (MAX_BYTES,),
    ).fetchall()
    chosen = [r for r in rows if is_properties_file(str(r[2]))]
    chosen.sort(key=lambda r: (len(PurePosixPath(str(r[2])).name), str(r[2])))  # base files first
    out: dict[int, dict[str, str]] = {}
    for app_id, app_path, file_path, sha in chosen:
        text = _read_verified(root / str(app_path) / str(file_path), str(sha))
        if text is None:
            continue
        merged = out.setdefault(int(app_id), {})
        for key, value in parse_file(str(file_path), text).items():
            merged.setdefault(key, value)
    return out


def resolve_placeholders(key: str, properties: Mapping[str, str]) -> str:
    """`/orders${api.v1}/list` -> `/orders/api/v1/list` where `api.v1` is known; what is not
    known becomes `{}`, as any other path parameter. Property names are lower case in keys."""

    def value(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        found = properties.get(name, match.group(2))
        if found is not None and _PATH_VALUE.match(found.strip()):
            return found.strip()
        return "{}"

    return normalize_path(_PLACEHOLDER.sub(value, key))


def has_placeholder(key: str) -> bool:
    return "${" in key
