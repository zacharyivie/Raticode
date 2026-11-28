import test from "node:test";
import assert from "node:assert/strict";
import { clockTime, compactAge, dateTime, durationParts, durationSeconds, localInputToOffsetIso, localTimeZone, longDate, monthName, runLength, shortDate, timeAgo, toLocalInput, weekdayName } from "./timeText.js";

const NOW = Date.parse("2026-11-25T12:00:00Z");
const before = seconds => new Date(NOW - seconds * 1000).toISOString();

test("timeAgo words recent times by minute, hour and day, then shows the date", () => {
  assert.equal(timeAgo(before(30), NOW), "just now");
  assert.equal(timeAgo(before(5 * 60), NOW), "5 min ago");
  assert.equal(timeAgo(before(3 * 3600), NOW), "3 h ago");
  assert.equal(timeAgo(before(86400), NOW), "yesterday");
  assert.equal(timeAgo(before(4 * 86400), NOW), "4 days ago");
  assert.equal(timeAgo(before(6 * 86400), NOW), "6 days ago");
  const old = new Date(NOW - 40 * 86400 * 1000);
  assert.equal(timeAgo(before(40 * 86400), NOW), old.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  assert.doesNotMatch(timeAgo(before(40 * 86400), NOW), /2026/);
  assert.match(timeAgo("2025-03-02T12:00:00Z", NOW), /2025/);
});

test("timeAgo takes dates, milliseconds and strings, and is blank for no time", () => {
  assert.equal(timeAgo(new Date(NOW - 120_000), new Date(NOW)), "2 min ago");
  assert.equal(timeAgo(NOW - 7200_000, NOW), "2 h ago");
  assert.equal(timeAgo(before(-30), NOW), "just now");
  assert.equal(timeAgo(null, NOW), "");
  assert.equal(timeAgo("not a date", NOW), "");
});

test("compactAge keeps the short badge form", () => {
  assert.equal(compactAge(before(30), NOW), "now");
  assert.equal(compactAge(before(5 * 60), NOW), "5 min");
  assert.equal(compactAge(before(3 * 3600), NOW), "3 h");
  assert.equal(compactAge(before(4 * 86400), NOW), "4 d");
  assert.equal(compactAge(undefined, NOW), "");
});

test("runLength gives seconds, then minutes and seconds, then hours and minutes", () => {
  assert.equal(runLength(45), "45s");
  assert.equal(runLength(303), "5m 3s");
  assert.equal(runLength(7440), "2h 4m");
  assert.equal(runLength(4.4), "4s");
  assert.equal(runLength(-3), "0s");
  assert.equal(runLength(undefined), "0s");
});

// Local dates, so these read the same in any time zone the tests run in.
const TODAY = new Date(2026, 10, 25, 12, 0);
const NOV_3 = new Date(2026, 10, 3, 14, 5);
const LAST_YEAR = new Date(2025, 2, 2, 9, 30);

test("shortDate gives the month and day, and the year only in another year", () => {
  assert.equal(shortDate(NOV_3, { now: TODAY }), "Nov 3");
  assert.equal(shortDate(LAST_YEAR, { now: TODAY }), "Mar 2, 2025");
  assert.equal(shortDate(NOV_3, { now: TODAY, weekday: true }), "Tue, Nov 3");
  assert.equal(shortDate(NOV_3.getTime(), { now: TODAY }), "Nov 3");
  // A bare date is that day wherever you are, not UTC midnight.
  assert.equal(shortDate("2026-11-03", { now: TODAY }), "Nov 3");
  for (const value of [undefined, null, "", "not a date"]) assert.equal(shortDate(value), "", String(value));
});

test("dateTime adds the clock time to the short date", () => {
  assert.equal(dateTime(NOV_3, { now: TODAY }), "Nov 3, 2:05 PM");
  assert.equal(dateTime(LAST_YEAR, { now: TODAY }), "Mar 2, 2025, 9:30 AM");
  assert.equal(dateTime(NOV_3, { now: TODAY, weekday: true }), "Tue, Nov 3, 2:05 PM");
  assert.equal(dateTime("not a date"), "");
});

test("clockTime, longDate, weekdayName and monthName", () => {
  assert.equal(clockTime(NOV_3), "2:05 PM");
  assert.equal(clockTime(new Date(2026, 10, 3, 0, 7)), "12:07 AM");
  assert.equal(longDate(NOV_3), "Tuesday, November 3, 2026");
  assert.equal(longDate(LAST_YEAR), "Sunday, March 2, 2025");
  assert.equal(weekdayName(NOV_3), "Tue");
  assert.equal(monthName(NOV_3, { now: TODAY }), "Nov");
  assert.equal(monthName(LAST_YEAR, { now: TODAY }), "Mar 2025");
  assert.equal(monthName(NOV_3, { long: true }), "November 2026");
  for (const format of [clockTime, longDate, weekdayName, monthName]) assert.equal(format("not a date"), "");
});

test("a timeZone shows the time as the clock reads there", () => {
  const instant = "2026-11-03T14:05:00Z";
  assert.equal(clockTime(instant, { timeZone: "UTC" }), "2:05 PM");
  assert.equal(clockTime(instant, { timeZone: "America/New_York" }), "9:05 AM");
  assert.equal(clockTime(instant, { timeZone: "America/New_York", zoneName: true }), "9:05 AM EST");
  assert.equal(dateTime(instant, { timeZone: "Asia/Tokyo", now: TODAY }), "Nov 3, 11:05 PM");
  assert.equal(weekdayName("2026-11-03T02:00:00Z", { timeZone: "America/Los_Angeles" }), "Mon");
  assert.equal(longDate("2026-11-03T02:00:00Z", { timeZone: "America/Los_Angeles" }), "Monday, November 2, 2026");
  assert.equal(monthName("2026-11-01T00:00:00Z", { long: true, timeZone: "UTC" }), "November 2026");
  // The year is the zone's: New Year's in UTC is still last year in Denver.
  assert.equal(shortDate("2026-01-01T03:00:00Z", { timeZone: "America/Denver", now: TODAY }), "Dec 31, 2025");
  // A zone the system doesn't know falls back to this computer's.
  assert.equal(clockTime(NOV_3, { timeZone: "Not/A_Zone" }), clockTime(NOV_3));
});

test("localTimeZone names this computer's zone", () => {
  assert.equal(localTimeZone(), new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
});

test("durationParts opens on the largest unit that divides the seconds evenly", () => {
  assert.deepEqual(durationParts(1200), { amount: "20", unit: "minutes" });
  assert.deepEqual(durationParts(1800), { amount: "30", unit: "minutes" });
  assert.deepEqual(durationParts(7200), { amount: "2", unit: "hours" });
  assert.deepEqual(durationParts(5400), { amount: "90", unit: "minutes" });
  assert.deepEqual(durationParts(172800), { amount: "2", unit: "days" });
  assert.deepEqual(durationParts("86400"), { amount: "1", unit: "days" });
  assert.deepEqual(durationParts(0), { amount: "0", unit: "minutes" });
  assert.deepEqual(durationParts(90), { amount: "1.5", unit: "minutes" });
  assert.deepEqual(durationParts(""), { amount: "", unit: "minutes" });
  assert.deepEqual(durationParts(null), { amount: "", unit: "minutes" });
});

test("durationSeconds saves whole seconds and keeps blank and bad entries visible to the form", () => {
  assert.equal(durationSeconds("20", "minutes"), "1200");
  assert.equal(durationSeconds("2", "hours"), "7200");
  assert.equal(durationSeconds("1", "days"), "86400");
  assert.equal(durationSeconds("1.5", "minutes"), "90");
  assert.equal(durationSeconds("0", "hours"), "0");
  assert.equal(durationSeconds("", "hours"), "");
  assert.equal(durationSeconds("abc", "minutes"), "NaN");
  for (const seconds of [0, 90, 1200, 5400, 7200, 86400, 1800]) {
    const { amount, unit } = durationParts(seconds);
    assert.equal(durationSeconds(amount, unit), String(seconds), `round trip ${seconds}`);
  }
});

test("localInputToOffsetIso keeps the picked wall time and adds this computer's offset", () => {
  const iso = localInputToOffsetIso("2026-12-01T09:30");
  assert.match(iso, /^2026-12-01T09:30:00[+-]\d{2}:\d{2}$/);
  assert.equal(Date.parse(iso), new Date(2026, 11, 1, 9, 30).getTime());
  assert.equal(toLocalInput(iso), "2026-12-01T09:30");
  assert.equal(localInputToOffsetIso(""), null);
  assert.equal(localInputToOffsetIso("not a date"), null);
});
