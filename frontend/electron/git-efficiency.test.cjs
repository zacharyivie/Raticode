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
    assert.ok(calls.every(args => args.includes('status')));
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
  let output = `worktree ${root}\0branch refs/heads/main\0\0worktree ${linked}\0locked reason\0\0worktree ${root}/missing\0prunable gone\0`;
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
    output += `\0worktree ${linked}\0branch refs/heads/new\0`;
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
    const snapshot = `worktree ${root}\0\0${mutated ? `worktree ${linked}\0` : ''}`;
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

test("branch cleanup removes outdated branches and their clean worktrees but keeps the newest", async () => {
  const { gitRepositoryAction } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-cleanup-'));
  const repo = path.join(root, 'repo');
  const previousGlobal = process.env.GIT_CONFIG_GLOBAL;
  process.env.GIT_CONFIG_GLOBAL = path.join(root, 'user.gitconfig');
  const git = (...args) => childProcess.execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: 'pipe' });
  const branches = () => git('for-each-ref', '--format=%(refname:lstrip=2)', 'refs/heads/').trim().split('\n');
  try {
    childProcess.execFileSync('git', ['init', '-b', 'main', repo]);
    git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com');
    git('commit', '--allow-empty', '-m', 'base');
    // Linear: A -> C1 -> B -> C2 -> C. A and B are behind C.
    git('branch', 'a'); git('switch', '-c', 'b');
    git('commit', '--allow-empty', '-m', 'c1'); git('switch', '-c', 'work');
    git('commit', '--allow-empty', '-m', 'c2'); git('branch', 'c');
    // Diverged: D2 on its own commit, D3 on another. D1 is behind both.
    git('switch', '-c', 'd1', 'a'); git('switch', '-c', 'd2'); git('commit', '--allow-empty', '-m', 'only d2');
    git('switch', '-c', 'd3', 'd1'); git('commit', '--allow-empty', '-m', 'only d3');
    git('switch', 'main');
    git('worktree', 'add', path.join(root, 'clean'), 'b');
    git('worktree', 'add', '-b', 'dirty', path.join(root, 'dirty'), 'a');
    fs.writeFileSync(path.join(root, 'dirty', 'notes.txt'), 'keep me');

    const plan = await gitRepositoryAction(repo, 'branch-cleanup-preview');
    assert.deepEqual(plan.remove.map(item => item.branch).sort(), ['a', 'b', 'd1']);
    assert.equal(plan.remove.find(item => item.branch === 'b').worktree, path.join(root, 'clean'));
    assert.deepEqual(plan.kept.map(item => [item.branch, item.reason]).sort(), [['dirty', 'Uncommitted changes'], ['main', 'Current worktree']]);
    // d1 sits on main's commit, so it is named after the protected branch rather than d2 or d3.
    assert.equal(plan.remove.find(item => item.branch === 'd1').newer, 'main');
    assert.equal(plan.remove.find(item => item.branch === 'b').newer, 'c');

    git('branch', 'late', 'a');
    await assert.rejects(gitRepositoryAction(repo, 'branch-cleanup', { items: plan.remove }), /changed/);
    git('branch', '-D', 'late');

    const result = await gitRepositoryAction(repo, 'branch-cleanup', { items: plan.remove });
    assert.deepEqual(result.cleanup.failed, []);
    assert.deepEqual(branches().sort(), ['c', 'd2', 'd3', 'dirty', 'main', 'work']);
    assert.equal(fs.existsSync(path.join(root, 'clean')), false);
    assert.equal(fs.readFileSync(path.join(root, 'dirty', 'notes.txt'), 'utf8'), 'keep me');
    assert.match(result.notice, /Deleted 3 branches and 1 worktree/);
    await assert.rejects(gitRepositoryAction(repo, 'branch-cleanup', { items: [] }), /no outdated branches/);
  } finally {
    if (previousGlobal === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = previousGlobal;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("branch cleanup removes branches whose commits are all in a protected branch and keeps work in progress", async () => {
  const { gitRepositoryAction } = require('./git-status.cjs');
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-cleanup-protected-')));
  const repo = path.join(root, 'repo');
  const tree = name => path.join(repo, '.worktrees', name);
  const managed = path.join(root, 'data', 'rem-worktrees');
  const previousGlobal = process.env.GIT_CONFIG_GLOBAL;
  process.env.GIT_CONFIG_GLOBAL = path.join(root, 'user.gitconfig');
  const git = (...args) => childProcess.execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: 'pipe' });
  const branches = () => git('for-each-ref', '--format=%(refname:lstrip=2)', 'refs/heads/').trim().split('\n').sort();
  let busy;
  try {
    childProcess.execFileSync('git', ['init', '-b', 'main', repo]);
    git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com');
    fs.writeFileSync(path.join(repo, '.gitignore'), '.worktrees\nnode_modules\n');
    git('add', '.gitignore'); git('commit', '-m', 'base');
    for (const name of ['dev', 'test', 'stage', 'main']) git('config', '--add', 'raticode.cleanupKeep', name);
    // The user's layout: the main folder is on test, dev and test point at the same commit,
    // and a finished feature branch was fast-forwarded into dev.
    git('switch', '-c', 'feature'); git('commit', '--allow-empty', '-m', 'feature work');
    git('branch', 'dev'); git('branch', 'test'); git('branch', 'no-worktree');
    for (const name of ['dirty', 'locked']) git('branch', name);
    // busy was worked on before: it was fast-forwarded, so it has history beyond its creation.
    git('branch', 'busy', 'main'); git('branch', '-f', 'busy', 'feature');
    git('switch', 'test');
    git('worktree', 'add', tree('feature'), 'feature');
    git('worktree', 'add', '-b', 'unique', tree('unique'), 'dev');
    fs.writeFileSync(path.join(tree('unique'), 'own.txt'), 'only here');
    childProcess.execFileSync('git', ['-C', tree('unique'), 'add', 'own.txt']);
    childProcess.execFileSync('git', ['-C', tree('unique'), 'commit', '-m', 'unique work']);
    git('worktree', 'add', tree('dirty'), 'dirty');
    fs.writeFileSync(path.join(tree('dirty'), 'draft.txt'), 'untracked draft');
    git('worktree', 'add', tree('busy'), 'busy');
    git('worktree', 'add', '--lock', tree('locked'), 'locked');
    git('worktree', 'add', '-b', 'rem/task', path.join(managed, 'run-1'), 'dev');
    // Just created for new work: identical to dev, clean, nothing committed yet.
    git('worktree', 'add', '-b', 'fresh', tree('fresh'), 'dev');
    // Two unprotected branches on the same commit: neither is behind the other.
    git('branch', 'twin-a', 'unique'); git('branch', 'twin-b', 'unique');
    // An ignored shared-packages link must be removed as a link, never followed.
    const shared = path.join(root, 'shared-node-modules');
    fs.mkdirSync(shared); fs.writeFileSync(path.join(shared, 'keep.js'), 'shared');
    fs.symlinkSync(shared, path.join(tree('feature'), 'node_modules'));
    if (process.platform === 'linux') busy = childProcess.spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { cwd: tree('busy'), stdio: 'ignore' });

    const options = { managedFolders: [managed] };
    const plan = await gitRepositoryAction(repo, 'branch-cleanup-preview', {}, options);
    assert.deepEqual(plan.remove.map(item => [item.branch, item.newer, item.worktree]).sort(), [
      ['feature', 'dev', tree('feature')],
      ...(busy ? [] : [['busy', 'dev', tree('busy')]]),
      ['no-worktree', 'dev', ''],
    ].sort());
    assert.deepEqual(plan.kept.map(item => [item.branch, item.reason]).sort(), [
      ...(busy ? [['busy', 'In use by a running program']] : []),
      // Protected branches behind the unique branch are listed as kept, never removed.
      ['dev', 'Always kept in this project'],
      ['dirty', 'Uncommitted changes'],
      ['fresh', 'No commits made on it yet'],
      ['locked', 'Worktree is locked'],
      ['main', 'Always kept in this project'],
      ['rem/task', 'Used by a Raticode task'],
      ['test', 'Current worktree'],
    ].sort());

    const result = await gitRepositoryAction(repo, 'branch-cleanup', { items: plan.remove }, options);
    assert.deepEqual(result.cleanup.failed, []);
    assert.deepEqual(branches(), ['dev', 'dirty', 'fresh', 'locked', 'main', 'rem/task', 'test', 'twin-a', 'twin-b', 'unique', ...(busy ? ['busy'] : [])].sort());
    assert.equal(fs.existsSync(tree('feature')), false);
    assert.equal(fs.readFileSync(path.join(shared, 'keep.js'), 'utf8'), 'shared');
    assert.equal(fs.readFileSync(path.join(tree('unique'), 'own.txt'), 'utf8'), 'only here');
    assert.equal(fs.readFileSync(path.join(tree('dirty'), 'draft.txt'), 'utf8'), 'untracked draft');
    for (const name of ['dirty', 'fresh', 'locked', 'unique', ...(busy ? ['busy'] : [])]) assert.equal(fs.existsSync(tree(name)), true);
    assert.equal(fs.existsSync(path.join(managed, 'run-1')), true);
  } finally {
    busy?.kill();
    if (previousGlobal === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = previousGlobal;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("branch cleanup rechecks every branch and worktree while it deletes", async () => {
  const { gitRepositoryAction, runGit } = require('./git-status.cjs');
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-cleanup-race-')));
  const repo = path.join(root, 'repo');
  const tree = name => path.join(root, name);
  const previousGlobal = process.env.GIT_CONFIG_GLOBAL;
  process.env.GIT_CONFIG_GLOBAL = path.join(root, 'user.gitconfig');
  const git = (...args) => childProcess.execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: 'pipe' });
  try {
    childProcess.execFileSync('git', ['init', '-b', 'main', repo]);
    git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com');
    git('commit', '--allow-empty', '-m', 'base');
    for (const name of ['dev', 'release']) git('config', '--add', 'raticode.cleanupKeep', name);
    git('switch', '-c', 'release'); git('commit', '--allow-empty', '-m', 'release only'); git('branch', 'via-release');
    git('switch', '-c', 'dev', 'main'); git('commit', '--allow-empty', '-m', 'landed');
    git('switch', 'main');
    // Finished branches that were fast-forwarded to dev's commit.
    for (const name of ['gains-file', 'gains-commit', 'moves', 'settled']) { git('branch', name); git('branch', '-f', name, 'dev'); }
    git('config', 'branch.settled.description', 'old settings');
    git('worktree', 'add', tree('gains-file'), 'gains-file');
    git('worktree', 'add', tree('gains-commit'), 'gains-commit');

    const plan = await gitRepositoryAction(repo, 'branch-cleanup-preview');
    assert.deepEqual(plan.remove.map(item => [item.branch, item.newer]).sort(), [
      ['gains-commit', 'dev'], ['gains-file', 'dev'], ['moves', 'dev'], ['settled', 'dev'], ['via-release', 'release'],
    ]);
    // After the plan is confirmed, other tools change things just before Raticode reaches each branch.
    const changes = [
      [args => args.includes('--quiet') && args.includes('refs/heads/gains-commit'),
        () => childProcess.execFileSync('git', ['-C', tree('gains-commit'), 'commit', '--allow-empty', '-m', 'meanwhile'])],
      [args => args.includes('remove') && args.includes(tree('gains-file')),
        () => fs.writeFileSync(path.join(tree('gains-file'), 'new.txt'), 'written meanwhile')],
      [args => args.includes('update-ref') && args.includes('refs/heads/moves'), () => git('branch', '-f', 'moves', 'release')],
      [args => args.includes('merge-base') && args.includes('refs/heads/release'), () => git('branch', '-f', 'release', 'main')],
    ];
    const runner = (args, ...rest) => {
      const index = changes.findIndex(([matches]) => matches(args));
      if (index >= 0) changes.splice(index, 1)[0][1]();
      return runGit(args, ...rest);
    };
    const result = await gitRepositoryAction(repo, 'branch-cleanup', { items: plan.remove }, { runGit: runner });
    assert.deepEqual(changes, []);
    assert.deepEqual(result.cleanup.removed.map(item => item.branch), ['settled']);
    const failed = Object.fromEntries(result.cleanup.failed.map(item => [item.branch, item.error]));
    assert.deepEqual(Object.keys(failed).sort(), ['gains-commit', 'gains-file', 'moves', 'via-release']);
    assert.equal(failed['gains-commit'], 'Branch moved.');
    assert.match(failed['gains-file'], /contains modified or untracked files/);
    assert.match(failed.moves, /moves/);
    assert.equal(failed['via-release'], 'release no longer contains its commits.');
    // Nothing that changed was lost.
    assert.match(git('log', '-1', '--format=%s', 'gains-commit'), /meanwhile/);
    assert.equal(fs.existsSync(tree('gains-commit')), true);
    assert.equal(fs.readFileSync(path.join(tree('gains-file'), 'new.txt'), 'utf8'), 'written meanwhile');
    assert.equal(git('rev-parse', 'moves').trim(), git('rev-parse', 'via-release').trim());
    assert.match(git('log', '-1', '--format=%s', 'via-release'), /release only/);
    assert.equal(git('for-each-ref', 'refs/heads/settled').trim(), '');
    assert.throws(() => git('config', 'branch.settled.description'));
  } finally {
    if (previousGlobal === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = previousGlobal;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("branch cleanup always keeps project and user patterns stored in Git config", async () => {
  const { gitRepositoryAction } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-cleanup-keep-'));
  const repo = path.join(root, 'repo');
  const previousGlobal = process.env.GIT_CONFIG_GLOBAL;
  process.env.GIT_CONFIG_GLOBAL = path.join(root, 'user.gitconfig');
  const git = (...args) => childProcess.execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: 'pipe' });
  try {
    childProcess.execFileSync('git', ['init', '-b', 'main', repo]);
    git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com');
    git('commit', '--allow-empty', '-m', 'base');
    for (const name of ['stage', 'test', 'release/1', 'old']) git('branch', name);
    git('commit', '--allow-empty', '-m', 'newer');
    git('branch', 'dev');

    const kept = await gitRepositoryAction(repo, 'branch-cleanup-keep', { scope: 'project', patterns: [' stage ', 'release/*', 'stage', ''] });
    assert.deepEqual(kept.keepPatterns, { project: ['stage', 'release/*'], user: [] });
    const plan = await gitRepositoryAction(repo, 'branch-cleanup-keep', { scope: 'user', patterns: ['test'] });
    assert.deepEqual(plan.keepPatterns.user, ['test']);
    assert.equal(git('config', '--local', '--get-all', 'raticode.cleanupKeep'), 'stage\nrelease/*\n');
    // dev points at main's commit and main is a protected default branch, so dev is a duplicate.
    assert.deepEqual(plan.remove.map(item => item.branch).sort(), ['dev', 'old']);
    assert.deepEqual(plan.kept.map(item => [item.branch, item.reason]).sort(), [
      ['release/1', 'Always kept in this project'], ['stage', 'Always kept in this project'], ['test', 'Always kept in all projects'],
    ]);
    await assert.rejects(gitRepositoryAction(repo, 'branch-cleanup-keep', { scope: 'project', patterns: ['--global'] }), /dash/);
    await assert.rejects(gitRepositoryAction(repo, 'branch-cleanup-keep', { scope: 'system', patterns: [] }), /project or user/);
    const cleared = await gitRepositoryAction(repo, 'branch-cleanup-keep', { scope: 'project', patterns: [] });
    assert.deepEqual(cleared.keepPatterns.project, []);
    assert.deepEqual(cleared.remove.map(item => item.branch).sort(), ['dev', 'old', 'release/1', 'stage']);
  } finally {
    if (previousGlobal === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = previousGlobal;
    fs.rmSync(root, { recursive: true, force: true });
  }
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
    fs.writeFileSync(path.join(root, 'base.txt'), 'changed\nadded\n');
    git('commit', '-am', 'edit');
    git('commit', '--allow-empty', '-m', 'empty');
    const [empty, edit, ...rest] = (await readGitHistory(root)).commits;
    assert.deepEqual([empty.subject, empty.insertions, empty.deletions, empty.binaryFiles], ['empty', 0, 0, 0]);
    assert.deepEqual([edit.subject, edit.insertions, edit.deletions, edit.binaryFiles], ['edit', 2, 1, 0]);
    assert.equal(rest.length, 3);
    git('reset', '--hard', 'HEAD~2');
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
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-baselines-"));
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
    assert.ok(calls[0].includes('status'));
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

test("graph history includes remote-only commits, peeled tags, merge parents and branch filters", async () => {
  const { readGitHistory } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rem-graph-'));
  const git = (...args) => childProcess.execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: 'pipe' }).trim();
  try {
    git('init', '-b', 'main');
    git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Test');
    assert.equal((await readGitHistory(root)).active, true);
    git('commit', '--allow-empty', '-m', 'base');
    const base = git('rev-parse', 'HEAD');
    for (const ref of ['remote', 'tag']) {
      const empty = await readGitHistory(root, { ref });
      assert.deepEqual(empty.commits, []);
      assert.equal(empty.active, true);
    }
    git('tag', '-a', 'v1', '-m', 'version');
    git('switch', '-c', 'remote-tip'); git('commit', '--allow-empty', '-m', 'remote only');
    const remote = git('rev-parse', 'HEAD');
    git('update-ref', 'refs/remotes/origin/main', remote);
    git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main');
    git('switch', 'main'); git('branch', '-D', 'remote-tip');
    git('commit', '--allow-empty', '-m', 'local only');
    let history = await readGitHistory(root);
    assert.equal(history.commits.length, 3);
    assert.ok(history.commits.find(commit => commit.hash === remote).labels.some(ref => ref.type === 'remote'));
    assert.ok(history.commits.find(commit => commit.hash === base).labels.some(ref => ref.type === 'tag'));
    assert.deepEqual(history.commits.find(commit => commit.hash === remote).parents, [base]);
    assert.ok(history.commits.find(commit => commit.isHead).labels.some(ref => ref.current));
    assert.ok(!history.refs.some(ref => ref.name === 'origin/HEAD'));
    history = await readGitHistory(root, { ref: 'refs/remotes/origin/main' });
    assert.deepEqual(history.commits.map(commit => commit.subject), ['remote only', 'base']);
    for (const [category, subjects] of [['local', ['local only', 'base']], ['remote', ['remote only', 'base']], ['tag', ['base']]]) {
      assert.deepEqual((await readGitHistory(root, { ref: category })).commits.map(commit => commit.subject), subjects);
    }
    const selectedRefs = ['refs/heads/main', 'refs/remotes/origin/main'];
    git('switch', '-c', 'unselected', base);
    git('commit', '--allow-empty', '-m', 'unselected work');
    git('switch', 'main');
    const combined = await readGitHistory(root, { refs: selectedRefs });
    assert.deepEqual(new Set(combined.commits.map(commit => commit.subject)), new Set(['local only', 'remote only', 'base']));
    assert.equal(combined.commits.length, 3, 'Shared ancestors must appear only once');
    assert.ok(combined.refs.some(ref => ref.name === 'unselected'), 'Unselected branches remain available in the picker');
    assert.deepEqual((await readGitHistory(root, { refs: [...selectedRefs, selectedRefs[0], 'refs/tags/v1'] })).commits.map(commit => commit.hash), combined.commits.map(commit => commit.hash));
    assert.deepEqual((await readGitHistory(root, { refs: [] })).commits, []);
    assert.ok((await readGitHistory(root, { refs: null })).commits.some(commit => commit.subject === 'unselected work'));
    for (const invalid of [['--all'], ['refs/heads/missing'], ['refs/heads/main', '--all'], [null], 'refs/heads/main']) {
      await assert.rejects(readGitHistory(root, { refs: invalid }), /existing history/);
    }
    git('remote', 'add', 'origin', root);
    git('branch', '--set-upstream-to=origin/main', 'main');
    const tracked = (await readGitHistory(root)).refs.find(ref => ref.current);
    assert.equal(tracked.upstream, 'origin/main');
    assert.equal(tracked.ahead, 1);
    assert.equal(tracked.behind, 1);
    for (const ref of ['--all', '__proto__', 'constructor']) await assert.rejects(readGitHistory(root, { ref }), /existing history/);
    git('merge', '--no-ff', 'origin/main', '-m', 'merge');
    assert.equal((await readGitHistory(root)).commits[0].parents.length, 2);
    const detached = git('rev-parse', 'HEAD');
    git('checkout', '--detach'); git('commit', '--allow-empty', '-m', 'detached work');
    assert.equal((await readGitHistory(root)).commits.find(commit => commit.isHead).subject, 'detached work');
    git('checkout', 'main');
    assert.equal(git('rev-parse', 'HEAD'), detached);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("history loads beyond the first page without losing ancestry or older branch tips", async () => {
  const { readGitHistory } = require('./git-status.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rem-graph-pages-'));
  const git = (...args) => childProcess.execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: 'pipe' }).trim();
  try {
    git('init', '-b', 'main');
    git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Test');
    for (let index = 0; index < 105; index++) {
      git('commit', '--allow-empty', '-m', `commit ${index}`);
      if (index === 0) git('update-ref', 'refs/remotes/origin/old', git('rev-parse', 'HEAD'));
    }
    const first = await readGitHistory(root);
    assert.equal(first.commits.length, 100);
    assert.equal(first.hasMore, true);
    const next = await readGitHistory(root, { limit: 200 });
    assert.equal(next.commits.length, 105);
    assert.equal(next.hasMore, false);
    assert.deepEqual(first.commits.map(commit => commit.hash), next.commits.slice(0, 100).map(commit => commit.hash));
    assert.ok(next.commits.at(-1).labels.some(ref => ref.name === 'origin/old'));
    assert.equal(first.commits.at(-1).parents[0], next.commits[100].hash);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
