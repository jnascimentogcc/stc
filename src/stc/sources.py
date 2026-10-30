"""Where the applications come from (IDX-01).

A *source* is a folder that holds applications. The folder given as `STC_ROOT` (or `--root`) is
the source called `default`; more are declared in a `sources.toml`:

    [[sources]]
    name = "legacy"
    type = "local"
    path = "D:/code/legacy"         # relative paths are relative to the sources.toml

Each source is scanned like the Root always was: its own `stc.toml` renames, excludes and merges
applications. An application remembers its source, so a file is always read from the right folder.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

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


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["local"] = "local"
    path: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("use lower case letters, digits, `-` and `_`")
        if value == DEFAULT_SOURCE:
            raise ValueError(f"{DEFAULT_SOURCE!r} is the source of STC_ROOT; pick another name")
        return value


def load_sources(file: Path) -> dict[str, Path]:
    """The sources declared in `file`, with absolute folders."""
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SourcesError(f"{file}: {exc}") from exc
    unknown = set(data) - {"sources"}
    if unknown:
        raise SourcesError(f"{file}: unknown key(s): {', '.join(sorted(unknown))}")
    out: dict[str, Path] = {}
    for entry in data.get("sources", []):
        try:
            source = SourceConfig.model_validate(entry)
        except ValidationError as exc:
            raise SourcesError(f"{file}: {exc}") from exc
        if source.name in out:
            raise SourcesError(f"{file}: the source {source.name!r} is declared twice")
        path = Path(source.path).expanduser()
        out[source.name] = path if path.is_absolute() else (file.parent / path).resolve()
    return out


def build_roots(root: Path | None, sources_file: Path | None) -> Roots:
    """`root` is the folder of the `default` source; `sources_file` (or a `sources.toml` in the
    current folder) declares the others."""
    paths: dict[str, Path] = {}
    if root is not None:
        paths[DEFAULT_SOURCE] = root
    file = sources_file
    if file is None and Path(SOURCES_FILENAME).is_file():
        file = Path(SOURCES_FILENAME)
    if file is not None:
        paths.update(load_sources(file))
    return Roots(paths)
