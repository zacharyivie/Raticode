/* global __dirname, console, process, setTimeout, clearTimeout, URL */
// Settings > Providers in light and dark mode: one summary row per installed app,
// details behind the chevron that open on models and end with a folded "App location"
// line, missing apps folded into "Not installed", and the
// workflow-only sources folded below the coding apps with one-line summaries.
// Isolated: mock provider and account APIs, no real CLIs, keys or backend.
// Run: electron --no-sandbox browser-tests/provider-settings-layout.browser.cjs
// Set PROVIDER_SETTINGS_SCREENSHOTS=<folder> to save a screenshot of each state.
// Where no X server can start (such as an agent's sandbox), set
// RATICODE_TEST_BACKGROUND_DISPLAY=1 and add --ozone-platform=headless
// --ozone-override-screen-size=1920,1080 to the electron command.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-provider-settings-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Provider settings layout test timed out"); app.exit(1); }, 120000);
const shots = process.env.PROVIDER_SETTINGS_SCREENSHOTS || "";
const efforts = [{ id: "low", displayName: "Low" }, { id: "medium", displayName: "Medium" }, { id: "high", displayName: "High" }];
const models = [{ id: "claude-opus-5-5", displayName: "Opus 5.5", efforts }, { id: "claude-sonnet-5-5", displayName: "Sonnet 5.5", efforts }];
const providers = [
  { id: "codex", displayName: "Codex", version: "0.156.1", available: true, enabled: true, detected: true, executable: "/usr/local/bin/codex",
    discoveryStatus: "unauthenticated", supportsBrowserLogin: true, error: "Sign in to Codex to discover models.", models: [] },
  { id: "claude_code", displayName: "Claude Code", version: "2.1.4", available: true, enabled: true, detected: true, executable: "/home/me/.local/bin/claude",
    discoveryStatus: "ready", supportsBrowserLogin: true, defaultModel: "claude-opus-5-5", models, discoveredModels: models, deniedModels: [] },
  { id: "cursor", displayName: "Cursor", version: "2026.11.04", available: true, enabled: false, detected: true, executable: "/usr/bin/cursor-agent",
    discoveryStatus: "ready", supportsBrowserLogin: true, defaultModel: "auto", models: [{ id: "auto", displayName: "Auto" }] },
  { id: "copilot", displayName: "GitHub Copilot", available: false, enabled: false, detected: false, discoveryStatus: "missing", models: [] },
  // A saved override that wasn't found, as the backend sends it: it keeps a row and its App location line opens by itself.
  { id: "opencode", displayName: "OpenCode", available: false, enabled: true, detected: false, executable: null,
    executableOverride: "/opt/opencode/bin/opencode", discoveryStatus: "missing", models: [] },
  { id: "antigravity", displayName: "Antigravity", available: false, enabled: false, detected: false, discoveryStatus: "missing", models: [] },
  { id: "grok", displayName: "Grok", version: "0.9.2", available: true, enabled: true, detected: true, executable: "/usr/bin/grok", discoveryStatus: "pending", models: [] },
];
const accounts = {
  providers: [
    { id: "claude_code", label: "Claude Code", cli: "claude", setup: "CLAUDE.md, skills, commands and agents", can_copy_setup: true, setup_folder: "/home/me/.claude" },
    { id: "codex", label: "Codex", cli: "codex", setup: "AGENTS.md, skills and config.toml", can_copy_setup: true, setup_folder: "/home/me/.codex" },
  ],
  accounts: [
    { name: "work", provider: "claude_code", label: "Claude Code · work", state: "signed_in", plan: "max_5x", email: "work@example.com" },
    { name: "home", provider: "claude_code", label: "Claude Code · home", state: "needs_sign_in", needs_sign_in: true, reason: "Not signed in yet." },
  ],
  warnings: [],
};
// Rem's saved account choices, and self-work's own: Claude Code's labels sit on the
// work and home rows; Codex has no accounts, so its Choose by usage shows as lines.
const remAccounts = { claude_code: "work", codex: "auto" };
const kanban = {
  version: "v1",
  board: { tickets: [], organizations: [], counts: {}, projects: [],
    participants: [{ participant_id: "rem", kind: "rem", name: "Rem", title: "Coordinator", organization_id: "", organization_name: "", projects: [], assignable: [], unavailable: [], hidden_projects: 0, revision: 1 }] },
  self_work: {
    settings: { enabled: false, max_threads: 2, auto_pull_backlog: false, idle_minutes: 15, revision: 1, provider_accounts: { claude_code: "home" } },
    limits: { max_threads_min: 1, max_threads_max: 16 },
    state: { mode: "off", active_threads: 0, thread_limit: 2, todo_remaining: 0, proposals_waiting: 0, assigned_projects: 0, outside_projects: 0, board_enabled: true, activity: [], last_prompt_at: "", starts_at: "", detail: "" },
    accounts: [{ provider: "claude_code", label: "Claude Code", choice: "home", rem_setting: "work", rem_setting_label: "work", accounts: ["home", "work"] }],
    report: { since: "", updated_at: "", unseen: false, summary: "", tickets_added: [], duplicates_merged: [], notes: [], work_done: [] },
  },
};
let vite;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), logLevel: "error", cacheDir: path.join(profile, "vite-cache"),
    server: { host: "127.0.0.1", port: 0 }, plugins: [{
      name: "provider-settings-layout-test",
      resolveId(id) { if (id === "/settings-entry.jsx") return id; },
      load(id) {
        if (id !== "/settings-entry.jsx") return;
        return `import React from "react"; import { createRoot } from "react-dom/client";
          import SettingsPopover from "/src/components/SettingsPopover.jsx";
          import { DEFAULT_APP_SETTINGS } from "/src/lib/settings.js"; import "/src/styles/index.css";
          if (location.search === "?theme=dark") document.documentElement.classList.add("dark");
          window.goferDesktop = { workspace: { selectPath: async () => "" } };
          const settings = { ...DEFAULT_APP_SETTINGS, assistant: { ...DEFAULT_APP_SETTINGS.assistant, providerAccounts: ${JSON.stringify(remAccounts)} } };
          createRoot(document.getElementById("root")).render(React.createElement(SettingsPopover,
            { open: true, initialCategory: "providers", settings, onChange() {}, onClose() { window.settingsClosed = (window.settingsClosed || 0) + 1; } }));`;
      },
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url.split("?")[0] === "/settings-test") {
            response.setHeader("Content-Type", "text/html");
            response.end(await server.transformIndexHtml("/settings-test", `<html><body class="bg-canvas"><div id="root"></div><script type="module" src="/settings-entry.jsx"></script></body></html>`));
            return;
          }
          if (!request.url.startsWith("/api/")) return next();
          response.setHeader("Content-Type", "application/json");
          const url = new URL(request.url, "http://localhost");
          if (url.pathname === "/api/provider/capabilities") {
            const id = url.searchParams.get("provider");
            return response.end(JSON.stringify({ providers: id ? providers.filter(provider => provider.id === id) : providers }));
          }
          if (url.pathname === "/api/provider/accounts") return response.end(JSON.stringify(accounts));
          if (url.pathname === "/api/rem/kanban") return response.end(JSON.stringify(kanban));
          if (url.pathname === "/api/provider/profiles") return response.end(JSON.stringify({ profiles: [
            { name: "ollama", subscription: "openai_api", local: true, loopback: true, api_base_url: "http://127.0.0.1:11434/v1" }] }));
          if (url.pathname === "/api/classifiers/jev") return response.end(JSON.stringify({ provider: "jev", configured: true, enabled: true, models: [] }));
          return response.end("{}");
        });
      },
    }] });
  await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}/settings-test`;
  const win = new BrowserWindow({ width: 900, height: 720, show: true, webPreferences: { partition: "provider-settings-layout", contextIsolation: true } });
  const evaluate = source => win.webContents.executeJavaScript(source, true);
  const wait = async (source, ms = 15000) => {
    const deadline = Date.now() + ms;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(r => setTimeout(r, 50)); }
  };
  async function shot(name) {
    if (!shots) return;
    await new Promise(r => setTimeout(r, 250));
    fs.mkdirSync(shots, { recursive: true });
    fs.writeFileSync(path.join(shots, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  }
  for (const theme of ["light", "dark"]) {
    await win.loadURL(`${base}?theme=${theme}`);
    await wait(`document.querySelectorAll('[data-provider-status]').length === 5`);
    await wait(`!document.body.textContent.includes("Checking providers")`);
    const layout = await evaluate(`(() => {
      const panel = document.querySelector('[aria-label="Application settings"]');
      const rows = [...document.querySelectorAll('section[data-provider]')].map(row => ({ id: row.dataset.provider, height: Math.round(row.getBoundingClientRect().height),
        status: row.querySelector('[data-provider-status]').textContent, switchOn: row.querySelector('[role=switch]').getAttribute('aria-checked') }));
      const overflow = [...panel.querySelectorAll('*')].filter(el => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible').map(el => el.className);
      const ink = getComputedStyle(document.querySelector('section[data-provider] h4 span span span')).color;
      const parts = [...document.querySelectorAll('[data-provider-part]')].map(part => [part.dataset.providerPart, part.querySelector('h3').textContent]);
      const sources = [...document.querySelectorAll('[data-workflow-source]')].map(source => [source.dataset.workflowSource,
        source.querySelector('[data-workflow-source-summary]').textContent, source.querySelector('button').getAttribute('aria-expanded')]);
      return { size: [innerWidth, innerHeight], rows, overflow, ink, parts, sources, missing: document.querySelector('[data-provider-group=missing] button').textContent };
    })()`);
    await shot(`providers-${theme}-rows`);
    assert.deepEqual(layout.rows.map(row => [row.id, row.status]), [["codex", "Needs sign-in"], ["claude_code", "Ready"], ["cursor", "Ready"], ["opencode", "Error"], ["grok", "Checking"]]);
    // A collapsed row stays compact: two short lines, plus one line for a message.
    for (const row of layout.rows) assert.ok(row.height <= 80, `${row.id} row is ${row.height}px tall`);
    assert.equal(layout.missing, "Not installed (2)");
    assert.deepEqual(layout.parts, [["coding-apps", "Coding apps"], ["workflow-only", "For workflows only"]]);
    assert.deepEqual(layout.sources, [["local-model-servers", "1 server", "false"], ["classification-models", "Jev: key saved", "false"]]);
    assert.deepEqual(layout.overflow, [], "Something in Settings > Providers scrolls sideways");
    // Text is dark on light and light on dark.
    const [r, g, b] = layout.ink.match(/\d+/g).map(Number);
    assert.ok(theme === "dark" ? r + g + b > 450 : r + g + b < 300, `${theme} provider name color ${layout.ink}`);
    await evaluate(`document.querySelector('[aria-controls="provider-details-claude_code"]').click()`);
    await wait(`Boolean(document.querySelector('[aria-label="Claude Code details"]'))`);
    await evaluate(`document.querySelector('[data-provider=claude_code]').scrollIntoView({ block: "start" })`);
    const detailsOverflow = await evaluate(`[...document.querySelector('[aria-label="Claude Code details"]').querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > document.querySelector('[aria-label="Application settings"]').getBoundingClientRect().right + 1).length`);
    assert.equal(detailsOverflow, 0, "Claude Code's details stick out of the Settings panel");
    await shot(`providers-${theme}-details`);
    // Details open on the parts people use; where the app runs from is one closed line at the end.
    const details = await evaluate(`(() => {
      const region = document.querySelector('[aria-label="Claude Code details"]');
      const text = region.textContent;
      const line = region.querySelector('[aria-controls="provider-app-location-claude_code"]');
      const last = region.lastElementChild;
      return { order: ["Show in model menus", "Default model and effort", "Claude Code accounts", "own sign-in again", "App location"].map(part => text.indexOf(part)),
        expanded: line.getAttribute("aria-expanded"), lineText: line.textContent, lineHeight: Math.round(line.getBoundingClientRect().height),
        lastIsLocation: last.contains(line), fields: Boolean(region.querySelector('[aria-label="Claude Code executable"]')) };
    })()`);
    assert.ok(details.order.every((index, at) => index >= 0 && (at === 0 || index > details.order[at - 1])), `details order: ${details.order}`);
    assert.deepEqual([details.expanded, details.lineText, details.lastIsLocation, details.fields], ["false", "App locationAutomatic · /home/me/.local/bin/claude", true, false]);
    assert.ok(details.lineHeight <= 24, `App location line is ${details.lineHeight}px tall`);
    await evaluate(`document.querySelector('[aria-controls="provider-app-location-claude_code"]').click()`);
    await wait(`Boolean(document.querySelector('[aria-label="Claude Code executable"]') && document.querySelector('[aria-label="Choose Claude Code executable"]'))`);
    await evaluate(`document.querySelector('[data-app-location=claude_code]').scrollIntoView({ block: "center" })`);
    const locationOverflow = await evaluate(`[...document.querySelector('[data-app-location=claude_code]').querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > document.querySelector('[aria-label="Application settings"]').getBoundingClientRect().right + 1).length`);
    assert.equal(locationOverflow, 0, "Claude Code's App location fields stick out of the Settings panel");
    await shot(`providers-${theme}-app-location`);
    // An override that wasn't found opens its App location line by itself.
    await evaluate(`document.querySelector('[aria-controls="provider-details-opencode"]').click()`);
    await wait(`Boolean(document.querySelector('[aria-label="OpenCode details"]'))`);
    const opencode = await evaluate(`(() => { const line = document.querySelector('[aria-controls="provider-app-location-opencode"]');
      return [line.getAttribute("aria-expanded"), line.textContent, document.querySelector('[aria-label="OpenCode executable"]')?.value]; })()`);
    assert.deepEqual(opencode, ["true", "App locationOverride · /opt/opencode/bin/opencode · not found", "/opt/opencode/bin/opencode"]);
    await evaluate(`document.querySelector('[data-app-location=opencode]').scrollIntoView({ block: "center" })`);
    await shot(`providers-${theme}-app-location-not-found`);
    await evaluate(`document.querySelector('[aria-controls="provider-details-opencode"]').click()`);
    await evaluate(`document.querySelector('[aria-label="Claude Code accounts"], [data-accounts-provider=claude_code]').scrollIntoView({ block: "center" })`);
    await shot(`providers-${theme}-accounts`);
    // A signed-in account keeps name, status, plan and email on one line, with only its ⋯ button.
    const accountRow = await evaluate(`(() => { const row = document.querySelector('[data-account=work]'); const header = row.firstElementChild;
      return { height: Math.round(header.getBoundingClientRect().height), text: header.textContent, buttons: [...header.querySelectorAll('button')].map(b => b.textContent || b.getAttribute('aria-label')) }; })()`);
    assert.equal(accountRow.text, "workSigned inmax_5xwork@example.com");
    assert.deepEqual(accountRow.buttons, ["Actions for work"]);
    assert.ok(accountRow.height <= 32, `The work account's row is ${accountRow.height}px tall`);
    // The account each kind of work uses carries its label and a Change link; a signed-out
    // choice keeps its label with "(needs sign-in)". Labels fit inside the panel.
    const uses = await evaluate(`(() => { const panel = document.querySelector('[aria-label="Application settings"]').getBoundingClientRect();
      const read = selector => [...document.querySelectorAll(selector)].map(el => [el.dataset.accountUse, el.textContent, Math.round(el.getBoundingClientRect().height),
        el.getBoundingClientRect().right <= panel.right + 1, getComputedStyle(el.querySelector('span')).color, getComputedStyle(el.querySelector('span')).backgroundColor]);
      return { work: read('[data-account=work] [data-account-use]'), home: read('[data-account=home] [data-account-use]'),
        lines: read('[data-accounts-provider=claude_code] > ul[aria-label] [data-account-use]') }; })()`);
    assert.deepEqual(uses.work.map(([id, text]) => [id, text]), [["rem", "Threads, tasks and Rem’s repliesChange"]]);
    assert.deepEqual(uses.home.map(([id, text]) => [id, text]), [["self_work", "Self-work (needs sign-in)Change"]]);
    assert.deepEqual(uses.lines, []);
    for (const [id, , height, inside, color, background] of [...uses.work, ...uses.home]) {
      assert.ok(height <= 24, `The ${id} label is ${height}px tall`);
      assert.ok(inside, `The ${id} label sticks out of the Settings panel`);
      assert.notEqual(color, background, `The ${id} label's text matches its background`);
    }
    await evaluate(`document.querySelector('[aria-controls="provider-details-codex"]').click()`);
    await wait(`Boolean(document.querySelector('[data-accounts-provider=codex] [data-account-use]'))`);
    const codexLines = await evaluate(`[...document.querySelectorAll('[data-accounts-provider=codex] [data-account-use]')].map(el => [el.dataset.accountUse, el.textContent])`);
    assert.deepEqual(codexLines, [["rem", "Threads, tasks and Rem’s replies:Choose by usageChange"], ["self_work", "Self-work:Choose by usageChange"]]);
    // With lines saying what's chosen, the no-accounts note doesn't also claim the default sign-in.
    assert.doesNotMatch(await evaluate(`document.querySelector('[data-accounts-provider=codex]').textContent`), /use your default Codex sign-in/);
    await evaluate(`document.querySelector('[data-accounts-provider=codex]').scrollIntoView({ block: "center" })`);
    await shot(`providers-${theme}-accounts-in-use`);
    await evaluate(`document.querySelector('[aria-controls="provider-details-codex"]').click()`);
    // The ⋯ menu by real keys: Enter opens it on its first item, arrows move, Escape closes
    // it and returns focus to the button without closing Settings, and Space opens it again.
    // Enter and Space also send the typed character, which is what activates a button.
    const press = async keyCode => {
      const char = { Return: "\r", Space: " " }[keyCode];
      win.webContents.sendInputEvent({ type: "keyDown", keyCode });
      if (char) win.webContents.sendInputEvent({ type: "char", keyCode: char });
      win.webContents.sendInputEvent({ type: "keyUp", keyCode });
      await new Promise(resolve => setTimeout(resolve, 100));
    };
    const focused = `(document.activeElement.textContent || document.activeElement.getAttribute('aria-label'))`;
    const menuOpen = `Boolean(document.querySelector('[role=menu][aria-label="Actions for work"]'))`;
    win.webContents.focus();
    await evaluate(`document.querySelector('[aria-label="Actions for work"]').focus()`);
    await press("Return");
    await wait(menuOpen);
    assert.equal(await evaluate(focused), "Sign in again");
    await press("Down");
    assert.equal(await evaluate(focused), "Rename");
    await press("Up"); await press("Up");
    assert.equal(await evaluate(focused), "Remove");
    const menu = await evaluate(`(() => { const menu = document.querySelector('[role=menu]'); const box = menu.getBoundingClientRect();
      const panel = document.querySelector('[aria-label="Application settings"]').getBoundingClientRect();
      const color = text => getComputedStyle([...menu.querySelectorAll('[role=menuitem]')].find(item => item.textContent === text)).color;
      return { items: [...menu.querySelectorAll('[role=menuitem]')].map(item => item.textContent), inside: box.left >= panel.left && box.right <= panel.right + 1,
        separatorBeforeRemove: menu.querySelector('[role=separator] + [role=menuitem]')?.textContent, remove: color("Remove"), rename: color("Rename") }; })()`);
    assert.deepEqual(menu.items, ["Sign in again", "Rename", "Import setup", "Remove"]);
    assert.ok(menu.inside, "The account menu sticks out of the Settings panel");
    assert.equal(menu.separatorBeforeRemove, "Remove");
    assert.notEqual(menu.remove, menu.rename, "Remove isn't styled as a danger action");
    await shot(`providers-${theme}-account-menu`);
    await press("Escape");
    await wait(`!${menuOpen}`);
    assert.equal(await evaluate(focused), "Actions for work");
    assert.equal(await evaluate(`window.settingsClosed || 0`), 0, "Escape in the account menu closed Settings");
    await press("Space");
    await wait(menuOpen);
    assert.equal(await evaluate(focused), "Sign in again");
    await press("Escape");
    await wait(`!${menuOpen}`);
    await evaluate(`document.querySelector('[data-provider-group=missing] button').click()`);
    await wait(`Boolean(document.querySelector('[aria-label="Choose GitHub Copilot executable"]'))`);
    // OpenCode's override wasn't found, so it keeps its own row instead of joining Not installed.
    const notInstalled = await evaluate(`[...document.querySelectorAll('[data-provider-group=missing] li[data-provider]')].map(item => item.dataset.provider).join(",")`);
    assert.equal(notInstalled, "copilot,antigravity");
    await evaluate(`document.querySelector('[data-provider-group=missing]').scrollIntoView({ block: "center" })`);
    await shot(`providers-${theme}-not-installed`);
    await evaluate(`document.querySelectorAll('[data-workflow-source] h4 button').forEach(button => button.click())`);
    await wait(`Boolean(document.querySelector('[aria-label="Local model server profile name"]') && document.querySelector('[aria-label="Jev API key"]'))`);
    await evaluate(`document.querySelector('[data-provider-part=workflow-only]').scrollIntoView({ block: "start" })`);
    const formOverflow = await evaluate(`[...document.querySelector('[data-provider-part=workflow-only]').querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > document.querySelector('[aria-label="Application settings"]').getBoundingClientRect().right + 1).length`);
    assert.equal(formOverflow, 0, "A workflow-only form sticks out of the Settings panel");
    await shot(`providers-${theme}-workflow-only`);
    // Change opens Settings > Rem with focus on the provider's Accounts picker.
    await evaluate(`document.querySelector('[aria-label="Change the Claude Code account for threads, tasks and Rem’s replies"]').click()`);
    await wait(`document.activeElement?.getAttribute('aria-label') === "Claude Code account"`);
    const reveal = await evaluate(`(() => { const select = document.activeElement; const box = select.getBoundingClientRect();
      const list = document.querySelector('[aria-label="Application settings"] .workflow-scrollbar').getBoundingClientRect();
      return { category: document.querySelector('[aria-current=page]').textContent, row: Boolean(select.closest('[data-setting-row=providerAccounts]')),
        visible: box.top >= list.top && box.bottom <= list.bottom, value: select.value }; })()`);
    assert.deepEqual(reveal, { category: "Rem", row: true, visible: true, value: "work" });
    await shot(`providers-${theme}-change-opens-rem-accounts`);
  }
  console.log("Provider settings layout: compact summary rows, details with the folded App location line, the account ⋯ menu by keyboard, the accounts in use with their Change links, the Not installed group and the workflow-only sources fit in light and dark mode.");
}

main().then(() => { clearTimeout(timer); return vite?.close(); }).then(() => app.exit(0)).catch(async (error) => {
  console.error(error);
  await vite?.close().catch(() => {});
  app.exit(1);
});
