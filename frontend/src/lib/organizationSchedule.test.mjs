import assert from "node:assert/strict";
import { test } from "node:test";
import {
  blockId, blockProblem, cleanBlock, clockText, daySegments, describeBlock, keywordsText, parseKeywords, scheduleProblem, withCadence, zoneClock,
} from "./organizationSchedule.js";

// The suggested schedule, as the backend sends it.
const suggested = [
  { id: "features", label: "New feature work", cadence: "daily", start: "08:00", end: "17:00" },
  { id: "hardening", label: "Application hardening", cadence: "daily", start: "17:00", end: "00:00" },
  { id: "nightly-release", label: "Nightly release", cadence: "daily", start: "00:00", end: "01:00" },
  { id: "gardening", label: "Process improvement and gardening", cadence: "daily", start: "00:00", end: "08:00" },
  { id: "weekly-release", label: "Weekly release", cadence: "weekly", days: [4], start: "13:00", end: "17:00" },
  { id: "dependency-audit", label: "Dependency updates and audit", cadence: "monthly", nthWeek: 1, weekday: 0, start: "09:00", end: "12:00" },
  { id: "quarterly-review", label: "Quarterly planning and security review", cadence: "quarterly", quarterMonth: 1, nthWeek: 1, weekday: 1, start: "09:00", end: "17:00" },
];

test("blocks are described in the backend's words", () => {
  assert.deepEqual(suggested.map(describeBlock), [
    "Every day 08:00–17:00",
    "Every day 17:00–00:00",
    "Every day 00:00–01:00",
    "Every day 00:00–08:00",
    "Fridays 13:00–17:00",
    "Monthly, 1st Monday, 09:00–12:00",
    "Quarterly, 1st Tuesday of the 1st month, 09:00–17:00",
  ]);
  assert.equal(describeBlock({ cadence: "monthly", monthDay: 31, start: "09:00", end: "10:00" }), "Monthly, last day, 09:00–10:00");
  assert.equal(describeBlock({ cadence: "quarterly", quarterMonth: 3, monthDay: 15, start: "00:00", end: "00:00" }), "Quarterly, day 15 of the 3rd month, all day");
  assert.equal(describeBlock({ cadence: "weekly", days: [0, 1, 2, 3, 4], start: "22:00", end: "06:00" }), "Weekdays 22:00–06:00");
});

test("a draft explains what to fix before saving", () => {
  assert.equal(scheduleProblem(suggested), "");
  assert.equal(blockProblem({ label: "", start: "08:00", end: "09:00" }), "Give each block a name.");
  assert.equal(blockProblem({ label: "Ship", cadence: "weekly", days: [], start: "08:00", end: "09:00" }), "Ship: choose the weekdays it runs on.");
  assert.match(blockProblem({ label: "Audit", cadence: "monthly", start: "08:00", end: "09:00" }), /day of the month or a weekday/);
  assert.match(blockProblem({ label: "Audit", cadence: "monthly", monthDay: 40, start: "08:00", end: "09:00" }), /1 to 31/);
  // Same cadence and length, overlapping: neither would win.
  assert.match(scheduleProblem([
    { label: "A", cadence: "daily", start: "08:00", end: "12:00" },
    { label: "B", cadence: "daily", start: "10:00", end: "14:00" },
  ]), /A and B overlap and neither takes precedence/);
  // A Sunday night block runs into Monday morning.
  assert.match(scheduleProblem([
    { label: "Late", cadence: "weekly", days: [6], start: "22:00", end: "04:00" },
    { label: "Early", cadence: "weekly", days: [0], start: "02:00", end: "08:00" },
  ]), /overlap/);
  assert.equal(scheduleProblem([
    { label: "Long", cadence: "daily", start: "00:00", end: "08:00" },
    { label: "Short", cadence: "daily", start: "00:00", end: "01:00" },
  ]), "");
});

test("a day shows which block wins each hour, wrap-around included", () => {
  const label = segments => segments.map(s => `${clockText(s.start)}-${clockText(s.end)} ${s.block?.id || "none"}`);
  // Thursday: the release hour beats gardening, and hardening runs to midnight.
  assert.deepEqual(label(daySegments(suggested, 3)), [
    "00:00-01:00 nightly-release",
    "01:00-08:00 gardening",
    "08:00-17:00 features",
    "17:00-00:00 hardening",
  ]);
  // Friday: the weekly release beats daily feature work in the afternoon.
  assert.deepEqual(label(daySegments(suggested, 4)).slice(2), [
    "08:00-13:00 features",
    "13:00-17:00 weekly-release",
    "17:00-00:00 hardening",
  ]);
  // An overnight block started the day before shows on the next morning.
  const night = [{ id: "night", label: "Night", cadence: "weekly", days: [4], start: "22:00", end: "06:00" }];
  assert.deepEqual(label(daySegments(night, 5)), ["00:00-06:00 night", "06:00-00:00 none"]);
});

test("the time zone decides today's weekday and minute", () => {
  const instant = new Date("2026-11-10T14:00:00Z"); // Tuesday 09:00 in New York, 23:00 in Tokyo
  assert.deepEqual(zoneClock("America/New_York", instant), { weekday: 1, minute: 540 });
  assert.deepEqual(zoneClock("Asia/Tokyo", instant), { weekday: 1, minute: 1380 });
  assert.deepEqual(zoneClock("Not/AZone", instant), { weekday: 1, minute: 840 });
});

test("keywords, IDs and cadence changes keep drafts tidy", () => {
  assert.deepEqual(parseKeywords(" test, Bug ,, bug, security "), ["test", "Bug", "security"]);
  assert.equal(keywordsText(["a", "b"]), "a, b");
  assert.equal(blockId("Nightly release!", ["nightly-release"]), "nightly-release-2");
  assert.equal(blockId("", []), "block");
  const weekly = withCadence({ label: "X", cadence: "daily", start: "09:00", end: "10:00" }, "weekly");
  assert.deepEqual(weekly.days, [4]);
  const quarterly = withCadence(weekly, "quarterly");
  assert.equal(quarterly.days, undefined);
  assert.deepEqual([quarterly.nthWeek, quarterly.weekday, quarterly.quarterMonth], [1, 0, 1]);
  const daily = withCadence(quarterly, "daily");
  assert.equal(daily.nthWeek, undefined);
  assert.deepEqual(cleanBlock({ id: "x", label: " X ", cadence: "monthly", monthDay: 3, nthWeek: undefined, days: [], start: "09:00", end: "10:00", keywords: [] }),
    { id: "x", label: "X", cadence: "monthly", start: "09:00", end: "10:00", monthDay: 3, keywords: [] });
});
