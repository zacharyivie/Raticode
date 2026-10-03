import pytest

from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.core.provider_context import (
    PROVIDER_CONTEXT_TOKENS,
    estimate_tokens,
    handoff_token_budget,
    provider_context_tokens,
    token_chunks,
)


def test_every_rem_provider_has_a_context_baseline():
    assert set(PROVIDER_CONTEXT_TOKENS) == set(CLI_PROVIDERS)


@pytest.mark.parametrize(
    ("provider", "model", "window", "budget"),
    [
        ("codex", "gpt-6.1-sol", 272_000, 217_600),
        ("claude_code", "claude-opus-5-5", 1_000_000, 800_000),
        ("cursor", "claude-sonnet-5-5", 200_000, 160_000),
        ("cursor", "claude-opus-5-5-thinking", 300_000, 240_000),
        ("cursor", "gpt-5.6-sol-high", 272_000, 217_600),
        ("cursor", "gemini-3.8-flash", 200_000, 160_000),
        ("cursor", "grok-4.7", 256_000, 204_800),
        ("opencode", "openai/gpt-6.1-sol", 1_050_000, 840_000),
        ("opencode", "anthropic/claude-sonnet-5", 1_000_000, 800_000),
        ("opencode", "google/gemini-3.8-flash", 1_048_576, 838_860),
        ("opencode", "xai/grok-4.7", 500_000, 400_000),
        ("copilot", "gpt-6.1-sol", 200_000, 160_000),
        ("antigravity", "gemini-3.1-pro-preview", 1_048_576, 838_860),
        ("antigravity", "claude-opus-5-5", 1_000_000, 800_000),
        ("grok", "grok-4.7", 500_000, 400_000),
        ("cursor", "cli-default", 200_000, 160_000),
        ("opencode", "custom-model", 200_000, 160_000),
        ("opencode", "github-copilot/gpt-6.1-sol", 200_000, 160_000),
    ],
)
def test_destination_context_and_eighty_percent_budget(provider, model, window, budget):
    assert provider_context_tokens(provider, model) == window
    assert handoff_token_budget(provider, model) == budget


def test_token_estimate_rounds_up_and_counts_unicode_bytes():
    assert estimate_tokens("") == 0
    assert estimate_tokens("12345") == 2
    assert estimate_tokens("猫猫") == 2


@pytest.mark.parametrize("budget", [1, 2, 7])
def test_summary_chunks_preserve_unicode_within_estimated_budget(budget):
    source = "abc猫🙂é" * 100
    chunks = list(token_chunks(source, budget))
    assert "".join(chunks) == source
    assert all(0 < estimate_tokens(chunk) <= budget for chunk in chunks)
