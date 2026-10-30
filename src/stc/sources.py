"""Where the applications come from (IDX-01).

A *source* is a folder that holds applications. The folder given as `STC_ROOT` (or `--root`) is
the source called `default`; more are declared in a `sources.toml`:

    [[sources]]
    name = "legacy"
    type = "local"
    path = "D:/code/legacy"         # relative paths are relative to the sources.toml

    [[sources]]
    name = "my-org"
    type = "github"                  # `stc sync` clones them into STC_DATA/sources/my-org
    owner = "my-org"                 # an organization or a user
    include = ["svc-*"]              # names; default: all
    exclude = ["*-archive"]
    exclude_archived = true          # default
    exclude_forks = false            # default
    branch = "main"                  # default: the repository's default branch
    # repositories = ["svc-a", "svc-b"]   # only these, by name
    # api_url = "https://github.example.com/api/v3"       # GitHub Enterprise

Each source is scanned like the Root always was: its own `stc.toml` renames, excludes and merges
applications. An application remembers its source, so a file is always read from the right folder.
The GitHub token is `STC_GITHUB_TOKEN` (read-only access to the contents is enough).
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

DEFAULT_SOURCE = "default"
SOURCES_FILENAME = "sources.toml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SourcesError(Exception):
    pass


@dataclass(frozen=True)
class Roots:
    """Source name -> the folder it is read from."""

    paths: Mapping[str, Path]

    @classmethod
    def single(cls, path: Path) -> "Roots":
        return cls({DEFAULT_SOURCE: path})

    def get(self, source: str) -> Path | None:
        return self.paths.get(source)

    def only(self, source: str) -> "Roots":
        if source not in self.paths:
            known = ", ".join(sorted(self.paths)) or "none"
            raise SourcesError(f"No source named {source!r}. Configured: {known}")
        return Roots({source: self.paths[source]})

    def __bool__(self) -> bool:
        return bool(self.paths)


def as_roots(root: "Path | Roots") -> Roots:
    return root if isinstance(root, Roots) else Roots.single(root)


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("use lower case letters, digits, `-` and `_`")
        if value == DEFAULT_SOURCE:
            raise ValueError(f"{DEFAULT_SOURCE!r} is the source of STC_ROOT; pick another name")
        return value


class LocalSource(_Source):
    type: Literal["local"] = "local"
    path: str


class GitHubSource(_Source):
    type: Literal["github"]
    owner: str
    repositories: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    exclude_archived: bool = True
    exclude_forks: bool = False
    branch: str | None = None
    api_url: str = "https://api.github.com"

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str) -> str:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]*$", value):
            raise ValueError("a GitHub organization or user name")
        return value

    @field_validator("api_url")
    @classmethod
    def _api_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("the API URL must be https (the token is sent to it)")
        return value.rstrip("/")


SourceConfig = LocalSource | GitHubSource
_ANY = TypeAdapter(Annotated[SourceConfig, Field(discriminator="type")])


def read_sources(file: Path) -> dict[str, SourceConfig]:
    """The sources declared in `file`; the folder of a local source is made absolute."""
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SourcesError(f"{file}: {exc}") from exc
    unknown = set(data) - {"sources"}
    if unknown:
        raise SourcesError(f"{file}: unknown key(s): {', '.join(sorted(unknown))}")
    out: dict[str, SourceConfig] = {}
    for entry in data.get("sources", []):
        if not isinstance(entry, dict):
            raise SourcesError(f"{file}: each source is a [[sources]] table")
        entry = {"type": "local", **entry}
        if entry["type"] not in ("local", "github"):
            raise SourcesError(f"{file}: the type of a source is `local` or `github`")
        try:
            source = _ANY.validate_python(entry)
        except ValidationError as exc:
            raise SourcesError(f"{file}: {exc}") from exc
        if source.name in out:
            raise SourcesError(f"{file}: the source {source.name!r} is declared twice")
        if isinstance(source, LocalSource):
            path = Path(source.path).expanduser()
            source = source.model_copy(
                update={"path": str(path if path.is_absolute() else (file.parent / path).resolve())}
            )
        out[source.name] = source
    return out


def github_folder(data: Path, name: str) -> Path:
    """Where `stc sync` keeps the clones of a GitHub source."""
    return data / "sources" / name


def folders_of(sources: Mapping[str, SourceConfig], data: Path) -> dict[str, Path]:
    return {
        name: Path(s.path) if isinstance(s, LocalSource) else github_folder(data, name)
        for name, s in sources.items()
    }


def load_sources(file: Path, data: Path = Path("data")) -> dict[str, Path]:
    """Source name -> folder, for the sources declared in `file`."""
    return folders_of(read_sources(file), data)


def sources_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    return Path(SOURCES_FILENAME) if Path(SOURCES_FILENAME).is_file() else None


def build_roots(root: Path | None, file: Path | None, data: Path = Path("data")) -> Roots:
    """`root` is the folder of the `default` source; `file` (or a `sources.toml` in the current
    folder) declares the others."""
    paths: dict[str, Path] = {}
    if root is not None:
        paths[DEFAULT_SOURCE] = root
    chosen = sources_file(file)
    if chosen is not None:
        paths.update(load_sources(chosen, data))
    return Roots(paths)
