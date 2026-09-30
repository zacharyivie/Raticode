const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const { scanProject, replaceProject } = require('../project-search.cjs');
const { createHash } = require('node:crypto');
const safeFiles = require('../safe-files.cjs');
const digest = buffer => createHash('sha256').update(buffer).digest('hex');

test('copy rejects a source replaced by a FIFO without waiting for a writer', { skip: process.platform === 'win32' }, t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-copy-fifo-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const destination = path.join(root, 'destination');
  fs.writeFileSync(source, 'original');
  // Run in a child so the regression cannot strand a blocked libuv open in the suite.
  const result = spawnSync(process.execPath, ['-e', `
    const fs = require('node:fs');
    const { execFileSync } = require('node:child_process');
    const assert = require('node:assert/strict');
    const safeFiles = require(process.argv[1]);
    const source = process.argv[2];
    const destination = process.argv[3];
    const lstat = fs.promises.lstat;
    let swapped = false;
    fs.promises.lstat = async (...args) => {
      const stat = await lstat(...args);
      if (!swapped && args[0] === source) {
        swapped = true;
        fs.unlinkSync(source);
        execFileSync('mkfifo', [source]);
      }
      return stat;
    };
    (async () => {
      await assert.rejects(safeFiles.copyPath(source, destination, {
        authorizeSource() {}, authorizeDestination() {},
      }), /Source changed/);
      assert.equal(swapped, true);
      assert.equal(fs.existsSync(destination), false);
    })().catch(error => { console.error(error); process.exitCode = 1; });
  `, require.resolve('../safe-files.cjs'), source, destination], { timeout: 3000, encoding: 'utf8' });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, result.stderr);
});

test('copy still streams ordinary files across multiple buffers', async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-copy-ordinary-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const destination = path.join(root, 'destination');
  const content = Buffer.alloc(150000, 'x');
  fs.writeFileSync(source, content);
  await safeFiles.copyPath(source, destination, { authorizeSource() {}, authorizeDestination() {} });
  assert.deepEqual(fs.readFileSync(destination), content);
  assert.deepEqual(fs.readFileSync(source), content);
});

test('copy rejects a destination inside its source through a directory alias', async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-copy-alias-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const alias = path.join(root, 'alias');
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, 'note.txt'), 'keep this');
  try { fs.symlinkSync(source, alias, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) {
    if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Symlinks unavailable');
    throw error;
  }
  // Bound the regression even if a recursive copy starts creating nested folders.
  const mkdir = fsp.mkdir;
  let created = 0;
  t.mock.method(fsp, 'mkdir', async (...args) => {
    if (++created > 3) throw new Error('Recursive copy started');
    return mkdir(...args);
  });
  await assert.rejects(safeFiles.copyPath(source, path.join(alias, 'copy'), {
    authorizeSource() {}, authorizeDestination() {},
  }), /Cannot copy a directory into itself/);
  assert.equal(created, 0);
  assert.deepEqual(fs.readdirSync(source), ['note.txt']);
});

test('copy rejects a destination ancestor redirected into the source after validation', async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-copy-parent-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const parent = path.join(root, 'destination');
  fs.mkdirSync(source);
  fs.mkdirSync(parent);
  try {
    fs.symlinkSync(source, path.join(root, 'probe'), process.platform === 'win32' ? 'junction' : 'dir');
    fs.unlinkSync(path.join(root, 'probe'));
  } catch (error) {
    if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Symlinks unavailable');
    throw error;
  }
  const lstat = fsp.lstat;
  let swapped = false;
  t.mock.method(fsp, 'lstat', async (...args) => {
    const stat = await lstat(...args);
    if (!swapped && args[0] === source) {
      swapped = true;
      fs.renameSync(parent, path.join(root, 'moved'));
      fs.symlinkSync(source, parent, process.platform === 'win32' ? 'junction' : 'dir');
    }
    return stat;
  });
  await assert.rejects(safeFiles.copyPath(source, path.join(parent, 'copy'), {
    authorizeSource() {}, authorizeDestination() {},
  }), /Cannot copy a directory into itself/);
  assert.equal(swapped, true);
  assert.deepEqual(fs.readdirSync(source), []);
});

for (const growAfterStat of [false, true]) {
  test(`replacement bounds its hash read when a file grows ${growAfterStat ? 'after' : 'before'} stat`, async t => {
    const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-replace-growth-')));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const target = path.join(root, 'growing.txt');
    const original = Buffer.from('needle');
    const limit = 100;
    const grown = Buffer.alloc(1000, 'x');
    fs.writeFileSync(target, growAfterStat ? original : grown);
    let bytesRead = 0;
    const open = fsp.open;
    t.mock.method(fsp, 'open', async (...args) => {
      const handle = await open(...args);
      if (path.basename(String(args[0])) === 'growing.txt') {
        const stat = handle.stat.bind(handle);
        t.mock.method(handle, 'stat', async () => {
          const result = await stat();
          if (growAfterStat) fs.writeFileSync(target, grown);
          return result;
        });
        const read = handle.read.bind(handle);
        t.mock.method(handle, 'read', async (...values) => {
          const result = await read(...values);
          bytesRead += result.bytesRead;
          return result;
        });
        const readFile = handle.readFile.bind(handle);
        t.mock.method(handle, 'readFile', async (...values) => {
          const result = await readFile(...values);
          bytesRead += result.length;
          return result;
        });
      }
      return handle;
    });
    await assert.rejects(safeFiles.writeFile(target, 'replacement', {
      expectedHash: digest(original), digest, maxBytes: limit,
    }));
    assert.equal(bytesRead, growAfterStat ? limit + 1 : 0);
    assert.deepEqual(fs.readFileSync(target), grown);
  });
}

test('bounded replacement preserves successful edits and rejects stale hashes', async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-replace-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  execFileSync('git', ['init', root], { stdio: 'pipe' });
  const target = path.join(root, 'file.txt');
  fs.writeFileSync(target, 'needle needle');
  const search = await scanProject(root, { query: 'needle' });
  const result = await replaceProject(root, { query: 'needle', replacement: 'new', files: search.files });
  assert.equal(result.error, undefined);
  assert.equal(result.count, 2);
  assert.equal(fs.readFileSync(target, 'utf8'), 'new new');
  await assert.rejects(safeFiles.writeFile(target, 'lost edit', {
    expectedHash: search.files[0].hash, digest, maxBytes: 100,
  }), /changed since the search/);
  assert.equal(fs.readFileSync(target, 'utf8'), 'new new');
});

for (const swapParent of [false, true]) {
  test(`search rejects a ${swapParent ? 'parent' : 'file'} link swapped after path validation`, async t => {
    const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-search-race-')));
    t.after(() => fs.rmSync(base, { recursive: true, force: true }));
    const root = path.join(base, 'project');
    const parent = path.join(root, 'folder');
    const outside = path.join(base, 'outside');
    fs.mkdirSync(parent, { recursive: true });
    fs.mkdirSync(outside);
    const target = path.join(parent, 'file.txt');
    fs.writeFileSync(target, 'ordinary text');
    fs.writeFileSync(path.join(outside, 'file.txt'), 'needle private text');
    execFileSync('git', ['init', root], { stdio: 'pipe' });
    execFileSync('git', ['-C', root, 'add', '.'], { stdio: 'pipe' });
    // Check OS link privileges before installing the deterministic race.
    try {
      fs.symlinkSync(outside, path.join(base, 'probe'), 'dir');
      fs.unlinkSync(path.join(base, 'probe'));
    } catch (error) {
      if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Symlinks unavailable');
      throw error;
    }
    const realpath = fsp.realpath;
    let swapped = false;
    t.mock.method(fsp, 'realpath', async (...args) => {
      const result = await realpath(...args);
      if (!swapped && args[0] === target) {
        swapped = true;
        if (swapParent) {
          fs.renameSync(parent, path.join(root, 'moved'));
          fs.symlinkSync(outside, parent, 'dir');
        } else {
          fs.unlinkSync(target);
          fs.symlinkSync(path.join(outside, 'file.txt'), target);
        }
      }
      return result;
    });
    const result = await scanProject(root, { query: 'needle' });
    assert.equal(swapped, true);
    assert.equal(result.count, 0, 'outside text must never appear in search results');
    assert.deepEqual(result.files, []);
  });
}

test('search bounds the actual read when a file grows after its size check', async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-search-growth-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  execFileSync('git', ['init', root], { stdio: 'pipe' });
  const target = path.join(root, 'growing.txt');
  fs.writeFileSync(target, 'needle');
  const limit = 2 * 1024 * 1024;
  let grown = false;
  let bytesRead = 0;
  const grow = () => {
    if (!grown) { grown = true; fs.writeFileSync(target, 'needle'.repeat(limit)); }
  };
  const stat = fsp.stat;
  t.mock.method(fsp, 'stat', async (...args) => {
    const result = await stat(...args);
    if (args[0] === target) grow();
    return result;
  });
  const readFile = fsp.readFile;
  t.mock.method(fsp, 'readFile', async (...args) => {
    const result = await readFile(...args);
    if (args[0] === target) bytesRead += result.length;
    return result;
  });
  const open = fsp.open;
  t.mock.method(fsp, 'open', async (...args) => {
    const handle = await open(...args);
    if (path.basename(String(args[0])) === 'growing.txt') {
      const handleStat = handle.stat.bind(handle);
      t.mock.method(handle, 'stat', async (...values) => {
        const result = await handleStat(...values);
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
  const result = await scanProject(root, { query: 'needle' });
  assert.equal(grown, true);
  assert.equal(result.count, 0);
  assert.equal(result.skipped, 1);
  assert.ok(bytesRead > 0 && bytesRead <= limit + 1, `Read ${bytesRead} bytes`);
});

for (const swapParent of [false, true]) {
  test(`bounded reader rejects a ${swapParent ? 'parent' : 'file'} replaced after opening`, async t => {
    const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-open-race-')));
    t.after(() => fs.rmSync(base, { recursive: true, force: true }));
    const parent = path.join(base, 'parent');
    const outside = path.join(base, 'outside');
    fs.mkdirSync(parent);
    fs.mkdirSync(outside);
    const target = path.join(parent, 'file.txt');
    fs.writeFileSync(target, 'original');
    fs.writeFileSync(path.join(outside, 'file.txt'), 'private text');
    try {
      fs.symlinkSync(outside, path.join(base, 'probe'), 'dir');
      fs.unlinkSync(path.join(base, 'probe'));
    } catch (error) {
      if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Symlinks unavailable');
      throw error;
    }
    const open = fsp.open;
    let swapped = false;
    t.mock.method(fsp, 'open', async (...args) => {
      const handle = await open(...args);
      if (path.basename(String(args[0])) === 'file.txt') {
        try {
          if (swapParent) {
            fs.renameSync(parent, path.join(base, 'moved'));
            fs.symlinkSync(outside, parent, 'dir');
          } else {
            fs.unlinkSync(target);
            fs.symlinkSync(path.join(outside, 'file.txt'), target);
          }
          swapped = true;
        } catch (error) { await handle.close(); throw error; }
      }
      return handle;
    });
    await assert.rejects(require('../safe-files.cjs').readFile(target, { maxBytes: 100 }));
    assert.equal(swapped, true);
  });
}
