"""OpenAI adapters for `LLMClient` and `EmbeddingsClient` (Chat Completions + Embeddings APIs).

Works with any OpenAI-compatible endpoint through `base_url`. SDK errors are translated to
`ProviderError`, whose `retryable` flag drives worker retries; request bodies (which contain
source code) and the API key never appear in error messages.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from stc.providers.errors import ProviderConfigError, ProviderError, ProviderResponseError
from stc.providers.llm import (
    LLMResponse,
    Message,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    TurnEnd,
    Usage,
)
from stc.providers.ratelimit import RateLimiter

PROVIDER = "openai"
_RETRYABLE_STATUS = frozenset({408, 409, 429})


def _make_client(
    api_key: str | None,
    base_url: str | None,
    client: AsyncOpenAI | None,
    max_retries: int,
    timeout: float,
) -> AsyncOpenAI:
    if client is not None:
        return client
    if not api_key:
        raise ProviderConfigError(PROVIDER, "OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries, timeout=timeout)


def _translate(exc: openai.OpenAIError) -> ProviderError:
    if isinstance(exc, openai.APIConnectionError):  # includes timeouts
        return ProviderError(PROVIDER, f"connection failed: {exc.message}", retryable=True)
    if isinstance(exc, openai.APIStatusError):
        retryable = exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500
        return ProviderError(
            PROVIDER, f"HTTP {exc.status_code}: {exc.message}", retryable=retryable
        )
    return ProviderError(PROVIDER, str(exc), retryable=False)


class OpenAILLM:
    provider = PROVIDER

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        limiter: RateLimiter | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 2,
        timeout: float = 120.0,
    ) -> None:
        if not model:
            raise ProviderConfigError(PROVIDER, "no LLM model configured")
        self.model = model
        self._client = _make_client(api_key, base_url, client, max_retries, timeout)
        self._limiter = limiter
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort

    async def aclose(self) -> None:
        """Close the HTTP connections; call it in the event loop that used the client."""
        await self._client.close()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs = self._request(messages, tools, response_schema)
        if self._limiter:
            await self._limiter.wait()
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        if not response.choices:
            raise ProviderResponseError(PROVIDER, "the response has no choices")
        choice = response.choices[0]
        usage = response.usage
        return LLMResponse(
            text=choice.message.content or "",
            tool_calls=[_parse_tool_call(call) for call in choice.message.tool_calls or []],
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            finish_reason=choice.finish_reason,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[str]:
        kwargs = self._request(messages, tools, None)
        if self._limiter:
            await self._limiter.wait()
        try:
            chunks = await self._client.chat.completions.create(stream=True, **kwargs)
            async for chunk in chunks:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc

    async def stream_turn(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """One turn, streamed: text deltas as they arrive, then a `TurnEnd` with the text, the
        tool calls (their arguments arrive in fragments and are put together here), the usage
        and the finish reason."""
        kwargs = self._request(messages, tools, None)
        kwargs["stream_options"] = {"include_usage": True}
        if self._limiter:
            await self._limiter.wait()
        text: list[str] = []
        fragments: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish: str | None = None
        try:
            chunks = await self._client.chat.completions.create(stream=True, **kwargs)
            async for chunk in chunks:
                if chunk.usage:
                    usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    text.append(choice.delta.content)
                    yield TextDelta(choice.delta.content)
                for part in choice.delta.tool_calls or []:
                    slot = fragments.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    if part.id:
                        slot["id"] = part.id
                    if part.function and part.function.name:
                        slot["name"] += part.function.name
                    if part.function and part.function.arguments:
                        slot["arguments"] += part.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        calls = [
            _tool_call(slot["id"], slot["name"], slot["arguments"])
            for _, slot in sorted(fragments.items())
        ]
        yield TurnEnd(LLMResponse("".join(text), calls, usage, finish))

    def _request(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if response_schema is not None:
            # Not `strict`: the caller validates the JSON against the schema anyway.
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema, "strict": False},
            }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._max_output_tokens is not None:
            kwargs["max_completion_tokens"] = self._max_output_tokens
        if self._reasoning_effort is not None:
            kwargs["reasoning_effort"] = self._reasoning_effort
        return kwargs


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        if not message.tool_call_id:
            raise ValueError("a tool message needs a tool_call_id")
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return wire


def _parse_tool_call(call: Any) -> ToolCall:
    return _tool_call(call.id, call.function.name, call.function.arguments)


def _tool_call(call_id: str, name: str, raw_arguments: str | None) -> ToolCall:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(
            PROVIDER, f"tool call {name!r} has invalid JSON arguments"
        ) from exc
    if not isinstance(arguments, dict):
        raise ProviderResponseError(PROVIDER, f"tool call {name!r} arguments are not a JSON object")
    return ToolCall(id=call_id, name=name, arguments=arguments)


class OpenAIEmbeddings:
    provider = PROVIDER

    def __init__(
        self,
        *,
        model: str,
        dimensions: int = 1536,
        api_key: str | None = None,
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        limiter: RateLimiter | None = None,
        batch_size: int = 128,
        batch_chars: int = 400_000,
        max_retries: int = 2,
        timeout: float = 120.0,
    ) -> None:
        if not model:
            raise ProviderConfigError(PROVIDER, "no embeddings model configured")
        self.model = model
        self.dimensions = dimensions
        self._client = _make_client(api_key, base_url, client, max_retries, timeout)
        self._limiter = limiter
        self._batch_size = batch_size
        self._batch_chars = batch_chars

    async def aclose(self) -> None:
        """Close the HTTP connections; call it in the event loop that used the client."""
        await self._client.close()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("cannot embed an empty text")
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(await self._embed_batch(batch))
        return vectors

    def _batches(self, texts: Sequence[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        chars = 0
        for text in texts:
            if current and (
                len(current) >= self._batch_size or chars + len(text) > self._batch_chars
            ):
                batches.append(current)
                current, chars = [], 0
            current.append(text)
            chars += len(text)
        if current:
            batches.append(current)
        return batches

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        if self._limiter:
            await self._limiter.wait()
        try:
            response = await self._client.embeddings.create(
                model=self.model,
                input=batch,
                dimensions=self.dimensions,
                encoding_format="float",
            )
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        data = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in data] != list(range(len(batch))):
            raise ProviderResponseError(PROVIDER, "embeddings response does not match the request")
        for item in data:
            if len(item.embedding) != self.dimensions:
                raise ProviderResponseError(
                    PROVIDER,
                    f"expected {self.dimensions} dimensions, got {len(item.embedding)}; "
                    "check STC_EMBEDDINGS_MODEL and STC_EMBEDDINGS_DIMENSIONS",
                )
        return [list(item.embedding) for item in data]
