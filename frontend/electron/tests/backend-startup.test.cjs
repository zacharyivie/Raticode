const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
const startupSource = source.slice(source.indexOf('function startBackend('), source.indexOf('function getBackendCommand()'));

function harness(t) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  const stopped = [];
  const crashes = [];
  const logs = [];
  const context = {
    process: { env: {}, stderr: { write() {} } },
    console: { log() {} },
    setTimeout, clearTimeout,
    // Keep the old deadline available so this test also fails against the old code.
    BACKEND_START_TIMEOUT_MS: 15000,
    BACKEND_READY_PREFIX: 'GOFER_UI_READY ',
    repoRoot: '/app', desktopGrantSecret: 'test-secret',
    getBackendCommand: () => ({ command: 'test-backend', args: [] }),
    getGoferDataDir: () => '/data',
    spawn: () => child,
    createBackendLogStream: () => null,
    closeBackendLogStream() {},
    writeBackendLog: line => logs.push(line),
    stopBackend: () => stopped.push(child),
    expectedBackendStops: new WeakSet(), isQuitting: false,
    showBackendCrash: error => crashes.push(error),
  };
  const start = vm.runInNewContext(`${startupSource}\nstartBackend`, context);
  return { start, child, stopped, crashes, logs };
}

function emitReady(child) {
  child.stdout.emit('data', Buffer.from('GOFER_UI_READY {"port":43210,"apiToken":"test-token"}\n'));
}

test('slow backend stays alive and all readiness waiters resume after ten minutes', async t => {
  const { start, child, stopped, logs } = harness(t);
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
  assert.equal(crashes.length, 1);
  assert.match(crashes[0].message, /Backend exited with code 1/);
});
