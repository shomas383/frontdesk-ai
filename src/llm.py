"""Small LLM provider interface: Anthropic API now, Bedrock later. Selected by LLM_PROVIDER."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from src.config import Settings, get_settings


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any] | None  # parsed JSON; None if the model stopped before valid output
    raw_text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    model_id: str

    def generate_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> LLMResult: ...


class _MessagesProvider:
    """Shared implementation: both clients expose the same `messages.create` surface."""

    def __init__(self, client: anthropic.Anthropic | anthropic.AnthropicBedrock, model_id: str):
        self._client = client
        self.model_id = model_id

    def generate_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> LLMResult:
        response = self._client.messages.create(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            # Short factual answers: no thinking, low effort keeps latency and cost down.
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return LLMResult(
            data=_parse_json(text) if response.stop_reason == "end_turn" else None,
            raw_text=text,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "anthropic":
        return _MessagesProvider(anthropic.Anthropic(), settings.llm_model)
    if settings.llm_provider == "bedrock":
        if not settings.bedrock_model_id:
            raise ValueError("LLM_PROVIDER=bedrock requires BEDROCK_MODEL_ID (a 'us.' inference profile)")
        client = anthropic.AnthropicBedrock(aws_region=settings.aws_region)
        return _MessagesProvider(client, settings.bedrock_model_id)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (expected anthropic|bedrock)")
