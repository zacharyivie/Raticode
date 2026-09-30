const PROVIDER_NAMES = {
  codex: "Codex", claude: "Claude Code", claude_code: "Claude Code", cursor: "Cursor", copilot: "GitHub Copilot",
  opencode: "OpenCode", antigravity: "Antigravity", agy: "Antigravity", grok: "Grok",
  openai: "OpenAI API", anthropic: "Anthropic API",
  openai_api: "OpenAI API", anthropic_api: "Anthropic API",
};

export function usageProviderName(provider) {
  return PROVIDER_NAMES[provider] || provider || "Unknown provider";
}

export function usageNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function formatUsageNumber(value) {
  const number = usageNumber(value);
  return number === null ? "Not reported" : number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function formatUsageAmount(value, unit) {
  if (usageNumber(value) === null) return "Not reported";
  if (unit === "percent" || unit === "%") return `${formatUsageNumber(value)}%`;
  if (unit === "usd" || unit === "USD") return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  return `${formatUsageNumber(value)}${unit ? ` ${unit}` : ""}`;
}

export function remainingPercent(window) {
  const reported = usageNumber(window.remaining_percent);
  if (reported !== null) return Math.max(0, Math.min(100, reported));
  const remaining = usageNumber(window.remaining);
  const limit = usageNumber(window.limit);
  if (remaining === null || limit === null || limit <= 0) return null;
  return Math.max(0, Math.min(100, 100 * remaining / limit));
}

export function allowanceLabel(window) {
  if (window.unlimited) return "Unlimited";
  if (usageNumber(window.remaining) !== null) return `${formatUsageAmount(window.remaining, window.unit)} remaining`;
  if (usageNumber(window.remaining_percent) !== null) return `${formatUsageNumber(window.remaining_percent)}% remaining`;
  if (usageNumber(window.used) !== null) return `${formatUsageAmount(window.used, window.unit)} used`;
  return "Not reported";
}

export function usageStatus(status) {
  return {
    ready: "Up to date", available: "Up to date", ok: "Up to date", fresh: "Up to date",
    stale: "Out of date", unavailable: "Not available", unsupported: "Not available",
    auth_required: "Sign in required", authentication_required: "Sign in required",
    not_installed: "Not installed", disabled: "Disabled", error: "Could not refresh",
    pending: "Awaiting usage", not_checked: "Not checked", configuration_required: "Set up reporting", unknown: "Not reported",
  }[status] || "Not reported";
}

export function formatUsageTime(value) {
  if (!value) return "Not reported";
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "Not reported" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function usageDashboardUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch { return null; }
}
