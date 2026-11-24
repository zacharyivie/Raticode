import nodeTest from "node:test";
import assert from "node:assert/strict";
import {
  MAX_QUOTED_BYTES,
  askRemAboutRunFailure,
  askRemAboutTerminalSelection,
  diagnosticForRem,
  fetchRunFailure,
  quotedTail,
  runCanAskRem,
  shownRunId,
  terminalSelectionForRem,
  timelineRowCanAskRem,
} from "./askRemAboutFailure.js";

// apiUrl reads the desktop's API address from the window; the default is /api. Each test
// gets a bare window only while it runs: every frontend test file loads in one process,
// and a window set while this file loads made App.test's modules fail to load.
function test(name, fn) {
  return nodeTest(name, async (t) => {
    const had = "window" in globalThis;
    const previous = globalThis.window;
    globalThis.window ??= {};
    try {
      return await fn(t);
    } finally {
      if (had) globalThis.window = previous;
      else delete globalThis.window;
    }
  });
}

function fakeFetch(status, body, seen = []) {
  return async (url) => {
    seen.push(url);
    return { ok: status < 400, status, json: async () => body };
  };
}

test("asking about a run reads its summary and attaches it without sending", async () => {
  const seen = [];
  const asked = [];
  const payload = await askRemAboutRunFailure(
    { runId: "r 1", workflowId: "deploy", nodeId: "ship" },
    { fetcher: fakeFetch(200, { name: "run-failure-r1-ship.txt", text: "summary" }, seen), ask: item => asked.push(item) },
  );
  assert.equal(seen[0], "/api/rattish/runs/r%201/failure?workflowId=deploy&nodeId=ship");
  assert.deepEqual(asked, [{ name: "run-failure-r1-ship.txt", text: "summary" }]);
  assert.equal(payload.text, "summary");
});

test("a run the backend can't find becomes a sentence to show", async () => {
  await assert.rejects(fetchRunFailure({ runId: "gone" }, fakeFetch(404, { error: "Run 'gone' was not found." })), /was not found/);
  await assert.rejects(fetchRunFailure({ runId: "" }, fakeFetch(200, {})), /Pick a run first/);
  const seen = [];
  await fetchRunFailure({ runId: "r1" }, fakeFetch(200, {}, seen));
  assert.equal(seen[0], "/api/rattish/runs/r1/failure");
});

test("only failed and interrupted runs and failed timeline rows offer it", () => {
  assert.equal(runCanAskRem("error"), true);
  assert.equal(runCanAskRem("disconnected"), true);
  assert.equal(runCanAskRem("success"), false);
  assert.equal(runCanAskRem("running"), false);
  for (const status of ["failed", "allowed failure", "stopped"]) assert.equal(timelineRowCanAskRem({ status }), true, status);
  for (const status of ["completed", "started", "cached", "reused", "retried"]) assert.equal(timelineRowCanAskRem({ status }), false, status);
});

test("quoted text keeps the last 200 lines or 8 KB, cut at a whole line", () => {
  assert.deepEqual(quotedTail("a\nb"), { text: "a\nb", note: "all 2 lines" });
  const many = Array.from({ length: 500 }, (_, index) => `line ${index}`).join("\n");
  const lines = quotedTail(many);
  assert.equal(lines.text.split("\n")[0], "line 300");
  assert.equal(lines.note, "last 200 of 500 lines, cut to 200 lines");
  const wide = Array.from({ length: 150 }, (_, index) => `${String(index).padStart(3, "0")} ${"é".repeat(60)}`).join("\n");
  const bytes = quotedTail(wide);
  assert.ok(new TextEncoder().encode(bytes.text).length <= MAX_QUOTED_BYTES);
  assert.match(bytes.text.split("\n")[0], /^\d{3} é{60}$/);
  assert.match(bytes.note, /cut to 8 KB$/);
});

test("a source problem names the file and line, other preflight problems, and is untrusted", () => {
  const problem = { severity: "error", code: "RATTISH_UNKNOWN_NODE", phase: "semantic", message: "Node 'shpi' doesn't exist.", span: { start: { line: 12, column: 7 } } };
  const missing = { severity: "error", code: "RATTISH_PROVIDER_MISSING", phase: "preflight", message: "codex isn't installed." };
  const text = diagnosticForRem(problem, { workflowName: "Deploy", workflowPath: "/p/workflow.rattish", diagnostics: [problem, missing] }, "abcd1234");
  assert.match(text, /^Raticode workflow problem from the Problems panel/);
  assert.match(text, /----- BEGIN UNTRUSTED WORKFLOW TEXT abcd1234 -----/);
  assert.match(text, /Workflow file: \/p\/workflow\.rattish/);
  assert.match(text, /Problem: error RATTISH_UNKNOWN_NODE at line 12, column 7/);
  assert.match(text, /- error RATTISH_PROVIDER_MISSING: codex isn't installed\./);
  assert.ok(text.endsWith("----- END UNTRUSTED WORKFLOW TEXT abcd1234 -----\n"));
});

test("a terminal selection is limited, names the shell and folder, and is untrusted", () => {
  const selection = Array.from({ length: 300 }, (_, index) => `out ${index}`).join("\n");
  const text = terminalSelectionForRem(selection, { shell: "zsh", cwd: "/p" }, "feedbeef");
  assert.match(text, /Terminal: zsh in \/p/);
  assert.match(text, /Selected text \(last 200 of 300 lines, cut to 200 lines\):\nout 100\n/);
  assert.match(text, /----- BEGIN UNTRUSTED TERMINAL OUTPUT feedbeef -----/);
  // Select all takes the terminal's empty rows; they are dropped, and trailing spaces go.
  const padded = terminalSelectionForRem("\n\n$ npm run build   \r\nnpm ERR! missing script: build\n" + "  \n".repeat(21), {}, "0a0b0c0d");
  assert.match(padded, /Selected text \(all 2 lines\):\n\$ npm run build\nnpm ERR! missing script: build\n----- END/);
  const asked = [];
  assert.equal(askRemAboutTerminalSelection("  ", {}, item => asked.push(item)), false);
  assert.equal(askRemAboutTerminalSelection("npm ERR!", { shell: "bash" }, item => asked.push(item)), true);
  assert.equal(asked[0].name, "terminal-selection.txt");
  // A random tag each time, so quoted text can't close the block.
  assert.notEqual(terminalSelectionForRem("x").match(/[0-9a-f]{8}/)[0], terminalSelectionForRem("x").match(/[0-9a-f]{8}/)[0]);
});

test("the Studio's shown run is the picked one or the open record", () => {
  assert.equal(shownRunId({ selectedRunId: "picked", path: "/d/runs/other.json" }), "picked");
  assert.equal(shownRunId({ path: "/d/rattish-runs/deploy/20261116T101010Z-abc.json" }), "20261116T101010Z-abc");
  assert.equal(shownRunId({ path: "/d/logs/run.log" }), "");
  assert.equal(shownRunId(null), "");
});
