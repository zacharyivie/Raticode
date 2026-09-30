const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { changeGitFile, readGitFileBaseline } = require('../git-status.cjs');

function fixture(t, beforeRead = () => {}) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-git-read-')));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const root = path.join(base, 'repo');
  const parent = path.join(root, 'folder');
  fs.mkdirSync(parent, { recursive: true });
  const target = path.join(parent, 'file.txt');
  fs.writeFileSync(target, 'resolved text\n');
  let staged = false;
  const runGit = async args => {
    if (args[2] === 'rev-parse') return root;
    if (args[2] === 'status') {
      beforeRead();
      return 'UU folder/file.txt\0';
    }
    if (args[2] === 'add') staged = true;
    if (args[2] === 'show') return 'original\n';
    return '';
  };
  return { base, root, parent, target, runGit, staged: () => staged };
}

for (const action of ['preview', 'stage']) {
  for (const swapParent of [false, true]) {
    test(`${action} rejects a ${swapParent ? 'parent' : 'file'} link replaced after status`, async t => {
      let swapped = false;
      const f = fixture(t, () => {
        if (swapped) return;
        swapped = true;
        if (swapParent) {
          fs.renameSync(f.parent, path.join(f.root, 'moved'));
          fs.symlinkSync(outside, f.parent, 'dir');
        } else {
          fs.unlinkSync(f.target);
          fs.symlinkSync(path.join(outside, 'file.txt'), f.target);
        }
      });
      const outside = path.join(f.base, 'outside');
      fs.mkdirSync(outside);
      fs.writeFileSync(path.join(outside, 'file.txt'), 'private outside text\n');
      try {
        fs.symlinkSync(outside, path.join(f.base, 'probe'), 'dir');
        fs.unlinkSync(path.join(f.base, 'probe'));
      } catch (error) {
        if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Symlinks unavailable');
        throw error;
      }
      if (action === 'preview') {
        const result = await readGitFileBaseline(f.target, { runGit: f.runGit });
        assert.equal(result.modifiedContent, undefined, 'outside content must not enter the preview');
      } else {
        await assert.rejects(changeGitFile(f.root, 'folder/file.txt', 'stage', { runGit: f.runGit }));
        assert.equal(f.staged(), false);
      }
      assert.equal(swapped, true);
    });
  }

  test(`${action} bounds reads when a file grows after stat`, async t => {
    const f = fixture(t);
    const limit = 16 * 1024 * 1024;
    let grown = false;
    let bytesRead = 0;
    const grow = () => {
      if (!grown) {
        grown = true;
        fs.writeFileSync(f.target, Buffer.alloc(limit + 1000, 'x'));
      }
    };
    const stat = fs.promises.stat;
    t.mock.method(fs.promises, 'stat', async (...args) => {
      const result = await stat(...args);
      if (args[0] === f.target) grow();
      return result;
    });
    const readFile = fs.promises.readFile;
    t.mock.method(fs.promises, 'readFile', async (...args) => {
      if (args[0] === f.target) grow();
      const result = await readFile(...args);
      if (args[0] === f.target) bytesRead += Buffer.byteLength(result);
      return result;
    });
    const open = fs.promises.open;
    t.mock.method(fs.promises, 'open', async (...args) => {
      const handle = await open(...args);
      if (path.basename(String(args[0])) === 'file.txt') {
        const handleStat = handle.stat.bind(handle);
        t.mock.method(handle, 'stat', async () => {
          const result = await handleStat();
          grow();
          return result;
        });
        const read = handle.read.bind(handle);
        t.mock.method(handle, 'read', async (...values) => {
          const result = await read(...values);
          bytesRead += result.bytesRead;
          return result;
        });
      }
      return handle;
    });
    if (action === 'preview') {
      const result = await readGitFileBaseline(f.target, { runGit: f.runGit });
      assert.equal(result.modifiedContent, undefined);
    } else {
      await assert.rejects(changeGitFile(f.root, 'folder/file.txt', 'stage', { runGit: f.runGit }), /size limit/);
      assert.equal(f.staged(), false);
    }
    assert.equal(grown, true);
    assert.equal(bytesRead, limit + 1);
  });

  test(`${action} rejects a FIFO without waiting for a writer`, { skip: process.platform === 'win32' }, t => {
    const f = fixture(t);
    const result = spawnSync(process.execPath, ['-e', `
      const fs = require('node:fs');
      const assert = require('node:assert/strict');
      const { execFileSync } = require('node:child_process');
      const { changeGitFile, readGitFileBaseline } = require(process.argv[1]);
      const [root, target, action] = process.argv.slice(2);
      const runGit = async args => {
        if (args[2] === 'rev-parse') return root;
        if (args[2] === 'status') {
          fs.unlinkSync(target);
          execFileSync('mkfifo', [target]);
          return 'UU folder/file.txt\\0';
        }
        if (args[2] === 'add') throw new Error('Must not stage');
        return '';
      };
      (async () => {
        if (action === 'preview') {
          const result = await readGitFileBaseline(target, { runGit });
          assert.equal(result.modifiedContent, undefined);
        } else {
          await assert.rejects(changeGitFile(root, 'folder/file.txt', 'stage', { runGit }), /linked or replaced/);
        }
      })().catch(error => { console.error(error); process.exitCode = 1; });
    `, require.resolve('../git-status.cjs'), f.root, f.target, action], { timeout: 3000, encoding: 'utf8' });
    assert.equal(result.error, undefined, result.error?.message);
    assert.equal(result.status, 0, result.stderr);
  });
}

for (const conflict of [false, true]) {
  test(`conflict staging ${conflict ? 'rejects remaining markers' : 'accepts resolved text'}`, async t => {
    const f = fixture(t);
    if (conflict) fs.writeFileSync(f.target, '<<<<<<< ours\nours\n=======\ntheirs\n>>>>>>> theirs\n');
    const result = changeGitFile(f.root, 'folder/file.txt', 'stage', { runGit: f.runGit });
    if (conflict) await assert.rejects(result, /Remove the conflict markers/);
    else await result;
    assert.equal(f.staged(), !conflict);
  });
}
