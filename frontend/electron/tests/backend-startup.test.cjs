const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const os = require('node:os');
const { featureEnv, featureStates, readFeatureChoices, writeFeatureChoice } = require('../feature-preview.cjs');
const { createBackendOutput } = require('../backend-log.cjs');
const { createTerminalOutput } = require('../main-errors.cjs');

const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
const startupSource = source.slice(source.indexOf('function startBackend('), source.indexOf('function getBackendCommand()'));

// A terminal stderr that fails each write the way a closed pipe does in Node:
// asynchronously, through an `error` event.
function closedPipe() {
  const stream = new EventEmitter();
  stream.writes = 0;
  stream.write = () => {
    stream.writes += 1;
    process.nextTick(() => stream.emit('error', Object.assign(new Error('write EPIPE'), { code: 'EPIPE', syscall: 'write', errno: -32 })));
    return false;
  };
  return stream;
}

function harness(t, { stderr = { write() {} } } = {}) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  const stopped = [];
  const crashes = [];
  const logs = [];
  const entries = [];
  const terminalLog = [];
  const context = {
    applicationLog: { write: (...args) => entries.push(args) },
    createBackendOutput, path, Date,
    reportPdfService: { env: { RATICODE_REPORT_PDF_URL: 'http://127.0.0.1:1234/pdf', RATICODE_REPORT_PDF_TOKEN: 'pdf-test-token' } },
    backendPathGrants: { reset() {} }, loggedPathGrants: new Set(),
    process: { env: { RATICODE_EXPERIMENTAL_ORGANIZATIONS: '1' }, stderr: { write() {} } },
    featureEnv,
    // Settings turned pairing on and Organizations off.
    featureChoices: () => ({ 'device-network': true, organizations: false }),
    console: { log() {} },
    setTimeout, clearTimeout,
    // Keep the old deadline available so this test also fails against the old code.
    BACKEND_START_TIMEOUT_MS: 15000,
    BACKEND_READY_PREFIX: 'GOFER_UI_READY ',
    repoRoot: '/app', desktopGrantSecret: 'test-secret',
    agentBrowserService: { url: 'http://127.0.0.1:4100/browser', adminToken: 'b'.repeat(64) },
    getBackendCommand: () => ({ command: 'test-backend', args: [] }),
    getGoferDataDir: () => '/data',
    spawn: (_command, _args, options) => {
      assert.equal(options.env.RATICODE_REPORT_PDF_TOKEN, 'pdf-test-token');
      // The backend alone may grant and revoke Rem's browser access.
      assert.equal(options.env.GOFER_BROWSER_SERVICE_URL, 'http://127.0.0.1:4100/browser');
      assert.equal(options.env.GOFER_BROWSER_ADMIN_TOKEN, 'b'.repeat(64));
      // Feature Preview choices replace the inherited environment.
      assert.equal(options.env.RATICODE_DEVICE_EXPERIMENTAL_NETWORK, '1');
      assert.equal('RATICODE_EXPERIMENTAL_ORGANIZATIONS' in options.env, false);
      // Backend log records come back as JSON lines the desktop can file by level.
      assert.equal(options.env.GOFER_LOG_FORMAT, 'desktop');
      assert.equal(options.env.GOFER_LOG_LEVEL, 'debug');
      return child;
    },
    createBackendLogStream: () => null,
    closeBackendLogStream() {},
    writeBackendLog: line => logs.push(line),
    stopBackend: () => stopped.push(child),
    expectedBackendStops: new WeakSet(), isQuitting: false,
    quitPending: false, backendStoppedDuringQuit: false, BACKEND_CRASH_GRACE_MS: 2000,
    terminalOutput: createTerminalOutput({ stderr, log: (...args) => terminalLog.push(args) }),
    showBackendCrash: error => crashes.push(error),
  };
  const start = vm.runInNewContext(`${startupSource}\nstartBackend`, context);
  return { start, child, stopped, crashes, logs, entries, terminalLog, context };
}

function emitReady(child) {
  child.stdout.emit('data', Buffer.from('GOFER_UI_READY {"port":43210,"apiToken":"test-token"}\n'));
}

test('slow backend stays alive and all readiness waiters resume after ten minutes', async t => {
  const { start, child, stopped, logs, entries } = harness(t);
  const ready = start(43210);
  let state = 'pending';
  ready.then(() => { state = 'ready'; }, () => { state = 'failed'; });
  const waiters = [ready.then(value => value), ready.then(value => value)];
  // Attach rejection handlers before advancing the old timeout.
  const results = Promise.allSettled(waiters);
  t.mock.timers.tick(10 * 60 * 1000);
  await Promise.resolve();
  assert.equal(state, 'pending');
  assert.equal(stopped.length, 0);
  child.stdout.emit('data', Buffer.from('Loading backend\r\nGOFER_UI_'));
  child.stdout.emit('data', Buffer.from('READY {"port":43210,"apiToken":"test-token"}\r\n'));
  for (const result of await results) {
    assert.equal(result.status, 'fulfilled');
    assert.equal(result.value.apiBaseUrl, 'http://127.0.0.1:43210');
    assert.equal(result.value.apiToken, 'test-token');
  }
  assert.equal(stopped.length, 0);
  assert.doesNotMatch(logs.join(''), /test-token/);
  assert.doesNotMatch(JSON.stringify(entries), /test-token/);
  assert.ok(entries.some(([level, source, message]) => level === 'info' && source === 'backend' && message === 'Loading backend'));
  assert.ok(entries.some(([level, , message]) => level === 'info' && /^Backend ready at http:\/\/127\.0\.0\.1:43210 after \d+\.\d s$/.test(message)));
});

test('backend log records keep their level and an unexpected exit is an error entry', async t => {
  const { start, child, crashes, entries } = harness(t);
  const ready = start();
  emitReady(child);
  await ready;
  const record = JSON.stringify({ level: 'warn', logger: 'gofer.devices.service', message: 'Phone could not sign in' });
  child.stderr.emit('data', Buffer.from(`GOFER_LOG ${record}\nTraceback (most recent call last):\nValueError: boom\n`));
  child.emit('exit', 1, null);
  t.mock.timers.tick(2000);
  assert.deepEqual(entries.find(entry => entry[3] === 'gofer.devices.service'), ['warn', 'devices', 'Phone could not sign in', 'gofer.devices.service']);
  assert.deepEqual(entries.find(entry => entry[3] === 'stderr'), ['error', 'backend', 'Traceback (most recent call last):\nValueError: boom', 'stderr']);
  assert.match(entries.at(-1)[2], /^Backend stopped unexpectedly after \d+\.\d s \(exit code 1\)$/);
  assert.equal(entries.at(-1)[0], 'error');
  // The crash window shows readable lines, not raw JSON.
  assert.match(crashes[0].message, /WARN gofer\.devices\.service: Phone could not sign in/);
  assert.doesNotMatch(crashes[0].message, /GOFER_LOG/);
});

test('backend spawn errors still reject startup', async t => {
  const { start, child, stopped } = harness(t);
  const rejected = assert.rejects(start(), /spawn failed/);
  child.emit('error', new Error('spawn failed'));
  await rejected;
  assert.equal(stopped.length, 1);
});

test('backend exit before readiness reports stderr even after a long wait', async t => {
  const { start, child, crashes } = harness(t);
  const rejected = assert.rejects(start(), /Unable to open database/);
  t.mock.timers.tick(10 * 60 * 1000);
  child.stderr.emit('data', Buffer.from('Unable to open database'));
  child.emit('exit', 1, null);
  await rejected;
  assert.equal(crashes.length, 0);
});

test('invalid readiness messages still reject startup', async t => {
  const { start, child, stopped } = harness(t);
  const rejected = assert.rejects(start(), /valid port/);
  child.stdout.emit('data', Buffer.from('GOFER_UI_READY {}\n'));
  await rejected;
  assert.equal(stopped.length, 1);
});

test('backend crash after readiness still reaches crash handling', async t => {
  const { start, child, crashes } = harness(t);
  const ready = start();
  emitReady(child);
  await ready;
  child.emit('exit', 1, null);
  // Ctrl+C reaches the backend and Electron together, so a crash waits for a quit to start.
  t.mock.timers.tick(1999);
  assert.equal(crashes.length, 0);
  t.mock.timers.tick(1);
  assert.equal(crashes.length, 1);
  assert.match(crashes[0].message, /Backend exited with code 1/);
});

test('feature preview choices persist and fall back to the launch environment', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-features-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'feature-preview.json');
  const env = { RATICODE_EXPERIMENTAL_ORGANIZATIONS: '1' };
  assert.deepEqual(readFeatureChoices(file), {});
  assert.deepEqual(featureStates({}, env), { organizations: true, 'device-network': false });
  writeFeatureChoice(file, 'device-network', true);
  writeFeatureChoice(file, 'organizations', false);
  assert.deepEqual(readFeatureChoices(file), { 'device-network': true, organizations: false });
  assert.deepEqual(featureStates(readFeatureChoices(file), env), { organizations: false, 'device-network': true });
  assert.throws(() => writeFeatureChoice(file, 'swarms', true), /Unknown feature preview/);
  assert.throws(() => writeFeatureChoice(file, 'organizations', 'yes'), /Choose on or off/);
  fs.writeFileSync(file, '{not json');
  assert.deepEqual(readFeatureChoices(file), {});
  assert.deepEqual(featureEnv({}, env), env);
});

test('backend lines echoed into a closed terminal stop quietly and stay in the app log', async t => {
  const stderr = closedPipe();
  const { start, child, entries, terminalLog } = harness(t, { stderr });
  const uncaught = [];
  const onUncaught = error => uncaught.push(error);
  process.on('uncaughtException', onUncaught);
  t.after(() => process.removeListener('uncaughtException', onUncaught));
  const ready = start();
  emitReady(child);
  await ready;
  const record = message => `GOFER_LOG ${JSON.stringify({ level: 'info', logger: 'gofer.ui.server', message })}\n`;
  child.stderr.emit('data', Buffer.from(record('Shutting down')));
  await new Promise(resolve => setImmediate(resolve));
  // The same burst the backend writes while Ctrl+C stops it.
  for (let index = 0; index < 50; index += 1) child.stderr.emit('data', Buffer.from(record(`Stopping service ${index}`)));
  child.stderr.emit('data', Buffer.from('KeyboardInterrupt\n'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(uncaught.length, 0);
  assert.equal(stderr.writes, 1, 'echo stops after the first failed write');
  assert.deepEqual(terminalLog, [['info', "The terminal stopped reading Raticode's stderr (EPIPE). Later output goes only to the app log."]]);
  assert.ok(entries.some(([, , message]) => message === 'Stopping service 49'));
  assert.ok(entries.some(([, , message]) => message === 'KeyboardInterrupt'));
});

test('a backend stopped by the terminal Ctrl+C is logged, not shown as a crash', async t => {
  // Electron's own SIGINT handler runs before-quit, which sets quitPending; the
  // backend's graceful shutdown on the same SIGINT can finish first or second.
  for (const quitStarts of ['before', 'after']) await t.test(`quit starts ${quitStarts} the backend exits`, async st => {
    const { start, child, crashes, entries, context } = harness(st);
    const ready = start();
    emitReady(child);
    await ready;
    if (quitStarts === 'before') context.quitPending = true;
    child.emit('exit', 0, null);
    if (quitStarts === 'after') { st.mock.timers.tick(500); context.quitPending = true; }
    st.mock.timers.tick(2000);
    assert.equal(crashes.length, 0);
    assert.deepEqual(entries.filter(([level]) => level === 'error'), []);
    assert.match(entries.at(-1)[2], /^Backend stopped after \d+\.\d s \(exit code 0\)$/);
    assert.equal(entries.at(-1)[0], 'info');
    // Kept so a quit cancelled for unsaved files can still say the backend is gone.
    assert.equal(context.backendStoppedDuringQuit, true);
  });
});
