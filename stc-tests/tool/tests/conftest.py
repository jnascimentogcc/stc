import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"


@pytest.fixture
def specs(tmp_path: Path) -> Path:
    """A specs folder with the valid example of the contract as application `cobol-bank`."""
    root = tmp_path / "specs" / "cobol-bank"
    (root / "features").mkdir(parents=True)
    shutil.copy(FIXTURES / "valid" / "_specs.json", root / "_specs.json")
    shutil.copy(
        FIXTURES / "valid" / "search-example.spec.json",
        root / "features" / "program-search-example.spec.json",
    )
    return tmp_path / "specs"
