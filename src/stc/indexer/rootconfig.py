"""`stc.toml` at the Root: manual corrections to detection and extra exclusions (IDX-03, TRV-03).

Example:

    [exclude]
    patterns = ["legacy/**", "**/*.snap"]        # gitignore-style, relative to the Root

    [[application]]                              # rename a detected application
    path = "services/billing"
    name = "billing"

    [[application]]                              # include a folder detection missed
    path = "tools/etl"

    [[application]]                              # ignore a detected application
    path = "samples/demo"
    exclude = true

    [[application]]                              # merge folders into one application
    name = "shop"
    paths = ["shop-web", "shop-api"]

    [[application]]                              # split one detected application
    path = "monolith/billing"                    # (every sub-path of it you list becomes its
    name = "billing"                             #  own application; the rest is not indexed)
    [[application]]
    path = "monolith/orders"
    name = "orders"
"""

import tomllib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_FILENAME = "stc.toml"


class ConfigError(Exception):
    pass


def _clean(path: str) -> PurePosixPath:
    cleaned = PurePosixPath(path.replace("\\", "/"))
    if cleaned.is_absolute() or ".." in cleaned.parts:
        raise ConfigError(f"Application paths must be relative to the Root: {path!r}")
    return cleaned


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    path: str | None = None
    paths: list[str] = Field(default_factory=list)
    exclude: bool = False

    @model_validator(mode="after")
    def _check(self) -> "ApplicationConfig":
        if (self.path is None) == (not self.paths):
            raise ValueError("set exactly one of `path` or `paths`")
        if self.paths and not self.name:
            raise ValueError("a merged application (`paths`) needs a `name`")
        if self.paths and self.exclude:
            raise ValueError("`exclude` only applies to `path`")
        try:
            self.roots()
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def roots(self) -> list[PurePosixPath]:
        raw = self.paths if self.paths else [self.path or "."]
        return [_clean(p) for p in raw]


class ExcludeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patterns: list[str] = Field(default_factory=list)


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exclude: ExcludeConfig = Field(default_factory=ExcludeConfig)
    application: list[ApplicationConfig] = Field(default_factory=list)


def load_root_config(root: Path) -> RootConfig:
    """Read `<root>/stc.toml`; an absent file means defaults."""
    path = root / CONFIG_FILENAME
    if not path.is_file():
        return RootConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return RootConfig.model_validate(data)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError(f"Invalid {CONFIG_FILENAME}: {exc}") from exc
