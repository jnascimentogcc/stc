"""COBOL, copybook and JCL files and applications are recognized (synthetic trees only)."""

from pathlib import Path, PurePosixPath

import pytest

from stc.indexer.detect import detect_applications
from stc.indexer.languages import language_of
from stc.indexer.rootconfig import load_root_config


def write(root: Path, *files: str) -> Path:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("       IDENTIFICATION DIVISION.\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("name", "language"),
    [
        ("A.cbl", "cobol"),
        ("A.COB", "cobol"),
        ("A.cobol", "cobol"),
        ("C.cpy", "cobol-copybook"),
        ("C.copy", "cobol-copybook"),
        ("J.jcl", "jcl"),
        ("P.proc", "jcl"),
        ("P.prc", "jcl"),
    ],
)
def test_cobol_and_jcl_files_have_languages(name: str, language: str) -> None:
    assert language_of(PurePosixPath("src") / name) == language


def test_first_level_folder_with_cobol_is_an_application(tmp_path: Path) -> None:
    write(tmp_path, "loans/src/A.cbl", "loans/jcl/J.jcl", "loans/copybooks/C.cpy", "docs/notes.md")
    (spec,) = detect_applications(tmp_path)
    assert spec.name == "loans" and spec.detected_by == "auto"
    assert spec.base == PurePosixPath("loans")
    assert "*.cbl" in spec.markers and "*.jcl" in spec.markers


def test_cobol_files_at_the_root_make_no_application(tmp_path: Path) -> None:
    write(tmp_path, "A.cbl")
    assert detect_applications(tmp_path) == []


def test_folder_with_a_project_marker_inside_is_not_a_cobol_application(tmp_path: Path) -> None:
    write(tmp_path, "mixed/svc/pom.xml", "mixed/legacy/A.cbl")
    write(tmp_path, "plain/pom.xml", "plain/src/A.cbl")
    found = {s.base.as_posix(): s.markers for s in detect_applications(tmp_path)}
    assert found == {"mixed/svc": ("pom.xml",), "plain": ("pom.xml",)}


def test_dependency_directories_do_not_count(tmp_path: Path) -> None:
    write(tmp_path, "x/node_modules/A.cbl")
    assert detect_applications(tmp_path) == []


def test_stc_toml_can_rename_a_cobol_application(tmp_path: Path) -> None:
    write(tmp_path, "loans/src/A.cbl")
    (tmp_path / "stc.toml").write_text(
        '[[application]]\npath = "loans"\nname = "loans-core"\n', encoding="utf-8"
    )
    specs = detect_applications(tmp_path, load_root_config(tmp_path))
    assert [s.name for s in specs] == ["loans-core"]
