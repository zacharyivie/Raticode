import { apiJson } from "./api.js";
import { dateTime } from "./timeText.js";

export const RUNS_ONLY_WHILE_RUNNING =
  "Triggers run only while Raticode or its background scheduler is running.";

export const OVERLAP_OPTIONS = [
  ["skip", "Skip this run"],
  ["queue", "Run once after it finishes"],
  ["parallel", "Run at the same time"],
];

export const MISSED_OPTIONS = [
  ["record", "Show as missed"],
  ["run_once", "Show as missed and run once"],
];

const KIND_LABELS = { cron: "Schedule", file: "File change", webhook: "Webhook" };
const STATUS_LABELS = {
  failed: "Failed",
  interrupted: "Interrupted",
  missed: "Missed",
  passed: "Passed",
  queued: "Queued",
  running: "Running",
  skipped: "Skipped",
  stopped: "Stopped",
  // The run hit its provider's usage limit; the scheduler resumes it after the reset.
  waiting: "Waiting for usage limit",
};

const PROBLEM_STATUSES = new Set(["failed", "interrupted", "missed", "skipped"]);

export function isProblemStatus(status) {
  return PROBLEM_STATUSES.has(status);
}

export function statusLabel(status) {
  return STATUS_LABELS[status] ?? status;
}

// Why a trigger won't run its workflow as it is now, or "" when it will.
const APPROVAL_TEXT = {
  changed: "Workflow changed since you approved it",
  unapproved: "Waiting for you to approve this workflow version",
};
const APPROVED_BY = {
  cli: "from the command line",
  desktop: "on this computer",
  phone: "from your phone",
  studio: "by saving it in Studio",
  upgrade: "when Raticode started asking for approval",
};
const FILE_CHANGES = {
  added: "Now used",
  changed: "Changed",
  missing: "Missing",
  removed: "No longer used",
};

// A triggered run found a file changed right before using it, so the trigger waits for
// approval even if the file has changed back since.
export function changedDuringRunLine(marked) {
  if (!marked?.path) return "";
  return `${marked.path} changed during a triggered run, so the step that uses it wasn't run.`;
}

export function approvalNotice(trigger) {
  if (trigger.runEditsWithoutApproval) return "";
  return APPROVAL_TEXT[trigger.approval?.state] ?? "";
}

export function approvedLine(approval) {
  if (!approval?.approvedAt) return "";
  const how = APPROVED_BY[approval.approvedBy];
  return `This version was approved ${dateTime(approval.approvedAt, { weekday: true })}${how ? ` ${how}` : ""}.`;
}

export function fileChangeLabel(change) {
  return FILE_CHANGES[change] ?? change;
}

export const UNPINNED_WARNING = "Package version not pinned: each run fetches the newest release";

// Files whose change Review changes couldn't show in full, and MCP servers whose package
// has no exact version. Approve this version asks about them a second time.
const UNSEEN_REASONS = {
  cut: "Its diff was cut short",
  unreadable: "Binary or too large to show",
  unpinned: UNPINNED_WARNING,
};

export function unseenReasonText(reason) {
  return UNSEEN_REASONS[reason] ?? "Not shown";
}

export function unseenFiles(changes) {
  return Array.isArray(changes?.unseen) ? changes.unseen : [];
}

export function triggerTitle(trigger) {
  return trigger.label || KIND_LABELS[trigger.kind] || trigger.kind;
}

export function triggerDetail(trigger) {
  // A cron trigger reads in words when its schedule is a common one; the card shows the
  // expression under them. Otherwise the expression stands alone.
  if (trigger.kind === "cron") return `${trigger.summary || trigger.config.expression} · ${trigger.config.timezone}`;
  if (trigger.kind === "file") {
    const pattern = trigger.config.glob === "*" ? "" : ` · ${trigger.config.glob}`;
    return `${trigger.config.path}${pattern}`;
  }
  return trigger.webhookUrl ?? "";
}

export function triggerPolicyText(trigger) {
  const overlap = OVERLAP_OPTIONS.find(([value]) => value === trigger.overlap)?.[1] ?? trigger.overlap;
  const parts = [`If already running: ${overlap.toLowerCase()}`];
  if (trigger.kind === "file") parts.push(`waits ${trigger.config.debounceSeconds}s for quiet`);
  if (trigger.kind === "cron" && trigger.missed === "run_once") parts.push("catches up one missed run");
  if (trigger.maxRunSeconds !== undefined) parts.push(timeLimitText(trigger).replace(/^\w/, (first) => first.toLowerCase()));
  if (trigger.waitForUsageLimit === false) parts.push("fails at a usage limit");
  return parts.join(" · ");
}

export const UNSANDBOXED_TITLE = "Runs unsandboxed";
export const UNSANDBOXED_TEXT =
  "These steps run with no operating-system sandbox. Their shell can change any file you can, read your credentials and reach any host, with nobody watching.";

export function unsandboxedSteps(payload) {
  const steps = payload?.sandbox?.unsandboxed;
  return Array.isArray(steps) ? steps : [];
}

export function unsandboxedStepText(step) {
  return `${step.node} on ${step.provider}: ${step.reason}`;
}

export const WIDENED_TITLE = "Widens its sandbox";
export const WIDENED_TEXT =
  "The workflow file lets these steps' shell reach beyond the project: write folders outside it or reach any host. Check that you expect this before a trigger runs it with nobody watching.";

// Agent steps whose own workflow settings widen their sandbox (extra paths outside the
// project, network: any). Older backends send none.
export function widenedSteps(payload) {
  const steps = payload?.sandbox?.widened;
  return Array.isArray(steps) ? steps : [];
}

export function widenedStepText(step) {
  return `${step.node}: ${step.reason}`;
}

// What the add-trigger confirmation says, or "" when nothing needs confirming.
export function triggerConfirmText(unsandboxed = [], widened = []) {
  const count = (items) => (items.length === 1 ? "1 step" : `${items.length} steps`);
  const parts = [];
  if (unsandboxed.length) parts.push(`${UNSANDBOXED_TITLE}: ${count(unsandboxed)} will run with nobody watching and no sandbox.`);
  if (widened.length) parts.push(`${WIDENED_TITLE}: ${count(widened)} can reach beyond the project, as the workflow file asks.`);
  return parts.length ? `${parts.join(" ")} Add the trigger anyway?` : "";
}

// Agent steps whose model is deprecated (with its retirement date) or already retired.
export function modelNotices(payload) {
  const notices = payload?.models?.notices;
  return Array.isArray(notices) ? notices : [];
}

export function modelNoticeTitle(notices) {
  return notices.some((notice) => notice.state === "retired") ? "Model retired" : "Model retiring";
}

export function modelNoticeText(notice) {
  return `${notice.node} on ${notice.provider}: ${notice.message}`;
}

export const PAUSED_TEXT = "All triggers are paused. Nothing fires until you resume them in Workflow runs.";

export function schedulerSummary(scheduler) {
  if (scheduler?.paused) return { running: Boolean(scheduler.running), paused: true, text: PAUSED_TEXT };
  if (!scheduler?.running) {
    return { running: false, text: "Not running. Nothing will fire until Raticode or its scheduler starts." };
  }
  return {
    running: true,
    text: scheduler.owner === "app" ? "Running in Raticode" : "Running in the background scheduler",
  };
}


export function emptyTriggerDraft(kind = "cron", defaultTimezone = "UTC") {
  return {
    kind,
    label: "",
    overlap: "skip",
    missed: "record",
    expression: "",
    timezone: defaultTimezone,
    path: "",
    glob: "*",
    debounceSeconds: "2",
    recursive: true,
    inputs: "",
    dailyMaxCostUsd: "",
    dailyMaxTokens: "",
    maxRunTime: formatRunTimeInput(DEFAULT_MAX_RUN_SECONDS),
    waitForUsageLimit: true,
    runEditsWithoutApproval: false,
  };
}

// A trigger's Time limit: the scheduler stops a triggered run that runs longer.
export const DEFAULT_MAX_RUN_SECONDS = 6 * 3600;
const MAX_RUN_SECONDS_CEILING = 30 * 86400;
const RUN_TIME_UNITS = {
  s: 1, sec: 1, secs: 1, second: 1, seconds: 1,
  m: 60, min: 60, mins: 60, minute: 60, minutes: 60,
  h: 3600, hr: 3600, hrs: 3600, hour: 3600, hours: 3600,
  d: 86400, day: 86400, days: 86400,
};

// A typed Time limit such as 2h, 30m or 1h30m, in seconds. null when empty or none.
export function parseRunTime(text) {
  const value = String(text ?? "").trim().toLowerCase();
  if (!value || ["none", "off", "no", "never", "0"].includes(value)) return null;
  const pattern = /(\d+(?:\.\d+)?)\s*([a-z]+)/g;
  const parts = [...value.matchAll(pattern)];
  if (!parts.length || value.replace(pattern, "").trim()) {
    throw new Error("The time limit must look like 30m, 2h or 1h30m, or be empty for no limit.");
  }
  let seconds = 0;
  for (const [, number, unit] of parts) {
    if (!(unit in RUN_TIME_UNITS)) throw new Error(`The time limit has an unknown unit "${unit}". Use s, m, h or d.`);
    seconds += Number(number) * RUN_TIME_UNITS[unit];
  }
  seconds = Math.round(seconds);
  if (seconds > MAX_RUN_SECONDS_CEILING) throw new Error("The time limit can be at most 30 days. Leave it empty for no limit.");
  return seconds || null;
}

// A length of time as people read it: 45 s, 12 min, 6 h or 7 h 12 min.
export function formatRunTime(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  if (whole < 60) return `${whole} s`;
  if (whole < 3600) {
    const rest = whole % 60;
    return `${Math.floor(whole / 60)} min${rest ? ` ${rest} s` : ""}`;
  }
  const minutes = Math.floor((whole % 3600) / 60);
  return `${Math.floor(whole / 3600)} h${minutes ? ` ${minutes} min` : ""}`;
}

// The same length in the form the Time limit field takes back: 6h, 1h30m, 90s.
export function formatRunTimeInput(seconds) {
  if (!seconds) return "";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return [[days, "d"], [hours, "h"], [minutes, "m"], [rest, "s"]]
    .filter(([value]) => value)
    .map(([value, unit]) => `${value}${unit}`)
    .join("");
}

function timeLimitText(trigger) {
  return trigger.maxRunSeconds ? `Time limit: ${formatRunTime(trigger.maxRunSeconds)}` : "No time limit";
}

function limitText(value) {
  return value === null || value === undefined ? "" : String(value);
}

// A typed daily limit: null when empty or 0, which means no limit.
export function parseDailyLimit(text, what, { whole = false } = {}) {
  const trimmed = String(text ?? "").trim().replace(/^\$/, "").replaceAll(",", "");
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`The daily ${what} limit must be a number, or empty for no limit.`);
  }
  if (whole && !Number.isInteger(value)) throw new Error(`The daily ${what} limit must be a whole number.`);
  return value === 0 ? null : value;
}

export function draftFromTrigger(trigger) {
  return {
    ...emptyTriggerDraft(trigger.kind, trigger.config.timezone ?? "UTC"),
    label: trigger.label,
    overlap: trigger.overlap,
    missed: trigger.missed,
    expression: trigger.config.expression ?? "",
    path: trigger.config.path ?? "",
    glob: trigger.config.glob ?? "*",
    debounceSeconds: String(trigger.config.debounceSeconds ?? "2"),
    recursive: trigger.config.recursive ?? true,
    inputs: Object.keys(trigger.inputs ?? {}).length ? JSON.stringify(trigger.inputs, null, 2) : "",
    dailyMaxCostUsd: limitText(trigger.dailyMaxCostUsd),
    dailyMaxTokens: limitText(trigger.dailyMaxTokens),
    // An older backend sends no Time limit; leave the field as it found it.
    maxRunTime: trigger.maxRunSeconds === undefined ? "" : formatRunTimeInput(trigger.maxRunSeconds),
    waitForUsageLimit: trigger.waitForUsageLimit !== false,
    runEditsWithoutApproval: Boolean(trigger.runEditsWithoutApproval),
  };
}

// `editing` is the saved trigger when changing one: a daily limit is sent only when it
// changed, and an emptied field removes it.
export function triggerBodyFromDraft(draft, editing = null) {
  const body = {
    label: draft.label.trim(),
    overlap: draft.overlap,
    missed: draft.missed,
    runEditsWithoutApproval: Boolean(draft.runEditsWithoutApproval),
  };
  const cost = parseDailyLimit(draft.dailyMaxCostUsd, "cost");
  const tokens = parseDailyLimit(draft.dailyMaxTokens, "token", { whole: true });
  if (editing ? cost !== (editing.dailyMaxCostUsd ?? null) : cost !== null) body.dailyMaxCostUsd = cost;
  if (editing ? tokens !== (editing.dailyMaxTokens ?? null) : tokens !== null) body.dailyMaxTokens = tokens;
  const runTime = parseRunTime(draft.maxRunTime);
  if (editing ? editing.maxRunSeconds !== undefined && runTime !== (editing.maxRunSeconds ?? null) : true) {
    body.maxRunSeconds = runTime;
  }
  // An older backend sends no switch; send it only when the user changed it from on.
  const wait = draft.waitForUsageLimit !== false;
  if (editing ? wait !== (editing.waitForUsageLimit !== false) : !wait) body.waitForUsageLimit = wait;
  if (draft.kind === "cron") {
    body.config = { expression: draft.expression, timezone: draft.timezone };
  } else if (draft.kind === "file") {
    const debounceSeconds = Number(draft.debounceSeconds);
    if (draft.debounceSeconds.trim() === "" || !Number.isFinite(debounceSeconds)) {
      throw new Error("Debounce must be a number of seconds.");
    }
    body.config = { path: draft.path, glob: draft.glob, debounceSeconds, recursive: draft.recursive };
  } else {
    body.config = {};
  }
  if (draft.inputs.trim()) {
    let inputs;
    try {
      inputs = JSON.parse(draft.inputs);
    } catch {
      throw new Error("Inputs must be valid JSON.");
    }
    if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) {
      throw new Error('Inputs must be a JSON object, such as {"topic": "news"}.');
    }
    body.inputs = inputs;
  } else {
    body.inputs = {};
  }
  return body;
}

export function webhookCurl(url, token) {
  return `curl -X POST -H 'Authorization: Bearer ${token}' -H 'Content-Type: application/json' -d '{}' ${url}`;
}

// Approve this version names the files it couldn't show when it refuses (error.unseen).
const send = (path, options = {}) =>
  apiJson(`/rattish/triggers${path}`, { ...options, failure: status => `Request failed (${status}).` });

export const triggersApi = {
  load: (workflowId) => send(`?workflowId=${encodeURIComponent(workflowId)}`),
  create: (workflowId, kind, body) => send("", { method: "POST", body: { ...body, workflowId, kind } }),
  update: (triggerId, body) => send(`/${encodeURIComponent(triggerId)}`, { method: "POST", body: body ?? {} }),
  remove: (triggerId) => send(`/${encodeURIComponent(triggerId)}`, { method: "DELETE" }),
  rotateToken: (triggerId) => send(`/${encodeURIComponent(triggerId)}/rotate-token`, { method: "POST", body: {} }),
  // What changed since the approved version, and approving the exact version reviewed.
  changes: (triggerId) => send(`/${encodeURIComponent(triggerId)}/changes`),
  // acceptUnseen is the second confirmation for changes that weren't shown in full.
  approve: (triggerId, fingerprint, { acceptUnseen = false } = {}) =>
    send(`/${encodeURIComponent(triggerId)}/approve`, { method: "POST", body: acceptUnseen ? { fingerprint, acceptUnseen: true } : { fingerprint } }),
  setStartAtLogin: (enabled) => send("/login", { method: "POST", body: { enabled } }),
  previewCron: (expression, timezone) => send("/preview", { method: "POST", body: { expression, timezone } }),
  // Every trigger on this computer at once. Each keeps its own on/off setting.
  pauseState: () => send("/pause"),
  setPaused: (paused) => send("/pause", { method: "POST", body: { paused } }),
  // The computer-wide daily limit over every triggered run. null removes a part.
  setBudget: (budget) => send("/budget", { method: "POST", body: budget ?? {} }),
};

// A trigger's or the computer's daily limit, as its card or the runs view shows it.
export function dailyLimitText(today) {
  if (!today) return "";
  const text = `Daily limit: ${today.summary}.`;
  return today.reached ? `${text} Triggered starts are skipped until midnight (${today.timezone}).` : text;
}

export function budgetSummary(dailyBudget) {
  if (!dailyBudget?.today) return "No daily limit for triggered runs.";
  return dailyLimitText(dailyBudget.today);
}

// The Pause all triggers switch's line under its label.
export function pauseSummary(state) {
  if (!state) return "";
  const { triggers = 0, enabled = 0 } = state;
  if (state.pause?.paused) {
    const since = dateTime(state.pause.pausedAt, { weekday: true });
    return `Paused${since ? ` since ${since}` : ""}. Cron times are skipped, not caught up. Resuming turns back on the ${enabled} of ${triggers} that were on.`;
  }
  if (!triggers) return "No triggers yet.";
  return `${enabled} of ${triggers} trigger${triggers === 1 ? "" : "s"} on.`;
}
