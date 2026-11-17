"""Where the specifications are read from and where the work is written (`--specs`, `--output`)."""

import os
from pathlib import Path

DEFAULT_SPECS = "./specs"
DEFAULT_OUTPUT = "./testsuites"


def specs_dir(option: Path | None) -> Path:
    """`--specs`, else `STC_SPECS`, else `./specs`: the folder `stc spec` writes."""
    return option or Path(os.environ.get("STC_SPECS") or DEFAULT_SPECS)


def output_dir(option: Path | None) -> Path:
    """`--output`, else `STC_TESTS`, else `./testsuites`."""
    return option or Path(os.environ.get("STC_TESTS") or DEFAULT_OUTPUT)


def catalog_path() -> Path | None:
    """`STC_TESTS_CATALOG`: the `catalog/actions.json` of the test repository, if set."""
    value = os.environ.get("STC_TESTS_CATALOG")
    return Path(value) if value else None


def load_env_file(path: Path) -> list[str]:
    """Put the KEY=VALUE lines of `path` in the environment, without replacing variables that are
    already set. Returns the names it set; values are never printed or returned."""
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            names.append(key)
    return names
