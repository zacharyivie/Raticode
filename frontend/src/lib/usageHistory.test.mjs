import assert from "node:assert/strict";
import test from "node:test";
import {
  bucketLabel, compactTokens, customRangeError, DEFAULT_USAGE_RANGE, loadUsageRange, normalizeUsageRange,
  loadUsageSplit, patternInk, saveUsageRange, saveUsageSplit, seriesStyle, seriesStyleVars, sharePercent, tokenChartModel,
  USAGE_RANGE_STORAGE, USAGE_RANGES, USAGE_SPLIT_STORAGE, usageRangeLabel, usageRangeQuery,
} from "./usageHistory.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: key => (values.has(key) ? values.get(key) : null), setItem: (key, value) => values.set(key, String(value)), values };
}

test("the period list is 24h to 30d, then 3mo, 6mo, 1Y, 5Y, All and Custom", () => {
  assert.deepEqual(USAGE_RANGES.map(range => range.short), ["24h", "7d", "30d", "3mo", "6mo", "1Y", "5Y", "All", "Custom"]);
  assert.equal(USAGE_RANGES.find(range => range.key === "3mo").label, "Last 3 months");
});

test("a saved 90-day period becomes 3mo and is rewritten", () => {
  for (const saved of ["90", JSON.stringify(90), JSON.stringify({ days: 90 }), JSON.stringify({ key: "90d" })]) {
    const storage = memoryStorage({ [USAGE_RANGE_STORAGE]: saved });
    assert.deepEqual(loadUsageRange(storage), { key: "3mo", start: "", end: "" });
    assert.equal(storage.values.get(USAGE_RANGE_STORAGE), JSON.stringify({ key: "3mo", start: "", end: "" }));
  }
  assert.equal(normalizeUsageRange(30).key, "30d");
  assert.equal(normalizeUsageRange(1).key, "24h");
});

test("the chosen period round-trips and bad values fall back to 30 days", () => {
  const storage = memoryStorage();
  assert.deepEqual(loadUsageRange(storage), DEFAULT_USAGE_RANGE);
  saveUsageRange({ key: "6mo" }, storage);
  assert.deepEqual(loadUsageRange(storage), { key: "6mo", start: "", end: "" });
  saveUsageRange({ key: "custom", start: "2026-01-01", end: "2026-02-01" }, storage);
  assert.deepEqual(loadUsageRange(storage), { key: "custom", start: "2026-01-01", end: "2026-02-01" });
  for (const bad of ["{oops", JSON.stringify({ key: "2y" }), JSON.stringify({ key: "custom", start: "2026-02-01", end: "2026-01-01" }), JSON.stringify(45)]) {
    assert.deepEqual(loadUsageRange(memoryStorage({ [USAGE_RANGE_STORAGE]: bad })), DEFAULT_USAGE_RANGE);
  }
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.deepEqual(loadUsageRange(broken), DEFAULT_USAGE_RANGE);
  assert.doesNotThrow(() => saveUsageRange({ key: "1y" }, broken));
});

test("custom ranges are checked before they are asked for", () => {
  assert.equal(customRangeError("", "2026-01-01", "2026-11-16"), "Choose a start and an end date.");
  assert.equal(customRangeError("2026-02-30", "2026-03-01", "2026-11-16"), "Enter dates as YYYY-MM-DD.");
  assert.equal(customRangeError("2026-03-02", "2026-03-01", "2026-11-16"), "The start date must be on or before the end date.");
  assert.equal(customRangeError("2026-12-01", "2026-12-02", "2026-11-16"), "The start date can't be in the future.");
  assert.equal(customRangeError("1999-01-01", "2026-01-01", "2026-11-16"), "The start date must be in 2000 or later.");
  assert.equal(customRangeError("2026-01-01", "2026-01-01", "2026-11-16"), "");
});

test("the request names the period, its dates and the viewer's time zone", () => {
  assert.equal(usageRangeQuery({ key: "5y" }, "America/New_York"), "range=5y&tz=America%2FNew_York");
  assert.equal(usageRangeQuery({ key: "custom", start: "2026-01-01", end: "2026-01-31" }, ""), "range=custom&start=2026-01-01&end=2026-01-31");
  assert.equal(usageRangeQuery(90, ""), "range=3mo");
  assert.equal(usageRangeLabel({ key: "all" }), "All time");
});

test("token numbers and shares stay short and honest", () => {
  assert.equal(compactTokens(950), "950");
  assert.equal(compactTokens(12_400), "12.4K");
  assert.equal(compactTokens(3_400_000), "3.4M");
  assert.equal(compactTokens(250_000_000), "250M");
  assert.equal(compactTokens(1_200_000_000), "1.2B");
  assert.equal(sharePercent(1, 1000), "<1%");
  assert.equal(sharePercent(0, 0), "0%");
  assert.equal(sharePercent(1, 3), "33%");
});

test("the chart stacks providers by their share and keeps empty buckets", () => {
  const model = tokenChartModel({
    bucket: "week",
    providers: [
      { provider: "claude_code", total_tokens: 700, calls: 3, style: { dark: "#D97757", light: "#C6613F", pattern: "solid" } },
      { provider: "codex", total_tokens: 300, calls: 2, style: { dark: "#10A37F", light: "#0D8A6B", pattern: "stripes" } },
      { provider: "cursor", total_tokens: null, calls: 1 },
    ],
    buckets: [
      { key: "2026-11-02", start: "2026-11-02T00:00:00-05:00", total_tokens: 400, calls: 2, unknown_calls: 0, providers: { codex: 100, claude_code: 300 } },
      { key: "2026-11-09", start: "2026-11-09T00:00:00-05:00", total_tokens: 0, calls: 0, unknown_calls: 0, providers: {} },
      { key: "2026-11-16", start: "2026-11-16T00:00:00-05:00", total_tokens: 600, calls: 4, unknown_calls: 1, providers: { claude_code: 400, codex: 200 } },
    ],
  });
  assert.equal(model.kind, "week");
  assert.equal(model.bars.length, 3);
  // Segments follow the legend order (largest provider first) and their shares add up.
  assert.deepEqual(model.bars[0].segments.map(segment => [segment.provider, segment.tokens, segment.share]), [["claude_code", 300, 0.75], ["codex", 100, 0.25]]);
  assert.deepEqual(model.bars[1].segments, []);
  assert.equal(model.bars[2].unknownCalls, 1);
  assert.ok(model.max >= 600 && model.max <= 1000);
  assert.equal(model.legend[0].share, 0.7);
  assert.equal(model.legend[2].total, null);
  // Each provider gets a different pattern, so color is never the only cue.
  assert.equal(new Set(model.legend.map(entry => entry.style.pattern)).size, 3);
  // Brand colors come from the backend's palette, for both themes; pattern ink suits the fill.
  assert.deepEqual(model.legend[0].style, { dark: "#D97757", light: "#C6613F", pattern: "solid", darkInk: "rgb(255 255 255 / .45)", lightInk: "rgb(255 255 255 / .45)" });
  // A provider sent without a style is gray, with a pattern by its position.
  assert.equal(model.legend[2].style.dark, "#8c8c8c");
  assert.equal(model.legend[2].style.pattern, "horizontal");
  assert.equal(model.empty, false);
  assert.equal(tokenChartModel({ bucket: "day", providers: [], buckets: [{ key: "a", start: "2026-11-01", total_tokens: 0, providers: {} }] }).empty, true);
});

test("chart styles keep only well-formed colors and pick readable pattern ink", () => {
  assert.equal(seriesStyle({ dark: "red", light: "#000000", pattern: "solid" }, 0).dark, "#8c8c8c");
  assert.equal(seriesStyle({ dark: "#000000", light: "#000000", pattern: "url(x)" }, 1).pattern, "dots");
  // Cursor's cream needs dark pattern lines; its dark ink on light themes keeps white ones.
  const cursor = seriesStyle({ dark: "#F7F7F4", light: "#26251E", pattern: "dots" }, 0);
  assert.equal(cursor.darkInk, "rgb(0 0 0 / .35)");
  assert.equal(cursor.lightInk, "rgb(255 255 255 / .45)");
  assert.deepEqual(seriesStyleVars(cursor), { "--series-dark": "#F7F7F4", "--series-light": "#26251E", "--series-ink-dark": "rgb(0 0 0 / .35)", "--series-ink-light": "rgb(255 255 255 / .45)" });
  assert.equal(patternInk("#10A37F"), "rgb(255 255 255 / .45)");
  assert.equal(seriesStyleVars(undefined), undefined);
});

test("bucket names read in the viewer's words without shifting the day", () => {
  assert.match(bucketLabel({ start: "2026-11-02T00:00:00-05:00" }, "week", true), /^Week of Nov 2, 2026$/);
  assert.match(bucketLabel({ start: "2026-11-01T00:00:00+14:00" }, "month", true), /November 2026/);
  assert.equal(bucketLabel({ start: "2026-01-01T00:00:00+00:00" }, "year"), "2026");
  assert.match(bucketLabel({ start: "2026-11-16T15:00:00-05:00" }, "hour"), /3/);
});

test("the By provider / By account choice is saved and bad values fall back to By provider", () => {
  const storage = memoryStorage();
  assert.equal(loadUsageSplit(storage), "provider");
  assert.equal(saveUsageSplit("account", storage), "account");
  assert.equal(storage.values.get(USAGE_SPLIT_STORAGE), "account");
  assert.equal(loadUsageSplit(storage), "account");
  assert.equal(loadUsageSplit(memoryStorage({ [USAGE_SPLIT_STORAGE]: "model" })), "provider");
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(loadUsageSplit(broken), "provider");
  assert.equal(saveUsageSplit("account", broken), "account");
});

test("by account, the chart stacks each provider account and names it", () => {
  const series = {
    bucket: "day",
    providers: [
      { provider: "claude_code", total_tokens: 900, calls: 3, style: { dark: "#D97757", light: "#C6613F", pattern: "solid" } },
      { provider: "codex", total_tokens: 100, calls: 1, style: { dark: "#10A37F", light: "#0D8A6B", pattern: "stripes" } },
    ],
    accounts: [
      { key: "claude_code:work", provider: "claude_code", profile: "work", name: "work", total_tokens: 600, calls: 2, style: { dark: "#F0C9BC", light: "#4F2719", pattern: "dots" } },
      { key: "claude_code:", provider: "claude_code", profile: null, name: "Default sign-in", total_tokens: 300, calls: 1, style: { dark: "#D97757", light: "#C6613F", pattern: "solid" } },
      { key: "codex:", provider: "codex", profile: null, name: "Default account", total_tokens: 100, calls: 1, style: { dark: "#10A37F", light: "#0D8A6B", pattern: "stripes" } },
    ],
    buckets: [
      { key: "2026-11-15", start: "2026-11-15T00:00:00+00:00", total_tokens: 1000, calls: 4, unknown_calls: 0, providers: { claude_code: 900, codex: 100 }, accounts: { "claude_code:work": 600, "claude_code:": 300, "codex:": 100 } },
    ],
  };
  const byProvider = tokenChartModel(series);
  assert.equal(byProvider.split, "provider");
  assert.deepEqual(byProvider.legend.map(entry => entry.label), ["Claude Code", "Codex"]);
  const model = tokenChartModel(series, "account");
  assert.equal(model.split, "account");
  assert.deepEqual(model.legend.map(entry => [entry.key, entry.label]), [
    ["claude_code:work", "Claude Code · work"], ["claude_code:", "Claude Code · Default sign-in"], ["codex:", "Codex · Default account"],
  ]);
  assert.deepEqual(model.bars[0].segments.map(segment => [segment.key, segment.label, segment.tokens, segment.share]), [
    ["claude_code:work", "Claude Code · work", 600, 0.6], ["claude_code:", "Claude Code · Default sign-in", 300, 0.3], ["codex:", "Codex · Default account", 100, 0.1],
  ]);
  // Two accounts of one provider differ by shade and by pattern.
  assert.notEqual(model.legend[0].style.dark, model.legend[1].style.dark);
  assert.notEqual(model.legend[0].style.pattern, model.legend[1].style.pattern);
  assert.equal(model.grand, 1000);
  // An older backend sends no accounts: the chart stays split by provider.
  const older = tokenChartModel({ ...series, accounts: undefined }, "account");
  assert.equal(older.split, "provider");
  assert.deepEqual(older.legend.map(entry => entry.key), ["claude_code", "codex"]);
});
