import assert from "node:assert/strict";
import test from "node:test";
import { defaultPermissionMode, providerPermissionDefault, providerPermissionOptions } from "./providerPermissions.js";

test("discovered permission IDs retain readable labels for every provider", () => {
  for (const [provider, id, label] of [
    ["codex", "danger-full-access", "Full Access"],
    ["codex", "workspace-write", "Workspace Write"],
    ["claude_code", "bypassPermissions", "Bypass Permissions"],
    ["claude_code", "dontAsk", "Don't Ask"],
    ["cursor", "cli-managed", "CLI Managed"],
    ["copilot", "allow-all-tools", "Allow All Tools"],
    ["opencode", "read_only", "Read Only"],
    ["antigravity", "cli-managed", "CLI-managed permissions"],
    ["grok", "default", "Strict resources, unavailable"],
  ]) {
    const options = providerPermissionOptions(provider, { permissionModes: [{ id, displayName: id }] });
    assert.equal(options[0][0], id);
    assert.equal(options[0][1], label);
  }
});

test("Grok defaults to CLI-managed even with a stale capability default", () => {
  assert.equal(defaultPermissionMode("grok"), "cli-managed");
  assert.equal(providerPermissionDefault("grok"), "cli-managed");
  assert.equal(providerPermissionDefault("grok", { defaultPermissionMode: "default" }), "cli-managed");
  assert.equal(providerPermissionDefault("codex", { defaultPermissionMode: "workspace-write" }), "workspace-write");
});
