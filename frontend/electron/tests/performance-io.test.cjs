const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { scanProject, searchProject } = require('../project-search.cjs');
const { parseGitStatus, readGitStatus } = require('../git-status.cjs');

function loggerWith(io) {
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app-log.cjs'), 'utf8'), {
    module, Buffer, require: name => name === 'node:fs' ? io : require(name),
  });
  return module.exports.createAppLog;
}

test('slow log storage never blocks write, bounds backlog, reports drops and drains on close', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const persisted = [];
  const create = loggerWith({
    promises: {
      mkdir: () => gate,
      stat: async () => ({ size: 0 }),
      appendFile: async (_file, line) => { persisted.push(JSON.parse(line)); },
    },
    writeSync: () => assert.fail('routine writes must not use synchronous I/O'),
  });
  const log = create('/logs', { maxQueueBytes: 300 });
  assert.equal(log.write('info', 'apiToken=source-secret', 'password=first-secret'), true);
  for (let i = 0; i < 1000; i++) log.write('info', 'backend', `message ${i}`);
  assert.equal(persisted.length, 0);
  let closed = false;
  const close = log.close().then(() => { closed = true; });
  await Promise.resolve();
  assert.equal(closed, false);
  assert.equal(log.write('info', 'backend', 'after close'), false);
  release(); await close;
  assert.ok(persisted.length <= 4);
  assert.match(persisted.at(-1).message, /Dropped \d+ log messages/);
  assert.ok(!JSON.stringify(persisted).includes('secret'));
  await log.close();
});

test('log writer failures resolve flush and emergency messages redact credentials', async () => {
  const errors = [];
  const log = loggerWith({
    promises: { mkdir: async () => { throw Object.assign(new Error('private'), { code: 'EACCES' }); } },
    writeSync: (_fd, text) => errors.push(text),
  })('/logs');
  log.write('error', 'backend', 'password=private');
  await log.flush();
  log.emergency('password=emergency-secret');
  assert.match(errors[0], /EACCES/);
  assert.ok(!errors.join('').includes('private'));
  assert.ok(!errors.join('').includes('emergency-secret'));
});

test('normal quit awaits the log drain and repeated quit requests share it', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
  const start = source.indexOf('app.on("before-quit",');
  const end = source.indexOf('app.on("window-all-closed"', start);
  let callback, release, closes = 0, quits = 0, prevented = 0;
  const gate = new Promise(resolve => { release = resolve; });
  vm.runInNewContext(source.slice(start, end), {
    app: { on: (_name, handler) => { callback = handler; }, quit: () => { quits++; } },
    archivesDrained: true, logsDrained: false, logsClosing: false, isQuitting: false,
    applicationLog: { close: () => { closes++; return gate; } },
    closeAllBrowsers() {}, closeTerminalEditorServer() {}, closeAllTerminals() {}, stopBackend() {},
  });
  const event = { preventDefault: () => { prevented++; } };
  callback(event); callback(event);
  assert.equal(prevented, 2); assert.equal(closes, 1); assert.equal(quits, 0);
  release(); await gate; await Promise.resolve();
  assert.equal(quits, 1);
  callback(event); assert.equal(prevented, 2);
});

test('porcelain v2 preserves special paths, renames, conflicts and submodule changes', () => {
  const output = [
    '# branch.head main', '# branch.ab +2 -3',
    '1 .M N... 100644 100644 100644 abc abc space\nand tab\t.txt',
    '2 R. N... 100644 100644 100644 abc abc R100 new name.txt', 'old\nname.txt',
    'u UU N... 100644 100644 100644 100644 abc abc abc conflict.txt',
    '1 .M S.MU 160000 160000 160000 abc abc submodule', '? unknown file.txt', '',
  ].join('\0');
  const entries = parseGitStatus(output);
  assert.equal(entries.length, 5);
  assert.equal(entries[0].path, 'space\nand tab\t.txt');
  assert.equal(entries[1].originalPath, 'old\nname.txt');
  assert.equal(entries[1].staged, true); assert.equal(entries[1].unstaged, false);
  assert.equal(entries[2].status, '!'); assert.equal(entries[2].unstaged, true);
  assert.equal(entries[3].status, 'M'); assert.equal(entries[4].status, 'U');
});

test('Git headers preserve upstream counts, detached HEAD and subdirectory paths', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'taskurotta-v2-'));
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  try {
    git('init', '-b', 'main'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid');
    git('commit', '--allow-empty', '-m', 'initial'); git('branch', 'upstream');
    git('branch', '--set-upstream-to=upstream'); git('commit', '--allow-empty', '-m', 'ahead');
    const sub = path.join(root, 'sub'); fs.mkdirSync(sub); fs.writeFileSync(path.join(sub, 'file.txt'), 'new');
    const status = await readGitStatus(sub);
    assert.equal(status.branch, 'main'); assert.equal(status.ahead, 1); assert.equal(status.behind, 0);
    assert.equal(status.entries[0].path, 'file.txt');
    git('checkout', '--detach');
    const detached = await readGitStatus(root);
    assert.equal(detached.branch, ''); assert.equal(detached.ahead, null); assert.equal(detached.behind, null);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('bounded search reads preserve ordering, replacement, limits and binary filtering', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'taskurotta-search-pool-'));
  try {
    for (let i = 0; i < 12; i++) fs.writeFileSync(path.join(root, `${i}.txt`), 'needle\r\nneedle\n');
    fs.writeFileSync(path.join(root, 'binary.txt'), Buffer.from([0, 110, 101, 101, 100, 108, 101]));
    fs.writeFileSync(path.join(root, 'large.txt'), 'needle'.repeat(400000));
    fs.mkdirSync(path.join(root, 'node_modules')); fs.writeFileSync(path.join(root, 'node_modules', 'hidden.txt'), 'needle');
    const options = { query: 'needle', replacement: 'changed', include: '*.txt' };
    const serial = await scanProject(root, { ...options, readConcurrency: 1 });
    const parallel = await searchProject(root, { ...options, readConcurrency: 4 });
    assert.deepEqual(parallel, serial); assert.equal(parallel.count, 24); assert.equal(parallel.skipped, 1);
    fs.writeFileSync(path.join(root, '0.txt'), 'needle\n'.repeat(1100));
    const limited = await scanProject(root, { ...options, readConcurrency: 4 });
    assert.equal(limited.count, 1000); assert.equal(limited.truncated, true);
    assert.deepEqual(limited, await scanProject(root, { ...options, readConcurrency: 1 }));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('search clamps concurrent reads to four even for an oversized request', async () => {
  let active = 0, peak = 0;
  const module = { exports: {} };
  const root = path.resolve('/fixture');
  const io = {
    realpath: async target => target,
    stat: async () => ({ isFile: () => true, size: 6 }),
    readFile: async () => {
      peak = Math.max(peak, ++active);
      await new Promise(resolve => setTimeout(resolve, 2));
      active--;
      return Buffer.from('needle');
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../project-search.cjs'), 'utf8'), {
    module, process, Buffer,
    require: name => name === 'node:fs/promises' ? io : name === 'node:child_process' ? {
      execFile: (_command, _args, _options, callback) => callback(null, { stdout: Array.from({ length: 20 }, (_, i) => `${i}.txt`).join('\0') }),
    } : require(name),
  });
  const result = await module.exports.scanProject(root, { query: 'needle', readConcurrency: 100000 });
  assert.equal(result.count, 20); assert.equal(peak, 4); assert.equal(active, 0);
});

test('parallel search rejects links in both files and parent directories', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'taskurotta-search-links-'));
  try {
    const project = path.join(root, 'project'), outside = path.join(root, 'outside');
    fs.mkdirSync(project); fs.mkdirSync(outside);
    fs.writeFileSync(path.join(outside, 'secret.txt'), 'needle');
    fs.writeFileSync(path.join(project, 'safe.txt'), 'needle');
    execFileSync('git', ['init', project], { stdio: 'pipe' });
    try {
      fs.symlinkSync(path.join(outside, 'secret.txt'), path.join(project, 'file.txt'));
      fs.symlinkSync(outside, path.join(project, 'directory'), 'dir');
    } catch (error) {
      if (['EPERM', 'EACCES'].includes(error.code)) { t.skip('OS does not permit test symlinks'); return; }
      throw error;
    }
    const result = await searchProject(project, { query: 'needle', readConcurrency: 4 });
    assert.deepEqual(result.files.map(file => file.relativePath), ['safe.txt']);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('rotation contention uses the emergency path without rejecting shutdown', async () => {
  const errors = [];
  const log = loggerWith({
    promises: {
      mkdir: async () => {}, stat: async () => ({ size: 1000 }),
      unlink: async () => { throw Object.assign(new Error('locked'), { code: 'EBUSY' }); },
    },
    writeSync: (_fd, message) => errors.push(message),
  })('/logs', { maxBytes: 100 });
  log.write('info', 'backend', 'line');
  await log.close();
  assert.equal(errors.length, 1); assert.match(errors[0], /EBUSY/);
});

test('unprintable log messages cannot throw into desktop event handlers', async () => {
  const errors = [];
  const log = loggerWith({ writeSync: (_fd, message) => errors.push(message) })('/logs');
  assert.equal(log.write('error', 'desktop', Object.create(null)), false);
  await log.close();
  assert.match(errors[0], /could not format/);
});
