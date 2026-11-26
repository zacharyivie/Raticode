const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const projectSearch = require('../project-search.cjs');
const safeFiles = require('../safe-files.cjs');

function project(t, files) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-replace-undo-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  execFileSync('git', ['init', root], { stdio: 'pipe' });
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(root, name), text);
  return root;
}
const read = (root, name) => fs.readFileSync(path.join(root, name), 'utf8');

test('search results carry each match replacement with regex captures filled in', async t => {
  const root = project(t, { 'a.py': 'say(hello12) say(hello3)\n', 'b.txt': 'x hello7 y\n' });
  const options = { query: 'hello(\\d+)', regex: true, replacement: '[$1|$&]' };
  const result = await projectSearch.searchProject(root, options);
  assert.deepEqual(result.files.map(file => file.matches.map(match => match.replacement)), [['[12|hello12]', '[3|hello3]'], ['[7|hello7]']]);
  assert.equal(result.files.some(file => 'content' in file || 'previous' in file), false, 'search results never carry whole files');
  // A lookbehind sees the whole line, as the replace that writes the file does.
  const behind = await projectSearch.searchProject(root, { query: '(?<=x )hello(\\d)', regex: true, replacement: 'n$1' });
  assert.deepEqual(behind.files.map(file => file.matches.map(match => match.replacement)), [['n7']]);
  const literal = await projectSearch.searchProject(root, { query: 'hello7', replacement: '$1' });
  assert.equal(literal.files[0].matches[0].replacement, '$1');
  const plain = await projectSearch.searchProject(root, { query: 'hello7' });
  assert.equal('replacement' in plain.files[0].matches[0], false);
});

test('undoReplace restores replaced files and skips, naming it, a file changed after the replace', async t => {
  const root = project(t, { 'a.py': 'hello one\r\nhello two\r\n', 'b.py': 'hello three\n', 'c.py': 'hello four\n' });
  const options = { query: 'hello', replacement: 'bye' };
  const found = await projectSearch.searchProject(root, options);
  const replaced = await projectSearch.replaceProject(root, { ...options, files: found.files });
  assert.equal(replaced.undoable, true);
  assert.equal(replaced.count, 4);
  assert.equal(read(root, 'a.py'), 'bye one\r\nbye two\r\n');
  fs.writeFileSync(path.join(root, 'b.py'), 'bye three, edited later\n');
  fs.rmSync(path.join(root, 'c.py'));
  const undone = await projectSearch.undoReplace(root);
  assert.deepEqual(undone.restored, [path.join(root, 'a.py')]);
  assert.deepEqual(undone.skipped.map(({ relativePath, changed }) => ({ relativePath, changed })), [
    { relativePath: 'b.py', changed: true },
    { relativePath: 'c.py', changed: true },
  ]);
  assert.equal(read(root, 'a.py'), 'hello one\r\nhello two\r\n');
  assert.equal(read(root, 'b.py'), 'bye three, edited later\n');
  await assert.rejects(projectSearch.undoReplace(root), /no replace to undo/);
});

test('undo after a replace that stopped partway puts back the files it already changed', async t => {
  const root = project(t, { 'a.py': 'hello a\n', 'b.py': 'hello b\n', 'c.py': 'hello c\n' });
  const options = { query: 'hello', replacement: 'bye' };
  const found = await projectSearch.searchProject(root, options);
  const writeFile = safeFiles.writeFile;
  t.mock.method(safeFiles, 'writeFile', async (target, ...rest) => {
    if (path.basename(target) === 'b.py') throw new Error('Disk full.');
    return writeFile(target, ...rest);
  });
  const replaced = await projectSearch.replaceProject(root, { ...options, files: found.files });
  assert.equal(replaced.error, 'b.py: Disk full.');
  assert.deepEqual(replaced.changed, [path.join(root, 'a.py')]);
  assert.equal(replaced.undoable, true);
  t.mock.restoreAll();
  const undone = await projectSearch.undoReplace(root);
  assert.deepEqual(undone, { restored: [path.join(root, 'a.py')], skipped: [] });
  assert.deepEqual(['a.py', 'b.py', 'c.py'].map(name => read(root, name)), ['hello a\n', 'hello b\n', 'hello c\n']);
});

test('only the latest replace in the same project can be undone', async t => {
  const root = project(t, { 'a.py': 'hello\n' });
  const other = project(t, { 'b.py': 'hello\n' });
  let found = await projectSearch.searchProject(root, { query: 'hello', replacement: 'one' });
  await projectSearch.replaceProject(root, { query: 'hello', replacement: 'one', files: found.files });
  found = await projectSearch.searchProject(root, { query: 'one', replacement: 'two' });
  await projectSearch.replaceProject(root, { query: 'one', replacement: 'two', files: found.files });
  await assert.rejects(projectSearch.undoReplace(other), /no replace to undo/);
  const undone = await projectSearch.undoReplace(root);
  assert.equal(undone.restored.length, 1);
  assert.equal(read(root, 'a.py'), 'one\n');
  await assert.rejects(projectSearch.undoReplace(root), /no replace to undo/);
});
