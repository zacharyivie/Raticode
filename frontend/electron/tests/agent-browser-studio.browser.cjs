// Drive Rem's browser tools through the actual desktop entry point and studio:
// tabs join the tab strip in the background and the user's focus stays put.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app } = require("electron");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-agent-browser-test-"));
app.setPath("userData", profile);
fs.mkdirSync(path.join(profile, "logs"));
app.setPath("logs", path.join(profile, "logs"));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
process.env.GOFER_ELECTRON_MODE = "production";
process.env.GOFER_DATA_DIR = path.join(profile, "data");
process.env.GOFER_ELECTRON_TEST_HOOKS = "1";
const timer = setTimeout(() => { console.error("Agent browser studio test timed out"); app.exit(1); }, 60000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
const site = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end("<title>Agent page</title><label>Query <input id=q></label><button onclick=\"document.title='done:'+q.value\">Run</button>");
});

async function waitFor(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function run() {
  await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  process.env.GOFER_API_BASE_URL = `http://127.0.0.1:${api.address().port}`;
  const created = new Promise((resolve) => app.once("browser-window-created", (_event, window) => resolve(window)));
  await import("../main.js");
  const window = await created;
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  const page = (code) => window.webContents.executeJavaScript(code);

  // Earlier versions published a master token here; it must not exist.
  assert.equal(fs.existsSync(path.join(process.env.GOFER_DATA_DIR, "browser-control.json")), false);
  const service = await waitFor(() => globalThis.__raticodeTestHooks?.agentBrowserService, "Browser service did not start");
  const admin = async (body) => {
    const response = await fetch(`${service.url}/admin`, {
      method: "POST",
      headers: { Authorization: `Bearer ${service.adminToken}` },
      body: JSON.stringify(body),
    });
    return response.json();
  };
  const capabilities = {};
  for (const owner of ["agent-thread-one-0001", "agent-thread-two-0002"]) {
    capabilities[owner] = (await admin({ action: "grant", owner, ttlSeconds: 600 })).capability;
  }
  const call = async (agent, action, extra = {}) => {
    const response = await fetch(service.url, {
      method: "POST",
      headers: { Authorization: `Bearer ${capabilities[agent]}` },
      // The body's agent is ignored; the capability decides.
      body: JSON.stringify({ agent: "agent-thread-two-0002", action, ...extra }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    return body;
  };

  // Simulate the user typing somewhere in the studio.
  window.focus();
  await waitFor(() => page("document.querySelector('#root')?.children.length > 0"), "Studio did not render");
  await page(`(() => {
    const field = document.createElement("input");
    field.id = "user-field";
    document.body.appendChild(field);
    field.focus();
  })()`);

  const url = `http://127.0.0.1:${site.address().port}/`;
  const [first, second] = await Promise.all([
    call("agent-thread-one-0001", "open", { url }),
    call("agent-thread-two-0002", "open", { url }),
  ]);
  assert.equal(first.title, "Agent page");
  assert.equal(second.title, "Agent page");
  const tabs = await waitFor(async () => {
    const labels = await page(`[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent)`);
    return labels.filter((label) => label.includes("Agent page")).length === 2 && labels;
  }, "Agent tabs did not appear in the tab strip");
  assert.ok(tabs.length >= 2);
  assert.equal(await page("document.querySelectorAll('[title$=\"opened this tab and can control it\"]').length"), 2);

  const { snapshot } = await call("agent-thread-one-0001", "snapshot");
  const ref = (pattern) => snapshot.match(pattern)[1];
  await call("agent-thread-one-0001", "type", { ref: ref(/- textbox "Query" \[ref=(e\d+)\]/), text: "hello" });
  const clicked = await call("agent-thread-one-0001", "click", { ref: ref(/- button "Run" \[ref=(e\d+)\]/) });
  assert.equal(clicked.title, "done:hello");
  assert.equal((await call("agent-thread-two-0002", "tabs")).tabs[0].title, "Agent page");
  assert.equal(await page("document.activeElement.id"), "user-field");
  // Agent tabs depend on live desktop sessions, so they are not restored.
  const saved = JSON.parse(await page("localStorage.getItem('raticode.editorSession.v2')"));
  assert.ok(saved, "Editor session was not saved");
  assert.equal(Object.values(saved.browserTabs || {}).some((tab) => tab.agentSessionId), false);
  assert.equal(saved.paths.some((item) => item.startsWith("raticode-browser:")), false);

  // Closing from Rem removes the studio tab; closing in the studio ends Rem's access.
  await call("agent-thread-one-0001", "close");
  await waitFor(async () => (await page(`[...document.querySelectorAll('[role=tab]')].filter((tab) => tab.textContent.includes("Agent page")).length`)) === 1, "Agent close did not remove the tab");
  await page(`[...document.querySelectorAll('[role=tab]')].find((tab) => tab.textContent.includes("Agent page")).parentElement.querySelector('[aria-label^="Close"]').click()`);
  await waitFor(async () => {
    try { await call("agent-thread-two-0002", "snapshot"); return false; } catch (error) { return /browser_open first/.test(error.message); }
  }, "Closing the studio tab did not end the agent session");

  clearTimeout(timer);
  console.log("Agent browser tabs in the studio passed.");
  api.close();
  site.close();
  app.exit(0);
}

run().catch((error) => { console.error(error); app.exit(1); });
