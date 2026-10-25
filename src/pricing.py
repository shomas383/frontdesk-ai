"""Token pricing and cost estimates, shared by the eval runner and the API."""

from __future__ import annotations

# USD per million tokens (prompts <= 100K tokens).
# Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-24).
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = next((p for name, p in PRICING_PER_MTOK.items() if name in model_id), None)
    if prices is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
