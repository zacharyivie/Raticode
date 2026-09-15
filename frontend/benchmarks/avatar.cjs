const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve('perf-dist');
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-dev-shm-usage');
app.commandLine.appendSwitch('force-device-scale-factor', process.env.PERF_SCALE || '1');
app.whenReady().then(async () => {
  const server = http.createServer((req, res) => {
    const file = path.join(root, req.url);
    fs.readFile(file, (error, data) => {
      if (error) { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream');
      res.end(data);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const win = new BrowserWindow({ width: 900, height: 600, webPreferences: { sandbox: false } });
  try {
    await win.loadURL(`http://127.0.0.1:${server.address().port}/benchmarks/editor.html`);
    await new Promise(resolve => setTimeout(resolve, 1500));
    await win.webContents.executeJavaScript(`
      const avatar = document.querySelector('.rem-avatar');
      const stage = document.createElement('div');
      stage.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;gap:25px;padding:20px;background:#e4dfd5';
      for (const [pose, blinking] of [['seated', false], ['seated', true], ['waving', false], ['sleeping', false]]) {
        const item = document.createElement('div');
        const clone = avatar.cloneNode(true);
        clone.dataset.pose = pose;
        clone.dataset.blinking = String(blinking);
        item.append(clone);
        item.append(document.createTextNode(blinking ? 'Closed eyes' : pose));
        stage.append(item);
      }
      document.body.append(stage);
    `);
    for (const zoom of [1, 1.5]) {
      win.webContents.setZoomFactor(zoom);
      await new Promise(resolve => setTimeout(resolve, 500));
      const result = await win.webContents.executeJavaScript(`({dpr:devicePixelRatio, sources:[...document.querySelectorAll('.rem-avatar img')].slice(0,4).map(i=>i.currentSrc)})`);
      console.log(JSON.stringify({ zoom, ...result }));
      fs.writeFileSync(`/tmp/taskurotta-avatar-${process.env.PERF_SCALE || '1'}x-zoom-${zoom}.png`, (await win.webContents.capturePage()).toPNG());
    }
  } finally { win.destroy(); server.close(); app.exit(); }
}).catch(error => { console.error(error); app.exit(1); });
