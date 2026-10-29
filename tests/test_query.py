"""The query engine against a real database: browsing, hybrid search, grep, symbols, reading."""

import asyncio
from pathlib import Path

import psycopg
import pytest

from stc.catalog.globalmap import write_global_map
from stc.catalog.store import generate_and_store, prepare
from stc.db.migrate import migrate
from stc.indexer.embed_store import embed_pending
from stc.query import QueryEngine, QueryError
from stc.query.search import query_terms
from test_catalog import FakeLLM, index, make_root, reply, write

VOCAB = ("invoice", "customer", "payment", "settings", "fetch", "alpha")


def vectorize(text: str) -> list[float]:
    lowered = text.lower()
    vector = [float(lowered.count(word)) for word in VOCAB]
    return [*vector, 0.01] + [0.0] * (1536 - len(VOCAB) - 1)


class KeywordEmbeddings:
    """Embeds by counting a few keywords, so semantic ranking is predictable."""

    provider = "fake"
    model = "keywords"
    dimensions = 1536

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        return [vectorize(t) for t in texts]


class KeywordQueryEmbedder:
    provider = "fake"
    model = "keywords"

    def __call__(self, text: str) -> list[float]:
        return vectorize(text)


@pytest.fixture
def env(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    write(root, "svc/billing.py", "def charge_customer(customer):\n    return 'payment ok'\n")
    write(root, "solo/package.json", '{"name": "solo"}')
    write(root, "solo/notes.js", "// alpha alpha alpha\nfunction alphaOnly() {}\n")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
        conn.execute("UPDATE applications SET indexed_version = 'v1'")
        asyncio.run(embed_pending(conn, KeywordEmbeddings()))
        yield conn, root, tmp_path / "catalogs"


def engine(env, *, semantic: bool = True) -> QueryEngine:  # type: ignore[no-untyped-def]
    conn, root, catalogs = env
    return QueryEngine(
        conn, root=root, catalogs=catalogs, embedder=KeywordQueryEmbedder() if semantic else None
    )


def make_catalog(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, catalogs = env
    llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
    prepared = prepare(
        conn, "svc", catalogs, provider="fake", model="fake-model", language="English"
    )
    asyncio.run(generate_and_store(conn, llm, prepared, language="English"))


# --- pure ----------------------------------------------------------------------------------------


def test_query_terms_split_identifiers_and_drop_noise() -> None:
    assert query_terms("where is getUserName used? a") == [
        "where", "is", "getusername", "get", "user", "name", "used",
    ]  # fmt: skip
    assert query_terms("charge_customer") == ["charge_customer", "charge", "customer"]


# --- browsing ------------------------------------------------------------------------------------


def test_list_applications_filters(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    everything = eng.list_applications()
    assert [a.name for a in everything.items] == ["solo", "svc", "web"]
    assert everything.items[1].files >= 4 and everything.items[1].languages["python"] >= 1
    assert [a.name for a in eng.list_applications(name="VC").items] == ["svc"]
    assert [a.name for a in eng.list_applications(technology="python").items] == ["svc"]
    related = eng.list_applications(related_to="web")
    assert [a.name for a in related.items] == ["svc"]
    empty = eng.list_applications(name="nothing")
    assert empty.items == [] and empty.notes
    with pytest.raises(QueryError) as caught:
        eng.list_applications(related_to="ghost")
    assert caught.value.code == "unknown_application"


def test_pagination_has_more_and_caps_the_limit(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    first = eng.list_applications(limit=2)
    second = eng.list_applications(limit=2, offset=2)
    assert [a.name for a in first.items] == ["solo", "svc"] and first.has_more
    assert [a.name for a in second.items] == ["web"] and not second.has_more
    assert eng.list_applications(limit=10_000).limit == 50
    with pytest.raises(QueryError):
        eng.list_applications(limit=0)


def test_catalog_by_section_and_missing_catalog(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    with pytest.raises(QueryError) as caught:
        eng.get_catalog("svc")
    assert caught.value.code == "not_found"
    make_catalog(env)
    view = eng.get_catalog("svc", ["purpose", "integrations"])
    assert set(view.sections) == {"purpose", "integrations"}
    assert "It issues invoices." in view.sections["purpose"]
    assert "stc:manual" not in view.sections["purpose"]
    assert view.status == "complete" and not view.outdated and view.code_version
    assert len(eng.get_catalog("svc").sections) == 9
    with pytest.raises(QueryError) as bad:
        eng.get_catalog("svc", ["nope"])
    assert bad.value.code == "invalid_argument"


def test_catalog_is_flagged_outdated_when_the_code_version_moves(env) -> None:  # type: ignore[no-untyped-def]
    make_catalog(env)
    env[0].execute("UPDATE applications SET indexed_version = 'newer' WHERE name = 'svc'")
    assert engine(env).get_catalog("svc").outdated


def test_global_map_from_file_or_built_on_the_fly(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    built = eng.get_global_map()
    assert not built.from_file and "## Relationships" in built.text
    write_global_map(env[0], env[2])
    assert eng.get_global_map().from_file


def test_dependencies_are_directional_and_carry_evidence(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    down = eng.get_dependencies("web", direction="depends_on")
    assert [(d.application, d.depth, d.type) for d in down] == [("svc", 1, "http")]
    assert down[0].evidence is not None and down[0].evidence.file == "client.js"
    up = eng.get_dependencies("svc", direction="used_by")
    assert [d.application for d in up] == ["web"] and up[0].via == ["svc"]
    assert eng.get_dependencies("web", direction="used_by") == []
    assert eng.get_dependencies("solo") == []
    with pytest.raises(QueryError):
        eng.get_dependencies("web", direction="sideways")
    with pytest.raises(QueryError):
        eng.get_dependencies("web", max_depth=99)


def test_transitive_dependencies_stop_at_cycles(env) -> None:  # type: ignore[no-untyped-def]
    conn = env[0]
    ids = {n: i for i, n in conn.execute("SELECT id, name FROM applications")}
    conn.execute(  # solo -> web -> svc already; svc -> solo closes a cycle
        "INSERT INTO relations (source_app_id, target_app_id, type, confidence, detail)"
        " VALUES (%s, %s, 'manual', 'high', 'x'), (%s, %s, 'manual', 'high', 'y')",
        (ids["solo"], ids["web"], ids["svc"], ids["solo"]),
    )
    deps = engine(env).get_dependencies("solo", direction="depends_on", max_depth=6)
    reached = [(d.application, d.depth) for d in deps]
    assert ("web", 1) in reached and ("svc", 2) in reached
    assert all(name != "solo" for name, _ in reached)


def test_status_reports_files_chunks_and_embeddings(env) -> None:  # type: ignore[no-untyped-def]
    status = engine(env).status("svc")
    (svc,) = status.applications
    assert svc.files["secret"] == 1 and svc.chunks > 0 and svc.embedded_chunks == svc.chunks
    assert svc.catalog_status is None and svc.relations == 1
    with pytest.raises(QueryError):
        engine(env).status("ghost")


# --- search --------------------------------------------------------------------------------------


def test_hybrid_search_fuses_semantic_lexical_and_name(env) -> None:  # type: ignore[no-untyped-def]
    page = engine(env).search_code("charge customer payment")
    top = page.items[0]
    assert top.source.application == "svc" and top.source.file == "billing.py"
    assert {"semantic", "lexical"} <= set(top.matched_by)
    assert top.source.code_version and top.source.start_line == 1
    by_name = engine(env).search_code("charge_customer")
    assert "name" in by_name.items[0].matched_by


def test_search_without_embedder_is_lexical_and_says_so(env) -> None:  # type: ignore[no-untyped-def]
    page = engine(env, semantic=False).search_code("customer")
    assert page.items and all("semantic" not in h.matched_by for h in page.items)
    assert any("lexical only" in n for n in page.notes)


def test_search_scope_filters(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    assert eng.search_code("alpha").items[0].source.application == "solo"
    only_svc = eng.search_code("customer payment", applications=["svc"])
    assert {h.source.application for h in only_svc.items} == {"svc"}
    js = eng.search_code("alpha", languages=["javascript"])
    assert js.items and all(h.source.file.endswith(".js") for h in js.items)
    by_path = eng.search_code("customer", paths=["billing*"])
    assert {h.source.file for h in by_path.items} == {"billing.py"}


def test_search_with_no_match_admits_it(env) -> None:  # type: ignore[no-untyped-def]
    page = engine(env, semantic=False).search_code("zzzunknownzzz")
    assert page.items == [] and any("No matches" in n for n in page.notes)
    with pytest.raises(QueryError):
        engine(env).search_code("   ")


def test_secrets_are_never_searchable(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    assert all(h.source.file != "credentials.json" for h in eng.search_code("token").items)
    assert eng.grep("token").items == []
    assert eng.find_symbol("token").items == []


def test_search_catalogs_is_separate_from_code(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    assert eng.search_catalogs("invoices").items == []
    make_catalog(env)
    hits = eng.search_catalogs("invoices")
    assert ("svc", "purpose") in [(h.application, h.section) for h in hits.items]
    assert eng.search_catalogs("invoices", applications=["web"]).items == []


# --- grep and symbols ----------------------------------------------------------------------------


def test_grep_literal_regex_case_and_lines(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    literal = eng.grep("payment ok")
    assert [(h.source.application, h.source.file, h.source.start_line) for h in literal.items] == [
        ("svc", "billing.py", 2)
    ]
    assert "payment ok" in literal.items[0].text
    assert eng.grep("PAYMENT").items == []
    assert len(eng.grep("PAYMENT", case_sensitive=False).items) == 1
    assert [h.source.start_line for h in eng.grep(r"def \w+_customer", regex=True).items] == [1]
    assert eng.grep("100%_literal").items == []  # LIKE wildcards are escaped
    with pytest.raises(QueryError) as bad:
        eng.grep("(", regex=True)
    assert bad.value.code == "invalid_argument"


def test_grep_paginates_over_lines(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    first = eng.grep("alpha", case_sensitive=False, limit=1)
    assert len(first.items) == 1 and first.has_more
    rest = eng.grep("alpha", case_sensitive=False, limit=1, offset=1)
    seen = {(h.source.file, h.source.start_line) for h in first.items + rest.items}
    assert len(seen) == len(first.items) + len(rest.items)


def test_find_symbol_definitions_and_fuzzy(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    hits = eng.find_symbol("charge_customer")
    assert [(h.source.file, h.source.start_line, h.is_definition) for h in hits.items] == [
        ("billing.py", 1, True)
    ]
    assert eng.find_symbol("charge").items == []
    assert [h.name for h in eng.find_symbol("charge", exact=False).items] == ["charge_customer"]
    assert eng.find_symbol("charge_customer", kind="class").items == []
    assert eng.find_symbol("nothing_here").notes


# --- reading files -------------------------------------------------------------------------------


def test_read_file_range_truncation_and_staleness(env) -> None:  # type: ignore[no-untyped-def]
    conn, root, _ = env
    eng = engine(env)
    one = eng.read_file("svc", "billing.py", start_line=2, end_line=2)
    assert one.text == "    return 'payment ok'" and one.source.start_line == 2
    assert one.total_lines == 2 and not one.truncated and not one.stale
    write(root, "svc/long.txt", "\n".join(f"line {i}" for i in range(1, 1001)))
    index(conn, root)
    big = eng.read_file("svc", "long.txt")
    assert big.truncated and big.next_start_line == 401 and big.text.count("\n") == 399
    (root / "svc" / "billing.py").write_text("changed\n", encoding="utf-8")
    assert eng.read_file("svc", "billing.py").stale


def test_read_file_refuses_secrets_traversal_and_unknown_files(env) -> None:  # type: ignore[no-untyped-def]
    eng = engine(env)
    cases = [
        (("svc", "credentials.json"), "forbidden"),
        (("svc", "../web/client.js"), "invalid_argument"),
        (("svc", "/etc/passwd"), "invalid_argument"),
        (("svc", "missing.py"), "not_found"),
        (("ghost", "x.py"), "unknown_application"),
    ]
    for args, code in cases:
        with pytest.raises(QueryError) as caught:
            eng.read_file(*args)
        assert caught.value.code == code, args
    with pytest.raises(QueryError) as bad_range:
        eng.read_file("svc", "billing.py", start_line=99)
    assert bad_range.value.code == "invalid_argument"
    conn, _root, catalogs = env
    no_root = QueryEngine(conn, root=None, catalogs=catalogs)
    with pytest.raises(QueryError) as unavailable:
        no_root.read_file("svc", "billing.py")
    assert unavailable.value.code == "unavailable"
    missing_root = QueryEngine(conn, root=Path("/does/not/exist"), catalogs=catalogs)
    with pytest.raises(QueryError, match="STC_ROOT") as wrong:
        missing_root.read_file("svc", "billing.py")
    assert wrong.value.code == "unavailable"
