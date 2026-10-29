"""OpenAI adapters against a mocked HTTP transport: no network, no API key, no cost."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from stc.providers import (
    Message,
    ProviderConfigError,
    ProviderError,
    ProviderResponseError,
    ToolCall,
    ToolSpec,
)
from stc.providers.openai_adapter import OpenAIEmbeddings, OpenAILLM
from stc.providers.ratelimit import RateLimiter

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="sk-test-secret",
        base_url="http://openai.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


def chat_reply(
    content: str | None = "hi", tool_calls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }


def llm(handler: Handler, **kwargs: Any) -> OpenAILLM:
    return OpenAILLM(model="test-model", client=make_client(handler), **kwargs)


# --- LLM ---------------------------------------------------------------------------------------


def test_complete_returns_text_usage_and_sends_expected_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_reply("hello"))

    result = asyncio.run(
        llm(handler, temperature=0.2, max_output_tokens=50).complete(
            [Message("system", "be brief"), Message("user", "hi")]
        )
    )
    assert (result.text, result.finish_reason) == ("hello", "stop")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 3)
    (request,) = seen
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-secret"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert (body["temperature"], body["max_completion_tokens"]) == (0.2, 50)
    assert "tools" not in body and "response_format" not in body


def test_clients_can_be_closed_in_the_loop_that_used_them() -> None:
    from stc.providers import closing

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_reply("ok"))

    client = llm(handler)

    async def go() -> str:
        reply = await closing(client.complete([Message("user", "hi")]), client)
        return reply.text

    assert asyncio.run(go()) == "ok"
    assert client._client.is_closed()


def test_reasoning_effort_is_sent_only_when_configured() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply("ok"))

    asyncio.run(llm(handler).complete([Message("user", "hi")]))
    asyncio.run(llm(handler, reasoning_effort="none").complete([Message("user", "hi")]))
    assert "reasoning_effort" not in bodies[0] and bodies[1]["reasoning_effort"] == "none"


def test_tools_are_sent_and_tool_calls_parsed() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_code", "arguments": '{"query": "login", "limit": 5}'},
        }
        return httpx.Response(200, json=chat_reply(None, [call]))

    tool = ToolSpec(
        "search_code",
        "Search the code",
        {"type": "object", "properties": {"query": {"type": "string"}}},
    )
    result = asyncio.run(llm(handler).complete([Message("user", "find login")], tools=[tool]))
    assert result.finish_reason == "tool_calls" and result.text == ""
    assert result.tool_calls == [ToolCall("call_1", "search_code", {"query": "login", "limit": 5})]
    assert bodies[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search the code",
                "parameters": tool.parameters,
            },
        }
    ]


def test_tool_round_trip_messages_are_serialized() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply("done"))

    call = ToolCall("call_1", "search_code", {"query": "x"})
    asyncio.run(
        llm(handler).complete(
            [
                Message("user", "q"),
                Message("assistant", "", tool_calls=(call,)),
                Message("tool", "result", tool_call_id="call_1"),
            ]
        )
    )
    assistant, tool = bodies[0]["messages"][1:]
    assert assistant["tool_calls"][0]["function"] == {
        "name": "search_code",
        "arguments": '{"query": "x"}',
    }
    assert tool == {"role": "tool", "tool_call_id": "call_1", "content": "result"}


def test_tool_message_without_id_is_rejected() -> None:
    adapter = llm(lambda r: httpx.Response(200, json=chat_reply()))
    with pytest.raises(ValueError):
        asyncio.run(adapter.complete([Message("tool", "x")]))


def test_response_schema_requests_json_schema_output() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply('{"purpose": "x"}'))

    schema = {"type": "object", "properties": {"purpose": {"type": "string"}}}
    result = asyncio.run(llm(handler).complete([Message("user", "go")], response_schema=schema))
    assert json.loads(result.text) == {"purpose": "x"}
    assert bodies[0]["response_format"]["type"] == "json_schema"
    assert bodies[0]["response_format"]["json_schema"]["schema"] == schema


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_malformed_tool_arguments_are_a_response_error(arguments: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        call = {"id": "c", "type": "function", "function": {"name": "t", "arguments": arguments}}
        return httpx.Response(200, json=chat_reply(None, [call]))

    with pytest.raises(ProviderResponseError):
        asyncio.run(llm(handler).complete([Message("user", "x")]))


def test_stream_yields_text_deltas() -> None:
    def event(delta: dict[str, Any], finish: str | None = None) -> str:
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    sse = (
        event({"role": "assistant", "content": ""})
        + event({"content": "Hel"})
        + event({"content": "lo"})
        + event({}, "stop")
        + "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    async def collect() -> list[str]:
        return [part async for part in llm(handler).stream([Message("user", "hi")])]

    assert asyncio.run(collect()) == ["Hel", "lo"]


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (500, True), (503, True), (400, False), (401, False)]
)
def test_http_errors_are_translated_with_retry_hint(status: int, retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "nope", "type": "x"}})

    with pytest.raises(ProviderError) as info:
        asyncio.run(llm(handler).complete([Message("user", "SECRET SOURCE CODE")]))
    assert info.value.retryable is retryable and info.value.provider == "openai"
    assert str(status) in str(info.value)
    # Neither the prompt (source code) nor the key may leak into errors.
    assert "SECRET SOURCE CODE" not in str(info.value) and "sk-test-secret" not in str(info.value)


def test_connection_errors_are_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ProviderError) as info:
        asyncio.run(llm(handler).complete([Message("user", "x")]))
    assert info.value.retryable


def test_missing_key_or_model_is_a_config_error() -> None:
    with pytest.raises(ProviderConfigError):
        OpenAILLM(model="m", api_key=None)
    with pytest.raises(ProviderConfigError):
        OpenAILLM(model="", api_key="k")


# --- embeddings --------------------------------------------------------------------------------


def embedding_reply(inputs: list[str], dimensions: int, *, shuffle: bool = False) -> dict[str, Any]:
    data = [
        {"object": "embedding", "index": i, "embedding": [float(len(text))] * dimensions}
        for i, text in enumerate(inputs)
    ]
    if shuffle:
        data.reverse()
    return {
        "object": "list",
        "data": data,
        "model": "m",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


def test_embeddings_batch_requests_and_keep_order() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=embedding_reply(body["input"], 4, shuffle=True))

    adapter = OpenAIEmbeddings(model="emb", dimensions=4, batch_size=2, client=make_client(handler))
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    vectors = asyncio.run(adapter.embed(texts))
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]  # order survives the shuffle
    assert [len(r["input"]) for r in requests] == [2, 2, 1]
    assert requests[0]["model"] == "emb" and requests[0]["dimensions"] == 4


def test_embeddings_batches_also_respect_a_character_budget() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sizes.append(len(body["input"]))
        return httpx.Response(200, json=embedding_reply(body["input"], 2))

    adapter = OpenAIEmbeddings(
        model="emb", dimensions=2, batch_chars=10, client=make_client(handler)
    )
    asyncio.run(adapter.embed(["x" * 6, "y" * 6, "z" * 3]))
    assert sizes == [1, 2]


def test_embeddings_wrong_dimension_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=embedding_reply(json.loads(request.content)["input"], 3))

    adapter = OpenAIEmbeddings(model="emb", dimensions=4, client=make_client(handler))
    with pytest.raises(ProviderResponseError, match="expected 4 dimensions"):
        asyncio.run(adapter.embed(["a"]))


def test_embeddings_reject_empty_text_and_handle_no_input() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=embedding_reply([], 2))

    adapter = OpenAIEmbeddings(model="emb", dimensions=2, client=make_client(handler))
    with pytest.raises(ValueError):
        asyncio.run(adapter.embed(["ok", "  "]))
    assert asyncio.run(adapter.embed([])) == [] and not calls


def test_embeddings_http_error_is_translated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    adapter = OpenAIEmbeddings(model="emb", dimensions=2, client=make_client(handler))
    with pytest.raises(ProviderError) as info:
        asyncio.run(adapter.embed(["a"]))
    assert info.value.retryable


# --- rate limiter ------------------------------------------------------------------------------


def test_rate_limiter_spaces_calls() -> None:
    async def run() -> float:
        limiter = RateLimiter(per_minute=600)  # one call every 0.1 s
        start = time.monotonic()
        await asyncio.gather(*(limiter.wait() for _ in range(3)))
        return time.monotonic() - start

    assert asyncio.run(run()) >= 0.18


def test_rate_limiter_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)
