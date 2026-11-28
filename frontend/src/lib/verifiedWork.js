// Settings › Usage › Verified work: how often Rem's tickets passed review on their own.
// The backend works the numbers out (gofer/ui/rem_verified_work.py); this only words them.

import { monthName, shortDate } from "./timeText.js";

function count(value) {
  return Number.isFinite(value) && value >= 0 ? Math.round(value) : 0;
}

function plural(value, one, many) {
  return `${value.toLocaleString()} ${value === 1 ? one : many}`;
}

function percent(value) {
  return Number.isFinite(value) ? ` (${Math.round(value)}%)` : "";
}

/** The headline, "1 of 3", and what it counts; null when nothing finished in the period. */
export function verifiedHeadline(numbers) {
  if (!numbers || typeof numbers !== "object") return null;
  const accepted = count(numbers.accepted);
  if (!accepted && !count(numbers.finished)) return null;
  if (!accepted) return { value: "0 accepted", label: "No ticket was accepted in this period.", percent: null };
  return {
    value: `${count(numbers.first_pass).toLocaleString()} of ${accepted.toLocaleString()}`,
    label: `${accepted === 1 ? "ticket" : "tickets"} passed review first time, with no repair and without you stepping in`,
    percent: Number.isFinite(numbers.first_pass_percent) ? Math.round(numbers.first_pass_percent) : null,
  };
}

/** The figures under the headline, each a label, a value and a short note. */
export function verifiedStats(numbers) {
  if (!numbers || typeof numbers !== "object") return [];
  const stats = [];
  const accepted = count(numbers.accepted);
  if (accepted) {
    const repairs = Number.isFinite(numbers.repairs_per_ticket) ? numbers.repairs_per_ticket : 0;
    stats.push({
      key: "repairs",
      label: "Repairs per ticket",
      value: repairs.toLocaleString(undefined, { maximumFractionDigits: 2 }),
      note: `${plural(count(numbers.repairs), "repair", "repairs")} over ${plural(accepted, "accepted ticket", "accepted tickets")}`,
    });
  }
  const finished = count(numbers.finished);
  if (finished) {
    stats.push({
      key: "blocked",
      label: "Blocked",
      value: `${count(numbers.blocked).toLocaleString()} of ${finished.toLocaleString()}`,
      note: `finished tickets${percent(numbers.blocked_percent)}`,
    });
  }
  const check = numbers.project_check || {};
  if (count(check.checked)) {
    stats.push({
      key: "check",
      label: "Project check failed first try",
      value: `${count(check.failed_first).toLocaleString()} of ${count(check.checked).toLocaleString()}`,
      note: `tickets with a recorded check${percent(check.failed_first_percent)}`,
    });
    // Older backends don't count flaky runs; leave the figure out rather than show a 0.
    if (Number.isFinite(check.flaky)) {
      stats.push({
        key: "flaky",
        label: "Flaky check runs",
        value: count(check.flaky).toLocaleString(),
        note: "failed, then passed on a rerun of the same commit; not counted as first-try failures",
      });
    }
  }
  const tokens = numbers.tokens || {};
  if (Number.isFinite(tokens.per_accepted)) {
    stats.push({
      key: "tokens",
      label: "Tokens per accepted ticket",
      value: Math.round(tokens.per_accepted).toLocaleString(),
      note: tokens.partial ? "Some calls didn't report their tokens, so this is a floor." : `across ${plural(count(tokens.tickets), "ticket", "tickets")} with recorded calls`,
    });
  }
  const steps = numbers.step_ins || {};
  stats.push({
    key: "steps",
    label: "You stepped in",
    value: plural(count(steps.total), "time", "times"),
    note: `${plural(count(steps.request_changes), "change request", "change requests")}, ${plural(count(steps.manual_moves), "move", "moves")} by hand`,
  });
  return stats;
}

/** Blocked tickets by cause, most first. */
export function verifiedCauses(numbers) {
  return (Array.isArray(numbers?.blocked_causes) ? numbers.blocked_causes : [])
    .filter(cause => cause && count(cause.tickets))
    .map(cause => ({ key: String(cause.cause || cause.label), label: String(cause.label || cause.cause), tickets: count(cause.tickets) }));
}

/** Bars for the trend: each bucket's first-pass and other accepted tickets. Empty without two buckets of data. */
export function verifiedTrend(numbers) {
  const buckets = Array.isArray(numbers?.trend?.buckets) ? numbers.trend.buckets : [];
  if (buckets.filter(bucket => count(bucket?.accepted) || count(bucket?.blocked)).length < 2) return [];
  const most = Math.max(1, ...buckets.map(bucket => count(bucket.accepted) + count(bucket.blocked)));
  return buckets.map(bucket => {
    const accepted = count(bucket.accepted);
    const first = Math.min(count(bucket.first_pass), accepted);
    const blocked = count(bucket.blocked);
    const label = (numbers.trend.bucket === "month" ? monthName(bucket.start) : shortDate(bucket.start)) || String(bucket.start || "");
    return {
      key: String(bucket.start),
      label,
      first: (100 * first) / most,
      repaired: (100 * (accepted - first)) / most,
      blocked: (100 * blocked) / most,
      text: `${label}: ${accepted ? `${first} of ${accepted} first time` : "none accepted"}${blocked ? `, ${blocked} blocked` : ""}`,
    };
  });
}

/** The self-work report card's line, "3 of 4 tickets passed review first time"; "" without one. */
export function selfWorkVerifiedLine(message) {
  const numbers = message?.kind === "self_work_report" ? message.self_work_report?.verified_work : null;
  if (!numbers || !count(numbers.accepted) || typeof numbers.line !== "string") return "";
  return `${numbers.line.trim().replace(/\.$/, "")}.`;
}
