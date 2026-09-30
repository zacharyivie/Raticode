/* global __dirname, console, setTimeout, clearTimeout, process, fetch */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "organization-ui-profile-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 90000);
async function main() {
  backend = spawn(process.env.GOFER_TEST_PYTHON || path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/organization_browser_server.py")], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") } });
  backend.stderr.on("data", data => process.stderr.write(data));
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    backend.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) { try { resolve(JSON.parse(output.split("\n")[0])); } catch (error) { reject(error); } } });
    backend.on("exit", code => reject(new Error(`Backend exited: ${code}`)));
  });
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(root, "frontend"), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "organization-ui-test",
    resolveId(id) { if (["/organization-entry.jsx", "/organization-global-entry.jsx"].includes(id)) return id; },
    load(id) { if (id === "/organization-global-entry.jsx") return `import React, {useState} from "react"; import {createRoot} from "react-dom/client"; import OrganizationWorkspace from "/src/components/OrganizationWorkspace.jsx"; import OrganizationSidebar from "/src/components/OrganizationSidebar.jsx"; import "/src/styles/index.css";
      function App() { const [id,setId] = useState(null); const [root,setRoot] = useState(${JSON.stringify(ready.project)}); return <><select aria-label="Current project" value={root} onChange={e=>setRoot(e.target.value)}><option value=${JSON.stringify(ready.project)}>First project</option><option value=${JSON.stringify(path.join(path.dirname(ready.project), "other-project"))}>Second project</option><option value="">No project</option></select><main style={{height:"90vh",display:"flex"}}><aside style={{width:230,display:"flex"}}><OrganizationSidebar active selectedId={id} onSelect={setId}/></aside>{id && <OrganizationWorkspace key={id} rootPath={root} organizationId={id} onSelect={setId} onClose={()=>setId(null)} />}</main></>; } createRoot(document.getElementById("root")).render(<App/>);`;
      if (id === "/organization-entry.jsx") return `import React, {useState} from "react"; import {createRoot} from "react-dom/client"; import OrganizationWorkspace from "/src/components/OrganizationWorkspace.jsx"; import "/src/styles/index.css"; function App() { const [id,setId] = useState("new"); return <main style={{height:"100vh",display:"flex"}}><OrganizationWorkspace key={id} rootPath={${JSON.stringify(ready.project)}} organizationId={id} onSelect={setId} onClose={()=>{}} /></main>; } createRoot(document.getElementById("root")).render(<App/>);`; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url === "/organization-global-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/organization-global-test", '<html><body><div id="root"></div><script type="module" src="/organization-global-entry.jsx"></script></body></html>')); }
      if (request.url === "/organization-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/organization-test", '<html><body><div id="root"></div><script type="module" src="/organization-entry.jsx"></script></body></html>')); }
      if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end('{"providers":[]}'); }
      if (!request.url.startsWith("/api/organizations")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 1200, height: 900, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = []; win.webContents.on("console-message", details => { if (details.level === "error") errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/organization-test`);
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function wait(code) { const until = Date.now() + 8000; while (!await evaluate(code)) { if (Date.now() > until) throw new Error(`Missing: ${code}; UI: ${await evaluate("document.body.innerText")}`); await new Promise(resolve => setTimeout(resolve, 30)); } }
  if (process.env.RATICODE_TEST_ORGS_DISABLED === "1") {
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/organization-global-test`);
    await wait("document.body.innerText.includes('Organizations is experimental and disabled')");
    assert.equal(await evaluate("document.querySelector('[aria-label=\"New organization\"]').disabled"), true);
    assert.equal(await evaluate("document.querySelectorAll('.org-sidebar-list button').length"), 0);
    assert.equal(await evaluate("fetch('/api/organizations', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'create', params:{config:{name:'Blocked'}}})}).then(r=>r.status)"), 403);
    console.log("Organizations browser: default opt-out blocks creation and explains experimental access.");
    return;
  }
  async function click(text) { await wait(`Array.from(document.querySelectorAll('button')).some(b => (b.textContent === ${JSON.stringify(text)} || b.getAttribute("aria-label") === ${JSON.stringify(text)}) && !b.disabled)`); await evaluate(`Array.from(document.querySelectorAll('button')).find(b => (b.textContent === ${JSON.stringify(text)} || b.getAttribute("aria-label") === ${JSON.stringify(text)}) && !b.disabled).click()`); }
  async function field(label, value) { await evaluate(`(() => { const label = [...document.querySelectorAll('label')].find(l => l.querySelector('span')?.textContent === ${JSON.stringify(label)}); const input = label.querySelector('input,textarea'); input.focus(); Object.getOwnPropertyDescriptor(input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', {bubbles:true})); })()`); }
  await wait("document.querySelector('.org-form') !== null");
  await field("Organization name", "Launch studio"); await field("Purpose", "Build and review releases");
  await field("Maximum turns per task", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.textContent.startsWith('Maximum turns')).querySelector('input').value"), "");
  await field("Maximum turns per task", "25"); await evaluate("document.activeElement.blur()");
  await click("Create organization"); await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Launch studio'");
  await click("Employee"); await field("Employee name", "Mira"); await field("Title", "Release engineer"); await field("Employee instructions", "Implement assigned releases and attach test evidence.");
  await field("Heartbeat interval in seconds, 0 disables", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.textContent.startsWith('Heartbeat interval')).querySelector('input').value"), "");
  await field("Heartbeat interval in seconds, 0 disables", "0"); await evaluate("document.activeElement.blur()");
  await click("Save employee"); await wait("document.querySelector('.org-person strong')?.textContent === 'Mira'");
  await field("Assignment for Mira", "Verify the release checks"); await click("Assign work");
  await click("Start work"); await click("Work"); await wait("document.querySelector('.org-work-group')?.textContent.includes('In review')");
  await evaluate("document.querySelector('.org-task-open').click()"); await wait("document.querySelector('.org-conversation')?.textContent.includes('test provider')");
  // Saved comments appear in the retained task conversation.
  await field("Comment", "Release evidence reviewed");
  await click("Save comment");
  await wait("document.querySelector('.org-conversation')?.textContent.includes('Release evidence reviewed')");
  await click("Employees");
  await click("Employee"); await field("Employee name", "Theo"); await field("Title", "Quality engineer");
  await click("Save employee");
  await wait("document.querySelectorAll('.org-person').length === 2");
  await wait("document.querySelector('.org-profile h3')?.textContent === 'Theo'");
  await field("Assignment for Theo", "This draft belongs to Theo");
  await evaluate("[...document.querySelectorAll('.org-person')].find(b=>b.querySelector('strong').textContent==='Mira').click()");
  await wait("document.querySelector('.org-profile h3')?.textContent === 'Mira'");
  assert.equal(await evaluate("document.querySelector('.org-assignment textarea').value"), "");
  // Editing uses the full available panel, including while Rem occupies the rest of a desktop window.
  await click("Edit employee");
  assert.equal(await evaluate("document.querySelector('.org-inspector .org-form')"), null);
  await evaluate("document.querySelector('main').style.width = '420px'");
  await wait("getComputedStyle(document.querySelector('.org-form-grid')).gridTemplateColumns.split(' ').length === 1");
  await wait("document.querySelector('.model-picker-trigger') !== null");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.model-picker-trigger')).display"), "flex");
  assert.equal(await evaluate("document.querySelector('.org-scroll').scrollWidth > document.querySelector('.org-scroll').clientWidth + 1"), false);
  await evaluate("document.querySelector('main').style.width = ''");
  await click("Cancel");
  // New-task forms cannot be replaced accidentally by selecting another row underneath them.
  await click("Work"); await click("Back to work queue"); await click("New task");
  assert.equal(await evaluate("document.querySelectorAll('.org-task').length"), 0);
  await field("Task title", "Approval-gated follow-up");
  await evaluate("[...document.querySelectorAll('label')].find(l=>l.textContent.includes('Require approval')).querySelector('input').click()");
  await click("Save task"); await wait("document.querySelector('.org-conversation h3')?.textContent === 'Approval-gated follow-up'");
  await click("Back to work queue");
  await wait("document.querySelector('.org-work-group h4')?.textContent.includes('Needs approval')");
  await click("Approve plan");
  await wait("!document.querySelector('.org-work-group h4')?.textContent.includes('Needs approval')");
  await click("Activity"); await wait("document.querySelectorAll('.org-event').length > 0");
  assert.ok(await evaluate("document.querySelector('.org-activity').textContent.includes('Approved the plan')"));
  await click("Configuration history"); await wait("document.querySelectorAll('.org-revision').length === 3");
  await evaluate("[...document.querySelectorAll('.org-revision button')].at(-1).click()"); await wait("document.querySelector('.org-restore') !== null");
  assert.equal(await evaluate("document.activeElement.classList.contains('org-restore')"), true);
  await evaluate("document.querySelector('.org-restore .org-changes details').open = true");
  assert.ok(await evaluate("document.querySelector('.org-restore').innerText.includes('Previous value') && document.querySelector('.org-restore').innerText.includes('New value')"));
  await click("Restore and pause employees"); await wait("document.querySelector('.org-heading small')?.textContent.includes('Revision 4')");
  await click("Employees"); await wait("document.querySelectorAll('.org-person').length === 0");
  await click("Configuration history"); await wait("document.querySelectorAll('.org-revision').length === 4");
  assert.equal(await evaluate("document.querySelector('.org-toolbar .org-status').textContent"), "Paused");
  assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
  const artifacts = path.join(root, "frontend/artifacts/organizations"); fs.mkdirSync(artifacts, { recursive: true });
  fs.writeFileSync(path.join(artifacts, "history-desktop.png"), (await win.webContents.capturePage()).toPNG());
  win.setSize(440, 850); await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
  fs.writeFileSync(path.join(artifacts, "history-mobile.png"), (await win.webContents.capturePage()).toPNG());
  await evaluate("document.documentElement.classList.add('dark')");
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(await evaluate("document.querySelector('.org-scroll').scrollWidth > document.querySelector('.org-scroll').clientWidth + 1"), false);
  fs.writeFileSync(path.join(artifacts, "history-mobile-dark.png"), (await win.webContents.capturePage()).toPNG());
  // Reporting lines persist and drive the chart. Descendants cannot become managers.
  await click("Employees");
  async function select(label, name) {
    await evaluate(`(() => { const el = [...document.querySelectorAll('label')].find(l => l.querySelector('span')?.textContent === ${JSON.stringify(label)}).querySelector('select'); el.value = [...el.options].find(o => o.textContent === ${JSON.stringify(name)}).value; el.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  }
  for (const [name, manager] of [["Ada", "Organization owner"], ["Kai", "Ada"], ["Mira", "Kai"]]) {
    await click("Employee"); await field("Employee name", name); await select("Reports to", manager);
    await click("Save employee"); await wait(`document.querySelector('.org-profile h3')?.textContent === ${JSON.stringify(name)}`);
  }
  await click("Org chart");
  await wait("document.querySelector('[aria-label=\"Reports to Ada\"] [aria-label=\"Reports to Kai\"] strong')?.textContent === 'Mira'");
  await evaluate("[...document.querySelectorAll('.org-chart-person')].find(b=>b.querySelector('strong')?.textContent==='Ada').click()");
  await wait("document.querySelector('.org-profile h3')?.textContent === 'Ada'");
  await click("Edit employee");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Reports to').querySelector('select').textContent"), "Organization owner");
  await click("Cancel");
  // Counts open name-only directories. Each editor saves just one collection entry.
  await click("0 teams"); await click("Add team");
  await field("Team name", "Engineering"); await field("Team ID", "new"); await select("Team manager", "Kai");
  await field("Team description", "Build the release"); await click("Save team");
  await wait("document.querySelector('.org-directory')?.textContent.includes('Engineering')");
  assert.equal(await evaluate("document.querySelectorAll('.org-form').length"), 0);
  await click("Engineering");
  await wait("document.querySelector('.org-form h3')?.textContent === 'Engineering'");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.org-collection-people button')].map(b=>b.getAttribute('aria-label'))"), ["Edit employee: Kai", "Edit employee: Mira"]);
  await click("Edit employee: Mira"); await wait("document.querySelector('.org-form h3')?.textContent === 'Edit Mira'");
  await click("Cancel"); await click("1 team"); await click("Engineering");
  await field("Team name", "Unsaved name"); await click("Cancel"); await click("Engineering");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Team name').querySelector('input').value"), "Engineering");
  await field("Team description", ""); await field("Team description", "Ship reviewed releases"); await evaluate("document.activeElement.blur()");
  await click("Save team"); await wait("document.querySelector('.org-directory') !== null");
  await click("Add team"); await field("Team name", "Operations"); await click("Save team");
  await wait("document.querySelectorAll('.org-directory li').length === 2");
  await field("Find teams", "Engineering");
  assert.equal(await evaluate("document.querySelectorAll('.org-directory li').length"), 1);
  await field("Find teams", "");
  assert.equal(await evaluate("document.querySelectorAll('.org-directory li').length"), 2);
  await click("Employees"); await click("0 initiatives"); await click("Add initiative");
  await field("Initiative name", "Desktop release"); await select("Initiative owner", "Mira");
  await field("Initiative description", "Signed installers"); await click("Save initiative");
  await wait("document.querySelector('.org-directory')?.textContent.includes('Desktop release')");
  await click("Back to org chart");
  await wait("document.querySelector('.org-chart-canvas') !== null");
  assert.equal(await evaluate("document.querySelector('.org-chart-view button[aria-pressed=true]').textContent"), "People");
  assert.equal(await evaluate("document.querySelectorAll('.org-chart-team').length"), 0);
  await evaluate("[...document.querySelectorAll('.org-chart-view button')].find(b=>b.textContent==='Teams').click()");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.org-chart-team[aria-label=\"Team: Engineering\"] .org-chart-person strong')].map(e=>e.textContent)"), ["Kai", "Mira"]);
  assert.equal(await evaluate("document.querySelector('.org-chart-team[aria-label=\"No team\"] strong').textContent"), "Ada");
  assert.ok(await evaluate("[...document.querySelectorAll('.org-chart-node')].find(n=>n.querySelector('strong')?.textContent==='Mira').textContent.includes('Desktop release')"));
  await click("Team: Engineering");
  await wait("document.querySelector('.org-form h3')?.textContent === 'Engineering'");
  assert.equal(await evaluate("document.querySelector('.org-form textarea').value"), "Ship reviewed releases");
  await click("Back to teams"); await click("Back to org chart"); await click("Initiative: Desktop release");
  await wait("document.querySelector('.org-form h3')?.textContent === 'Desktop release'");
  assert.equal(await evaluate("document.querySelector('.org-form textarea').value"), "Signed installers");
  assert.equal(await evaluate("document.querySelector('.org-collection-people small').textContent"), "Initiative owner");
  await click("Edit employee: Mira"); await wait("document.querySelector('.org-form h3')?.textContent === 'Edit Mira'");
  await click("Cancel");
  await click("Organization settings");
  assert.equal(await evaluate("[...document.querySelectorAll('summary')].some(el=>el.textContent==='Projects and teams')"), false);
  await click("Cancel"); await click("Org chart");
  for (const [width, dark] of [[1200, false], [440, false], [440, true]]) {
    win.setSize(width, 900); await evaluate(`document.documentElement.classList.toggle('dark', ${dark})`);
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(await evaluate("document.querySelector('.org-scroll').scrollWidth > document.querySelector('.org-scroll').clientWidth + 1"), false);
    if (width === 440) {
      await evaluate("[...document.querySelectorAll('.org-chart-view button')].find(b=>b.textContent==='Teams').click()");
      const point = await evaluate("(() => { const canvas = document.querySelector('.org-chart-canvas'); canvas.scrollLeft = 0; const r = canvas.getBoundingClientRect(); return { x: Math.round(r.right - 35), y: Math.round(r.top + 12) }; })()");
      win.webContents.sendInputEvent({ type: "mouseDown", ...point, button: "left", clickCount: 1 });
      win.webContents.sendInputEvent({ type: "mouseMove", x: point.x - 150, y: point.y, button: "left", modifiers: ["leftButtonDown"] });
      win.webContents.sendInputEvent({ type: "mouseUp", x: point.x - 150, y: point.y, button: "left", clickCount: 1 });
      await wait("document.querySelector('.org-chart-canvas').scrollLeft > 80");
      assert.ok(await evaluate("document.querySelector('.org-chart-canvas') !== null"));
    }
    fs.writeFileSync(path.join(artifacts, `chart-${width}-${dark ? 'dark' : 'light'}.png`), (await win.webContents.capturePage()).toPNG());
  }
  // The same list and organization stay available across project changes and no-project scope.
  win.setSize(1200, 900);
  const otherProject = path.join(path.dirname(ready.project), "other-project");
  const thirdProject = path.join(path.dirname(ready.project), "third-project");
  await evaluate(`fetch('/api/organizations', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'create', params:{config:{name:'Other studio', projectRoots:[${JSON.stringify(otherProject)}]}}})}).then(async r => {if (!r.ok) throw new Error(await r.text());})`);
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/organization-global-test`);
  await wait("document.querySelectorAll('.org-sidebar-list button').length === 2");
  await evaluate("[...document.querySelectorAll('.org-sidebar-list button')].find(b=>b.title==='Launch studio').click()");
  await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Launch studio'");
  for (const current of [otherProject, ""]) {
    await evaluate(`(() => {const select = document.querySelector('[aria-label="Current project"]'); select.value=${JSON.stringify(current)}; select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    assert.equal(await evaluate("document.querySelectorAll('.org-sidebar-list button').length"), 2);
    assert.equal(await evaluate("document.querySelector('.org-toolbar h2').textContent"), "Launch studio");
  }
  await click("Projects");
  await wait("document.querySelector('.org-directory button') !== null");
  assert.ok(await evaluate(`document.querySelector('.org-directory').textContent.includes(${JSON.stringify(ready.project)})`));
  await click("Assign project"); await field("Project folder", otherProject); await click("Assign project and pause");
  await wait("document.querySelector('[role=alert]')?.textContent.includes('already owned by Other studio')");
  await field("Project folder", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Project folder').querySelector('input').value"), "");
  await field("Project folder", thirdProject); await evaluate("document.activeElement.blur()"); await click("Assign project and pause");
  await wait("document.querySelectorAll('.org-directory li').length === 2");
  await evaluate("document.querySelectorAll('.org-directory button')[1].click()"); await click("Use as default");
  await wait(`document.querySelector('.org-directory li')?.textContent.includes(${JSON.stringify(thirdProject)})`);
  await evaluate("[...document.querySelectorAll('.org-sidebar-list button')].find(b=>b.title==='Other studio').click()");
  await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Other studio'");
  await evaluate("[...document.querySelectorAll('.org-sidebar-list button')].find(b=>b.title==='Launch studio').click()");
  await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Launch studio'");
  await click("Projects"); await wait("document.querySelectorAll('.org-directory li').length === 2");
  assert.ok(await evaluate(`document.querySelector('.org-directory li').textContent.includes(${JSON.stringify(thirdProject)})`));
  fs.writeFileSync(path.join(artifacts, "owned-projects-desktop.png"), (await win.webContents.capturePage()).toPNG());
  await click("New organization"); await field("Organization name", "Unassigned studio"); await click("Create organization");
  await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Unassigned studio'");
  await click("Projects"); await wait("document.querySelector('.org-empty h3')?.textContent === 'No projects assigned'");
  await wait("document.querySelectorAll('.org-sidebar-list button').length === 3");
  // New operating controls use the same real backend, without live provider calls.
  await evaluate("[...document.querySelectorAll('.org-sidebar-list button')].find(b=>b.title==='Launch studio').click()");
  await wait("document.querySelector('.org-toolbar h2')?.textContent === 'Launch studio'");
  await click("Operations");
  await field("Assumed seconds per turn", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Assumed seconds per turn').querySelector('input').value"), "");
  await field("Assumed seconds per turn", "15"); await evaluate("document.activeElement.blur()");
  await click("Rehearse"); await wait("document.querySelector('.org-operation-table') !== null");
  await evaluate("[...document.querySelectorAll('summary')].find(s=>s.textContent==='Add a routine').click()");
  await field("Routine name", "Daily review"); await field("Task title", "Review routine output");
  await field("Cron schedule, blank for API only", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Cron schedule, blank for API only').querySelector('input').value"), "");
  await field("Cron schedule, blank for API only", "0 9 * * 1-5"); await evaluate("document.activeElement.blur()");
  await click("Save routine"); await wait("document.querySelector('.org-routine strong')?.textContent === 'Daily review'");
  await click("Queue occurrence");
  await wait("document.querySelector('.org-routine').textContent.includes('Previous task: todo')");
  await click("Refresh outcomes");
  await wait("document.querySelector('.org-operations').textContent.includes('Cost per accepted result')");
  await evaluate("[...document.querySelectorAll('summary')].find(s=>s.textContent==='Add or edit a destination').click()");
  await field("Destination ID", "bounded-team"); await field("Swarm ID", "fixture-swarm");
  await evaluate("document.querySelector('.org-operations fieldset input[type=checkbox]').click()");
  await field("Reserved provider turns", "");
  assert.equal(await evaluate("[...document.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent==='Reserved provider turns').querySelector('input').value"), "");
  await field("Reserved provider turns", "3"); await evaluate("document.activeElement.blur()");
  await click("Save destination");
  await wait("[...document.querySelectorAll('.org-routine strong')].some(s=>s.textContent==='bounded-team')");
  assert.ok(await evaluate("document.querySelector('.org-operations').textContent.includes('3 reserved turns')"));
  await click("Revoke destination");
  await wait("![...document.querySelectorAll('.org-routine strong')].some(s=>s.textContent==='bounded-team')");
  await evaluate("document.querySelector('.org-scroll').scrollTop = 0");
  await new Promise(resolve => setTimeout(resolve, 100));
  fs.writeFileSync(path.join(artifacts, "operations-desktop.png"), (await win.webContents.capturePage()).toPNG());
  win.setSize(440, 900);
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(await evaluate("document.querySelector('.org-scroll').scrollWidth > document.querySelector('.org-scroll').clientWidth + 1"), false);
  fs.writeFileSync(path.join(artifacts, "operations-mobile.png"), (await win.webContents.capturePage()).toPNG());
  win.setSize(1200, 900);
  await click("Work");
  if (await evaluate("!!document.querySelector('.org-conversation')")) await click("Back to work queue");
  await field("Search work", "routine output");
  assert.equal(await evaluate("document.querySelectorAll('.org-task').length"), 1);
  await field("Search work", "");
  await click("New task"); await field("Task title", "Review acceptance test");
  await field("Evidence", "Saved test evidence");
  await select("Status", "In review");
  await evaluate("[...document.querySelectorAll('label')].find(l=>l.textContent.includes('Require independent completion review')).querySelector('input').click()");
  await click("Save task");
  await wait("document.querySelector('.org-conversation h3')?.textContent === 'Review acceptance test'");
  await field("Review reason", "Checked the result independently"); await click("Accept result");
  await wait("document.querySelector('.org-conversation .org-status')?.textContent === 'Done'");
  assert.deepEqual(errors, []);
  console.log("Organizations browser: real backend create, employee, draft inputs, task execution, conversation, history, restore, org chart, initiatives, global organizations and project ownership passed.");
}
main().then(async () => { clearTimeout(timeout); backend?.kill(); await vite?.close(); win?.destroy(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); backend?.kill(); await vite?.close(); app.exit(1); });
