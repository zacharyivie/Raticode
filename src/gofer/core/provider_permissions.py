"""Permission flags shared by provider adapters and Rem."""

CODEX_PERMISSION_MODES = ("read-only", "workspace-write", "danger-full-access")
CLAUDE_PERMISSION_MODES = (
    "default",
    "acceptEdits",
    "auto",
    "manual",
    "dontAsk",
    "plan",
    "bypassPermissions",
)


def provider_permission_args(provider: str, mode: str | None) -> list[str]:
    if provider == "codex":
        mode = "workspace-write" if mode in (None, "default") else mode
        if mode not in CODEX_PERMISSION_MODES:
            raise ValueError(f"Unknown Codex permission mode '{mode}'")
        return ["--sandbox", mode]
    if provider == "claude_code":
        if mode is None or mode == "default":
            return []
        if mode not in CLAUDE_PERMISSION_MODES:
            raise ValueError(f"Unknown Claude Code permission mode '{mode}'")
        return ["--permission-mode", mode]
    if provider in {"antigravity", "grok"}:
        if mode in (None, "default", "cli-managed"):
            return []
        raise ValueError(f"{provider} does not support permission mode '{mode}'")
    if provider in {"cursor", "copilot", "opencode"}:
        if mode in (None, "default"):
            return []
        raise ValueError(f"{provider} does not support permission mode '{mode}'")
    raise ValueError(f"Unknown provider '{provider}'")
