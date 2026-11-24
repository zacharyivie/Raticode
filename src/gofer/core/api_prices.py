"""Public API list prices for the models Raticode's usage ledger records.

This is the one price table. Settings > Usage, the phone's Usage page and
``Raticode usage show`` all estimate what Raticode's tokens would have cost at API prices
from it. To update a price, edit its row: every row names its source page and the date it
was read. ``tests/unit/test_api_prices.py`` checks each row is complete and dated.

Prices are US dollars per million tokens, standard tier, no batch or priority discount.

* ``input``: uncached input. ``cache_read``: input served from the prompt cache.
* ``cache_write`` and ``cache_write_long``: input written to the cache for the provider's
  short and long lifetimes (Anthropic: 5 minutes and 1 hour; OpenAI: 30 minutes and
  12 hours). None means the provider charges no write premium: written input costs what
  uncached input does.
* ``cache_read`` None means the provider has no cached-input discount.

What a row can't capture, and the estimate ignores (the UI says so):

* Long-context surcharges apply per request above a prompt size, but the ledger records one
  sum per agent turn, which holds many requests. Rows use the standard tier.
* Google bills cache storage per hour, and some providers bill tool calls such as web
  search per request. Neither is in the ledger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The date the table as a whole was last checked against the sources.
PRICES_CHECKED = "2026-11-18"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    vendor: str
    model: str
    input: float
    output: float
    cache_read: float | None
    cache_write: float | None
    cache_write_long: float | None
    source: str
    checked: str
    aliases: tuple[str, ...] = ()
    note: str = ""

    @property
    def caches(self) -> bool:
        """False for a model with no prompt cache, whose input never has a cache split."""
        return not (self.cache_read is None and self.cache_write is None)

    @property
    def cache_read_price(self) -> float:
        return self.input if self.cache_read is None else self.cache_read

    @property
    def cache_write_price(self) -> float:
        return self.input if self.cache_write is None else self.cache_write

    @property
    def cache_write_long_price(self) -> float:
        if self.cache_write_long is not None:
            return self.cache_write_long
        return self.cache_write_price

    def payload(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "model": self.model,
            "aliases": list(self.aliases),
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "cache_write_long": self.cache_write_long,
            "source": self.source,
            "checked": self.checked,
            "note": self.note,
        }


_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
_OPENAI = "https://developers.openai.com/api/docs/pricing"
_GOOGLE = "https://aistudio.google.com/docs/pricing"
_XAI = "https://docs.x.ai/developers/pricing"
_CURSOR = "https://cursor.com/docs/models-and-pricing"
_OPENCODE = "https://opencode.ai/docs/zen"
_TYPESAFE = "https://docs.typesafe.ai/models"
_DAY = "2026-11-18"

# fmt: off
PRICES: tuple[ModelPrice, ...] = (
    # Anthropic: writes are 1.25x input for 5 minutes and 2x for 1 hour.
    ModelPrice("anthropic", "claude-opus-5-5", 4, 20, 0.20, 5, 8, _ANTHROPIC, _DAY,
               aliases=("opus",), note="1M context at the standard price"),
    ModelPrice("anthropic", "claude-sonnet-5-5", 2, 10, 0.10, 2.50, 4, _ANTHROPIC, _DAY,
               aliases=("sonnet",), note="1M context at the standard price"),
    ModelPrice("anthropic", "claude-haiku-5-5", 0.10, 0.50, 0.01, 0.125, 0.20, _ANTHROPIC,
               _DAY, aliases=("haiku",),
               note="Prompts over 100K tokens cost 5x (0.50 / 2.50 / 0.05 / 0.625 / 1)"),
    ModelPrice("anthropic", "claude-haiku-4-5", 1, 5, 0.10, 1.25, 2, _ANTHROPIC, _DAY),
    ModelPrice("anthropic", "claude-opus-5", 5, 25, 0.50, 6.25, 10, _ANTHROPIC, _DAY),
    ModelPrice("anthropic", "claude-opus-4-8", 5, 25, 0.50, 6.25, 10, _ANTHROPIC, _DAY,
               aliases=("claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5")),
    ModelPrice("anthropic", "claude-sonnet-5", 2, 10, 0.20, 2.50, 4, _ANTHROPIC, _DAY),
    ModelPrice("anthropic", "claude-sonnet-4-6", 3, 15, 0.30, 3.75, 6, _ANTHROPIC, _DAY,
               aliases=("claude-sonnet-4-5",)),
    ModelPrice("anthropic", "claude-fable-5-2", 8, 40, 0.20, 10, 16, _ANTHROPIC, _DAY,
               note="Limited availability"),
    ModelPrice("anthropic", "claude-fable-5-1", 10, 50, 0.25, 12.50, 20, _ANTHROPIC, _DAY,
               note="Limited availability"),
    ModelPrice("anthropic", "claude-fable-5", 10, 50, 1, 12.50, 20, _ANTHROPIC, _DAY,
               note="Limited availability"),
    # OpenAI: writes are 1.25x input for 30 minutes and 2.5x for 12 hours.
    ModelPrice("openai", "gpt-6.2-astra", 8, 40, 0.40, 10, 20, _OPENAI, _DAY),
    ModelPrice("openai", "gpt-6.2-sol", 2, 10, 0.10, 2.50, 5, _OPENAI, _DAY),
    ModelPrice("openai", "gpt-6.1-sol", 2, 10, 0.10, 2.50, 5, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-6.1-terra", 1, 5, 0.05, 1.25, 2.50, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-6-astra", 10, 50, 1, 12.50, 25, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-6-luna", 0.10, 0.50, 0.01, 0.125, 0.25, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-6-sol", 2, 10, 0.20, 2.50, 5, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-5.6-sol", 4, 20, 0.40, 5, 10, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-5.6-terra", 2, 12, 0.20, 2.50, 5, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-5.6-luna", 0.20, 1.20, 0.02, 0.25, 0.50, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    # GPT-5.5 and earlier charge nothing extra to write the cache.
    ModelPrice("openai", "gpt-5.5", 5, 30, 0.50, None, None, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    ModelPrice("openai", "gpt-5.4", 2.50, 15, 0.25, None, None, _OPENAI, _DAY,
               note="Prompts over 272K tokens cost 2x input, 1.5x output"),
    # Google: cached reads plus hourly storage, which the ledger doesn't record.
    ModelPrice("google", "gemini-3.8-flash", 0.75, 3.75, 0.075, None, None, _GOOGLE, _DAY,
               note="Doubles on 2027-01-01. Cache storage $0.50 per million tokens per hour"),
    ModelPrice("google", "gemini-3.1-pro-preview", 2, 12, 0.20, None, None, _GOOGLE, _DAY,
               note="Prompts over 200K tokens: 4 / 18 / 0.40. Cache storage $4.50/M/hour"),
    ModelPrice("google", "gemini-4-carbon", 2, 10, 0.10, None, None, _GOOGLE, _DAY,
               note="Doubles on 2026-12-01. Implicit caching, no storage fee"),
    # xAI caches automatically, with no write charge.
    ModelPrice("xai", "grok-build-0.1", 1, 2, 0.20, None, None, _XAI, _DAY,
               aliases=("grok-code-fast-1",), note="Prompts of 200K tokens or more cost 2x"),
    ModelPrice("xai", "grok-4.7", 2, 6, 0.50, None, None, _XAI, _DAY,
               aliases=("grok-4.6",), note="Prompts of 200K tokens or more cost 2x"),
    ModelPrice("xai", "grok-4.5", 2, 6, 0.30, None, None, _XAI, _DAY,
               note="Prompts of 200K tokens or more cost 2x"),
    ModelPrice("xai", "grok-4.3", 1.25, 2.50, 0.20, None, None, _XAI, _DAY,
               note="Prompts of 200K tokens or more cost 2x"),
    # Cursor's own model; Auto has no per-token list price, so it stays unknown.
    ModelPrice("cursor", "composer-2.5", 0.50, 2.50, 0.20, None, None, _CURSOR, _DAY,
               note="Fast mode costs more (3 / 15 / 0.50)"),
    ModelPrice("opencode", "opencode/big-pickle", 0, 0, 0, None, None, _OPENCODE, _DAY,
               aliases=("big-pickle",), note="Free for a limited time"),
    # Jev bills input only.
    ModelPrice("typesafe", "jev-1.14.0", 0.042, 0, None, None, None, _TYPESAFE, _DAY,
               aliases=("jev-latest",), note="Billed on input only; output is free"),
)
# fmt: on

# Which vendor's price a Raticode provider's unprefixed model names refer to. A provider
# missing here (GitHub Copilot bills premium requests, not tokens) has no token prices.
PROVIDER_VENDORS: dict[str, str] = {
    "claude_code": "anthropic",
    "anthropic_api": "anthropic",
    "codex": "openai",
    "openai_api": "openai",
    "antigravity": "google",
    "grok": "xai",
    "cursor": "cursor",
    "opencode": "opencode",
    "jev": "typesafe",
}
# OpenCode and other routers name models "vendor/model".
_PREFIX_VENDORS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "gemini": "google",
    "xai": "xai",
    "x-ai": "xai",
    "opencode": "opencode",
}
_SUFFIX = re.compile(
    r"(\[[^\]]*\]|-(?:\d{8}|\d{4}-\d{2}-\d{2})|-(?:minimal|low|medium|high|xhigh|max))$"
)
_INDEX: dict[tuple[str, str], ModelPrice] = {}


def _index() -> dict[tuple[str, str], ModelPrice]:
    if not _INDEX:
        for row in PRICES:
            for name in (row.model, *row.aliases):
                _INDEX[(row.vendor, name.lower())] = row
    return _INDEX


def model_key(model: str) -> str:
    """A model name as the table spells it: lower case, without [1m], dates or effort."""
    name = model.strip().lower()
    while True:
        trimmed = _SUFFIX.sub("", name)
        if trimmed == name:
            return name
        name = trimmed


def price_for(provider: str, model: str | None) -> ModelPrice | None:
    """The list price a call's model would have cost, or None when Raticode can't tell."""
    if not model:
        return None
    vendor = PROVIDER_VENDORS.get(provider)
    name = model.strip()
    if "/" in name:
        prefix, rest = name.split("/", 1)
        vendor = _PREFIX_VENDORS.get(prefix.lower(), vendor)
        # OpenCode's own models keep their prefix in the table ("opencode/big-pickle").
        if vendor != "opencode":
            name = rest
    if vendor is None:
        return None
    table = _index()
    for candidate in (name.lower(), model_key(name)):
        found = table.get((vendor, candidate))
        if found is not None:
            return found
    return None
