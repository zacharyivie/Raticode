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

// Why a step passes over another account of a provider that hit its usage limit.
export const SKIPPED_ACCOUNT_TEXT =
  "Raticode doesn't move work to another account of the same provider at a usage limit";

// One sentence per switch or skipped entry, in the order the step made them.
export function fallbackLines(fallbacks) {
  if (!Array.isArray(fallbacks)) return [];
  return fallbacks
    .filter((item) => item?.from && item?.to)
    .map((item) => {
      if (item.skipped) return `Skipped ${providerLabel(item.to)}: ${SKIPPED_ACCOUNT_TEXT}.`;
      const reason = FALLBACK_REASON_LABELS[item.reason] ?? String(item.reason ?? "");
      return `Switched from ${providerLabel(item.from)} to ${providerLabel(item.to)}: ${reason}.`;
    });
}

// Whether a run's fallbacks include a real switch, not only skipped accounts.
export function switchedProvider(fallbacks) {
  return Array.isArray(fallbacks) && fallbacks.some((item) => item && !item.skipped);
}
