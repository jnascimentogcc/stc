"""Credential values in configuration files never leave the disk (IDX-06, XCT-01).

Found on a real project: a Spring application.properties with a literal datasource password was
indexed (the generic detectors look for quoted values), embedded, sent to the catalog model and
written into a catalog. The values are now replaced wherever a file is read."""

from pathlib import Path, PurePosixPath

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import scan_root
from stc.indexer.redact import REDACTED, is_config_file, redact
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, rebuild_relations
from stc.query import QueryEngine

PASSWORD = "S3cr3tPassw0rd"
USER = "svc_shop"
PROPERTIES = f"""server.port=8080
apiprefix.v1=/api/v1
spring.datasource.url=jdbc:mysql://localhost:3306/shop?useSSL=false&user={USER}&password={PASSWORD}
spring.datasource.username={USER}
spring.datasource.password={PASSWORD}
spring.mail.password=${{MAIL_PASSWORD}}
spring.jpa.show-sql=true
spring.datasource.hikari.password=
jwt.token-validity-seconds=3600
jwt.secret = {PASSWORD}
app.api-key: "{PASSWORD}"
"""
# The generic detectors ignore values that look sequential or like a placeholder: a password such
# as this one is not found by them, which is exactly the case redaction exists for.
UNSEEN = "abcdefghij"
PLAIN_PROPERTIES = (
    "\n".join(
        line.replace(PASSWORD, UNSEEN) for line in PROPERTIES.splitlines() if "api-key" not in line
    )
    + "\n"
)


def test_credential_values_are_replaced_and_everything_else_is_kept() -> None:
    result = redact("application.properties", PROPERTIES)
    assert PASSWORD not in result.text and USER not in result.text
    lines = dict(line.split("=", 1) for line in result.text.splitlines() if "=" in line)
    assert lines["spring.datasource.password"] == REDACTED
    assert lines["spring.datasource.username"] == REDACTED
    assert lines["spring.datasource.url"] == (
        f"jdbc:mysql://localhost:3306/shop?useSSL=false&user={REDACTED}&password={REDACTED}"
    )
    # Kept: other settings, placeholders, empty values, numbers and booleans.
    assert lines["server.port"] == "8080" and lines["apiprefix.v1"] == "/api/v1"
    assert lines["spring.mail.password"] == "${MAIL_PASSWORD}"
    assert lines["spring.datasource.hikari.password"] == ""
    assert lines["jwt.token-validity-seconds"] == "3600" and lines["spring.jpa.show-sql"] == "true"
    assert lines["jwt.secret "] == f" {REDACTED}"  # spacing around `=` is kept
    assert 'app.api-key: "<redacted>"' in result.text  # quotes are kept
    assert result.values == 6


def test_a_number_is_a_credential_when_it_is_the_value_of_a_credential_key() -> None:
    text = "\n".join(
        [
            "spring.datasource.password=0123456789",
            "admin.password=12345678",
            "jwt.token-validity-seconds=3600",
            "jwt.refreshTokenTtl=900",
            "server.ssl.key-store-password-length=12",
            "feature.password-reset.enabled=true",
        ]
    )
    out = redact("application.properties", text)
    assert out.text.splitlines() == [
        "spring.datasource.password=<redacted>",
        "admin.password=<redacted>",
        "jwt.token-validity-seconds=3600",
        "jwt.refreshTokenTtl=900",
        "server.ssl.key-store-password-length=12",
        "feature.password-reset.enabled=true",
    ]
    assert out.values == 2


def test_no_line_is_added_or_removed_and_redaction_is_idempotent() -> None:
    once = redact("application.properties", PROPERTIES)
    assert once.text.count("\n") == PROPERTIES.count("\n")
    again = redact("application.properties", once.text)
    assert again.text == once.text and again.values == 0
    windows = PROPERTIES.replace("\n", "\r\n")
    assert PASSWORD not in redact("application.properties", windows).text


def test_yaml_json_xml_and_urls() -> None:
    yaml = redact(
        "application-dev.yml",
        f"spring:\n  datasource:\n    password: {PASSWORD}\n    username: {USER}\n"
        f"  credentials:\n    token: '{PASSWORD}'\nserver:\n  port: 8080\n"
        f"broker: amqp://{USER}:{PASSWORD}@rabbit:5672/vhost\n",
    )
    assert PASSWORD not in yaml.text and USER not in yaml.text
    assert "port: 8080" in yaml.text and "credentials:\n" in yaml.text
    assert "amqp://<redacted>@rabbit:5672/vhost" in yaml.text

    document = redact(
        "appsettings.json",
        f'{{\n  "Password": "{PASSWORD}",\n  "Credentials": {{\n    "ApiKey": "{PASSWORD}",\n'
        f'    "Name": "x"\n  }}\n}}\n',
    )
    assert PASSWORD not in document.text and '"Credentials": {' in document.text
    assert '"Name": "x"' in document.text and '"Password": "<redacted>",' in document.text

    xml = redact(
        "applicationContext.xml",
        f'<beans><bean><property name="password" value="{PASSWORD}"/>'
        f'<property name="url" value="jdbc:x"/></bean><password>{PASSWORD}</password></beans>',
    )
    assert PASSWORD not in xml.text and 'name="url" value="jdbc:x"' in xml.text


def test_only_configuration_files_are_touched() -> None:
    code = f'DB_PASSWORD = "{PASSWORD}"\n'
    assert redact("settings.py", code).text == code  # code: the detectors and the file rules
    assert redact("package.json", f'{{"jsonwebtoken": "{PASSWORD}"}}').values == 0
    assert redact("pom.xml", f"<password>{PASSWORD}</password>").values == 0
    for name in (
        "application.properties", "bootstrap.yaml", ".env.example", "config.ini", "app.conf",
        "appsettings.Production.json", "web.config", "terraform.tfvars",
    ):  # fmt: skip
        assert is_config_file(PurePosixPath("src") / name), name
    for name in ("main.py", "README.md", "package-lock.json", "pom.xml", "data.json"):
        assert not is_config_file(name), name


def test_templates_with_placeholders_are_left_alone() -> None:
    template = "DB_PASSWORD=\nAPI_KEY=${API_KEY}\nSECRET=<your secret here>\nDEBUG=true\n"
    result = redact(".env.example", template)
    assert result.text == template and result.values == 0


# --- through the pipeline ------------------------------------------------------------------------


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CONTROLLER = """@RestController
@RequestMapping("/noc${apiprefix.v1}")
public class C {
    @GetMapping("/list/root")
    String root() { return ""; }
}
"""


@pytest.fixture
def project(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    write(root, "be/pom.xml", "<project><artifactId>be</artifactId></project>")
    write(root, "be/src/main/java/C.java", CONTROLLER)
    write(root, "be/src/main/resources/application.properties", PLAIN_PROPERTIES)
    write(root, "fe/package.json", '{"name": "fe"}')
    write(root, "fe/src/s.js", "fetch(`${process.env.REACT_APP_BE_URL}/noc/api/v1/list/root`);")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        sync_registry(conn, scan_root(root))
        stats = chunk_pending(conn, root)
        extract_pending(conn, root)
        yield conn, root, stats, tmp_path


def test_the_values_reach_no_table_and_no_reader(project) -> None:  # type: ignore[no-untyped-def]
    conn, root, stats, tmp_path = project
    assert (stats.files_redacted, stats.values_redacted) == (1, 5)
    for table, column in (
        ("chunks", "content"), ("symbols", "name"), ("signals", "detail"), ("signals", "key"),
    ):  # fmt: skip
        for secret in (UNSEEN, USER):
            row = conn.execute(
                f"SELECT count(*) FROM {table} WHERE position(%s in {column}) > 0", (secret,)
            ).fetchone()
            assert row == (0,), (table, column)
    engine = QueryEngine(conn, root=root, catalogs=tmp_path / "c")
    assert engine.grep(UNSEEN).items == [] and engine.grep(USER).items == []
    assert engine.search_code("datasource password", applications=["be"]).items  # still there
    piece = engine.read_file("be", "src/main/resources/application.properties")
    assert UNSEEN not in piece.text and USER not in piece.text
    assert "spring.datasource.password=<redacted>" in piece.text
    assert piece.total_lines == len(PLAIN_PROPERTIES.splitlines())  # no line added or removed
    # The row says nothing was held back: the file is indexed, only its values are not.
    status = conn.execute(
        "SELECT status FROM files WHERE path LIKE '%application.properties'"
    ).fetchone()
    assert status == ("indexed",)


def test_the_configuration_still_resolves_the_api_prefix(project) -> None:  # type: ignore[no-untyped-def]
    conn, root, _stats, _ = project
    assert rebuild_relations(conn, {}, root).total == 1  # fe -> be through /noc/api/v1/...


def test_files_are_read_again_when_the_rules_change(project) -> None:  # type: ignore[no-untyped-def]
    conn, root, _stats, _ = project
    assert chunk_pending(conn, root).files_chunked == 0  # nothing to do on the next run
    old = conn.execute(
        "SELECT chunked_key, signals_key FROM files WHERE path LIKE '%C.java'"
    ).fetchone()
    assert old is not None and all(k and ":" in k for k in old)
    # A file chunked under older rules (the key carries the version of the rules) is redone.
    conn.execute("UPDATE files SET chunked_key = split_part(chunked_key, ':', 1) || ':0.0'")
    assert chunk_pending(conn, root).files_chunked >= 1
