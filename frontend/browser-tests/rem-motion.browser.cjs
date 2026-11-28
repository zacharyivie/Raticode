/* global __dirname, console, process, setTimeout, clearTimeout */
// Rem's spinners and progress bars move smoothly while Rem works. In a real Chromium, with
// the task list polled and changed every 2.5 s as when workers run:
// - Chromium runs every animation in the side pane and the chat's task card on the
//   compositor (its trace names none that failed to composite), so a busy window can't make
//   them stutter. The progress bars animated background-position, which repaints every frame
//   on the window's main thread.
// - A spinner keeps one animation across polls instead of starting over.
// Isolated: fake tasks served to the real store, no backend, no visible window.
// MOTION_SCREENSHOTS=<folder> saves what it measured.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-rem-motion-"));
app.setPath("userData", profile);
// As the app runs (main.js): no GPU, so compositing is in software.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Rem motion test timed out"); app.exit(1); }, 120000);
const shots = process.env.MOTION_SCREENSHOTS || "";
let vite;

const entry = `import React from "react"; import { createRoot } from "react-dom/client";
  import RemHomeSidebar from "/src/components/RemHomeSidebar.jsx"; import { ProgressSteps } from "/src/components/RemRichParts.jsx";
  import { ThreadList } from "/src/components/ThreadSections.jsx";
  import * as store from "/src/lib/remHomeStore.js"; import "/src/styles/index.css"; import "/src/components/RemHome.css";
  const started = new Date(Date.now() - 6 * 60000).toISOString();
  let polls = 0;
  // Every poll sends a changed list, as while a worker reports its progress.
  const tasks = () => [{ id: "task-working", revision: 1, state: "enabled", type: "develop", title: "Make the spinners smooth", schedule: { kind: "once" },
    acceptance_criteria: [], active_run: { id: "run-working", state: "executing", step: "work", started_at: started, created_at: started, heartbeat_at: new Date().toISOString(), turns: polls } }];
  window.fetch = async input => {
    const url = String(input);
    if (url.endsWith("/tasks")) polls += 1;
    window.__polls = polls;
    const payload = url.endsWith("/conversation") ? { conversation: { id: "rem", turn_state: "idle" }, messages: [] }
      : url.endsWith("/tasks") ? { tasks: tasks(), version: "v" + polls } : url.includes("/tasks/task-working") ? { task: { ...tasks()[0], prompt: "Smooth." } } : {};
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  };
  window.__rem = store;
  const h = React.createElement;
  const card = { state: "executing", step: "work", run: { created_at: started } };
  // Two threads streaming replies: each new message moves its thread to the top.
  function Streaming() {
    const [tick, setTick] = React.useState(0);
    React.useEffect(() => { const timer = setInterval(() => setTick(value => value + 1), 400); return () => clearInterval(timer); }, []);
    window.__reorders = tick;
    const at = offset => new Date(Date.UTC(2026, 10, 26, 12, 0, offset)).toISOString();
    const threads = [{ id: "thread-a", title: "Streaming A", updatedAt: at(tick % 2 ? 2 : 1) }, { id: "thread-b", title: "Streaming B", updatedAt: at(tick % 2 ? 1 : 2) }];
    return h("div", { className: "rem-threads", style: { width: 320 } }, h(ThreadList, { threads, activityByThread: { "thread-a": { sending: true }, "thread-b": { sending: true } } }));
  }
  createRoot(document.getElementById("root")).render(h("main", { style: { height: "100vh", display: "flex" } },
    h("aside", { className: "studio-sidebar bg-white", style: { width: 360, flexShrink: 0, display: "flex" } },
      h(RemHomeSidebar, { active: true, open: false, onOpen() {}, projectOptions: [] })),
    h("section", { className: "rem-work", style: { flex: 1, padding: 24 } }, h(ProgressSteps, { status: card, now: new Date() }), h(Streaming))));`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ configLoader: "runner", root: path.join(__dirname, ".."), logLevel: "error", cacheDir: testViteCacheDir("rem-motion"),
    server: { host: "127.0.0.1", port: 0 }, plugins: [{
      name: "rem-motion-test",
      resolveId(id) { if (id === "/rem-motion-entry.jsx") return id; },
      load(id) { if (id === "/rem-motion-entry.jsx") return entry; },
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url.split("?")[0] !== "/rem-motion-test") return next();
          response.setHeader("Content-Type", "text/html");
          response.end(await server.transformIndexHtml("/rem-motion-test", `<html><body class="bg-canvas" style="margin:0"><div id="root"></div><script type="module" src="/rem-motion-entry.jsx"></script></body></html>`));
        });
      },
    }] });
  await vite.listen();
  const win = new BrowserWindow({ width: 1000, height: 700, show: true, webPreferences: { partition: "rem-motion", contextIsolation: true } });
  const evaluate = source => win.webContents.executeJavaScript(source, true);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const wait = async (source, ms = 20000) => {
    const deadline = Date.now() + ms;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await sleep(50); }
  };
  const shot = async name => { if (shots) { await sleep(300); fs.writeFileSync(path.join(shots, name), (await win.webContents.capturePage()).toPNG()); } };
  // Chromium says why an animation can't run on the compositor when it starts, so the
  // trace covers the whole visit.
  const debug = win.webContents.debugger;
  debug.attach("1.3");
  const events = [];
  let complete;
  const completed = new Promise(resolve => { complete = resolve; });
  debug.on("message", (_event, method, params) => {
    if (method === "Tracing.dataCollected") events.push(...params.value);
    if (method === "Tracing.tracingComplete") complete();
  });
  await debug.sendCommand("Tracing.start", { transferMode: "ReportEvents", traceConfig: { includedCategories: ["blink.animations", "devtools.timeline"] } });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-motion-test`);

  // The spinner in the task list keeps the same animation through several changed polls.
  await wait(`Boolean(document.querySelector('.rem-task-row .rem-spin'))`);
  await evaluate(`(() => { const spinner = document.querySelector('.rem-task-row .rem-spin'); window.__spinner = { node: spinner, animation: spinner.getAnimations()[0], at: spinner.getAnimations()[0].currentTime, polls: window.__polls }; return true; })()`);
  await wait(`window.__polls >= window.__spinner.polls + 3`, 15000);
  const list = await evaluate(`(() => { const s = window.__spinner; const now = s.node.getAnimations()[0];
    return { connected: s.node.isConnected, same: now === s.animation, advanced: now ? now.currentTime - s.at : null, polls: window.__polls - s.polls }; })()`);
  assert.equal(list.connected, true, "the list's spinner isn't replaced by a poll");
  assert.equal(list.same, true, "the list's spinner keeps its animation through polls");
  assert.ok(list.advanced > 5000, `the list's spinner kept turning without starting over: ${JSON.stringify(list)}`);
  await shot("rem-motion-list.png");

  // Threads that keep trading places as replies stream in keep their spinners turning.
  await wait(`document.querySelectorAll('[data-thread-id] .animate-spin').length === 2`);
  // A spinner whose animation starts over when its row moves gets a new start time.
  const starts = `[...document.querySelectorAll('[data-thread-id] .animate-spin')].map(node => node.getAnimations()[0]?.startTime ?? null)`;
  const samples = [];
  const reordersBefore = await evaluate(`window.__reorders`);
  for (let sample = 0; sample < 8; sample += 1) { samples.push(await evaluate(starts)); await sleep(230); }
  const reorders = (await evaluate(`window.__reorders`)) - reordersBefore;
  const restarts = samples.slice(1).reduce((count, sample, index) => count + sample.filter((start, spinner) => start !== samples[index][spinner]).length, 0);
  assert.ok(reorders >= 4, `the threads traded places while measured: ${reorders}`);
  assert.equal(restarts, 0, `thread spinners keep turning when their rows move (${restarts} restarts over ${reorders} moves): ${JSON.stringify(samples)}`);

  // The opened task: its spinner and progress bar, beside the chat's task card.
  await evaluate(`window.__rem.openTaskSheet("task-working")`);
  await wait(`Boolean(document.querySelector('.rem-steps li[data-state=current]')) && Boolean(document.querySelector('.rem-progress-steps li[data-state=current]'))`);
  const before = await evaluate(`window.__polls`);
  // Long enough for two polls, so animations that restart show up as well.
  await sleep(5500);
  await debug.sendCommand("Tracing.end");
  await completed;
  debug.detach();
  const running = await evaluate(`document.getAnimations().filter(a => a.playState === 'running').map(a => ({ name: a.animationName, target: a.effect.target.className?.baseVal ?? a.effect.target.className, pseudo: a.effect.pseudoElement || '' }))`);
  const started = events.filter(event => event.name === "Animation" && event.ph === "b").map(event => event.args?.data?.displayName);
  assert.ok(started.includes("rem-spin") && started.includes("rem-progress"), `the trace saw the spinner and progress bars start: ${started}`);
  const failed = events.filter(event => event.name === "Animation" && event.args?.data?.compositeFailed)
    .map(event => ({ id: event.id2?.local || event.id, ...event.args.data }));
  const names = Object.fromEntries(events.filter(event => event.name === "Animation" && event.ph === "b").map(event => [event.id2?.local || event.id, event.args.data.displayName]));
  const report = { polls: (await evaluate(`window.__polls`)) - before, running, started, notComposited: failed.map(item => ({ animation: names[item.id] || "?", reason: item.compositeFailed, properties: item.unsupportedProperties || [] })) };
  console.log(`Rem motion: ${JSON.stringify(report)}`);
  if (shots) fs.writeFileSync(path.join(shots, "rem-motion-trace.json"), JSON.stringify(report, null, 2));
  await shot("rem-motion-task.png");
  assert.ok(running.some(item => item.name === "rem-progress" && item.pseudo), "the task sheet's progress bar is animating");
  assert.ok(running.some(item => item.name === "rem-spin"), "the task sheet's spinner is animating");
  assert.ok(report.polls >= 2, `polls kept changing the list while measured: ${report.polls}`);
  assert.deepEqual(report.notComposited, [], "every Rem animation runs on the compositor");
  console.log("Rem motion: spinners keep turning through polls and every animation runs on the compositor.");
}

main().then(() => { clearTimeout(timer); return vite?.close(); }).then(() => { fs.rmSync(profile, { recursive: true, force: true }); app.exit(0); }).catch(async (error) => {
  console.error(error);
  await vite?.close().catch(() => {});
  fs.rmSync(profile, { recursive: true, force: true });
  app.exit(1);
});
