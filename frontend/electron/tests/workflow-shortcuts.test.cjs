// Workflow shortcuts in the main process (workflow-shortcuts.cjs): registering keys,
// what a press does, following the run, and the Run sheet's page. The backend, the
// clipboard, notices and the sheet window are fakes; the Wayland test uses a fake KDE
// shortcut service on a private bus. Nothing reaches the developer's desktop.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  CAUTION,
  MAX_TEXT_CHARS,
  createWorkflowShortcuts,
  pollDelay,
  runCommand,
  shortcutFromArgv,
} = require("../workflow-shortcuts.cjs");
const { connect } = require("../dbus-session.cjs");
const { createLinuxShortcuts } = require("../linux-shortcuts.cjs");
const { dbusDaemonInstalled, startBus, startFakeKde } = require("./fake-desktop-bus.cjs");

const electronDir = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(electronDir, name), "utf8");
const ID_A = "aaaaaaaaaaa1";
const ID_B = "bbbbbbbbbbb2";

function fakeShortcuts({ refuse = [] } = {}) {
  const shortcuts = {
    registered: new Map(),
    register(accelerator, callback) {
      if (refuse.includes(accelerator) || shortcuts.registered.has(accelerator)) return false;
      shortcuts.registered.set(accelerator, callback);
      return true;
    },
    unregister(accelerator) { shortcuts.registered.delete(accelerator); },
    press(accelerator) { shortcuts.registered.get(accelerator)?.(); },
  };
  return shortcuts;
}

/** A fake backend: the list, each shortcut's target, run starts and the runs list. */
function fakeBackend({ list = [], targets = {}, runs = {} } = {}) {
  const backend = { list, targets, runs, calls: [], startError: "", startCode: "", approvals: {} };
  backend.api = async (method, requestPath, body) => {
    backend.calls.push([method, requestPath, body]);
    if (method === "GET" && requestPath === "/api/workflow-shortcuts") return { shortcuts: backend.list };
    const detail = requestPath.match(/^\/api\/workflow-shortcuts\/(\w+)$/);
    if (method === "GET" && detail) {
      const target = backend.targets[detail[1]];
      if (!target) throw Object.assign(new Error("No workflow shortcut has that ID."), { status: 404 });
      const approval = backend.approvals[detail[1]] ? { state: backend.approvals[detail[1]] } : { state: "approved" };
      return { shortcut: { id: detail[1], target, approval } };
    }
    const start = requestPath.match(/^\/api\/workflow-shortcuts\/(\w+)\/run$/);
    if (method === "POST" && start) {
      if (backend.startCode) throw Object.assign(new Error(backend.startError), { status: 409, code: backend.startCode, state: "changed", workflowName: "Summarize" });
      if (backend.startError) throw Object.assign(new Error(backend.startError), { status: 400 });
      const runId = `run-${backend.calls.filter(([verb, where]) => verb === "POST" && where.endsWith("/run")).length}`;
      backend.runs[runId] = { id: runId, status: "running", projectRoot: "/p" };
      return { run: { workflowId: "wf", workflowName: backend.targets[start[1]]?.workflowName || "Flow", projectRoot: "/p", runId, status: "running" } };
    }
    if (method === "GET" && requestPath.startsWith("/api/rattish/runs?")) return { runs: Object.values(backend.runs) };
    throw new Error(`unexpected ${method} ${requestPath}`);
  };
  return backend;
}

function fakeSheet() {
  const sheet = {
    state: null,
    handlers: null,
    closed: 0,
    open(state, handlers) { sheet.state = { ...state }; sheet.handlers = handlers; },
    update(partial) { sheet.state = { ...sheet.state, ...partial }; },
    close() { sheet.state = null; sheet.closed += 1; },
    isOpen(id) { return sheet.state?.id === id; },
    focus() { sheet.focused = (sheet.focused || 0) + 1; },
  };
  return sheet;
}

/** Timers run when the test says so. */
function fakeTimers() {
  const pending = new Set();
  return {
    setTimer(callback, delay) { const timer = { callback, delay }; pending.add(timer); return timer; },
    clearTimer(timer) { pending.delete(timer); },
    async runAll(filter = () => true) {
      for (const timer of [...pending]) {
        if (!filter(timer)) continue;
        pending.delete(timer);
        await timer.callback();
      }
      await new Promise((resolve) => setImmediate(resolve));
    },
    pending,
  };
}

function setup({ list, targets, clipboard = "", desktopShortcuts = null, refuse = [] } = {}) {
  const shortcuts = fakeShortcuts({ refuse });
  const backend = fakeBackend({ list, targets });
  const sheet = fakeSheet();
  const timers = fakeTimers();
  const notices = [];
  const opened = [];
  const reviews = [];
  const controller = createWorkflowShortcuts({
    globalShortcut: shortcuts,
    desktopShortcuts,
    api: backend.api,
    readClipboard: () => clipboard,
    sheet,
    notify: (notice) => { const shown = { ...notice, closed: false, close() { shown.closed = true; } }; notices.push(shown); return shown; },
    openRun: (run) => opened.push(run),
    openReview: (review) => reviews.push(review),
    showApp: () => opened.push("app"),
    commandFor: (id) => `Raticode --run-workflow-shortcut=${id}`,
    now: () => 1000,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  return { controller, shortcuts, backend, sheet, timers, notices, opened, reviews };
}

const LIST = [
  { id: ID_A, workflowId: "wf-a", workflowName: "Tidy", binding: "Ctrl+Alt+KeyT", available: true },
  { id: ID_B, workflowId: "wf-b", workflowName: "Summarize", binding: "Ctrl+Alt+KeyS", available: true },
];

test("the command-line flag names one shortcut and nothing else", () => {
  assert.equal(shortcutFromArgv(["raticode", `--run-workflow-shortcut=${ID_A}`]), ID_A);
  assert.equal(shortcutFromArgv(["raticode", "--run-workflow-shortcut=../../x"]), "");
  assert.equal(shortcutFromArgv(["raticode", "--run-workflow-shortcut", ID_A]), "");
  assert.equal(shortcutFromArgv(null), "");
  assert.equal(runCommand("env XDG_CONFIG_HOME=/c /opt/Raticode --toggle-dictation", ID_A), `env XDG_CONFIG_HOME=/c /opt/Raticode --run-workflow-shortcut=${ID_A}`);
  assert.equal(runCommand("", ID_A), "");
  assert.equal(pollDelay(0), 2000);
  assert.equal(pollDelay(60_000), 5000);
  assert.equal(pollDelay(600_000), 15000);
});

test("sync registers each key and gives the dictation shortcuts their keys first", async () => {
  const { controller, shortcuts, backend } = setup({ list: LIST });
  const status = await controller.sync();
  assert.deepEqual([...shortcuts.registered.keys()], ["Control+Alt+T", "Control+Alt+S"]);
  assert.ok(status.shortcuts.every((item) => item.registered && !item.error));

  // Dictation takes Ctrl+Alt+S: the workflow lets go and says why.
  const reserved = controller.reserve({ "Control+Alt+S": "Dictate to Rem from any app" });
  assert.deepEqual([...shortcuts.registered.keys()], ["Control+Alt+T"]);
  const summarize = reserved.shortcuts.find((item) => item.id === ID_B);
  assert.match(summarize.error, /Dictate to Rem from any app uses this key/);

  // `rattish shortcuts remove` on the CLI: the next sync lets go of the key.
  backend.list = [LIST[0]];
  await controller.sync();
  assert.deepEqual([...shortcuts.registered.keys()], ["Control+Alt+T"]);
  backend.list = [];
  const empty = await controller.sync();
  assert.equal(shortcuts.registered.size, 0);
  assert.deepEqual(empty.shortcuts, []);
  controller.dispose();
});

test("a key another app holds, a bad binding and a missing workflow are reported", async () => {
  const { controller } = setup({
    refuse: ["Control+Alt+T"],
    list: [
      LIST[0],
      { id: ID_B, workflowId: "wf-b", workflowName: "Summarize", binding: "KeyS", available: true },
      { id: "ccccccccccc3", workflowId: "wf-c", workflowName: "Gone", binding: "Ctrl+Alt+KeyG", available: false },
    ],
  });
  const status = await controller.sync();
  const byId = Object.fromEntries(status.shortcuts.map((item) => [item.id, item]));
  assert.match(byId[ID_A].error, /Another app already uses this shortcut/);
  assert.match(byId[ID_B].error, /modifier/);
  assert.match(byId.ccccccccccc3.error, /isn't in an open project/);
  controller.dispose();
});

test("recording a key in Settings suspends the shortcuts and resuming brings them back", async () => {
  const { controller, shortcuts } = setup({ list: LIST });
  await controller.sync();
  controller.suspend(true);
  assert.equal(shortcuts.registered.size, 0);
  controller.suspend(false);
  assert.equal(shortcuts.registered.size, 2);
  controller.dispose();
});

test("a workflow with no inputs runs at once, and notices report it and open the run", async () => {
  const { controller, shortcuts, backend, timers, notices, opened } = setup({
    list: LIST,
    targets: { [ID_A]: { mode: "run", workflowName: "Tidy" } },
  });
  await controller.sync();
  shortcuts.press("Control+Alt+T");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const start = backend.calls.find(([method, where]) => method === "POST" && where === `/api/workflow-shortcuts/${ID_A}/run`);
  assert.deepEqual(start[2], {});
  assert.equal(notices[0].title, "Running Tidy");
  notices[0].onClick();
  assert.deepEqual(opened[0], { workflowId: "wf", runId: "run-1", status: "running", projectRoot: "/p", workflowName: "Tidy" });

  // Still running: look again later. Then it finishes.
  await timers.runAll((timer) => timer.delay === 2000);
  assert.equal(notices.length, 1);
  backend.runs["run-1"] = { id: "run-1", status: "error", error: "Step build failed.", projectRoot: "/p" };
  await timers.runAll((timer) => timer.delay === 2000);
  assert.equal(notices[0].closed, true);
  assert.equal(notices[1].title, "Tidy failed");
  assert.match(notices[1].body, /Step build failed\. Click to open the run\./);
  notices[1].onClick();
  assert.equal(opened[1].status, "error");
  controller.dispose();
});

test("a workflow with a text input opens the Run sheet from the clipboard; Enter runs it", async () => {
  const long = "x".repeat(MAX_TEXT_CHARS + 5);
  const { controller, backend, sheet, notices } = setup({
    list: LIST,
    clipboard: "Summarize this article",
    targets: { [ID_B]: { mode: "text", workflowName: "Summarize", input: "article", inputLabel: "Article" } },
  });
  await controller.sync();
  assert.equal(await controller.press(ID_B), true);
  assert.equal(sheet.state.text, "Summarize this article");
  assert.equal(sheet.state.inputLabel, "Article");
  assert.equal(sheet.state.caution, CAUTION);
  assert.match(CAUTION, /instructions you didn't write/);
  // Pressing again while it is open brings the sheet forward instead of a second one.
  await controller.press(ID_B);
  assert.equal(sheet.focused, 1);

  // A refused start keeps the sheet open with the reason.
  backend.startError = "Article must be at most 10 characters.";
  await sheet.handlers.onRun("edited");
  assert.equal(sheet.state.error, "Article must be at most 10 characters.");
  assert.equal(sheet.state.busy, false);
  backend.startError = "";
  await sheet.handlers.onRun("edited text");
  const starts = backend.calls.filter(([method, where]) => method === "POST" && where.endsWith("/run"));
  assert.deepEqual(starts.at(-1)[2], { text: "edited text" });
  assert.equal(sheet.state, null);
  assert.equal(notices.at(-1).title, "Running Summarize");
  controller.dispose();

  // A clipboard longer than a run takes is cut, and the sheet says so.
  const second = setup({ list: LIST, clipboard: long, targets: { [ID_B]: { mode: "text", workflowName: "Summarize", inputLabel: "Article" } } });
  await second.controller.press(ID_B);
  assert.equal(second.sheet.state.text.length, MAX_TEXT_CHARS);
  assert.equal(second.sheet.state.cut, true);
  second.controller.dispose();

  // An empty clipboard at the press is read again once the sheet has the focus.
  let clipboard = "";
  const third = setup({ list: LIST, targets: { [ID_B]: { mode: "text", workflowName: "Summarize", inputLabel: "Article" } } });
  const reread = createWorkflowShortcuts({
    globalShortcut: third.shortcuts, api: third.backend.api, sheet: third.sheet, readClipboard: () => clipboard,
    setTimer: third.timers.setTimer, clearTimer: third.timers.clearTimer,
  });
  await reread.press(ID_B);
  assert.equal(third.sheet.state.text, "");
  clipboard = "Copied in another app";
  third.sheet.handlers.onFocus();
  assert.equal(third.sheet.state.text, "Copied in another app");
  assert.equal(third.sheet.state.refill, true);
  reread.dispose();
  third.controller.dispose();
});

test("a press that can't run says why, and a removed shortcut does nothing", async () => {
  const { controller, backend, notices, sheet } = setup({
    list: LIST,
    targets: { [ID_A]: { mode: "unavailable", workflowName: "Tidy", problem: "This workflow needs count, which a shortcut can't fill." } },
  });
  await controller.sync();
  assert.equal(await controller.press(ID_A), false);
  assert.equal(notices[0].title, "Can't run Tidy");
  assert.match(notices[0].body, /needs count/);
  assert.equal(await controller.press(ID_B), false);
  assert.equal(notices.length, 1);
  assert.equal(sheet.state, null);
  assert.ok(backend.calls.filter(([method, where]) => method === "GET" && where === "/api/workflow-shortcuts").length >= 2);
  assert.equal(await controller.press("../../etc"), false);
  controller.dispose();
});

test("a changed workflow doesn't run, and its notice opens Review changes", async () => {
  const { controller, backend, notices, reviews, sheet } = setup({
    list: LIST,
    clipboard: "Some text",
    targets: {
      [ID_A]: { mode: "run", workflowName: "Tidy" },
      [ID_B]: { mode: "text", workflowName: "Summarize", inputLabel: "Article" },
    },
  });
  await controller.sync();
  backend.approvals[ID_A] = "changed";
  assert.equal(await controller.press(ID_A), false);
  assert.equal(backend.calls.some(([method, where]) => method === "POST" && where.endsWith("/run")), false);
  assert.equal(notices[0].title, "Tidy changed since you added its shortcut");
  assert.match(notices[0].body, /didn't run/);
  notices[0].onClick();
  assert.deepEqual(reviews, [{ shortcutId: ID_A, workflowName: "Tidy" }]);

  // A shortcut saved before approvals asks for its first one.
  backend.approvals[ID_A] = "unapproved";
  await controller.press(ID_A);
  assert.equal(notices[1].title, "Tidy needs your approval before its shortcut runs it");

  // The workflow changes while the Run sheet is open: the backend refuses the start, the
  // sheet closes and the notice opens Review changes.
  assert.equal(await controller.press(ID_B), true);
  backend.startCode = "shortcut_changed";
  backend.startError = "Summarize changed since you added its shortcut";
  assert.equal(await sheet.handlers.onRun("Some text"), false);
  assert.equal(sheet.state, null);
  assert.equal(notices.at(-1).title, "Summarize changed since you added its shortcut");
  notices.at(-1).onClick();
  assert.deepEqual(reviews.at(-1), { shortcutId: ID_B, workflowName: "Summarize" });
  controller.dispose();
});

test("main.js passes the backend's code on and opens Review changes in the window", () => {
  const main = read("main.js");
  assert.match(main, /code: payload\.code/);
  assert.match(main, /openReview: openWorkflowShortcutReview/);
  assert.match(main, /action: "review", shortcutId, workflowName/);
});

test("on a Wayland desktop the keys go to the desktop with the dictation shortcuts", async () => {
  const updates = [];
  const desktopShortcuts = {
    update(id, entry) { updates.push([id, entry]); return Promise.resolve(entry ? { route: "kde", state: "registered", trigger: "Ctrl+Alt+T" } : { state: "off" }); },
  };
  const { controller, shortcuts } = setup({ list: LIST, desktopShortcuts });
  const status = await controller.sync();
  assert.equal(shortcuts.registered.size, 0);
  assert.deepEqual(updates.map(([id, entry]) => [id, entry?.description]), [[`workflow-${ID_A}`, "Run Tidy"], [`workflow-${ID_B}`, "Run Summarize"]]);
  assert.ok(status.shortcuts.every((item) => item.registered && item.route === "kde"));
  // A desktop that only takes a command shows it.
  const manual = controller.desktopChanged({ route: "manual", shortcuts: { [`workflow-${ID_A}`]: { state: "manual" } } });
  const tidy = manual.shortcuts.find((item) => item.id === ID_A);
  assert.equal(tidy.registered, false);
  assert.equal(tidy.command, `Raticode --run-workflow-shortcut=${ID_A}`);
  const before = updates.length;
  await controller.reserve({ "Control+Alt+T": "Dictate to Rem from any app" });
  assert.deepEqual(updates.slice(before).find(([id]) => id === `workflow-${ID_A}`), [`workflow-${ID_A}`, null]);
  controller.dispose();
});

test("KDE presses reach the workflow shortcut through linux-shortcuts.cjs", async (t) => {
  if (!dbusDaemonInstalled()) {
    t.skip("dbus-daemon is not installed");
    return;
  }
  const bus = await startBus();
  const kde = await startFakeKde(bus.socketPath, {});
  let controller = null;
  const linux = createLinuxShortcuts({
    connectBus: () => connect({ socketPath: bus.socketPath }),
    env: { XDG_CURRENT_DESKTOP: "KDE" },
    component: { unique: "raticode-test", friendly: "Raticode" },
    onPress: (id) => { if (id.startsWith("workflow-")) void controller.press(id.slice("workflow-".length)); },
  });
  t.after(async () => {
    controller?.dispose();
    await linux.dispose();
    kde.close();
    bus.stop();
  });
  const context = setup({ list: [LIST[0]], desktopShortcuts: linux, targets: { [ID_A]: { mode: "run", workflowName: "Tidy" } } });
  controller = context.controller;
  const status = await controller.sync();
  assert.equal(status.shortcuts[0].registered, true);
  assert.equal(status.shortcuts[0].route, "kde");
  kde.press("raticode-test", `workflow-${ID_A}`);
  const end = Date.now() + 3000;
  while (!context.backend.calls.some(([method, where]) => method === "POST" && where.endsWith("/run"))) {
    if (Date.now() > end) throw new Error("the press didn't start a run");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
});

test("the Run sheet page shows text safely and Enter runs it", () => {
  const html = read("workflow-run-sheet.html");
  assert.match(html, /Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'self'"/);
  const script = read("workflow-run-sheet.js");
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|outerHTML/);
  assert.match(script, /event\.key === "Enter" && !event\.shiftKey/);
  const preload = read("workflow-run-sheet-preload.cjs");
  assert.deepEqual([...preload.matchAll(/action: "(\w+)"/g)].map((match) => match[1]), ["run", "cancel"]);
  const sheet = read("workflow-run-sheet.cjs");
  assert.match(sheet, /event\.sender !== window\.webContents/);
  assert.match(sheet, /sandbox: true/);
});
