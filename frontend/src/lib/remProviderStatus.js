// Whether the app Rem is set to use can run here, read from provider capabilities
// (GET /provider/capabilities). Settings › Rem and Rem home's welcome both use it, so a
// saved app that isn't installed, is turned off or needs sign-in is named, never swapped
// for the first app that works.

const NAMES = {
  codex: "Codex", claude_code: "Claude Code", cursor: "Cursor", copilot: "GitHub Copilot",
  opencode: "OpenCode", antigravity: "Antigravity", grok: "Grok",
};

// Short words for a select option, such as "Codex (not installed)".
export const PROVIDER_PROBLEM_LABELS = {
  missing: "not installed",
  off: "turned off",
  sign_in: "needs sign-in",
};

/** The app's display name, from capabilities when they list it. */
export function providerName(capabilities, id) {
  const listed = Array.isArray(capabilities) ? capabilities.find((item) => item.id === id) : null;
  return listed?.displayName || NAMES[id] || id;
}

/** True when Rem and the settings selects may use this app. */
export function providerUsable(provider) {
  return Boolean(provider?.available) && provider.enabled !== false;
}

/**
 * Why the saved app can't run here: { kind: "missing" | "off" | "sign_in", name }, or null
 * when it is fine or its state isn't known yet (capabilities still loading). An id the
 * capabilities don't list counts as not installed.
 */
export function providerProblem(capabilities, id) {
  if (!id || !Array.isArray(capabilities) || !capabilities.length) return null;
  const provider = capabilities.find((item) => item.id === id);
  const name = providerName(capabilities, id);
  if (!provider) return { kind: "missing", name };
  if (typeof provider.available !== "boolean") return null;
  if (!provider.available) return { kind: "missing", name };
  if (provider.enabled === false) return { kind: "off", name };
  if (provider.discoveryStatus === "unauthenticated") return { kind: "sign_in", name };
  return null;
}

/** The app that writes Rem's chat replies: its own chat provider, else the default provider. */
export function remChatProviderId(assistant) {
  return assistant?.chatProvider || assistant?.provider || "";
}

/** Rem home's welcome line for an app Rem can't use. */
export function remProviderProblemText(problem) {
  if (!problem) return "";
  if (problem.kind === "sign_in") return `Rem's app, ${problem.name}, needs you to sign in. Sign in from Settings › Providers, or choose another in Settings › Rem.`;
  if (problem.kind === "off") return `Rem's app, ${problem.name}, is turned off on this computer. Choose another in Settings › Rem.`;
  return `Rem's app, ${problem.name}, isn't installed on this computer. Choose another in Settings › Rem.`;
}
