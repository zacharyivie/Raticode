/* global __dirname, console, process, setTimeout, clearTimeout */
// Settings > Devices with a paired phone, clicked with a real mouse. Turning on a
// phone switch must not scroll the Settings panel itself and leave it half empty.
// Isolated: mock device API, no real keys, phones or backend.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-settings-devices-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Settings devices layout test timed out"); app.exit(1); }, 90000);
const phone = "11111111-1111-4111-8111-111111111111";
const state = { enabled: true, experimental_network: true, lan_available: true, rem_ready: true, port: 44233, lan_host: "192.168.1.203",
  fingerprint: "02".repeat(32), outbound: "idle", grants: [], fleet: [], workspace_peers: [], remote_control_peers: [], desktop_control_peers: [],
  peers: [{ device_id: phone, name: "Phone", fingerprint: "01".repeat(32), role: "controller", state: "active", last_seen: 1793378953, capabilities: [] }] };
let vite;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), logLevel: "error", cacheDir: path.join(profile, "vite-cache"),
    server: { host: "127.0.0.1", port: 0 }, plugins: [{
      name: "settings-devices-layout-test",
      resolveId(id) { if (id === "/settings-entry.jsx") return id; },
      load(id) {
        if (id !== "/settings-entry.jsx") return;
        return `import React from "react"; import { createRoot } from "react-dom/client";
          import SettingsPopover from "/src/components/SettingsPopover.jsx";
          import { DEFAULT_APP_SETTINGS } from "/src/lib/settings.js"; import "/src/styles/index.css";
          document.documentElement.classList.add("dark");
          createRoot(document.getElementById("root")).render(React.createElement(SettingsPopover,
            { open: true, initialCategory: "devices", settings: DEFAULT_APP_SETTINGS, onChange() {}, onClose() {} }));`;
      },
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url === "/settings-test") {
            response.setHeader("Content-Type", "text/html");
            response.end(await server.transformIndexHtml("/settings-test", `<html><body class="bg-canvas"><div id="root"></div><script type="module" src="/settings-entry.jsx"></script></body></html>`));
            return;
          }
          if (!request.url.startsWith("/api/")) return next();
          response.setHeader("Content-Type", "application/json");
          if (request.url !== "/api/devices") return response.end("{}");
          if (request.method === "GET") return response.end(JSON.stringify(state));
          let raw = "";
          for await (const chunk of request) raw += chunk;
          const body = JSON.parse(raw);
          const list = { share_workspace: "workspace_peers", remote_control: "remote_control_peers", desktop_control: "desktop_control_peers" }[body.action];
          if (list) state[list] = body.enabled ? [body.device_id] : [];
          response.end(JSON.stringify(state));
        });
      },
    }] });
  await vite.listen();
  // A short window, like a laptop screen, so the phone switches start below the fold.
  const win = new BrowserWindow({ width: 900, height: 640, show: true, webPreferences: { partition: "settings-devices-layout", contextIsolation: true } });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/settings-test`);
  const evaluate = source => win.webContents.executeJavaScript(source, true);
  const wait = async (source, ms = 10000) => {
    const deadline = Date.now() + ms;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(r => setTimeout(r, 50)); }
  };
  const panel = `document.querySelector('[aria-label="Application settings"]')`;
  const scrolled = () => evaluate(`({ panel: ${panel}.scrollTop, panelLeft: ${panel}.scrollLeft, nav: document.querySelector('[aria-label="Settings categories"]').scrollTop })`);
  async function realClick(title) {
    // Bring the switch into view the way a user scrolls, then click it with the mouse.
    const box = await evaluate(`(() => { const label = [...document.querySelectorAll('label')].find(l => l.textContent.includes(${JSON.stringify(title)}));
      const scroller = label.closest('.overflow-y-auto'); const view = scroller.getBoundingClientRect();
      scroller.scrollTop += label.getBoundingClientRect().top - view.top - view.height / 2;
      const r = label.querySelector('[aria-hidden=true]').getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
    win.webContents.sendInputEvent({ type: "mouseDown", x: box.x, y: box.y, button: "left", clickCount: 1 });
    win.webContents.sendInputEvent({ type: "mouseUp", x: box.x, y: box.y, button: "left", clickCount: 1 });
  }
  await wait(`Boolean(document.querySelector('[aria-label="Device trust registry"] li'))`);
  await realClick("Sync desktop threads with this phone");
  await wait(`[...document.querySelectorAll('label')].find(l => l.textContent.includes("Sync desktop threads")).querySelector('input').checked`);
  const first = await scrolled();
  if (process.env.SETTINGS_LAYOUT_SCREENSHOT) fs.writeFileSync(process.env.SETTINGS_LAYOUT_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
  assert.deepEqual(first, { panel: 0, panelLeft: 0, nav: 0 }, "Turning on thread sync scrolled the Settings panel itself");
  await wait(`![...document.querySelectorAll('label')].find(l => l.textContent.includes("Use Raticode from this phone")).querySelector('input').disabled`);
  await realClick("Use Raticode from this phone");
  await wait(`[...document.querySelectorAll('label')].find(l => l.textContent.includes("Use Raticode from this phone")).querySelector('input').checked`);
  await new Promise(r => setTimeout(r, 300));
  const after = await scrolled();
  if (process.env.SETTINGS_LAYOUT_SCREENSHOT) fs.writeFileSync(process.env.SETTINGS_LAYOUT_SCREENSHOT.replace(".png", "-2.png"), (await win.webContents.capturePage()).toPNG());
  assert.deepEqual(after, { panel: 0, panelLeft: 0, nav: 0 }, "Giving the phone access scrolled the Settings panel itself");
  // The panel still shows its header and category list.
  assert.equal(await evaluate(`document.querySelector('[aria-label="Settings categories"]').getBoundingClientRect().top > ${panel}.getBoundingClientRect().top`), true);
  console.log("Settings devices layout: real clicks on phone switches keep the Settings panel in place.");
}

main().then(() => { clearTimeout(timer); return vite?.close(); }).then(() => app.exit(0)).catch(async (error) => {
  console.error(error);
  await vite?.close().catch(() => {});
  app.exit(1);
});
