/* global __dirname, console, setTimeout, clearTimeout, process */
// Wheel scrolling in Rem's chat, in a real Chromium with smooth wheel scrolling.
// Run through run-rem-chat-scroll.cjs. A mock backend serves the conversation; no providers.
//
// From the bottom of the chat, a wheel turn upward must move the chat up and keep it there,
// even while messages lay out as they come near (content-visibility) and while Rem's
// reply is still being written. At the bottom, the chat still follows new text.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "rem-chat-scroll-")));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
// Wheel turns animate, as they do on the desktop.
app.commandLine.appendSwitch("enable-smooth-scrolling");
let vite, win;
const timeout = setTimeout(() => { console.error("Rem chat scroll test timed out"); app.exit(1); }, 240000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const trace = Boolean(process.env.REM_SCROLL_TRACE);

const entry = `import React from "react"; import { createRoot } from "react-dom/client";
import RemHomeWorkspace from "/src/components/RemHomeWorkspace.jsx";
import * as store from "/src/lib/remHomeStore.js";
import "/src/styles/index.css";
window.__rem = store;
const at = new Date().toISOString();
// Messages of very different heights, so each one changes size when it first lays out.
const paragraph = n => Array.from({ length: n }, (_, i) => "Line " + i + " of a longer reply that wraps across the column and keeps going for a while.").join(" ");
const list = n => Array.from({ length: n }, (_, i) => "- item " + i + " with a few words").join("\\n");
window.__chat = Array.from({ length: 90 }, (_, index) => ({
  id: "m" + index, role: index % 2 ? "rem" : "user", created_at: at,
  body: index % 2 ? (index % 6 === 1 ? paragraph(6) + "\\n\\n" + list(8) : index % 6 === 3 ? "Short reply " + index : paragraph(2 + (index % 5))) : "Question " + index,
}));
window.__conversation = { id: "rem", turn_state: "idle" };
window.fetch = async input => {
  const url = String(input);
  const payload = url.endsWith("/conversation") ? { conversation: window.__conversation, messages: window.__chat }
    : url.endsWith("/tasks") ? { tasks: [] } : {};
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
};
// Rem writes its reply: the draft grows and the chat refreshes, as the poll does.
let writer = null;
window.__startReply = (ms = 60) => {
  window.__conversation = { id: "rem", turn_state: "replying", turn_label: "Writing", draft_reply: "Working on it." };
  void store.refreshRemHome();
  writer = setInterval(() => {
    window.__conversation = { ...window.__conversation, draft_reply: window.__conversation.draft_reply + " More of the reply arrives as it is written." };
    void store.refreshRemHome();
  }, ms);
};
window.__stopReply = () => { clearInterval(writer); writer = null; };
// Every scroll position the chat takes, with its distance from the bottom.
window.__positions = [];
let watched = null;
window.__watch = () => {
  const stream = document.querySelector(".rem-stream");
  window.__positions = [];
  if (watched === stream) return;
  watched = stream;
  stream.addEventListener("scroll", () => window.__positions.push({ t: Math.round(performance.now()), top: Math.round(stream.scrollTop), gap: Math.round(stream.scrollHeight - stream.scrollTop - stream.clientHeight) }));
};
function App() {
  return <main style={{ height: "100vh", display: "flex" }}><section style={{ flex: 1, minWidth: 0, display: "flex" }}><RemHomeWorkspace /></section></main>;
}
createRoot(document.getElementById("root")).render(<App />);`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root, logLevel: "error", cacheDir: testViteCacheDir("rem-chat-scroll"), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "rem-chat-scroll",
    resolveId(id) { if (id === "/rem-chat-scroll-entry.jsx") return id; },
    load(id) { if (id === "/rem-chat-scroll-entry.jsx") return entry; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (!request.url.startsWith("/rem-chat-scroll-test")) return next();
      response.setHeader("Content-Type", "text/html");
      response.end(await server.transformIndexHtml("/rem-chat-scroll-test", '<html><body><div id="root"></div><script type="module" src="/rem-chat-scroll-entry.jsx"></script></body></html>'));
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 1000, height: 760, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", details => { if (details.level === "error") { errors.push(details.message); console.error(`[page] ${details.message}`); } });
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  const wait = async (source, ms = 10000) => {
    const deadline = Date.now() + ms;
    while (!await evaluate(`Boolean(${source})`)) { if (Date.now() > deadline) throw new Error(`Missing condition: ${source}`); await sleep(30); }
  };
  const gap = () => evaluate("(() => { const s = document.querySelector('.rem-stream'); return Math.round(s.scrollHeight - s.scrollTop - s.clientHeight); })()");
  const box = await (async () => {
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-scroll-test`);
    await wait("document.querySelector('[data-message-id=\"m89\"]')");
    await sleep(600);
    return evaluate("(() => { const b = document.querySelector('.rem-stream').getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; })()");
  })();
  // One notch of a mouse wheel: Chromium animates it over a few frames.
  const notch = async (up = true) => {
    win.webContents.sendInputEvent({ type: "mouseMove", x: box.x, y: box.y });
    win.webContents.sendInputEvent({ type: "mouseWheel", x: box.x, y: box.y, deltaX: 0, deltaY: up ? 100 : -100, wheelTicksX: 0, wheelTicksY: up ? 1 : -1, canScroll: true, hasPreciseScrollingDeltas: false });
  };
  const report = async label => { if (trace) console.log(label, JSON.stringify(await evaluate("window.__positions"))); };
  // While Rem writes, the chat resizes between frames, so following is judged by where
  // each scroll left it: the chat moved with the new text and every move ended at the bottom.
  const following = async label => {
    await report(label);
    const positions = await evaluate("window.__positions");
    assert.ok(positions.length >= 2 && positions.every(position => position.gap <= 1), `The chat follows the reply while it is written (${label} ${JSON.stringify(positions)})`);
  };
  // A turn upward only ever moves up: no frame jumps back toward the bottom.
  const steadyClimb = async label => {
    const positions = await evaluate("window.__positions");
    assert.ok(positions.length >= 2, `The wheel moved the chat (${label})`);
    const jump = positions.findIndex((position, index) => index > 0 && position.top > positions[index - 1].top + 1);
    assert.equal(jump, -1, `A wheel turn up jumped back toward the bottom (${label}): ${JSON.stringify(positions)}`);
  };
  const toBottom = async () => {
    await evaluate("(() => { const s = document.querySelector('.rem-stream'); s.scrollTop = s.scrollHeight; })()");
    await sleep(400);
    await evaluate("(() => { const s = document.querySelector('.rem-stream'); s.scrollTop = s.scrollHeight; })()");
    await sleep(200);
  };

  // 1. Reading back from the bottom of a quiet chat. Each try starts from a fresh load,
  // when the messages above the view haven't laid out yet.
  const quiet = [];
  for (let attempt = 0; attempt < 6; attempt++) {
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-scroll-test`);
    await wait("document.querySelector('[data-message-id=\"m89\"]')");
    await sleep(500);
    assert.ok(await gap() <= 1, "The chat opens at its newest message");
    await evaluate("window.__watch()");
    await notch();
    await sleep(500);
    const after = await gap();
    await report(`quiet ${attempt}:`);
    await steadyClimb(`quiet ${attempt}`);
    quiet.push(after);
  }
  console.log(`Quiet chat, distance from the bottom after one wheel notch up: ${quiet.join(", ")}`);
  assert.ok(quiet.every(value => value >= 60), `A wheel notch up from the bottom of a quiet chat snapped back: ${quiet.join(", ")}`);

  // 2. A message changes size during the turn, as a task card update or a picture
  // loading does, with no reply being written.
  const resized = [];
  for (let attempt = 0; attempt < 4; attempt++) {
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-scroll-test`);
    await wait("document.querySelector('[data-message-id=\"m89\"]')");
    await sleep(500);
    await evaluate("window.__watch()");
    await notch();
    await sleep(30 + attempt * 25);
    await evaluate("window.__chat = window.__chat.map(message => message.id === 'm89' ? { ...message, body: message.body + ' Updated with a few more lines. '.repeat(8) } : message); window.__rem.refreshRemHome()");
    await sleep(500);
    resized.push(await gap());
    await report(`resized ${attempt}:`);
    await steadyClimb(`resized ${attempt}`);
  }
  console.log(`A message resizing during the turn, distance from the bottom after one notch up: ${resized.join(", ")}`);
  assert.ok(resized.every(value => value >= 60), `A message resizing during a wheel turn snapped the chat back: ${resized.join(", ")}`);

  // 3. Reading back while Rem's reply is being written.
  const busy = [];
  for (let attempt = 0; attempt < 6; attempt++) {
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-scroll-test`);
    await wait("document.querySelector('[data-message-id=\"m89\"]')");
    await sleep(300);
    await evaluate("window.__startReply()");
    await wait("document.querySelector('.rem-draft')");
    await sleep(300 + attempt * 37);
    await evaluate("window.__watch()");
    await sleep(500);
    await following(`follow ${attempt}:`);
    await evaluate("window.__watch()");
    await notch();
    await sleep(160);
    await notch();
    await sleep(600);
    const after = await gap();
    await report(`busy ${attempt}:`);
    await steadyClimb(`busy ${attempt}`);
    busy.push(after);
    await evaluate("window.__stopReply()");
  }
  console.log(`While Rem writes, distance from the bottom after two wheel notches up: ${busy.join(", ")}`);
  assert.ok(busy.every(value => value >= 120), `Wheel notches up while Rem writes snapped back: ${busy.join(", ")}`);

  // 4. Reading up stays put while the reply grows, and wheeling back down follows it again.
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-scroll-test`);
  await wait("document.querySelector('[data-message-id=\"m89\"]')");
  await sleep(300);
  await evaluate("window.__startReply(120)");
  await wait("document.querySelector('.rem-draft')");
  await sleep(300);
  for (let index = 0; index < 4; index++) { await notch(); await sleep(120); }
  await sleep(400);
  const readingTop = await evaluate("document.querySelector('.rem-stream').scrollTop");
  assert.ok(await gap() >= 200, "Four notches up move well away from the bottom");
  await sleep(800);
  assert.ok(Math.abs(await evaluate("document.querySelector('.rem-stream').scrollTop") - readingTop) <= 2, "The chat stays where you are reading while the reply grows");
  for (let index = 0; index < 30 && await gap() > 1; index++) { await notch(false); await sleep(120); }
  assert.ok(await gap() <= 1, "Wheeling down reaches the newest text");
  await evaluate("window.__watch()");
  await sleep(900);
  await following("back at the bottom:");
  await evaluate("window.__stopReply()");

  // 5. A new message while you sit at the bottom brings it into view.
  await toBottom();
  await evaluate("window.__conversation = { id: 'rem', turn_state: 'idle' }; window.__chat = [...window.__chat, { id: 'late', role: 'rem', body: 'A new reply. ' + 'More words. '.repeat(60), created_at: new Date().toISOString() }]; window.__rem.refreshRemHome()");
  await wait("document.querySelector('[data-message-id=\"late\"]')");
  await sleep(400);
  assert.ok(await gap() <= 1, "A new message at the bottom stays in view");
  assert.deepEqual(errors, []);
  console.log("Rem chat scroll checks passed: wheel up from the bottom moves and stays, while quiet and while Rem writes; following resumes at the bottom.");
}
main().then(() => finish()).catch(error => finish(error));
async function finish(error) { clearTimeout(timeout); win?.destroy(); await vite?.close(); if (error) console.error(error); app.exit(error ? 1 : 0); }
