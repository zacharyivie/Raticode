/* global __dirname, console, setTimeout, clearTimeout */
// Real settings UI against deterministic usage responses. No provider calls.
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
};

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "usage-ui-test",
    resolveId(id) { if (id === "/usage-entry.jsx") return id; },
    load(id) { if (id === "/usage-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import SettingsPopover from "/src/components/SettingsPopover.jsx"; import {DEFAULT_APP_SETTINGS} from "/src/lib/settings.js"; import "/src/styles/index.css"; createRoot(document.getElementById("root")).render(React.createElement(SettingsPopover, {open: true, initialCategory: "usage", settings: DEFAULT_APP_SETTINGS}));`; },
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url === "/usage-test") {
          response.setHeader("Content-Type", "text/html");
          return response.end(await server.transformIndexHtml("/usage-test", '<html><body><div id="root"></div><script type="module" src="/usage-entry.jsx"></script></body></html>'));
        }
        if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end('{"providers":[]}'); }
        if (!request.url.startsWith("/api/usage/")) return next();
        requests.push({ url: request.url, method: request.method });
        response.setHeader("Content-Type", "application/json");
        response.setHeader("Cache-Control", "no-store");
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
  win.webContents.on("console-message", details => { if (details.level === "error" && !details.message.includes("503")) errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/usage-test`);
  async function evaluate(source) { return win.webContents.executeJavaScript(source, true); }
  async function wait(source) {
    const deadline = Date.now() + 5000;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(resolve => setTimeout(resolve, 25)); }
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
  assert.equal(await evaluate("document.body.textContent.includes('Could not refresh')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('45 requests remaining')"), true);
  await evaluate("document.querySelector('.usage-account summary').click()");
  assert.equal(await evaluate("document.querySelector('.usage-account .usage-account-details').checkVisibility()"), true);
  assert.equal(await evaluate("document.querySelector('.usage-account').querySelectorAll('.usage-window').length"), 2);
  await screenshot("allowances-desktop");
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
  assert.deepEqual(requests.at(-1), { url: "/api/usage/refresh?days=30", method: "POST" });
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
  await click("Tokens in Raticode");
  await wait("document.querySelector('.usage-token-table').checkVisibility()");
  assert.equal(await evaluate("document.querySelector('.usage-total').textContent.includes('2 of 3 calls reported tokens')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('30 estimated tokens. Estimates are excluded')"), true);
  assert.equal(await evaluate("document.body.textContent.includes('1 call reported only part')"), true);
  assert.equal(await evaluate("document.querySelectorAll('.usage-token-table tbody tr')[1].textContent.includes('Not reported')"), true);
  await select("Usage period", "7");
  await wait("document.querySelector('.usage-token-table') !== null");
  assert.equal(requests.some(request => request.url === "/api/usage/overview?days=7" && request.method === "GET"), true);
  await click("Refresh usage");
  await wait("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Refresh usage')");
  assert.deepEqual(requests.at(-1), { url: "/api/usage/refresh?days=7", method: "POST" });
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
  assert.deepEqual(errors, []);
  console.log("Usage UI passed: settings integration, allowance and token views, credential preserve/replace/remove, draft clear/type/blur, profile isolation, credential store errors, saved usage refresh, mobile and dark rendering.");
  clearTimeout(timer);
  win.destroy();
  await vite.close();
  fs.rmSync(profile, { recursive: true, force: true });
  app.quit();
}
main().catch(async error => { console.error(error); if (vite) await vite.close(); app.exit(1); });
