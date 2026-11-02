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

// A branch is stale when another local branch contains its tip and has more commits.
// Branches that nothing else contains are the newest ones and are always kept.
async function staleBranches(git) {
  const branches = lines(await git('for-each-ref', '--format=%(refname:lstrip=2)%00%(objectname)', 'refs/heads/'))
    .map(line => { const [name, hash] = line.split('\0'); return { name, hash }; });
  const tips = [...new Set(branches.map(b => b.hash))];
  const containers = new Map();
  for (let i = 0; i < tips.length; i += 8) {
    await Promise.all(tips.slice(i, i + 8).map(async tip => {
      for (const name of lines(await git('for-each-ref', `--merged=${tip}`, '--format=%(refname:lstrip=2)', 'refs/heads/'))) {
        if (branches.find(b => b.name === name)?.hash === tip) continue;
        if (!containers.has(name)) containers.set(name, []);
        containers.get(name).push(...branches.filter(b => b.hash === tip).map(b => b.name));
      }
    }));
  }
  return branches.filter(b => containers.has(b.name)).map(b => {
    const ahead = containers.get(b.name);
    return { ...b, newer: ahead.find(name => !containers.has(name)) || ahead[0] };
  });
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
  const remove = [], kept = [];
  for (const branch of await staleBranches(git)) {
    const worktree = worktrees.find(w => w.branch === branch.name);
    const keep = reason => kept.push({ branch: branch.name, reason });
    if (worktree && path.resolve(worktree.path) === path.resolve(root)) { keep('Current worktree'); continue; }
    if (worktree === mainWorktree) { keep('Checked out in the main folder'); continue; }
    if (keepPatterns.project.some(p => matchesPattern(branch.name, p))) { keep('Always kept in this project'); continue; }
    if (keepPatterns.user.some(p => matchesPattern(branch.name, p))) { keep('Always kept in all projects'); continue; }
    if (defaults.has(branch.name)) { keep('Default branch'); continue; }
    if (worktree?.locked) { keep('Worktree is locked'); continue; }
    if (worktree && fs.existsSync(worktree.path)) {
      try { await options.authorizeTarget?.(worktree.path); } catch { keep('No access to its worktree folder'); continue; }
      const status = await readGitStatus(worktree.path, { ...options, forceMetadata: true });
      if (!status.active) { keep('Could not read its worktree'); continue; }
      if (status.entries.length) { keep('Uncommitted changes'); continue; }
      if (status.operation) { keep(`${status.operation === 'rebase' ? 'Rebase' : 'Merge'} in progress`); continue; }
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
      if (item.worktree) {
        // Without --force, Git refuses a worktree that gained changes after the check.
        if (fs.existsSync(item.worktree)) await git('worktree', 'remove', item.worktree);
        else await git('worktree', 'prune', '--expire', 'now');
      }
      if (String(await git('rev-parse', '--verify', `refs/heads/${item.branch}`)).trim() !== item.hash) throw new Error('Branch moved.');
      await git('branch', '-D', '--', item.branch);
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

module.exports = { branchCleanupAction };
