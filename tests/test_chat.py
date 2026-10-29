"""The chat: evidence checking, the agent loop, conversation scope and the HTTP API (SSE)."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from stc.api.app import create_app
from stc.chat.agent import run_agent
from stc.chat.evidence import Evidence
from stc.config import Settings
from stc.db.migrate import migrate
from stc.mcp_server import build_server
from stc.providers import LLMResponse, Message, ProviderError, ToolCall, ToolSpec, Usage
from stc.query import QueryEngine, QueryError
from test_catalog import index, make_root, write

# --- evidence ------------------------------------------------------------------------------------


def test_citations_are_verified_against_what_the_tools_returned() -> None:
    evidence = Evidence()
    evidence.observe(
        {
            "items": [
                {
                    "source": {
                        "application": "svc",
                        "file": "app.py",
                        "start_line": 5,
                        "end_line": 20,
                    }
                }
            ]
        }
    )
    evidence.observe({"application": "svc", "sections": {"purpose": "Handles it. `README.md:1-3`"}})
    answer = (
        "Proven: `svc/app.py:7-8` and `svc/app.py:10`; also `svc/README.md:1-2`. "
        "Made up: `svc/app.py:90-95`, `svc/ghost.py:1-2`."
    )
    by_ref = {c.ref: c for c in evidence.check(answer)}
    assert by_ref["svc/app.py:7-8"].verified and by_ref["svc/app.py:7-8"].application == "svc"
    assert by_ref["svc/app.py:10"].verified
    assert by_ref["svc/README.md:1-2"].verified  # from the catalog text
    assert not by_ref["svc/app.py:90-95"].verified and by_ref["svc/app.py:90-95"].file is None
    assert not by_ref["svc/ghost.py:1-2"].verified


def test_outdated_and_stale_flags_become_warnings() -> None:
    evidence = Evidence()
    evidence.observe({"application": "svc", "outdated": True, "sections": {}})
    evidence.observe(
        {
            "source": {"application": "svc", "file": "a.py", "start_line": 1, "end_line": 1},
            "stale": True,
        }
    )
    evidence.observe({"application": "web", "outdated": True})
    assert len(evidence.warnings) == 3 and "svc" in evidence.warnings[0]


# --- helpers -------------------------------------------------------------------------------------


def call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        "", [ToolCall(f"c-{name}", name, arguments)], Usage(10, 2), finish_reason="tool_calls"
    )


def say(text: str) -> LLMResponse:
    return LLMResponse(text, [], Usage(20, 5), finish_reason="stop")


class ScriptedLLM:
    provider = "fake"
    model = "scripted"

    def __init__(self, *responses: LLMResponse | ProviderError) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[list[Message], bool]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.requests.append((list(messages), bool(tools)))
        item = self.responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

    def stream(self, messages, *, tools=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


@pytest.fixture
def settings(temp_db_url: str, tmp_path: Path) -> Settings:
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    write(root, "svc/billing.py", "def charge_customer(customer):\n    return 'payment ok'\n")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
        conn.execute("UPDATE applications SET indexed_version = 'v1'")
    return Settings(
        _env_file=None,
        db_url=temp_db_url,
        root=root,
        catalogs=tmp_path / "catalogs",
        language="English",
    )


def agent_events(settings: Settings, llm: ScriptedLLM, question: str = "q", **kwargs: Any) -> list:  # type: ignore[type-arg]
    server = build_server(settings, lambda _s: None, kwargs.get("scope"))

    async def go() -> list:  # type: ignore[type-arg]
        return [
            e
            async for e in run_agent(
                llm,
                server,
                history=kwargs.get("history", []),
                question=question,
                profile=kwargs.get("profile", "technical"),
                language="English",
                scope=kwargs.get("scope"),
                max_steps=kwargs.get("max_steps", 8),
            )
        ]

    return asyncio.run(go())


# --- agent ---------------------------------------------------------------------------------------


def test_agent_calls_tools_then_answers_with_verified_citations(settings: Settings) -> None:
    llm = ScriptedLLM(
        call("grep_code", pattern="payment ok"),
        say("It returns `svc/billing.py:2-2` when charging. Also `svc/billing.py:99-99`."),
    )
    events = agent_events(settings, llm)
    assert [e.type for e in events][:2] == ["tool_call", "tool_result"]
    assert events[0].data["name"] == "grep_code" and events[1].data["ok"]
    assert events[1].data["summary"] == "1 result(s)"
    text = "".join(e.data["delta"] for e in events if e.type == "text")
    done = events[-1]
    assert done.type == "done" and done.data["answer"] == text.strip()
    citations = {c["ref"]: c for c in done.data["citations"]}
    assert citations["svc/billing.py:2-2"]["verified"]
    assert not citations["svc/billing.py:99-99"]["verified"]
    assert any("svc/billing.py:99-99" in w for w in done.data["warnings"])
    assert done.data["steps"] == 2 and done.data["usage"] == {
        "input_tokens": 30,
        "output_tokens": 7,
    }
    # The tool result reached the model as a tool message.
    second_request, with_tools = llm.requests[1]
    assert with_tools and second_request[-1].role == "tool"
    assert "billing.py" in second_request[-1].content


def test_agent_gives_the_model_the_same_tools_as_mcp_and_the_profile(settings: Settings) -> None:
    llm = ScriptedLLM(say("done"))
    agent_events(settings, llm, profile="functional")
    (messages, with_tools) = llm.requests[0]
    assert with_tools
    system = messages[0].content
    assert "not a developer" in system and "proven in the code" in system.lower()
    assert "This conversation covers every indexed application." in system


def test_tool_errors_go_back_to_the_model_not_to_the_user(settings: Settings) -> None:
    llm = ScriptedLLM(
        call("read_file", application="svc", path="credentials.json"),
        say("I could not read it."),
    )
    events = agent_events(settings, llm)
    result = next(e for e in events if e.type == "tool_result")
    assert result.data["ok"] is False and "forbidden" in result.data["summary"]
    assert "forbidden" in llm.requests[1][0][-1].content
    assert events[-1].type == "done"


def test_step_limit_forces_an_answer_without_tools(settings: Settings) -> None:
    llm = ScriptedLLM(call("list_applications"), call("list_applications"), say("Best effort."))
    events = agent_events(settings, llm, max_steps=2)
    assert events[-1].type == "done" and events[-1].data["answer"] == "Best effort."
    last_messages, with_tools = llm.requests[-1]
    assert not with_tools and "used all the tool calls" in last_messages[-1].content


def test_provider_failure_is_an_error_event(settings: Settings) -> None:
    events = agent_events(settings, ScriptedLLM(ProviderError("fake", "rate limited")))
    assert [e.type for e in events] == ["error"]
    assert "rate limited" in events[0].data["message"]


def test_history_is_part_of_the_context(settings: Settings) -> None:
    history = [Message("user", "Tell me about svc"), Message("assistant", "It bills customers.")]
    llm = ScriptedLLM(say("And what about its endpoints?"))
    agent_events(settings, llm, "and web?", history=history)
    roles = [m.role for m in llm.requests[0][0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert llm.requests[0][0][-1].content == "and web?"


# --- scope (CHT-06) -----------------------------------------------------------------------------


def test_scoped_engine_cannot_see_other_applications(settings: Settings) -> None:
    assert settings.db_url
    with psycopg.connect(settings.db_url, autocommit=True) as conn:
        engine = QueryEngine(conn, root=settings.root, catalogs=settings.catalogs, scope=["svc"])
        assert [a.name for a in engine.list_applications().items] == ["svc"]
        assert {h.source.application for h in engine.search_code("fetch invoices").items} <= {"svc"}
        assert engine.grep("fetch").items == []  # that line lives in web
        assert [h.source.application for h in engine.grep("payment ok").items] == ["svc"]
        for blocked in (
            lambda: engine.read_file("web", "client.js"),
            lambda: engine.get_catalog("web"),
            lambda: engine.get_dependencies("web"),
            lambda: engine.get_global_map(),
            lambda: engine.search_code("x", applications=["web"]),
            lambda: engine.status("web"),
        ):
            with pytest.raises(QueryError) as caught:
                blocked()
            assert caught.value.code == "forbidden"
        assert [a.name for a in engine.status().applications] == ["svc"]
        assert engine.get_dependencies("svc")  # relations of an in-scope application are visible


def test_scoped_agent_has_no_global_map_and_is_told_the_scope(settings: Settings) -> None:
    llm = ScriptedLLM(call("read_file", application="web", path="client.js"), say("Not allowed."))
    events = agent_events(settings, llm, scope=["svc"])
    assert next(e for e in events if e.type == "tool_result").data["ok"] is False
    system = llm.requests[0][0][0].content
    assert "restricted to these applications: svc" in system
    names = {
        t.name for t in asyncio.run(build_server(settings, lambda _s: None, ["svc"]).list_tools())
    }
    assert "get_global_map" not in names and "search_code" in names


# --- HTTP API ------------------------------------------------------------------------------------


def make_client(settings: Settings, llm: ScriptedLLM) -> TestClient:
    return TestClient(
        create_app(settings, llm_factory=lambda _s: llm, embedder_factory=lambda _s: None)
    )


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_conversation_lifecycle(settings: Settings) -> None:
    client = make_client(settings, ScriptedLLM())
    created = client.post("/api/conversations", json={"profile": "functional"})
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["scope"] == {"type": "root"} and conversation["profile"] == "functional"
    cid = conversation["id"]

    scoped = client.patch(
        f"/api/conversations/{cid}", json={"applications": ["svc"], "title": "Billing"}
    )
    assert scoped.json()["scope"] == {"type": "applications", "applications": ["svc"]}
    assert scoped.json()["title"] == "Billing" and scoped.json()["profile"] == "functional"
    back = client.patch(f"/api/conversations/{cid}", json={"all_applications": True})
    assert back.json()["scope"] == {"type": "root"}

    assert [c["id"] for c in client.get("/api/conversations").json()] == [cid]
    assert client.get(f"/api/conversations/{cid}").json()["messages"] == []
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.delete(f"/api/conversations/{cid}").status_code == 404


def test_conversation_validation(settings: Settings) -> None:
    client = make_client(settings, ScriptedLLM())
    assert client.post("/api/conversations", json={"applications": ["ghost"]}).status_code == 422
    assert client.post("/api/conversations", json={"applications": []}).status_code == 422
    assert client.post("/api/conversations", json={"profile": "poet"}).status_code == 422
    assert client.get("/api/conversations/not-a-uuid").status_code == 404
    assert client.get("/api/applications").json() == [
        {"name": "svc", "has_catalog": False},
        {"name": "web", "has_catalog": False},
    ]


def test_ask_streams_events_and_saves_the_exchange(settings: Settings) -> None:
    llm = ScriptedLLM(
        call("grep_code", pattern="payment ok"),
        say("It answers `svc/billing.py:2-2`."),
        say("The second question is answered."),
    )
    client = make_client(settings, llm)
    cid = client.post("/api/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/conversations/{cid}/messages", json={"content": "What does charging return?"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [name for name, _ in events][:2] == ["tool_call", "tool_result"]
    assert events[-1][0] == "done"
    done = events[-1][1]
    assert done["citations"][0]["verified"] and isinstance(done["message_id"], int)

    stored = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]
    assert stored["messages"][1]["content"] == "It answers `svc/billing.py:2-2`."
    assert stored["messages"][1]["citations"][0]["ref"] == "svc/billing.py:2-2"
    assert stored["title"] == "What does charging return?"

    second = client.post(f"/api/conversations/{cid}/messages", json={"content": "And then?"})
    assert parse_sse(second.text)[-1][0] == "done"
    follow_up_context = [m.content for m in llm.requests[-1][0]]
    assert "What does charging return?" in follow_up_context
    assert "It answers `svc/billing.py:2-2`." in follow_up_context


def test_ask_in_a_scoped_conversation_is_enforced(settings: Settings) -> None:
    llm = ScriptedLLM(call("read_file", application="web", path="client.js"), say("No access."))
    client = make_client(settings, llm)
    cid = client.post("/api/conversations", json={"applications": ["svc"]}).json()["id"]
    events = parse_sse(
        client.post(f"/api/conversations/{cid}/messages", json={"content": "Show web"}).text
    )
    result = next(data for name, data in events if name == "tool_result")
    assert result["ok"] is False and "outside the scope" in result["summary"]


def test_ask_reports_provider_errors_and_missing_pieces(settings: Settings) -> None:
    client = make_client(settings, ScriptedLLM(ProviderError("fake", "boom")))
    cid = client.post("/api/conversations", json={}).json()["id"]
    events = parse_sse(
        client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"}).text
    )
    assert [n for n, _ in events] == ["error"]
    stored = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in stored["messages"]] == ["user"]  # the question is kept

    assert (
        client.post(f"/api/conversations/{cid}/messages", json={"content": ""}).status_code == 422
    )
    missing = "00000000-0000-0000-0000-000000000000"
    assert (
        client.post(f"/api/conversations/{missing}/messages", json={"content": "x"}).status_code
        == 404
    )

    def unavailable(_s: Settings) -> ScriptedLLM:
        raise ProviderError("openai", "STC_LLM_CHAT_MODEL is not set")

    broken = TestClient(create_app(settings, llm_factory=unavailable))
    cid2 = broken.post("/api/conversations", json={}).json()["id"]
    refused = broken.post(f"/api/conversations/{cid2}/messages", json={"content": "x"})
    assert refused.status_code == 503 and "STC_LLM_CHAT_MODEL" in refused.json()["detail"]


def test_file_slice_endpoint_returns_code_and_refuses_secrets(settings: Settings) -> None:
    client = make_client(settings, ScriptedLLM())
    ok = client.get(
        "/api/files/slice",
        params={"application": "svc", "path": "billing.py", "start_line": 2, "end_line": 2},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert (
        body["text"] == "    return 'payment ok'" and body["source"]["ref"] == "svc/billing.py:2-2"
    )
    forbidden = client.get(
        "/api/files/slice", params={"application": "svc", "path": "credentials.json"}
    )
    assert forbidden.status_code == 403
    assert (
        client.get("/api/files/slice", params={"application": "ghost", "path": "x"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/files/slice", params={"application": "svc", "path": "missing.py"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/files/slice", params={"application": "svc", "path": "billing.py", "start_line": 0}
        ).status_code
        == 422
    )


def test_feedback_on_an_answer(settings: Settings) -> None:
    client = make_client(settings, ScriptedLLM(say("An answer.")))
    cid = client.post("/api/conversations", json={}).json()["id"]
    done = parse_sse(client.post(f"/api/conversations/{cid}/messages", json={"content": "q"}).text)[
        -1
    ][1]
    mid = done["message_id"]
    flagged = client.post(
        f"/api/messages/{mid}/feedback", json={"rating": "incorrect", "correction": " It is X. "}
    )
    assert flagged.status_code == 201
    assert (
        flagged.json()["correction"] == "It is X." and flagged.json()["proposal_status"] == "none"
    )
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": "meh"}).status_code == 422
    assert (
        client.post("/api/messages/999999/feedback", json={"rating": "incomplete"}).status_code
        == 404
    )
    first = client.get(f"/api/conversations/{cid}").json()["messages"][0]["id"]
    assert (
        client.post(f"/api/messages/{first}/feedback", json={"rating": "incomplete"}).status_code
        == 422
    )
