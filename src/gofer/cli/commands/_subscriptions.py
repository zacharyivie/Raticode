"""Provider runtimes for the legacy agent and workflow commands.

Command modules import this only inside the commands that run a provider, so
`Raticode --version` and unrelated commands never load the provider adapters.
"""

from __future__ import annotations

from gofer.subscriptions.acp_providers import AcpSubscription
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription


def cli_subscriptions() -> dict[str, Subscription]:
    """Build one runtime per provider. `agent create --help` lists them in this order."""
    return {
        "claude_code": ClaudeCodeSubscription(),
        "codex": CodexSubscription(),
        **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
        "antigravity": AntigravitySubscription(),
        "grok": AcpSubscription("grok"),
        "openai_api": OpenAiApiSubscription(),
        "anthropic_api": AnthropicApiSubscription(),
    }
