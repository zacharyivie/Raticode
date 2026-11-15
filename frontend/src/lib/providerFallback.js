// Which provider answered a model step and why it switched to a fallback provider.
// Mirrors gofer.rattish.provider_fallback so the Studio, CLI and phone read the same.

export const FALLBACK_REASON_LABELS = {
  rate_limit: "rate limit or allowance used up",
  overloaded: "service overloaded",
  signed_out: "sign-in expired",
  not_installed: "CLI not installed",
};

// "codex (gpt-5)", or "codex profile work (gpt-5)" when a profile is set.
export function providerLabel(choice) {
  if (!choice?.provider) return "";
  const name = choice.profile ? `${choice.provider} profile ${choice.profile}` : choice.provider;
  return choice.model ? `${name} (${choice.model})` : name;
}

// One sentence per switch, in the order the step made them.
export function fallbackLines(fallbacks) {
  if (!Array.isArray(fallbacks)) return [];
  return fallbacks
    .filter((item) => item?.from && item?.to)
    .map((item) => {
      const reason = FALLBACK_REASON_LABELS[item.reason] ?? String(item.reason ?? "");
      return `Switched from ${providerLabel(item.from)} to ${providerLabel(item.to)}: ${reason}.`;
    });
}
