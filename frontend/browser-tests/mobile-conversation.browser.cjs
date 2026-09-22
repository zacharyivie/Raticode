/* global __dirname, console, setTimeout, clearTimeout */
// Disposable renderer fixture. No provider execution or real desktop state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { app, BrowserWindow } = require('electron');
app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-phone-chat-')));
app.commandLine.appendSwitch('disable-dev-shm-usage'); app.disableHardwareAcceleration(); app.commandLine.appendSwitch('no-sandbox');
let vite, win, complete=false;
const timer=setTimeout(()=>app.exit(1),90000);
const metadata={id:'phone-thread',title:'Mobile conversation review',projectRoot:'/fixture',projectName:'Raticode Mobile',provider:'codex',model:'test-model',updatedAt:new Date().toISOString()};
const user={id:'request',role:'user',body:'Review the mobile conversation layout.',origin:'phone',attachments:[{id:'fixture-file',name:'README-mobile-fixture.md',size:24,mime:'text/markdown'}]};
const thought={id:'thought',role:'assistant',kind:'thought',body:'Checking the composer layout.',groupId:'phone-thoughts-request',deviceRequestId:'request',deviceSequence:1};
const summary={id:'summary',role:'assistant',kind:'turn-summary',body:'',deviceRequestId:'request',deviceSequence:3,running:true,startedAt:Date.now()};
const remote=()=>({thread_id:'wire',revision:1,sync_token:complete?'done':'running',metadata,running:!complete,active_turn:complete?null:'turn',messages:complete?[user,{...thought,body:'Checking the composer layout. The send button remains visible.'},{id:'reply-request',role:'assistant',kind:'final',body:'The updated layout keeps the composer visible and preserves the conversation.',deviceRequestId:'request',deviceSequence:2},{...summary,running:false,completedAt:new Date().toISOString(),durationMs:3200}]:[user,thought,summary]});
const actions=[];
async function main(){
 await app.whenReady(); const {createServer}=await import('vite');
 vite=await createServer({root:path.join(__dirname,'..'),server:{host:'127.0.0.1',port:0},plugins:[{
 name:'phone-conversation-review',resolveId(id){if(id==='/phone-entry.jsx')return id;},
 load(id){if(id==='/phone-entry.jsx')return `import React from 'react';import{createRoot}from'react-dom/client';import{ChatPane}from'/src/pages/App.jsx';import{startDeviceWorkspaceSync}from'/src/lib/deviceWorkspaceSync.js';import'/src/styles/index.css';const meta=${JSON.stringify(metadata)};localStorage.setItem('gofer-flow-chat-threads',JSON.stringify([{id:meta.id,projectRoot:meta.projectRoot,scopeIndexed:true}]));localStorage.setItem('gofer-flow-chat-thread-meta:'+meta.id,JSON.stringify(meta));window.goferDesktop={workspace:{pathGrantForApi:()=>'fixture-grant'}};createRoot(document.getElementById('root')).render(<ChatPane width="100%" activeProjectRoot="/fixture" assistantDefaults={{provider:'codex',model:'test-model'}}/>);setTimeout(()=>startDeviceWorkspaceSync({interval:250}),400);`;},
 configureServer(server){server.middlewares.use(async(req,res,next)=>{
  if(req.url==='/phone-review'){res.setHeader('Content-Type','text/html');return res.end(await server.transformIndexHtml('/phone-review','<html><body><div id="root" style="height:100vh;max-width:560px;margin:auto"></div><script type="module" src="/phone-entry.jsx"></script></body></html>'));}
  if(!req.url.startsWith('/api/'))return next();res.setHeader('Content-Type','application/json');
  if(req.url==='/api/devices'){
   if(req.method==='GET')return res.end(JSON.stringify({workspace_peers:['fixture']}));
   let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);actions.push(body);
   if(body.action==='workspace_poll')return res.end(JSON.stringify({threads:body.known?.wire===remote().sync_token?[]:[remote()]}));
   if(body.action==='workspace_exchange')return res.end(JSON.stringify(remote()));
   throw new Error('Unexpected mutation '+body.action);
  }
  if(req.url.includes('/provider/capabilities'))return res.end(JSON.stringify({providers:[{id:'codex',displayName:'Codex',available:true,discoveryStatus:'ready',defaultModel:'test-model',models:[{id:'test-model',displayName:'Test model'}],permissionModes:[{id:'read-only',displayName:'Read only'}],defaultPermissionMode:'read-only'}]}));
  if(req.url.includes('/chat/steering'))return res.end(JSON.stringify({receipts:[]}));
  if(req.url.includes('/chat/stream'))throw new Error('No provider execution allowed');
  res.end('{}');
 });}
 }]});await vite.listen();win=new BrowserWindow({width:650,height:950,show:true,webPreferences:{contextIsolation:true,nodeIntegration:false}});
 const errors=[];win.webContents.on('console-message',d=>{if(d.level==='error')errors.push(d.message);});
 await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/phone-review`);
 const evaluate=s=>win.webContents.executeJavaScript(s,true);
 async function wait(s){const end=Date.now()+15000;while(!await evaluate(s)){if(Date.now()>end)throw new Error('Missing '+s+'\n'+await evaluate('document.body.innerText'));await new Promise(r=>setTimeout(r,100));}}
 await wait("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('Mobile conversation review'))");
 await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Mobile conversation review')).click()");
 await wait("document.querySelector('[aria-label=\"Sent from your phone\"]')");
 await wait("document.querySelector('[data-chat-pane]').getAttribute('aria-busy')==='true'");complete=true;
 await wait("document.body.textContent.includes('The updated layout keeps the composer visible')");
 await wait("document.querySelector('[data-chat-pane]').getAttribute('aria-busy')!=='true'");
 await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith('Show thoughts'))?.click()");
 await wait("document.body.textContent.includes('The send button remains visible')");
 assert.equal(await evaluate("document.querySelectorAll('[aria-label=\"Sent from your phone\"]').length"),1);
 assert.equal(await evaluate("document.querySelectorAll('[data-message-id=\"reply-request\"]').length"),1);
 await wait("document.body.textContent.includes('README-mobile-fixture.md')");
 assert.ok(actions.filter(a=>a.action==='workspace_exchange').some(a=>a.messages.some(m=>m.attachments?.[0]?.name==='README-mobile-fixture.md')));
 assert.ok(actions.filter(a=>a.action==='workspace_exchange').some(a=>a.messages.some(m=>m.kind==='thought' && m.groupId==='phone-thoughts-request')));
 const out=path.join(__dirname,'..','artifacts','mobile-conversation');fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'desktop-phone-turn.png'),(await win.webContents.capturePage()).toPNG());assert.deepEqual(errors,[]);
 console.log('PASS: phone marker, live trace updates, normal thought rendering, final reply, busy state, attachment rendering and presentation sync, no provider execution');
}
main().then(async()=>{clearTimeout(timer);win?.destroy();await vite?.close();app.exit(0);}).catch(async e=>{console.error(e);clearTimeout(timer);win?.destroy();await vite?.close();app.exit(1);});
