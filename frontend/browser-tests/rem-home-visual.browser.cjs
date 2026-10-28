/* global __dirname, console, setTimeout, clearTimeout, process */
// Visual and interaction audit of the Rem home (task sidebar + conversation).
// Run through run-rem-home-visual.cjs. Scenes use explicit fixtures; no backend or providers.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const out = process.env.CAPTURE_DIR || path.join(os.tmpdir(), "raticode-rem-home-visual");
fs.mkdirSync(out, { recursive: true });
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "rem-home-capture-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let vite, win;
const timeout = setTimeout(() => app.exit(1), 300000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const entry = `import React from "react"; import { createRoot } from "react-dom/client";
import RemHomeSidebar from "/src/components/RemHomeSidebar.jsx";
import RemHomeWorkspace from "/src/components/RemHomeWorkspace.jsx";
import * as store from "/src/lib/remHomeStore.js";
import { SAMPLE_PROJECTS, remHomeScene } from "/src/lib/remHomeSample.js";
import "/src/styles/index.css";
const scene = new URLSearchParams(location.search).get("scene");
window.__rem = store;
window.__opened = [];
window.addEventListener("gofer:rem-open-thread", event => window.__opened.push(event.detail.threadId));
// The offline scene exercises the real request path against a backend without the Rem service.
const nativeFetch = window.fetch.bind(window);
if (scene === "history") {
  window.__history = remHomeScene("busy");
  const work = { task_id: "task-vector-db", run_id: "run-vector-1", thread_id: "thread-vector-worker" };
  window.__history.messages = [
    { id: "audit-request", role: "user", body: "Compare vector search options.", created_at: new Date().toISOString() },
    { id: "audit-ack", role: "rem", body: "On it. I’ll save the findings.", work, created_at: new Date().toISOString() },
    { id: "other-request", role: "user", body: "What’s Theo’s newest video about?", created_at: new Date().toISOString() },
  ];
  window.__appendTaskUpdate = () => window.__history.messages.push({ id: "audit-result", role: "rem", body: "The comparison is ready. " + "These are the checked findings. ".repeat(14) + "Next, use SQLite.", work: { ...work, state: "succeeded" }, created_at: new Date().toISOString() });
  window.fetch = async input => {
    const url = String(input);
    const payload = url.endsWith("/conversation") ? { conversation: window.__history.conversation, messages: window.__history.messages }
      : url.endsWith("/tasks") ? { tasks: window.__history.tasks }
      : url.includes("/threads/") ? { metadata: { id: url.split("/").at(-1), updatedAt: new Date().toISOString() }, messages: [] } : {};
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  };
} else {
  window.fetch = async (input, options) => String(input).includes("/api/rem/") ? new Response(JSON.stringify({ detail: "Not Found" }), { status: 404, headers: { "Content-Type": "application/json" } }) : nativeFetch(input, options);
  if (scene !== "offline") await store.loadRemHomeFixture(scene === "empty" ? "empty" : scene === "replying" ? "replying" : "busy");
}

function App() {
  return <main style={{ height: "100vh", display: "flex" }}>
    <aside className="studio-sidebar bg-white" style={{ width: 272, flexShrink: 0, display: "flex", borderRight: "1px solid rgb(var(--color-line))" }}>
      <div style={{ width: 40, borderRight: "1px solid rgb(var(--color-line))" }} />
      <RemHomeSidebar active open onOpen={() => {}} />
    </aside>
    <section style={{ flex: 1, minWidth: 0, display: "flex" }}><RemHomeWorkspace projectOptions={SAMPLE_PROJECTS} /></section>
  </main>;
}
createRoot(document.getElementById("root")).render(<App />);`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "rem-home-capture",
    resolveId(id) { if (id === "/rem-home-entry.jsx") return id; },
    load(id) { if (id === "/rem-home-entry.jsx") return entry; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (!request.url.startsWith("/rem-home-test")) return next();
      response.setHeader("Content-Type", "text/html");
      response.end(await server.transformIndexHtml("/rem-home-test", '<html><body><div id="root"></div><script type="module" src="/rem-home-entry.jsx"></script></body></html>'));
    }); },
  }] });
  await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}/rem-home-test`;
  win = new BrowserWindow({ width: 1440, height: 900, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", details => { if (details.level === "error") { errors.push(details.message); console.error(`[page] ${details.message}`); } });
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function click(text, selector = "button") {
    const ok = await evaluate(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(b => b.textContent.trim() === ${JSON.stringify(text)} || b.getAttribute('aria-label') === ${JSON.stringify(text)}); if (!el) return false; el.click(); return true; })()`);
    assert.ok(ok, `Missing control: ${text}`); await sleep(250);
  }
  async function openTask(title) {
    const ok = await evaluate(`(() => { const el = [...document.querySelectorAll('.rem-task-open')].find(b => b.querySelector('strong').textContent === ${JSON.stringify(title)}); el?.click(); return Boolean(el); })()`);
    assert.ok(ok, `Missing task: ${title}`); await sleep(250);
  }
  async function shot(name) {
    await sleep(300);
    const overflow = await evaluate("document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.rem-home *, .rem-side *')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible' && e.getBoundingClientRect().right > innerWidth + 1).map(e => e.className).slice(0, 3)");
    assert.ok(overflow === false || (Array.isArray(overflow) && overflow.length === 0), `Overflow in ${name}: ${JSON.stringify(overflow)}`);
    fs.writeFileSync(path.join(out, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  }
  const scenes = (process.env.CAPTURE_SCENES || "busy,running,blocked,new,menu,empty,offline,replying,history").split(",");
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_WIDTHS || "1440,980").split(",").map(Number)) {
      for (const scene of scenes) {
        win.setSize(width, 900);
        await win.loadURL(`${base}?scene=${scene}`); await sleep(900);
        await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"})`); await sleep(200);
        const tag = `${theme}-${width}-${scene}`;
        if (scene === "history") {
          const readMessages = "[...document.querySelectorAll('[data-message-id]')].map(el => [el.dataset.messageId, el.querySelector('.rem-markdown, .rem-bubble').textContent])";
          const original = await evaluate(readMessages);
          await evaluate("window.__appendTaskUpdate(); __rem.refreshRemHome()");
          const updated = await evaluate(readMessages);
          assert.deepEqual(updated.slice(0, -1), original, "A task update replaced or moved an earlier message");
          assert.match(updated.at(-1)[1], /Next, use SQLite\./, "A saved reply was truncated");
          await evaluate("document.querySelector('.rem-work-main').click()");
          await sleep(250);
          await click("Conversation · 2 updates", ".rem-task-updates summary");
          assert.equal(await evaluate("document.querySelectorAll('.rem-update-message').length"), 2);
          assert.equal(await evaluate("document.querySelector('.rem-update-message').textContent"), original[1][1]);
          await click("Open thread", ".rem-task-updates button");
          assert.deepEqual(await evaluate("window.__opened"), ["thread-vector-worker"]);
          await shot(tag);
          await evaluate("document.querySelector('.rem-update-message').click()");
          await sleep(250);
          assert.equal(await evaluate("document.querySelector('.rem-sheet')"), null);
          assert.equal(await evaluate("document.activeElement.dataset.messageId"), "audit-ack");
          const box = await evaluate("(() => { const b = document.activeElement.getBoundingClientRect(); return { top: b.top, bottom: b.bottom }; })()");
          assert.ok(box.top >= 0 && box.bottom <= 900, "Message navigation did not reveal the original reply");
        }
        if (scene === "busy") {
          // Running tasks spin in the sidebar; every work card links to its thread.
          assert.equal(await evaluate("document.querySelectorAll('.rem-side [data-kind=running] .rem-spin').length"), 2, `Running spinners in ${tag}`);
          assert.equal(await evaluate("[...document.querySelectorAll('.rem-work')].every(card => card.querySelector('.rem-work-links button'))"), true, `Thread link missing in ${tag}`);
          await click("Open thread");
          assert.deepEqual(await evaluate("window.__opened"), [], "Fixture threads must not dispatch open requests");
          assert.match(await evaluate("document.querySelector('.rem-home-notice')?.textContent || ''"), /Preview threads/);
          await click("Dismiss");
          await shot(tag);
        }
        if (scene === "running") {
          await openTask("Local-first sync engines");
          assert.equal(await evaluate("document.querySelector('.rem-steps [aria-current=step]')?.textContent"), "Checking");
          // Editing a running task's goal says Rem will steer the thread; Escape keeps the draft.
          await evaluate("(() => { const t = document.querySelector('#rem-task-prompt'); t.focus(); t.setSelectionRange(t.value.length, t.value.length); })()");
          await win.webContents.insertText(" Include CRDT libraries.");
          await sleep(150);
          assert.match(await evaluate("document.querySelector('.rem-sheet-steer')?.textContent || ''"), /update the running thread/);
          assert.equal(await evaluate("document.querySelector('.rem-sheet-actions [type=submit]').textContent"), "Save and update");
          win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" }); win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
          await sleep(200);
          assert.equal(await evaluate("Boolean(document.querySelector('.rem-sheet'))"), true, "Escape discarded a dirty draft");
          await shot(tag);
          await click("Save and update");
          assert.match(await evaluate("__rem.getState().tasks.find(t => t.id === 'task-sync-research').prompt"), /CRDT libraries\.$/);
          assert.equal(await evaluate("document.querySelector('.rem-sheet-steer')"), null);
        }
        if (scene === "blocked") {
          await openTask("CSV export for Runs");
          assert.equal(await evaluate("[...document.querySelectorAll('.rem-status-actions button')].map(e => e.textContent).join('|')"), "Work thread|Run again|Dismiss");
          await shot(tag);
          await click("Dismiss");
          assert.equal(await evaluate("document.querySelectorAll('.rem-side [data-kind=attention]').length"), 0, "Dismissed task still needs attention");
        }
        if (scene === "new") {
          await click("New task");
          await click("Maintain", "[role=radio]");
          await click("Weekly", "[role=radio]");
          await click("We", "[aria-pressed]");
          // Focus, clear, retype: the draft keeps exactly what was typed.
          await evaluate("(() => { const t = document.querySelector('input[type=time]'); t.focus(); })()");
          await click("Custom", "[role=radio]");
          await evaluate("document.querySelector('.rem-inline-field input').focus()");
          await win.webContents.insertText("0 9 * * 1");
          await evaluate("document.querySelector('.rem-inline-field input').select()");
          win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Backspace" }); win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Backspace" });
          await sleep(100);
          assert.equal(await evaluate("document.querySelector('.rem-inline-field input').value"), "");
          await win.webContents.insertText("30 8 * * 1-5");
          await evaluate("document.querySelector('.rem-inline-field input').blur()");
          assert.equal(await evaluate("document.querySelector('.rem-inline-field input').value"), "30 8 * * 1-5");
          await click("Weekly", "[role=radio]");
          assert.equal(await evaluate("document.querySelector('.rem-sheet-actions [type=submit]').disabled"), true, "Saving needs a goal");
          await evaluate("document.querySelector('#rem-task-prompt').focus()");
          await win.webContents.insertText("Keep the docs site building and fix broken links.");
          await click("Save task");
          assert.match(await evaluate("document.querySelector('.rem-field-error')?.textContent || ''"), /need a project/);
          await shot(`${tag}-invalid`);
          await evaluate("(() => { const select = document.querySelector('#rem-task-project'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, select.options[1].value); select.dispatchEvent(new Event('change', { bubbles: true })); })()");
          await sleep(150);
          await shot(tag);
          await click("Save task");
          const created = await evaluate("__rem.getState().tasks[0]");
          assert.deepEqual([created.type, created.schedule.kind, created.schedule.weekdays.join(","), created.schedule.local_time], ["maintain", "weekly", "monday,wednesday", "09:00"]);
          assert.equal(created.title, "Keep the docs site building and fix broken links");
        }
        if (scene === "menu") {
          await evaluate("[...document.querySelectorAll('.rem-task-row')].find(row => row.querySelector('strong')?.textContent === 'Local-first sync engines').querySelector('.rem-menu-trigger').click()"); await sleep(200);
          assert.equal(await evaluate("[...document.querySelectorAll('.rem-menu [role=menuitem]')].map(e => e.textContent).join('|')"), "Edit|Stop current run|Delete…");
          await shot(tag);
          await click("Stop current run", "[role=menuitem]");
          assert.equal(await evaluate("__rem.getState().tasks.find(t => t.id === 'task-sync-research').active_run.state"), "cancelling");
          await evaluate("document.querySelector('.rem-task-row[data-kind=scheduled] .rem-menu-trigger').click()"); await sleep(200);
          await click("Delete…", "[role=menuitem]");
          await shot(`${tag}-confirm`);
          await click("Keep");
          // Clearing asks first, then leaves an empty conversation and task list.
          await evaluate("document.querySelector('.rem-home-options .rem-menu-trigger').click()"); await sleep(200);
          await click("Clear history and tasks…", "[role=menuitem]");
          assert.equal(await evaluate("Boolean(document.querySelector('.rem-clear-confirm'))"), true);
          await shot(`${tag}-clear`);
          await click("Clear history and tasks", ".rem-clear-confirm button");
          await sleep(200);
          assert.equal(await evaluate("document.querySelector('.rem-clear-confirm')"), null);
          assert.equal(await evaluate("__rem.getState().tasks.length + __rem.getState().messages.length"), 0);
          assert.equal(await evaluate("Boolean(document.querySelector('.rem-welcome'))"), true);
        }
        if (scene === "empty") { await shot(tag); assert.equal(await evaluate("Boolean(document.querySelector('.rem-welcome .rem-avatar'))"), true, "Rem's sprite greets on the welcome screen"); await click("What's running right now?"); assert.equal(await evaluate("document.querySelector('.rem-composer textarea').value"), "What's running right now?"); }
        if (scene === "offline") {
          await sleep(400);
          assert.equal(await evaluate("__rem.getState().status"), "offline");
          assert.equal(await evaluate("document.querySelector('.rem-composer textarea + button').disabled"), true);
          await shot(tag);
        }
        if (scene === "replying") { await shot(tag); }
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(`Rem home visual checks passed. Screenshots: ${out}`);
}
main().then(async () => { clearTimeout(timeout); await vite?.close(); win?.destroy(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); await vite?.close(); app.exit(1); });
