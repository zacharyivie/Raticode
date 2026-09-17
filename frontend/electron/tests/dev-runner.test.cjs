const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dev-runner.cjs'), 'utf8');
function launch(env, args = []) {
  let spawned;
  vm.runInNewContext(source, {
    require(name) {
      if (name === 'electron') return '/electron';
      if (name === './chromium-stderr.cjs') return { createChromiumStderrFilter: () => ({}) };
      if (name === 'node:child_process') return { spawn(command, argv, options) {
        spawned = { command, argv, options };
        return { stderr: { pipe: () => ({ pipe() {} }) }, on() {} };
      } };
      return require(name);
    },
    process: { env, argv: ['node', 'dev-runner.cjs', ...args], stderr: {} },
  });
  return spawned;
}
test('dev launcher separates its Electron profile under the requested XDG config root', () => {
  const result = launch({ XDG_CONFIG_HOME: '/tmp/taskurotta-dev/config' });
  assert.ok(result.argv.includes('--user-data-dir=/tmp/taskurotta-dev/config/raticode-dev'));
});
test('dev launcher preserves explicit profiles and forwards diagnostic flags', () => {
  const result = launch({}, ['--user-data-dir=/tmp/explicit', '--remote-debugging-port=9232']);
  assert.equal(result.argv.filter(arg => arg.startsWith('--user-data-dir')).length, 1);
  assert.ok(result.argv.includes('--remote-debugging-port=9232'));
});
