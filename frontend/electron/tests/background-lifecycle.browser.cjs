// Exercise the actual desktop entry point with an isolated profile and fake API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { app, BrowserWindow } = require('electron');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-background-test-'));
app.setPath('userData', profile);
fs.mkdirSync(path.join(profile, 'logs'));
app.setPath('logs', path.join(profile, 'logs'));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-dev-shm-usage');
process.env.GOFER_ELECTRON_MODE = 'production';
process.env.GOFER_DATA_DIR = path.join(profile, 'data');
const timer = setTimeout(() => { console.error('Background lifecycle timed out'); app.exit(1); }, 20000);
const api = http.createServer((_request, response) => {
  response.setHeader('Content-Type', 'application/json');
  response.setHeader('Access-Control-Allow-Origin', '*');
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});

async function run() {
  await new Promise(resolve => api.listen(0, '127.0.0.1', resolve));
  process.env.GOFER_API_BASE_URL = `http://127.0.0.1:${api.address().port}`;
  const created = new Promise(resolve => app.once('browser-window-created', (_event, window) => resolve(window)));
  await import('../main.js');
  const window = await created;
  await new Promise(resolve => window.webContents.once('did-finish-load', resolve));
  const contentsId = window.webContents.id;
  let quitting = false;
  app.on('before-quit', () => { quitting = true; });
  window.close();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(quitting, false);
  assert.equal(window.isDestroyed(), false);
  assert.equal(window.isVisible(), false);
  assert.equal(BrowserWindow.getAllWindows().length, 1);
  app.emit('second-instance');
  assert.equal(window.isVisible(), true);
  assert.equal(window.webContents.id, contentsId);
  clearTimeout(timer);
  console.log('Actual desktop close, background retention, and reopen passed.');
  api.close();
  app.quit();
}

run().catch(error => { console.error(error); api.close(); app.exit(1); });
