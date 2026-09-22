/* global __dirname, console, setTimeout, clearTimeout */
// Isolated component interaction test. No real keys, relay, providers or work.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-pair-ui-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Pairing UI test timed out"); app.exit(1); }, 60000);
let vite;
let win;
const actions = [];
const pin = "01".repeat(32);
const peer = { device_id: "11111111-1111-4111-8111-111111111111", fingerprint: pin, role: "controller", state: "pending", last_seen: null, capabilities: [] };
const state = { port: 44233, lan_host: "192.168.1.203", enabled: true, network_release: false, notice: "Independent security review pending. Local test peers only.", fingerprint: "02".repeat(32), peers: [peer], outbound: "idle" };

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "isolated-pairing-ui-test",
    resolveId(id) { if (id === "/pairing-entry.jsx") return id; },
    load(id) { if (id === "/pairing-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import PairedDevices from "/src/components/PairedDevices.jsx"; import "/src/styles/index.css"; createRoot(document.getElementById("root")).render(React.createElement(PairedDevices));`; },
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url === "/pairing-test") {
          response.setHeader("Content-Type", "text/html");
          response.end(await server.transformIndexHtml("/pairing-test", `<html><body><div id="root" style="overflow:auto;padding:24px"></div><script type="module" src="/pairing-entry.jsx"></script></body></html>`));
          return;
        }
        if (request.url !== "/api/devices") return next();
        response.setHeader("Content-Type", "application/json");
        response.setHeader("Cache-Control", "no-store");
        if (request.method === "GET") return response.end(JSON.stringify(state));
        let raw = "";
        for await (const chunk of request) raw += chunk;
        const body = JSON.parse(raw);
        actions.push(body);
        if (body.action === "confirm") { assert.equal(body.fingerprint, pin); peer.state = "confirmed"; state.workspace_peers = [peer.device_id]; }
        if (body.action === "revoke") peer.state = "revoked";
        if (["unpair", "remove_revoked"].includes(body.action)) state.peers = state.peers.filter(p => p.device_id !== body.device_id);
        if (body.action === "share_workspace") state.workspace_peers = body.enabled ? [peer.device_id] : [];
        if (body.action === "invite") return response.end(JSON.stringify({ uri: "raticode://pair?v=2&data=TEST_ONLY", expires_at: Math.floor(Date.now() / 1000) + 300, qr: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" }));
        if (body.action === "preview") return response.end(JSON.stringify({ name: "Disposable desktop", fingerprint: pin, expires_at: Math.floor(Date.now() / 1000) + 300 }));
        if (body.action === "pair") state.outbound = "awaiting_peer_confirmation";
        if (body.action === "authorize_thread") state.grants = [{ device_id: body.device_id, thread_id: body.thread_id, ...body.context }];
        if (body.action === "revoke_thread") state.grants = [];
        if (body.action === "send") return response.end(JSON.stringify({ state: "queued", request_id: body.request_id }));
        response.end(JSON.stringify(state));
      });
    },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 600, height: 700, show: true, webPreferences: { partition: "pair-test", nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", (details) => { if (details.level === "error") errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/pairing-test`);
  async function evaluate(source) {
    try { return await win.webContents.executeJavaScript(source, true); }
    catch (error) { throw new Error(`Browser evaluation failed: ${source}`, {cause: error}); }
  }
  async function wait(source) {
    const deadline = Date.now() + 5000;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(resolve => setTimeout(resolve, 25)); }
  }
  async function click(text) {
    await wait(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)})?.disabled === false`);
    assert.equal(await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)}).checkVisibility()`), true, `Hidden button: ${text}`);
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)}).click()`);
  }
  async function expand(text) {
    await wait(`Array.from(document.querySelectorAll('summary')).some(s => s.textContent === ${JSON.stringify(text)})`);
    await evaluate(`(() => { const summary = Array.from(document.querySelectorAll('summary')).find(s => s.textContent === ${JSON.stringify(text)}); if (!summary.parentElement.open) summary.click(); })()`);
  }
  async function screenshot(name) {
    await new Promise(resolve => setTimeout(resolve, 200));
    const directory = path.join(__dirname, "..", "artifacts", "paired-devices");
    fs.mkdirSync(directory, {recursive: true});
    fs.writeFileSync(path.join(directory, name + ".png"), (await win.webContents.capturePage()).toPNG());
  }
  await wait("document.body.textContent.includes('Identity matches, confirm pairing')");
  assert.equal(await evaluate("Array.from(document.querySelectorAll('input')).length"), 0);
  await click("Pair a device");
  // Controlled draft must tolerate focus -> clear -> type -> blur.
  await evaluate(`(() => { const input = document.querySelector('input'); input.focus(); Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, ''); input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  assert.equal(await evaluate("document.querySelector('input').value"), "");
  await evaluate(`(() => { const input = document.querySelector('input'); Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, 'Test desktop'); input.dispatchEvent(new Event('input', {bubbles:true})); input.blur(); })()`);
  assert.equal(await evaluate("document.querySelector('input').value"), "Test desktop");
  await wait("document.body.textContent.includes('Phone connections are disabled')");
  assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Create QR code').disabled"), true);
  assert.equal(actions.filter(a => a.action === "invite").length, 0);
  state.experimental_network = true;
  state.lan_available = false;
  state.relay_enabled = true;
  await wait("document.body.textContent.includes('Direct LAN is unavailable')");
  state.relay_error = "relay_rate_limited";
  await wait("document.body.textContent.includes('The relay is rate limited')");
  state.relay_error = null;
  state.lan_available = true;
  await wait("document.body.textContent.includes('Local network available')");
  await click("Create QR code");
  await wait("Boolean(document.querySelector('img'))");
  assert.equal(actions.at(-1).name, "Test desktop");
  assert.equal(await evaluate("localStorage.length"), 0);
  await click("Cancel invitation");
  await wait("!document.querySelector('img')");
  await click("Identity matches, confirm pairing");
  await wait("document.body.textContent.includes('Confirmed, waiting for acknowledgment')");
  await click("Revoke");
  assert.equal(actions.filter(a => a.action === "revoke").length, 0);
  await click("Keep device");
  await wait("!document.querySelector('[aria-label=\"Confirm revocation\"]')");
  await click("Revoke");
  await click("Revoke device");
  await wait("document.body.textContent.includes('revoked')");
  assert.equal(actions.filter(a => a.action === "revoke").length, 1);
  await expand("Revoked devices (1)");
  await click("Remove from revoked list");
  await wait("!document.querySelector('[aria-label=\"Revoked devices\"]')");
  assert.equal(actions.at(-1).action, "remove_revoked");
  assert.equal(state.peers.length, 0);
  await expand("Pair another desktop");
  await evaluate(`(() => { const input = document.querySelector('textarea'); input.focus(); Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'test invitation'); input.dispatchEvent(new Event('input', {bubbles:true})); input.blur(); })()`);
  await click("Review identity");
  await wait("document.body.textContent.includes('Disposable desktop')");
  assert.equal(actions.filter(a => a.action === "pair").length, 0);
  await click("Identity matches, request pairing");
  await wait("document.body.textContent.includes('Waiting for confirmation')");
  assert.equal(await evaluate("document.querySelector('textarea').value"), "");
  peer.state = "active";
  peer.role = "desktop";
  state.peers = [peer];
  await evaluate(`window.goferDesktop = { workspace: { selectPath: async () => '/approved/project', pathGrantForApi: () => 'test-folder-grant' } }; true`);
  await wait("document.body.textContent.includes('Desktop work')");
  await expand("Desktop work");
  await click("Choose allowed project");
  await wait("document.body.textContent.includes('/approved/project')");
  await click("Grant this thread access");
  await wait("document.body.textContent.includes('Revoke thread access')");
  const grant = actions.find(a => a.action === "authorize_thread");
  assert.equal(grant.context.permission_mode, "read-only");
  assert.equal(grant.context.project_path, "/approved/project");
  assert.equal(grant.grantId, "test-folder-grant");
  assert.equal(grant.context.fleet_execute, false);
  await expand("Delegation to other desktops");
  assert.equal(await evaluate("document.querySelector('input[type=checkbox]').checked"), false);
  await wait("document.querySelector('input[type=checkbox]')?.disabled === false");
  await evaluate("document.querySelector('input[type=checkbox]').click()");
  await click("Grant this thread access");
  await wait("document.body.textContent.includes('Fleet delegation: allowed')");
  assert.equal(actions.filter(a => a.action === 'authorize_thread').at(-1).context.fleet_execute, true);
  await expand("Shared conversations (1)");
  await click("Revoke thread access");
  await wait("!Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Revoke thread access')");
  assert.equal(actions.at(-1).action, 'revoke_thread');
  await wait("document.querySelector('input[type=checkbox]')?.disabled === false");
  await evaluate("document.querySelector('input[type=checkbox]').click()");
  await click("Grant this thread access");
  await wait("document.body.textContent.includes('Fleet delegation: disabled')");
  await evaluate(`(() => { const input = Array.from(document.querySelectorAll('label')).find(l => l.firstChild.textContent === 'Message').querySelector('textarea'); input.focus(); Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'Harmless test request'); input.dispatchEvent(new Event('input', {bubbles:true})); input.blur(); })()`);
  await click("Queue work");
  await wait("document.querySelector('[aria-label=\"Fleet result\"]')?.textContent.includes('queued')");
  const firstRequest = actions.find(a => a.action === "send");
  await click("Queue work");
  await wait("!Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Queue work').disabled");
  assert.equal(actions.filter(a => a.action === "send").at(-1).request_id, firstRequest.request_id);
  assert.equal(firstRequest.kind, "job.submit");
  await click("New request");
  assert.equal(await evaluate("Array.from(document.querySelectorAll('label')).find(l => l.firstChild.textContent === 'Message').querySelector('textarea').value"), "");
  // A newly paired phone inherits workspace sync and has no project grant controls.
  peer.role = "controller";
  state.rem_ready = true;
  state.workspace_peers = [peer.device_id];
  state.grants[0].allow_thread_create = true;
  await wait("document.body.textContent.includes('Rem ready')");
  await wait("document.body.textContent.includes('1 thread shared. New conversations allowed.')");
  assert.equal(await evaluate("document.querySelector('input[type=checkbox]').checked"), true);
  assert.equal(await evaluate("document.body.textContent.includes('Project access')"), false);
  assert.equal(await evaluate("document.body.textContent.includes('Device details')"), false);
  assert.equal(await evaluate("document.body.textContent.includes('Choose allowed project')"), false);
  assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Unpair').checkVisibility()"), true);
  await evaluate("document.querySelector('input[type=checkbox]').click()");
  await wait("document.body.textContent.includes('Thread sync is off.')");
  assert.equal(actions.at(-1).enabled, false);
  await evaluate("document.querySelector('input[type=checkbox]').click()");
  await wait("document.body.textContent.includes('1 thread shared. New conversations allowed.')");
  assert.equal(actions.at(-1).enabled, true);
  await click("Close setup");
  await click("Dismiss result");
  await evaluate("document.querySelectorAll('details').forEach(d => d.open = false)");
  assert.equal(await evaluate("Array.from(document.querySelectorAll('input')).filter(i => i.checkVisibility()).length"), 1);
  assert.equal(await evaluate("Array.from(document.querySelectorAll('pre')).filter(i => i.checkVisibility()).length"), 0);
  await screenshot("phone-light");
  await evaluate("document.documentElement.classList.add('dark')");
  await screenshot("phone-dark");
  await expand("Connection and troubleshooting");
  assert.equal(await evaluate("document.body.textContent.includes('192.168.1.203:44233')"), true);
  win.setSize(380, 700);
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(await evaluate("document.getElementById('root').scrollWidth <= document.getElementById('root').clientWidth"), true);
  await screenshot("phone-narrow");
  await click("Unpair");
  assert.equal(actions.filter(a => a.action === "unpair").length, 0);
  await click("Keep paired");
  await wait("!document.querySelector('[aria-label=\"Confirm unpairing\"]')");
  await click("Unpair");
  await click("Unpair device");
  await wait("document.body.textContent.includes('No devices paired.')");
  assert.equal(actions.at(-1).action, "unpair");
  assert.equal(state.peers.length, 0);
  assert.equal(await evaluate("Boolean(document.querySelector('[aria-label=\"Revoked devices\"]'))"), false);
  assert.deepEqual(errors, []);
  console.log("Pairing UI: draft editing, QR/identity consent, fleet opt-in and revocation, stable request retry, and no local secret persistence passed.");
  clearTimeout(timer);
  win.destroy();
  await vite.close();
  fs.rmSync(profile, { recursive: true, force: true });
  app.quit();
}
main().catch(async error => { console.error(error); if (vite) await vite.close(); app.exit(1); });
