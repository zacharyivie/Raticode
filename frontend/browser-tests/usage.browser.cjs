/* global __dirname, console, setTimeout, clearTimeout */
// Real settings UI against deterministic usage responses. No provider calls.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { URL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-usage-ui-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Usage UI test timed out"); app.exit(1); }, 60000);
let vite;
let win;
let failRefresh = false;
let failSave = false;
let storageAvailable = true;
let pendingRefreshes = 0;
const credentialRequests = [];
const alertRequests = [];
let alertSettings = { enabled: true, thresholds: [80, 95], defaults: { enabled: true, thresholds: [80, 95] } };
const configuredCredentials = new Map();
function credentialConfig(provider, profile) {
  const config = configuredCredentials.get(`${provider}:${profile || ""}`);
  return {
    provider, profile, storage_available: storageAvailable,
    storage_error: storageAvailable ? undefined : "Unlock your operating system credential store to save reporting settings.",
    description: provider === "claude_code" ? "Claude Code plan usage needs a Claude OAuth token. An Anthropic API key reports separate API usage." : "Provide reporting credentials for this account.",
    help_url: "https://example.com/usage-reporting",
    fields: [
      { name: provider === "claude_code" ? "oauth_token" : "admin_api_key", label: provider === "claude_code" ? "Claude OAuth token" : "Admin API key", secret: true, configured: config?.secret ?? true, help: "Leave the field blank to keep the saved credential." },
      ...(provider === "cursor" ? [{ name: "email", label: "Account email", secret: false, configured: true, value: config?.email ?? "person@example.com" }] : []),
      ...(provider === "openai_api" ? [{ name: "project_id", label: "Project ID", secret: false, configured: !!config?.project_id, value: config?.project_id || "" }] : []),
    ],
  };
}
const requests = [];
const authRequests = [];
const stamp = "2026-09-24T16:00:00Z";
const overview = {
  scope: "device", tracking_started_at: stamp, observed_at: stamp,
  accounts: [
    { id: "codex", provider: "codex", label: "ChatGPT account", status: "ready", source: "Codex account/rateLimits/read", observed_at: stamp, windows: [
      { id: "primary", label: "5-hour window", unit: "percent", used: 25, limit: 100, remaining: 75, remaining_percent: 75, resets_at: stamp },
      { id: "secondary", label: "Weekly window", unit: "percent", used: 90, limit: 100, remaining: 10, remaining_percent: 10, resets_at: stamp },
    ] },
    { id: "claude", provider: "claude_code", profile: "personal", label: "Claude account", status: "stale", reason: "The last allowance event is out of date.", source: "Claude usage event", observed_at: stamp, windows: [{ id: "plan", label: "Plan allowance", unit: "percent", remaining: 35, remaining_percent: 35 }] },
    { id: "cursor", provider: "cursor", status: "unavailable", reason: "Team reporting requires additional access.", dashboard_url: "https://cursor.com/dashboard", windows: [] },
    { id: "copilot", provider: "copilot", status: "auth_required", reason: "Sign in to GitHub Copilot to check usage.", windows: [] },
    { id: "grok", provider: "grok", status: "error", reason: "The provider did not respond.", windows: [] },
    { id: "openai-work", provider: "openai_api", profile: "work", label: "Work API", status: "ready", windows: [{ id: "requests", label: "Requests per minute", unit: "requests", used: 15, limit: 60, remaining: 45, resets_at: stamp }] },
  ],
  activity: { calls: 3, reported_calls: 2, unknown_calls: 1, partial_calls: 1, estimated_calls: 1, estimated_total_tokens: 30, input_tokens: 1000, output_tokens: 100, total_tokens: 1100, providers: [
    { provider: "codex", calls: 2, reported_calls: 2, unknown_calls: 0, input_tokens: 1000, output_tokens: 100, total_tokens: 1100 },
    { provider: "cursor", calls: 1, reported_calls: 0, unknown_calls: 1, input_tokens: null, output_tokens: null, total_tokens: null },
  ] },
  range: { key: "3mo", label: "Last 3 months", days: 90 },
  api_cost: {
    method: "Estimate at public API list prices.", prices_checked: "2026-11-17",
    total: { cost_usd: 12.5, lower_bound: true, calls: 3, priced_calls: 2, unpriced_calls: 0, no_token_calls: 1, estimated_calls: 1, partial_calls: 1, unsplit_calls: 1,
      breakdown: { input: 2, cache_read: 0.5, cache_write: 4, output: 6 } },
    providers: [
      { provider: "codex", profile: null, cost_usd: 12.5, lower_bound: false, calls: 2, unsplit_calls: 1, unpriced_calls: 0, unpriced_models: [], breakdown: { input: 2, cache_read: 0.5, cache_write: 4, output: 6 } },
      { provider: "cursor", profile: null, cost_usd: null, lower_bound: false, calls: 1, unsplit_calls: 0, unpriced_calls: 1, unpriced_models: ["auto"], breakdown: null },
    ],
    prices: [{ vendor: "openai", model: "gpt-6.1-terra", aliases: [], input: 1, output: 5, cache_read: 0.05, cache_write: 1.25, cache_write_long: 2.5, source: "https://developers.openai.com/api/docs/pricing", checked: "2026-11-17", note: "" }],
  },
  series: { bucket: "week", timezone: "UTC", providers: [
    { provider: "claude_code", total_tokens: 900, calls: 3, style: { dark: "#D97757", light: "#C6613F", pattern: "solid" } },
    { provider: "codex", total_tokens: 300, calls: 2, style: { dark: "#10A37F", light: "#0D8A6B", pattern: "stripes" } },
    { provider: "cursor", total_tokens: null, calls: 1, style: { dark: "#F7F7F4", light: "#26251E", pattern: "dots" } },
  ], accounts: [
    { key: "claude_code:work", provider: "claude_code", profile: "work", name: "work", total_tokens: 700, calls: 2, style: { dark: "#F0C9BC", light: "#4F2719", pattern: "dots" } },
    { key: "claude_code:", provider: "claude_code", profile: null, name: "Default sign-in", total_tokens: 200, calls: 1, style: { dark: "#D97757", light: "#C6613F", pattern: "solid" } },
    { key: "codex:", provider: "codex", profile: null, name: "Default account", total_tokens: 300, calls: 2, style: { dark: "#10A37F", light: "#0D8A6B", pattern: "stripes" } },
    { key: "cursor:", provider: "cursor", profile: null, name: "Default account", total_tokens: null, calls: 1, style: { dark: "#F7F7F4", light: "#26251E", pattern: "dots" } },
  ], buckets: [
    { key: "2026-08-31", start: "2026-08-31T00:00:00+00:00", end: "2026-09-07T00:00:00+00:00", calls: 2, unknown_calls: 0, total_tokens: 400, providers: { claude_code: 300, codex: 100 }, accounts: { "claude_code:work": 100, "claude_code:": 200, "codex:": 100 } },
    { key: "2026-09-07", start: "2026-09-07T00:00:00+00:00", end: "2026-09-14T00:00:00+00:00", calls: 0, unknown_calls: 0, total_tokens: 0, providers: {}, accounts: {} },
    { key: "2026-09-14", start: "2026-09-14T00:00:00+00:00", end: "2026-09-21T00:00:00+00:00", calls: 2, unknown_calls: 1, total_tokens: 200, providers: { codex: 200 }, accounts: { "codex:": 200 } },
    { key: "2026-09-21", start: "2026-09-21T00:00:00+00:00", end: "2026-09-28T00:00:00+00:00", calls: 2, unknown_calls: 0, total_tokens: 600, providers: { claude_code: 600 }, accounts: { "claude_code:work": 600 } },
  ] },
};

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ cacheDir: testViteCacheDir(), root: path.join(__dirname, ".."), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "usage-ui-test",
    resolveId(id) { if (id === "/usage-entry.jsx") return id; },
    load(id) { if (id === "/usage-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import SettingsPopover from "/src/components/SettingsPopover.jsx"; import {DEFAULT_APP_SETTINGS} from "/src/lib/settings.js"; import "/src/styles/index.css"; createRoot(document.getElementById("root")).render(React.createElement(SettingsPopover, {open: true, initialCategory: "usage", settings: DEFAULT_APP_SETTINGS}));`; },
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url === "/usage-test") {
          response.setHeader("Content-Type", "text/html");
          return response.end(await server.transformIndexHtml("/usage-test", '<html><body><div id="root"></div><script type="module" src="/usage-entry.jsx"></script></body></html>'));
        }
        if (request.url.startsWith("/api/provider/auth")) {
          let body = "";
          for await (const chunk of request) body += chunk;
          authRequests.push({ method: request.method, url: request.url, body: body ? JSON.parse(body) : null });
          response.setHeader("Content-Type", "application/json");
          return response.end(JSON.stringify({ status: request.method === "POST" ? "pending" : "complete" }));
        }
        if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end('{"providers":[]}'); }
        if (!request.url.startsWith("/api/usage/")) return next();
        requests.push({ url: request.url, method: request.method });
        response.setHeader("Content-Type", "application/json");
        response.setHeader("Cache-Control", "no-store");
        if (request.url.startsWith("/api/usage/alerts")) {
          if (request.method === "GET") return response.end(JSON.stringify(alertSettings));
          let body = "";
          for await (const chunk of request) body += chunk;
          const data = JSON.parse(body);
          alertRequests.push(data);
          const next = { ...alertSettings, ...data };
          if (next.thresholds[0] >= next.thresholds[1]) { response.statusCode = 400; return response.end('{"error":"The second alert level must be higher than the first."}'); }
          alertSettings = next;
          return response.end(JSON.stringify(alertSettings));
        }
        if (request.url.startsWith("/api/usage/credentials")) {
          if (request.method === "GET") {
            const query = new URL(request.url, "http://localhost").searchParams;
            return response.end(JSON.stringify(credentialConfig(query.get("provider"), query.get("profile"))));
          }
          let body = "";
          for await (const chunk of request) body += chunk;
          const data = JSON.parse(body);
          credentialRequests.push(data);
          if (failSave) { response.statusCode = 503; return response.end('{"error":"Credential store is locked. Unlock it and retry."}'); }
          const key = `${data.provider}:${data.profile || ""}`;
          const state = { secret: true, ...configuredCredentials.get(key), ...data.values };
          const secretName = data.provider === "claude_code" ? "oauth_token" : "admin_api_key";
          if (data.values[secretName]) state.secret = true;
          if (data.remove.includes(secretName)) state.secret = false;
          delete state[secretName];
          configuredCredentials.set(key, state);
          return response.end(JSON.stringify(credentialConfig(data.provider, data.profile)));
        }
        if (request.method === "POST" && failRefresh) { response.statusCode = 503; return response.end('{"error":"Usage refresh unavailable"}'); }
        if (request.method === "POST" && pendingRefreshes > 0) { pendingRefreshes -= 1; return response.end(JSON.stringify({ ...overview, refreshing: true })); }
        response.end(JSON.stringify(overview));
      });
    },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 1000, height: 800, show: true, webPreferences: { partition: "usage-test", nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", details => { if (details.level === "error" && !details.message.includes("503") && !details.message.includes("status of 400")) errors.push(details.message); });
  const pageUrl = `http://127.0.0.1:${vite.httpServer.address().port}/usage-test`;
  await win.loadURL(pageUrl);
  // A period saved as 90 days, the old "Last 90 days", opens as 3mo.
  await win.webContents.executeJavaScript("localStorage.setItem('raticode.usage.tokenRange', '90')", true);
  await win.loadURL(pageUrl);
  async function evaluate(source) { return win.webContents.executeJavaScript(source, true); }
  async function wait(source) {
    const deadline = Date.now() + 5000;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(resolve => setTimeout(resolve, 25)); }
  }
  // Where to put the pointer to hover an element: its center, read once two reads in
  // a row agree and the element itself is what that point hits. Scrolling and late
  // layout can still move it after scrollIntoView returns.
  async function settledCenter(selector) {
    await evaluate("delete window.__usageSettledCenter");
    const read = `(() => {
      const target = ${selector};
      const r = target.getBoundingClientRect();
      const point = { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      const last = window.__usageSettledCenter;
      window.__usageSettledCenter = point;
      return last?.x === point.x && last?.y === point.y && document.elementFromPoint(point.x, point.y) === target;
    })()`;
    await wait(read);
    return evaluate("window.__usageSettledCenter");
  }
  async function click(text) {
    await wait(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)})?.disabled === false`);
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)}).click()`);
  }
  async function select(label, value) {
    await evaluate(`(() => { const select = document.querySelector('select[aria-label="${label}"]'); select.value = ${JSON.stringify(value)}; select.dispatchEvent(new Event('change', {bubbles: true})); })()`);
  }
  async function screenshot(name) {
    await new Promise(resolve => setTimeout(resolve, 150));
    const directory = path.join(__dirname, "..", "artifacts", "usage");
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  }
  async function accountAction(provider, action, profile = null) {
    await evaluate(`(() => {
      const row = [...document.querySelectorAll('.usage-account')].find(row => row.querySelector('.usage-identity strong').textContent === ${JSON.stringify(provider)} && (!${JSON.stringify(profile)} || row.querySelector('.usage-identity > span').textContent === ${JSON.stringify(profile)}));
      if (!row.open) row.querySelector('summary').click();
      if (${JSON.stringify(action)} === 'configure') row.querySelector('.usage-configure').click();
    })()`);
  }
  async function fieldValue(name, value) {
    await evaluate(`(() => {
      const input = document.querySelector('.usage-credential-form input[name=${JSON.stringify(name)}]');
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(value)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
  }
  await wait("document.querySelectorAll('.usage-account').length === 6");
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  assert.equal(await evaluate("document.querySelector('.usage-account').textContent.includes('75% remaining')"), true);
  assert.equal(await evaluate("document.querySelector('.usage-account[data-status=stale]').textContent.includes('Out of date')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('Sign in required')"), true);
  // A signed-out account shows its sign-in action without expanding the row.
  assert.equal(await evaluate("document.querySelector('.usage-attention').textContent.startsWith('1 account needs you to sign in')"), true);
  assert.equal(await evaluate("document.querySelectorAll('.usage-sign-in').length"), 1);
  assert.equal(await evaluate("document.querySelector('.usage-sign-in-primary').checkVisibility()"), true);
  assert.equal(await evaluate("document.querySelector('.usage-sign-in').textContent.includes('copilot login')"), true);
  await evaluate("document.querySelector('.usage-sign-in').scrollIntoView({ block: 'center' })");
  await screenshot("sign-in-required-desktop");
  const refreshesBeforeSignIn = requests.filter(request => request.url.startsWith("/api/usage/refresh")).length;
  await click("Sign in to GitHub Copilot");
  const deadline = Date.now() + 5000;
  while (requests.filter(request => request.url.startsWith("/api/usage/refresh")).length === refreshesBeforeSignIn && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
  assert.deepEqual(authRequests[0], { method: "POST", url: "/api/provider/auth", body: { provider: "copilot", action: "start" } });
  assert.equal(authRequests[1].url, "/api/provider/auth?provider=copilot");
  assert.ok(requests.filter(request => request.url.startsWith("/api/usage/refresh")).length > refreshesBeforeSignIn, "Completing sign-in refreshes usage");
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  assert.equal(await evaluate("document.body.textContent.includes('Could not refresh')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('45 requests remaining')"), true);
  await evaluate("document.querySelector('.usage-account summary').click()");
  assert.equal(await evaluate("document.querySelector('.usage-account .usage-account-details').checkVisibility()"), true);
  assert.equal(await evaluate("document.querySelector('.usage-account').querySelectorAll('.usage-window').length"), 2);
  await screenshot("allowances-desktop");
  // Limit alerts: on by default at 80 and 95; levels keep a draft until blur and a rejected save shows why.
  await wait("document.querySelector('.usage-alerts [role=switch]') !== null");
  assert.equal(await evaluate("document.querySelector('.usage-alerts [role=switch]').getAttribute('aria-checked')"), "true");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.usage-alert-level input')].map(input => input.value)"), ["80", "95"]);
  const levelInput = index => `document.querySelectorAll('.usage-alert-level input')[${index}]`;
  async function typeLevel(index, value) {
    await evaluate(`(() => { const input = ${levelInput(index)}; input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  }
  await typeLevel(0, "");
  assert.equal(await evaluate(`${levelInput(0)}.value`), "", "A cleared level stays empty while focused");
  await typeLevel(0, "7");
  await typeLevel(0, "75");
  assert.equal(alertRequests.length, 0, "Nothing is saved while typing");
  await evaluate(`${levelInput(0)}.blur()`);
  await wait("document.querySelectorAll('.usage-alert-level input')[0].value === '75' && !document.querySelector('.usage-alerts [role=switch]').disabled");
  assert.deepEqual(alertRequests.at(-1), { thresholds: [75, 95] });
  await typeLevel(0, "");
  await evaluate(`${levelInput(0)}.blur()`);
  assert.equal(await evaluate(`${levelInput(0)}.value`), "75", "A cleared level goes back to the saved value");
  assert.equal(alertRequests.length, 1);
  await typeLevel(1, "60");
  await evaluate(`${levelInput(1)}.blur()`);
  await wait("document.querySelector('.usage-alerts [role=alert]')?.textContent.includes('must be higher')");
  assert.deepEqual(alertSettings.thresholds, [75, 95]);
  await evaluate("document.querySelector('.usage-alerts [role=switch]').click()");
  await wait("document.querySelector('.usage-alerts [role=switch]').getAttribute('aria-checked') === 'false'");
  assert.deepEqual(alertRequests.at(-1), { enabled: false });
  assert.equal(await evaluate(`${levelInput(0)}.disabled`), true);
  assert.equal(await evaluate("document.querySelector('.usage-alerts [role=alert]')"), null);
  await evaluate("document.querySelector('.usage-alerts').scrollIntoView({ block: 'center' })");
  await screenshot("limit-alerts-desktop");
  await evaluate("document.querySelector('.usage-alerts [role=switch]').click()");
  await wait("document.querySelector('.usage-alerts [role=switch]').getAttribute('aria-checked') === 'true'");
  assert.equal(requests.some(request => request.url.includes("/credentials")), false, "Credentials are fetched only when configuration is opened");
  await accountAction("Claude Code", "configure");
  await wait("document.querySelector('input[name=oauth_token]') !== null");
  assert.equal(requests.at(-1).url, "/api/usage/credentials?provider=claude_code&profile=personal");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').type"), "password");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').value"), "");
  assert.equal(await evaluate("document.querySelector('.usage-credential-form').textContent.includes('An Anthropic API key reports separate API usage')"), true);
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1), { provider: "claude_code", profile: "personal", values: {}, remove: [] });
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  assert.equal(requests.at(-1).method, "POST");
  assert.ok(requests.at(-1).url.startsWith("/api/usage/refresh?range=3mo&tz="), requests.at(-1).url);
  await fieldValue("oauth_token", "temporary-draft");
  await fieldValue("oauth_token", "");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').value"), "");
  await fieldValue("oauth_token", "test-claude-reporting-token");
  await evaluate("document.querySelector('input[name=oauth_token]').blur()");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').value"), "test-claude-reporting-token");
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1).values, { oauth_token: "test-claude-reporting-token" });
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').value"), "");
  assert.equal(await evaluate("document.body.textContent.includes('test-claude-reporting-token')"), false);
  assert.equal(await evaluate("Object.values(localStorage).some(value => value.includes('test-claude-reporting-token'))"), false);
  await screenshot("claude-reporting-desktop");
  await evaluate("document.querySelector('.usage-remove-credential input').click()");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').disabled"), true);
  failSave = true;
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-form [role=alert]')?.textContent.includes('Credential store is locked')");
  assert.equal(await evaluate("document.querySelector('.usage-remove-credential input').checked"), true);
  assert.equal(configuredCredentials.get("claude_code:personal").secret, true);
  failSave = false;
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1), { provider: "claude_code", profile: "personal", values: {}, remove: ["oauth_token"] });
  assert.equal(await evaluate("document.querySelector('.usage-remove-credential') === null"), true);
  await click("Close reporting settings");
  await accountAction("Cursor", "configure");
  await wait("document.querySelector('input[name=email]') !== null");
  await fieldValue("email", "");
  assert.equal(await evaluate("document.querySelector('input[name=email]').value"), "");
  await fieldValue("email", "new-person@example.com");
  await evaluate("document.querySelector('input[name=email]').blur()");
  assert.equal(await evaluate("document.querySelector('input[name=email]').value"), "new-person@example.com");
  await fieldValue("admin_api_key", "test-cursor-reporting-key");
  failSave = true;
  const refreshesBeforeFailedSave = requests.filter(request => request.url.startsWith("/api/usage/refresh")).length;
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-form [role=alert]')?.textContent.includes('Credential store is locked')");
  assert.equal(await evaluate("document.querySelector('input[name=admin_api_key]').value"), "test-cursor-reporting-key");
  assert.equal(requests.filter(request => request.url.startsWith("/api/usage/refresh")).length, refreshesBeforeFailedSave);
  failSave = false;
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1), { provider: "cursor", profile: null, values: { admin_api_key: "test-cursor-reporting-key", email: "new-person@example.com" }, remove: [] });
  await click("Close reporting settings");
  storageAvailable = false;
  await accountAction("OpenAI API", "configure");
  await wait("document.querySelector('input[name=project_id]') !== null");
  assert.equal(requests.at(-1).url, "/api/usage/credentials?provider=openai_api&profile=work");
  assert.equal(await evaluate("document.querySelector('.usage-credential-form fieldset').disabled"), true);
  assert.equal(await evaluate("document.querySelector('.usage-credential-form [role=alert]').textContent.includes('Unlock your operating system')"), true);
  assert.equal(await evaluate("document.querySelector('input[name=admin_api_key]').value"), "");
  await click("Close reporting settings");
  storageAvailable = true;
  await accountAction("OpenAI API", "configure");
  await wait("document.querySelector('input[name=project_id]') !== null");
  await fieldValue("project_id", "project-work");
  await fieldValue("admin_api_key", "test-openai-work-key");
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1), { provider: "openai_api", profile: "work", values: { admin_api_key: "test-openai-work-key", project_id: "project-work" }, remove: [] });
  await click("Close reporting settings");
  overview.accounts.push({ id: "openai-personal", provider: "openai_api", profile: "personal", status: "configuration_required", windows: [] });
  pendingRefreshes = 1;
  await click("Refresh usage");
  await wait("document.querySelectorAll('.usage-account').length === 7");
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  await accountAction("OpenAI API", "configure", "personal");
  await wait("document.querySelector('input[name=project_id]') !== null");
  assert.equal(requests.at(-1).url, "/api/usage/credentials?provider=openai_api&profile=personal");
  assert.equal(await evaluate("document.querySelector('input[name=project_id]').value"), "");
  await fieldValue("project_id", "project-personal");
  await click("Save reporting settings");
  await wait("document.querySelector('.usage-credential-saved') !== null");
  assert.deepEqual(credentialRequests.at(-1), { provider: "openai_api", profile: "personal", values: { project_id: "project-personal" }, remove: [] });
  await click("Close reporting settings");
  await accountAction("OpenAI API", "configure", "work");
  await wait("document.querySelector('input[name=project_id]') !== null");
  assert.equal(await evaluate("document.querySelector('input[name=project_id]').value"), "project-work");
  await click("Close reporting settings");
  await select("Usage account type", "api");
  await wait("document.querySelectorAll('.usage-account').length === 2");
  assert.equal(await evaluate("document.querySelector('.usage-account').textContent.includes('OpenAI API')"), true);
  await select("Usage account type", "all");
  // The account cards and their weekly pace charts belong to Remaining allowance only.
  assert.equal(await evaluate("document.querySelectorAll('.usage-glance').length"), 1);
  assert.equal(await evaluate("document.querySelector('.usage-glance').checkVisibility()"), true);
  assert.equal(await evaluate("document.querySelector('[role=tabpanel]:not([hidden]) .usage-glance') !== null"), true);
  await click("Tokens in Raticode");
  await wait("document.querySelector('.usage-token-table').checkVisibility()");
  assert.equal(await evaluate("document.querySelector('[role=tabpanel]:not([hidden]) .usage-glance')"), null);
  assert.equal(await evaluate("[...document.querySelectorAll('.usage-glance')].some(card => card.checkVisibility())"), false);
  assert.equal(await evaluate("document.querySelector('.usage-total').textContent.includes('2 of 3 calls reported tokens')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('30 estimated tokens. Estimates are excluded')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('1 call reported only part')"), true);
  assert.equal(await evaluate("document.querySelectorAll('.usage-token-table tbody tr')[1].textContent.includes('Not reported')"), true);
  assert.ok(requests.some(request => request.method === "GET" && request.url.startsWith("/api/usage/overview?range=3mo&tz=")));
  assert.equal(await evaluate("localStorage.getItem('raticode.usage.tokenRange')"), JSON.stringify({ key: "3mo", start: "", end: "" }));
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Usage period\"] [role=radio][aria-checked=true]').textContent"), "3mo");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('[aria-label=\"Usage period\"] [role=radio]')].map(b => b.textContent)"),["24h", "7d", "30d", "3mo", "6mo", "1Y", "5Y", "All", "Custom"]);
  // Cost at API prices: an estimate per provider, unknown never shown as $0, and no plan-price form.
  await wait("document.querySelector('.usage-cost') !== null");
  assert.equal(await evaluate("document.querySelector('.usage-cost h3').textContent"), "Cost at API prices Estimate");
  assert.equal(await evaluate("document.querySelector('.usage-cost-card strong').textContent"), "at least $12.50");
  const costRows = await evaluate("[...document.querySelectorAll('.usage-cost-table tbody tr')].map(row => row.textContent)");
  assert.ok(costRows[0].includes("$2.00") && costRows[0].includes("$0.50") && costRows[0].includes("$4.00") && costRows[0].includes("$12.50"), costRows[0]);
  assert.ok(costRows[0].includes("1 call without a cache breakdown"), costRows[0]);
  assert.ok(costRows[1].includes("No list price: auto") && costRows[1].includes("Unknown") && !costRows[1].includes("$0.00"), costRows[1]);
  assert.equal(await evaluate("document.body.textContent.includes('priced as uncached input. That overstates a call that used the cache')"), true);
  assert.equal(await evaluate("document.querySelectorAll('.usage-cost-card').length"), 1);
  assert.equal(await evaluate("document.querySelector('.usage-cost').textContent.includes('Saved vs API')"), false);
  assert.equal(await evaluate("document.querySelector('.usage-cost').textContent.includes('What you pay each month')"), false);
  assert.equal(await evaluate("document.querySelector('.usage-cost input, .usage-cost form')"), null);
  assert.equal(await evaluate("[...document.querySelectorAll('.usage-cost button')].some(b => b.textContent.includes('Save prices'))"), false);
  assert.equal(await evaluate("document.querySelector('.usage-cost-details > summary').textContent"), "Price list (1 models, US$ per million tokens)");
  assert.equal(await evaluate("document.querySelector('.usage-price-table a').href"), "https://developers.openai.com/api/docs/pricing");
  await evaluate("document.querySelector('.usage-cost').scrollIntoView({ block: 'start' })");
  await new Promise(resolve => setTimeout(resolve, 150));
  await screenshot("api-cost-desktop");
  // The stacked chart: one column per week, a segment per provider sized by its tokens.
  await wait("document.querySelector('.usage-chart') !== null");
  assert.equal(await evaluate("document.querySelectorAll('.usage-chart-column').length"), 4);
  assert.equal(await evaluate("document.querySelectorAll('.usage-chart-column')[1].querySelectorAll('.usage-chart-segment').length"), 0);
  const heights = await evaluate("[...document.querySelectorAll('.usage-chart-column')[0].querySelectorAll('.usage-chart-segment')].map(s => s.getBoundingClientRect().height)");
  assert.equal(heights.length, 2);
  assert.ok(Math.abs(heights[0] / heights[1] - 3) < 0.25, `Claude Code should be 3x Codex: ${heights}`);
  const columns = await evaluate("[...document.querySelectorAll('.usage-chart-stack')].map(s => s.getBoundingClientRect().height)");
  assert.ok(Math.abs(columns[3] / columns[0] - 1.5) < 0.1, `600 vs 400 tokens: ${columns}`);
  assert.equal(await evaluate("new Set([...document.querySelectorAll('.usage-chart-legend .usage-chart-swatch')].map(s => s.dataset.pattern)).size"), 3);
  // Brand colors from the series: each theme reads its own shade, and Cursor's cream gets dark dots.
  const swatches = "[...document.querySelectorAll('.usage-chart-legend .usage-chart-swatch')].map(s => { const c = getComputedStyle(s); return [c.backgroundColor, c.getPropertyValue('--series-ink').trim()]; })";
  const themed = async dark => evaluate(`(() => { document.documentElement.classList.toggle('dark', ${dark}); return ${swatches}; })()`);
  const wasDark = await evaluate("document.documentElement.classList.contains('dark')");
  assert.deepEqual((await themed(true)).map(([color]) => color), ["rgb(217, 119, 87)", "rgb(16, 163, 127)", "rgb(247, 247, 244)"]);
  assert.equal((await themed(true))[2][1], "rgb(0 0 0 / .35)");
  assert.deepEqual((await themed(false)).map(([color]) => color), ["rgb(198, 97, 63)", "rgb(13, 138, 107)", "rgb(38, 37, 30)"]);
  assert.equal((await themed(false))[2][1], "rgb(255 255 255 / .45)");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.usage-chart-segment')).backgroundColor"), "rgb(198, 97, 63)");
  await themed(wasDark);
  assert.equal(await evaluate("document.querySelector('.usage-chart-legend').textContent.includes('Cursor') && document.querySelector('.usage-chart-legend').textContent.includes('Tokens not reported')"), true);
  assert.equal(await evaluate("document.querySelector('.usage-chart-legend').textContent.includes('75%')"), true);
  // Hovering a segment shows the week, its total and each provider's tokens and share.
  // The cost section above may have scrolled the chart away; bring it back first.
  await evaluate("document.querySelector('.usage-chart').scrollIntoView({ block: 'center' })");
  const point = await settledCenter("document.querySelectorAll('.usage-chart-column')[0].querySelector('.usage-chart-segment')");
  win.webContents.sendInputEvent({ type: "mouseMove", x: point.x, y: point.y });
  // The tooltip and its hovered row show in the same update.
  await wait("document.querySelector('.usage-chart-tooltip li[data-hovered]') !== null");
  assert.equal(await evaluate("document.querySelector('.usage-chart-tooltip li[data-hovered]').textContent.startsWith('Claude Code')"), true);
  const tip = await evaluate("document.querySelector('.usage-chart-tooltip').textContent");
  assert.match(tip, /Week of Aug 31, 2026/);
  assert.match(tip, /400 tokens/);
  assert.match(tip, /Claude Code300/);
  assert.match(tip, /75%/);
  assert.match(tip, /25%/);
  await screenshot("tokens-chart-tooltip");
  // Above the stack the pointer is on the bar but on none of its pieces, so the
  // tooltip stays and no row is marked.
  const above = await evaluate("(() => { const r = document.querySelectorAll('.usage-chart-column')[0].getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + 4) }; })()");
  assert.equal(await evaluate(`document.elementFromPoint(${above.x}, ${above.y}) === document.querySelectorAll('.usage-chart-column')[0]`), true);
  win.webContents.sendInputEvent({ type: "mouseMove", x: above.x, y: above.y });
  await wait("document.querySelector('.usage-chart-tooltip') !== null && document.querySelector('.usage-chart-tooltip li[data-hovered]') === null");
  assert.equal(await evaluate("document.querySelector('.usage-chart-column[data-active]') === document.querySelectorAll('.usage-chart-column')[0]"), true);
  win.webContents.sendInputEvent({ type: "mouseMove", x: 5, y: 5 });
  // Focus starts on the last bar only when the pointer has let go of the chart.
  await wait("document.querySelector('.usage-chart-tooltip') === null");
  // The keyboard reads each bar aloud through a live region.
  win.webContents.focus();
  await evaluate("document.querySelector('.usage-chart-bars').focus()");
  // A hidden test window may not own focus, so its focus events can be held back.
  await evaluate("document.querySelector('.usage-chart-bars').dispatchEvent(new FocusEvent('focusin', { bubbles: true }))");
  await wait("document.querySelector('.usage-chart [aria-live]').textContent.includes('600 tokens')");
  await evaluate("document.querySelector('.usage-chart-bars').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))");
  await wait("document.querySelector('.usage-chart [aria-live]').textContent.includes('1 call without token data')");
  await evaluate("document.querySelector('.usage-chart-bars').blur()");
  assert.equal(await evaluate("document.querySelector('.usage-chart-table summary').textContent"), "Show as a table");
  // By account: each Claude Code account gets its own shade and pattern, named in every view.
  assert.equal(await evaluate("document.querySelector('.usage-chart-split [aria-checked=true]').textContent"), "By provider");
  await click("By account");
  await wait("document.querySelector('.usage-chart-legend').getAttribute('aria-label') === 'Accounts'");
  assert.equal(await evaluate("localStorage.getItem('raticode.usage.chartSplit')"), "account");
  const legend = await evaluate("[...document.querySelectorAll('.usage-chart-legend strong')].map(s => s.textContent)");
  assert.deepEqual(legend, ["Claude Code · work", "Claude Code · Default sign-in", "Codex · Default account", "Cursor · Default account"]);
  const accountPatterns = await evaluate("[...document.querySelectorAll('.usage-chart-legend .usage-chart-swatch')].slice(0, 2).map(s => s.dataset.pattern)");
  assert.deepEqual(accountPatterns, ["dots", "solid"]);
  assert.equal(await evaluate("document.querySelectorAll('.usage-chart-column')[0].querySelectorAll('.usage-chart-segment').length"), 3);
  const accountFills = await evaluate("[...document.querySelectorAll('.usage-chart-legend .usage-chart-swatch')].slice(0, 2).map(s => getComputedStyle(s).backgroundColor)");
  assert.notEqual(accountFills[0], accountFills[1]);
  assert.match(await evaluate("document.querySelector('.usage-chart-bars').getAttribute('aria-label')"), /by account/);
  await evaluate("document.querySelector('.usage-chart-bars').dispatchEvent(new FocusEvent('focusin', { bubbles: true }))");
  await wait("document.querySelector('.usage-chart [aria-live]').textContent.includes('Claude Code · work 600 (100%)')");
  await evaluate("document.querySelector('.usage-chart-bars').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))");
  await wait("document.querySelector('.usage-chart [aria-live]').textContent.includes('Claude Code · Default sign-in 200 (50%)')");
  await evaluate("document.querySelector('.usage-chart-bars').blur()");
  await evaluate("document.querySelector('.usage-chart').scrollIntoView({ block: 'center' })");
  const accountPoint = await settledCenter("document.querySelectorAll('.usage-chart-column')[3].querySelector('.usage-chart-segment')");
  win.webContents.sendInputEvent({ type: "mouseMove", x: accountPoint.x, y: accountPoint.y });
  await wait("document.querySelector('.usage-chart-tooltip li[data-hovered]')?.textContent.startsWith('Claude Code · work')");
  await screenshot("tokens-chart-by-account");
  win.webContents.sendInputEvent({ type: "mouseMove", x: 5, y: 5 });
  await wait("document.querySelector('.usage-chart-tooltip') === null");
  await evaluate("document.querySelector('.usage-chart-table').open = true");
  const heads = await evaluate("[...document.querySelectorAll('.usage-bucket-table thead th')].map(th => th.textContent)");
  assert.deepEqual(heads, ["Week", "Claude Code · work", "Claude Code · Default sign-in", "Codex · Default account", "Cursor · Default account", "Total"]);
  assert.match(await evaluate("document.querySelector('.usage-bucket-table caption').textContent"), /by account/);
  await evaluate("document.querySelector('.usage-chart-table').open = false");
  // Arrow keys move between the two choices, like the period picker.
  await evaluate("document.querySelector('.usage-chart-split [aria-checked=true]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))");
  await wait("document.querySelector('.usage-chart-split [aria-checked=true]').textContent === 'By provider'");
  assert.equal(await evaluate("localStorage.getItem('raticode.usage.chartSplit')"), "provider");
  await click("By account");
  await click("7d");
  await wait("document.querySelector('.usage-token-table') !== null");
  // The split is kept when the chart reloads for another period.
  await wait("document.querySelector('.usage-chart-split [aria-checked=true]')?.textContent === 'By account'");
  await click("By provider");
  assert.ok(requests.some(request => request.method === "GET" && request.url.startsWith("/api/usage/overview?range=7d&tz=")));
  await click("Refresh usage");
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  assert.equal(requests.at(-1).method, "POST");
  assert.ok(requests.at(-1).url.startsWith("/api/usage/refresh?range=7d&tz="));
  // A custom range is checked before it is asked for.
  await click("Custom");
  await wait("document.querySelector('.usage-range-custom') !== null");
  const setDate = async (index, value) => evaluate(`(() => { const input = document.querySelectorAll('.usage-range-custom input')[${index}]; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await setDate(0, "2026-03-10");
  await setDate(1, "2026-03-01");
  const before = requests.length;
  await click("Show range");
  await wait("document.querySelector('.usage-range-error')?.textContent === 'The start date must be on or before the end date.'");
  assert.equal(requests.length, before);
  await setDate(1, "2026-04-10");
  await click("Show range");
  await wait("document.querySelector('.usage-range-error') === null");
  await wait("document.querySelector('.usage-token-table') !== null");
  assert.ok(requests.some(request => request.url.startsWith("/api/usage/overview?range=custom&start=2026-03-10&end=2026-04-10&tz=")));
  assert.equal(await evaluate("localStorage.getItem('raticode.usage.tokenRange')"), JSON.stringify({ key: "custom", start: "2026-03-10", end: "2026-04-10" }));
  assert.equal(await evaluate("document.querySelector('[role=tabpanel]:not([hidden]) .usage-filters').textContent.includes('Mar 10, 2026 to Apr 10, 2026')"), true);
  failRefresh = true;
  await click("Refresh usage");
  await wait("document.querySelector('[role=alert]')?.textContent.includes('Showing the last loaded results')");
  assert.equal(await evaluate("document.querySelector('.usage-token-table').checkVisibility()"), true);
  failRefresh = false;
  await click("Refresh usage");
  await wait("!document.querySelector('[role=alert]')");
  await screenshot("tokens-desktop");
  await evaluate("document.querySelector('[role=tab][aria-selected=true]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))");
  await wait("document.querySelector('[role=tab][aria-selected=true]').textContent === 'Remaining allowance'");
  assert.equal(await evaluate("document.activeElement.textContent"), "Remaining allowance");
  win.setSize(390, 760);
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(await evaluate("document.querySelector('[role=dialog]').getBoundingClientRect().width <= window.innerWidth"), true);
  assert.equal(await evaluate("document.querySelector('.usage-dashboard').scrollWidth <= document.querySelector('.usage-dashboard').clientWidth"), true);
  await screenshot("allowances-mobile");
  await accountAction("Claude Code", "configure");
  await wait("document.querySelector('input[name=oauth_token]') !== null");
  await evaluate("document.querySelector('.usage-credential-form').scrollIntoView({block: 'center'})");
  assert.equal(await evaluate("document.querySelector('.usage-dashboard').scrollWidth <= document.querySelector('.usage-dashboard').clientWidth"), true);
  await screenshot("claude-reporting-mobile");
  await fieldValue("oauth_token", "unsaved-local-draft");
  await click("Close reporting settings");
  await accountAction("Claude Code", "configure");
  await wait("document.querySelector('input[name=oauth_token]') !== null");
  assert.equal(await evaluate("document.querySelector('input[name=oauth_token]').value"), "");
  await click("Close reporting settings");
  await click("Tokens in Raticode");
  assert.equal(await evaluate("document.querySelector('.usage-dashboard').scrollWidth <= document.querySelector('.usage-dashboard').clientWidth"), true);
  await screenshot("tokens-mobile");
  await evaluate("document.documentElement.classList.add('dark')");
  await screenshot("tokens-mobile-dark");
  // The custom range survives reopening Settings.
  const reloads = requests.length;
  await win.loadURL(pageUrl);
  await wait(`document.querySelector('.usage-dashboard') !== null`);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.ok(requests.slice(reloads).some(request => request.url.startsWith("/api/usage/overview?range=custom&start=2026-03-10&end=2026-04-10")));
  assert.deepEqual(errors, []);
  console.log("Usage UI passed: settings integration, limit alert switch and levels, allowance and token views, stacked token chart with tooltips and keyboard reading, 3mo to All and custom periods with validation, saved period and 90-day migration, credential preserve/replace/remove, draft clear/type/blur, profile isolation, credential store errors, saved usage refresh, mobile and dark rendering.");
  clearTimeout(timer);
  win.destroy();
  await vite.close();
  fs.rmSync(profile, { recursive: true, force: true });
  app.quit();
}
main().catch(async error => { console.error(error); if (vite) await vite.close(); app.exit(1); });
