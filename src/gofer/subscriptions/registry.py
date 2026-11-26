"""One provider runtime per provider, in the order `agent create --help` lists them.

Callers import this only where they run a provider, so `Raticode --version` and
unrelated commands never load the provider adapters.
"""

from __future__ import annotations

from functools import cache

from gofer.subscriptions.acp_providers import AcpSubscription
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription


def provider_subscriptions() -> dict[str, Subscription]:
    """Build a new runtime for each provider."""
    return {
        "claude_code": ClaudeCodeSubscription(),
        "codex": CodexSubscription(),
        **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
        "antigravity": AntigravitySubscription(),
        "grok": AcpSubscription("grok"),
        "openai_api": OpenAiApiSubscription(),
        "anthropic_api": AnthropicApiSubscription(),
    }


@cache
def shared_subscriptions() -> dict[str, Subscription]:
    """The runtimes queued runs and Second Brain research share within one process."""
    return provider_subscriptions()
