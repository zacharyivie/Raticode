/* global __dirname, console, setTimeout, clearTimeout, process */
// Visual and interaction audit of the Rem home (task sidebar + conversation).
// Run through run-rem-home-visual.cjs. Scenes use explicit fixtures; no backend or providers.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow, protocol } = require("electron");
const { execFileSync } = require("node:child_process");
const { createMediaPreviews } = require("../electron/media-preview.cjs");
// The media scene serves real files through the app's own preview protocol.
protocol.registerSchemesAsPrivileged([{ scheme: "raticode-media", privileges: { standard: true, secure: true, stream: true } }]);
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
if (scene === "attachments") {
  // You sent a picture, a video, notes, a PDF and a file that has since gone; two more wait in the composer.
  const urls = JSON.parse(new URLSearchParams(location.search).get("media"));
  const [picture, video] = Object.keys(urls);
  const folder = picture.slice(0, picture.lastIndexOf("/"));
  window.goferDesktop = { textFiles: {
    openPreview: async target => { if (!urls[target]) throw new Error("ENOENT"); return { id: target, url: urls[target] }; },
    closePreview: async () => {},
    readAttachment: async () => ({ data: btoa("# Launch notes\\n\\n- Ship the phone build\\n- Check the chart\\n") }),
  }, workspace: { openPath: async () => {} } };
  const sent = [picture, video, folder + "/notes.md", folder + "/report.pdf", folder + "/gone.png"]
    .map((file, index) => ({ id: "s" + index, name: file.split("/").at(-1), size: 48213, type: "", path: file }));
  const messages = [{ id: "ask", role: "user", body: "Here's what I have so far.", attachments: sent, created_at: new Date().toISOString() }];
  window.fetch = async input => {
    const url = String(input);
    const payload = url.endsWith("/conversation") ? { conversation: { id: "rem", turn_state: "idle" }, messages } : url.endsWith("/tasks") ? { tasks: [] } : {};
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  };
  const canvas = Object.assign(document.createElement("canvas"), { width: 320, height: 200 });
  const paint = canvas.getContext("2d"); const gradient = paint.createLinearGradient(0, 0, 320, 200);
  gradient.addColorStop(0, "#4f46e5"); gradient.addColorStop(1, "#f472b6"); paint.fillStyle = gradient; paint.fillRect(0, 0, 320, 200);
  const photo = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
  const { attachToRemComposer } = await import("/src/lib/remComposer.js");
  attachToRemComposer([new File([photo], "photo.png", { type: "image/png" }), new File(["a,b\\n1,2\\n"], "data.csv", { type: "text/csv" })]);
} else if (scene === "media") {
  // Rem replies with a picture, a video, a file that has since gone and a text file.
  const urls = JSON.parse(new URLSearchParams(location.search).get("media"));
  const [picture, video] = Object.keys(urls);
  const folder = picture.slice(0, picture.lastIndexOf("/"));
  window.goferDesktop = { textFiles: {
    openPreview: async target => { if (!urls[target]) throw new Error("ENOENT"); return { id: target, url: urls[target] }; },
    closePreview: async () => {},
  } };
  const files = [picture, video, folder + "/gone.png", folder + "/notes.txt"].map(file => ({ name: file.split("/").at(-1), size: 48213, path: file }));
  const messages = [
    { id: "ask", role: "user", body: "Send me the chart and the demo video.", created_at: new Date().toISOString() },
    { id: "reply", role: "rem", body: "Here are the chart and the demo.", files, created_at: new Date().toISOString() },
  ];
  window.fetch = async input => {
    const url = String(input);
    const payload = url.endsWith("/conversation") ? { conversation: { id: "rem", turn_state: "idle" }, messages } : url.endsWith("/tasks") ? { tasks: [] } : {};
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  };
} else if (scene === "history") {
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
} else if (scene === "reply") {
  // A small backend: it stores what is posted and previews the message a reply refers to.
  const at = new Date().toISOString();
  window.__posted = [];
  window.__chat = [
    { id: "rem-old", role: "rem", body: "The **deploy** failed on step three because the cache key changed. " + "Rebuilding fixes it. ".repeat(20), created_at: at },
    ...Array.from({ length: 12 }, (_, index) => ({ id: "fill-" + index, role: index % 2 ? "rem" : "user", body: "Filler message " + index, created_at: at })),
    { id: "u-gone", role: "user", body: "What about that one?", refers_to: "cleared", created_at: at },
  ];
  const preview = id => {
    const found = window.__chat.find(message => message.id === id);
    return found ? { id, role: found.role, body: found.body.replace(/[*_\x60]/g, "").slice(0, 279) + "…", created_at: found.created_at } : { id, unavailable: true };
  };
  window.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.endsWith("/messages") && options.method === "POST") {
      const body = JSON.parse(options.body);
      window.__posted.push(body);
      window.__chat.push({ id: body.request_id, request_id: body.request_id, role: "user", body: body.body, created_at: new Date().toISOString(), ...(body.refers_to ? { refers_to: body.refers_to } : {}) });
      return new Response(JSON.stringify({ accepted: true, request_id: body.request_id }), { headers: { "Content-Type": "application/json" } });
    }
    const messages = window.__chat.map(message => message.refers_to ? { ...message, referenced: preview(message.refers_to) } : message);
    const payload = url.endsWith("/conversation") ? { conversation: { id: "rem", turn_state: "idle" }, messages } : url.endsWith("/tasks") ? { tasks: [] } : {};
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  };
} else {
  window.fetch = async (input, options) => String(input).includes("/api/rem/") ? new Response(JSON.stringify({ detail: "Not Found" }), { status: 404, headers: { "Content-Type": "application/json" } }) : nativeFetch(input, options);
  if (scene !== "offline") await store.loadRemHomeFixture(scene === "empty" ? "empty" : scene === "replying" ? "replying" : "busy");
}

// ?side=<px> sets the project sidebar's width, rail included (240 to 420 in the app).
const side = Number(new URLSearchParams(location.search).get("side")) || 272;
// ?pane=<px> renders Rem's chat alone in a side pane of that width, as Settings > Layout can place it.
const pane = Number(new URLSearchParams(location.search).get("pane")) || 0;
function App() {
  if (pane) return <main style={{ height: "100vh", display: "flex" }}>
    <section style={{ flex: 1, minWidth: 0 }} />
    <aside className="studio-rem-pane bg-white" data-rem-pane="true" style={{ width: pane, flexShrink: 0, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden", borderLeft: "1px solid rgb(var(--color-line))" }}>
      <RemHomeWorkspace projectOptions={SAMPLE_PROJECTS} />
    </aside>
  </main>;
  return <main style={{ height: "100vh", display: "flex" }}>
    <aside className="studio-sidebar bg-white" style={{ width: side, flexShrink: 0, display: "flex", borderRight: "1px solid rgb(var(--color-line))" }}>
      <div style={{ width: 40, flexShrink: 0, borderRight: "1px solid rgb(var(--color-line))" }} />
      <RemHomeSidebar active open onOpen={() => {}} projectOptions={SAMPLE_PROJECTS} />
    </aside>
    <section style={{ flex: 1, minWidth: 0, display: "flex" }}><RemHomeWorkspace projectOptions={SAMPLE_PROJECTS} /></section>
  </main>;
}
createRoot(document.getElementById("root")).render(<App />);`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  // A private optimizer cache keeps the running dev app's shared one untouched.
  vite = await createServer({ root, logLevel: "error", cacheDir: fs.mkdtempSync(path.join(os.tmpdir(), "rem-home-vite-")), server: { host: "127.0.0.1", port: 0 }, plugins: [{
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
  const media = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "rem-home-media-")));
  const ffmpeg = (args, name) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args, "-threads", "1", path.join(media, name)]);
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x400:rate=5:duration=1", "-frames:v", "1"], "chart.png");
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"], "demo.mp4");
  const previews = createMediaPreviews();
  protocol.handle("raticode-media", request => previews.handle(request));
  const mediaUrls = Object.fromEntries(["chart.png", "demo.mp4"].map(name => [path.join(media, name), previews.open(path.join(media, name), 0).url]));
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
  const scenes = (process.env.CAPTURE_SCENES || "busy,running,blocked,new,menu,empty,offline,replying,history,reply").split(",");
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_WIDTHS || "1440,980").split(",").map(Number)) {
      for (const scene of scenes) {
        win.setSize(width, 900);
        await win.loadURL(`${base}?scene=${scene}${scene === "media" || scene === "attachments" ? `&media=${encodeURIComponent(JSON.stringify(mediaUrls))}` : ""}`); await sleep(900);
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
          // The task stays open in the sidebar while the chat scrolls to the message.
          await evaluate("document.querySelector('.rem-update-message').click()");
          await sleep(250);
          assert.equal(await evaluate("Boolean(document.querySelector('.rem-side .rem-sheet'))"), true, "The task stays open beside the chat");
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
          // Every message carries its time; back-to-back Rem replies are split by a faint rule.
          assert.equal(await evaluate("[...document.querySelectorAll('[data-message-id]')].every(el => el.querySelector('time.rem-msg-time')?.textContent.trim())"), true, `Time stamp missing in ${tag}`);
          // m-4 joins them only when the fixture's "22 hours ago" and "95 minutes ago" fall on the same day.
          const follows = await evaluate("[...document.querySelectorAll('[data-follows]')].map(el => el.previousElementSibling?.dataset.role === 'rem' && el.dataset.role === 'rem' ? el.dataset.messageId : 'wrong')");
          assert.ok(follows.includes("m-3") && follows.every(id => ["m-3", "m-4"].includes(id)), `Rem dividers in ${tag}: ${follows}`);
          assert.equal(await evaluate("getComputedStyle(document.querySelector('[data-follows] .rem-msg-body')).borderTopStyle"), "solid");
          await evaluate("document.querySelector('.rem-stream').scrollTop = 0");
          await shot(`${tag}-earlier`);
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
          assert.equal(await evaluate("document.querySelector('.rem-composer button[aria-label=\"Send\"]').disabled"), true);
          await shot(tag);
        }
        if (scene === "replying") { await shot(tag); }
        if (scene === "reply") {
          const inMessage = (id, code) => evaluate(`(() => { const el = document.querySelector('[data-message-id=${JSON.stringify(id)}]'); return ${code}; })()`);
          // Any message can be replied to, Rem's and your own; the message box says which.
          assert.equal(await inMessage("u-gone", "Boolean(el.querySelector('button[aria-label=\"Reply to this message\"]'))"), true, "Your own messages offer Reply");
          await inMessage("rem-old", "el.querySelector('button[aria-label=\"Reply to this message\"]').click()"); await sleep(200);
          assert.equal(await evaluate("document.querySelector('.rem-composer .rem-quote-author').textContent"), "Replying to Rem");
          assert.match(await evaluate("document.querySelector('.rem-composer .rem-quote-text').textContent"), /^The deploy failed on step three/);
          assert.equal(await evaluate("document.activeElement === document.querySelector('.rem-composer textarea')"), true, "Reply puts the cursor in the message box");
          await click("Don't reply to this message");
          assert.equal(await evaluate("document.querySelector('.rem-composer .rem-quote')"), null);
          await inMessage("rem-old", "el.querySelector('button[aria-label=\"Reply to this message\"]').click()"); await sleep(200);
          await evaluate("document.querySelector('.rem-composer textarea').focus()");
          await win.webContents.insertText("Why did the cache key change?");
          await shot(`${tag}-composing`);
          await click("Send"); await sleep(600);
          assert.deepEqual(await evaluate("window.__posted.map(p => [p.body, p.refers_to])"), [["Why did the cache key change?", "rem-old"]]);
          assert.equal(await evaluate("document.querySelector('.rem-composer .rem-quote')"), null, "Sending clears the reply");
          await evaluate("__rem.refreshRemHome()"); await sleep(300);
          const sent = await evaluate("window.__posted[0].request_id");
          assert.equal(await inMessage(sent, "el.querySelector('.rem-quote-author').textContent"), "In reply to Rem");
          await shot(`${tag}-sent`);
          // The quote shows the original, far up the chat.
          await evaluate("document.querySelector('.rem-stream').scrollTop = document.querySelector('.rem-stream').scrollHeight"); await sleep(150);
          await inMessage(sent, "el.querySelector('button.rem-quote-main').click()"); await sleep(250);
          assert.equal(await evaluate("document.activeElement.dataset.messageId"), "rem-old");
          const box = await evaluate("(() => { const b = document.activeElement.getBoundingClientRect(); return { top: b.top, bottom: b.bottom }; })()");
          assert.ok(box.top >= 0 && box.top <= 900, "The quote did not reveal the original message");
          // A message that has left the chat says so and can't be opened.
          assert.equal(await inMessage("u-gone", "el.querySelector('.rem-quote-text').textContent"), "No longer in this chat");
          assert.equal(await inMessage("u-gone", "Boolean(el.querySelector('button.rem-quote-main'))"), false);
          await inMessage("u-gone", "el.querySelector('button[aria-label=\"Reply to this message\"]').click()"); await sleep(200);
          assert.equal(await evaluate("document.querySelector('.rem-composer .rem-quote-author').textContent"), "Replying to You");
          await shot(tag);
        }
        if (scene === "attachments") {
          await sleep(600);
          const tile = name => `[aria-label="Preview ${name}"]`;
          // Pictures show themselves in the composer and on the sent message; other files show their type.
          assert.equal(await evaluate(`document.querySelector('.rem-composer ${tile("photo.png")} img')?.naturalWidth`), 320);
          assert.equal(await evaluate(`document.querySelector('.rem-bubble-files ${tile("chart.png")} img')?.naturalWidth`), 640);
          assert.ok(await evaluate(`document.querySelector('.rem-bubble-files ${tile("demo.mp4")} video')?.readyState`) >= 1, "Video thumbnail loads");
          assert.match(await evaluate(`document.querySelector('.rem-bubble-files ${tile("report.pdf")}').textContent`), /PDF/);
          assert.match(await evaluate(`document.querySelector('.rem-bubble-files ${tile("gone.png")}').textContent`), /No longer available/);
          assert.match(await evaluate(`document.querySelector('.rem-composer ${tile("data.csv")}').textContent`), /CSV/);
          await shot(tag);
          await click("Preview notes.md"); await sleep(500);
          assert.match(await evaluate("document.querySelector('[data-media-viewer=text] pre')?.textContent || ''"), /Ship the phone build/);
          await shot(`${tag}-text`);
          await click("Close");
          await click("Preview report.pdf"); await sleep(400);
          assert.match(await evaluate("document.querySelector('[data-media-viewer=file]')?.textContent || ''"), /Open in editor/);
          await shot(`${tag}-file`);
          await click("Close");
          await click("Preview photo.png"); await sleep(400);
          assert.equal(await evaluate("document.querySelector('[data-media-viewer=image] img')?.naturalWidth"), 320);
          await click("Close");
        }
        if (scene === "media") {
          await sleep(600);
          // The picture decodes and the video's first frame loads inline; the missing file says so.
          assert.equal(await evaluate("document.querySelector('.rem-media[data-kind=image] img')?.naturalWidth"), 640);
          assert.ok(await evaluate("document.querySelector('.rem-media[data-kind=video] video')?.readyState") >= 1, "Video poster loads");
          assert.match(await evaluate("document.querySelector('.rem-media-list').textContent"), /This picture is no longer on this computer/);
          assert.match(await evaluate("document.querySelector('.rem-files').textContent"), /notes\.txt/);
          await shot(tag);
          await click("View chart.png larger");
          assert.equal(await evaluate("document.querySelector('[data-media-viewer=image] img')?.naturalWidth"), 640);
          await shot(`${tag}-viewer`);
          await evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))"); await sleep(200);
          assert.equal(await evaluate("Boolean(document.querySelector('[data-media-viewer]'))"), false, "Escape closes the viewer");
          await click("Play demo.mp4"); await sleep(1500);
          assert.ok(await evaluate("document.querySelector('[data-media-viewer=video] video').currentTime") > 0.3, "The player starts playing");
          await shot(`${tag}-player`);
          await click("Close");
        }
      }
    }
  }
  // An opened task fills the Rem sidebar, never the chat, at the narrowest and
  // widest sidebar, and the back arrow returns to every task.
  const sideOverflow = `(() => {
    const panel = document.querySelector('#sidebar-panel-rem').getBoundingClientRect();
    const name = e => (e.tagName.toLowerCase() + '.' + String(e.className?.baseVal ?? e.className).trim().split(/\\s+/).slice(0, 2).join('.')).slice(0, 60);
    const all = [...document.querySelectorAll('#sidebar-panel-rem *')].filter(e => !e.closest('.rem-menu'));
    const scrollers = all.filter(e => e.scrollWidth > e.clientWidth + 1 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowX)).map(name);
    const outside = all.filter(e => { const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0 && (box.right > panel.right + 1 || box.left < panel.left - 1); })
      .filter((e, _, list) => !list.includes(e.parentElement)).map(name);
    return { scrollers, outside: outside.slice(0, 8) };
  })()`;
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_SIDES || "240,420").split(",").map(Number)) {
      for (const scene of ["running", "blocked", "new"]) {
        win.setSize(1200, 900);
        await win.loadURL(`${base}?scene=${scene === "new" ? "busy" : scene}&side=${width}`); await sleep(900);
        await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"})`); await sleep(200);
        const tag = `side-${theme}-${width}-${scene}`;
        if (scene === "running") await openTask("Local-first sync engines");
        if (scene === "blocked") await openTask("CSV export for Runs");
        if (scene === "new") { await click("New task"); await click("Weekly", "[role=radio]"); }
        assert.equal(await evaluate("Boolean(document.querySelector('#sidebar-panel-rem .rem-sheet')) && !document.querySelector('.rem-home .rem-sheet')"), true, `${tag}: the task opens in the sidebar, not the chat`);
        assert.equal(await evaluate("document.querySelectorAll('.rem-task-row').length"), 0, `${tag}: the task replaces the list`);
        for (const top of [0, 1e9]) {
          await evaluate(`document.querySelector('.rem-sheet-scroll').scrollTop = ${top}`); await sleep(150);
          const found = await evaluate(sideOverflow);
          assert.deepEqual(found, { scrollers: [], outside: [] }, `${tag} spills sideways: ${JSON.stringify(found)}`);
          fs.writeFileSync(path.join(out, `${tag}-${top ? "end" : "top"}.png`), (await win.webContents.capturePage()).toPNG());
        }
        // The back arrow sits at the top left, like leaving a thread.
        const back = await evaluate("(() => { const header = document.querySelector('.rem-sheet-header').getBoundingClientRect(); const button = document.querySelector('[aria-label=\"Back to all tasks\"]').getBoundingClientRect(); return button.left - header.left < 16 && button.top - header.top < 16; })()");
        assert.equal(back, true, `${tag}: back arrow at the top left`);
        await click("Back to all tasks");
        assert.equal(await evaluate("document.querySelectorAll('.rem-task-row').length > 0 && !document.querySelector('.rem-sheet')"), true, `${tag}: back returns to every task`);
      }
    }
  }
  // Rem in a side pane never scrolls sideways: every scene at the narrowest
  // allowed pane width, the default width and the width from the user's report.
  // Code blocks and charts keep their own sideways scroll; nothing else may.
  const paneOverflow = `(() => {
    const pane = document.querySelector('[data-rem-pane]').getBoundingClientRect();
    const own = e => e.closest('pre, .rc-chart-scroll, [data-media-viewer], .rem-menu');
    const name = e => (e.tagName.toLowerCase() + '.' + String(e.className?.baseVal ?? e.className).trim().split(/\\s+/).slice(0, 2).join('.')).slice(0, 60);
    const all = [...document.querySelectorAll('[data-rem-pane] *')].filter(e => !own(e));
    const scrollers = all.filter(e => e.scrollWidth > e.clientWidth + 1 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowX)).map(name);
    const outside = all.filter(e => { const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0 && (box.right > pane.right + 1 || box.left < pane.left - 1); })
      .filter((e, _, list) => !list.includes(e.parentElement)).map(name);
    // Content also stays inside its message column, so cards never spill past their edges.
    const spill = all.filter(e => { const body = e.parentElement?.closest('.rem-msg-body'); if (!body) return false; const box = e.getBoundingClientRect(); return box.width > 0 && box.right > body.getBoundingClientRect().right + 1; })
      .filter((e, _, list) => !list.includes(e.parentElement)).map(name);
    return { scrollers, outside: [...outside, ...spill].slice(0, 8) };
  })()`;
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_PANES || "300,380,571").split(",").map(Number)) {
      for (const scene of (process.env.CAPTURE_PANE_SCENES || "busy,replying,history,reply,media,attachments,empty").split(",")) {
        win.setSize(1440, 900);
        await win.loadURL(`${base}?scene=${scene}&pane=${width}${scene === "media" || scene === "attachments" ? `&media=${encodeURIComponent(JSON.stringify(mediaUrls))}` : ""}`); await sleep(900);
        await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"})`); await sleep(200);
        // Expand every Details section so their contents are measured too.
        await evaluate("document.querySelectorAll('[data-rem-pane] details').forEach(d => { d.open = true; })"); await sleep(200);
        for (const top of [0, 1e9]) {
          await evaluate(`document.querySelector('.rem-stream').scrollTop = ${top}`); await sleep(150);
          const found = await evaluate(paneOverflow);
          assert.deepEqual(found, { scrollers: [], outside: [] }, `Rem side pane ${theme} ${width}px ${scene} scrolls sideways: ${JSON.stringify(found)}`);
        }
        fs.writeFileSync(path.join(out, `pane-${theme}-${width}-${scene}.png`), (await win.webContents.capturePage()).toPNG());
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(`Rem home visual checks passed. Screenshots: ${out}`);
}
main().then(async () => { clearTimeout(timeout); await vite?.close(); win?.destroy(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); await vite?.close(); app.exit(1); });
