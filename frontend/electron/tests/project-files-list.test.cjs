require('./test-tmp.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { listProjectFiles } = require('../project-search.cjs');

function makeProject(t, prefix) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const write = (relative, text = 'x') => {
    fs.mkdirSync(path.dirname(path.join(root, relative)), { recursive: true });
    fs.writeFileSync(path.join(root, relative), text);
  };
  return { root, write };
}

test('quick open lists tracked and untracked files git does not ignore', (t) => {
  const { root, write } = makeProject(t, 'raticode-quick-open-git-');
  write('.gitignore', 'secret.log\n');
  write('src/App.jsx');
  write('README.md');
  write('notes/new.txt');
  write('secret.log');
  write('node_modules/pkg/index.js');
  const git = (...args) => execFileSync('git', ['-C', root, '-c', 'user.name=t', '-c', 'user.email=t@example.com', ...args], { stdio: 'ignore' });
  git('init', '-q');
  git('add', '.gitignore', 'src/App.jsx', 'README.md');
  git('commit', '-qm', 'init');
  return listProjectFiles(root).then(({ files, truncated }) => {
    const relative = files.map((file) => file.relativePath);
    assert.deepEqual(relative, ['.gitignore', 'README.md', 'notes/new.txt', 'src/App.jsx']);
    assert.equal(files.find((file) => file.relativePath === 'src/App.jsx').path, path.join(root, 'src', 'App.jsx'));
    assert.equal(truncated, false);
  });
});

test('quick open walks a folder outside git and skips dependency folders and links', { skip: process.platform === 'win32' }, async (t) => {
  const { root, write } = makeProject(t, 'raticode-quick-open-plain-');
  write('a.txt');
  write('deep/b.md');
  write('node_modules/c.js');
  write('.venv/d.py');
  fs.symlinkSync(path.join(root, 'deep'), path.join(root, 'linked'));
  // Keep git from finding a repository above the temporary folder.
  const previous = process.env.GIT_CEILING_DIRECTORIES;
  process.env.GIT_CEILING_DIRECTORIES = path.dirname(root);
  t.after(() => { if (previous === undefined) delete process.env.GIT_CEILING_DIRECTORIES; else process.env.GIT_CEILING_DIRECTORIES = previous; });
  const { files } = await listProjectFiles(root);
  assert.deepEqual(files.map((file) => file.relativePath), ['a.txt', 'deep/b.md']);
});
