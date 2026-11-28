/* global __dirname, console, process, setTimeout, clearTimeout */
// An opened Rem task in a real Chromium layout, at the narrowest sidebar, the width
// in the user's report and wider. Every line of text wraps inside the sheet, long
// unbroken paths break, the run's buttons wrap instead of being cut off, and nothing
// scrolls sideways. A path in the run summary used to widen the whole status card,
// so the summary, the failed checks and their reasons ran past the sheet's edge.
// Isolated: fake tasks served to the real store, no backend, no visible window.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-task-sheet-layout-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Task sheet layout test timed out"); app.exit(1); }, 180000);
const shots = process.env.TASK_SHEET_LAYOUT_SCREENSHOTS || "";

const longPath = "/home/someone/Projects/Raticode/Raticode/.worktrees/reader-versions-forget-older/frontend/src/components/ReaderVersions.jsx";
const otherPath = "/home/someone/Projects/second-brain/reports/rem/Reports/Raticode/a-very-long-report-name-without-any-spaces-2026-11-14.html";
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const failedCriteria = [
  { id: "c-1", description: `Implement the requested behavior in ${longPath} and demonstrate it works`, result: "fail",
    evidence: `Nothing was built. The worker stopped at once because the run was rooted in ${otherPath} and could not write to ${longPath}. ` + "It listed the folder and stopped. ".repeat(8) },
  { id: "c-2", description: "Pass relevant regression tests and required project checks", result: "fail",
    evidence: `The worker said it ran no checks and made no commit. Checked ${longPath}.` },
  { id: "c-3", description: `Unresolved ${longPath}`, result: "unknown", evidence: `No receipts for ${otherPath}` },
  { id: "c-4", description: `Passed ${longPath}`, result: "pass", evidence: `Fine: ${longPath}` },
];
const tasks = [
  { id: "task-review", revision: 2, state: "enabled", type: "develop", title: `Forget older versions in ${longPath}`,
    prompt: `Run this again with project_dir set to ${longPath} so the worker can write there.`, project_dir: longPath, schedule: { kind: "once" },
    acceptance_criteria: failedCriteria.map(item => ({ id: item.id, description: item.description, authored_by: "rem_recommended" })),
    last_run: { id: "run-review-1", state: "blocked", finished_at: ago(30), thread_id: "thread-review", verification_thread_id: "thread-review-check",
      summary_short: `The review still has unresolved checks. Run this task again with project_dir set to ${longPath} so the worker can write there. Follow worktree-dev-flow: make a worktree off dev and build these four pieces.`,
      criteria: failedCriteria, tickets: [{ key: "REM-123", ticket_id: "t-123", title: `A ticket about ${longPath}`, role: "worked" }] } },
  { id: "task-repair", revision: 1, state: "enabled", type: "develop", title: "Fixing what the review found",
    prompt: "Fix it.", project_dir: longPath, schedule: { kind: "once" }, acceptance_criteria: [],
    active_run: { id: "run-repair-1", state: "repairing", started_at: ago(5), thread_id: "thread-repair", verification_thread_id: "thread-repair-check", criteria: failedCriteria } },
  { id: "task-waiting", revision: 1, state: "enabled", type: "develop", title: "Waits for the others", prompt: "Then this.", project_dir: longPath,
    schedule: { kind: "once" }, after: ["task-review"], acceptance_criteria: [], active_run: null, last_run: null,
    dependency: { state: "held", reason: `On hold: ${longPath} was blocked.`, prerequisites: [
      { task_id: "task-review", title: `Forget older versions in ${longPath}`, state: "held", reason: `${longPath} was blocked`, run_id: "run-review-1", run_state: "blocked", retryable: true },
    ] } },
];
const messages = [{ id: "m-1", role: "rem", kind: "task_update", created_at: ago(29), body: `Finished with notes about ${otherPath}`,
  work: { task_id: "task-review", run_id: "run-review-1", thread_id: "thread-review", title: tasks[0].title, type: "develop", state: "blocked" } }];
let vite;

// Anything in the opened task that sits outside the sheet, scrolls sideways, or
// whose own text runs past its box.
const MEASURE = `(() => {
  const panel = document.querySelector('#sidebar-panel-rem');
  const sheet = panel.getBoundingClientRect();
  const name = e => (e.tagName.toLowerCase() + '.' + String(e.className?.baseVal ?? e.className).trim().split(/\\s+/).slice(0, 2).join('.')).slice(0, 60);
  const all = [...panel.querySelectorAll('*')].filter(e => e.getClientRects().length);
  const outside = all.filter(e => { const box = e.getBoundingClientRect(); return box.width > 0 && (box.right > sheet.right + 0.5 || box.left < sheet.left - 0.5); })
    .filter((e, _, list) => !list.includes(e.parentElement)).map(name);
  // A ticket pill keeps its title on one line and ends it with an ellipsis, inside the sheet.
  const scrollers = all.filter(e => e.scrollWidth > e.clientWidth + 1 && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) && !e.matches('.ticket-pill-title')).map(e => name(e) + ' +' + (e.scrollWidth - e.clientWidth));
  const card = document.querySelector('.rem-status')?.getBoundingClientRect();
  const scroll = document.querySelector('.rem-sheet-scroll');
  return { outside: outside.slice(0, 8), scrollers: scrollers.slice(0, 8), page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    sheet: { left: sheet.left, right: sheet.right }, card: card && { left: card.left, right: card.right, width: card.width },
    scroll: { scrollWidth: scroll.scrollWidth, clientWidth: scroll.clientWidth } };
})()`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ configLoader: "runner", root: path.join(__dirname, ".."), logLevel: "error", cacheDir: testViteCacheDir("task-sheet-layout"),
    server: { host: "127.0.0.1", port: 0 }, plugins: [{
      name: "task-sheet-layout-test",
      resolveId(id) { if (id === "/task-sheet-entry.jsx") return id; },
      load(id) {
        if (id !== "/task-sheet-entry.jsx") return;
        return `import React from "react"; import { createRoot } from "react-dom/client";
          import RemHomeSidebar from "/src/components/RemHomeSidebar.jsx"; import * as store from "/src/lib/remHomeStore.js";
          import "/src/styles/index.css";
          const params = new URLSearchParams(location.search);
          document.documentElement.classList.toggle("dark", params.get("theme") === "dark");
          const data = ${JSON.stringify({ tasks, messages })};
          window.fetch = async input => {
            const url = String(input);
            const payload = url.endsWith("/conversation") ? { conversation: { id: "rem", turn_state: "idle" }, messages: data.messages } : url.endsWith("/tasks") ? { tasks: data.tasks } : {};
            return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
          };
          window.__rem = store;
          const h = React.createElement;
          const side = Number(params.get("side"));
          createRoot(document.getElementById("root")).render(h("main", { style: { height: "100vh", display: "flex" } },
            h("aside", { className: "studio-sidebar bg-white", style: { width: side, flexShrink: 0, display: "flex", borderRight: "1px solid rgb(var(--color-line))" } },
              h("div", { style: { width: 40, flexShrink: 0 } }),
              h(RemHomeSidebar, { active: true, open: false, onOpen() {}, projectOptions: [] })),
            h("section", { style: { flex: 1, minWidth: 0 } })));`;
      },
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url.split("?")[0] !== "/task-sheet-test") return next();
          response.setHeader("Content-Type", "text/html");
          response.end(await server.transformIndexHtml("/task-sheet-test", `<html><body class="bg-canvas" style="margin:0"><div id="root"></div><script type="module" src="/task-sheet-entry.jsx"></script></body></html>`));
        });
      },
    }] });
  await vite.listen();
  const win = new BrowserWindow({ width: 1200, height: 900, show: true, webPreferences: { partition: "task-sheet-layout", contextIsolation: true } });
  const evaluate = source => win.webContents.executeJavaScript(source, true);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const wait = async (source, ms = 20000) => {
    const deadline = Date.now() + ms;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await sleep(50); }
  };
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}/task-sheet-test`;
  // The sidebar's narrowest width (rail included), the user's report, its widest, and a wide pane.
  for (const theme of ["dark", "light"]) {
    for (const side of [240, 360, 420, 720]) {
      await win.loadURL(`${origin}?side=${side}&theme=${theme}`);
      await wait(`document.querySelectorAll('.rem-task-row').length > 0`);
      for (const task of tasks) {
        const where = `${task.id} in a ${side} px sidebar (${theme})`;
        await evaluate(`window.__rem.openTaskSheet(${JSON.stringify(task.id)})`);
        await wait(`Boolean(document.querySelector('#sidebar-panel-rem .rem-status'))`);
        // As it opens, with long reasons folded, then with every reason, passed check and update unfolded.
        for (const [state, top] of [["folded", 0], ["open", 0], ["open", 1e9]]) {
          if (state === "open") await evaluate(`document.querySelectorAll('.rem-check-more[aria-expanded=false]').forEach(b => b.click()); document.querySelectorAll('#sidebar-panel-rem details').forEach(d => { d.open = true; })`);
          await evaluate(`document.querySelector('.rem-sheet-scroll').scrollTop = ${top}`);
          await sleep(120);
          const layout = await evaluate(MEASURE);
          if (shots && theme === "dark") fs.writeFileSync(path.join(shots, `${task.id}-${side}-${state}-${top ? "end" : "top"}.png`), (await win.webContents.capturePage()).toPNG());
          assert.deepEqual(layout.outside, [], `nothing pokes out of the sheet: ${where}`);
          assert.deepEqual(layout.scrollers, [], `no text runs past its box and nothing scrolls sideways: ${where}`);
          assert.equal(layout.scroll.scrollWidth, layout.scroll.clientWidth, `the sheet doesn't scroll sideways: ${where}`);
          assert.equal(layout.page, 0, `the page doesn't scroll sideways: ${where}`);
          assert.ok(layout.card.right <= layout.sheet.right - 8, `the status card keeps its margin inside the sheet: ${where}`);
        }
        // Every run button is fully visible once scrolled to: hit-testing both of its ends lands on the button.
        const clipped = await evaluate(`[...document.querySelectorAll('.rem-status-actions button')].filter(b => { b.scrollIntoView({ block: 'center' }); const box = b.getBoundingClientRect();
          return [box.left + 3, box.right - 3].some(x => document.elementFromPoint(x, box.top + box.height / 2)?.closest('button') !== b); }).map(b => b.textContent)`);
        assert.ok((await evaluate(`document.querySelectorAll('.rem-status-actions button').length`)) >= 2, `the run has its buttons: ${where}`);
        assert.deepEqual(clipped, [], `the run buttons aren't cut off: ${where}`);
        await evaluate(`window.__rem.closeTaskSheet()`);
        await wait(`document.querySelectorAll('.rem-task-row').length > 0`);
      }
      // The summary of the blocked run wraps over many lines instead of one long line.
      await evaluate(`window.__rem.openTaskSheet("task-review")`);
      await wait(`Boolean(document.querySelector('#sidebar-panel-rem .rem-status'))`);
      const summary = await evaluate(`(() => { const s = document.querySelector('.rem-status-line strong').getBoundingClientRect(); return { width: s.width, height: s.height }; })()`);
      assert.ok(summary.height > 60, `the run summary wraps in a ${side} px sidebar (${theme}): ${JSON.stringify(summary)}`);
      await evaluate(`window.__rem.closeTaskSheet()`);
    }
  }
  console.log("Task sheet layout: text, paths and buttons stay inside the sheet at 240, 360, 420 and 720 px, light and dark.");
}

main().then(() => { clearTimeout(timer); return vite?.close(); }).then(() => { fs.rmSync(profile, { recursive: true, force: true }); app.exit(0); }).catch(async (error) => {
  console.error(error);
  await vite?.close().catch(() => {});
  fs.rmSync(profile, { recursive: true, force: true });
  app.exit(1);
});
