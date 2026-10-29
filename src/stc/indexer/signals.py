"""Extracts cross-application signals from one file (CAT-07).

A signal is one fact that can link two applications: an HTTP endpoint it exposes or calls, a
topic it publishes to or subscribes to, a database it uses, a package it provides or depends on.
Extraction is deterministic and line-based (regular expressions, no execution); `relations.py`
joins the signals of different applications. Credentials are never kept: connection strings are
reduced to engine, host, port and database.
"""

import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from stc.indexer.manifests import is_manifest, manifest_signals
from stc.indexer.signal_types import Signal

EXTRACTOR_VERSION = 2  # 2: Spring ${property} kept in endpoint keys; test files ignored
MAX_LINE = 2_000  # longer lines (minified code) are cut before matching

# Languages whose files can contain signals; docs (markdown, text) are left out as noisy.
SIGNAL_LANGUAGES = frozenset(
    {
        "python", "javascript", "typescript", "java", "kotlin", "scala", "groovy", "csharp",
        "vb", "fsharp", "go", "rust", "ruby", "php", "swift", "dart", "elixir", "c", "cpp",
        "yaml", "json", "toml", "xml", "ini", "properties", "dotenv", "dockerfile", "shell",
        "powershell", "batch", "terraform", "vue", "svelte", "html",
    }
)  # fmt: skip

_GENERIC_PATHS = frozenset(
    {"health", "healthz", "ping", "status", "metrics", "ready", "readyz", "live", "livez",
     "version", "favicon.ico", "api", "v1", "v2", "docs", "swagger", "openapi.json", "robots.txt",
     "index.html", "error", "login", "logout"}
)  # fmt: skip


def wanted(path: PurePosixPath, language: str | None) -> bool:
    return is_manifest(path) or (language in SIGNAL_LANGUAGES)


def is_generic_path(path: str) -> bool:
    segments = [s for s in path.split("/") if s]
    return not segments or (len(segments) == 1 and segments[0] in _GENERIC_PATHS)


# --------------------------------------------------------------------------------------------
# path normalization
# --------------------------------------------------------------------------------------------

_PARAM = re.compile(r"\$\{[^}]*\}|\{\{[^}]*\}\}|\{[^/}]*\}|<[^/>]*>|\[[^/\]]*\]|:[A-Za-z_]\w*")
_DJANGO_GROUP = re.compile(r"\(\?P<\w+>[^)]*\)|\([^)]*\)")


_PROPERTY = re.compile(r"\$\{([A-Za-z][\w.\-]*)(?::[^}]*)?\}")
# Private-use characters mark a property while the rest of the path is normalized.
_MARK_OPEN, _MARK_CLOSE = "\ue000", "\ue001"
_STASHED = re.compile(_MARK_OPEN + r"(\d+)" + _MARK_CLOSE)


def normalize_path(raw: str, *, keep_properties: bool = False) -> str:
    """`/Orders/{id}/`, `/orders/:id`, `/orders/<int:pk>` and `/orders/42` all become
    `/orders/{}`. With `keep_properties` a Spring `${api.prefix}` stays as it is (lower case) so
    that it can be given its value from the application's configuration later."""
    path = raw.split("?")[0].split("#")[0].strip()
    kept: list[str] = []
    if keep_properties:

        def stash(match: re.Match[str]) -> str:
            kept.append(match.group(0).lower())
            return f"{_MARK_OPEN}{len(kept) - 1}{_MARK_CLOSE}"

        path = _PROPERTY.sub(stash, path)
    path = _PARAM.sub("{}", path)
    segments = ["{}" if s.isdigit() else s.lower() for s in path.split("/") if s]
    result = "/" + "/".join(segments)
    return _STASHED.sub(lambda m: kept[int(m.group(1))], result) if kept else result


def _join(*parts: str) -> str:
    return "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))


def _django(raw: str) -> str:
    cleaned = _DJANGO_GROUP.sub("{}", raw.removeprefix("^").removesuffix("$"))
    return "/" + cleaned.strip("/")


# --------------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------------


_TEST_DIRECTORIES = frozenset(
    {"test", "tests", "__tests__", "spec", "specs", "e2e", "cypress", "testing", "fixtures",
     "__mocks__", "mocks", "testdata"}
)  # fmt: skip
_TEST_FILE = re.compile(
    r"(?:\.(?:test|spec|e2e)\.[\w]+$|^test_[\w.]+$|_test\.\w+$|(?:Test|Tests|IT|ITCase)\.\w+$)"
)


def is_test_path(path: PurePosixPath) -> bool:
    """Tests call and serve things that no real application depends on (a test that calls its own
    API, a mock server), so they give no signals."""
    return bool(_TEST_DIRECTORIES & {p.lower() for p in path.parts[:-1]}) or bool(
        _TEST_FILE.search(path.name)
    )


def extract_signals(path: PurePosixPath, text: str, language: str | None) -> list[Signal]:
    if is_manifest(path):
        return manifest_signals(path, text)
    if language not in SIGNAL_LANGUAGES or is_test_path(path):
        return []
    lines = [line[:MAX_LINE] for line in text.replace("\r\n", "\n").split("\n")]
    found: list[Signal] = []
    found += _endpoints(path, text, lines, language)
    found += _http_consumers(lines)
    found += _messaging(lines)
    found += _databases(lines)
    return _dedupe(found)


def _dedupe(signals: list[Signal]) -> list[Signal]:
    seen: set[tuple[str, str, str, str | None, int]] = set()
    unique: list[Signal] = []
    for s in signals:
        marker = (s.kind, s.role, s.key, s.host, s.start_line)
        if marker not in seen:
            seen.add(marker)
            unique.append(s)
    return unique


def _provides(line: int, method: str, raw_path: str, *, properties: bool = False) -> Signal:
    key = normalize_path(raw_path, keep_properties=properties)
    return Signal("http", "provides", key, None, f"{method.upper()} {raw_path}", line, line)


# --------------------------------------------------------------------------------------------
# endpoints a file exposes
# --------------------------------------------------------------------------------------------

_PY_ROUTE = re.compile(
    r"""@[\w.]+\.(get|post|put|delete|patch|head|options|route|api_route)\(\s*[rRfF]?(['"])(/[^'"]*)\2"""
)
_DJANGO_PATH = re.compile(r"""\b(?:re_)?path\(\s*r?(['"])([^'"]*)\1""")
_JS_ROUTE = re.compile(
    r"""\b(?:app|router|routes?|server|fastify|\w*Router|\w*router)\.(get|post|put|delete|patch|all)"""
    r"""\(\s*(['"`])(/[^'"`]*)\2\s*,"""
)
_JS_CHAIN = re.compile(r"""\.route\(\s*(['"`])(/[^'"`]*)\1\s*\)""")
_NEST_CTRL = re.compile(r"""@Controller\(\s*(?:\{[^}]*path:\s*)?(['"`])([^'"`]*)\1""")
_NEST_ROUTE = re.compile(r"""@(Get|Post|Put|Delete|Patch|All)\(\s*(?:(['"`])([^'"`]*)\2)?\s*\)""")
_SPRING = re.compile(
    r"""@(Get|Post|Put|Delete|Patch)Mapping\b(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*"([^"]*)")?"""
)
_SPRING_REQ = re.compile(
    r"""@RequestMapping\b(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*"([^"]*)")?"""
)
_JAXRS = re.compile(r'''@Path\(\s*"([^"]*)"''')
_CLASS = re.compile(r"\b(?:class|interface|object)\s+(\w+)")
_KTOR = re.compile(r"""^\s*(get|post|put|delete|patch)\(\s*"(/[^"]*)"\s*\)\s*\{""")
_CS_ROUTE = re.compile(r"""\[Route\(\s*"([^"]*)"\s*\)\]""")
_CS_HTTP = re.compile(r"""\[Http(Get|Post|Put|Delete|Patch)(?:\(\s*"([^"]*)"\s*\))?\]""")
_CS_MINIMAL = re.compile(r'''\.Map(Get|Post|Put|Delete|Patch)\(\s*"([^"]*)"''')
_GO_STD = re.compile(
    r'''\.(?:HandleFunc|Handle)\(\s*"(?:(GET|POST|PUT|DELETE|PATCH)\s+)?(/[^"]*)"'''
)
_GO_ROUTER = re.compile(
    r'''\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(\s*"(/[^"]*)"'''
)
_RAILS = re.compile(r"""^\s*(get|post|put|patch|delete)\s+['"]([^'"]+)['"]""")
_RAILS_RES = re.compile(r"""^\s*resources?\s+:(\w+)""")
_LARAVEL = re.compile(r"""Route::(get|post|put|delete|patch|any)\(\s*['"]([^'"]*)['"]""")
_YAML_PATH = re.compile(r"""^\s+(['"]?)(/[^\s'":]*)\1\s*:\s*$""")
_OPENAPI = re.compile(
    r"""(?:^|[{,])\s*['"]?(openapi|swagger)['"]?\s*:""", re.MULTILINE | re.IGNORECASE
)


def _class_ahead(lines: list[str], index: int, window: int = 4) -> str | None:
    """Name of a class declared on this line or within the next few (decorator-to-class gap)."""
    for line in lines[index : index + window]:
        match = _CLASS.search(line)
        if match:
            return match.group(1)
    return None


def _endpoints(
    path: PurePosixPath, text: str, lines: list[str], language: str | None
) -> list[Signal]:
    found: list[Signal] = []
    if language in ("yaml", "json") and _OPENAPI.search(text[:4000]):
        return _openapi(text, lines, language)
    prefix = ""
    controller = ""
    for index, line in enumerate(lines):
        number = index + 1
        if language == "python":
            if m := _PY_ROUTE.search(line):
                found.append(_provides(number, "ANY" if "route" in m[1] else m[1], m[3]))
            if path.name.startswith("urls"):
                for hit in _DJANGO_PATH.finditer(line):
                    found.append(_provides(number, "ANY", _django(hit[2])))
        elif language in ("javascript", "typescript"):
            if m := _JS_ROUTE.search(line):
                found.append(_provides(number, m[1], m[3]))
            if m := _JS_CHAIN.search(line):
                found.append(_provides(number, "ANY", m[2]))
            if m := _NEST_CTRL.search(line):
                prefix = m[2]
            elif (m := _NEST_ROUTE.search(line)) and "@Controller" in text:
                found.append(_provides(number, m[1], _join(prefix, m[3] or "")))
        elif language in ("java", "kotlin"):
            found += _jvm_line(lines, index, number, language)
        elif language == "csharp":
            if m := _CS_ROUTE.search(line):
                name = _class_ahead(lines, index)
                if name:
                    controller = name.removesuffix("Controller").lower()
                    prefix = m[1].replace("[controller]", controller)
                else:
                    found.append(_provides(number, "ANY", _join(prefix, m[1])))
            elif m := _CS_HTTP.search(line):
                found.append(_provides(number, m[1], _join(prefix, m[2] or "")))
            if m := _CS_MINIMAL.search(line):
                found.append(_provides(number, m[1], m[2]))
        elif language == "go":
            if m := _GO_STD.search(line):
                found.append(_provides(number, m[1] or "ANY", m[2]))
            elif m := _GO_ROUTER.search(line):
                found.append(_provides(number, m[1], m[2]))
        elif language == "ruby" and path.name.startswith("routes"):
            if m := _RAILS.search(line):
                found.append(_provides(number, m[1], "/" + m[2].lstrip("/")))
            elif m := _RAILS_RES.search(line):
                found.append(_provides(number, "ANY", f"/{m[1]}"))
                found.append(_provides(number, "ANY", f"/{m[1]}/:id"))
        elif language == "php" and (m := _LARAVEL.search(line)):
            found.append(_provides(number, m[1], "/" + m[2].lstrip("/")))
    return found


def _jvm_line(lines: list[str], index: int, number: int, language: str | None) -> list[Signal]:
    """Spring / JAX-RS / Ktor. A `@RequestMapping`/`@Path` right above a class is a prefix that
    applies to the mappings below it, which are looked up from the preceding lines."""
    line = lines[index]
    if language == "kotlin" and (m := _KTOR.search(line)):
        return [_provides(number, m[1], m[2])]
    spring = _SPRING.search(line)
    if not spring:
        spring_req = _SPRING_REQ.search(line)
        if spring_req and not _class_ahead(lines, index):
            path = _jvm_prefix(lines, index) + (spring_req[1] or "")
            return [_provides(number, "ANY", path, properties=True)]
        jaxrs = _JAXRS.search(line)
        if jaxrs and not _class_ahead(lines, index):
            return [_provides(number, "ANY", _jvm_prefix(lines, index) + jaxrs[1], properties=True)]
        return []
    path = _jvm_prefix(lines, index) + (spring[2] or "")
    return [_provides(number, spring[1], path, properties=True)]


def _jvm_prefix(lines: list[str], index: int) -> str:
    """The class-level @RequestMapping/@Path above the nearest preceding class declaration."""
    for back in range(index, -1, -1):
        if _CLASS.search(lines[back]) and back != index:
            for above in range(back, max(back - 6, -1), -1):
                m = _SPRING_REQ.search(lines[above]) or _JAXRS.search(lines[above])
                if m and m.group(1):
                    return "/" + m.group(1).strip("/")
            return ""
    return ""


def _openapi(text: str, lines: list[str], language: str | None) -> list[Signal]:
    found: list[Signal] = []
    if language == "json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return []
        paths = document.get("paths") if isinstance(document, dict) else None
        if not isinstance(paths, dict):
            return []
        for raw in paths:
            needle = f'"{raw}"'
            number = next((i + 1 for i, line in enumerate(lines) if needle in line), 1)
            found.append(_provides(number, "ANY", str(raw)))
        return found
    in_paths = False
    for index, line in enumerate(lines):
        if re.match(r"^paths\s*:", line):
            in_paths = True
            continue
        if in_paths and line and not line[0].isspace() and not line.startswith("#"):
            in_paths = False
        if in_paths and (m := _YAML_PATH.match(line)):
            found.append(_provides(index + 1, "ANY", m[2]))
    return found


# --------------------------------------------------------------------------------------------
# HTTP calls a file makes
# --------------------------------------------------------------------------------------------

_URL = re.compile(r"""https?://[^\s'"`<>()\[\]\\,;]+""")
_INTERPOLATED = re.compile(r"""(?:\$\{|\{\{?|%\()\s*([A-Za-z_][\w.]*)\s*[)}]\}?(/[\w\-./{}:$]+)""")
_CLIENT_CALL = re.compile(
    r"""(?:\bfetch|\baxios(?:\.\w+)?|\$http(?:\.\w+)?|\bhttp\.(?:get|post|put|delete|patch)"""
    r"""|\bhttpClient\.\w+|\brequests\.(?:get|post|put|delete|patch)|\bclient\.(?:get|post|put|delete|patch)"""
    r"""|\brestTemplate\.\w+|\bwebClient\.\w+|\bHttpClient\.\w+|\bgot(?:\.\w+)?|\bky(?:\.\w+)?)"""
    r"""\(\s*[`'"](/[^`'"\s]*)"""
)
_ENV_VAR = re.compile(
    r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)_(?:URL|URI|HOST|ENDPOINT|BASE_URL|ADDR|ADDRESS)\b"
)
_INTERNAL_SUFFIXES = (".local", ".internal", ".svc", ".lan", ".cluster.local")
_IGNORED_URL_HOSTS = frozenset(
    {"www.w3.org", "schemas.xmlsoap.org", "json-schema.org", "schemas.microsoft.com",
     "maven.apache.org", "xmlns.jcp.org", "java.sun.com", "www.springframework.org",
     "example.com", "www.example.com"}
)  # fmt: skip
_PLACEHOLDER_HOST = re.compile(r"[${}%()]")
_MAPPING_LINE = re.compile(r"@(?:\w*Mapping|Path|Controller|RestController)\b")


def _http_consumers(lines: list[str]) -> list[Signal]:
    found: list[Signal] = []
    for index, line in enumerate(lines):
        number = index + 1
        for url in _URL.finditer(line):
            signal = _url_signal(url.group(0), number)
            if signal:
                found.append(signal)
        if _MAPPING_LINE.search(line):
            continue  # `${property}/path` in a mapping annotation is an endpoint, not a call
        for interp in _INTERPOLATED.finditer(line):
            variable, tail = interp.group(1), interp.group(2)
            env = _ENV_VAR.fullmatch(variable.upper().split(".")[-1])
            host = env.group(1).lower().replace("_", "-") if env else None
            found.append(
                Signal(
                    "http",
                    "consumes",
                    normalize_path(tail),
                    host,
                    f"{variable}{tail}",
                    number,
                    number,
                )
            )
        if m := _CLIENT_CALL.search(line):
            found.append(
                Signal("http", "consumes", normalize_path(m[1]), None, m[1], number, number)
            )
        for env_var in _ENV_VAR.finditer(line):
            token = env_var.group(1).lower().replace("_", "-")
            found.append(
                Signal("http", "consumes", "", token, f"via {env_var.group(0)}", number, number)
            )
    return found


def _url_signal(url: str, number: int) -> Signal | None:
    try:
        parts = urlsplit(url.rstrip(".'\"`"))
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if _PLACEHOLDER_HOST.search(url.split("//", 1)[1].split("/", 1)[0]):
        return None  # handled by the interpolation / env-var patterns
    if not host or host in _IGNORED_URL_HOSTS:
        return None
    detail = f"{parts.scheme}://{host}{parts.path}"
    local = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    return Signal(
        "http", "consumes", normalize_path(parts.path) if parts.path else "",
        None if local else host, detail, number, number,
    )  # fmt: skip


# --------------------------------------------------------------------------------------------
# messaging
# --------------------------------------------------------------------------------------------

_TOPIC = r"""['"]([\w][\w.\-/:]*)['"]"""
_PUBLISH_PATTERNS = (
    re.compile(r"""new\s+ProducerRecord\s*<[^>]*>\s*\(\s*""" + _TOPIC),
    re.compile(
        r"""\b(?:kafkaTemplate|producer|channel|emitter|bus)\.(?:send|produce|publish|emit|convertAndSend|basicPublish|basic_publish|sendMessage)\(\s*(?:topic\s*[=:]\s*|routing_key\s*=\s*|queue\s*=\s*)?"""
        + _TOPIC
    ),
    re.compile(r"""\.send\(\s*\{\s*topic\s*:\s*""" + _TOPIC),
    re.compile(r"""routing_key\s*=\s*""" + _TOPIC),
    re.compile(r"""\b(?:convertAndSend|sendMessage|SendMessage)\(\s*""" + _TOPIC),
)
_SUBSCRIBE_PATTERNS = (
    re.compile(r"""@KafkaListener\([^)]*topics\s*=\s*\{?\s*""" + _TOPIC),
    re.compile(r"""@RabbitListener\([^)]*queues\s*=\s*\{?\s*""" + _TOPIC),
    re.compile(r"""@SqsListener\(\s*(?:value\s*=\s*)?""" + _TOPIC),
    re.compile(r"""@(?:MessagePattern|EventPattern)\(\s*""" + _TOPIC),
    re.compile(
        r"""\b(?:consumer|channel)\.(?:subscribe|basic_consume|basicConsume)\(\s*\[?\s*(?:topic\s*[=:]\s*|queue\s*=\s*)?"""
        + _TOPIC
    ),
    re.compile(r"""\bsubscribe\(\s*\{?\s*topics?\s*:\s*\[?\s*""" + _TOPIC),
)


def _messaging(lines: list[str]) -> list[Signal]:
    found: list[Signal] = []
    for index, line in enumerate(lines):
        number = index + 1
        for role, patterns in (
            ("publishes", _PUBLISH_PATTERNS),
            ("subscribes", _SUBSCRIBE_PATTERNS),
        ):
            for pattern in patterns:
                for m in pattern.finditer(line):
                    topic = m.group(1)
                    if _topic_like(topic):
                        found.append(
                            Signal("messaging", role, topic.lower(), None, topic, number, number)
                        )
    return found


def _topic_like(value: str) -> bool:
    """A bare word such as "hello" is almost always not a topic; require a separator."""
    return len(value) >= 3 and any(c in value for c in ".-_:/")


# --------------------------------------------------------------------------------------------
# databases
# --------------------------------------------------------------------------------------------

_DB_URL = re.compile(
    r"""\b(?:jdbc:)?(postgres(?:ql)?|mysql|mariadb|sqlserver|oracle|mongodb(?:\+srv)?|rediss?)"""
    r"""://([^\s'"`<>]+)""",
    re.IGNORECASE,
)
_ADO = re.compile(
    r"""(?:Server|Data Source)\s*=\s*([^;'"]+);.*?(?:Database|Initial Catalog)\s*=\s*([^;'"]+)""",
    re.IGNORECASE,
)
_DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "sqlserver": 1433,
                  "oracle": 1521, "mongodb": 27017, "redis": 6379}  # fmt: skip


def _databases(lines: list[str]) -> list[Signal]:
    found: list[Signal] = []
    for index, line in enumerate(lines):
        number = index + 1
        for db in _DB_URL.finditer(line):
            signal = _db_signal(db.group(1), db.group(2), number)
            if signal:
                found.append(signal)
        if m := _ADO.search(line):
            host = m.group(1).strip().split(",")[0].lower()
            if not _PLACEHOLDER_HOST.search(host):
                key = f"sqlserver://{host}:1433/{m.group(2).strip().lower()}"
                found.append(Signal("database", "uses", key, host, key, number, number))
    return found


def _db_signal(scheme: str, rest: str, number: int) -> Signal | None:
    engine = scheme.lower().split("+")[0]
    engine = "postgresql" if engine.startswith("postgres") else engine
    engine = "redis" if engine.startswith("redis") else engine
    authority, _, tail = rest.partition("/")
    if ";" in authority:  # jdbc:sqlserver://host:1433;databaseName=db
        authority, _, options = authority.partition(";")
        tail = tail or next(
            (o.split("=", 1)[1] for o in options.split(";") if o.lower().startswith("database")),
            "",
        )
    hostport = authority.rsplit("@", 1)[-1]  # drop user:password
    if not hostport or _PLACEHOLDER_HOST.search(hostport):
        return None
    host, _, port = hostport.partition(":")
    database = tail.split("?")[0].split(";")[0].strip("/").lower()
    host = host.lower()
    if not host or not database and engine != "redis":
        return None
    port_number = port if port.isdigit() else str(_DEFAULT_PORTS.get(engine, ""))
    key = f"{engine}://{host}:{port_number}/{database}"
    return Signal("database", "uses", key, host, key, number, number)
