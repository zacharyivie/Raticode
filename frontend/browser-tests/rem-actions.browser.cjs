/* global __dirname, console, setTimeout, clearTimeout */
/* Isolated UI regression: mock provider responses, real IndexedDB history. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-rem-actions-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
let vite, win;
const timer = setTimeout(() => { console.error("Rem actions browser test timed out"); app.exit(1); }, 60000);
const entry = `
import React from "react";
import {createRoot} from "react-dom/client";
import {ChatPane, persistChatThreads, loadChatThreads} from "/src/pages/App.jsx";
import RemActionIcon from "/src/components/RemActionIcon.jsx";
import {conversationRepository} from "/src/lib/conversationRepository.js";
import "/src/styles/index.css";
window.requests = [];
window.fetch = async (url, options) => {
  if (String(url).includes("provider/capabilities")) return new Response(JSON.stringify({providers:[{id:"codex", available:true, discoveryStatus:"ready", defaultModel:"cli-default", models:[{id:"cli-default", label:"CLI default", efforts:[]}]}]}));
  if (String(url).includes("chat/stream")) {
    window.requests.push(JSON.parse(options.body));
    return new Response(new ReadableStream({start(controller) {
      setTimeout(() => { controller.enqueue(new TextEncoder().encode(JSON.stringify({type:"final",message:{body:"Mock reply"}})+"\\n")); controller.close(); }, 1600);
    }}));
  }
  return new Response("{}");
};
const parent = {id:"parent",title:"Debug provider discovery",projectRoot:"/repo",projectName:"Raticode",provider:"codex",model:"cli-default",resources:{shell:true,web:false,skills:[],mcpServers:[]},permissionsByProvider:{codex:"default"},updatedAt:new Date().toISOString()};
const history = Array.from({length:120},(_,i)=>({id:"m"+i,role:i%2?"assistant":"user",body:"History message "+i}));
const repository = conversationRepository();
await repository.save(parent.id,history);
persistChatThreads([parent]);
window.repository = repository;
window.loadThreads = loadChatThreads;
createRoot(document.getElementById("root")).render(<div style={{display:"flex",height:"100vh",justifyContent:"center"}}>
  <section style={{padding:40,width:400}}><h1 style={{fontSize:28,fontWeight:650}}>Rem actions</h1><p style={{margin:"16px 0"}}>A rat, a little magic, and a new direction for a conversation.</p>
    <div style={{display:"flex",alignItems:"center",gap:20,marginTop:32}}><RemActionIcon size={96}/><button className="rem-action-button grid h-8 w-8 place-items-center rounded border border-line bg-canvas" aria-label="Generate commit message with Rem"><RemActionIcon/></button></div>
    <p style={{fontSize:13,marginTop:24}}>Actual button size at right. The icon follows the app's text color.</p></section>
  <ChatPane activeProjectRoot="/repo" recentProjectRoots={["/repo"]} width={440} assistantDefaults={{provider:"codex",model:"cli-default",avatarEnabled:false,swarmAccessEnabled:false}} />
</div>);
`;
async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root:path.join(__dirname,".."), server:{host:"127.0.0.1",port:0}, plugins:[{
    name:"rem-actions-regression",
    resolveId(id) { if (id === "/rem-actions-entry.jsx") return id; },
    load(id) { if (id === "/rem-actions-entry.jsx") return entry; },
    configureServer(server) { server.middlewares.use(async (request,response,next) => {
      if (request.url !== "/rem-actions-test") return next();
      response.setHeader("Content-Type","text/html");
      response.end(await server.transformIndexHtml("/rem-actions-test",'<html><body><div id="root"></div><script type="module" src="/rem-actions-entry.jsx"></script></body></html>'));
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({width:900,height:850,show:true,webPreferences:{contextIsolation:true,nodeIntegration:false}});
  const errors = [];
  win.webContents.on("console-message", details => { if(details.level === "error") errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/rem-actions-test`);
  const evaluate = source => win.webContents.executeJavaScript(source,true);
  const wait = async source => { const deadline = Date.now()+8000; while(!await evaluate(source)) { if(Date.now()>deadline) throw new Error(`Missing condition: ${source}\n${await evaluate('document.body.innerText')}`); await new Promise(resolve=>setTimeout(resolve,30)); } };
  const draft = async value => evaluate(`(() => { const input=document.querySelector('textarea'); input.focus(); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,${JSON.stringify(value)}); input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  const enter = modifiers => { win.webContents.sendInputEvent({type:"keyDown",keyCode:"Return",modifiers}); win.webContents.sendInputEvent({type:"keyUp",keyCode:"Return",modifiers}); };
  await wait("document.querySelector('[data-assistant-home]')");
  await draft("Check provider discovery"); enter(["control"]);
  await wait("window.requests.length === 1 && document.querySelector('[data-thread-launching]')");
  assert.equal(await evaluate("!!document.querySelector('[data-assistant-home]') && document.querySelector('textarea').value === ''"),true);
  assert.equal(await evaluate("!!document.querySelector('[data-thread-launching] [title=\"Rem response running\"]')"),true);
  const artifact = path.join(__dirname,"../artifacts/rem-actions"); fs.mkdirSync(artifact,{recursive:true});
  fs.writeFileSync(path.join(artifact,"rat-action-and-background-thread.png"),(await win.webContents.capturePage()).toPNG());
  await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.includes('Debug provider discovery')).click()");
  await wait("document.querySelector('[data-message-id=\"m100\"]')");
  assert.equal(await evaluate("!!document.querySelector('[data-message-id=\"m0\"]')"),false);
  await evaluate("document.querySelector('[data-message-id=\"m100\"]').parentElement.querySelector('[aria-label=\"Fork thread from here\"]').click()");
  await wait("window.loadThreads().some(thread=>thread.forkedFromMessageId === 'm100') && document.body.innerText.includes('Thread forked.')");
  const copied = await evaluate("(async()=>{const thread=window.loadThreads().find(t=>t.forkedFromMessageId==='m100');return {thread,messages:await window.repository.all(thread.id),original:await window.repository.all('parent')};})()");
  assert.equal(copied.messages.length,101); assert.equal(copied.original.length,120);
  assert.equal(copied.messages[0].body,"History message 0");
  assert.equal(copied.messages.at(-1).id,"m100");
  fs.writeFileSync(path.join(artifact,"forked-history.png"),(await win.webContents.capturePage()).toPNG());
  await draft("Try another approach"); enter([]);
  await wait("window.requests.length === 2");
  const sent = await evaluate("window.requests[1]");
  assert.equal(sent.conversationId,copied.thread.id);
  assert.equal(sent.messages.length,102);
  assert.ok(!sent.messages.some(message=>message.body==='History message 101'));
  assert.deepEqual(errors,[]);
  console.log("Rem browser checks passed: background launch, running state, cleared input, unloaded history fork, independent continuation.");
}
main().then(()=>finish()).catch(error=>finish(error));
async function finish(error) { clearTimeout(timer); win?.destroy(); await vite?.close(); if(error) console.error(error); app.exit(error?1:0); }
