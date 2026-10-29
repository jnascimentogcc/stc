"""The chat agent: a tool-use loop over the same tools the MCP server exposes (CHT-02).

The model decides which tools to call and iterates until it has enough evidence. The tools are
the MCP server's own (same names, schemas, descriptions and errors), so chat and MCP cannot drift
apart. The turn yields events for a streaming client: tool calls and results as they happen,
then the answer, then a final event with the verified citations and warnings.

The answer text is sent as the provider produces it. A provider that cannot stream a turn (it
only has `complete`) answers in one piece, which is then sent in small pieces. Text that comes
before a turn's tool calls is not the answer, so a `text_reset` event tells the client to drop
it."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from stc.chat.evidence import Evidence
from stc.chat.prompt import system_prompt
from stc.providers import (
    LLMClient,
    LLMResponse,
    Message,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolSpec,
    turn_events,
)

MAX_STEPS = 8
MAX_TOOL_CHARS = 12_000  # what the model sees of one tool result
CHUNK_CHARS = 48  # size of the answer pieces sent to the client

FINAL_NUDGE = (
    "You have used all the tool calls allowed. Answer now with the evidence you have, and say "
    "clearly what you could not establish."
)


@dataclass
class Event:
    type: str  # tool_call | tool_result | text | text_reset | done | error
    data: dict[str, Any] = field(default_factory=dict)


def _clip(text: str) -> str:
    if len(text) <= MAX_TOOL_CHARS:
        return text
    return text[:MAX_TOOL_CHARS] + "\n...[result cut: narrow the request or use offset/limit]"


def _pieces(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for word in text.split(" "):
        current = f"{current} {word}" if current else word
        if len(current) >= CHUNK_CHARS or "\n" in word:
            out.append(current + " ")
            current = ""
    if current:
        out.append(current)
    return out


async def _call_tool(server: MCPServer, call: ToolCall) -> tuple[str, Any, bool]:
    """(text for the model, structured data, is_error)."""
    try:
        result = await server.call_tool(call.name, call.arguments)
    except ToolError as exc:
        return str(exc), None, True
    except Exception as exc:  # a tool crashing must not end the conversation
        return f"Tool {call.name} failed: {type(exc).__name__}", None, True
    data = getattr(result, "structured_content", None)
    text = (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if data is not None
        else "\n".join(getattr(block, "text", "") for block in getattr(result, "content", []))
    )
    return text, data, bool(getattr(result, "is_error", False))


def _summary(data: Any, is_error: bool, text: str) -> str:
    if is_error:
        return text[:200]
    if isinstance(data, dict):
        for key in ("items", "result"):
            if isinstance(data.get(key), list):
                return f"{len(data[key])} result(s)"
    return "ok"


async def run_agent(
    llm: LLMClient,
    server: MCPServer,
    *,
    history: list[Message],
    question: str,
    profile: str,
    language: str,
    scope: list[str] | None,
    max_steps: int = MAX_STEPS,
) -> AsyncIterator[Event]:
    specs = [
        ToolSpec(t.name, t.description or "", t.input_schema) for t in await server.list_tools()
    ]
    messages: list[Message] = [
        Message("system", system_prompt(profile=profile, language=language, scope=scope)),
        *history,
        Message("user", question),
    ]
    evidence = Evidence()
    input_tokens = output_tokens = steps = 0
    answer: str | None = None

    while answer is None:
        last = steps >= max_steps
        if last:
            messages.append(Message("user", FINAL_NUDGE))
        streamed = False
        response: LLMResponse | None = None
        try:
            async for item in turn_events(llm, messages, None if last else specs):
                if isinstance(item, TextDelta):
                    streamed = True
                    yield Event("text", {"delta": item.text})
                else:
                    response = item.response
        except ProviderError as exc:
            message = str(exc)
            if "reasoning_effort" in message:
                message += " (set STC_LLM_CHAT_REASONING_EFFORT=none)"
            yield Event("error", {"code": "provider_error", "message": message})
            return
        if response is None:
            yield Event("error", {"code": "provider_error", "message": "The model sent no reply."})
            return
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        steps += 1
        if not response.tool_calls or last:
            answer = response.text.strip() or "I could not produce an answer."
            if not streamed:  # a provider that cannot stream a turn: send it in pieces
                for piece in _pieces(answer):
                    yield Event("text", {"delta": piece})
            break
        if streamed:
            # Text that came before the tool calls is not the answer: the client drops it.
            yield Event("text_reset")

        messages.append(Message("assistant", response.text, tool_calls=tuple(response.tool_calls)))
        for call in response.tool_calls:
            yield Event(
                "tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments}
            )
        results = await asyncio.gather(*(_call_tool(server, c) for c in response.tool_calls))
        for call, (text, data, is_error) in zip(response.tool_calls, results, strict=True):
            if not is_error:
                evidence.observe(data)
            messages.append(Message("tool", _clip(text), tool_call_id=call.id))
            yield Event(
                "tool_result",
                {
                    "id": call.id,
                    "name": call.name,
                    "ok": not is_error,
                    "summary": _summary(data, is_error, text),
                },
            )

    citations = evidence.check(answer)
    warnings = list(evidence.warnings)
    unverified = [c.ref for c in citations if not c.verified]
    if unverified:
        warnings.append(
            "Some citations could not be matched to evidence retrieved in this turn: "
            + ", ".join(unverified)
        )
    yield Event(
        "done",
        {
            "answer": answer,
            "citations": [c.__dict__ for c in citations],
            "warnings": warnings,
            "steps": steps,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )
