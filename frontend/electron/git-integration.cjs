const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runGit, readGitStatus, parseGitWorktrees } = require('./git-status.cjs');

// Preview in a disposable repository. Never touch the user's index or worktree.
async function integrationAction(root, action, value = {}, options = {}) {
  const run = options.runGit || runGit;
  const git = (...args) => run(['-C', root, ...args]);
  const snapshot = await readGitStatus(root, options);
  if (!snapshot.active || path.resolve(snapshot.root) !== path.resolve(root)) throw new Error('Open the repository root to manage merges and stashes.');
  if (action === 'stash-list') {
    const output = await git('stash', 'list', '--format=%gd%x00%H%x00%gs');
    return { stashes: String(output).trim().split('\n').filter(Boolean).map(line => {
      const [ref, hash, subject] = line.split('\0'); return { ref, hash, subject };
    }) };
  }
  if (action.startsWith('stash-')) {
    const { stashes } = await integrationAction(root, 'stash-list', {}, options);
    if (action === 'stash-clear') {
      if (JSON.stringify(stashes.map(s => s.hash)) !== JSON.stringify(value.hashes)) throw new Error('Stashes changed. Refresh and review them before discarding.');
      await git('stash', 'clear');
      return readGitStatus(root, options);
    }
    const stash = stashes.find(s => s.hash === value.hash);
    if (!stash) throw new Error('This stash is no longer available. Refresh the stash list.');
    if (action === 'stash-drop') { await git('stash', 'drop', stash.ref); return readGitStatus(root, options); }
    if (action === 'stash-preview') {
      const diff = String(await git('stash', 'show', '--include-untracked', '--patch', '--binary', '--no-ext-diff', '--no-color', stash.hash));
      if (snapshot.entries.length || snapshot.operation) return { diff, blocked: true, notice: 'Commit or stash current changes and finish any active merge or rebase before checking whether this stash applies cleanly.' };
      return preview(root, await git('rev-parse', 'HEAD'), ['stash', 'apply', '--index', stash.hash], diff, run);
    }
    if (action === 'stash-apply-selected') {
      if (snapshot.entries.length || snapshot.operation) throw new Error('Commit or stash current changes and finish the active operation before applying a stash.');
      await git('stash', 'apply', '--index', stash.hash);
      return readGitStatus(root, options);
    }
  }
  if (/^(merge|rebase)-(abort|continue)$/.test(action)) {
    const [kind, command] = action.split('-');
    if (snapshot.operation !== kind) throw new Error(`There is no ${kind} to ${command}.`);
    if (command === 'continue' && snapshot.entries.some(e => e.status === '!')) throw new Error('Resolve and stage every conflict before continuing.');
    await git('-c', 'core.editor=true', kind, `--${command}`);
    return readGitStatus(root, options);
  }
  if (!['merge-preview', 'merge-branch', 'rebase-preview', 'rebase-branch'].includes(action)) throw new Error('Unknown integration action.');
  const { source, target } = value;
  if (!snapshot.branches.includes(source) || !snapshot.branches.includes(target) || source === target) throw new Error('Choose two different local branches.');
  const rebase = action.startsWith('rebase-');
  const strategy = value.strategy || 'merge';
  const flags = { merge: [], squash: ['--squash'], 'ff-only': ['--ff-only'], 'no-ff': ['--no-ff'] }[strategy];
  if (!flags) throw new Error('Unknown merge strategy.');
  const worktrees = parseGitWorktrees(await git('worktree', 'list', '--porcelain'));
  const destination = worktrees.find(w => w.branch === (rebase ? source : target));
  const destinationRoot = destination?.path || root;
  await options.authorizeTarget?.(destinationRoot);
  const destinationStatus = await readGitStatus(destinationRoot, options);
  if (!destinationStatus.active || destinationStatus.entries.length || destinationStatus.operation) throw new Error('Commit or stash changes and finish the active operation in the destination worktree first.');
  const sourceRoot = worktrees.find(w => w.branch === source)?.path;
  if (sourceRoot) await options.authorizeTarget?.(sourceRoot);
  if (sourceRoot && (await readGitStatus(sourceRoot, options)).entries.length) throw new Error('Commit or stash changes in the source worktree first. Only committed changes can be merged.');
  const sourceHash = String(await git('rev-parse', `refs/heads/${source}`)).trim();
  const targetHash = String(await git('rev-parse', `refs/heads/${target}`)).trim();
  const diff = String(await git('diff', '--no-ext-diff', '--no-color', `${targetHash}...${sourceHash}`));
  if (action.endsWith('-preview')) return { ...await preview(root, rebase ? sourceHash : targetHash,
    rebase ? ['rebase', targetHash] : ['merge', '--no-commit', ...flags, sourceHash], diff, run), sourceHash, targetHash, destinationRoot };
  if (value.sourceHash !== sourceHash || value.targetHash !== targetHash) throw new Error('A branch changed since the preview. Preview again before proceeding.');
  const destGit = (...args) => run(['-C', destinationRoot, ...args]);
  if (!destination) await destGit('switch', '--no-guess', rebase ? source : target);
  try {
    await destGit('-c', 'core.editor=true', rebase ? 'rebase' : 'merge', ...(rebase ? [targetHash] : ['--no-edit', ...flags, sourceHash]));
  } catch (error) {
    const destinationStatus = await readGitStatus(destinationRoot, options);
    const conflicts = destinationStatus.entries.filter(entry => entry.status === '!').map(entry => entry.path);
    const result = { ...await readGitStatus(root, options), destinationRoot, destinationStatus };
    if (conflicts.length && (strategy === 'squash' || destinationStatus.operation === (rebase ? 'rebase' : 'merge'))) {
      return { ...result, conflicts, notice: `${rebase ? 'Rebase' : 'Merge'} paused in ${rebase ? source : target}. Resolve the files marked !, stage them, then ${strategy === 'squash' ? 'commit the squash result' : 'continue'}.` };
    }
    return { ...result, error: String(error.stderr || error.message) };
  }
  return { ...await readGitStatus(root, options), destinationRoot, notice: strategy === 'squash' ? `Squash changes from ${source} are staged in ${target}. Review and commit them before removing the source worktree.` : `${rebase ? 'Rebased' : 'Merged'} ${source} ${rebase ? 'onto' : 'into'} ${target}. You can now remove the source worktree when you no longer need it.` };
}

async function preview(root, head, command, diff, run) {
  const temp = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'raticode-git-preview-'));
  try {
    await run(['clone', '--shared', '--no-checkout', '--', root, temp]);
    const git = (...args) => run(['-C', temp, '-c', 'user.name=Raticode preview', '-c', 'user.email=preview@localhost', '-c', 'commit.gpgSign=false', '-c', 'core.hooksPath=/dev/null', ...args]);
    await git('checkout', '--detach', String(head).trim());
    let failure = '';
    try { await git(...command); } catch (error) { failure = String(error.stderr || error.message); }
    const conflicts = String(await git('diff', '--name-only', '--diff-filter=U', '-z')).split('\0').filter(Boolean);
    return { diff, conflicts, blocked: Boolean(failure && !conflicts.length), notice: conflicts.length ? `${conflicts.length} file${conflicts.length === 1 ? '' : 's'} will conflict. The operation will pause for resolution.` : failure || 'No conflicts found against the current committed versions.' };
  } finally { await fs.promises.rm(temp, { recursive: true, force: true }); }
}
module.exports = { integrationAction };
