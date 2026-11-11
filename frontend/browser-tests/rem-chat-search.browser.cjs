/* global __dirname, console, setTimeout, clearTimeout */
// Searching Rem's chat, in a real Chromium. Run through run-rem-chat-search.cjs. A mock
// backend serves a long conversation and the search answers; no providers.
//
// Search opens from the chat header (and Ctrl+F), types with a real keyboard, and
// closing it leaves the chat scrolled where it was with the message box's draft kept.
// Choosing a result jumps far back to that message, opens it when its match is in the
// folded part of a long message, gives it focus and marks it, and the chat stays there.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "rem-chat-search-")));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
let vite, win;
const timeout = setTimeout(() => { console.error("Rem chat search test timed out"); app.exit(1); }, 180000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const entry = `import React from "react"; import { createRoot } from "react-dom/client";
import RemHomeWorkspace from "/src/components/RemHomeWorkspace.jsx";
import "/src/styles/index.css";
const at = new Date().toISOString();
const paragraph = n => Array.from({ length: n }, (_, i) => "Line " + i + " of a reply that wraps across the column and keeps going for a while.").join(" ");
// m12 is long enough to fold, and its only "zephyr" is near its end, in the folded part.
window.__chat = Array.from({ length: 400 }, (_, index) => ({
  id: "m" + index, role: index % 2 ? "rem" : "user", created_at: at,
  body: index === 12 ? paragraph(80) + " The zephyr launch moved to Friday." : index % 2 ? paragraph(1 + (index % 4)) : "Question " + index,
}));
window.__searches = [];
window.fetch = async input => {
  const url = String(input);
  let payload = {};
  if (url.endsWith("/conversation")) payload = { conversation: { id: "rem", turn_state: "idle" }, messages: window.__chat };
  else if (url.endsWith("/tasks")) payload = { tasks: [] };
  else if (url.includes("/search?")) {
    const q = new URL(url, location.href).searchParams.get("q");
    window.__searches.push(q);
    payload = { total: 1, next: null, results: [{ id: "m12", seq: 13, role: "rem", created_at: at, where: "message",
      snippet: [{ text: "…keeps going for a while. The ", match: false }, { text: "zephyr", match: true }, { text: " launch moved to Friday.", match: false }] }] };
  }
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
};
function App() {
  return <main style={{ height: "100vh", display: "flex" }}><section style={{ flex: 1, minWidth: 0, display: "flex" }}><RemHomeWorkspace /></section></main>;
}
createRoot(document.getElementById("root")).render(<App />);`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root, logLevel: "error", cacheDir: testViteCacheDir("rem-chat-search"), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "rem-chat-search",
    resolveId(id) { if (id === "/rem-chat-search-entry.jsx") return id; },
    load(id) { if (id === "/rem-chat-search-entry.jsx") return entry; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (!request.url.startsWith("/rem-chat-search-test")) return next();
      response.setHeader("Content-Type", "text/html");
      response.end(await server.transformIndexHtml("/rem-chat-search-test", '<html><body><div id="root"></div><script type="module" src="/rem-chat-search-entry.jsx"></script></body></html>'));
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
  const key = async (keyCode, modifiers = []) => {
    win.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers });
    await sleep(80);
  };
  const top = () => evaluate("Math.round(document.querySelector('.rem-stream').scrollTop)");
  const draft = () => evaluate("document.querySelector('.rem-composer textarea').value");
  const panelOpen = () => evaluate("Boolean(document.querySelector('.rem-search'))");
  // Where m12 and its matched words sit within the chat's visible area.
  const placement = () => evaluate(`(() => {
    const stream = document.querySelector('.rem-stream').getBoundingClientRect();
    const message = document.querySelector('[data-message-id="m12"]');
    const box = message.getBoundingClientRect();
    const walker = document.createTreeWalker(message, NodeFilter.SHOW_TEXT);
    let words = null;
    while (walker.nextNode()) {
      const at = walker.currentNode.textContent.indexOf("zephyr");
      if (at < 0) continue;
      const range = document.createRange();
      range.setStart(walker.currentNode, at);
      range.setEnd(walker.currentNode, at + "zephyr".length);
      words = range.getBoundingClientRect();
    }
    return { streamTop: stream.top, streamBottom: stream.bottom, top: box.top, bottom: box.bottom, focused: document.activeElement === message,
      found: message.dataset.found === "true", folded: Boolean(message.querySelector('[data-folded]')),
      wordsTop: words && words.top, wordsBottom: words && words.bottom };
  })()`);

  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-chat-search-test`);
  await wait("document.querySelector('[data-message-id=\"m399\"]')");
  await sleep(600);
  // A draft in the message box, and the chat scrolled up to read.
  await evaluate("document.querySelector('.rem-composer textarea').focus()");
  win.webContents.insertText("A half-written thought");
  await sleep(150);
  assert.equal(await draft(), "A half-written thought");
  await evaluate("(() => { const s = document.querySelector('.rem-stream'); s.scrollTop = s.scrollHeight - s.clientHeight - 1500; })()");
  await sleep(400);
  const reading = await top();
  assert.ok(reading > 1000, "The chat is scrolled up to read");

  // Ctrl+F in the chat opens search with the box focused; typing searches after a pause.
  await key("F", ["control"]);
  await wait("document.activeElement && document.activeElement.getAttribute('aria-label') === \"Search Rem's chat\"");
  win.webContents.insertText("zeph");
  await sleep(60);
  win.webContents.insertText("yr");
  await wait("document.querySelector('[data-search-result]')");
  assert.deepEqual(await evaluate("window.__searches"), ["zephyr"], "Typing searches once, after a pause");
  assert.equal(await evaluate("document.querySelector('.rem-search mark').textContent"), "zephyr");
  assert.match(await evaluate("document.querySelector('.rem-search-summary').textContent"), /^1 match$/);
  assert.equal(await top(), reading, "Searching doesn't move the chat");

  // Escape closes search: the chat stays where it was and the draft is kept.
  await key("Escape");
  assert.equal(await panelOpen(), false);
  assert.ok(Math.abs(await top() - reading) <= 1, "Closing search keeps the chat's scroll position");
  assert.equal(await draft(), "A half-written thought");
  assert.equal(await evaluate("document.activeElement.getAttribute('aria-label')"), "Search chat", "Focus returns to the search button");

  // The search button opens it again; a result jumps hundreds of messages back.
  await evaluate("document.querySelector('[aria-label=\"Search chat\"]').click()");
  await wait("document.activeElement && document.activeElement.getAttribute('aria-label') === \"Search Rem's chat\"");
  win.webContents.insertText("zephyr");
  await wait("document.querySelector('[data-search-result]')");
  assert.equal(await placement().then(p => p.folded), true, "The long message starts folded");
  await key("Down");
  assert.equal(await evaluate("document.activeElement.hasAttribute('data-search-result')"), true, "Down arrow moves to the results");
  // Enter on the focused result chooses it, as a real key press does (keyDown, char, keyUp).
  win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
  win.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
  win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
  await sleep(120);
  assert.equal(await panelOpen(), false, "Choosing a result closes search");
  await sleep(300);
  let where = await placement();
  assert.ok(where.focused && where.found, `The message gets focus and is marked: ${JSON.stringify(where)}`);
  assert.equal(where.folded, false, "The match's folded message opens");
  // The message is taller than the chat; its matched words, near its end, are brought into view and highlighted.
  assert.ok(where.top < where.streamBottom && where.bottom > where.streamTop, `The message is in view: ${JSON.stringify(where)}`);
  assert.ok(where.wordsTop >= where.streamTop && where.wordsBottom <= where.streamBottom, `The matched words are in view: ${JSON.stringify(where)}`);
  assert.equal(await evaluate("CSS.highlights.has('rem-found')"), true, "The matched words are highlighted");
  // Messages around it lay out as they come into view; the chat keeps the words there.
  await sleep(1200);
  where = await placement();
  assert.ok(where.wordsTop >= where.streamTop - 40 && where.wordsBottom <= where.streamBottom + 40, `The words stay in view: ${JSON.stringify(where)}`);
  assert.ok(await top() < reading - 5000, "The chat moved far back to the message");
  assert.equal(await draft(), "A half-written thought", "The draft survives the jump");
  await sleep(1400);
  assert.equal((await placement()).found, false, "The mark fades");
  assert.equal(await evaluate("CSS.highlights.has('rem-found')"), false, "The highlight goes with it");
  assert.deepEqual(errors, []);
  console.log("Rem chat search checks passed: Ctrl+F and the button open it, Escape keeps the place and draft, a result jumps far back to an opened, focused message with its words in view.");
}
main().then(() => finish()).catch(error => finish(error));
async function finish(error) { clearTimeout(timeout); win?.destroy(); await vite?.close(); if (error) console.error(error); app.exit(error ? 1 : 0); }
