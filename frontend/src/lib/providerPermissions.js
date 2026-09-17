export const PROVIDER_PERMISSIONS = {
  antigravity: [["default", "Strict resources, unavailable"], ["cli-managed", "CLI-managed permissions"]],
  grok: [["default", "Strict resources, unavailable"], ["cli-managed", "CLI-managed permissions"]],
  codex: [
    ["read-only", "Read Only", "Read files without changing them."],
    ["workspace-write", "Workspace Write", "Edit files in the workspace and permitted folders."],
    ["danger-full-access", "Full Access", "Run commands without the Codex sandbox."],
  ],
  claude_code: [
    ["default", "CLI default", "Use Claude Code's configured permissions."],
    ["manual", "Manual", "Use Claude Code's manual permission checks."],
    ["acceptEdits", "Accept Edits", "Automatically approve file edits."],
    ["auto", "Auto", "Let Claude Code review tool permissions automatically."],
    ["dontAsk", "Don't Ask", "Run allowed tools and deny tools that need approval."],
    ["plan", "Plan", "Explore and plan before making changes."],
    ["bypassPermissions", "Bypass Permissions", "Skip Claude Code permission checks."],
  ],
};

export function defaultPermissionMode(provider) {
  return provider === "grok" ? "cli-managed" : provider === "claude_code" ? "dontAsk" : provider === "codex" ? "workspace-write" : "default";
}

export function providerPermissionOptions(provider, capability) {
  return capability?.permissionModes?.length
    ? capability.permissionModes.map(mode => {
        const known = (PROVIDER_PERMISSIONS[provider] || []).find(([id]) => id === mode.id);
        return [mode.id, known?.[1] || permissionLabel(mode.displayName || mode.id), known?.[2]];
      })
    : PROVIDER_PERMISSIONS[provider] || [["default", "CLI default"]];
}

export function providerPermissionDefault(provider, capability) {
  return provider === "grok" ? "cli-managed" : capability?.defaultPermissionMode || defaultPermissionMode(provider);
}

function permissionLabel(value) {
  return value.replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, letter => letter.toUpperCase())
    .replace(/\bCli\b/g, "CLI");
}
