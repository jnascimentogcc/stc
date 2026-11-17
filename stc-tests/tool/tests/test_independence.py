"""STC and the test tool share only the contract: neither imports the other."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _imports(folder: Path) -> set[str]:
    names: set[str] = set()
    for path in folder.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def test_the_tool_does_not_import_stc() -> None:
    tool = ROOT / "stc-tests" / "tool"
    assert "stc" not in _imports(tool / "src") | _imports(tool / "tests")


def test_stc_does_not_import_the_tool() -> None:
    assert "stc_tests" not in _imports(ROOT / "src") | _imports(ROOT / "tests")
