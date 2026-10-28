/* global __dirname, console, process, setTimeout, clearTimeout, fetch */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "brain-browser-profile-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 120000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  backend = spawn(process.env.GOFER_TEST_PYTHON || path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/second_brain_browser_server.py")], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") } });
  backend.stderr.on("data", data => process.stderr.write(data));
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    backend.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) { try { resolve(JSON.parse(output.split("\n")[0])); } catch (error) { reject(error); } } });
    backend.on("exit", code => reject(new Error(`Backend exited: ${code}`)));
  });
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(root, "frontend"), logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "second-brain-api-test",
    resolveId(id) { if (id === "/brain-api-entry.jsx") return id; },
    load(id) { if (id === "/brain-api-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import SecondBrainWorkspace from "/src/components/SecondBrainWorkspace.jsx"; import SecondBrainSidebar from "/src/components/SecondBrainSidebar.jsx"; import * as store from "/src/lib/secondBrainStore.js"; import "/src/styles/index.css"; window.__sb=store; createRoot(document.getElementById("root")).render(<main style={{height:"100vh",display:"flex"}}><aside style={{width:260,display:"flex"}}><SecondBrainSidebar active open onOpen={()=>{}}/></aside><section style={{flex:1,minWidth:0,display:"flex"}}><SecondBrainWorkspace assistant={{provider:"codex",model:"test-model"}} onClose={()=>{}}/></section></main>);`; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url === "/brain-api-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/brain-api-test", '<html><body><div id="root"></div><script type="module" src="/brain-api-entry.jsx"></script></body></html>')); }
      if (request.url.startsWith("/api/provider/capabilities")) {
        response.setHeader("Content-Type", "application/json");
        return response.end(JSON.stringify({providers: ["codex", "claude_code", "cursor", "copilot", "opencode", "grok", "antigravity"].map(id => ({id, displayName:id, available:true, enabled:true, defaultModel:`${id}-model`, models:[{id:`${id}-model`, defaultEffort:"medium", efforts:[{id:"medium"},{id:"high"}]}]}))}));
      }
      if (!request.url.startsWith("/api/second-brains")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer second-brain-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  const url = `http://127.0.0.1:${vite.httpServer.address().port}/brain-api-test`;
  win = new BrowserWindow({ width: 1360, height: 900, show: true, webPreferences: { contextIsolation: true, nodeIntegration: false } });
  const errors = []; win.webContents.on("console-message", details => { if (details.level === "error") { errors.push(details.message); console.error(details.message); } });
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function wait(code) { const until = Date.now() + 15000; while (!await evaluate(code)) { if (Date.now() > until) throw new Error(`Missing ${code}. UI: ${await evaluate("document.body.innerText")}`); await sleep(50); } }
  async function click(text) { const controls = "(document.querySelector('.sb-modal')||document).querySelectorAll('button')"; await wait(`Array.from(${controls}).some(b=>(b.textContent.trim()===${JSON.stringify(text)}||b.getAttribute('aria-label')===${JSON.stringify(text)})&&!b.disabled)`); await evaluate(`Array.from(${controls}).find(b=>(b.textContent.trim()===${JSON.stringify(text)}||b.getAttribute('aria-label')===${JSON.stringify(text)})&&!b.disabled).click()`); }
  async function field(label, value) { await evaluate(`(()=>{const l=[...document.querySelectorAll('label')].find(el=>el.querySelector('span')?.textContent===${JSON.stringify(label)});const i=document.querySelector('[aria-label='+JSON.stringify(${JSON.stringify(label)})+']')||l?.querySelector('input,textarea');if(!i)throw new Error('Missing field '+${JSON.stringify(label)});i.focus();Object.getOwnPropertyDescriptor(i.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(i,${JSON.stringify(value)});i.dispatchEvent(new Event('input',{bubbles:true}));i.blur();})()`); }
  async function select(label, value) { await evaluate(`(()=>{const l=[...document.querySelectorAll('label')].find(el=>el.querySelector('span')?.textContent===${JSON.stringify(label)});const s=l?.querySelector('select');if(!s)throw new Error('Missing select '+${JSON.stringify(label)});s.value=${JSON.stringify(value)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`); }
  async function chooseProvider(id) {
    await wait("document.querySelector('[data-picker-trigger=provider]')!==null");
    await evaluate("(document.querySelector('.sb-modal')||document).querySelector('[data-picker-trigger=provider]').click()");
    await evaluate(`(()=>{const option=[...document.querySelectorAll('[data-picker-menu=provider] [role=option]')].find(b=>b.querySelector('span')?.textContent===${JSON.stringify(id)});if(!option||option.disabled)throw new Error('Provider unavailable');option.click();})()`);
  }
  await win.loadURL(url);
  await wait("__sb.getState().loaded");
  assert.equal(await evaluate("__sb.getState().brains.length"), 0);
  await click("Open existing folder"); await field("Folder", ready.root); await click("Open brain");
  await wait("__sb.getState().documents.length===2");
  await evaluate("__sb.updateSearch({text:'brain:\"'+__sb.getState().brains[0].name+'\"'})");
  await wait("document.querySelector('.sb-results-head strong')?.textContent==='2 documents'");
  await evaluate("__sb.updateSearch({text:'exact observations'})");
  await wait("document.querySelector('.sb-results-head strong')?.textContent==='1 document'");
  const documentId = await evaluate("__sb.getState().documents.find(doc=>doc.path==='evidence.md').id");
  await evaluate(`__sb.openDocument(${JSON.stringify(documentId)},{preview:false})`);
  await wait("document.querySelector('.sb-reading')?.textContent.includes('exact observations') || document.querySelector('.sb-reader')?.textContent.includes('exact observations')");
  await evaluate(`(()=>{const paragraphs=[...document.querySelectorAll('.sb-doc [data-block] p')].filter(p=>p.textContent.includes('exact observations')||p.textContent.includes('additional context'));if(paragraphs.length!==2)throw new Error('Expected two paragraphs');const range=document.createRange();range.setStartBefore(paragraphs[0].firstChild);range.setEndAfter(paragraphs[1].lastChild);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.querySelector('.sb-reader-body').dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));})()`);
  await wait("document.querySelector('.sb-selection-bar')?.textContent.includes('2 exact passage citations')");
  await click("Save citation");
  await evaluate("__sb.flushLibraryWrites()");
  await wait("__sb.getState().citations.length===2");
  assert.equal(await evaluate("new Set(__sb.getState().citations.map(c=>c.blockId)).size"),2);
  await evaluate(`(()=>{const doc=__sb.documentById(__sb.getState(),${JSON.stringify(documentId)});const block=doc.blocks.find(block=>block.text.includes('exact observations'));__sb.saveCitation({docId:doc.id,blockId:block.id,quote:'exact observations',note:'Desktop citation'});})()`);
  await evaluate("__sb.flushLibraryWrites()");
  await win.loadURL(url); await wait("window.__sb && __sb.getState().citations.some(citation=>citation.note==='Desktop citation')");
  await evaluate("__sb.navigate('settings')");
  await wait("document.querySelector('.sb-settings')!==null");
  await evaluate("[...document.querySelectorAll('.sb-settings label')].find(l=>l.textContent.includes('Allow cloud processing')).querySelector('input').click()");
  await evaluate("__sb.flushLibraryWrites()");
  await evaluate("(()=>{const docs=__sb.getState().documents;const collection=__sb.createCollection('Desktop research',docs.map(doc=>__sb.evidenceItem(doc.id)));window.__collection=collection.id;__sb.navigate('collections',{collectionId:collection.id,collectionTab:'compare'});})()");
  await field("Question", "Compare observations"); await chooseProvider("cursor"); await click("Build comparison");
  await wait("__sb.getState().collections.find(c=>c.id===window.__collection)?.comparison?.rows.length===1");
  await evaluate(`__sb.openDialog('report-builder',{sources:[{docId:${JSON.stringify(documentId)}}],question:'What observations were recorded?'})`);
  await chooseProvider("grok");
  await evaluate("document.querySelector('.sb-modal [data-picker-trigger=effort]').click()");
  await evaluate("[...document.querySelectorAll('[data-picker-menu=effort] [role=option]')].find(b=>b.textContent.trim().toLowerCase()==='high').click()");
  await click("Generate report");
  await wait("__sb.getState().reports.length===1");
  assert.equal(await evaluate("__sb.getState().reports[0].provider"), "grok");
  assert.equal(await evaluate("__sb.getState().reports[0].model"), "grok-model");
  assert.equal(await evaluate("__sb.getState().reports[0].effort"), "high");
  await wait("document.querySelector('.sb-report-body')?.textContent.includes('Evidence was read')");
  await click("Mark support reviewed"); await click("Claims");
  await evaluate("document.querySelector('.sb-claim-row input').click()");
  await click("Publish reviewed report"); await evaluate("__sb.flushLibraryWrites()");
  await wait("__sb.getState().reports[0].draft===false");
  await evaluate("__sb.navigate('inbox');__sb.openDialog('capture',{mode:'excerpt'})");
  await field("Title ", "Desktop capture"); await field("Excerpt", "A captured source from the desktop test."); await click("Save to Inbox");
  await wait("__sb.getState().documents.some(doc=>doc.title==='Desktop capture'&&doc.inbox)");
  await evaluate("__sb.openDialog('export')"); await field("Save package as", ready.export); await click("Export");
  await wait("__sb.getState().tasks.some(task=>task.kind==='export'&&task.state==='done')");
  assert.ok(fs.existsSync(ready.export));
  await evaluate("__sb.openDialog('import')"); await field("Package", ready.export); await click("Preview import");
  await wait("document.querySelector('.sb-import-preview')!==null");
  await field("Destination folder", ready.import); await click("Import");
  await wait("__sb.getState().brains.length===2");
  await win.loadURL(url); await wait("window.__sb && __sb.getState().brains.length===2&&__sb.getState().reports.length===2");
  await evaluate("__sb.navigate('settings')");
  await chooseProvider("opencode");
  await evaluate("__sb.flushLibraryWrites()");
  assert.equal(await evaluate("__sb.getState().brains.find(b=>b.id===__sb.getState().activeBrainId).settings.researchProvider.id"), "opencode");
  await evaluate("__sb.navigate('jobs')");
  await click("New job"); await chooseProvider("copilot"); await click("Create job");
  await evaluate("__sb.flushLibraryWrites()");
  assert.equal(await evaluate("__sb.getState().jobs.find(j=>j.brainId===__sb.getState().activeBrainId).provider.id"), "copilot");
  await evaluate("document.querySelector('.sb-job input[role=switch]').click()");
  await evaluate("__sb.flushLibraryWrites()");
  assert.equal(await evaluate("__sb.getState().jobs.find(j=>j.brainId===__sb.getState().activeBrainId).provider.id"), "copilot");
  await evaluate("__sb.openDialog('summarize',{docIds:[__sb.getState().documents.find(d=>d.brainId===__sb.getState().activeBrainId&&d.path==='evidence.md').id]})");
  await chooseProvider("antigravity"); await click("Summarize with Rem");
  await wait("__sb.getState().reports.some(r=>r.provider==='antigravity')");
  assert.equal(await evaluate("__sb.getState().reports.find(r=>r.provider==='antigravity').model"), "antigravity-model");
  await win.loadURL(url); await wait("window.__sb && __sb.getState().loaded");
  assert.equal(await evaluate("__sb.getState().brains.find(b=>b.id===__sb.getState().activeBrainId).settings.researchProvider.id"), "opencode");
  await evaluate("__sb.navigate('settings')");
  await evaluate("[...document.querySelectorAll('.sb-settings label')].find(l=>l.textContent.includes('Allow cloud processing')).querySelector('input').click()");
  await evaluate("__sb.flushLibraryWrites()");
  await evaluate("__sb.openDialog('summarize',{docIds:[__sb.getState().documents.find(d=>d.brainId===__sb.getState().activeBrainId&&d.path==='evidence.md').id]})");
  assert.equal(await evaluate("[...document.querySelectorAll('.sb-modal button')].find(b=>b.textContent.trim()==='Summarize with Rem').disabled"), false);
  assert.equal(await evaluate("document.querySelector('.sb-modal').textContent.includes('Cloud models can’t read')"), false);
  await chooseProvider("cursor"); await click("Summarize with Rem");
  await wait("__sb.getState().reports.some(r=>r.provider==='cursor')");
  assert.equal(await evaluate("__sb.getState().brains.find(b=>b.id===__sb.getState().activeBrainId).settings.allowCloud"), false);
  await evaluate("__sb.openDialog('summarize',{docIds:[__sb.getState().documents.find(d=>d.brainId===__sb.getState().activeBrainId&&d.path==='evidence.md').id]})");
  await chooseProvider("OpenAI API");
  await wait("document.querySelector('.sb-modal input[aria-label=\"Research model\"]')!==null");
  await evaluate("(()=>{const input=document.querySelector('.sb-modal input[aria-label=\"Research model\"]');input.focus();Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));})()");
  assert.equal(await evaluate("document.querySelector('.sb-modal input[aria-label=\"Research model\"]').value"), "");
  await win.webContents.insertText("custom-local-model");
  await evaluate("document.querySelector('.sb-modal input[aria-label=\"Research model\"]').blur()");
  await click("Summarize with Rem");
  await wait("__sb.getState().reports.some(r=>r.model==='custom-local-model')");
  assert.equal(await evaluate("__sb.getState().reports.find(r=>r.model==='custom-local-model').provider"), "openai_api");
  await evaluate("__sb.openDialog('report-builder',{})");
  assert.equal(await evaluate("document.querySelector('.sb-modal').textContent.includes('Include previous Second Brain outputs')"), true);
  assert.equal(await evaluate("document.querySelector('.sb-modal').textContent.includes('including AI-written files')"), true);
  await click("Cancel");
  await evaluate("__sb.openDialog('open-folder',{})");
  await field("Folder", ready.largeRoot); await click("Open brain");
  await wait("__sb.getState().documents.filter(d=>d.brainId===__sb.getState().activeBrainId).length===347");
  await evaluate("__sb.openDialog('report-builder',{})");
  assert.equal(await evaluate("document.querySelector('.sb-source-picker')===null"), true);
  await field("Question or brief", "What evidence does Raticode preserve?");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='15 sources selected from 347 eligible documents'");
  assert.equal(await evaluate("document.querySelector('.sb-auto-sources').textContent.includes('Cooking')"), false);
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Top matches\"]').value"), "");
  await field("Minimum topic match", "100");
  await wait("document.querySelector('.sb-auto-sources').textContent.includes('No eligible sources meet')");
  await field("Minimum topic match", "60");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='15 sources selected from 347 eligible documents'");
  await field("Top matches", "5");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='5 sources selected from 347 eligible documents'");
  await evaluate("(()=>{const input=document.querySelector('[aria-label=\"Top matches\"]');input.focus();Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));})()");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Top matches\"]').value"), "");
  await win.webContents.insertText("3");
  await evaluate("document.querySelector('[aria-label=\"Top matches\"]').blur()");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='3 sources selected from 347 eligible documents'");
  await field("Top matches", "0");
  assert.equal(await evaluate("[...document.querySelectorAll('.sb-modal button')].find(b=>b.textContent.trim()==='Generate report').disabled"), true);
  await field("Top matches", "");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='15 sources selected from 347 eligible documents'");
  if (process.env.GOFER_TEST_REPORT_SOURCE_SCREENSHOT) fs.writeFileSync(process.env.GOFER_TEST_REPORT_SOURCE_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
  await click("Choose manually");
  await wait("document.querySelector('.sb-source-picker')!==null");
  assert.equal(await evaluate("document.querySelectorAll('.sb-source-picker input:checked').length"), 15);
  await click("Clear selection");
  await field("Filter sources", "unrelated-016");
  assert.equal(await evaluate("document.querySelectorAll('.sb-source-picker label').length"), 1);
  await evaluate("document.querySelector('.sb-source-picker input').click()");
  assert.equal(await evaluate("document.querySelector('.sb-source-picker legend').textContent"), "Sources · 1 included");
  await click("Choose automatically");
  await field("Question or brief", "Quantum bananas");
  await wait("document.querySelector('.sb-auto-sources').textContent.includes('No eligible sources meet')");
  assert.equal(await evaluate("[...document.querySelectorAll('.sb-modal button')].find(b=>b.textContent.trim()==='Generate report').disabled"), true);
  await field("Question or brief", "What evidence does Raticode preserve?");
  await wait("document.querySelector('.sb-auto-sources h4')?.textContent==='15 sources selected from 347 eligible documents'");
  await chooseProvider("cursor"); await click("Generate report");
  await wait("__sb.getState().reports.some(r=>r.brainId===__sb.getState().activeBrainId&&r.sourceSelection?.mode==='auto')");
  assert.equal(await evaluate("__sb.getState().reports.find(r=>r.brainId===__sb.getState().activeBrainId&&r.sourceSelection?.mode==='auto').sourceVersions.length"), 15);
  assert.equal(await evaluate("__sb.getState().tasks.filter(t=>t.kind==='report'&&t.brainId===__sb.getState().activeBrainId).at(-1).request.sourceSelection"), "auto");
  assert.equal(await evaluate("__sb.getState().reports.find(r=>r.brainId===__sb.getState().activeBrainId&&r.sourceSelection?.mode==='auto').sourceSelection.minimumRelevance"), 60);
  assert.equal(await evaluate("__sb.getState().reports.find(r=>r.brainId===__sb.getState().activeBrainId&&r.sourceSelection?.mode==='auto').sourceSelection.limit"), null);
  await click("Provenance");
  await wait("document.querySelector('.sb-provenance')?.textContent.includes('Local search selected 15 of 347 eligible documents')");
  await evaluate("__sb.navigate('jobs')"); await click("New job");
  await field("Name", "Hourly topic summaries");
  await field("Topic or query", "Raticode");
  await select("Time rule", "window"); await select("Time unit", "hours");
  await evaluate("(()=>{const input=document.querySelector('[aria-label=\"Look-back amount\"]');input.focus();Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));})()");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Look-back amount\"]').value"), "");
  await win.webContents.insertText("24");
  await evaluate("document.querySelector('[aria-label=\"Look-back amount\"]').blur()");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Look-back amount\"]').value"), "24");
  await field("Most documents per run", "2"); await field("Most Rem turns per run", "2");
  await click("Preview matching sources");
  await wait("document.querySelector('.sb-job-source-preview')?.textContent.includes('15 eligible sources. The next run can process 2; 13 remain')");
  if (process.env.GOFER_TEST_JOB_SOURCE_SCREENSHOT) { await evaluate("document.querySelector('.sb-job-source-rules').scrollIntoView({block:'start'})"); fs.writeFileSync(process.env.GOFER_TEST_JOB_SOURCE_SCREENSHOT, (await win.webContents.capturePage()).toPNG()); }
  await chooseProvider("cursor"); await click("Create job"); await evaluate("__sb.flushLibraryWrites()");
  await wait("__sb.getState().jobs.some(j=>j.name==='Hourly topic summaries'&&j.sourceRules.time.count===24)");
  const ruleJobId = await evaluate("__sb.getState().jobs.find(j=>j.name==='Hourly topic summaries').id");
  await win.loadURL(url); await wait("window.__sb&&__sb.getState().loaded");
  await evaluate("__sb.navigate('jobs')");
  await wait("document.querySelector('.sb-job-facts')?.textContent.includes('last 24 hours')");
  await click("Edit");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Look-back amount\"]').value"), "24");
  await select("Time rule", "since-success"); await click("Save job"); await evaluate("__sb.flushLibraryWrites()");
  assert.equal(await evaluate(`__sb.getState().jobs.find(j=>j.id===${JSON.stringify(ruleJobId)}).sourceRules.time.mode`), "since-success");
  await evaluate("__sb.updateBrain(__sb.getState().activeBrainId,b=>({settings:{...b.settings,allowCloud:true}}))");
  await evaluate("__sb.flushLibraryWrites()"); await click("Run now");
  await wait(`__sb.getState().jobs.find(j=>j.id===${JSON.stringify(ruleJobId)})?.lastRun?.processedCount===2`);
  assert.equal(await evaluate(`__sb.getState().jobs.find(j=>j.id===${JSON.stringify(ruleJobId)}).lastRun.remainingCount`), 13);
  assert.equal(await evaluate(`Boolean(__sb.getState().jobs.find(j=>j.id===${JSON.stringify(ruleJobId)}).lastSuccessfulRunAt)`), false);
  await evaluate("__sb.navigate('jobs')");
  await wait("document.querySelector('.sb-job').textContent.includes('13 sources waiting for a later run')");
  await evaluate("__sb.navigate('health')");
  await wait("document.querySelectorAll('.sb-health-rows > li').length===15");
  const healthCount = await evaluate("(()=>{const state=__sb.getState();const ids=new Set(state.documents.filter(d=>d.brainId===state.activeBrainId).map(d=>d.id));return state.health.filter(issue=>ids.has(issue.docId)).length;})()");
  assert.ok(healthCount > 45);
  assert.equal(await evaluate("Number(document.querySelector('.sb-kpis button strong').textContent)"), healthCount);
  assert.equal(await evaluate("document.querySelector('.sb-health-pagination [role=status]').textContent"), `Showing 15 of ${healthCount} problems`);
  await evaluate("document.querySelector('.sb-health-rows input').click()");
  for (const count of [30, 45]) {
    await evaluate("document.querySelector('.sb-health-pagination button').scrollIntoView({block:'end'})");
    await wait(`document.querySelectorAll('.sb-health-rows > li').length===${count}`);
    await sleep(100);
    assert.equal(await evaluate("document.querySelectorAll('.sb-health-rows > li').length"), count);
    assert.equal(await evaluate("document.querySelector('.sb-health-rows input').checked"), true);
  }
  // Filtering restarts pagination at the top while preserving selected problems.
  await evaluate("[...document.querySelectorAll('.sb-kpis button')].find(b=>b.textContent.includes('Missing metadata')).click()");
  await wait("document.querySelectorAll('.sb-health-rows > li').length===15");
  assert.equal(await evaluate("document.querySelector('.sb-health-rows').closest('.sb-page').scrollTop"), 0);
  await evaluate("document.querySelector('.sb-kpis button').click()");
  await wait("document.querySelectorAll('.sb-health-rows > li').length===15");
  assert.equal(await evaluate("document.querySelector('.sb-health-rows input').checked"), true);
  await click("Ignore"); await evaluate("__sb.flushLibraryWrites()");
  await wait(`Number(document.querySelector('.sb-kpis button strong').textContent)===${healthCount - 1}`);
  assert.equal(await evaluate("document.querySelectorAll('.sb-health-rows > li').length"), 15);
  assert.equal(await evaluate("document.querySelector('.sb-bulk')===null"), true);
  // The final batch is partial and stops observing once every problem is rendered.
  for (let count = 15; count < healthCount - 1;) {
    count = Math.min(count + 15, healthCount - 1);
    await evaluate("document.querySelector('.sb-health-pagination button').scrollIntoView({block:'end'})");
    await wait(`document.querySelectorAll('.sb-health-rows > li').length===${count}`);
  }
  assert.equal(await evaluate("document.querySelector('.sb-health-pagination button')===null"), true);
  // Keyboard users and environments without IntersectionObserver can load the next batch.
  await evaluate("__sb.navigate('settings');window.__healthObserver=window.IntersectionObserver;window.IntersectionObserver=undefined");
  await wait("document.querySelector('.sb-health-rows')===null");
  await evaluate("__sb.navigate('health')");
  await wait("document.querySelectorAll('.sb-health-rows > li').length===15");
  await evaluate("document.querySelector('.sb-health-pagination button').scrollIntoView({block:'end'})");
  await sleep(100);
  assert.equal(await evaluate("document.querySelectorAll('.sb-health-rows > li').length"), 15);
  await click("Show 15 more problems");
  await wait("document.querySelectorAll('.sb-health-rows > li').length===30");
  await evaluate("window.IntersectionObserver=window.__healthObserver;delete window.__healthObserver;__sb.setActiveBrain(__sb.getState().brains.find(b=>b.id!==__sb.getState().activeBrainId).id);__sb.navigate('health')");
  await wait("document.querySelector('.sb-health-rows')!==null");
  assert.ok(await evaluate("document.querySelectorAll('.sb-health-rows > li').length<=15"));
  assert.equal(await evaluate("document.querySelector('.sb-bulk')===null"), true);
  await evaluate("__sb.navigate('settings')");
  win.setSize(980,900); await sleep(200);
  assert.equal(await evaluate("document.documentElement.scrollWidth>innerWidth"),false);
  assert.deepEqual(errors,[]);
  console.log("Second Brain desktop API test passed, including automatic report sources, job source rules, and health batches of 15 with scrolling, filters, selection, final batches and manual fallback.");
}
main().then(async()=>{clearTimeout(timeout);await vite?.close();win?.destroy();backend?.kill();app.exit(0);}).catch(async error=>{console.error(error);clearTimeout(timeout);await vite?.close();backend?.kill();app.exit(1);});
