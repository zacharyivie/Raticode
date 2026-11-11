// Rich parts of Rem's chat: result cards, task progress, follow-ups and reply details.
// The phone app has a Kotlin twin of these rules (RemRich.kt), so both say the same thing.

import { plainMarkdown } from "./remHome.js";

// Plan · Work · Check · Report, with the step a run is on.
const STEP_KEYS = ["plan", "work", "check", "report"];
const STEP_LABELS = { plan: "Plan", work: "Work", check: "Check", report: "Report" };
const STEP_AT = { queued: 0, executing: 1, cancelling: 1, verifying: 2, repairing: 2 };
const ENDED = new Set(["failed", "blocked", "cancelled", "outcome_unknown"]);

// The desktop sends each run's `step`. It matters for a queued run: one whose work is
// done but waits to be checked again (a retried review, a restart's recovery check) is
// at Check, not Plan. Older desktops send no step; the state decides, as before.
const STEP_INDEX = { plan: 0, work: 1, check: 2 };

export function runSteps(state, { reachedCheck = false, step } = {}) {
  const at = state === "succeeded" ? 4
    : !(state in STEP_AT) && !ENDED.has(state) ? -1
      : STEP_INDEX[step] ?? STEP_AT[state] ?? (reachedCheck ? 2 : 1);
  if (at < 0) return [];
  const stopped = ENDED.has(state);
  return STEP_KEYS.map((key, index) => ({
    key,
    label: STEP_LABELS[key],
    state: index < at ? "done" : index === at ? (stopped ? "stopped" : "current") : "todo",
  }));
}

// Words, not just color: "Plan done", "Work in progress", "Check not started".
export function stepText(step) {
  return `${step.label} ${{ done: "done", current: "in progress", stopped: "stopped", todo: "not started" }[step.state]}`;
}

export function elapsedLabel(fromIso, until = new Date()) {
  const start = Date.parse(fromIso || "");
  const end = until instanceof Date ? until.getTime() : Date.parse(until || "");
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${Math.max(seconds, 1)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

// A check count with a symbol and words, so it reads the same without color.
export function checksSummary(checks) {
  const total = Number(checks?.total) || 0;
  if (!total) return null;
  const passed = Number(checks.passed) || 0;
  const failed = Number(checks.failed) || 0;
  if (failed) return { tone: "danger", symbol: "✕", text: `${failed} of ${total} checks failed` };
  if (passed === total) return { tone: "success", symbol: "✓", text: `${passed} of ${total} checks passed` };
  if (!passed) return { tone: "neutral", symbol: "○", text: total === 1 ? "Check not run yet" : `${total} checks not run yet` };
  return { tone: "neutral", symbol: "◐", text: `${passed} of ${total} checks passed so far` };
}

// The text without the report links a result card already shows as buttons.
// Only whole paragraphs that are exactly one of the card's links are dropped.
export function bodyWithoutCardLinks(body, result) {
  const links = new Set((result?.artifacts || []).map(artifact => String(artifact.link || "").trim()).filter(Boolean));
  if (!links.size) return String(body ?? "");
  const kept = String(body ?? "").split(/\n{2,}/).filter(paragraph => !links.has(paragraph.trim()));
  return kept.join("\n\n").trim();
}

export function fileSize(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function artifactLine(artifact) {
  return [String(artifact.kind || "file").toUpperCase(), fileSize(artifact.size)].join(" · ");
}

// "claude-sonnet-5-5 · 4.2 s" under a reply, when the desktop recorded it.
export function replyMeta(meta) {
  if (!meta) return "";
  const seconds = typeof meta.seconds === "number" && Number.isFinite(meta.seconds) ? `${meta.seconds.toFixed(1)} s` : "";
  const model = meta.model && meta.model !== "cli-default" ? meta.model : meta.provider || "";
  return [model, seconds].filter(Boolean).join(" · ");
}

// "Reply" links a new message to this one. The desktop stores only the ID and builds
// the same preview when the chat is read (reference_preview in rem_home_store.py).
export function replyTarget(message, limit = 280) {
  const plain = plainMarkdown(String(message?.body ?? "")).replace(/\s+/g, " ").trim();
  const files = (message?.attachments?.length || 0) + (message?.files?.length || 0);
  return {
    id: message.id,
    role: message.role || "rem",
    body: plain.length > limit ? `${plain.slice(0, limit - 1).trimEnd()}…` : plain,
    created_at: message.created_at || "",
    ...(message.kind === "task_update" ? { kind: "task_update" } : {}),
    ...(message.work?.title ? { title: message.work.title } : {}),
    ...(files ? { files } : {}),
  };
}

// Who wrote the message a reply refers to, and the line shown from it.
export function referenceLines(reference) {
  if (!reference?.id) return null;
  if (reference.unavailable) return { author: "an earlier message", text: "No longer in this chat" };
  const author = reference.role === "user" ? "You" : reference.kind === "task_update" ? "Rem · Task update" : "Rem";
  const files = reference.files ? (reference.files === 1 ? "1 file" : `${reference.files} files`) : "";
  return { author, text: reference.body || (reference.title ? `Task: ${reference.title}` : "") || files || "Message" };
}

// Copy puts the reply and its details on the clipboard as Markdown.
export function messageCopyText(message) {
  return [message?.body, message?.details].map(part => String(part ?? "").trim()).filter(Boolean).join("\n\n");
}

// Rem's presence in the header: what it is doing, as a state and a word.
export function remPresence({ offline = false, replying = false, turnLabel = "", tasks = [], status = "" } = {}) {
  if (offline) return { state: "offline", label: "Not connected" };
  if (replying) return { state: "thinking", label: turnLabel ? `${turnLabel}…` : "Thinking…" };
  if (/needs? you/.test(status)) return { state: "attention", label: status };
  if (/^Working on/.test(status) || tasks.some(task => ["queued", "executing", "verifying", "repairing"].includes(task?.active_run?.state))) {
    return { state: "working", label: status };
  }
  return { state: "idle", label: status };
}

// Follow-up prompts show only under the newest Rem message, and only until you answer.
export function latestSuggestionMessageId(messages = []) {
  const last = messages.at(-1);
  return last && last.role !== "user" && last.suggestions?.length ? last.id : null;
}

// Long messages fold to a preview of about a third of the screen. Only a message at
// least half again as tall folds, so folding always hides a useful amount. The phone
// uses the same numbers in dp (RemRich.collapseLimits).
export function collapseLimits(screenHeight) {
  const height = Number(screenHeight) > 0 ? Number(screenHeight) : 800;
  const preview = Math.round(Math.min(360, Math.max(168, height * 0.3)));
  return { preview, threshold: Math.round(preview * 1.5) };
}

export function shouldCollapse(contentHeight, screenHeight) {
  return contentHeight > collapseLimits(screenHeight).threshold;
}

// Rem's newest message opens in full, since it is the one being read; older long
// messages and long messages you sent start folded.
export function latestRemMessageId(messages = []) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role !== "user") return messages[index]?.id ?? null;
  }
  return null;
}

const ACTIVE = new Set(["queued", "executing", "verifying", "repairing", "cancelling"]);

// Action chips under the newest update about a finished task. They come only from the
// task record, never from text Rem wrote, and appear only when the control would work:
// Open report needs a saved report, Run again a task that isn't running, and Retry
// review the run whose review was interrupted (the desktop marks it review_retryable).
export function resultActions(message, tasks = [], { live = true, reviewRetry = true } = {}) {
  if (!live || message?.role === "user" || message?.kind !== "task_update" || !message.work) return [];
  const actions = [];
  const report = message.result?.artifacts?.[0];
  if (report) actions.push({ id: "open", label: "Open report", artifact: report });
  const task = tasks.find(item => item.id === message.work.task_id);
  if (!task || task.deleted_at) return actions;
  if (task.active_run && ACTIVE.has(task.active_run.state)) return actions;
  // A task waiting for others: retry each prerequisite that didn't succeed, or start it now.
  if (task.dependency) {
    for (const item of task.dependency.prerequisites || []) {
      if (item.retryable) actions.push({ id: `retry-after:${item.task_id}`, label: `Retry “${item.title}”`, task: { id: item.task_id } });
    }
    actions.push({ id: "run", label: "Start now", task });
    return actions;
  }
  const run = task.last_run;
  if (reviewRetry && run?.id === message.work.run_id && run.review_retryable === true) actions.push({ id: "retry", label: "Retry review", task, run });
  actions.push({ id: "run", label: "Run again", task });
  return actions;
}
