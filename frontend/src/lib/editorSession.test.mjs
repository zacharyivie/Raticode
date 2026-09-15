import test from 'node:test';
import assert from 'node:assert/strict';
import { loadEditorSession, saveEditorSession, loadWorkflowDraft, saveWorkflowDraft, EDITOR_SESSION_KEY } from './editorSession.js';
function memory() { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) }; }
test('versioned editor restoration preserves mixed tabs and their project identities', () => {
  const storage = memory();
  const graph = 'workflow-graph:atlas';
  saveEditorSession({ paths: [graph, '/beacon/config.py', 'browser:docs'], activePath: '/beacon/config.py', activity: 'search', workflowTabs: { [graph]: { workflowId: 'atlas', projectRoot: '/atlas', sourcePath: '/atlas/workflow.rattish' } }, browserTabs: { 'browser:docs': { url: 'https://example.test' } } }, storage);
  const restored = loadEditorSession(storage);
  assert.equal(restored.activePath, '/beacon/config.py');
  assert.equal(restored.workflowTabs[graph].projectRoot, '/atlas');
  assert.equal(restored.browserTabs['browser:docs'].url, 'https://example.test');
  assert.equal(restored.activity, 'search');
});
test('malformed and old sessions fall back without restoring execution', () => {
  const storage = memory();
  for (const raw of ['broken', 'null', '{"version":1,"paths":[]}', '{"version":2,"paths":null}']) {
    storage.setItem(EDITOR_SESSION_KEY, raw);
    assert.equal(loadEditorSession(storage), null);
  }
  storage.setItem(EDITOR_SESSION_KEY, JSON.stringify({ version: 2, paths: [null, 7, 'workflow-graph:missing', '/file', '/file'], activePath: '/gone', workflowTabs: {} }));
  assert.deepEqual(loadEditorSession(storage).paths, ['/file']);
  assert.equal(loadEditorSession(storage).activePath, '/file');
});
test('recovery drafts remain separate by source and clear only after that document saves', () => {
  const storage = memory();
  saveWorkflowDraft('/atlas/workflow.rattish', { source: 'A edited', savedRevision: 'a1', dirty: true }, storage);
  saveWorkflowDraft('/beacon/workflow.rattish', { source: 'B edited', savedRevision: 'b1', dirty: true }, storage);
  saveWorkflowDraft('/atlas/workflow.rattish', { source: 'A edited', dirty: false }, storage);
  assert.equal(loadWorkflowDraft('/atlas/workflow.rattish', storage), null);
  assert.deepEqual(loadWorkflowDraft('/beacon/workflow.rattish', storage), { source: 'B edited', savedRevision: 'b1' });
});

test('real browser tab identities and legacy browser tabs restore as browser documents', () => {
  const storage = memory();
  const actualPath = 'raticode-browser:1726178220-1';
  const legacyPath = 'browser:docs';
  saveEditorSession({ paths: [actualPath, legacyPath, '/project/readme.md'], activePath: actualPath, browserTabs: {
    [actualPath]: { url: 'https://example.test/current', title: 'Current browser' },
    [legacyPath]: { url: 'https://example.test/legacy', title: 'Legacy browser' },
  } }, storage);
  const restored = loadEditorSession(storage);
  assert.equal(restored.activePath, actualPath);
  assert.equal(restored.browserTabs[actualPath].url, 'https://example.test/current');
  assert.equal(restored.browserTabs[legacyPath].title, 'Legacy browser');
  assert.deepEqual(restored.paths, [actualPath, legacyPath, '/project/readme.md']);
});

test('orphan browser paths never restore as filesystem editors', () => {
  const storage = memory();
  saveEditorSession({ paths: ['raticode-browser:missing', 'browser:missing', '/file'], activePath: 'raticode-browser:missing', browserTabs: {} }, storage);
  assert.deepEqual(loadEditorSession(storage).paths, ['/file']);
  assert.equal(loadEditorSession(storage).activePath, '/file');
});

test('recovery reports quota failures without throwing or changing the live draft', () => {
  const document = { source: 'unsaved source', savedSource: 'disk source', savedRevision: 'r1', dirty: true };
  const fullStorage = { setItem() { throw new Error('QuotaExceededError'); }, removeItem() {} };
  assert.equal(saveWorkflowDraft('/project/workflow.rattish', document, fullStorage), false);
  assert.equal(document.source, 'unsaved source');
  assert.equal(document.dirty, true);
  assert.equal(saveWorkflowDraft('/project/workflow.rattish', document, {}), false);
  const storage = memory();
  assert.equal(saveWorkflowDraft('/project/workflow.rattish', document, storage), true);
  assert.equal(loadWorkflowDraft('/project/workflow.rattish', storage).savedSource, 'disk source');
  assert.equal(saveWorkflowDraft('/project/workflow.rattish', { ...document, dirty: false }, storage), true);
});

test('workflow recovery drafts remain available after extension migration', () => {
  const storage = memory();
  const draft = { source: 'Radish: 1', savedRevision: 'old', dirty: true };
  saveWorkflowDraft('/project/workflow.rad', draft, storage);
  assert.equal(loadWorkflowDraft('/project/workflow.rattish', storage).source, draft.source);
  saveWorkflowDraft('/project/workflow.rattish', { dirty: false }, storage);
  assert.equal(loadWorkflowDraft('/project/workflow.rad', storage), null);
});
