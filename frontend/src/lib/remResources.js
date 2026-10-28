// Keep in sync with AgentResources defaults in src/gofer/core/prompt_envelope.py.
export const DEFAULT_REM_RESOURCES = { shell: true, web: true, agents: true, browser: false, skills: [], mcpServers: [] };

// Capture a complete, independent configuration before starting a provider turn.
// Explicit false values must survive defaults and later composer edits.
export function snapshotRemResources(resources) {
  return structuredClone({ ...DEFAULT_REM_RESOURCES, ...resources });
}

// Threads list the brains turned off, so every brain, including ones added
// later, starts on. Older threads stored the brains turned on instead. That list
// only existed after a choice, so an empty one means every brain was turned off.
export function disabledBrainIds(resources, brains) {
  if (Array.isArray(resources.disabledSecondBrains)) return resources.disabledSecondBrains;
  const chosen = resources.secondBrains;
  return Array.isArray(chosen) ? brains.filter((brain) => !chosen.includes(brain.id)).map((brain) => brain.id) : [];
}

// Rem sends registered brains to cloud providers only with permission. The
// Settings > Memory folder was already approved there and stays usable by path.
export function brainUsable(brain, legacyRoot) {
  return brain.state === "available" && (brain.settings?.allowCloud === true || brain.root === legacyRoot);
}

// Settings > Memory turns Second Brain on; each thread can then turn brains off.
// An empty brains list means the Settings > Memory folder, addressed by path.
// `served` lists the brains Rem actually receives: registered brains require
// cloud permission, so the folder reached by path is left out beside them.
export function selectThreadBrains(resources, brains, legacy) {
  if (!legacy.enabled) return { enabled: false, brains: [], served: [] };
  if (!brains.length) return { enabled: Boolean(legacy.root), brains: [], served: [] };
  const off = new Set(disabledBrainIds(resources, brains));
  const on = brains.filter((brain) => !off.has(brain.id) && brainUsable(brain, legacy.root));
  const cloud = on.filter((brain) => brain.settings?.allowCloud === true);
  return { enabled: on.length > 0, brains: cloud, served: (cloud.length ? cloud : on).map((brain) => brain.id) };
}

export function remToolSummary(config = DEFAULT_REM_RESOURCES) {
  const resources = { ...DEFAULT_REM_RESOURCES, ...config };
  const count = (items, one, many) => items.length ? `${items.length} ${items.length === 1 ? one : many}` : "";
  return [
    resources.shell ? "Commands" : "",
    resources.web ? "Web" : "",
    resources.agents ? "Subagents" : "",
    resources.browser ? "Browser" : "",
    count(resources.skills.filter((skill) => skill.enabled !== false), "skill", "skills"),
    count(resources.mcpServers.filter((server) => server.enabled !== false), "server", "servers"),
  ].filter(Boolean).join(" · ") || "No tools";
}

export function remResourceError(config = DEFAULT_REM_RESOURCES) {
  if ((config.skills || []).length > 100) return "Use at most 100 skills per thread.";
  if ((config.mcpServers || []).length > 100) return "Use at most 100 MCP servers per thread.";
  for (const skill of config.skills || []) {
    if (!skill.path?.trim()) return "Enter a folder for each skill, or remove the empty row.";
    if (skill.path.length > 4096) return "Skill paths must be at most 4096 characters.";
  }
  const names = new Set();
  for (const server of config.mcpServers || []) {
    if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(server.name || "")) return "Give each MCP server a name using letters, digits, hyphens, or underscores.";
    if (names.has(server.name)) return "MCP server names must be unique.";
    names.add(server.name);
    if ((server.command || "").length > 4096) return "MCP executable paths must be at most 4096 characters.";
    if ((server.args || []).length > 100) return "Use at most 100 arguments per MCP server.";
    if ((server.url || "").length > 2048) return "MCP server URLs must be at most 2048 characters.";
    if (server.type === "stdio") {
      if (!server.command?.trim()) return "Enter an executable for each local MCP server.";
      continue;
    }
    try {
      const url = new URL(server.url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash) throw new Error();
    } catch { return "Enter an HTTP or HTTPS server URL without credentials or fragments."; }
  }
  return "";
}
