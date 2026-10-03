"""Editable context baselines for Rem provider handoffs, in tokens.

These are harness limits from the 2026-10-02 context-window report, not API
maximums. Unknown models use their provider's baseline. Copilot's default is
unverified, so it uses the same 200k fallback as unknown OpenCode models.
"""

from __future__ import annotations

from collections.abc import Iterator

PROVIDER_CONTEXT_TOKENS = {
    "codex": 272_000,
    "claude_code": 1_000_000,
    "cursor": 200_000,
    "copilot": 200_000,
    "opencode": 200_000,
    "antigravity": 1_048_576,
    "grok": 500_000,
}

# Match model families within qualified IDs such as openai/gpt-6.1-sol.
# Keep Cursor defaults distinct from its optional maximum context variants.
MODEL_CONTEXT_TOKENS: dict[str, tuple[tuple[str, int], ...]] = {
    "cursor": (
        ("opus", 300_000),
        ("sonnet", 200_000),
        ("gpt", 272_000),
        ("gemini", 200_000),
        ("grok", 256_000),
    ),
    "opencode": (
        ("gpt", 1_050_000),
        ("claude", 1_000_000),
        ("sonnet", 1_000_000),
        ("opus", 1_000_000),
        ("gemini", 1_048_576),
        ("grok", 500_000),
    ),
    "antigravity": (
        ("claude", 1_000_000),
        ("sonnet", 1_000_000),
        ("opus", 1_000_000),
    ),
}
HANDOFF_CONTEXT_PERCENT = 80


def provider_context_tokens(provider: str, model: str) -> int:
    """Return the baseline for the destination harness and model family."""
    normalized = model.lower()
    if provider == "opencode" and normalized.partition("/")[0] in {"copilot", "github-copilot"}:
        return PROVIDER_CONTEXT_TOKENS["copilot"]
    for family, tokens in MODEL_CONTEXT_TOKENS.get(provider, ()):
        if family in normalized:
            return tokens
    return PROVIDER_CONTEXT_TOKENS.get(provider, 200_000)


def handoff_token_budget(provider: str, model: str) -> int:
    return provider_context_tokens(provider, model) * HANDOFF_CONTEXT_PERCENT // 100


def estimate_tokens(text: str) -> int:
    """Napkin estimate: four UTF-8 bytes per token, rounded up.

    This avoids treating non-ASCII characters as single bytes. It is not a
    provider tokenizer and does not account for image or hidden native context.
    """
    return (len(text.encode("utf-8")) + 3) // 4


def token_chunks(text: str, budget: int) -> Iterator[str]:
    """Split summary input within the estimated budget without splitting Unicode."""
    if budget < 1:
        raise ValueError("Token chunk budget must be positive")
    encoded = text.encode("utf-8")
    start = 0
    while start < len(encoded):
        end = min(start + budget * 4, len(encoded))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        yield encoded[start:end].decode("utf-8")
        start = end
