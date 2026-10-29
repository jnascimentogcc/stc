"""Results of the query engine. Every hit carries its evidence: application, file, lines and the
code version the knowledge was indexed from (QRY-09)."""

from dataclasses import dataclass, field


class QueryError(Exception):
    """A request the engine cannot answer. `code` is stable for clients (MCP-07):
    unknown_application | invalid_argument | not_found | forbidden | unavailable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Source:
    application: str
    file: str  # relative to the application
    start_line: int
    end_line: int
    code_version: str | None

    def __str__(self) -> str:
        return f"{self.application}/{self.file}:{self.start_line}-{self.end_line}"


@dataclass
class Page[T]:
    items: list[T]
    offset: int
    limit: int
    has_more: bool
    # Explicit statements about what the engine could not do or find (QRY-10).
    notes: list[str] = field(default_factory=list)


@dataclass
class CodeHit:
    source: Source
    kind: str
    name: str | None
    snippet: str
    score: float
    matched_by: list[str]  # semantic | lexical | name


@dataclass
class LineHit:
    source: Source
    text: str


@dataclass
class SymbolHit:
    source: Source
    name: str
    kind: str
    is_definition: bool


@dataclass
class CatalogHit:
    application: str
    section: str
    snippet: str
    score: float
    code_version: str | None


@dataclass
class AppSummary:
    name: str
    path: str
    code_version: str | None
    files: int
    languages: dict[str, int]
    has_catalog: bool


@dataclass
class CatalogView:
    application: str
    code_version: str | None
    generated_at: str | None
    status: str | None
    outdated: bool  # the code was reindexed after the catalog was generated (CHT-11)
    sections: dict[str, str]


@dataclass
class GlobalMapView:
    text: str
    from_file: bool  # False when the file does not exist yet and the map was built on the fly


@dataclass
class Dependency:
    application: str  # the application reached
    direction: str  # depends_on | used_by
    depth: int
    via: list[str]  # applications on the path, starting at the one asked about
    type: str
    confidence: str
    detail: str
    evidence: Source | None


@dataclass
class FileSlice:
    source: Source
    text: str
    total_lines: int
    truncated: bool
    next_start_line: int | None
    stale: bool  # the file changed on disk after it was indexed


@dataclass
class AppStatus:
    name: str
    code_version: str | None
    files: dict[str, int]  # by status
    chunks: int
    embedded_chunks: int
    catalog_status: str | None
    catalog_outdated: bool
    relations: int


@dataclass
class IndexStatus:
    applications: list[AppStatus]
    notes: list[str] = field(default_factory=list)
