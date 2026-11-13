const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dev-runner.cjs'), 'utf8');
const os = require('node:os');
class Exit extends Error {}
function launch(env, args = [], alive = () => true) {
  let spawned;
  const stderr = [];
  vm.runInNewContext(source, {
    require(name) {
      if (name === 'electron') return '/electron';
      if (name === './chromium-stderr.cjs') return { createChromiumStderrFilter: () => ({}) };
      if (name === './gecko-display.cjs') return require('../gecko-display.cjs');
      if (name === 'node:child_process') return { spawn(command, argv, options) {
        spawned = { command, argv, options };
        return { stderr: { pipe: () => ({ pipe() {} }) }, on() {} };
      } };
      return require(name);
    },
    process: {
      env, argv: ['node', 'dev-runner.cjs', ...args], stderr: { write: text => stderr.push(text) },
      kill(pid, signal) {
        assert.equal(signal, 0);
        if (!alive(pid)) throw Object.assign(new Error('gone'), { code: 'ESRCH' });
      },
      exit(code) { throw new Exit(String(code)); },
    },
  });
  return spawned;
}
function launchOutcome(...args) {
  try {
    return { spawned: launch(...args) };
  } catch (error) {
    if (!(error instanceof Exit)) throw error;
    return { exit: Number(error.message) };
  }
}
function lockedProfile(owner) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-dev-lock-'));
  fs.symlinkSync(owner, path.join(profile, 'SingletonLock'));
  return profile;
}
test('dev launcher separates its Electron profile under the requested XDG config root', () => {
  const result = launch({ XDG_CONFIG_HOME: '/tmp/raticode-dev-fixture/config' });
  assert.ok(result.argv.includes('--user-data-dir=/tmp/raticode-dev-fixture/config/raticode-dev'));
});
test('dev launcher preserves explicit profiles and forwards diagnostic flags', () => {
  const result = launch({}, ['--user-data-dir=/tmp/explicit', '--remote-debugging-port=9232']);
  assert.equal(result.argv.filter(arg => arg.startsWith('--user-data-dir')).length, 1);
  assert.ok(result.argv.includes('--remote-debugging-port=9232'));
});
test('dev launcher refuses a profile another window holds instead of bringing that window forward', () => {
  const profile = lockedProfile(`${os.hostname()}-4242`);
  const outcome = launchOutcome({}, [`--user-data-dir=${profile}`], pid => pid === 4242);
  fs.rmSync(profile, { recursive: true, force: true });
  assert.equal(outcome.exit, 1);
  assert.equal(outcome.spawned, undefined);
});
test('dev launcher starts when the profile lock is stale or from another host', () => {
  for (const owner of [`${os.hostname()}-4242`, 'other-host-4243']) {
    const profile = lockedProfile(owner);
    const outcome = launchOutcome({}, ['--user-data-dir', profile], () => owner.startsWith('other-host'));
    fs.rmSync(profile, { recursive: true, force: true });
    assert.equal(outcome.spawned.command, '/electron');
  }
});

// Runs the launcher with event-emitting fakes for the process and Electron.
function launchWithSignals() {
  const { EventEmitter } = require('node:events');
  const fakeProcess = new EventEmitter();
  Object.assign(fakeProcess, {
    env: { XDG_CONFIG_HOME: '/tmp/raticode-dev-fixture/config' }, argv: ['node', 'dev-runner.cjs'],
    stderr: Object.assign(new EventEmitter(), { write() {} }), stdout: new EventEmitter(),
    raised: [], exits: [],
    kill(pid, signal) { fakeProcess.raised.push(signal); },
    exit(code) { fakeProcess.exits.push(code); },
  });
  const child = new EventEmitter();
  Object.assign(child, { exitCode: null, signalCode: null, killed: [], kill(signal) { child.killed.push(signal); } });
  const filtered = { piped: [], resumed: false, pipe(target) { filtered.piped.push(target); }, unpipe(target) { filtered.piped = filtered.piped.filter(item => item !== target); }, resume() { filtered.resumed = true; } };
  child.stderr = { pipe: () => filtered };
  vm.runInNewContext(source, {
    require(name) {
      if (name === 'electron') return '/electron';
      if (name === './chromium-stderr.cjs') return { createChromiumStderrFilter: () => ({}) };
      if (name === './gecko-display.cjs') return require('../gecko-display.cjs');
      if (name === 'node:child_process') return { spawn: () => child };
      return require(name);
    },
    process: fakeProcess,
  });
  return { fakeProcess, child, filtered };
}

test('dev launcher stays until Electron has quit after Ctrl+C and never passes the signal on', () => {
  const { fakeProcess, child } = launchWithSignals();
  // Ctrl+C reaches both processes; leaving now would close Electron's stderr under it.
  fakeProcess.emit('SIGINT', 'SIGINT');
  assert.deepEqual(fakeProcess.exits, []);
  assert.deepEqual(fakeProcess.raised, []);
  // Electron only handles the first signal; a second one would kill it outright.
  assert.deepEqual(child.killed, []);
  child.emit('close', 0, null);
  assert.deepEqual(fakeProcess.exits, [0]);
  assert.equal(fakeProcess.listenerCount('SIGINT'), 0);
});

test('a second Ctrl+C ends the dev launcher at once', () => {
  const { fakeProcess, child } = launchWithSignals();
  fakeProcess.emit('SIGINT', 'SIGINT');
  fakeProcess.emit('SIGINT', 'SIGINT');
  assert.deepEqual(fakeProcess.raised, ['SIGINT']);
  assert.equal(fakeProcess.listenerCount('SIGINT'), 0);
  assert.deepEqual(child.killed, []);
});

test('dev launcher re-raises the signal that ended Electron once its own handlers are gone', () => {
  const { fakeProcess, child } = launchWithSignals();
  child.emit('close', null, 'SIGTERM');
  assert.deepEqual(fakeProcess.raised, ['SIGTERM']);
  assert.equal(fakeProcess.listenerCount('SIGTERM'), 0);
});

test('dev launcher keeps draining Electron stderr after the terminal stops reading', () => {
  const { fakeProcess, filtered } = launchWithSignals();
  assert.deepEqual(filtered.piped, [fakeProcess.stderr]);
  fakeProcess.stderr.emit('error', Object.assign(new Error('write EPIPE'), { code: 'EPIPE' }));
  assert.deepEqual(filtered.piped, []);
  assert.equal(filtered.resumed, true);
  // A broken stdout is not an uncaught error either.
  assert.doesNotThrow(() => fakeProcess.stdout.emit('error', Object.assign(new Error('write EPIPE'), { code: 'EPIPE' })));
});
