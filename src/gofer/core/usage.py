from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class LlmPricing(BaseModel):
    input_cost_per_1k_tokens: float = 0.0
    output_cost_per_1k_tokens: float = 0.0
    chars_per_token: float = 4.0


class LlmUsageEstimate(BaseModel):
    provider: str
    profile: str | None = None
    model: str | None = None
    prompt_length: int
    output_length: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float
    duration_seconds: float
    estimated: bool = True
    source: str = "fallback_chars_per_token"


@dataclass
class LlmUsageTotals:
    agent_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    agent_time_seconds: float = 0.0

    def add(self, usage: LlmUsageEstimate) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens
        self.estimated_cost += usage.estimated_cost
        self.agent_time_seconds += usage.duration_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_calls": self.agent_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.estimated_cost,
            "agent_time_seconds": self.agent_time_seconds,
        }


def estimate_tokens(text: str | None, pricing: LlmPricing | None = None) -> int:
    if not text:
        return 0
    chars_per_token = (pricing or LlmPricing()).chars_per_token
    if chars_per_token <= 0:
        chars_per_token = 4.0
    return max(1, math.ceil(len(text) / chars_per_token))


def summarize_node_outputs(
    node_outputs: dict[str, Any],
    node_runs: dict[str, list[Any]] | None = None,
) -> dict[str, object]:
    totals = LlmUsageTotals()
    nodes: list[dict[str, object]] = []
    outputs: list[tuple[str, Any]] = []
    if node_runs is None:
        outputs = list(node_outputs.items())
    else:
        for node_id, runs in node_runs.items():
            outputs.extend((node_id, output) for output in runs)
        for node_id, output in node_outputs.items():
            if node_id not in node_runs:
                outputs.append((node_id, output))

    for node_id, output in outputs:
        data = output.get("data") if isinstance(output, dict) else getattr(output, "data", {})
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict):
            continue
        total_tokens = int(usage.get("total_tokens") or usage.get("totalTokens") or 0)
        input_tokens = int(usage.get("input_tokens") or usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("outputTokens") or 0)
        prompt_length = int(usage.get("prompt_length") or usage.get("promptLength") or 0)
        output_length = int(usage.get("output_length") or usage.get("outputLength") or 0)
        cost = float(usage.get("estimated_cost") or usage.get("estimatedCost") or 0.0)
        duration = float(usage.get("duration_seconds") or usage.get("durationSeconds") or 0.0)
        totals.agent_calls += 1
        totals.input_tokens += input_tokens
        totals.output_tokens += output_tokens
        totals.total_tokens += total_tokens
        totals.estimated_cost += cost
        totals.agent_time_seconds += duration
        nodes.append(
            {
                "node_id": node_id,
                "agent_id": data.get("agent_id"),
                "provider": usage.get("provider"),
                "profile": usage.get("profile"),
                "model": usage.get("model"),
                "prompt_length": prompt_length,
                "output_length": output_length,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost": cost,
                "duration_seconds": duration,
                "estimated": usage.get("estimated", True),
                "source": usage.get("source"),
            }
        )
    return {
        "totals": totals.to_dict(),
        "nodes": nodes,
        "most_expensive_nodes": sorted(nodes, key=_node_cost, reverse=True)[:5],
        "slowest_nodes": sorted(nodes, key=_node_duration, reverse=True)[:5],
    }


def _node_cost(node: dict[str, object]) -> float:
    value = node.get("estimated_cost")
    return float(value) if isinstance(value, int | float | str) else 0.0


def _node_duration(node: dict[str, object]) -> float:
    value = node.get("duration_seconds")
    return float(value) if isinstance(value, int | float | str) else 0.0
