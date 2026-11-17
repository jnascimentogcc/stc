"""A minimal LLM client: one call, a JSON Schema reply.

It is deliberately small and separate from STC's own providers (the tool cannot import `stc`):
a system prompt, a user prompt, a schema, and a validated JSON object back. Prompts contain
specification text, so they are never logged, and neither is the API key."""

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from jsonschema import Draft202012Validator

MODEL_ENV = "STC_TESTS_LLM_MODEL"


class LLMError(Exception):
    """The call failed or the reply never followed the schema."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


class JsonLLM(Protocol):
    model: str

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]: ...


class OpenAIJsonLLM:
    """OpenAI (or any OpenAI-compatible endpoint through `base_url`)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        if not model:
            raise LLMError(f"no model configured: set {MODEL_ENV}")
        self.model = model
        if client is None:
            if not api_key:
                raise LLMError("OPENAI_API_KEY is not set")
            client = openai.OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
            )
        self._client = client

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]:
        """The reply as a dict that validates against `schema`; one repair attempt otherwise."""
        validator = Draft202012Validator(schema)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        usage = Usage()
        problem = "no reply"
        for _ in range(2):
            text, used = self._call(messages, schema)
            usage += used
            try:
                reply = json.loads(text)
            except ValueError:
                problem = "the reply is not valid JSON"
            else:
                errors = sorted(e.message for e in validator.iter_errors(reply))
                if not errors:
                    return reply, usage
                problem = "; ".join(errors[:3])
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"The reply does not follow the schema ({problem}). Reply again.",
                },
            ]
        raise LLMError(f"the reply never followed the schema: {problem}")

    def _call(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> tuple[str, Usage]:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": False},
                },
            )
        except openai.OpenAIError as error:
            raise LLMError(f"{type(error).__name__}: {getattr(error, 'message', error)}") from error
        if not response.choices:
            raise LLMError("the response has no choices")
        usage = response.usage
        return (
            response.choices[0].message.content or "",
            Usage(usage.prompt_tokens, usage.completion_tokens) if usage else Usage(),
        )


def make_llm() -> JsonLLM:
    """The client the environment sets up (STC_TESTS_LLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL)."""
    return OpenAIJsonLLM(
        model=os.environ.get(MODEL_ENV, ""),
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )
