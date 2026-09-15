"""Provider runtime adapters shared by Rattish preflight and execution."""

from __future__ import annotations

from collections.abc import Mapping

from gofer.core.provider_profiles import ProfileSubscription
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription


def runtime_subscription_id(provider_id: str) -> ProfileSubscription:
    """Map portable kebab-case provider IDs to existing runtime subscription IDs."""
    subscriptions: dict[str, ProfileSubscription] = {
        "anthropic-api": "anthropic_api",
        "claude-code": "claude_code",
        "codex": "codex",
        "openai-api": "openai_api",
    }
    try:
        return subscriptions[provider_id]
    except KeyError as exc:
        raise ValueError(f"Provider {provider_id!r} has no installed runtime mapping.") from exc


def default_provider_subscriptions() -> Mapping[str, Subscription]:
    """Construct the provider runtimes supported by this Raticode installation."""
    return {
        "anthropic_api": AnthropicApiSubscription(),
        "claude_code": ClaudeCodeSubscription(),
        "codex": CodexSubscription(),
        "openai_api": OpenAiApiSubscription(),
    }
