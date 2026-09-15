const {app,BrowserWindow}=require('electron');
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');
app.disableHardwareAcceleration();app.commandLine.appendSwitch('disable-gpu');app.commandLine.appendSwitch('disable-setuid-sandbox');app.commandLine.appendSwitch('disable-dev-shm-usage');app.commandLine.appendSwitch('no-sandbox');app.commandLine.appendSwitch('enable-precise-memory-info');
const root=path.resolve(process.env.PERF_DIST||'perf-dist');
app.whenReady().then(async()=>{
 const server=http.createServer((req,res)=>{const file=path.join(root,decodeURIComponent(req.url.split('?')[0]));fs.readFile(file,(error,data)=>{if(error){res.writeHead(404).end();return;}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'application/octet-stream');res.end(data);});});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const win=new BrowserWindow({width:1440,height:900,show:true,webPreferences:{nodeIntegration:false,contextIsolation:false,sandbox:false}});
 try{await win.loadURL(`http://127.0.0.1:${server.address().port}/benchmarks/editor.html`);await new Promise(r=>setTimeout(r,2500));const cpuBefore=app.getAppMetrics().find(item=>item.pid===win.webContents.getOSProcessId())?.cpu.cumulativeCPUUsage;const result=await win.webContents.executeJavaScript(process.env.PERF_SPLIT ? 'window.__splitCheck()' : 'window.__benchmark()');result.rendererCpuSeconds=app.getAppMetrics().find(item=>item.pid===win.webContents.getOSProcessId())?.cpu.cumulativeCPUUsage-cpuBefore;win.webContents.debugger.attach('1.3');await win.webContents.debugger.sendCommand('HeapProfiler.collectGarbage');result.retainedHeap=await win.webContents.debugger.sendCommand('Runtime.getHeapUsage');fs.writeFileSync((process.env.PERF_RESULT||'/tmp/taskurotta-editor-perf.json')+'.png',(await win.webContents.capturePage()).toPNG());console.log(JSON.stringify(result,null,2));fs.writeFileSync(process.env.PERF_RESULT||'/tmp/taskurotta-editor-perf.json',JSON.stringify(result,null,2));}
 catch(error){console.error(error);process.exitCode=1;}
 finally{win.destroy();server.close();app.exit(process.exitCode||0);}
});
