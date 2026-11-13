const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { verifyMetadata } = require('../../../scripts/verify-update-metadata.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-updater-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const name = 'Raticode-1.2.3-arm64.zip';
  const bytes = Buffer.from('final signed bytes');
  fs.writeFileSync(path.join(root, name), bytes);
  const sha512 = crypto.createHash('sha512').update(bytes).digest('base64');
  fs.writeFileSync(path.join(root, `${name}.blockmap`), zlib.gzipSync(JSON.stringify({ files: [{ sizes: [bytes.length] }] })));
  const doc = { version: '1.2.3', files: [{ url: name, sha512, size: bytes.length }], path: name, sha512 };
  const write = () => fs.writeFileSync(path.join(root, 'latest-mac.yml'), JSON.stringify(doc));
  write();
  return { root, doc, write, name };
}

test('final updater hashes accept complete metadata', (t) => {
  const { root } = fixture(t);
  assert.match(verifyMetadata(root, '1.2.3', 'darwin'), /all updater hashes match/);
});

for (const change of ['version', 'hash', 'size', 'legacy hash', 'missing file', 'changed bytes', 'traversal', 'empty files', 'dmg only', 'path not zip', 'no zip blockmap']) {
  test(`final updater hashes reject ${change}`, (t) => {
    const { root, doc, write, name } = fixture(t);
    if (change === 'version') doc.version = '1.2.2';
    if (change === 'hash') doc.files[0].sha512 = 'bad';
    if (change === 'size') doc.files[0].size += 1;
    if (change === 'legacy hash') doc.sha512 = 'bad';
    if (change === 'missing file') fs.unlinkSync(path.join(root, name));
    if (change === 'changed bytes') fs.writeFileSync(path.join(root, name), 'changed after signing');
    if (change === 'traversal') doc.files[0].url = '%2e%2e%2foutside.zip';
    if (change === 'empty files') doc.files = [];
    if (change === 'dmg only' || change === 'path not zip') {
      const dmg = 'Raticode-1.2.3-arm64.dmg';
      const bytes = Buffer.from('signed dmg');
      fs.writeFileSync(path.join(root, dmg), bytes);
      const entry = { url: dmg, sha512: crypto.createHash('sha512').update(bytes).digest('base64'), size: bytes.length };
      if (change === 'dmg only') doc.files = [entry];
      else doc.files.push(entry);
      doc.path = dmg;
      doc.sha512 = entry.sha512;
    }
    if (change === 'no zip blockmap') fs.unlinkSync(path.join(root, `${name}.blockmap`));
    write();
    assert.throws(() => verifyMetadata(root, '1.2.3', 'darwin'));
  });
}

test('only the Mac feed must point Squirrel.Mac at a zip', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-updater-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const name = 'Raticode-1.2.3-x86_64.AppImage';
  const bytes = Buffer.from('appimage');
  fs.writeFileSync(path.join(root, name), bytes);
  const sha512 = crypto.createHash('sha512').update(bytes).digest('base64');
  fs.writeFileSync(path.join(root, 'latest-linux.yml'), JSON.stringify({ version: '1.2.3', files: [{ url: name, sha512, size: bytes.length }], path: name, sha512 }));
  assert.match(verifyMetadata(root, '1.2.3', 'linux'), /all updater hashes match/);
});
