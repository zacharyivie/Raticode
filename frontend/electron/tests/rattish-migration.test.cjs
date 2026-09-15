const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { migrateSource } = require('../rattish-migration.cjs');

test('legacy sources migrate without changing bytes and old references resolve', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rattish-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const old = path.join(root, 'workflow.rad');
  const next = path.join(root, 'workflow.rattish');
  fs.writeFileSync(old, 'Radish: 1\r\n');
  const checked = [];
  assert.equal(migrateSource(old, value => { checked.push(value); return value; }), next);
  assert.deepEqual(checked, [old, next]);
  assert.equal(fs.readFileSync(next, 'utf8'), 'Radish: 1\r\n');
  assert.equal(fs.existsSync(old), false);
  assert.equal(migrateSource(old), next);
});

test('migration refuses a conflicting destination or denied authorization', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rattish-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const old = path.join(root, 'workflow.rad');
  const next = path.join(root, 'workflow.rattish');
  fs.writeFileSync(old, 'old');
  assert.throws(() => migrateSource(old, () => { throw new Error('denied'); }), /denied/);
  assert.equal(fs.existsSync(next), false);
  fs.writeFileSync(next, 'new');
  assert.throws(() => migrateSource(old), /already exists/);
  assert.equal(fs.readFileSync(old, 'utf8'), 'old');
  assert.equal(fs.readFileSync(next, 'utf8'), 'new');
});
