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
