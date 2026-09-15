const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const cp = require('node:child_process');
const { performance } = require('node:perf_hooks');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'taskurotta-perf-git-'));
const calls = [];
const mod = { exports: {} };
vm.runInNewContext(fs.readFileSync(path.resolve('frontend/electron/git-status.cjs'), 'utf8'), { require: name => name === 'node:child_process' ? { execFile(...args) { calls.push(args[1]); return cp.execFile(...args); } } : require(name), module: mod, process, Buffer, structuredClone });
(async () => {
 try {
  const git = (...args) => cp.execFileSync('git', ['-C', root, ...args], { stdio: 'ignore' });
  git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid');
  const files = Array.from({length: 10}, (_, i) => path.join(root, `${i}.txt`));
  files.forEach(file => fs.writeFileSync(file, 'sample\n'.repeat(1000)));
  git('add', '.'); git('commit', '-m', 'fixture');
  const refresh = () => Promise.all(files.map(file => mod.exports.readGitFileBaseline(file)));
  await refresh();
  const samples = [];
  for (let i = 0; i < 5; i++) { calls.length = 0; const start = performance.now(); await refresh(); samples.push({ms: performance.now() - start, subprocesses: calls.length}); }
  console.log(JSON.stringify(samples, null, 2));
 } finally { fs.rmSync(root, {recursive:true, force:true}); }
})();
