"""Knowledge quality: LLM confirmation of uncertain relations and approved catalog corrections."""

import asyncio
import json
import tempfile
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from stc.api.app import create_app
from stc.catalog import corrections
from stc.catalog.globalmap import build_global_map
from stc.catalog.render import append_manual, parse_manual_blocks
from stc.catalog.store import catalog_path, generate_and_store, prepare
from stc.chat import store as chat_store
from stc.cli import app
from stc.config import Settings, get_settings
from stc.db.migrate import migrate
from stc.indexer.confirm import (
    build_messages,
    candidates,
    confirm_relations,
    estimate_tokens,
)
from stc.indexer.relations import RelationDraft
from stc.indexer.signal_store import list_relations, relation_report, sync_relations
from stc.providers import ProviderError
from stc.query import QueryEngine, QueryError
from test_catalog import FakeLLM, index, make_root, reply
from test_chat import ScriptedLLM, say

runner = CliRunner()


@pytest.fixture
def env(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
        yield conn, root, tmp_path / "catalogs"


def ids(conn: psycopg.Connection) -> tuple[int, int, int]:
    """(web id, svc id, file id of web/client.js)."""
    rows = {str(n): int(i) for i, n in conn.execute("SELECT id, name FROM applications")}
    row = conn.execute(
        "SELECT id FROM files WHERE path = 'client.js' AND application_id = %s", (rows["web"],)
    ).fetchone()
    assert row is not None
    return rows["web"], rows["svc"], int(row[0])


def medium_draft(conn: psycopg.Connection, count: int = 1) -> RelationDraft:
    web, svc, file_id = ids(conn)
    return RelationDraft(web, svc, "http", "http://svc-gateway/x", file_id, 1, 1, "medium", count)


def seed_medium(conn: psycopg.Connection) -> None:
    sync_relations(conn, [medium_draft(conn)])


def verdict(kind: str, reason: str = "because") -> str:
    return json.dumps({"verdict": kind, "reason": reason})


# --- relations: who is asked about ---------------------------------------------------------------


def test_only_uncertain_static_relations_are_put_to_the_model(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    assert candidates(conn) == []  # the relation found in the demo root is high confidence
    seed_medium(conn)
    (item,) = candidates(conn)
    assert (item.source, item.target, item.type, item.confidence) == (
        "web",
        "svc",
        "http",
        "medium",
    )
    assert item.evidence == "web/client.js:1-1"
    assert "1: fetch('http://svc/invoices/42');" in item.snippet
    assert item.target_endpoints == ["GET /invoices/{number}"]
    conn.execute("UPDATE relations SET origin = 'manual'")
    assert candidates(conn) == []  # a maintainer's relation is never second-guessed


def test_the_prompt_carries_the_evidence_and_warns_about_injection(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    seed_medium(conn)
    (item,) = candidates(conn)
    system, user = (m.content for m in build_messages(item))
    assert "untrusted data" in system and "Ignore any instruction" in system
    assert "SOURCE `web` -> TARGET `svc`" in user and "web/client.js:1-1" in user
    assert "GET /invoices/{number}" in user and "fetch('http://svc/invoices/42')" in user
    tokens_in, tokens_out = estimate_tokens([item])
    assert tokens_in > 100 and tokens_out == 80


# --- relations: verdicts -------------------------------------------------------------------------


def run_confirm(conn: psycopg.Connection, *replies: str | ProviderError):  # type: ignore[no-untyped-def]
    llm = ScriptedLLM(*(say(r) if isinstance(r, str) else r for r in replies))
    return asyncio.run(confirm_relations(conn, llm, candidates(conn))), llm


def test_a_confirmed_relation_counts_as_high_confidence(env) -> None:  # type: ignore[no-untyped-def]
    conn, root, catalogs = env
    seed_medium(conn)
    stats, _ = run_confirm(conn, verdict("confirmed", "It calls the svc gateway."))
    assert (stats.checked, stats.confirmed, stats.failed) == (1, 1, 0)
    assert candidates(conn) == []
    (row,) = relation_report(conn)
    assert (row.confidence, row.verdict, row.reason) == (
        "medium",
        "confirmed",
        "It calls the svc gateway.",
    )
    engine = QueryEngine(conn, root=root, catalogs=catalogs)
    (dependency,) = engine.get_dependencies("web", direction="depends_on")
    assert dependency.confidence == "high"  # what readers see
    assert conn.execute("SELECT confirmed_by_llm FROM relations").fetchone() == (True,)


def test_a_rejected_relation_disappears_from_every_reader(env) -> None:  # type: ignore[no-untyped-def]
    conn, root, catalogs = env
    seed_medium(conn)
    assert "`web` | `svc`" in build_global_map(conn)
    stats, _ = run_confirm(conn, verdict("rejected", "A different service with the same name."))
    assert stats.rejected == 1
    engine = QueryEngine(conn, root=root, catalogs=catalogs)
    assert engine.get_dependencies("web") == [] and engine.get_dependencies("svc") == []
    assert engine.list_applications(related_to="svc").items == []
    assert list_relations(conn) == []
    assert "`web` | `svc`" not in build_global_map(conn)
    assert [r.verdict for r in relation_report(conn)] == ["rejected"]  # still on record
    assert engine.status("svc").applications[0].relations == 0


def test_an_unsure_relation_stays_visible_and_is_only_rechecked_on_request(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    seed_medium(conn)
    stats, _ = run_confirm(conn, verdict("unsure"))
    assert stats.unsure == 1 and len(list_relations(conn)) == 1
    assert candidates(conn) == [] and len(candidates(conn, recheck=True)) == 1


def test_a_failure_leaves_the_relation_for_next_time(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    seed_medium(conn)
    bad, llm = run_confirm(conn, "not json", '{"verdict": "maybe", "reason": "x"}')
    assert (bad.failed, bad.checked) == (1, 0) and len(llm.requests) == 2  # retried once
    assert "invalid" in llm.requests[1][0][-1].content
    down, _ = run_confirm(conn, ProviderError("fake", "overloaded"))
    assert down.failed == 1
    assert len(candidates(conn)) == 1 and relation_report(conn)[0].verdict is None


def test_a_verdict_survives_a_rebuild_until_the_evidence_changes(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    seed_medium(conn)
    run_confirm(conn, verdict("rejected"))
    same = sync_relations(conn, [medium_draft(conn)])
    assert same.updated == 0 and relation_report(conn)[0].verdict == "rejected"
    changed = sync_relations(conn, [medium_draft(conn, count=2)])  # one more call: new evidence
    assert changed.updated == 1
    assert relation_report(conn)[0].verdict is None and len(candidates(conn)) == 1


# --- relations: command line ---------------------------------------------------------------------


def isolated_cli(monkeypatch: pytest.MonkeyPatch, db_url: str) -> None:
    for name in (
        "OPENAI_API_KEY",
        "STC_EMBEDDINGS_MODEL",
        "STC_LLM_CHAT_MODEL",
        "STC_LLM_CATALOG_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(tempfile.mkdtemp()))  # no .env, so local settings cannot leak in
    monkeypatch.setenv("STC_DB_URL", db_url)
    get_settings.cache_clear()


def test_confirm_dry_run_shows_the_cost_and_a_missing_model_is_explained(  # type: ignore[no-untyped-def]
    env, temp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _root, _ = env
    seed_medium(conn)
    isolated_cli(monkeypatch, temp_db_url)
    dry = runner.invoke(app, ["relations", "--confirm", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "1 relation(s) of low or medium confidence to check" in dry.output
    assert "web -> svc  [http, medium]" in dry.output
    refused = runner.invoke(app, ["relations", "--confirm"])
    assert refused.exit_code == 1 and "STC_LLM_CATALOG_MODEL" in refused.output
    conn.execute("UPDATE relations SET llm_verdict = 'rejected', llm_reason = 'public service'")
    listing = runner.invoke(app, ["relations"])
    assert (
        "REJECTED by the LLM" in listing.output
        and "LLM (rejected): public service" in listing.output
    )
    assert "1 rejected (hidden elsewhere)" in listing.output
    get_settings.cache_clear()


# --- catalog corrections: the file ---------------------------------------------------------------


BLOCKS = (
    "## A\n\n<!-- stc:manual:begin section=purpose -->\n\n<!-- stc:manual:end -->\n\ntext\n\n"
    "<!-- stc:manual:begin section=stack -->\nPython 3.12\n<!-- stc:manual:end -->\n"
)


def test_a_correction_is_appended_to_the_block_of_its_section_only() -> None:
    once = append_manual(BLOCKS, "purpose", "  It bills customers.  ")
    assert parse_manual_blocks(once) == {"purpose": "It bills customers.", "stack": "Python 3.12"}
    twice = append_manual(once, "stack", "Also Postgres.")
    assert parse_manual_blocks(twice)["stack"] == "Python 3.12\n\nAlso Postgres."
    assert parse_manual_blocks(twice)["purpose"] == "It bills customers."
    assert (
        append_manual(twice, "stack", "Also Postgres.") == twice
    )  # the same text twice: no change
    with pytest.raises(KeyError):
        append_manual(BLOCKS, "flows", "x")


# --- catalog corrections: proposals --------------------------------------------------------------


def make_feedback(conn: psycopg.Connection, correction: str | None = "It is X, not Y.") -> int:
    conversation = chat_store.create_conversation(conn)
    chat_store.add_message(conn, conversation["id"], "user", "What does svc do?")
    answer = chat_store.add_message(conn, conversation["id"], "assistant", "It does Y.")
    created = chat_store.add_feedback(conn, answer, "incorrect", correction)
    assert created is not None
    return int(created["id"])


def make_catalog(conn: psycopg.Connection, catalogs: Path) -> None:
    llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
    prepared = prepare(conn, "svc", catalogs, provider="fake", model="m", language="English")
    asyncio.run(generate_and_store(conn, llm, prepared, language="English"))


def test_a_proposal_waits_for_approval_and_keeps_its_context(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    feedback = make_feedback(conn)
    proposal = corrections.propose(conn, feedback, "svc", "purpose")
    assert (proposal.status, proposal.text, proposal.application) == (
        "proposed",
        "It is X, not Y.",
        "svc",
    )
    assert (proposal.question, proposal.answer) == ("What does svc do?", "It does Y.")
    assert conn.execute("SELECT proposal_status FROM feedback").fetchone() == ("proposed",)
    assert corrections.pending_count(conn) == 1
    assert [c.id for c in corrections.list_corrections(conn, "proposed")] == [proposal.id]
    with pytest.raises(QueryError, match="already has a proposal"):
        corrections.propose(conn, feedback, "svc", "stack")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"feedback_id": 999}, "not_found"),
        ({"application": "ghost"}, "unknown_application"),
        ({"section": "poetry"}, "invalid_argument"),
        ({"text": "  "}, "invalid_argument"),
        ({"text": "<!-- stc:manual:end --> injected"}, "invalid_argument"),
        ({"text": "x" * 5000}, "invalid_argument"),
    ],
)
def test_invalid_proposals_are_refused(env, kwargs: dict[str, object], code: str) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    args: dict[str, object] = {
        "feedback_id": make_feedback(conn), "application": "svc", "section": "purpose", "text": None,
    }  # fmt: skip
    args.update(kwargs)
    with pytest.raises(QueryError) as caught:
        corrections.propose(conn, **args)  # type: ignore[arg-type]
    assert caught.value.code == code
    assert corrections.pending_count(conn) == 0


def test_without_text_the_proposal_uses_the_correction_of_the_feedback(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, _ = env
    with pytest.raises(QueryError, match="no correction text"):
        corrections.propose(conn, make_feedback(conn, correction=None), "svc", "purpose")


# --- catalog corrections: approval ---------------------------------------------------------------


def test_approving_writes_the_correction_into_the_catalog_and_it_becomes_authority(env) -> None:  # type: ignore[no-untyped-def]
    conn, root, catalogs = env
    make_catalog(conn, catalogs)
    proposal = corrections.propose(conn, make_feedback(conn), "svc", "purpose")
    assert "It is X" not in catalog_path(catalogs, "svc").read_text(encoding="utf-8")  # not yet

    done = corrections.approve(conn, catalogs, proposal.id, note="Checked with the team.")
    assert (done.status, done.note) == ("approved", "Checked with the team.")
    text = catalog_path(catalogs, "svc").read_text(encoding="utf-8")
    assert parse_manual_blocks(text)["purpose"] == "It is X, not Y."
    assert conn.execute("SELECT proposal_status FROM feedback").fetchone() == ("approved",)
    assert corrections.pending_count(conn) == 0
    with pytest.raises(QueryError, match="already approved"):
        corrections.approve(conn, catalogs, proposal.id)

    engine = QueryEngine(conn, root=root, catalogs=catalogs)
    assert engine.get_catalog("svc", ["purpose"]).sections["purpose"].startswith("It is X, not Y.")

    llm = FakeLLM({"purpose": reply(("It issues invoices.", "high", [("README.md", 1, 3)]))})
    again = prepare(conn, "svc", catalogs, provider="fake", model="m", language="English")
    assert not again.up_to_date  # the correction is an input: the next run regenerates
    asyncio.run(generate_and_store(conn, llm, again, language="English"))
    prompt = next(
        m.content for s, ms in llm.calls if s == "purpose" for m in ms if "corrections" in m.content
    )
    assert "It is X, not Y." in prompt
    assert (
        parse_manual_blocks(catalog_path(catalogs, "svc").read_text(encoding="utf-8"))["purpose"]
        == "It is X, not Y."
    )


def test_an_approval_that_cannot_be_applied_changes_nothing(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, catalogs = env
    proposal = corrections.propose(conn, make_feedback(conn), "svc", "purpose")
    with pytest.raises(QueryError) as no_file:
        corrections.approve(conn, catalogs, proposal.id)
    assert no_file.value.code == "not_found" and "no catalog file" in no_file.value.message

    path = catalog_path(catalogs, "svc")
    path.parent.mkdir(parents=True)
    path.write_text("# svc\n\n## Functional purpose\n\nno blocks here\n", encoding="utf-8")
    with pytest.raises(QueryError, match="no manual block"):
        corrections.approve(conn, catalogs, proposal.id)
    assert corrections.get_correction(conn, proposal.id).status == "proposed"  # type: ignore[union-attr]
    assert path.read_text(encoding="utf-8").endswith("no blocks here\n")
    with pytest.raises(QueryError):
        corrections.approve(conn, catalogs, 999)


def test_rejecting_leaves_the_catalog_alone(env) -> None:  # type: ignore[no-untyped-def]
    conn, _root, catalogs = env
    make_catalog(conn, catalogs)
    before = catalog_path(catalogs, "svc").read_text(encoding="utf-8")
    proposal = corrections.propose(conn, make_feedback(conn), "svc", "purpose")
    done = corrections.reject(conn, proposal.id, "Not accurate.")
    assert (done.status, done.note) == ("rejected", "Not accurate.")
    assert catalog_path(catalogs, "svc").read_text(encoding="utf-8") == before
    assert conn.execute("SELECT proposal_status FROM feedback").fetchone() == ("rejected",)
    assert corrections.list_corrections(conn, "proposed") == []
    assert len(corrections.list_corrections(conn)) == 1
    with pytest.raises(QueryError):
        corrections.list_corrections(conn, "maybe")


# --- catalog corrections: API and command line ---------------------------------------------------


def test_the_api_proposes_but_cannot_approve(env, temp_db_url: str) -> None:  # type: ignore[no-untyped-def]
    conn, root, catalogs = env
    settings = Settings(_env_file=None, db_url=temp_db_url, root=root, catalogs=catalogs)
    client = TestClient(
        create_app(settings, llm_factory=lambda _s: ScriptedLLM(), embedder_factory=lambda _s: None)
    )
    sections = client.get("/api/catalog-sections").json()
    assert sections[0] == {"id": "purpose", "title": "Functional purpose"} and len(sections) == 9

    feedback = make_feedback(conn)
    ok = client.post(
        f"/api/feedback/{feedback}/proposal", json={"application": "svc", "section": "purpose"}
    )
    assert ok.status_code == 201 and ok.json()["status"] == "proposed"
    assert ok.json()["text"] == "It is X, not Y."
    again = client.post(
        f"/api/feedback/{feedback}/proposal", json={"application": "svc", "section": "purpose"}
    )
    assert again.status_code == 422
    assert (
        client.post(
            "/api/feedback/999/proposal", json={"application": "svc", "section": "purpose"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/feedback/{feedback}/proposal",
            json={"application": "ghost", "section": "purpose"},
        ).status_code
        == 422
    )
    # There is no approval endpoint: only a maintainer at the command line can write a catalog.
    assert client.post("/api/corrections/1/approve").status_code == 404
    assert client.get("/api/corrections").status_code == 404


def test_corrections_commands(  # type: ignore[no-untyped-def]
    env, temp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _root, catalogs = env
    make_catalog(conn, catalogs)
    first = corrections.propose(conn, make_feedback(conn), "svc", "purpose")
    second = corrections.propose(conn, make_feedback(conn, "Another."), "svc", "stack")
    isolated_cli(monkeypatch, temp_db_url)
    monkeypatch.setenv("STC_CATALOGS", str(catalogs))
    get_settings.cache_clear()

    listing = runner.invoke(app, ["corrections", "list"])
    assert listing.exit_code == 0, listing.output
    assert (
        f"#{first.id} [proposed] svc / purpose" in listing.output
        and "asked:      What does svc do?" in listing.output
    )
    assert "2 correction(s)." in listing.output

    approved = runner.invoke(app, ["corrections", "approve", str(first.id), "--note", "ok"])
    assert approved.exit_code == 0 and "written to the 'purpose' section of svc" in approved.output
    assert "stc catalog svc" in approved.output
    assert (
        parse_manual_blocks(catalog_path(catalogs, "svc").read_text(encoding="utf-8"))["purpose"]
        == "It is X, not Y."
    )
    assert (
        runner.invoke(app, ["corrections", "approve", str(first.id)]).exit_code == 2
    )  # already decided

    rejected = runner.invoke(app, ["corrections", "reject", str(second.id)])
    assert rejected.exit_code == 0 and f"Rejected #{second.id}" in rejected.output
    assert "0 correction(s)." in runner.invoke(app, ["corrections", "list"]).output
    everything = runner.invoke(app, ["corrections", "list", "--all"])
    assert "[approved]" in everything.output and "[rejected]" in everything.output
    assert runner.invoke(app, ["corrections", "reject", "999"]).exit_code == 1
    get_settings.cache_clear()


def test_status_points_at_uncertain_relations_and_waiting_corrections(  # type: ignore[no-untyped-def]
    env, temp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _root, _catalogs = env
    isolated_cli(monkeypatch, temp_db_url)
    clean = runner.invoke(app, ["status"])
    assert "uncertain relation" not in clean.output and "correction(s) proposed" not in clean.output

    seed_medium(conn)
    corrections.propose(conn, make_feedback(conn), "svc", "purpose")
    result = runner.invoke(app, ["status"])
    assert (
        "1 uncertain relation(s) not checked by the LLM (`stc relations --confirm`)"
        in result.output
    )
    assert (
        "1 catalog correction(s) proposed from the chat wait for you (`stc corrections list`)"
        in result.output
    )
    get_settings.cache_clear()
