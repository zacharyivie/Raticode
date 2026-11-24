const PROVIDER_NAMES = {
  codex: "Codex", claude: "Claude Code", claude_code: "Claude Code", cursor: "Cursor", copilot: "GitHub Copilot",
  opencode: "OpenCode", antigravity: "Antigravity", agy: "Antigravity", grok: "Grok",
  openai: "OpenAI API", anthropic: "Anthropic API",
  openai_api: "OpenAI API", anthropic_api: "Anthropic API",
  jev: "Jev (TypeSafe)",
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

/** A dollar amount for the API-price estimate. Missing stays "Unknown", never $0. */
export function formatUsd(value) {
  const number = usageNumber(value);
  if (number === null) return "Unknown";
  const size = Math.abs(number);
  if (size > 0 && size < 0.01) return number < 0 ? "−<$0.01" : "<$0.01";
  const text = size.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return number < 0 ? `−$${text}` : `$${text}`;
}

/** An estimate that leaves some calls out is a floor: "at least $3.20". */
export function costLabel(entry) {
  const amount = usageNumber(entry?.cost_usd);
  if (amount === null) return "Unknown";
  return `${entry.lower_bound ? "at least " : ""}${formatUsd(amount)}`;
}

// Mirrors BROWSER_LOGIN_COMMANDS in provider_capabilities.py.
const BROWSER_SIGN_IN = new Set(["claude_code", "codex", "cursor", "copilot", "grok"]);
const SIGN_IN_COMMANDS = {
  claude_code: "claude auth login", codex: "codex login", cursor: "cursor-agent login",
  copilot: "copilot login", opencode: "opencode auth login", antigravity: "agy", grok: "grok login",
};

/** How a user can sign in to a provider account, or null when the account needs no sign-in. */
export function usageSignIn(account) {
  if (account?.status !== "auth_required" || account.provider?.endsWith("_api")) return null;
  // A named profile has its own sign-in store; a default sign-in would not update it.
  if (account.profile) return { browser: false, command: null };
  return { browser: BROWSER_SIGN_IN.has(account.provider), command: SIGN_IN_COMMANDS[account.provider] || null };
}

function usageDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "resets in 2h 14m" for a window's reset time, counted from now. Empty when unknown. */
export function resetCountdown(resetsAt, now = Date.now()) {
  const date = usageDate(resetsAt);
  if (!date) return "";
  const seconds = (date.getTime() - now) / 1000;
  if (seconds <= 0) return "reset time passed";
  if (seconds < 60) return "resets in under a minute";
  const minutes = Math.floor(seconds / 60);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days) return `resets in ${days}d ${hours}h`;
  if (hours) return `resets in ${hours}h ${rest}m`;
  return `resets in ${rest}m`;
}

/** When an account was last checked, short and relative while it's recent. */
export function checkedLabel(observedAt, now = Date.now()) {
  const date = usageDate(observedAt);
  if (!date) return "Not checked yet";
  const minutes = Math.floor((now - date.getTime()) / 60000);
  if (minutes < 1) return "Checked just now";
  if (minutes < 60) return `Checked ${minutes} min ago`;
  if (minutes < 24 * 60) return `Checked ${Math.floor(minutes / 60)}h ago`;
  return `Checked ${formatUsageTime(observedAt)}`;
}

// The windows every Claude Code card shows side by side, read or not.
const CLAUDE_CARD_WINDOWS = [["five_hour", "5-hour"], ["seven_day", "Weekly"]];
// Accounts with nothing to show at a glance stay in the list below the cards.
const NO_CARD_STATUSES = new Set(["not_installed", "disabled", "unsupported"]);
export const SIGN_IN_LABELS = { signed_in: "Signed in", needs_sign_in: "Needs sign-in", unavailable: "Claude Code not found", unknown: "Sign-in not checked" };

/** A card's sign-in pill. "Not found" names the card's own app (Claude Code, Codex). */
export function signInLabel(state, providerName = "Claude Code") {
  if (state === "unavailable") return `${providerName || "The app"} not found`;
  return SIGN_IN_LABELS[state] || SIGN_IN_LABELS.unknown;
}

function shortWindowLabel(window) {
  return String(window.label || window.id || "Allowance").replace(/ allowance$/i, "");
}

/**
 * Settings > Usage cards, one per provider account. Each card keeps its own readings:
 * accounts can have different limits, so nothing here adds accounts together.
 * Claude Code cards always carry the 5-hour and weekly windows first, as two meters.
 */
export function usageCards(accounts = []) {
  return accounts.filter(account => {
    if (NO_CARD_STATUSES.has(account.status)) return false;
    const windows = account.windows || [];
    // Metered API rows and providers without an allowance report have nothing to show.
    return !(account.status === "unavailable" && !windows.length && !account.profile && account.provider !== "claude_code");
  }).map(account => {
    const windows = account.windows || [];
    const claude = account.provider === "claude_code";
    const fixed = claude ? CLAUDE_CARD_WINDOWS.map(([id, label]) => ({ id, label, window: windows.find(window => window.id === id) || null })) : [];
    const rest = windows.filter(window => !fixed.some(item => item.id === window.id))
      .map((window, index) => ({ id: `${window.id || "window"}:${window.model || ""}:${index}`, label: shortWindowLabel(window), window }));
    // Every provider account (Claude Code, Codex) reads its sign-in from its own app.
    const ownState = account.profile && account.sign_in_state ? account.sign_in_state : null;
    const signIn = claude ? account.sign_in_state || (account.status === "auth_required" ? "needs_sign_in" : "unknown") : ownState;
    return {
      id: account.id || `${account.provider}:${account.profile || ""}`,
      provider: account.provider,
      providerName: usageProviderName(account.provider),
      accountName: account.profile || (claude ? "Default sign-in" : "Default account"),
      profile: account.profile || null,
      claude,
      status: account.status,
      signIn,
      // An account's own status (`claude auth status`, Codex's account/read) is newer than its last usage reading.
      needsSignIn: ownState ? signIn === "needs_sign_in" : signIn === "needs_sign_in" || account.status === "auth_required",
      remNext: account.rem_next && typeof account.rem_next === "object" ? { why: String(account.rem_next.why || "") } : null,
      checkedAt: account.observed_at || null,
      reason: account.reason || "",
      meters: [...fixed, ...rest],
      account,
    };
  });
}

const PACE_TEXT = { not_enough: "Not enough readings yet", on_pace: "On pace to last until reset", used_up: "Used up until reset" };

/** "Thu 14:00" in this computer's time zone, or the zone given. */
export function paceTime(value, timeZone) {
  const date = usageDate(value);
  if (!date) return "";
  const parts = new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", ...(timeZone ? { timeZone } : {}) }).formatToParts(date);
  const part = type => parts.find(item => item.type === type)?.value || "";
  return `${part("weekday")} ${part("hour")}:${part("minute")}`;
}

/** The card's one line about the weekly window's pace, from the provider's own readings. */
export function paceText(pace, timeZone) {
  const state = pace?.state;
  if (state === "runs_out") {
    const at = paceTime(pace.runs_out_at, timeZone);
    return at ? `At this pace, runs out around ${at}` : String(pace.text || "");
  }
  return PACE_TEXT[state] || PACE_TEXT.not_enough;
}

/**
 * The account card's weekly burndown: percent left over the current window, as readings
 * the provider reported, beside an even-pace line from the window's start to its reset.
 * Null when the account has no weekly chart. Coordinates are 0..1 across and down.
 */
export function weeklyBurndown(account, timeZone) {
  const chart = account?.weekly_burndown;
  const start = usageDate(chart?.starts_at);
  const reset = usageDate(chart?.resets_at);
  if (!chart || !start || !reset || reset <= start) return null;
  const span = reset.getTime() - start.getTime();
  const x = time => Math.min(1, Math.max(0, (time - start.getTime()) / span));
  const y = used => Math.min(1, Math.max(0, used / 100));
  const readings = (Array.isArray(chart.readings) ? chart.readings : [])
    .map(reading => ({ at: usageDate(reading?.observed_at), used: Number(reading?.used_percent) }))
    .filter(reading => reading.at && Number.isFinite(reading.used))
    .sort((a, b) => a.at - b.at);
  // Readings are saved only when they change, so each one holds until the next. The last
  // holds until the provider last confirmed it.
  const checked = usageDate(account.observed_at);
  const points = [];
  readings.forEach((reading, index) => {
    if (index) points.push([x(reading.at.getTime()), points[points.length - 1][1]]);
    points.push([x(reading.at.getTime()), y(reading.used)]);
  });
  const last = readings[readings.length - 1];
  if (last && checked && checked > last.at) points.push([x(checked.getTime()), y(last.used)]);
  const pace = chart.pace && typeof chart.pace === "object" ? chart.pace : {};
  return {
    label: String(chart.label || "Weekly allowance").replace(/ allowance$/i, ""),
    points,
    count: Number.isInteger(chart.reading_count) ? chart.reading_count : readings.length,
    left: last ? Math.max(0, 100 - last.used) : null,
    state: (pace.state in PACE_TEXT || pace.state === "runs_out") ? pace.state : "not_enough",
    text: paceText(pace, timeZone),
    startsAt: start,
    resetsAt: reset,
  };
}
