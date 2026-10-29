"""Provider-neutral LLM types. Each provider adapter translates these to its own wire format."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """`tool_calls` is set on assistant messages that call tools; `tool_call_id` on the
    `tool` messages that carry the results back."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None  # "stop", "tool_calls", "length", ...


class LLMClient(Protocol):
    """Chat, structured output and streaming for every LLM provider."""

    provider: str
    model: str

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """One model turn. With `response_schema` the reply text is JSON for that schema (the
        caller still validates it); with `tools` the model may answer with `tool_calls`."""
        ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[str]:
        """Text deltas of one model turn."""
        ...


@dataclass(frozen=True)
class TextDelta:
    """A piece of the reply text, as soon as the provider sends it."""

    text: str


@dataclass(frozen=True)
class TurnEnd:
    """The whole turn once the stream ends: its text, tool calls, usage and finish reason."""

    response: LLMResponse


StreamEvent = TextDelta | TurnEnd


class StreamingLLMClient(LLMClient, Protocol):
    """A provider that streams a whole turn, tool calls included (`stream` above is text only)."""

    def stream_turn(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Text deltas as they arrive, then one `TurnEnd` with the complete response."""
        ...


async def turn_events(
    llm: LLMClient, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
) -> AsyncIterator[StreamEvent]:
    """One model turn as events. A provider that cannot stream a turn answers in one piece: the
    caller then gets only the `TurnEnd`."""
    stream_turn = getattr(llm, "stream_turn", None)
    if stream_turn is None:
        yield TurnEnd(await llm.complete(messages, tools=tools))
        return
    async for event in stream_turn(messages, tools=tools):
        yield event
