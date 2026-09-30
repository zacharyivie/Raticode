/* global __dirname, console, setTimeout, clearTimeout, process, fetch */
// Visual regression audit with a real isolated backend and a fake provider.
// Run through run-organizations-visual.cjs. No user projects or providers are used.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
const out = process.env.CAPTURE_DIR || path.join(os.tmpdir(), "raticode-organizations-visual");
fs.mkdirSync(out, { recursive: true });
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "organization-capture-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 240000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  backend = spawn(path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/organization_browser_server.py")], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") } });
  backend.stderr.on("data", data => process.stderr.write(data));
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    backend.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
    backend.on("exit", code => reject(new Error(`Backend exited: ${code}`)));
  });
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(root, "frontend"), logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "organization-capture",
    resolveId(id) { if (id === "/organization-entry.jsx") return id; },
    load(id) { if (id === "/organization-entry.jsx") return `import React, {useState} from "react"; import {createRoot} from "react-dom/client"; import OrganizationWorkspace from "/src/components/OrganizationWorkspace.jsx"; import "/src/styles/index.css"; function App() { const [id,setId] = useState(location.hash.slice(1) || "new"); return <main style={{height:"100vh",display:"flex"}}><OrganizationWorkspace key={id} rootPath={${JSON.stringify(ready.project)}} organizationId={id} onSelect={setId} onClose={()=>{}} /></main>; } createRoot(document.getElementById("root")).render(<App/>);`; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url.startsWith("/organization-test")) { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/organization-test", '<html><body><div id="root"></div><script type="module" src="/organization-entry.jsx"></script></body></html>')); }
      if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end('{"providers":[]}'); }
      if (!request.url.startsWith("/api/organizations")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}/organization-test`;
  win = new BrowserWindow({ width: 1280, height: 860, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", details => { if (details.level === "error") errors.push(details.message); });
  await win.loadURL(base);
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  const api = (action, organizationId, params = {}) => evaluate(`fetch("/api/organizations?projectRoot=${encodeURIComponent(ready.project)}&action=${action}", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(${JSON.stringify({ projectRoot: ready.project, action, organizationId, params })}) }).then(r => r.json()).then(p => { if (p.error) throw new Error(p.error); return p.result; })`);
  const person = (id, name, title, role, reportsTo, extra = {}) => ({ id, name, title, role, reportsTo, instructions: `${name} owns ${role.toLowerCase()}. Keep notes short and attach evidence to every handoff.`, provider: "codex", model: "cli-default", permissionMode: "workspace-write", heartbeatSeconds: 0, ...extra });
  const org = await api("create", undefined, { config: {
    name: "Launch studio", slug: "launch-studio", description: "Ship reliable desktop releases every two weeks with reviewed changelogs and signed installers.",
    instructions: "Prefer small reviewed changes. Escalate blocked work to your manager.", goals: ["Two-week release cadence", "Zero unsigned installers"],
    maxConcurrency: 2, maxTaskTurns: 20, turnTimeoutSeconds: 1800, monthlyBudgetUsd: 50, monthlyTurnLimit: 400,
    employees: [
      person("ada", "Ada Okafor", "Chief executive", "Company direction and prioritisation", null),
      person("kai", "Kai Lindqvist", "Chief technology officer", "Engineering planning and code review", "ada"),
      person("mira", "Mira Santos", "Release engineer", "Build pipelines, packaging and release verification", "kai", { heartbeatSeconds: 3600 }),
      person("theo", "Theo Brandt-Nakamura", "Quality engineer", "Regression testing across Windows, macOS and Linux installers", "kai"),
      person("juno", "Juno Park", "Product designer", "Release notes, screenshots and in-app announcements", "ada", { paused: true }),
    ],
    projects: [{ id: "desktop", name: "Desktop 0.9", owner: "kai", description: "Next desktop release" }],
    teams: [{ id: "engineering", name: "Engineering", manager: "kai" }],
  } });
  const id = org.id;
  let current = await api("read", id);
  await api("configure", id, { config: { ...current.config, instructions: current.config.instructions + " Record decisions in the task conversation." }, expectedRevision: current.revision, reason: "Clarified where decisions are recorded" });
  current = await api("read", id);
  await api("configure", id, { config: { ...current.config, maxConcurrency: 3 }, expectedRevision: current.revision, reason: "Allow three employees to work at once" });
  const tasks = [
    { title: "Verify the Linux AppImage signature before publishing", description: "Check the detached signature against the release key and attach the command output.", assignee: "mira", priority: "high", project: "desktop" },
    { title: "Draft the 0.9 release notes", description: "Summarise user-facing changes since 0.8 in plain language.", assignee: "juno", priority: "medium", status: "backlog" },
    { title: "Run installer regression suite on Windows 11", assignee: "theo", priority: "critical", approvalRequired: true, project: "desktop" },
    { title: "Weekly dependency audit", assignee: "kai", priority: "low", recurring: true, intervalSeconds: 604800 },
    { title: "Plan the 1.0 roadmap", assignee: "ada", priority: "medium", status: "blocked" },
  ];
  for (const task of tasks) await api("task_create", id, task);
  await api("control", id, { state: "running" });
  await sleep(2500);
  await api("control", id, { state: "paused" });
  current = await api("read", id);
  const first = current.runtime.tasks[0];
  await api("comment", id, { taskId: first.id, body: "Use the release key from the vault, not the developer key.\nAttach the gpg --verify output." });
  await win.loadURL(`${base}#${id}`);
  await sleep(800);
  async function clickText(text, selector = "button") { const ok = await evaluate(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(b => b.textContent.trim() === ${JSON.stringify(text)} || b.getAttribute('aria-label') === ${JSON.stringify(text)} || b.querySelector('strong')?.textContent === ${JSON.stringify(text)}); if (!el) return false; el.click(); return true; })()`); assert.ok(ok, `Missing control: ${text}`); await sleep(350); return ok; }
  async function shot(name) { await sleep(250); const overflow = await evaluate("document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.org-workspace *')].some(e => !e.closest('.org-chart-canvas') && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible' && e.getBoundingClientRect().right > innerWidth)"); assert.equal(overflow, false, `Overflow in ${name}`); fs.writeFileSync(path.join(out, `${name}.png`), (await win.webContents.capturePage()).toPNG()); }
  const scenes = (process.env.CAPTURE_SCENES || "people,chart,teams,projects,employee-edit,work,task-edit,task-open,history,restore,activity,settings,new").split(",");
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_WIDTHS || "1280,720,420").split(",").map(Number)) {
      win.setSize(width, 860); await win.loadURL(`${base}#${id}`); await sleep(700);
      await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"})`);
      const tag = `${theme}-${width}`;
      for (const scene of scenes) {
        await win.loadURL(`${base}?scene=${scene}&width=${width}&theme=${theme}#${scene === "new" ? "new" : id}`); await sleep(700);
        await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"})`);
        if (scene === "chart") { await clickText("Org chart"); await shot(`${tag}-chart`); }
        if (["teams", "projects"].includes(scene)) {
          await clickText("Org chart");
          await evaluate(`document.querySelectorAll('.org-chart-navigation button')[${scene === "teams" ? 1 : 2}].click()`);
          await shot(`${tag}-${scene}`);
          await evaluate("document.querySelector('.org-directory button').click()");
          if (scene === "projects") assert.deepEqual(await evaluate("[...document.querySelectorAll('.org-collection-people button')].map(button => button.getAttribute('aria-label'))"), ["Edit employee: Kai Lindqvist", "Edit employee: Mira Santos", "Edit employee: Theo Brandt-Nakamura"]);
          await shot(`${tag}-${scene}-detail`);
        }
        if (scene === "people") await shot(`${tag}-people`);
        if (scene === "employee-edit") {
          await clickText("Mira Santos", ".org-person"); await clickText("Edit employee"); await shot(`${tag}-employee-edit`);
          await evaluate("document.querySelector('.org-provider-fields').scrollIntoView({block:'start'})");
          await shot(`${tag}-employee-provider`);
          await clickText("Tools, skills and MCP servers", "summary");
          await evaluate("[...document.querySelectorAll('summary')].find(s=>s.textContent==='Tools, skills and MCP servers').scrollIntoView({block:'start'})");
          await shot(`${tag}-employee-tools`);
        }
        if (scene === "work") { await clickText("Work"); await shot(`${tag}-work`); }
        if (scene === "task-edit") { await clickText("Work"); await clickText("New task"); await shot(`${tag}-task-edit`); }
        if (scene === "task-open") { await clickText("Work"); await clickText(first.title, ".org-task-open"); await shot(`${tag}-task-open`); }
        if (scene === "history") { await clickText("Configuration history"); await shot(`${tag}-history`); }
        if (scene === "restore") { await clickText("Configuration history"); await evaluate("[...document.querySelectorAll('button')].filter(b => /Preview restore|Preview/.test(b.textContent) && !b.disabled).at(-1)?.click()"); await sleep(400); await shot(`${tag}-restore`); }
        if (scene === "activity") { await clickText("Activity"); await shot(`${tag}-activity`); }
        if (scene === "settings") { await clickText("Organization settings"); await shot(`${tag}-settings`); }
        if (scene === "new") { await shot(`${tag}-new`); }
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(`Organizations visual checks passed. Screenshots: ${out}`);
}
main().then(async () => { clearTimeout(timeout); backend?.kill(); await vite?.close(); win?.destroy(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); backend?.kill(); await vite?.close(); app.exit(1); });
