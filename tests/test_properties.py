"""Spring `${property}` paths, tests and configuration-variable hosts: what a real project showed.

A Java back end exposes `/noc${apiprefix.v1}/list/root` (the prefix lives in application.yml), a
React front end calls `process.env.REACT_APP_BE_URL/noc/api/v1/list/root`, and the project's own
tests call the API they test. None of that linked the two applications before."""

from pathlib import Path, PurePosixPath

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import scan_root
from stc.indexer.properties import (
    has_placeholder,
    is_properties_file,
    load_properties,
    parse_properties,
    parse_yaml,
    resolve_placeholders,
)
from stc.indexer.registry import sync_registry
from stc.indexer.relations import AppRef, SignalRow, derive_relations
from stc.indexer.signal_store import extract_pending, list_relations, rebuild_relations
from stc.indexer.signals import extract_signals, is_test_path, normalize_path

CONTROLLER = """package app;

@RestController
@RequestMapping("/noc${apiprefix.v1}")
public class OccupationController {
    @GetMapping("/list/root")
    public String root() { return ""; }

    @GetMapping("/list/{id}")
    public String one() { return ""; }
}
"""
CLIENT = """const base = process.env.REACT_APP_BE_URL;
export const root = () => fetch(`${base}/noc/api/v1/list/root`);
export const one = () => fetch(`${process.env.REACT_APP_BE_URL}/noc/api/v1/list/7`);
"""


def kinds(path: str, text: str, language: str) -> list[tuple[str, str, str]]:
    return [(s.role, s.key, s.detail) for s in extract_signals(PurePosixPath(path), text, language)]


# --- signals -------------------------------------------------------------------------------------


def test_a_spring_property_stays_in_the_endpoint_key_until_it_can_be_resolved() -> None:
    found = kinds("src/main/java/OccupationController.java", CONTROLLER, "java")
    provides = {key for role, key, _ in found if role == "provides"}
    assert provides == {
        "/noc${apiprefix.v1}/list/root",
        "/noc${apiprefix.v1}/list/{}",
    }
    assert normalize_path("/a/${Prefix.V1}/{id}", keep_properties=True) == "/a/${prefix.v1}/{}"
    assert normalize_path("/a/${Prefix.V1}/{id}") == "/a/{}/{}"  # other languages: a parameter


def test_a_property_in_a_mapping_annotation_is_not_a_client_call() -> None:
    found = kinds("src/main/java/OccupationController.java", CONTROLLER, "java")
    assert [role for role, _, _ in found if role == "consumes"] == []
    # ...but the same shape in a real client call still is one.
    client = 'String url = "${base.url}/orders/list"; restTemplate.getForObject(url, X.class);'
    assert any(
        role == "consumes" for role, _, _ in kinds("src/main/java/Client.java", client, "java")
    )


@pytest.mark.parametrize(
    "path",
    [
        "tests/api.test.ts", "src/__tests__/a.js", "src/test/java/com/x/OrderTest.java",
        "src/service.spec.ts", "pkg/test_orders.py", "pkg/orders_test.go", "e2e/login.js",
        "src/main/java/com/x/OrderIT.java", "cypress/support/commands.js",
    ],
)  # fmt: skip
def test_tests_give_no_signals(path: str) -> None:
    assert is_test_path(PurePosixPath(path))
    call = "fetch('http://billing/invoices/1'); app.get('/health', handler);"
    assert extract_signals(PurePosixPath(path), call, "javascript") == []


@pytest.mark.parametrize(
    "path",
    ["src/service.ts", "src/main/java/com/x/Contest.java", "src/latest.js", "lib/testing_utils.py"],
)
def test_ordinary_files_are_not_mistaken_for_tests(path: str) -> None:
    assert not is_test_path(PurePosixPath(path))


# --- properties ----------------------------------------------------------------------------------


def test_properties_files_in_both_formats() -> None:
    assert parse_properties("# c\napiprefix.v1=/api/v1\nserver.port: 8080\nname value\n") == {
        "apiprefix.v1": "/api/v1",
        "server.port": "8080",
        "name": "value",
    }
    assert parse_yaml("apiprefix:\n  v1: /api/v1\nserver:\n  port: 8080\n---\nother: x\n") == {
        "apiprefix.v1": "/api/v1",
        "server.port": "8080",
        "other": "x",
    }
    assert parse_yaml("a: [unclosed") == {}


def test_which_files_are_configuration() -> None:
    for name in (
        "application.properties",
        "application-dev.yml",
        "bootstrap.yaml",
        "application_x.yml",
    ):
        assert is_properties_file(f"src/main/resources/{name}"), name
    for name in ("pom.xml", "applications.yml", "docker-compose.yml", "myapplication.yml"):
        assert not is_properties_file(name), name


def test_placeholders_are_resolved_from_properties_or_become_parameters() -> None:
    props = {"apiprefix.v1": "/api/v1", "weird": "http://host/with space"}
    assert resolve_placeholders("/noc${apiprefix.v1}/list/root", props) == "/noc/api/v1/list/root"
    assert resolve_placeholders("/x${missing}/y", props) == "/x{}/y"
    assert resolve_placeholders("/x${missing:/dflt}/y", props) == "/x/dflt/y"  # the default
    assert resolve_placeholders("/x${weird}/y", props) == "/x{}/y"  # not a path: not used
    assert resolve_placeholders("/${a}/${a}", {"a": "/v1"}) == "/v1/v1"
    assert has_placeholder("/a${b}") and not has_placeholder("/a/{}")


# --- relations -----------------------------------------------------------------------------------


def row(app: int, key: str, host: str | None, detail: str, role: str = "consumes") -> SignalRow:
    return SignalRow(app * 1000 + len(key), app, app, "http", role, key, host, detail, 1, 1)


def test_a_configuration_variable_host_falls_back_to_the_path() -> None:
    apps = [AppRef(1, "web"), AppRef(2, "api")]
    served = row(2, "/noc/api/v1/list/root", None, "GET /noc/api/v1/list/root", "provides")
    call = row(
        1,
        "/noc/api/v1/list/root",
        "react-app-be",
        "process.env.REACT_APP_BE_URL/noc/api/v1/list/root",
    )
    (draft,) = derive_relations(apps, [served, call])
    assert (draft.source_app_id, draft.target_app_id, draft.confidence) == (1, 2, "medium")


def test_a_real_url_to_an_unknown_host_is_still_external() -> None:
    apps = [AppRef(1, "web"), AppRef(2, "api")]
    served = row(2, "/v1/charges/list", None, "GET /v1/charges/list", "provides")
    external = row(
        1, "/v1/charges/list", "api.stripe.com", "https://api.stripe.com/v1/charges/list"
    )
    assert derive_relations(apps, [served, external]) == []
    # A variable that is only a path with no specific path is not matched either.
    generic = row(1, "/health", "react-app-be", "process.env.REACT_APP_BE_URL/health")
    assert derive_relations(apps, [generic]) == []


# --- end to end ----------------------------------------------------------------------------------


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    write(root, "be/pom.xml", "<project><artifactId>be</artifactId></project>")
    write(root, "be/src/main/java/OccupationController.java", CONTROLLER)
    write(root, "be/src/main/resources/application.yml", "apiprefix:\n  v1: /api/v1\n")
    write(root, "be/src/test/java/OccupationControllerTest.java", CONTROLLER)
    write(root, "fe/package.json", '{"name": "fe"}')
    write(root, "fe/src/service.js", CLIENT)
    write(root, "fe/src/service.test.js", "fetch('http://be/noc/api/v1/list/root');")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        sync_registry(conn, scan_root(root))
        chunk_pending(conn, root)
        extract_pending(conn, root)
        yield conn, root


def test_a_front_end_is_linked_to_the_back_end_through_the_configured_prefix(project) -> None:  # type: ignore[no-untyped-def]
    conn, root = project
    assert load_properties(conn, root) == {
        int(conn.execute("SELECT id FROM applications WHERE name = 'be'").fetchone()[0]): {
            "apiprefix.v1": "/api/v1"
        }
    }
    without = rebuild_relations(conn, {})  # no Root: the placeholder cannot be resolved
    assert without.total == 0
    stats = rebuild_relations(conn, {}, root)
    assert stats.total == 1
    ((source, target, kind, confidence, _detail, evidence, count),) = list_relations(conn)
    assert (source, target, kind, confidence) == ("fe", "be", "http", "medium")
    assert evidence.startswith("fe/src/service.js:") and count == 2
    # The test files of both applications added nothing.
    tests = conn.execute(
        "SELECT count(*) FROM signals s JOIN files f ON f.id = s.file_id WHERE f.path LIKE '%est%'"
    ).fetchone()
    assert tests == (0,)


def test_a_properties_file_held_back_as_a_secret_is_never_read(project) -> None:  # type: ignore[no-untyped-def]
    conn, root = project
    conn.execute("UPDATE files SET status = 'secret' WHERE path LIKE '%application.yml'")
    assert load_properties(conn, root) == {}
    assert rebuild_relations(conn, {}, root).total == 0
