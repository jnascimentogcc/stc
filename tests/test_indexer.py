"""Detection and hygiene tests on small synthetic trees (no real code)."""

from pathlib import Path

import pytest

from stc.indexer.detect import detect_applications
from stc.indexer.pipeline import scan_root
from stc.indexer.rootconfig import ConfigError, RootConfig, load_root_config
from stc.indexer.scan import scan_application


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def names(root: Path, config: RootConfig | None = None) -> list[str]:
    return [s.name for s in detect_applications(root, config)]


# --- detection -------------------------------------------------------------------------------


def test_detects_applications_by_markers_in_any_language(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "web/package.json": "{}",
            "api/pom.xml": "<project/>",
            "svc/Shop.Api.csproj": "<Project/>",
            "tool/pyproject.toml": "[project]",
            "go-svc/go.mod": "module x",
            "docs/readme.md": "# not an app",
        },
    )
    assert names(tmp_path) == ["api", "go-svc", "svc", "tool", "web"]


def test_application_owns_its_subtree(tmp_path: Path) -> None:
    write(tmp_path, {"app/package.json": "{}", "app/frontend/package.json": "{}"})
    assert names(tmp_path) == ["app"]


def test_workspace_roots_are_not_applications(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "mono/package.json": '{"workspaces": ["packages/*"]}',
            "mono/packages/a/package.json": "{}",
            "mono/packages/b/package.json": "{}",
            "parent/pom.xml": (
                "<project><packaging>pom</packaging>"
                "<modules><module>child</module></modules></project>"
            ),
            "parent/child/pom.xml": "<project/>",
            "rust/Cargo.toml": "[workspace]\nmembers = ['x']",
            "rust/x/Cargo.toml": "[package]\nname='x'",
        },
    )
    assert sorted(s.base.as_posix() for s in detect_applications(tmp_path)) == [
        "mono/packages/a",
        "mono/packages/b",
        "parent/child",
        "rust/x",
    ]


def test_dependencies_and_build_dirs_are_not_searched(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"app/package.json": "{}", "node_modules/dep/package.json": "{}", "x/vendor/y/go.mod": "m"},
    )
    assert names(tmp_path) == ["app"]


def test_duplicate_names_fall_back_to_relative_paths(tmp_path: Path) -> None:
    write(tmp_path, {"a/api/package.json": "{}", "b/api/package.json": "{}"})
    assert names(tmp_path) == ["a/api", "b/api"]


def test_root_itself_can_be_an_application(tmp_path: Path) -> None:
    write(tmp_path, {"package.json": "{}", "src/index.js": "x"})
    (spec,) = detect_applications(tmp_path)
    assert spec.name == tmp_path.name and spec.base.as_posix() == "."


# --- stc.toml --------------------------------------------------------------------------------


def config_for(root: Path, toml: str) -> RootConfig:
    write(root, {"stc.toml": toml})
    return load_root_config(root)


def test_config_rename_include_exclude(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "services/billing/package.json": "{}",
            "samples/demo/package.json": "{}",
            "tools/etl/script.py": "print(1)",
        },
    )
    config = config_for(
        tmp_path,
        """
[[application]]
path = "services/billing"
name = "billing-service"
[[application]]
path = "samples/demo"
exclude = true
[[application]]
path = "tools/etl"
""",
    )
    specs = {s.name: s for s in detect_applications(tmp_path, config)}
    assert set(specs) == {"billing-service", "etl"}
    assert specs["etl"].detected_by == "manual"
    assert specs["billing-service"].detected_by == "auto"


def test_config_merge_and_split(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "shop-web/package.json": "{}",
            "shop-api/pom.xml": "<project/>",
            "monolith/package.json": "{}",
            "monolith/billing/a.js": "x",
            "monolith/orders/b.js": "y",
        },
    )
    config = config_for(
        tmp_path,
        """
[[application]]
name = "shop"
paths = ["shop-web", "shop-api"]
[[application]]
path = "monolith/billing"
name = "billing"
[[application]]
path = "monolith/orders"
name = "orders"
""",
    )
    specs = {s.name: s for s in detect_applications(tmp_path, config)}
    assert set(specs) == {"shop", "billing", "orders"}
    assert specs["shop"].base.as_posix() == "." and len(specs["shop"].roots) == 2


def test_invalid_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        config_for(tmp_path, '[[application]]\npath = "../outside"\n')
    with pytest.raises(ConfigError):
        config_for(tmp_path, '[[application]]\npaths = ["a", "b"]\n')  # merge needs a name
    with pytest.raises(ConfigError):
        config_for(tmp_path, "not toml [")


# --- hygiene ---------------------------------------------------------------------------------

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # the public example key from AWS documentation


def scanned(root: Path) -> dict[str, tuple[str, str | None]]:
    (app,) = scan_root(root)
    return {f.path: (f.status, f.exclusion_reason) for f in app.files}


def test_hygiene_classifies_files(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/package.json": "{}",
            "app/src/main.py": "print('hi')\n",
            "app/README.md": "# app\n",
            "app/notes.weird": "plain text\n",
            "app/package-lock.json": "{}",
            "app/node_modules/x/index.js": "x",
            "app/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
            "app/blob": b"\x00\x01\x02binary",
            "app/dist/bundle.js": "x",
            "app/app.min.js": "x",
            "app/gen.py": "# Code generated by tool. DO NOT EDIT.\nx = 1\n",
        },
    )
    result = scanned(tmp_path)
    assert result["src/main.py"][0] == "indexed"
    assert result["README.md"][0] == "indexed"  # documentation is knowledge (IDX-09)
    assert result["notes.weird"][0] == "unsupported"
    assert result["package-lock.json"] == ("excluded", "lockfile")
    assert result["logo.png"][0] == "excluded"
    assert result["blob"] == ("excluded", "binary or asset")
    assert result["gen.py"] == ("excluded", "generated file")
    assert result["app.min.js"] == ("excluded", "generated file")
    assert "node_modules/x/index.js" not in result and "dist/bundle.js" not in result


def test_secrets_are_excluded_without_exposing_the_value(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/package.json": "{}",
            "app/.env": "DB_PASSWORD=hunter2\n",
            "app/.env.example": "DB_PASSWORD=\n",
            "app/deploy/server.pem": "-----BEGIN CERTIFICATE-----\n",
            "app/src/config.py": f'AWS_ACCESS_KEY_ID = "{AWS_KEY}"\n',
            "app/src/ok.py": "x = 1\n",
        },
    )
    (app,) = scan_root(tmp_path)
    by_path = {f.path: f for f in app.files}
    assert by_path[".env"].status == "secret"
    assert by_path["deploy/server.pem"].status == "secret"
    assert by_path["src/config.py"].status == "secret"
    assert by_path[".env.example"].status == "indexed"  # templates are documentation
    assert by_path["src/ok.py"].status == "indexed"
    for f in app.files:
        assert AWS_KEY not in (f.exclusion_reason or "")
        assert "hunter2" not in (f.exclusion_reason or "")
        if f.status == "secret":
            assert f.content_sha256 is None


def test_user_exclusion_patterns_apply(tmp_path: Path) -> None:
    config = config_for(tmp_path, '[exclude]\npatterns = ["app/legacy/", "**/*.snap"]\n')
    write(
        tmp_path,
        {
            "app/package.json": "{}",
            "app/legacy/old.py": "x",
            "app/a.snap": "x",
            "app/keep.py": "x",
        },
    )
    (spec,) = detect_applications(tmp_path, config)
    from stc.indexer.exclusions import Exclusions

    app = scan_application(tmp_path, spec, Exclusions(config.exclude.patterns))
    paths = {f.path: f.status for f in app.files}
    assert "legacy/old.py" not in paths
    assert paths["a.snap"] == "excluded"
    assert paths["keep.py"] == "indexed"


def test_scan_is_deterministic_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    write(tmp_path, {"app/package.json": "{}", "app/a.py": "x", "outside/secret.py": "y"})
    try:
        (tmp_path / "app" / "link.py").symlink_to(tmp_path / "outside" / "secret.py")
    except OSError:
        pytest.skip("symlinks not available")
    first = scan_root(tmp_path)
    assert first == scan_root(tmp_path)
    by_path = {f.path: f for f in first[0].files}
    assert by_path["link.py"].status == "excluded"
    assert by_path["link.py"].content_sha256 is None
