"""What the tool keeps of a specification: the items tests can trace to, and what was retired."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    text: str
    text_sha256: str
    confidence: str
    scenario: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class Gap:
    id: str
    text: str
    open: bool


@dataclass(frozen=True)
class Feature:
    slug: str
    title: str
    code_version: str
    items: tuple[Item, ...]
    retired: tuple[str, ...]
    gaps: tuple[Gap, ...]


@dataclass(frozen=True)
class Application:
    name: str
    code_version: str
    features: tuple[Feature, ...] = field(default_factory=tuple)

    def items(self) -> list[Item]:
        return [item for feature in self.features for item in feature.items]

    def retired(self) -> list[str]:
        return [rid for feature in self.features for rid in feature.retired]
