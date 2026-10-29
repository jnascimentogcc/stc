"""Derives application -> application relations from the signals of all applications (CAT-07).

Direction is who calls / depends on whom: an HTTP caller points at the application that serves the
endpoint, a publisher at its subscribers, a dependent at the library it uses. `shared_database`
is undirected and stored with the lower application id as source. Every relation keeps the
strongest piece of evidence and a confidence level; ambiguous matches are dropped, not guessed.
"""

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from stc.indexer.signals import is_generic_path

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_INTERNAL_SUFFIXES = (".local", ".internal", ".svc", ".lan", ".cluster.local")
_NAME_SUFFIXES = ("-service", "-svc", "-api", "-server", "-app")
_GENERIC_DB_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "db", "database", "postgres", "postgresql", "mysql",
     "mariadb", "mongo", "mongodb", "redis", "mssql", "sqlserver", "sql"}
)  # fmt: skip


@dataclass(frozen=True)
class AppRef:
    id: int
    name: str
    hosts: tuple[str, ...] = ()  # extra names other applications use for it (stc.toml)


@dataclass(frozen=True)
class SignalRow:
    id: int
    application_id: int
    file_id: int
    kind: str
    role: str
    key: str
    host: str | None
    detail: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RelationDraft:
    source_app_id: int
    target_app_id: int
    type: str
    detail: str
    evidence_file_id: int
    evidence_start_line: int
    evidence_end_line: int
    confidence: str
    evidence_count: int


@dataclass
class _Evidence:
    confidence: str
    signal: SignalRow
    detail: str


@dataclass
class _Collector:
    items: dict[tuple[int, int, str], list[_Evidence]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def add(self, source: int, target: int, type_: str, evidence: _Evidence) -> None:
        if source != target:
            self.items[(source, target, type_)].append(evidence)

    def drafts(self) -> list[RelationDraft]:
        drafts = []
        for (source, target, type_), evidence in self.items.items():
            best = max(evidence, key=lambda e: CONFIDENCE_RANK[e.confidence])
            details = list(dict.fromkeys(e.detail for e in evidence))[:3]
            drafts.append(
                RelationDraft(
                    source,
                    target,
                    type_,
                    "; ".join(details),
                    best.signal.file_id,
                    best.signal.start_line,
                    best.signal.end_line,
                    best.confidence,
                    len(evidence),
                )  # fmt: skip
            )
        return sorted(drafts, key=lambda d: (d.source_app_id, d.target_app_id, d.type))


# --------------------------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------------------------


def service_token(name: str) -> str:
    """`Billing_Service`, `billing-svc` and `billing` are the same service: `billing`."""
    token = re.sub(r"[_.\s]+", "-", name.strip().lower())
    changed = True
    while changed:
        changed = False
        for suffix in _NAME_SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix) + 2:
                token = token.removesuffix(suffix)
                changed = True
    return token


def _host_token(host: str) -> str | None:
    """The service name a host stands for. A public host such as `api.example.com` is not an
    internal service, so only bare names and cluster-internal names count."""
    host = host.lower().split(":")[0]
    if "." not in host:
        return service_token(host)
    if host.endswith(_INTERNAL_SUFFIXES):
        return service_token(host.split(".")[0])
    return None


class _Names:
    def __init__(self, apps: Sequence[AppRef]) -> None:
        tokens: dict[str, set[int]] = defaultdict(set)
        exact: dict[str, set[int]] = defaultdict(set)
        for app in apps:
            tokens[service_token(app.name)].add(app.id)
            for alias in app.hosts:
                exact[alias.lower()].add(app.id)
                tokens[service_token(alias.split(".")[0])].add(app.id)
        self._tokens = tokens
        self._exact = exact

    def resolve(self, host: str) -> int | None:
        """The one application a host refers to, or None if unknown or ambiguous."""
        hits = set(self._exact.get(host.lower().split(":")[0], ()))
        if not hits:
            token = _host_token(host)
            if token and len(token) >= 3:
                hits = set(self._tokens.get(token, ()))
        return next(iter(hits)) if len(hits) == 1 else None


# --------------------------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------------------------


def derive_relations(apps: Sequence[AppRef], signals: Sequence[SignalRow]) -> list[RelationDraft]:
    collector = _Collector()
    _http(collector, apps, signals)
    _messaging(collector, signals)
    _libraries(collector, signals)
    _databases(collector, signals)
    return collector.drafts()


def _http(collector: _Collector, apps: Sequence[AppRef], signals: Sequence[SignalRow]) -> None:
    names = _Names(apps)
    provided: dict[str, list[SignalRow]] = defaultdict(list)
    for s in signals:
        if s.kind == "http" and s.role == "provides":
            provided[s.key].append(s)

    def providers_of(key: str, exclude: int) -> tuple[set[int], bool]:
        """Apps that serve `key` (exactly, else by path suffix) and whether it was exact."""
        exact = {p.application_id for p in provided.get(key, ()) if p.application_id != exclude}
        if exact:
            return exact, True
        fuzzy: set[int] = set()
        key_parts = key.strip("/").split("/")
        for other, rows in provided.items():
            other_parts = other.strip("/").split("/")
            shorter = min(len(key_parts), len(other_parts))
            if shorter < 2 or is_generic_path(other):
                continue
            if key_parts[-shorter:] == other_parts[-shorter:]:
                fuzzy |= {p.application_id for p in rows if p.application_id != exclude}
        return fuzzy, False

    for s in signals:
        if s.kind != "http" or s.role != "consumes":
            continue
        specific_path = bool(s.key) and not is_generic_path(s.key)
        if s.host:
            target = names.resolve(s.host)
            if target is None:
                continue  # an external or unknown host: not one of our applications
            via_url = s.detail.startswith("http")
            confidence = "high" if via_url else "medium"
            collector.add(s.application_id, target, "http", _Evidence(confidence, s, s.detail))
        elif specific_path:
            targets, exact = providers_of(s.key, s.application_id)
            if len(targets) == 1:  # several candidates: ambiguous, so no relation
                collector.add(
                    s.application_id, next(iter(targets)), "http",
                    _Evidence("medium" if exact else "low", s, s.detail),
                )  # fmt: skip


def _messaging(collector: _Collector, signals: Sequence[SignalRow]) -> None:
    publishers: dict[str, list[SignalRow]] = defaultdict(list)
    subscribers: dict[str, list[SignalRow]] = defaultdict(list)
    for s in signals:
        if s.kind == "messaging":
            (publishers if s.role == "publishes" else subscribers)[s.key].append(s)
    for topic, pubs in publishers.items():
        for pub in pubs:
            for sub in subscribers.get(topic, ()):
                collector.add(
                    pub.application_id, sub.application_id, "messaging",
                    _Evidence("medium", pub, f"topic {topic}"),
                )  # fmt: skip


def _libraries(collector: _Collector, signals: Sequence[SignalRow]) -> None:
    providers: dict[str, set[int]] = defaultdict(set)
    for s in signals:
        if s.kind == "library" and s.role == "provides":
            providers[s.key].add(s.application_id)
    for s in signals:
        if s.kind == "library" and s.role == "consumes":
            for provider in providers.get(s.key, ()):
                collector.add(s.application_id, provider, "library", _Evidence("high", s, s.detail))


def _databases(collector: _Collector, signals: Sequence[SignalRow]) -> None:
    users: dict[str, dict[int, SignalRow]] = defaultdict(dict)
    for s in signals:
        if s.kind == "database" and s.role == "uses":
            users[s.key].setdefault(s.application_id, s)
    for key, by_app in users.items():
        for a, b in combinations(sorted(by_app), 2):
            first = by_app[a]
            generic = (first.host or "") in _GENERIC_DB_HOSTS
            collector.add(
                a, b, "shared_database", _Evidence("low" if generic else "medium", first, key)
            )


def aliases_to_apps(
    apps: Sequence[tuple[int, str]], aliases: Mapping[str, Sequence[str]]
) -> list[AppRef]:
    return [AppRef(i, name, tuple(aliases.get(name, ()))) for i, name in apps]
