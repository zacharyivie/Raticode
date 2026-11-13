/* global __dirname, console, setTimeout, clearTimeout, process, fetch */
// Drives the Organizations experience against a real, isolated backend whose sample
// organization ran one ticket with a test employee. Nothing touches the user's data.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "organization-home-profile-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 180000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  backend = spawn(process.env.GOFER_TEST_PYTHON || path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/organization_home_server.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src"), RATICODE_ORG_HOME_STDIN: "1" },
  });
  backend.stderr.on("data", data => process.stderr.write(data));
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    backend.stdout.on("data", chunk => { output += chunk; const line = output.split("\n").find(item => item.startsWith("{")); if (line) { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } } });
    backend.on("exit", code => reject(new Error(`Backend exited: ${code}`)));
  });
  const oid = ready.organizationId;
  const api = async (action, params = {}) => {
    const response = await fetch(`http://127.0.0.1:${ready.port}/api/organizations`, { method: "POST", headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, body: JSON.stringify({ action, organizationId: oid, params }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error);
    return payload.result;
  };
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(root, "frontend"), cacheDir: testViteCacheDir("organization-home"), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "organization-home-test",
    resolveId(id) { if (id === "/organization-home-entry.jsx") return id; },
    load(id) {
      if (id === "/organization-home-entry.jsx") return `import React, {useState} from "react"; import {createRoot} from "react-dom/client"; import OrganizationHome from "/src/components/OrganizationHome.jsx"; import "/src/styles/index.css";
        function App() { const [id, setId] = useState(${JSON.stringify(oid)}); return <main style={{height:"100vh",display:"flex"}}>{id ? <OrganizationHome key={id} rootPath="" organizationId={id} onSelect={setId} onClose={() => setId(null)} /> : <p>Closed</p>}</main>; }
        createRoot(document.getElementById("root")).render(<App/>);`;
    },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url === "/organization-home-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/organization-home-test", '<html><body><div id="root"></div><script type="module" src="/organization-home-entry.jsx"></script></body></html>')); }
      if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end('{"providers":[{"id":"codex","name":"Codex","enabled":true,"models":[]},{"id":"claude_code","name":"Claude Code","enabled":true,"models":[]}]}'); }
      if (!request.url.startsWith("/api/organizations")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 1440, height: 900, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = []; win.webContents.on("console-message", details => { if (details.level === "error") errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/organization-home-test`);
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function wait(code, label = code) { const until = Date.now() + 10000; while (!await evaluate(`Boolean(${code})`)) { if (Date.now() > until) throw new Error(`Missing: ${label}\nUI: ${(await evaluate("document.body.innerText")).slice(0, 3000)}`); await sleep(40); } }
  const button = (text, scope = "document") => `[...${scope}.querySelectorAll('button')].find(b => !b.disabled && (b.textContent.trim() === ${JSON.stringify(text)} || b.getAttribute('aria-label') === ${JSON.stringify(text)}))`;
  async function click(text, scope) { await wait(button(text, scope), `button ${text}`); await evaluate(`${button(text, scope)}.click()`); }
  async function tab(name) { await evaluate(`[...document.querySelectorAll('[role=tab]')].find(b => b.textContent.startsWith(${JSON.stringify(name)})).click()`); await sleep(150); }
  async function fill(selector, value) { await evaluate(`(() => { const input = ${selector}; input.focus(); const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', {bubbles: true})); })()`); }
  async function key(selector, name, extra = {}) { await evaluate(`${selector}.dispatchEvent(new KeyboardEvent('keydown', ${JSON.stringify({ key: name, bubbles: true, cancelable: true, ...extra })}))`); }
  // Escape leaves a focused field first, then returns from the details to the list.
  async function escape() { await evaluate("document.activeElement?.blur(); window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}))"); await wait("!document.querySelector('.oh-detail')", "details closed"); }
  const sheet = () => evaluate("document.querySelector('.oh-detail')?.innerText || ''");
  async function drag(title, column) { await evaluate(`(() => { const card = [...document.querySelectorAll('.oh-card')].find(c => c.textContent.includes(${JSON.stringify(title)})); const target = document.querySelector('.col-body[data-col=${column}]'); const data = new DataTransfer(); card.dispatchEvent(new DragEvent('dragstart', {dataTransfer: data, bubbles: true})); target.dispatchEvent(new DragEvent('dragover', {dataTransfer: data, bubbles: true, cancelable: true})); target.dispatchEvent(new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true})); })()`); }
  const task = async title => (await api("read")).runtime.tasks.find(item => item.title === title);

  // Organization: mission, milestones, what needs the user, and recent activity.
  await wait("document.querySelector('.oh-mission')");
  const overview = await evaluate("document.querySelector('.oh-view').innerText");
  for (const text of ["Make agent work something you can trust at a glance.", "Every agent claim has evidence", "Organizations 2.0", "STU-2", "Drag cards between columns", "Raticode Desktop", "Docs site"]) assert.ok(overview.includes(text), text);
  await click("Edit");
  await fill("document.querySelector('#oh-mission')", "Every agent claim is checked before you accept it.");
  await click("Save mission");
  await wait("document.querySelector('.oh-mission .statement')?.textContent === 'Every agent claim is checked before you accept it.'");
  assert.equal((await api("read")).config.mission, "Every agent claim is checked before you accept it.");
  assert.equal((await api("history"))[0].reason, "Updated the mission");

  // Review: the ticket's claims show Raticode's own checks, and a weak claim asks for care.
  await click("Review");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Drag cards between columns'");
  let text = await sheet();
  for (const expected of ["Board tests pass", "Verified: check", "Verified: quote", "Verified: commit", "No evidence", "1 without verified evidence", "Read in a run", "The board has five fixed columns", "org/drag-cards-between-columns"]) assert.ok(text.includes(expected), expected);
  await wait("document.querySelector('.oh-detail').innerText.includes('1 file')", "changed files");
  await evaluate("document.querySelector('.oh-evbtn.ok').click()");
  await wait("document.querySelector('.oh-detail').innerText.includes('Raticode reran it')");
  text = await sheet();
  assert.ok(text.includes("3 passed") && text.includes("Project check “unit”"), text);
  await evaluate("document.querySelector('.oh-trail button').click()");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Drag cards between columns'");
  await evaluate("[...document.querySelectorAll('.oh-detail .oh-chip')].find(b => b.textContent.startsWith('Run on')).click()");
  await wait("document.querySelector('.oh-detail').innerText.includes('Posted a claim')", "run steps");
  assert.ok((await sheet()).includes("Consulted organization knowledge"));
  await click("Back to STU-2");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Drag cards between columns'");
  await click("Accept anyway");
  await wait("document.querySelector('.oh-confirm')");
  await click("Accept", "document.querySelector('.oh-confirm')");
  await wait("document.querySelector('.oh-detail')?.innerText.includes('Reopen')", "accepted ticket");
  const accepted = await task("Drag cards between columns");
  assert.equal(accepted.status, "done");
  assert.match(accepted.evidence, /Board tests pass \(verified\)/);
  assert.match(accepted.evidence, /Feels faster on a real machine \(no evidence\)/);
  await escape();

  // Details fill the main area under the tabs; Escape returns to the list it came from.
  assert.equal(await evaluate("document.querySelectorAll('[role=tab]').length"), 5);
  assert.ok(await evaluate("Boolean(document.querySelector('.oh-mission'))"));
  await click("Milestone");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'New organization milestone'");
  await click("Cancel", "document.querySelector('.oh-detail')");
  await wait("!document.querySelector('.oh-detail') && Boolean(document.querySelector('.oh-mission'))", "milestone cancel");
  assert.equal((await api("read")).config.milestones.length, 4);

  // Issues: drag rules, quick add and the epic board.
  await tab("Issues");
  await wait("document.querySelector('.oh-card')");
  await drag("Group the board by epic", "todo");
  await wait("[...document.querySelectorAll('.col-body[data-col=todo] .oh-card')].some(c => c.textContent.includes('Group the board'))");
  assert.equal((await task("Group the board by epic")).status, "todo");
  // A To do card says what it waits for: here, the organization is paused.
  await wait("[...document.querySelectorAll('.col-body[data-col=todo] .oh-card')].find(c => c.textContent.includes('Group the board'))?.querySelector('.oh-wait')?.textContent.includes('starts when you start the organization')", "start hint on card");
  await wait("document.querySelector('.oh-toasts').innerText.includes('It starts when you start the organization')", "start hint in toast");
  await drag("Write the Organizations guide", "done");
  await wait("document.querySelector('.oh-toasts').innerText.includes('Only reviewed work moves to Done')");
  // Done work dragged to Backlog reopens straight into Backlog, never passing through To do.
  await drag("Drag cards between columns", "backlog");
  await wait("document.querySelector('.oh-toasts').innerText.includes('STU-2 reopened in Backlog')", "reopen toast");
  assert.equal((await task("Drag cards between columns")).status, "backlog");
  // In progress means "next": the ticket goes first in its worker's queue and runs when they're free.
  await drag("Write the Organizations guide", "running");
  await wait("document.querySelector('.oh-toasts').innerText.includes('STU-5 is next for Sage Kim')", "pull toast");
  const pulled = await task("Write the Organizations guide");
  assert.equal(pulled.status, "todo"); assert.ok(pulled.pulledAt);
  assert.match(pulled.comments.at(-1).body, /moved this to In progress/);
  await click("Add ticket");
  await fill("document.querySelector('.oh-add-input')", "Keyboard moves on the board");
  await key("document.querySelector('.oh-add-input')", "Enter");
  await wait("[...document.querySelectorAll('.col-body[data-col=backlog] .oh-card')].some(c => c.textContent.includes('STU-6') && c.textContent.includes('Keyboard moves'))", "quick add");
  assert.equal((await task("Keyboard moves on the board")).status, "backlog");
  await evaluate("document.querySelector('#oh-f-epic').value = 'E1'; document.querySelector('#oh-f-epic').dispatchEvent(new Event('change', {bubbles: true}))");
  await wait("document.querySelectorAll('.oh-card').length === 3", "epic filter");
  await click("Epics");
  await wait("document.querySelector('.oh-epic-card')");
  assert.ok((await evaluate("document.querySelector('.oh-board.epics').innerText")).includes("Evidence receipts"));
  await click("Tickets");

  // Checks can wait for the user. Start the organization so the scheduler runs STU-3 for real.
  await tab("Projects");
  await wait("document.querySelector('.oh-phead')");
  await click("Details");
  await wait("[...document.querySelectorAll('.oh-detail .oh-toggle')].some(l => l.textContent.includes('without asking'))");
  await evaluate("[...document.querySelectorAll('.oh-detail .oh-toggle')].find(l => l.textContent.includes('without asking')).querySelector('input').click()");
  await click("Save checks");
  await wait("document.querySelector('.oh-toasts').innerText.includes('Checks saved')");
  assert.equal((await api("read")).config.projects.find(item => item.id === "desktop").autoRunChecks, undefined);
  await escape();
  await click("Start");
  await click("Start organization");
  await tab("Issues");
  await wait("[...document.querySelectorAll('.col-body[data-col=review] .oh-card')].some(c => c.textContent.includes('Group the board'))", "STU-3 in review");
  await click("Pause");
  await evaluate("[...document.querySelectorAll('.oh-card')].find(c => c.textContent.includes('Group the board')).click()");
  await wait("document.querySelector('.oh-detail')?.innerText.includes('Ready to check')", "check waiting");
  await click("Run 1 check");
  await click("Run them");
  await wait("document.querySelector('.oh-detail')?.innerText.includes('Verified: check')", "manual check result");
  await escape();

  // Projects: milestones contain epics; add one through the sheet.
  await tab("Projects");
  await wait("document.querySelector('.oh-milestone')");
  await click("Add epic");
  await fill("document.querySelector('.oh-detail input[required]')", "Knowledge links");
  await click("Add", "document.querySelector('.oh-detail')");
  await wait("[...document.querySelectorAll('.oh-epic-row b')].some(b => b.textContent === 'Knowledge links')", "new epic");
  const epic = (await api("read")).config.epics.find(item => item.title === "Knowledge links");
  assert.equal(epic.milestoneId, "PM1"); assert.equal(epic.projectId, "desktop");

  // Workers: the org chart opens an editor for persona and responsibilities.
  await tab("Workers");
  await wait("document.querySelector('.oh-node')");
  await evaluate("[...document.querySelectorAll('.oh-node')].find(b => b.textContent.includes('Sage Kim')).click()");
  await wait("document.querySelector('[aria-label=\"Add a responsibility\"]')");
  await fill("document.querySelector('[aria-label=\"Add a responsibility\"]')", "Keep knowledge fresh");
  await key("document.querySelector('[aria-label=\"Add a responsibility\"]')", "Enter");
  await wait("document.querySelector('.oh-dirty')");
  await evaluate("[...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Limit ticket actions')).querySelector('input').click()");
  await wait("document.querySelector('[aria-label=\"Create tickets in Raticode Desktop\"]')");
  await evaluate("(() => { const select = [...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Check-ins')).querySelector('select'); select.value = '3600'; select.dispatchEvent(new Event('change', {bubbles: true})); })()");
  for (const label of ["Create tickets in Raticode Desktop", "Edit tickets in Raticode Desktop", "Cancel tickets in Raticode Desktop", "Create tickets in Outside a project", "Edit tickets in Outside a project", "Move tickets in Outside a project", "Cancel tickets in Outside a project"]) await evaluate(`document.querySelector('[aria-label="${label}"]').click()`);
  await click("Save worker");
  await wait("document.querySelector('.oh-toasts').innerText.includes('Updated Sage Kim')");
  const sage = (await api("read")).config.employees.find(item => item.id === "sage");
  assert.deepEqual(sage.responsibilities, ["Cite sources", "Keep knowledge fresh"]);
  assert.deepEqual(sage.ticketAccess, { "*": [], desktop: ["move"], docs: ["create", "edit", "move", "cancel"] });
  assert.equal(sage.paused, true);
  assert.equal(sage.heartbeatSeconds, 3600);
  await escape();

  // The org chart zooms with Ctrl and the wheel or Ctrl with + and 0.
  await wait("document.querySelector('.oh-chart-wrap')");
  await evaluate("document.querySelector('.oh-chart-wrap').dispatchEvent(new WheelEvent('wheel', {deltaY: -120, ctrlKey: true, bubbles: true, cancelable: true}))");
  await wait("document.querySelector('.oh-zoom output').textContent === '112%'", "ctrl wheel zoom");
  await evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: '=', ctrlKey: true, bubbles: true, cancelable: true}))");
  await wait("Number.parseInt(document.querySelector('.oh-zoom output').textContent) > 112", "ctrl plus zoom");
  await evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: '0', ctrlKey: true, bubbles: true, cancelable: true}))");
  await wait("document.querySelector('.oh-zoom output').textContent === '100%'", "ctrl 0");

  // Knowledge: the graph opens notes; notes can be written and linked.
  await tab("Knowledge");
  await wait("document.querySelectorAll('.oh-graph .g-node').length >= 4", "graph nodes");
  await evaluate("[...document.querySelectorAll('.oh-graph .g-node')].find(g => g.getAttribute('aria-label')?.includes('five fixed columns')).dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'The board has five fixed columns'");
  text = await sheet();
  assert.ok(text.includes("STU-2") && text.includes("Quoted in"), text);
  await escape();
  await click("New note");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'New note'");
  await click("Back to Knowledge");
  await wait("!document.querySelector('.oh-detail') && Boolean(document.querySelector('.oh-kb-tools'))", "back to knowledge");
  await click("New note");
  await fill("document.querySelector('.oh-detail input[required]')", "Only you move work into Done");
  await fill("[...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Note')).querySelector('textarea')", "Workers propose; the owner accepts after review.");
  await click("Add note");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Only you move work into Done'");
  const notes = Object.values((await api("read")).runtime.knowledge);
  assert.ok(notes.some(item => item.title === "Only you move work into Done" && item.summary === "Workers propose; the owner accepts after review."));
  await escape();
  assert.ok(await evaluate("Boolean(document.querySelector('.oh-kb-tools'))"));
  await click("List");
  await wait("document.querySelector('.oh-kb-row')");
  assert.ok((await evaluate("document.querySelector('.oh-kb-list').innerText")).includes("Logs never include tokens"));

  // Find anything with Ctrl K.
  await evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true, cancelable: true}))");
  await wait("document.querySelector('.oh-palette input')");
  await fill("document.querySelector('.oh-palette input')", "STU-5");
  await wait("document.querySelector('.oh-palette [role=option]')");
  await key("document.querySelector('.oh-palette input')", "Enter");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Write the Organizations guide'");

  await escape();

  // Folders are added from the Projects tab; the shared repository from Organization.
  await tab("Projects");
  await click("Type a path");
  const extra = path.join(path.dirname(ready.project), "brain");
  await fill("document.querySelector('#oh-folder-path')", extra);
  await click("Add folder");
  await wait("document.querySelector('.oh-folders').innerText.includes('brain')", "added folder");
  assert.ok((await api("read")).config.projectRoots.includes(extra));
  await tab("Organization");
  await click("Turn on", "document.querySelector('.oh-repo')");
  await wait("document.querySelector('.oh-repo')?.innerText.includes('README.md')", "repository on");
  const shared = await api("repository");
  assert.equal(shared.exists, true);
  assert.ok(shared.commits.some(item => item[3].startsWith("Save decision: The board has five fixed columns")));

  // Backlog pulls: the seed turned them off so its Backlog stays put. The switch saves a
  // revision that says what changed. The organization is paused, so nothing is pulled.
  const pullState = "document.querySelector('.oh-pulls .oh-section-head .oh-muted')?.textContent";
  await wait(`${pullState} === 'Off'`, "backlog pulls off");
  await click("Turn on", "document.querySelector('.oh-pulls')");
  await wait(`${pullState} === 'On'`, "backlog pulls on");
  assert.equal((await api("read")).config.pullBacklog, true);
  assert.equal((await api("history"))[0].reason, "Workers pull Backlog tickets when they need work");
  await click("Turn off", "document.querySelector('.oh-pulls')");
  await wait(`${pullState} === 'Off'`, "backlog pulls off again");
  assert.equal((await api("read")).config.pullBacklog, false);

  // Working hours: closed hours show in the header and the panel, and 24/7 comes back.
  const clock = Date.now();
  const hhmm = hours => new Date(clock + hours * 3600000).toISOString().slice(11, 16);
  await click("Change", "document.querySelector('.oh-hours')");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Working hours'");
  await evaluate("[...document.querySelectorAll('.oh-detail .oh-radio')].find(l => l.textContent.startsWith('Only during')).querySelector('input').click()");
  await wait("document.querySelector('.oh-window')");
  await fill("[...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Time zone')).querySelector('input')", "UTC");
  for (const day of ["Sat", "Sun"]) await click(day, "document.querySelector('.oh-days')");
  await fill("document.querySelector('[aria-label=\"Start of hours 1\"]')", hhmm(2));
  await fill("document.querySelector('[aria-label=\"End of hours 1\"]')", hhmm(3));
  await evaluate("[...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Keep running after')).querySelector('input').click()");
  await click("Save hours");
  await wait("document.querySelector('.oh-hours-pill.closed')?.textContent.startsWith('Off hours until')", "off-hours pill");
  assert.ok((await evaluate("document.querySelector('.oh-hours').innerText")).includes("Closed now"));
  let saved = await api("read");
  assert.deepEqual(saved.config.operatingHours.windows, [{ days: [0, 1, 2, 3, 4, 5, 6], start: hhmm(2), end: hhmm(3) }]);
  assert.equal(saved.config.resumeAfterRestart, true);
  assert.equal(saved.diagnostics.hours.open, false);
  assert.equal((await api("history"))[0].reason, "Changed working hours");
  await click("Change", "document.querySelector('.oh-hours')");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Working hours'");
  await evaluate("[...document.querySelectorAll('.oh-detail .oh-radio')].find(l => l.textContent.startsWith('Around the clock')).querySelector('input').click()");
  await click("Save hours");
  await wait("!document.querySelector('.oh-hours-pill') && document.querySelector('.oh-hours')?.innerText.includes('24/7')", "back to 24/7");
  saved = await api("read");
  assert.equal(saved.config.operatingHours.mode, "always");

  // Work schedule: nothing is set until the user starts from the suggestion and saves it.
  await wait("document.querySelector('.oh-schedule')?.innerText.includes('Not set')", "schedule not set");
  await click("Start from the suggested schedule", "document.querySelector('.oh-schedule')");
  await wait("document.querySelector('.oh-detail h2')?.textContent === 'Work schedule'");
  assert.equal(await evaluate("document.querySelectorAll('.oh-block-row').length"), 7);
  const rows = await evaluate("[...document.querySelectorAll('.oh-block-toggle')].map(b => b.innerText.replace(/\\s+/g, ' ').trim())");
  assert.ok(rows.includes("Weekly release Fridays 13:00–17:00"), rows.join(" | "));
  assert.ok(rows.includes("Dependency updates and audit Monthly, 1st Monday, 09:00–12:00"), rows.join(" | "));
  await fill("[...document.querySelectorAll('.oh-detail label')].find(l => l.textContent.startsWith('Time zone')).querySelector('input')", "Asia/Tokyo");
  // Two daily blocks of the same length that overlap can't be saved.
  await click("Add block");
  await wait("document.querySelector('[aria-label=\"Start of block 8\"]')");
  await fill("document.querySelector('[aria-label=\"Start of block 8\"]')", "09:00");
  await fill("document.querySelector('[aria-label=\"End of block 8\"]')", "18:00");
  await wait("document.querySelector('.oh-schedule-form [role=alert]')?.textContent.includes('neither takes precedence')", "overlap refused");
  await click("Remove New block");
  await wait("!document.querySelector('.oh-schedule-form [role=alert]')", "overlap gone");
  await click("Save schedule");
  await wait("document.querySelector('.oh-schedule .oh-now b') && document.querySelector('.oh-block-pill')", "current block shown");
  saved = await api("read");
  assert.equal(saved.config.workSchedule.blocks.length, 7);
  assert.equal(saved.config.operatingHours.timezone, "Asia/Tokyo");
  assert.equal(saved.config.operatingHours.mode, "always");
  assert.equal(saved.diagnostics.schedule.timezone, "Asia/Tokyo");
  assert.equal(await evaluate("document.querySelector('.oh-schedule .oh-now b').textContent"), saved.diagnostics.schedule.current.label);
  assert.equal(await evaluate("document.querySelector('.oh-block-pill').textContent"), saved.diagnostics.schedule.current.label);
  assert.ok(await evaluate("document.querySelectorAll('.oh-day > span').length >= 4"));
  assert.equal(await evaluate("document.querySelectorAll('.oh-occasional li').length"), 3);
  assert.equal((await api("history"))[0].reason, "Changed working hours and the work schedule");

  // Settings keeps the full configuration, history and operations console.
  await click("Settings, history and operations");
  await wait("document.querySelector('.org-workspace')");
  await click("Close organization");
  await wait("document.querySelector('.oh-mission')");
  assert.equal((await api("audit_verify")).ok, true);

  const screenshot = await win.webContents.capturePage();
  fs.writeFileSync(path.join(os.tmpdir(), "organizations-home.png"), screenshot.toPNG());
  assert.deepEqual(errors.filter(message => !message.includes("Download the React DevTools")), []);
  console.log("Organizations home browser: overview, full-page details with Back and Escape, review with checked claims, board rules with In progress meaning next, projects, folders, worker permissions and check-ins, working hours, chart zoom, knowledge, search and the shared repository work against a real backend.");
}

main().then(() => { clearTimeout(timeout); win?.destroy(); vite?.close(); backend?.stdin.end(); backend?.kill(); app.exit(0); }, error => { console.error(error); clearTimeout(timeout); backend?.kill(); app.exit(1); });
