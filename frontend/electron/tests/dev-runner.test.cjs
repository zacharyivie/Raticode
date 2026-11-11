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
