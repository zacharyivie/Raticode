#!/usr/bin/env node
// Background acceptance: a real, isolated Raticode desktop closed to the tray, and the
// emulator phone over Wi-Fi. Uses a temporary profile, data folder and device port, so an
// everyday Raticode instance and its paired phones are untouched. No provider or workflow runs.
//
// Required: RATICODE_DEVICE_HOST (this computer's private IP), a running emulator, Android SDK/JDK environment, xvfb-run, Node 22 and uv.
// Optional: HIDDEN_SECONDS (default 360, past Chromium's five-minute background threshold).
import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const host = process.env.RATICODE_DEVICE_HOST;
if (!host) throw new Error("Set RATICODE_DEVICE_HOST.");
const hiddenSeconds = Number(process.env.HIDDEN_SECONDS || 360);
const mobile = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const desktop = process.env.RATICODE_DESKTOP_SOURCE || path.resolve(mobile, "../..");
const frontend = path.join(desktop, "frontend");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-background-"));
const vitePort = 5299, debugPort = 9333, mainPort = 9334, devicePort = 38473;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const children = [];
const report = { hiddenSeconds, steps: [] };
const step = (name, extra = {}) => { const entry = { name, at: new Date().toISOString(), ...extra }; report.steps.push(entry); console.log(JSON.stringify(entry)); };

function start(command, args, options) {
  const child = spawn(command, args, { ...options, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const log = fs.createWriteStream(path.join(temp, `${path.basename(command)}-${children.length}.log`));
  child.stdout.pipe(log); child.stderr.pipe(log);
  child.on("error", error => { child.failure = error; });
  children.push(child);
  return child;
}

async function until(label, check, timeout = 120000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    try { const value = await check(); if (value) return value; } catch { /* Retry until the deadline. */ }
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${label}`);
    await sleep(1000);
  }
}

let socket, nextId = 0;
const pending = new Map();
async function connect() {
  const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
  const page = targets.find(t => t.type === "page" && t.url.startsWith(`http://127.0.0.1:${vitePort}`));
  if (!page) return false;
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  };
  return true;
}
function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise(resolve => pending.set(id, resolve));
}
async function evaluate(expression) {
  const reply = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (reply.result?.exceptionDetails) throw new Error(reply.result.exceptionDetails.text);
  return reply.result?.result?.value;
}
// The renderer's fetch adds its own local API token; nothing here reads it.
// Main-process inspector: close the window exactly as the window's close button does.
async function main(expression) {
  const [target] = await (await fetch(`http://127.0.0.1:${mainPort}/json/list`)).json();
  const inspector = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { inspector.onopen = resolve; inspector.onerror = reject; });
  const reply = await new Promise(resolve => {
    inspector.onmessage = event => { const m = JSON.parse(event.data); if (m.id === 1) resolve(m); };
    inspector.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression, returnByValue: true } }));
  });
  inspector.close();
  return reply.result?.result?.value;
}
const studio = `process.mainModule.require("electron").BrowserWindow.getAllWindows().find(w => w.webContents.getURL().includes(":${vitePort}"))`;
const api = body => evaluate(`(async () => {
  const base = window.__goferSessionBaseUrl || window.goferApiBaseUrl;
  const response = await fetch(base + "/api/devices", ${body ? `{ method: "POST", headers: { "Content-Type": "application/json" }, body: ${JSON.stringify(JSON.stringify(body))} }` : "{ cache: \"no-store\" }"});
  return { ok: response.ok, body: await response.json() };
})()`);

const thread = "background-test-thread";
const injectThread = `(() => {
  const now = new Date().toISOString();
  const meta = { id: "${thread}", title: "Background test thread", provider: "codex", model: "cli-default", createdAt: now, updatedAt: now };
  localStorage.setItem("gofer-flow-chat-thread-meta:${thread}", JSON.stringify(meta));
  localStorage.setItem("gofer-flow-chat-threads", JSON.stringify([{ id: meta.id, title: meta.title, updatedAt: now }]));
  localStorage.setItem("gofer-flow-chat-thread:${thread}", JSON.stringify([
    { id: "desktop-1", role: "user", body: "Hello from the desktop" },
    { id: "desktop-2", role: "assistant", body: "Desktop reply", kind: "final" }]));
  return true;
})()`;
// The same records a desktop turn writes: a message, then the thread's updated time.
const addHiddenMessage = `(async () => {
  const db = await new Promise((resolve, reject) => { const r = indexedDB.open("gofer-flow-conversations", 1); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  const tx = db.transaction(["messages", "threads"], "readwrite");
  const done = new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
  const threads = tx.objectStore("threads");
  const meta = await new Promise(resolve => { const q = threads.get("${thread}"); q.onsuccess = () => resolve(q.result || { id: "${thread}", sequence: 0 }); });
  meta.sequence += 1;
  const message = { id: "hidden-1", role: "assistant", body: "Added while hidden", kind: "final" };
  tx.objectStore("messages").put({ threadId: "${thread}", id: "hidden-1", sequence: meta.sequence, message, text: "added while hidden", grams: [] });
  threads.put(meta);
  await done;
  const key = "gofer-flow-chat-thread-meta:${thread}";
  const stored = JSON.parse(localStorage.getItem(key)); stored.updatedAt = new Date().toISOString();
  localStorage.setItem(key, JSON.stringify(stored));
  return true;
})()`;

let failed = false;
try {
  start("npx", ["vite", "--host", "127.0.0.1", "--port", String(vitePort), "--strictPort"], { cwd: frontend });
  await until("Vite", async () => (await fetch(`http://127.0.0.1:${vitePort}`)).ok);
  start("xvfb-run", ["-a", "node", "electron/dev-runner.cjs", `--user-data-dir=${temp}/profile`, `--remote-debugging-port=${debugPort}`, `--inspect=${mainPort}`], {
    cwd: frontend,
    env: { ...process.env, GOFER_VITE_DEV_SERVER_URL: `http://127.0.0.1:${vitePort}`, GOFER_DATA_DIR: `${temp}/data`,
      RATICODE_DEVICE_EXPERIMENTAL_NETWORK: "1", RATICODE_DEVICE_HOST: host, RATICODE_DEVICE_PORT: String(devicePort) },
  });
  await until("desktop window", connect, 180000);
  await until("desktop backend", async () => (await api())?.ok, 180000);
  await evaluate(injectThread);
  await send("Page.reload");
  await sleep(3000);
  await until("reloaded window", connect);
  await until("desktop backend after reload", async () => (await api())?.ok, 60000);
  const enabled = await api({ action: "enable" });
  if (!enabled.ok || !enabled.body.lan_available) throw new Error("Desktop device listener did not start on the local network.");
  const invite = await api({ action: "invite", name: "Background acceptance desktop" });
  if (!invite.ok) throw new Error("Desktop did not create an invitation.");
  fs.writeFileSync("/tmp/raticode-test-invitation.txt", invite.body.uri);
  execFileSync("adb", ["push", "/tmp/raticode-test-invitation.txt", "/data/local/tmp/raticode-test-invitation.txt"]);
  step("invitation created");
  const instrumentation = start(path.join(mobile, "gradlew"), ["--console=plain", "connectedDebugAndroidTest",
    "-Pandroid.testInstrumentationRunnerArguments.class=dev.raticode.mobile.LiveBackgroundDesktopTest",
    "-Pandroid.testInstrumentationRunnerArguments.backgroundInvitationFile=/data/local/tmp/raticode-test-invitation.txt",
    `-Pandroid.testInstrumentationRunnerArguments.hiddenWaitSeconds=${hiddenSeconds + 300}`], { cwd: mobile });
  const finished = new Promise(resolve => { instrumentation.on("exit", code => resolve(code)); instrumentation.on("error", () => resolve(-1)); });
  await until("phone pairing request", async () => {
    const pending = (await api()).body.peers.find(p => p.state === "pending");
    if (!pending) return false;
    return (await api({ action: "confirm", device_id: pending.device_id, fingerprint: pending.fingerprint })).ok;
  }, 300000);
  step("phone confirmed");
  await until("shared thread", async () => { const s = (await api()).body; return s.workspace_peers.length && s.grants.length; }, 180000);
  await sleep(15000); // Let the phone load the thread's history before hiding.
  await main(`${studio}.close()`);
  await sleep(2000);
  const hidden = await main(`(() => { const w = ${studio}; return Boolean(w && !w.isDestroyed() && !w.isVisible()); })()`);
  if (!hidden) throw new Error("Closing the desktop window did not hide it to the tray.");
  step("desktop window closed to the tray", { hidden });
  await sleep(hiddenSeconds * 1000);
  await evaluate(addHiddenMessage);
  const added = Date.now();
  step("message added on the hidden desktop");
  await until("phone deletion finished by the hidden desktop", async () =>
    !(await evaluate(`localStorage.getItem("gofer-flow-chat-thread-meta:${thread}")`)) && !(await api()).body.grants.length, (hiddenSeconds + 600) * 1000);
  step("hidden desktop archived and removed the phone-deleted thread", { seconds_after_message: Math.round((Date.now() - added) / 1000) });
  // A page-initiated close destroys the window; Raticode must restore a hidden one.
  await evaluate("window.close(), true");
  await sleep(5000);
  await until("restored hidden window", async () => (await connect()) && (await api())?.ok, 60000);
  const restored = await main(`(() => { const w = ${studio}; return Boolean(w && !w.isVisible()); })()`);
  if (!restored) throw new Error("A destroyed desktop window was not restored in the background.");
  step("destroyed window restored hidden and syncing");
  const code = await finished;
  report.phone_test_exit = code;
  if (code !== 0) throw new Error("Phone instrumentation failed; see the Gradle log.");
  step("phone test passed");
} catch (error) {
  failed = true;
  report.error = error.message;
  console.error(error.message);
} finally {
  for (const child of children.reverse()) { try { process.kill(-child.pid, "SIGTERM"); } catch { /* Already stopped. */ } }
  // Electron can outlive its launcher briefly. Stop only processes using this run's profile.
  for (let attempt = 0; attempt < 15; attempt += 1) {
    const stragglers = execFileSync("ps", ["-eo", "pid=,args="]).toString().split("\n")
      .filter(line => line.includes(`${temp}/profile`)).map(line => Number(line.trim().split(/\s+/)[0]))
      .filter(pid => pid && pid !== process.pid);
    if (!stragglers.length) break;
    for (const pid of stragglers) { try { process.kill(pid, attempt < 10 ? "SIGTERM" : "SIGKILL"); } catch { /* Gone. */ } }
    await sleep(1000);
  }
  // Remove only this run's protected device identity from the OS keyring.
  try {
    execFileSync("uv", ["run", "python", "-c",
      "import hashlib,keyring,pathlib,sys;from keyring.backends.SecretService import Keyring;" +
      "k=Keyring();a=hashlib.sha256(str(pathlib.Path(sys.argv[1]).resolve()).encode()).hexdigest();" +
      "k.get_password('Raticode devices v2',a) and k.delete_password('Raticode devices v2',a)", path.join(temp, "data", "devices")],
      { cwd: desktop, stdio: "inherit" });
    report.keyring_cleaned = true;
  } catch { report.keyring_cleaned = false; }
  report.logs = temp;
  fs.writeFileSync(path.join(temp, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  process.exit(failed ? 1 : 0);
}
