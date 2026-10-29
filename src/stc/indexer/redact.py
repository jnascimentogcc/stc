"""Redaction of secret values in configuration files (IDX-06, XCT-01).

Whole-file exclusion (`secrets.py`) catches files that are secrets: `.env`, keys, tokens with a
known shape. It cannot catch a configuration file that is mostly ordinary and holds one literal
password, such as a Spring `application.properties` with `spring.datasource.password=...`: the
generic detectors look for quoted values, and this one is not. Such a file is still worth indexing
(ports, prefixes, flags), so what is redacted is the *value*, wherever the file is read for any
purpose (chunks, signals, properties, the reader of cited lines). The keys, the placeholders
(`${DB_PASSWORD}`) and every other line stay, and no line is added or removed, so line numbers
(and with them every piece of evidence) are unchanged.

What is redacted: the value of a key that names a credential (password, secret, token, api key,
private key, ...), the user of a datasource, mail, LDAP or similar connection, the credentials
inside a URL (`scheme://user:password@host`) and the credential parameters of a JDBC-style URL.
Not redacted: empty values, `${placeholders}`, booleans, and numbers except as the value of a
credential key (`password=0123456789` is one; `token-validity-seconds=3600` is not).
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Bump when the rules change: files are then read again and what was derived from them replaced.
REDACTION_VERSION = 2
REDACTED = "<redacted>"

_CONFIG_SUFFIXES = (
    ".properties", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".config", ".toml", ".env", ".xml",
    ".tfvars", ".hcl",
)  # fmt: skip
_JSON_CONFIG = re.compile(r"(?:appsettings|config|settings|secrets?|credentials?)[\w.\-]*\.json$")
# Manifests list dependencies (`"jsonwebtoken": "^9.0.0"`), not credentials.
_NEVER = frozenset({"pom.xml", "package.json", "composer.json", "pyproject.toml", "cargo.toml"})

_CREDENTIAL_KEY = re.compile(
    r"pass(?:word|wd|phrase|code)?|pwd|secret|token|api[_.\-]?key|apikey|access[_.\-]?key"
    r"|private[_.\-]?key|credential|signing[_.\-]?key|encryption[_.\-]?key|auth[_.\-]?key"
    r"|client[_.\-]?secret|connection[_.\-]?string",
    re.IGNORECASE,
)
_USER_KEY = re.compile(r"(?:^|[._\-])(?:user(?:name)?|login|uid|account)$", re.IGNORECASE)
_CONNECTION_CONTEXT = re.compile(
    r"datasource|jdbc|database|\bdb\b|mail|smtp|ldap|redis|mongo|rabbit|ftp|ssh|proxy|broker|jms",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^(?:\$\{.*\}|<.*>|%\(.*\)s|\{\{.*\}\}|\$[A-Za-z_]\w*)$")
_BOOLEAN = re.compile(r"^(?:true|false|null|none|yes|no|on|off)$", re.IGNORECASE)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
# A credential key whose value is a number is still a credential (`password=0123456789`), unless
# the key says it is a setting about credentials (`token-validity-seconds=3600`).
_NUMERIC_WORDS = frozenset(
    {
        "second", "seconds", "sec", "secs", "millis", "ms", "minute", "minutes", "min", "hour",
        "hours", "day", "days", "length", "size", "count", "timeout", "expiration", "expiry",
        "expires", "expire", "validity", "ttl", "port", "max", "attempts", "attempt",
        "iterations", "iteration", "rounds", "round", "strength", "duration", "enabled", "version",
    }
)  # fmt: skip
_KEY_WORDS = re.compile(r"[._\-\s]+|(?<=[a-z])(?=[A-Z])")
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][\w+.\-]*://)[^\s/@:'\"]+:[^\s/@'\"]+@")
_URL_PARAM = re.compile(
    r"(?P<sep>[?&;])(?P<name>password|passwd|pwd|user|username|uid|apikey|api_key|token|secret"
    r"|accesskey|access_key|sslkey|sslpassword)=(?P<value>[^&;\s'\"]+)",
    re.IGNORECASE,
)
_XML_TAG = re.compile(
    r"(<(?P<tag>[\w.\-]*(?:pass(?:word|wd)?|pwd|secret|token|apikey|api-key|credential)[\w.\-]*)>)"
    r"(?P<value>[^<]+)(</(?P=tag)>)",
    re.IGNORECASE,
)
_XML_ATTR = re.compile(
    r"""(?P<head><(?:property|entry|param|add|setting)\b[^>]*?\bname\s*=\s*["'][^"']*"""
    r"""(?:pass(?:word|wd)?|pwd|secret|token|apikey|api[_.\-]key|credential)[^"']*["']"""
    r"""[^>]*?\bvalue\s*=\s*)(?P<quote>["'])(?P<value>[^"']*)(?P=quote)""",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"[=:]")
_YAML_PARENT = re.compile(r"""^\s*["']?([\w.\-]+)["']?\s*:\s*(?:#.*)?$""")


@dataclass(frozen=True)
class Redacted:
    text: str
    values: int  # how many values were replaced


def is_config_file(path: PurePosixPath | str) -> bool:
    name = PurePosixPath(str(path)).name.lower()
    if name in _NEVER:
        return False
    return (
        name.endswith(_CONFIG_SUFFIXES) or name.startswith(".env") or bool(_JSON_CONFIG.match(name))
    )


def _is_literal(value: str, *, numbers: bool = False) -> bool:
    """Whether `value` is a literal that could be a credential. Booleans never are; numbers are
    only where the caller says a number can be one."""
    bare = value.strip().strip("'\"").rstrip(",;").strip()
    if not bare or _PLACEHOLDER.match(bare) or _BOOLEAN.match(bare):
        return False
    if _NUMBER.match(bare) and not numbers:
        return False
    # The structure of the file, not a value: `"credentials": {`, a list, a YAML block scalar.
    return not bare.startswith(("{", "[", "|", ">")) or bare.startswith("${")


def _redact_value(raw: str) -> str:
    """Replace a value, keeping the quotes and a trailing comma around it."""
    stripped = raw.rstrip()
    trailing = raw[len(stripped) :]
    comma = "," if stripped.endswith(",") else ""
    core = stripped[: len(stripped) - len(comma)]
    quote = core[0] if core[:1] in ("'", '"') and core[-1:] == core[:1] and len(core) > 1 else ""
    return f"{quote}{REDACTED}{quote}{comma}{trailing}"


def _is_sensitive_key(key: str, context: str = "") -> bool:
    """`context` is the chain of parent keys of a nested (YAML) file: `username` under
    `spring.datasource` is the user of a connection."""
    cleaned = key.strip().strip("'\"-").strip()
    if not cleaned:
        return False
    if _CREDENTIAL_KEY.search(cleaned):
        return True
    return bool(_USER_KEY.search(cleaned) and _CONNECTION_CONTEXT.search(f"{context}.{cleaned}"))


def _line(line: str, context: str = "") -> tuple[str, int]:
    count = 0

    def userinfo(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group('scheme')}{REDACTED}@"

    def param(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_literal(match.group("value")):
            return match.group(0)
        count += 1
        return f"{match.group('sep')}{match.group('name')}={REDACTED}"

    line = _URL_USERINFO.sub(userinfo, line)
    line = _URL_PARAM.sub(param, line)

    def tag(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_literal(match.group("value")):
            return match.group(0)
        count += 1
        return f"{match.group(1)}{REDACTED}{match.group(4)}"

    def attribute(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_literal(match.group("value")):
            return match.group(0)
        count += 1
        quote = match.group("quote")
        return f"{match.group('head')}{quote}{REDACTED}{quote}"

    line = _XML_TAG.sub(tag, line)
    line = _XML_ATTR.sub(attribute, line)

    separator = _SEPARATOR.search(line)
    if separator is None:
        return line, count
    key, value = line[: separator.start()], line[separator.end() :]
    numbers = not any(w.lower() in _NUMERIC_WORDS for w in _KEY_WORDS.split(key.strip()))
    if (
        _is_sensitive_key(key, context)
        and _is_literal(value, numbers=numbers)
        and REDACTED not in value
    ):
        count += 1
        spacing = value[: len(value) - len(value.lstrip())]
        return f"{key}{separator.group(0)}{spacing}{_redact_value(value.lstrip())}", count
    return line, count


def redact(path: PurePosixPath | str, text: str) -> Redacted:
    """`text` of a configuration file with its credential values replaced; any other file is
    returned unchanged."""
    if not is_config_file(path):
        return Redacted(text, 0)
    nested = str(path).lower().endswith((".yml", ".yaml"))
    parents: list[tuple[int, str]] = []  # YAML: the keys above the current line
    total = 0
    out: list[str] = []
    for line in text.split("\n"):
        context = ""
        if nested and line.strip() and not line.lstrip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            while parents and parents[-1][0] >= indent:
                parents.pop()
            context = ".".join(name for _, name in parents)
            opening = _YAML_PARENT.match(line)
            if opening:
                parents.append((indent, opening.group(1)))
        redacted, count = _line(line, context)
        total += count
        out.append(redacted)
    return Redacted("\n".join(out), total)
