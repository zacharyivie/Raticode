// Settings > Usage > Tokens in Raticode: periods, the saved choices and the stacked chart.
// Mirrors gofer/core/usage_history.py, which does the bucketing.
import { usageProviderName } from "./usage.js";
import { clockTime, dateTime, localTimeZone, monthName, shortDate } from "./timeText.js";

export const USAGE_RANGES = [
  { key: "24h", short: "24h", label: "Last 24 hours" },
  { key: "7d", short: "7d", label: "Last 7 days" },
  { key: "30d", short: "30d", label: "Last 30 days" },
  { key: "3mo", short: "3mo", label: "Last 3 months" },
  { key: "6mo", short: "6mo", label: "Last 6 months" },
  { key: "1y", short: "1Y", label: "Last year" },
  { key: "5y", short: "5Y", label: "Last 5 years" },
  { key: "all", short: "All", label: "All time" },
  { key: "custom", short: "Custom", label: "Custom range" },
];
const KEYS = new Set(USAGE_RANGES.map(range => range.key));
// Older saved values: day counts, where 90 ("Last 90 days") is now 3mo.
const LEGACY_DAYS = { 1: "24h", 7: "7d", 30: "30d", 90: "3mo", 180: "6mo", 365: "1y", 1825: "5y", 1826: "5y" };
export const USAGE_RANGE_STORAGE = "raticode.usage.tokenRange";
export const DEFAULT_USAGE_RANGE = Object.freeze({ key: "30d", start: "", end: "" });
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function validDate(value) {
  if (typeof value !== "string" || !DATE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** A saved or older period value as { key, start, end }; anything unreadable is the default. */
export function normalizeUsageRange(value) {
  if (typeof value === "number" || (typeof value === "string" && /^\d+$/.test(value))) {
    const key = LEGACY_DAYS[Number(value)];
    return key ? { key, start: "", end: "" } : { ...DEFAULT_USAGE_RANGE };
  }
  if (typeof value === "string") value = { key: value };
  if (!value || typeof value !== "object") return { ...DEFAULT_USAGE_RANGE };
  if (value.key === undefined && value.days !== undefined) return normalizeUsageRange(value.days);
  const key = value.key === "90d" ? "3mo" : value.key;
  if (!KEYS.has(key)) return { ...DEFAULT_USAGE_RANGE };
  if (key !== "custom") return { key, start: "", end: "" };
  if (!validDate(value.start) || !validDate(value.end) || value.start > value.end) return { ...DEFAULT_USAGE_RANGE };
  return { key, start: value.start, end: value.end };
}

export function loadUsageRange(storage = globalThis.window?.localStorage) {
  try {
    const raw = storage?.getItem(USAGE_RANGE_STORAGE);
    if (raw === null || raw === undefined) return { ...DEFAULT_USAGE_RANGE };
    let parsed;
    try { parsed = JSON.parse(raw); } catch { parsed = raw; }
    const range = normalizeUsageRange(parsed);
    // Rewrite older values (such as 90 days) in the current form.
    if (JSON.stringify(range) !== raw) saveUsageRange(range, storage);
    return range;
  } catch { return { ...DEFAULT_USAGE_RANGE }; }
}

export function saveUsageRange(range, storage = globalThis.window?.localStorage) {
  try { storage?.setItem(USAGE_RANGE_STORAGE, JSON.stringify(normalizeUsageRange(range))); } catch { /* Private mode or full storage: keep the choice in memory only. */ }
}

// The chart's split, By provider or By account, saved on this computer like the period.
export const USAGE_SPLITS = [
  { key: "provider", label: "By provider" },
  { key: "account", label: "By account" },
];
export const USAGE_SPLIT_STORAGE = "raticode.usage.chartSplit";
const DEFAULT_USAGE_SPLIT = "provider";

export function loadUsageSplit(storage = globalThis.window?.localStorage) {
  try {
    const saved = storage?.getItem(USAGE_SPLIT_STORAGE);
    return USAGE_SPLITS.some(split => split.key === saved) ? saved : DEFAULT_USAGE_SPLIT;
  } catch { return DEFAULT_USAGE_SPLIT; }
}

export function saveUsageSplit(split, storage = globalThis.window?.localStorage) {
  const value = USAGE_SPLITS.some(item => item.key === split) ? split : DEFAULT_USAGE_SPLIT;
  try { storage?.setItem(USAGE_SPLIT_STORAGE, value); } catch { /* Private mode or full storage: keep the choice in memory only. */ }
  return value;
}

export function localDateString(date = new Date()) {
  const pad = number => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Why a custom range can't be shown, or "" when it can. Dates are YYYY-MM-DD. */
export function customRangeError(start, end, today = localDateString()) {
  if (!start || !end) return "Choose a start and an end date.";
  if (!validDate(start) || !validDate(end)) return "Enter dates as YYYY-MM-DD.";
  if (start < "2000-01-01") return "The start date must be in 2000 or later.";
  if (start > end) return "The start date must be on or before the end date.";
  if (start > today) return "The start date can't be in the future.";
  return "";
}

/** The query string the usage endpoints take for a period. */
export function usageRangeQuery(range, timeZone = localTimeZone()) {
  const value = normalizeUsageRange(range);
  const params = new URLSearchParams({ range: value.key });
  if (value.key === "custom") { params.set("start", value.start); params.set("end", value.end); }
  if (timeZone) params.set("tz", timeZone);
  return params.toString();
}

export function usageRangeLabel(range) {
  const value = normalizeUsageRange(range);
  if (value.key === "custom") return `${shortDate(value.start) || value.start} to ${shortDate(value.end) || value.end}`;
  return USAGE_RANGES.find(item => item.key === value.key).label;
}

/** Short token counts for axes: 950, 12K, 3.4M, 1.2B. */
export function compactTokens(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "0";
  const steps = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
  for (const [size, suffix] of steps) {
    if (Math.abs(value) >= size) {
      const scaled = value / size;
      return `${scaled >= 100 ? Math.round(scaled) : Number(scaled.toFixed(1))}${suffix}`;
    }
  }
  return String(Math.round(value));
}

export function sharePercent(part, whole) {
  if (!whole) return "0%";
  const percent = 100 * part / whole;
  if (percent > 0 && percent < 1) return "<1%";
  return `${Math.round(percent)}%`;
}

// Wall-clock text from the backend's ISO stamp, without shifting it into this computer's zone.
function wallClock(stamp) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(stamp || "");
  if (!match) return null;
  const [, year, month, day, hour = "00", minute = "00"] = match;
  return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute));
}

/** A bucket's name in words, short for the axis or long for tooltips and the table. */
export function bucketLabel(bucket, kind, long = false) {
  const start = wallClock(bucket?.start);
  if (!start) return "";
  if (kind === "hour") return long ? dateTime(start) : clockTime(start);
  if (kind === "week") return long ? `Week of ${shortDate(start)}` : shortDate(start);
  if (kind === "month") return monthName(start, { long });
  if (kind === "year") return String(start.getFullYear());
  return shortDate(start, { weekday: long });
}

// Patterns, by position, for a provider the backend sent no style for. Those are drawn gray.
// Every other provider's brand colors and pattern come with the series, from the one palette
// in gofer/core/provider_colors.py, which the phone reads too.
const FALLBACK_PATTERNS = ["stripes", "dots", "horizontal", "vertical", "cross", "grid", "back-stripes", "checks", "rings", "solid"];
const PATTERNS = new Set([...FALLBACK_PATTERNS]);
const HEX = /^#[0-9a-f]{6}$/i;

/** A provider's chart style: colors for dark and light themes, a pattern, and ink for the pattern. */
export function seriesStyle(style, index) {
  const valid = style && HEX.test(style.dark) && HEX.test(style.light) && PATTERNS.has(style.pattern);
  const { dark, light, pattern } = valid ? style : { dark: "#8c8c8c", light: "#6e6e6e", pattern: FALLBACK_PATTERNS[index % FALLBACK_PATTERNS.length] };
  return { dark, light, pattern, darkInk: patternInk(dark), lightInk: patternInk(light) };
}

/** Pattern lines that show on a fill: dark on light colors such as Cursor's cream, white otherwise. */
export function patternInk(hex) {
  const [r, g, b] = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16) / 255)
    .map(channel => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 ? "rgb(0 0 0 / .35)" : "rgb(255 255 255 / .45)";
}

/** The CSS variables a segment or swatch reads its colors from, for both themes. */
export function seriesStyleVars(style) {
  if (!style) return undefined;
  return { "--series-dark": style.dark, "--series-light": style.light, "--series-ink-dark": style.darkInk, "--series-ink-light": style.lightInk };
}

/**
 * Bars, segments, scale and legend for a stacked token chart from the backend's series.
 * ``split`` "account" stacks each provider account (series.accounts) instead of each provider;
 * a series from an older backend without accounts stays split by provider.
 */
export function tokenChartModel(series, split = DEFAULT_USAGE_SPLIT) {
  const kind = series?.bucket || "day";
  const byAccount = split === "account" && Array.isArray(series?.accounts);
  const source = byAccount ? series.accounts : series?.providers || [];
  const entries = source.map((entry, index) => {
    const provider = entry.provider;
    const name = byAccount ? entry.name || entry.profile || "Default account" : "";
    return {
      key: byAccount ? String(entry.key ?? `${provider}:${entry.profile || ""}`) : provider,
      provider,
      name,
      label: byAccount ? `${usageProviderName(provider)} · ${name}` : usageProviderName(provider),
      total: typeof entry.total_tokens === "number" ? entry.total_tokens : null,
      calls: entry.calls || 0,
      style: seriesStyle(entry.style, index),
    };
  });
  const field = byAccount ? "accounts" : "providers";
  const grand = entries.reduce((sum, entry) => sum + (entry.total || 0), 0);
  const bars = (series?.buckets || []).map(bucket => {
    const total = bucket.total_tokens || 0;
    const segments = entries
      .map(entry => ({ key: entry.key, provider: entry.provider, label: entry.label, tokens: bucket[field]?.[entry.key] || 0 }))
      .filter(segment => segment.tokens > 0)
      .map(segment => ({ ...segment, share: total ? segment.tokens / total : 0 }));
    return { key: bucket.key || bucket.start, start: bucket.start, end: bucket.end, total, calls: bucket.calls || 0, unknownCalls: bucket.unknown_calls || 0, segments };
  });
  const peak = Math.max(0, ...bars.map(bar => bar.total));
  const max = niceCeiling(peak);
  return {
    kind, bars, max, grand,
    split: byAccount ? "account" : "provider",
    ticks: max ? [0, max / 4, max / 2, (3 * max) / 4, max] : [0],
    legend: entries.map(entry => ({ ...entry, share: grand && entry.total ? entry.total / grand : 0 })),
    // About eight axis labels however many bars there are.
    labelEvery: Math.max(1, Math.ceil(bars.length / 8)),
    empty: !bars.some(bar => bar.total > 0),
  };
}

function niceCeiling(value) {
  if (!value) return 0;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.25, 2, 2.5, 4, 5, 8, 10].find(multiple => multiple * power >= value);
  return step * power;
}
