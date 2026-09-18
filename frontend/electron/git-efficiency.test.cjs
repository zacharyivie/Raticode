const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const childProcess = require("node:child_process");

test("Git status reuses repository locations and still detects external branch changes", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-git-cache-"));
  const calls = [];
  const module = { exports: {} };
  const localRequire = (name) => name === "node:child_process" ? {
    execFile(...args) { calls.push(args[1]); return childProcess.execFile(...args); },
  } : require(name);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "git-status.cjs"), "utf8"), { require: localRequire, module, process, Buffer });
  const { readGitStatus } = module.exports;
  try {
    childProcess.execFileSync("git", ["init", "-b", "main", root]);
    fs.writeFileSync(path.join(root, "new.txt"), "fixture");
    const first = await readGitStatus(root);
    assert.equal(first.branch, "main");
    assert.equal(first.entries[0].path, "new.txt");
    assert.equal(calls.length, 6);
    calls.length = 0;
    childProcess.execFileSync("git", ["-C", root, "checkout", "-b", "external"]);
    const second = await readGitStatus(root);
    assert.equal(second.branch, "external");
    assert.equal(calls.length, 4);
    assert.ok(calls.every((args) => !args.includes("rev-parse")));
    assert.deepEqual(JSON.parse(JSON.stringify(first.entries)), JSON.parse(JSON.stringify(second.entries)));
    calls.length = 0;
    fs.writeFileSync(path.join(root, '.git', 'MERGE_HEAD'), 'external-operation');
    const idle = await readGitStatus(root);
    assert.equal(idle.operation, 'merge');
    assert.equal(calls.length, 1);
    assert.ok(calls.every(args => ['status'].includes(args[2])));
    await module.exports.runGit(['-C', root, 'remote', 'add', 'origin', 'https://example.invalid/repo']);
    calls.length = 0;
    assert.deepEqual(Array.from((await readGitStatus(root)).remotes), ['origin']);
    assert.equal(calls.length, 4);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("idle status keeps files fresh while refreshing metadata every 15 seconds", async () => {
  const { readGitStatus } = require('./git-status.cjs');
  let now = 0;
  let branch = 'main', status = '', stashes = '';
  const calls = [];
  const runGit = async (args) => {
    calls.push(args);
    if (args[2] === 'rev-parse') return args.includes('--show-toplevel') ? '/repo' : '';
    if (args[2] === 'branch') return branch;
    if (args[2] === 'status') return `# branch.head ${branch}\0${status}`;
    if (args[2] === 'for-each-ref') return `main\n${branch}`;
    if (args[2] === 'rev-list') return '0 0';
    if (args[2] === 'stash') return stashes;
    return '';
  };
  const options = { runGit, now: () => now };
  await readGitStatus('/repo', options);
  stashes = 'stash@{0}';
  status = 'UU conflict.txt\0M  staged.txt\0';
  for (now = 2000; now < 15000; now += 2000) {
    const result = await readGitStatus('/repo', options);
    assert.equal(result.entries[0].status, '!');
    assert.equal(result.entries[1].staged, true);
    assert.equal(result.stashCount, 0);
  }
  assert.equal(calls.filter(args => args[2] === 'status').length, 8);
  for (const command of ['for-each-ref', 'remote', 'stash']) assert.equal(calls.filter(args => args[2] === command).length, 1);
  assert.equal((await readGitStatus('/repo', options)).stashCount, 1);
  branch = 'external';
  assert.equal((await readGitStatus('/repo', options)).branch, 'external');
  assert.equal(calls.filter(args => args[2] === 'stash').length, 3);
});

test('worktree reads share pending enumeration, never prune, and see external changes', async () => {
  const { readGitWorktrees } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-worktrees-'));
  const linked = path.join(root, 'linked');
  fs.mkdirSync(linked);
  let output = `worktree ${root}\nbranch refs/heads/main\n\nworktree ${linked}\nlocked reason\n\nworktree ${root}/missing\nprunable gone\n`;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const calls = [];
  const runGit = async args => {
    calls.push(args);
    if (args[2] === 'rev-parse') return root;
    await gate;
    return output;
  };
  try {
    const first = readGitWorktrees(root, { runGit });
    const second = readGitWorktrees(root, { runGit });
    await new Promise(resolve => setImmediate(resolve));
    release();
    const results = await Promise.all([first, second]);
    assert.equal(calls.filter(args => args[2] === 'worktree').length, 1);
    assert.equal(results[0].worktrees.length, 2);
    assert.equal(results[0].worktrees[1].locked, true);
    fs.rmSync(linked, { recursive: true });
    assert.equal((await readGitWorktrees(root, { runGit })).worktrees.length, 1);
    fs.mkdirSync(linked);
    output += `\nworktree ${linked}\nbranch refs/heads/new\n`;
    assert.equal((await readGitWorktrees(root, { runGit })).worktrees.at(-1).branch, 'new');
    assert.ok(calls.every(args => !args.includes('prune')));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('adding a worktree cannot reuse a listing begun before the mutation', async () => {
  const { readGitWorktrees, addGitWorktree } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-worktree-race-'));
  const linked = path.join(root, 'linked');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let lists = 0;
  let mutated = false;
  const runGit = async args => {
    if (args[2] === 'rev-parse') return root;
    if (args[2] === 'worktree' && args[3] === 'add') {
      fs.mkdirSync(linked);
      mutated = true;
    }
    if (args[2] !== 'worktree' || args[3] !== 'list') return '';
    lists += 1;
    const snapshot = `worktree ${root}\n\n${mutated ? `worktree ${linked}\n` : ''}`;
    if (lists === 1) await gate;
    return snapshot;
  };
  try {
    const stale = readGitWorktrees(root, { runGit });
    await new Promise(resolve => setImmediate(resolve));
    const added = await addGitWorktree(root, linked, 'feature', { runGit });
    assert.equal(added.worktrees.length, 2);
    assert.equal(lists, 2);
    release();
    assert.equal((await stale).worktrees.length, 1);
  } finally {
    release();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("branch deletion preserves checked-out and unmerged branches and refreshes the list", async () => {
  const { gitRepositoryAction, readGitStatus } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-branches-'));
  const git = (...args) => childProcess.execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: 'pipe' });
  try {
    git('init', '-b', 'main');
    git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com');
    git('commit', '--allow-empty', '-m', 'initial');
    git('branch', 'merged');
    git('switch', '-c', 'unmerged'); git('commit', '--allow-empty', '-m', 'unique'); git('switch', 'main');
    git('worktree', 'add', '-b', 'occupied', path.join(root, 'linked'));
    await readGitStatus(root);
    for (const branch of ['main', 'occupied', '--all', 'missing']) {
      await assert.rejects(gitRepositoryAction(root, 'branch-delete', branch));
      await assert.rejects(gitRepositoryAction(root, 'branch-delete-force', branch));
    }
    assert.deepEqual(await gitRepositoryAction(root, 'branch-delete', 'unmerged'), { branchDeleteUnmerged: true });
    const result = await gitRepositoryAction(root, 'branch-delete', 'merged');
    assert.equal(result.branch, 'main');
    assert.deepEqual(result.branches, ['main', 'occupied', 'unmerged']);
    const forced = await gitRepositoryAction(root, 'branch-delete-force', 'unmerged');
    assert.deepEqual(forced.branches, ['main', 'occupied']);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("history reports binary files and merge changes against the first parent", async () => {
  const { readGitHistory } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rem-history-'));
  const git = (...args) => childProcess.execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  try {
    git('init', '-b', 'main');
    git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Test');
    fs.writeFileSync(path.join(root, 'base.txt'), 'base\n');
    git('add', '.'); git('commit', '-m', 'base');
    git('checkout', '-b', 'feature');
    fs.writeFileSync(path.join(root, 'new.txt'), 'one\ntwo\n');
    fs.writeFileSync(path.join(root, 'icon.bin'), Buffer.from([0, 1, 2]));
    git('add', '.'); git('commit', '-m', 'feature');
    git('checkout', 'main'); git('merge', '--no-ff', 'feature', '-m', 'merge');
    const result = await readGitHistory(root);
    assert.equal(result.active, true);
    assert.equal(result.commits[0].subject, 'merge');
    assert.equal(result.commits[0].insertions, 2);
    assert.equal(result.commits[0].deletions, 0);
    assert.equal(result.commits[0].binaryFiles, 1);
    assert.equal(result.commits.find(commit => commit.subject === "feature").binaryFiles, 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("unchanged baselines reuse results and invalidate after external edits and commits", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taskurotta-baselines-"));
  const calls = [];
  const module = { exports: {} };
  const localRequire = name => name === "node:child_process" ? {
    execFile: (...args) => { calls.push(args[1]); return childProcess.execFile(...args); },
  } : require(name);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "git-status.cjs"), "utf8"), { require: localRequire, module, process, Buffer, structuredClone });
  const git = (...args) => childProcess.execFileSync("git", ["-C", root, ...args]);
  try {
    git("init", "-b", "main");
    git("config", "user.email", "test@example.invalid");
    git("config", "user.name", "Test");
    const files = Array.from({ length: 10 }, (_, i) => path.join(root, `${i}.txt`));
    files.forEach(file => fs.writeFileSync(file, "original\n"));
    git("add", "."); git("commit", "-m", "fixture");
    // Warm repository discovery before concurrently reading files.
    await module.exports.readGitFileBaseline(files[0]);
    await Promise.all(files.map(file => module.exports.readGitFileBaseline(file)));
    calls.length = 0;
    const results = await Promise.all(files.map(file => module.exports.readGitFileBaseline(file)));
    assert.ok(results.every(result => result.tracked && result.content === "original\n"));
    assert.equal(calls.length, 1, "Only shared status should spawn Git for ten unchanged files");
    fs.writeFileSync(files[0], "edited\n");
    const dirty = await module.exports.readGitFileBaseline(files[0]);
    assert.equal(dirty.modifiedContent, "edited\n");
    assert.equal(dirty.changed, true);
    git("add", "."); git("commit", "-m", "external commit");
    const committed = await module.exports.readGitFileBaseline(files[0]);
    assert.equal(committed.content, "edited\n");
    assert.equal(committed.changed, false);
    fs.unlinkSync(files[0]);
    assert.equal((await module.exports.readGitFileBaseline(files[0])).deleted, true);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('baseline cache avoids show/diff and invalidates external writes, index and HEAD', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-baseline-cache-'));
  const calls = [];
  const module = {exports:{}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'git-status.cjs'),'utf8'), {
    require: name => name === 'node:child_process' ? {execFile(...args){calls.push(args[1]);return childProcess.execFile(...args);}} : require(name), module, process, Buffer, structuredClone,
  });
  const read = module.exports.readGitFileBaseline;
  const git = (...args) => childProcess.execFileSync('git',['-C',root,...args],{stdio:'ignore'});
  const file = path.join(root,'fixture.txt');
  try {
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid');
    fs.writeFileSync(file,'original\n');git('add','.');git('commit','-m','fixture');
    await read(file);await read(file);calls.length=0;
    assert.equal((await read(file)).content,'original\n');
    assert.equal(calls.length,1);
    assert.equal(calls[0][2],'status');
    fs.writeFileSync(file,'modified\n');
    assert.equal((await read(file)).modifiedContent,'modified\n');
    git('add','.');
    assert.equal((await read(file,{group:'unstaged'})).content,'modified\n');
    git('commit','-m','external');
    assert.equal((await read(file)).content,'modified\n');
    git('checkout','HEAD~1','--','fixture.txt');
    assert.equal((await read(file,{group:'staged'})).modifiedContent,'original\n');
    fs.unlinkSync(file);
    assert.equal((await read(file)).deleted,true);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});


test("thread branch reads share cached work and never enumerate changed files", async () => {
  const { readGitBranches } = require('./git-status.cjs');
  const calls = [];
  const runner = async args => {
    calls.push(args);
    if (args.includes('rev-parse')) return '/repo';
    if (args.includes('for-each-ref')) return '';
    if (args.includes('branch')) return 'main\n';
    assert.fail(`Unexpected Git command: ${args}`);
  };
  const options = { runner, now: () => 0 };
  const [first, second] = await Promise.all([readGitBranches('/repo', options), readGitBranches('/repo', options)]);
  assert.deepEqual(first, { active: true, root: '/repo', branch: 'main', branches: [] });
  assert.deepEqual(second, first);
  await readGitBranches('/repo', options);
  assert.equal(calls.length, 3);
  await readGitBranches('/repo', { runner, now: () => 15001 });
  assert.equal(calls.length, 6);
});

test('Git metadata reads overlap and optional failures preserve successful fields', async () => {
  const { readGitStatus } = require('./git-status.cjs');
  const gate = Promise.withResolvers();
  const started = [];
  const runGit = async args => {
    const command = args[2];
    if (command === 'rev-parse') return args.includes('--show-toplevel') ? '/repo' : '';
    if (command === 'status') return '# branch.head main\0';
    started.push(command);
    await gate.promise;
    if (command === 'for-each-ref') throw new Error('refs unavailable');
    return command === 'remote' ? 'origin\n' : 'stash@{0}\nstash@{1}\n';
  };
  const pending = readGitStatus('/repo', { runGit });
  try {
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(started.sort(), ['for-each-ref', 'remote', 'stash']);
  } finally { gate.resolve(); }
  const result = await pending;
  assert.equal(result.active, true);
  assert.equal(result.branchesUnavailable, true);
  assert.deepEqual(result.remotes, ['origin']);
  assert.equal(result.stashCount, 2);
});

for (const conflict of [false, true]) {
  test(`diff reads overlap and preserve ${conflict ? 'conflict sides' : 'staged content and hunks'}`, async () => {
    const { readGitFileBaseline } = require('./git-status.cjs');
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-diff-concurrency-'));
    const gate = Promise.withResolvers();
    const started = [];
    const runGit = async args => {
      if (args[2] === 'rev-parse') return root;
      if (args[2] === 'status') return `${conflict ? 'UU' : 'M '} file.txt\0`;
      started.push(args[2] === 'show' ? args[3] : 'diff');
      await gate.promise;
      if (args[2] === 'diff') return '@@ -1 +1 @@\n-old\n+new\n';
      return args[3].startsWith(':3:') ? 'incoming\n' : args[3] === ':file.txt' ? 'new\n' : 'old\n';
    };
    const pending = readGitFileBaseline(path.join(root, 'file.txt'), { runGit, group: 'staged' });
    try {
      await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(started.sort(), (conflict ? [':2:file.txt', ':3:file.txt', ':file.txt', 'diff'] : ['HEAD:file.txt', ':file.txt', 'diff']).sort());
    } finally { gate.resolve(); }
    try {
      const result = await pending;
      assert.equal(result.content, 'old\n');
      assert.equal(result.modifiedContent, 'new\n');
      assert.equal(result.changed, true);
      assert.equal(result.hunks.length, 1);
      if (conflict) assert.equal(result.incomingContent, 'incoming\n');
    } finally { fs.rmSync(root, { recursive: true, force: true }); }
  });
}
