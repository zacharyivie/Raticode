import React, { createRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import CodeWorkspace from '../src/components/CodeWorkspace.jsx';
import RemAvatar from '../src/components/RemAvatar.jsx';
import { DEFAULT_APP_SETTINGS } from '../src/lib/settings.js';
import '../src/styles/index.css';
const paths = Array.from({length:10}, (_,i)=>`/fixture/file-${i}.txt`);
const source = 'The editor performance fixture has ordinary text.\n'.repeat(40000);
window.__gitCalls = 0;
window.__saved = '';
window.goferDesktop = {
  workspace: {gitFileBaseline: async()=> {window.__gitCalls++; return {tracked:true,changed:false,content:''};}},
  textFiles: {read:async path=>({content:path===paths[0]?source:'small file\n'}),write:async ({content})=>{window.__saved=content;return {};}}
};
const ref = createRef();
window.__editorRef=ref;
const settings={...DEFAULT_APP_SETTINGS,editor:{...DEFAULT_APP_SETTINGS.editor,minimap:false}};
function Fixture(){const [active,setActive]=useState(paths[0]);window.__activate=setActive;return <><RemAvatar animated={false}/><div style={{height:650,display:'flex'}}><CodeWorkspace ref={ref} active activePath={active} openPaths={paths} settings={settings} theme="light" onActivePathChange={setActive}/></div></>;}
createRoot(document.getElementById('root')).render(<Fixture/>);
window.__benchmark = async () => {
 const {loadRattishMonaco}=await import('../src/lib/monaco.js');
 const monaco=loadRattishMonaco();
 const model=monaco.editor.getModels().find(m=>m.uri.path===paths[0]);
 if(!model || model.getValueLength()<1000000) throw new Error('Fixture is not ready');
 let reads=0; const original=model.getValue.bind(model);model.getValue=(...args)=>{reads++;return original(...args);};
 window.__commits=0;const times=[];
 for(let i=0;i<80;i++) {const start=performance.now();model.applyEdits([{range:new monaco.Range(1,1,1,1),text:'x'}]);times.push(performance.now()-start);await new Promise(r=>setTimeout(r,0));}
 const typing={fullDocumentReads:reads,reactCommits:window.__commits,medianMs:times.sort((a,b)=>a-b)[40],p95Ms:times[76]};
 await ref.current.saveActive();
 if(window.__saved!==original()) throw new Error('Immediate save lost edits');
 window.__gitCalls=0;await new Promise(r=>setTimeout(r,2300));const idleGitCalls=window.__gitCalls;
 window.__activate(paths[1]);await new Promise(r=>setTimeout(r,150));const activationGitCalls=window.__gitCalls-idleGitCalls;
 const images=[...document.querySelectorAll('.rem-avatar img')];
 const blobs=await Promise.all(images.map(i=>fetch(i.currentSrc).then(r=>r.blob())));
 const decodes=[];
 for(let i=0;i<5;i++){const start=performance.now();const bitmaps=await Promise.all(blobs.map(blob=>createImageBitmap(blob)));bitmaps.forEach(bitmap=>bitmap.close());decodes.push(performance.now()-start);}
 return {avatarDecodeMedianMs:decodes.sort((a,b)=>a-b)[2],characters:model.getValueLength(),typing,idleGitCalls,activationGitCalls,heapBytes:performance.memory?.usedJSHeapSize,images:[...document.querySelectorAll('.rem-avatar img')].map(i=>({width:i.naturalWidth,url:i.currentSrc})),saveMatches:true};
};

window.__splitCheck = async () => {
  const tab = [...document.querySelectorAll('[role="tab"]')].find(item => item.textContent.includes('file-1.txt'));
  tab.parentElement.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 120, clientY: 120 }));
  await new Promise(resolve => setTimeout(resolve, 50));
  [...document.querySelectorAll('button')].find(item => item.textContent.trim() === 'Split right').click();
  await new Promise(resolve => setTimeout(resolve, 250));
  window.__gitCalls = 0;
  await new Promise(resolve => setTimeout(resolve, 2300));
  if (window.__gitCalls !== 2) throw new Error(`Expected both visible panes to poll once, got ${window.__gitCalls}`);
  return { splitGitCalls: window.__gitCalls, splitVisible: Boolean(document.querySelector('[aria-label="Split editor tabs"]')) };
};
