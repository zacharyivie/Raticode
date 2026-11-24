import assert from "node:assert/strict";
import test from "node:test";
import { selfWorkVerifiedLine, verifiedCauses, verifiedHeadline, verifiedStats, verifiedTrend } from "./verifiedWork.js";

// The shape gofer/ui/rem_verified_work.py returns for three accepted tickets and one blocked.
const NUMBERS = {
  accepted: 3, first_pass: 1, first_pass_percent: 33, repairs: 3, repairs_per_ticket: 1,
  finished: 4, blocked: 1, blocked_percent: 25,
  blocked_causes: [{ cause: "review", label: "Review checks failed", tickets: 1 }, { cause: "other", label: "Other", tickets: 0 }],
  project_check: { checked: 2, failed_first: 1, failed_first_percent: 50, flaky: 1 },
  step_ins: { total: 1, request_changes: 0, manual_moves: 1, tickets: 1 },
  tokens: { total: 6000, tickets: 3, per_accepted: 2000, partial: false },
  line: "1 of 3 tickets passed review first time",
  trend: { bucket: "day", buckets: [
    { start: "2026-11-19T00:00:00+00:00", accepted: 2, first_pass: 1, repairs: 1, blocked: 0 },
    { start: "2026-11-20T00:00:00+00:00", accepted: 0, first_pass: 0, repairs: 0, blocked: 0 },
    { start: "2026-11-21T00:00:00+00:00", accepted: 1, first_pass: 0, repairs: 2, blocked: 1 },
  ] },
};

test("the headline says how many accepted tickets passed first time", () => {
  assert.deepEqual(verifiedHeadline(NUMBERS), {
    value: "1 of 3",
    label: "tickets passed review first time, with no repair and without you stepping in",
    percent: 33,
  });
  assert.equal(verifiedHeadline({ accepted: 0, finished: 0 }), null);
  assert.equal(verifiedHeadline(null), null);
  assert.equal(verifiedHeadline({ accepted: 0, finished: 2 }).value, "0 accepted");
});

test("the figures cover repairs, blocks, the project check, tokens and step-ins", () => {
  const stats = Object.fromEntries(verifiedStats(NUMBERS).map(stat => [stat.key, stat]));
  assert.equal(stats.repairs.value, "1");
  assert.equal(stats.repairs.note, "3 repairs over 3 accepted tickets");
  assert.equal(stats.blocked.value, "1 of 4");
  assert.equal(stats.blocked.note, "finished tickets (25%)");
  assert.equal(stats.check.value, "1 of 2");
  // Flaky runs are their own figure, outside first-try failures; an older backend sends none.
  assert.equal(stats.flaky.value, "1");
  assert.match(stats.flaky.note, /rerun of the same commit/);
  const older = verifiedStats({ ...NUMBERS, project_check: { checked: 2, failed_first: 1 } });
  assert.equal(older.some(stat => stat.key === "flaky"), false);
  assert.equal(stats.tokens.value, (2000).toLocaleString());
  assert.equal(stats.steps.value, "1 time");
  assert.equal(stats.steps.note, "0 change requests, 1 move by hand");
  // Nothing accepted or checked: only what there is to say.
  assert.deepEqual(verifiedStats({ finished: 1, blocked: 1, step_ins: {} }).map(stat => stat.key), ["blocked", "steps"]);
  assert.match(verifiedStats({ ...NUMBERS, tokens: { ...NUMBERS.tokens, partial: true } }).find(stat => stat.key === "tokens").note, /floor/);
});

test("causes leave out empty ones and the trend needs two buckets with work", () => {
  assert.deepEqual(verifiedCauses(NUMBERS), [{ key: "review", label: "Review checks failed", tickets: 1 }]);
  const bars = verifiedTrend(NUMBERS);
  assert.equal(bars.length, 3);
  assert.equal(bars[0].first, 50);
  assert.equal(bars[0].repaired, 50);
  assert.equal(bars[2].blocked, 50);
  assert.match(bars[2].text, /0 of 1 first time, 1 blocked$/);
  assert.match(bars[1].text, /none accepted$/);
  assert.deepEqual(verifiedTrend({ trend: { bucket: "day", buckets: [NUMBERS.trend.buckets[0]] } }), []);
});

test("a failed task shows as its own cause, such as no activity for 20 minutes", () => {
  // rem_verified_work names a failed task by its message; Why tickets were blocked lists each.
  const numbers = { ...NUMBERS, blocked: 4, blocked_causes: [
    { cause: "no_activity", label: "No activity for 20 minutes", tickets: 2 },
    { cause: "provider_error", label: "Provider error", tickets: 1 },
    { cause: "failed", label: "Other failure", tickets: 1 },
  ] };
  assert.deepEqual(verifiedCauses(numbers).map(cause => `${cause.label} ${cause.tickets}`), [
    "No activity for 20 minutes 2", "Provider error 1", "Other failure 1",
  ]);
  assert.equal(verifiedCauses(numbers)[0].key, "no_activity");
});

test("the self-work report card shows the line only when tickets were accepted", () => {
  const report = { kind: "self_work_report", self_work_report: { items: [], verified_work: { accepted: 3, line: NUMBERS.line } } };
  assert.equal(selfWorkVerifiedLine(report), "1 of 3 tickets passed review first time.");
  assert.equal(selfWorkVerifiedLine({ ...report, self_work_report: { verified_work: { accepted: 0, line: "No tickets were accepted in this period." } } }), "");
  assert.equal(selfWorkVerifiedLine({ kind: "text", self_work_report: report.self_work_report }), "");
});
