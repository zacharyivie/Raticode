import { apiUrl } from "./api.js";

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
};

const PROBLEM_STATUSES = new Set(["failed", "interrupted", "missed", "skipped"]);

export function isProblemStatus(status) {
  return PROBLEM_STATUSES.has(status);
}

export function statusLabel(status) {
  return STATUS_LABELS[status] ?? status;
}

export function triggerTitle(trigger) {
  return trigger.label || KIND_LABELS[trigger.kind] || trigger.kind;
}

export function triggerDetail(trigger) {
  if (trigger.kind === "cron") return `${trigger.config.expression} · ${trigger.config.timezone}`;
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
  return parts.join(" · ");
}

export function schedulerSummary(scheduler) {
  if (!scheduler?.running) {
    return { running: false, text: "Not running. Nothing will fire until Raticode or its scheduler starts." };
  }
  return {
    running: true,
    text: scheduler.owner === "app" ? "Running in Raticode" : "Running in the background scheduler",
  };
}

export function formatWhen(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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
  };
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
  };
}

export function triggerBodyFromDraft(draft) {
  const body = { label: draft.label.trim(), overlap: draft.overlap, missed: draft.missed };
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

async function send(path, options) {
  const response = await fetch(apiUrl(`/rattish/triggers${path}`), options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function post(path, body) {
  return send(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const triggersApi = {
  load: (workflowId) => send(`?workflowId=${encodeURIComponent(workflowId)}`),
  create: (workflowId, kind, body) => post("", { ...body, workflowId, kind }),
  update: (triggerId, body) => post(`/${encodeURIComponent(triggerId)}`, body),
  remove: (triggerId) => send(`/${encodeURIComponent(triggerId)}`, { method: "DELETE" }),
  rotateToken: (triggerId) => post(`/${encodeURIComponent(triggerId)}/rotate-token`),
  setStartAtLogin: (enabled) => post("/login", { enabled }),
  previewCron: (expression, timezone) => post("/preview", { expression, timezone }),
};
