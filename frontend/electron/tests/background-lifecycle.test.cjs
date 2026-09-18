const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');

test('window close hides the UI without quitting, but explicit quit closes it', () => {
  const start = source.indexOf('  mainWindow.on("close",');
  const end = source.indexOf('  mainWindow.on("closed",', start);
  let close, hidden = 0, prevented = 0;
  const context = {
    isQuitting: false, isSmokeTest: false,
    mainWindow: { on: (_name, callback) => { close = callback; }, hide: () => hidden++ },
  };
  vm.runInNewContext(source.slice(start, end), context);
  close({ preventDefault: () => prevented++ });
  assert.equal(hidden, 1); assert.equal(prevented, 1);
  context.isQuitting = true;
  close({ preventDefault: () => prevented++ });
  assert.equal(hidden, 1); assert.equal(prevented, 1);
});

test('cold startup creates the shell while backend and terminal readiness are pending', async () => {
  const start = source.indexOf('app.whenReady().then(');
  const end = source.indexOf('function showMainWindow()', start);
  let releaseBackend, readyCallback;
  const backend = new Promise(resolve => { releaseBackend = resolve; });
  const never = new Promise(() => {});
  const windows = [];
  const context = {
    app: { whenReady: () => ({ then: callback => { readyCallback = callback; } }),
      getPath: () => '/logs', getVersion: () => 'test', on() {} },
    createAppLog: () => ({ write() {}, emergency() {} }),
    process: { on() {}, env: {}, platform: 'win32', arch: 'x64' },
    Menu: { setApplicationMenu() {} },
    setupIpcHandlers() {}, setupAutoUpdater() {}, createBackgroundTray() {},
    allocateBackendPort: async () => 43210,
    startBackend: port => { assert.equal(port, 43210); return backend; },
    startTerminalEditorServer: () => never,
    createWindow: url => windows.push(url),
    showMainWindow() {}, isSmokeTest: false,
    createBackendErrorWindow: error => { throw error; },
  };
  vm.runInNewContext(source.slice(start, end), context);
  const startup = readyCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(windows, ['http://127.0.0.1:43210']);
  assert.equal(context.activeApiBaseUrl, undefined);
  releaseBackend({ apiBaseUrl: windows[0], apiToken: 'test-token' });
  await startup;
  assert.equal(context.activeUiApiToken, 'test-token');
  assert.equal(windows.length, 1, 'backend readiness must not reload the UI');
});

test('reopening shows the retained window, and closing all windows does not stop services', () => {
  let show = 0, focus = 0, restore = 0, quit = 0, allClosed;
  const context = {
    mainWindow: { isDestroyed: () => false, isMinimized: () => true,
      show: () => show++, focus: () => focus++, restore: () => restore++ },
    isQuitting: false, isSmokeTest: false,
    app: { on: (_name, callback) => { allClosed = callback; }, quit: () => quit++ },
  };
  const start = source.indexOf('function showMainWindow()');
  const end = source.indexOf('function createBackgroundTray()', start);
  vm.runInNewContext(`${source.slice(start, end)}\nshowMainWindow();`, context);
  assert.deepEqual([show, focus, restore], [1, 1, 1]);
  const closedStart = source.indexOf('app.on("window-all-closed",');
  const closedEnd = source.indexOf('function setupIpcHandlers()', closedStart);
  vm.runInNewContext(source.slice(closedStart, closedEnd), context);
  allClosed(); assert.equal(quit, 0);
  context.isQuitting = true;
  allClosed(); assert.equal(quit, 1);
});
