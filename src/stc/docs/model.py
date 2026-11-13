from dataclasses import dataclass, field
from typing import Literal

from stc.catalog.context import SectionContext

# Bump when pages, parts, prompts or the page format change: documentation is then regenerated.
SCHEMA_VERSION = 1

Style = Literal["paragraphs", "steps"]
INDEX_PAGE = "index.md"
FEATURES_PAGE = "features/index.md"
ORPHANS_PAGE = "_orphaned-notes.md"


@dataclass(frozen=True)
class Part:
    """A narrative the model writes from evidence that code assembled (one request)."""

    id: str
    title: str
    context: SectionContext
    style: Style = "paragraphs"
    instructions: str = ""  # extra text the writer must follow, e.g. the target stack of a spec


@dataclass(frozen=True)
class Static:
    """Markdown built from facts, with no model: tables, diagrams, lists."""

    markdown: str


Block = Part | Static


@dataclass
class Page:
    path: str  # relative to the application's folder, e.g. features/job-lnnight.md
    title: str
    group: str  # navigation group
    blocks: list[Block] = field(default_factory=list)

    @property
    def parts(self) -> list[Part]:
        return [b for b in self.blocks if isinstance(b, Part)]

    def key(self, part_id: str) -> str:
        """Id of a manual block: unique across the set (`features/job-x:flow`)."""
        return f"{self.path.removesuffix('.md')}:{part_id}"


@dataclass
class Plan:
    application: str
    code_version: str | None
    files: dict[str, int]  # citable files -> number of lines
    pages: list[Page]
    skipped_features: list[str] = field(default_factory=list)
