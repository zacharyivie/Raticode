let themeJob = null;
let jobNumber = 0;
let holdTheme = false;
/* global __dirname, console, setTimeout, clearTimeout, Buffer */
// Real browser coverage with deterministic provider output, no live model calls.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-themes-ui-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
let vite, win;
const timer = setTimeout(() => { console.error("Theme UI test timed out"); app.exit(1); }, 60000);
const calls = [];
let fail = false;
const demo = '<!doctype html><html><head><style>body{background:#123449;color:#f5f9fb;font:18px/1.6 system-ui;padding:32px}h1{font-size:44px;color:#8bddd5}table{width:100%;border-collapse:collapse}td{border-top:1px solid #658394;padding:12px}</style></head><body><h1>Marine journal</h1><p>A sample project review.</p><table><tr><td>Prototype</td><td>Ready for review</td></tr><tr><td>Release</td><td>Next week</td></tr></table></body></html>';
async function run() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "theme-ui-test",
    resolveId(id) { if (id === "/themes-entry.jsx") return id; },
    load(id) { if (id === "/themes-entry.jsx") return `
      import React from "react"; import {createRoot} from "react-dom/client";
      import SettingsPopover from "/src/components/SettingsPopover.jsx";
      import {normalizeAppSettings, updateSetting} from "/src/lib/settings.js";
      import {startGenerationJob} from "/src/lib/generationJobs.js";
      import "/src/styles/index.css";
      window.goferDesktop = {rem: {configure: async (key, value) => { if(window.themeTestFailSave) throw new Error("Could not save theme settings."); if(key === "reportFormat") { localStorage.setItem("theme-test-format", value); return { reportFormat: value }; } localStorage.setItem("theme-test-memory", JSON.stringify(value)); return {reportThemes: value}; }}};
      window.addEventListener("gofer:rem-report-theme", event => {event.detail.handled=true; startGenerationJob("theme", event.detail, {provider:"codex",model:"active-model",effort:"high"}).then(event.detail.resolve,event.detail.reject);});
      function TestSettings(){const [settings,setSettings]=React.useState(()=>normalizeAppSettings({memory:{reportFormat:localStorage.getItem("theme-test-format")||"html",reportThemes:JSON.parse(localStorage.getItem("theme-test-memory")||"null"),secondBrainTheme:"blueprint"}})); return <SettingsPopover open initialCategory="memory" settings={settings} onChange={(key,value)=>setSettings(old=>updateSetting(old,key,value))} onClose={()=>{}}/>;}
      createRoot(document.getElementById("root")).render(<TestSettings/>);
    `; },
    configureServer(server) { server.middlewares.use(async (req, res, next) => {
      if (req.url === "/themes-test") { res.setHeader("Content-Type", "text/html"); return res.end(await server.transformIndexHtml("/themes-test", '<html><body><div id="root"></div><script type="module" src="/themes-entry.jsx"></script></body></html>')); }
      if (req.url.startsWith("/api/provider/capabilities")) { res.setHeader("Content-Type", "application/json"); return res.end(JSON.stringify({providers:[{id:"codex",displayName:"Codex",available:true,models:[{id:"custom-model",displayName:"Custom model",efforts:[{id:"low",displayName:"Low"}]}]}]})); }
      if (!req.url.startsWith("/api/generation-jobs")) return next();
      res.setHeader("Content-Type", "application/json");
      if (req.method === "GET") return res.end(JSON.stringify({jobs:themeJob?[themeJob]:[]}));
      if (req.url.endsWith("/dismiss")) { themeJob = null; return res.end('{"ok":true}'); }
      let body=""; for await (const chunk of req) body += chunk;
      const request = JSON.parse(body);
      calls.push(request);
      if(fail){res.statusCode=400;return res.end('{"error":"Provider unavailable. Retry when signed in."}');}
      themeJob = { id: String(++jobNumber), description: request.description, status: calls.length === 1 || holdTheme ? "running" : "completed", progress: "Choosing the marine palette", updatedAt: jobNumber };
      if (themeJob.status === "completed") themeJob.result = {label:"Marine journal",instructions:"Use navy and sea-glass tones.",html:demo};
      res.statusCode = 202; res.end(JSON.stringify(themeJob));
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({width:1040,height:960,show:true,webPreferences:{contextIsolation:true,nodeIntegration:false}});
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/themes-test`);
  const js = async text => { try { return await win.webContents.executeJavaScript(text); } catch(error) { console.error("Browser script failed:", text); throw error; } };
  const until = async text => { for(let i=0;i<150;i++){if(await js(`Boolean(${text})`))return;await new Promise(r=>setTimeout(r,30));}throw new Error(`Timed out: ${text}`); };
  const click = text => (console.log("Theme UI:", text), js(`Array.from(document.querySelectorAll('button')).find(b=>(b.textContent.trim()===${JSON.stringify(text)} || b.getAttribute("aria-label")===${JSON.stringify(text)})).click()`));
  const change = (label,value) => js(`(()=>{const n=document.querySelector('[aria-label="${label}"]');const p=n.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:n.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(n,${JSON.stringify(value)});n.dispatchEvent(new Event(n.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);
  await until(`document.querySelectorAll('.report-theme-choice').length===16`);
  assert.ok(await js(`document.body.textContent.includes('Second Brain') && document.body.textContent.includes('Report themes')`));
  for (const format of ["slides", "pdf", "md", "html"]) {
    await change("Report output format", format);
    await until(`document.querySelector('[aria-label="Report output format"]').value === '${format}'`);
    assert.equal(await js(`localStorage.getItem('theme-test-format')`), format);
  }
  assert.ok(await js(`document.querySelector('.report-theme-gallery').scrollWidth>document.querySelector('.report-theme-gallery').clientWidth`));
  await js(`document.querySelector('[aria-label="Next report themes"]').click()`);
  await until(`document.querySelector('.report-theme-gallery').scrollLeft>0`);
  await click("+ New theme"); await change("Theme description","Marine field journal");
  assert.ok(await js(`document.querySelector('.report-theme-heading').nextElementSibling.matches('.report-theme-editor')`));
  assert.ok(await js(`document.querySelector('[aria-label="Transcribe message locally"]') && document.querySelector('[aria-label="Attach files"]')`));
  const composerImage=await win.webContents.capturePage(); fs.writeFileSync(path.join(os.tmpdir(),"raticode-report-theme-composer.png"),composerImage.toPNG());
  await click("Generate preview");
  await until(`document.querySelector('.report-theme-progress')?.textContent.includes('Choosing the marine palette')`);
  assert.ok(await js(`document.querySelector('.report-theme-spinner')`));
  assert.equal(await js(`document.querySelector('iframe[title="New report theme preview"]')`), null);
  themeJob = { ...themeJob, progress:"Laying out the sample report", updatedAt: 99 };
  await until(`document.querySelector('.report-theme-progress')?.textContent.includes('Laying out the sample report')`);
  assert.ok(await js(`!document.body.textContent.includes('Choosing the marine palette')`));
  await click("Continue in background");
  await win.reload();
  await until(`document.querySelector('.report-theme-progress')?.textContent.includes('Laying out the sample report')`);
  assert.equal(await js(`document.querySelector('[aria-label="Theme description"]').value`), "Marine field journal");
  themeJob = { ...themeJob, status: "completed", updatedAt: 100, result: {label:"Marine journal",instructions:"Use navy and sea-glass tones.",html:demo} };
  await until(`document.querySelector('iframe[title="New report theme preview"]')`);
  assert.ok(await js(`!document.querySelector('.report-theme-progress') && !document.querySelector('.report-theme-spinner') && document.body.textContent.includes('Theme generated successfully.')`));
  assert.deepEqual(calls[0],{kind:"theme", provider:"codex",model:"active-model",effort:"high",description:"Marine field journal",attachments:[]});
  assert.equal(await js(`localStorage.getItem('theme-test-memory')`),null);
  assert.equal(await js(`document.querySelector('iframe[title="New report theme preview"]').getAttribute('sandbox')`),"");
  await js(`document.querySelector('[aria-label="Theme name"]').focus()`);
  await change("Theme name",""); assert.equal(await js(`document.querySelector('[aria-label="Theme name"]').value`),"");
  await change("Theme name","Marine journal"); await js(`document.querySelector('[aria-label="Theme name"]').blur()`);
  await click("Save theme"); await until(`document.querySelectorAll('.report-theme-choice').length===17`);
  await win.reload(); await until(`document.querySelectorAll('.report-theme-choice').length===17`);
  assert.ok(await js(`document.querySelector('[aria-label="Marine journal"]').getAttribute('aria-pressed')==='true'`));
  const original = JSON.parse(await js(`localStorage.getItem('theme-test-memory')`));
  const downloadedPath = path.join(profile, "shared-theme.json");
  const download = new Promise((resolve, reject) => win.webContents.session.once("will-download", (event, item) => {
    assert.equal(item.getFilename(), "Marine-journal.raticode-theme.json");
    item.setSavePath(downloadedPath);
    item.once("done", (_, state) => state === "completed" ? resolve() : reject(new Error(`Theme download ${state}`)));
  }));
  await click("Export theme"); await download;
  const shared = fs.readFileSync(downloadedPath, "utf8");
  assert.deepEqual(JSON.parse(shared), { format: "raticode-report-theme", version: 1, theme: { label: original.custom[0].label, instructions: original.custom[0].instructions, html: demo } });
  // A fresh installation imports the actual downloaded file, then persists across reloads.
  await js(`localStorage.removeItem('theme-test-memory')`);
  await win.reload(); await until(`document.querySelectorAll('.report-theme-choice').length===16`);
  assert.ok(await js(`!Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Export theme')`));
  const importFile = content => js(`(()=>{const dt=new DataTransfer();dt.items.add(new File([${JSON.stringify(content)}],'shared.raticode-theme.json',{type:'application/json'}));const input=document.querySelector('[aria-label="Import report theme file"]');input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await click("Import theme"); await importFile(shared);
  await until(`document.querySelectorAll('.report-theme-choice').length===17`);
  let imported = JSON.parse(await js(`localStorage.getItem('theme-test-memory')`));
  assert.deepEqual(imported.custom[0], { ...original.custom[0], id: imported.selected });
  assert.notEqual(imported.selected, original.selected);
  assert.ok(await js(`document.querySelector('.report-theme-transfer-status').textContent.includes('Imported "Marine journal"')`));
  assert.equal(await js(`document.querySelector('[aria-label="Import report theme file"]').value`), "");
  assert.equal(await js(`document.querySelector('iframe[title="Marine journal miniature"]').getAttribute('sandbox')`), "");
  await win.reload(); await until(`document.querySelectorAll('.report-theme-choice').length===17`);
  assert.ok(await js(`document.querySelector('[aria-label="Marine journal"]').getAttribute('aria-pressed')==='true'`));
  await importFile(shared); await until(`document.querySelectorAll('.report-theme-choice').length===18`);
  imported = JSON.parse(await js(`localStorage.getItem('theme-test-memory')`));
  assert.notEqual(imported.custom[0].id, imported.custom[1].id);
  const beforeInvalid = await js(`localStorage.getItem('theme-test-memory')`);
  for (const [content, message] of [["invalid", "not valid JSON"], [JSON.stringify({ format: "raticode-report-theme", version: 2 }), "Unsupported theme"], ["x".repeat(2 * 1024 * 1024 + 1), "2 MB"]]) {
    await importFile(content);
    await until(`document.querySelector('[role="alert"]')?.textContent.includes(${JSON.stringify(message)})`);
    assert.equal(await js(`localStorage.getItem('theme-test-memory')`), beforeInvalid);
    assert.equal(await js(`document.querySelectorAll('.report-theme-choice').length`), 18);
  }
  await js(`window.themeTestFailSave=true`); await importFile(shared);
  await until(`document.querySelector('[role="alert"]')?.textContent.includes('Could not save theme settings')`);
  assert.equal(await js(`localStorage.getItem('theme-test-memory')`), beforeInvalid);
  await js(`window.themeTestFailSave=false`); await importFile(shared);
  await until(`document.querySelectorAll('.report-theme-choice').length===19`);
  await js(`document.querySelector('.report-generation-settings').open=true`);
  await until(`document.querySelector('[aria-label="Theme generation provider"] option[value="codex"]')`);
  await change("Theme generation provider","codex"); await until(`document.querySelector('[aria-label="Theme generation model"]')`);
  await change("Theme generation model","custom-model"); await until(`!document.querySelector('[aria-label="Theme generation effort"]').disabled`);
  await change("Theme generation effort","low"); await until(`JSON.parse(localStorage.getItem("theme-test-memory")).generation.effort==="low"`);
  await click("+ New theme");
  await js(`(()=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOtQAAAAASUVORK5CYII='),c=>c.charCodeAt(0))],'reference.png',{type:'image/png'}));const input=document.querySelector('.report-theme-composer input[type=file]');input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`document.querySelector('[aria-label="Remove reference.png"]')`); await click("Generate preview"); await until(`document.querySelector('iframe[title="New report theme preview"]')`);
  assert.equal(calls.at(-1).provider,"codex"); assert.equal(calls.at(-1).model,"custom-model"); assert.equal(calls.at(-1).effort,"low"); assert.equal(calls.at(-1).attachments[0].type,"image/png");
  await click("Close"); await click("View generated theme");
  await js(`document.querySelector('[aria-label="Remove reference.png"]').click()`);
  await change("Theme description", "");
  await js(`(()=>{const dt=new DataTransfer();dt.setData('text/plain','Navy journal instructions. '.repeat(1000));document.querySelector('[aria-label="Theme description"]').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})()`);
  await until(`document.querySelector('[aria-label^="Remove pasted-text-"]')`);
  assert.equal(await js(`document.querySelector('[aria-label="Theme description"]').value`), "");
  await js(`(()=>{const dt=new DataTransfer();dt.items.add(new File(['image'],'pasted.png',{type:'image/png'}));document.querySelector('[aria-label="Theme description"]').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})()`);
  await until(`document.querySelector('[aria-label="Remove pasted.png"]')`);
  await js(`(()=>{const dt=new DataTransfer();dt.items.add(new File(['<html>Example report</html>'],'report.html',{type:'text/html'}));document.querySelector('.report-theme-composer').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));})()`);
  await until(`document.querySelector('[aria-label="Remove report.html"]')`);
  await js(`document.querySelector('[aria-label="Theme description"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))`);
  await until(`document.querySelector('iframe[title="New report theme preview"]')`);
  assert.equal(calls.at(-1).attachments.length, 3);
  assert.equal(Buffer.from(calls.at(-1).attachments[0].data,'base64').toString(), 'Navy journal instructions. '.repeat(1000));
  await click("Close");
  holdTheme = true;
  await click("View generated theme"); await click("Generate another preview");
  await until(`document.querySelector('.report-theme-progress')?.textContent.includes('Choosing the marine palette')`);
  await click("Continue in background");
  await until(`!document.querySelector('.report-theme-editor')`);
  holdTheme = false;
  themeJob = { ...themeJob, status:"completed", updatedAt:200, result:{label:"Background theme",instructions:"Navy",html:demo} };
  await win.reload();
  await until(`document.querySelector('[aria-label="Theme name"]')?.value === 'Background theme'`);
  assert.ok(await js(`document.body.textContent.includes('Theme generated successfully.')`));
  await click("Close"); fail=true; await click("View generated theme"); await change("Theme description","Another theme"); await click("Generate preview");
  await until(`document.querySelector('[role="alert"]')?.textContent.includes('Provider unavailable')`);
  await click("Close");
  console.log("Theme UI: capture gallery");
  await js(`document.querySelector(".report-themes").scrollIntoView({block:"start"})`);
  const image=await win.webContents.capturePage(); fs.writeFileSync(path.join(os.tmpdir(),"raticode-report-themes-gallery.png"),image.toPNG());
  console.log("Theme UI: narrow layout");
  win.setSize(440,800); await new Promise(r=>setTimeout(r,150));
  assert.ok(await js(`document.documentElement.scrollWidth<=window.innerWidth`));
  await js(`document.querySelector(".report-themes").scrollIntoView({block:"start"})`);
  const mobile=await win.webContents.capturePage(); fs.writeFileSync(path.join(os.tmpdir(),"raticode-report-themes-narrow.png"),mobile.toPNG());
  await click("+ New theme");
  await js(`document.documentElement.classList.add('dark');document.querySelector('.report-theme-editor').scrollIntoView({block:'nearest'})`);
  assert.ok(await js(`document.documentElement.scrollWidth<=window.innerWidth`));
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await js(`getComputedStyle(document.querySelector("[data-chat-composer]")).backgroundColor`), "rgb(24, 24, 26)");
  assert.ok(await js(`[...document.querySelectorAll('.report-themes select')].every(select => getComputedStyle(select).colorScheme === 'dark' && [...select.options].filter(option => !option.selected).every(option => getComputedStyle(option).backgroundColor === 'rgb(30, 30, 32)' && getComputedStyle(option).color === 'rgb(243, 243, 245)'))`));
  const narrowComposer=await win.webContents.capturePage(); fs.writeFileSync(path.join(os.tmpdir(),"raticode-report-theme-composer-narrow.png"),narrowComposer.toPNG());
  console.log("Report theme browser checks passed: gallery, preview, draft editing, save/reload, export download, import/reload, duplicate import, invalid files, failed save/retry, paperclip, file paste, long text paste, drop, Enter submit, model overrides, errors, narrow composer.");
}
run().then(()=>{clearTimeout(timer);win?.destroy();return vite?.close();}).then(()=>app.quit()).catch(error=>{console.error(error);clearTimeout(timer);win?.destroy();void vite?.close();app.exit(1);});
