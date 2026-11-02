import assert from "node:assert/strict";
import { test } from "node:test";
import { canHighlight, codeLanguage, tokenize } from "./highlight.js";
import { chartScale, chartSummary, parseRemChart } from "./remChart.js";
import {
  bodyWithoutCardLinks, checksSummary, elapsedLabel, latestSuggestionMessageId, messageCopyText, quoteForReply,
  remPresence, replyMeta, resultActions, runSteps, stepText,
} from "./remRich.js";

test("progress steps follow the run and say their state in words", () => {
  assert.deepEqual(runSteps("queued").map(step => step.state), ["current", "todo", "todo", "todo"]);
  assert.deepEqual(runSteps("executing").map(step => step.state), ["done", "current", "todo", "todo"]);
  assert.deepEqual(runSteps("repairing").map(step => step.state), ["done", "done", "current", "todo"]);
  assert.deepEqual(runSteps("succeeded").map(step => step.state), ["done", "done", "done", "done"]);
  assert.deepEqual(runSteps("failed").map(step => step.state), ["done", "stopped", "todo", "todo"]);
  assert.deepEqual(runSteps("blocked", { reachedCheck: true }).map(step => step.state), ["done", "done", "stopped", "todo"]);
  assert.deepEqual(runSteps("mystery"), []);
  assert.equal(stepText(runSteps("executing")[1]), "Work in progress");
});

test("elapsed time reads in seconds, minutes and hours", () => {
  const start = "2026-11-01T10:00:00Z";
  assert.equal(elapsedLabel(start, new Date("2026-11-01T10:00:20Z")), "20 s");
  assert.equal(elapsedLabel(start, "2026-11-01T10:04:10Z"), "4 min");
  assert.equal(elapsedLabel(start, "2026-11-01T11:05:00Z"), "1 h 5 min");
  assert.equal(elapsedLabel("", new Date()), "");
});

test("check counts carry a symbol and words, never only a color", () => {
  assert.equal(checksSummary({ passed: 3, failed: 0, total: 3 }).text, "3 of 3 checks passed");
  assert.equal(checksSummary({ passed: 3, failed: 0, total: 3 }).symbol, "✓");
  assert.equal(checksSummary({ passed: 1, failed: 1, total: 3 }).text, "1 of 3 checks failed");
  assert.equal(checksSummary({ passed: 0, failed: 0, total: 2 }).text, "2 checks not run yet");
  assert.equal(checksSummary({ passed: 1, failed: 0, total: 2 }).text, "1 of 2 checks passed so far");
  assert.equal(checksSummary({ total: 0 }), null);
});

test("report links the card shows as buttons leave the text, other text stays", () => {
  const link = "[report](</tmp/reports/report.html>)";
  const body = `Saved the report.\n\nSee [the guide](https://example.com) too.\n\n${link}`;
  const result = { artifacts: [{ link, path: "/tmp/reports/report.html" }] };
  assert.equal(bodyWithoutCardLinks(body, result), "Saved the report.\n\nSee [the guide](https://example.com) too.");
  assert.equal(bodyWithoutCardLinks(`Inline ${link} stays.`, result), `Inline ${link} stays.`);
  assert.equal(bodyWithoutCardLinks(body, null), body);
});

test("reply details, quotes and copy text", () => {
  assert.equal(replyMeta({ provider: "claude_code", model: "claude-sonnet-5-5", seconds: 4.24 }), "claude-sonnet-5-5 · 4.2 s");
  assert.equal(replyMeta({ provider: "codex", model: "cli-default", seconds: null }), "codex");
  assert.equal(replyMeta(null), "");
  assert.equal(quoteForReply("**Done.** See [the report](</x.html>)."), "> Done. See the report.\n\n");
  assert.equal(quoteForReply("x".repeat(400)).length, 2 + 280 + 2);
  assert.equal(messageCopyText({ body: "Lead.", details: "| a |\n|---|\n| 1 |" }), "Lead.\n\n| a |\n|---|\n| 1 |");
});

test("presence names what Rem is doing", () => {
  assert.deepEqual(remPresence({ replying: true, turnLabel: "Reading earlier messages" }), { state: "thinking", label: "Reading earlier messages…" });
  assert.equal(remPresence({ status: "1 thing needs you" }).state, "attention");
  assert.equal(remPresence({ status: "Working on 2 things" }).state, "working");
  assert.equal(remPresence({ offline: true }).label, "Not connected");
  assert.equal(remPresence({ status: "All caught up" }).state, "idle");
});

test("follow-ups belong to the newest Rem message only", () => {
  const rem = { id: "a", role: "rem", suggestions: ["Why?"] };
  assert.equal(latestSuggestionMessageId([rem]), "a");
  assert.equal(latestSuggestionMessageId([rem, { id: "b", role: "user" }]), null);
  assert.equal(latestSuggestionMessageId([{ id: "c", role: "rem" }]), null);
});

test("code tokens keep every character and mark keywords, strings and comments", () => {
  const code = "const x = 'a'; // note\nreturn 42;";
  const tokens = tokenize(code, "js");
  assert.equal(tokens.map(token => token.text).join(""), code);
  assert.deepEqual(tokens.filter(token => token.type !== "text").map(token => [token.type, token.text]), [
    ["keyword", "const"], ["string", "'a'"], ["comment", "// note"], ["keyword", "return"], ["number", "42"],
  ]);
  assert.deepEqual(tokenize("# hi\nx = None", "py").filter(token => token.type !== "text").map(token => token.type), ["comment", "keyword"]);
  assert.equal(codeLanguage("language-TypeScript"), "ts");
  assert.equal(canHighlight("brainfuck"), false);
  assert.deepEqual(tokenize("<b>x</b>", "html").map(token => token.type), ["tag", "text", "tag"]);
  assert.deepEqual(tokenize("x", "unknown"), [{ type: "text", text: "x" }]);
});

test("rem-chart blocks parse to a bounded chart or fall back", () => {
  const chart = parseRemChart(JSON.stringify({ type: "bar", title: "Runs", labels: ["Mon", "Tue"], series: [{ name: "Done", values: [3, "x"] }] }));
  assert.deepEqual(chart, { type: "bar", title: "Runs", labels: ["Mon", "Tue"], series: [{ name: "Done", values: [3, null] }] });
  assert.equal(parseRemChart("{not json"), null);
  assert.equal(parseRemChart(JSON.stringify({ type: "pie", labels: ["a"], series: [{ values: [1] }] })), null);
  assert.equal(parseRemChart(JSON.stringify({ type: "line", labels: Array(51).fill("x"), series: [{ values: [1] }] })), null);
  const scale = chartScale({ series: [{ values: [3, 17] }] });
  assert.equal(scale.low, 0);
  assert.ok(scale.high >= 17 && scale.ticks.includes(0));
  assert.match(chartSummary(chart), /^Bar chart: Runs\. 2 points for Done/);
});

test("action chips come from the task record and show only when their control works", () => {
  const work = { task_id: "t1", run_id: "r1" };
  const report = { path: "/r/report.html", title: "Report" };
  const update = { id: "u1", role: "rem", kind: "task_update", work, result: { artifacts: [report] } };
  const finished = { id: "t1", last_run: { id: "r1", state: "succeeded" }, active_run: null };
  const ids = (message, tasks, options) => resultActions(message, tasks, options).map(action => action.id);
  assert.deepEqual(ids(update, [finished]), ["open", "run"]);
  // A run whose review was interrupted can retry it; the desktop decides with review_retryable.
  const interrupted = { ...finished, last_run: { id: "r1", state: "blocked", review_retryable: true } };
  assert.deepEqual(ids(update, [interrupted]), ["open", "retry", "run"]);
  assert.equal(resultActions(update, [interrupted])[1].run.id, "r1");
  // An older run's update never offers to retry the newer run's review.
  assert.deepEqual(ids({ ...update, work: { ...work, run_id: "r0" } }, [interrupted]), ["open", "run"]);
  // A running task, a deleted or missing task, or a stale card offers no task controls.
  assert.deepEqual(ids(update, [{ ...interrupted, active_run: { id: "r2", state: "executing" } }]), ["open"]);
  assert.deepEqual(ids(update, [{ ...finished, deleted_at: "now" }]), ["open"]);
  assert.deepEqual(ids(update, []), ["open"]);
  assert.deepEqual(ids(update, [finished], { live: false }), []);
  // Only task updates get chips, whatever Rem wrote; a reply that merely mentions a task gets none.
  assert.deepEqual(ids({ ...update, kind: undefined, body: "Run again? Retry review?" }, [interrupted]), []);
  assert.deepEqual(ids({ ...update, result: undefined }, [finished]), ["run"]);
});
