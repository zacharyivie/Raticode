const fs = require('node:fs');
const path = require('node:path');
const { runGit, readGitStatus, parseGitWorktrees } = require('./git-status.cjs');

const lines = output => String(output).split('\n').filter(Boolean);
const KEEP_KEY = 'raticode.cleanupKeep';
const SCOPES = { project: '--local', user: '--global' };

// Kept-branch patterns live in Git config: --local is shared by every worktree of the
// repository and never committed; --global applies to all of the user's projects.
async function readKeepPatterns(git) {
  const read = async flag => { try { return lines(await git('config', flag, '--get-all', KEEP_KEY)); } catch { return []; } };
  return { project: await read(SCOPES.project), user: await read(SCOPES.user) };
}

const matchesPattern = (name, pattern) => new RegExp(`^${pattern.split('*').map(part => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')}$`).test(name);

function validPatterns(patterns) {
  if (!Array.isArray(patterns) || patterns.length > 100) throw new Error('Enter up to 100 branch names or patterns.');
  const clean = [...new Set(patterns.map(p => typeof p === 'string' ? p.trim() : ''))].filter(Boolean);
  if (clean.some(p => p.length > 200 || /[\s\0]/.test(p) || p.startsWith('-'))) throw new Error('Branch names and patterns cannot contain spaces or start with a dash.');
  return clean;
}

// A branch is stale when another local branch contains its tip and has more commits, or
// when a protected branch (default or always kept) points at the same commit: everything
// on it is already in that protected branch. Branches nothing else contains are kept.
async function staleBranches(git, isProtected) {
  const branches = lines(await git('for-each-ref', '--format=%(refname:lstrip=2)%00%(objectname)', 'refs/heads/'))
    .map(line => { const [name, hash] = line.split('\0'); return { name, hash }; });
  const tips = [...new Set(branches.map(b => b.hash))];
  const containers = new Map();
  const contain = (name, by) => {
    if (!by.length) return;
    if (!containers.has(name)) containers.set(name, []);
    containers.get(name).push(...by);
  };
  for (let i = 0; i < tips.length; i += 8) {
    await Promise.all(tips.slice(i, i + 8).map(async tip => {
      const atTip = branches.filter(b => b.hash === tip).map(b => b.name);
      for (const name of lines(await git('for-each-ref', `--merged=${tip}`, '--format=%(refname:lstrip=2)', 'refs/heads/'))) {
        if (branches.find(b => b.name === name)?.hash !== tip) contain(name, atTip);
        else if (!isProtected(name)) contain(name, atTip.filter(other => other !== name && isProtected(other)));
      }
    }));
  }
  return branches.filter(b => containers.has(b.name)).map(b => {
    const ahead = containers.get(b.name);
    return { ...b, newer: ahead.find(isProtected) || ahead.find(name => !containers.has(name)) || ahead[0] };
  });
}

const inside = (folder, parent) => {
  const relative = path.relative(parent, folder);
  return relative === '' || (!!relative && !relative.startsWith('..') && !path.isAbsolute(relative));
};
const realPath = folder => { try { return fs.realpathSync(folder); } catch { return path.resolve(folder); } };

// Programs working inside a worktree (an agent, a terminal, a dev server) are using it.
// Linux shows each process's working folder; elsewhere this finds nothing.
async function busyFolders() {
  if (process.platform !== 'linux') return [];
  let ids;
  try { ids = (await fs.promises.readdir('/proc')).filter(name => /^\d+$/.test(name)); } catch { return []; }
  const folders = await Promise.all(ids.map(id => fs.promises.readlink(`/proc/${id}/cwd`).catch(() => '')));
  return folders.filter(folder => path.isAbsolute(folder));
}

async function cleanupPlan(root, options) {
  const run = options.runGit || runGit;
  const git = (...args) => run(['-C', root, ...args]);
  const snapshot = await readGitStatus(root, options);
  if (!snapshot.active || path.resolve(snapshot.root) !== path.resolve(root)) throw new Error('Open the repository root to clean up branches.');
  const worktrees = parseGitWorktrees(await git('worktree', 'list', '--porcelain', '-z'));
  const mainWorktree = worktrees[0];
  const defaults = new Set(['main', 'master', ...lines(await git('for-each-ref', '--format=%(symref:lstrip=3)', 'refs/remotes/'))]);
  const keepPatterns = await readKeepPatterns(git);
  const isProtected = name => defaults.has(name) || [...keepPatterns.project, ...keepPatterns.user].some(p => matchesPattern(name, p));
  // Raticode's own task worktrees (Rem, swarms, organizations) are removed by their task.
  const managed = (options.managedFolders || []).filter(Boolean).map(realPath);
  const busy = await busyFolders();
  const remove = [], kept = [];
  for (const branch of await staleBranches(git, isProtected)) {
    const worktree = worktrees.find(w => w.branch === branch.name);
    const keep = reason => kept.push({ branch: branch.name, reason });
    if (worktree && path.resolve(worktree.path) === path.resolve(root)) { keep('Current worktree'); continue; }
    if (worktree === mainWorktree) { keep('Checked out in the main folder'); continue; }
    if (keepPatterns.project.some(p => matchesPattern(branch.name, p))) { keep('Always kept in this project'); continue; }
    if (keepPatterns.user.some(p => matchesPattern(branch.name, p))) { keep('Always kept in all projects'); continue; }
    if (defaults.has(branch.name)) { keep('Default branch'); continue; }
    if (worktree?.locked) { keep('Worktree is locked'); continue; }
    if (worktree && fs.existsSync(worktree.path)) {
      const folder = realPath(worktree.path);
      if (managed.some(parent => inside(folder, parent))) { keep('Used by a Raticode task'); continue; }
      if (busy.some(cwd => inside(cwd, folder))) { keep('In use by a running program'); continue; }
      try { await options.authorizeTarget?.(worktree.path); } catch { keep('No access to its worktree folder'); continue; }
      const status = await readGitStatus(worktree.path, { ...options, forceMetadata: true });
      if (!status.active) { keep('Could not read its worktree'); continue; }
      if (status.entries.length) { keep('Uncommitted changes'); continue; }
      if (status.operation) { keep(`${status.operation === 'rebase' ? 'Rebase' : 'Merge'} in progress`); continue; }
      // A worktree whose branch was only created is waiting for work, not finished with it.
      let history = [];
      try { history = lines(await git('log', '--walk-reflogs', '--format=%gs', `refs/heads/${branch.name}`, '--')); } catch { /* No reflog. */ }
      if (history.length === 1 && history[0].startsWith('branch: Created from ')) { keep('No commits made on it yet'); continue; }
    }
    remove.push({ branch: branch.name, hash: branch.hash, newer: branch.newer, worktree: worktree?.path || '' });
  }
  return { remove, kept, keepPatterns };
}

async function branchCleanupAction(root, action, value = {}, options = {}) {
  if (action === 'branch-cleanup-keep') {
    const flag = SCOPES[value.scope];
    if (!flag) throw new Error('Choose project or user settings.');
    const patterns = validPatterns(value.patterns);
    const run = options.runGit || runGit;
    const git = (...args) => run(['-C', root, ...args]);
    try { await git('config', flag, '--unset-all', KEEP_KEY); } catch { /* Nothing saved yet. */ }
    for (const pattern of patterns) await git('config', flag, '--add', KEEP_KEY, pattern);
    return cleanupPlan(root, options);
  }
  const plan = await cleanupPlan(root, options);
  if (action === 'branch-cleanup-preview') return plan;
  if (action !== 'branch-cleanup') throw new Error('Unknown cleanup action.');
  const key = items => JSON.stringify((items || []).map(({ branch, hash, worktree }) => [branch, hash, worktree || '']));
  if (!plan.remove.length) throw new Error('There are no outdated branches to clean up.');
  if (key(plan.remove) !== key(value.items)) throw new Error('Branches or worktrees changed. Review the cleanup again.');
  const run = options.runGit || runGit;
  const git = (...args) => run(['-C', root, ...args]);
  const removed = [], failed = [];
  for (const item of plan.remove) {
    try {
      // Agents and terminals keep working during cleanup. Recheck each branch right before removing it.
      let tip = '';
      try { tip = String(await git('rev-parse', '--verify', '--quiet', `refs/heads/${item.branch}`)).trim(); } catch { /* Deleted meanwhile. */ }
      if (tip !== item.hash) throw new Error('Branch moved.');
      try { await git('merge-base', '--is-ancestor', item.hash, `refs/heads/${item.newer}`); }
      catch { throw new Error(`${item.newer} no longer contains its commits.`); }
      if (item.worktree) {
        // Without --force, Git refuses a worktree that gained changes or a lock after the check.
        if (fs.existsSync(item.worktree)) await git('worktree', 'remove', item.worktree);
        else await git('worktree', 'prune', '--expire', 'now');
      }
      if (parseGitWorktrees(await git('worktree', 'list', '--porcelain', '-z')).some(w => w.branch === item.branch)) throw new Error('Checked out in a worktree.');
      // Deletes the branch only if it still points at the checked commit.
      await git('update-ref', '-d', `refs/heads/${item.branch}`, item.hash);
      try { await git('config', '--local', '--remove-section', `branch.${item.branch}`); } catch { /* No branch settings. */ }
      removed.push(item);
    } catch (error) {
      failed.push({ branch: item.branch, error: String(error.stderr || error.message).replace(/^Command failed:[^\n]*\n?/, '').trim() });
    }
  }
  const count = n => `${n} branch${n === 1 ? '' : 'es'}`;
  const worktreeCount = removed.filter(item => item.worktree).length;
  return {
    ...await readGitStatus(root, { ...options, forceMetadata: true }),
    cleanup: { removed, failed },
    notice: `Deleted ${count(removed.length)}${worktreeCount ? ` and ${worktreeCount} worktree${worktreeCount === 1 ? '' : 's'}` : ''}.${failed.length ? ` Kept ${count(failed.length)} that could not be removed.` : ''}`,
  };
}

module.exports = { branchCleanupAction, readKeepPatterns, matchesPattern };
