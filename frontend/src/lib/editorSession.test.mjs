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
test('the main content is restored apart from the sidebar activity', () => {
  const storage = memory();
  const restore = mainContent => { saveEditorSession({ paths: [], activity: 'source-control', mainContent }, storage); return loadEditorSession(storage); };
  assert.equal(restore('rem').mainContent, 'rem');
  assert.equal(restore('rem').activity, 'source-control');
  assert.equal(restore('second-brain').mainContent, 'second-brain');
  // A swarm or organization must be chosen again, so the editor is restored.
  assert.equal(restore('organizations').mainContent, 'editor');
  // Sessions saved before this field existed follow the sidebar activity.
  assert.equal(restore(undefined).mainContent, '');
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
  saveEditorSession({ paths: ['/project/readme.md'], activePath: '/project/readme.md', browserPaths: [actualPath, legacyPath], browserActivePath: actualPath, browserTabs: {
    [actualPath]: { url: 'https://example.test/current', title: 'Current browser', engine: 'edge' },
    [legacyPath]: { url: 'https://example.test/legacy', title: 'Legacy browser' },
  } }, storage);
  const restored = loadEditorSession(storage);
  assert.equal(restored.browserActivePath, actualPath);
  assert.equal(restored.browserTabs[actualPath].url, 'https://example.test/current');
  assert.equal(restored.browserTabs[actualPath].engine, 'edge');
  assert.equal(restored.browserTabs[legacyPath].title, 'Legacy browser');
  assert.deepEqual(restored.browserPaths, [actualPath, legacyPath]);
  assert.deepEqual(restored.paths, ['/project/readme.md']);
  assert.equal(restored.activePath, '/project/readme.md');
  // Older Raticode versions read `paths` only, so web tabs are listed there too.
  assert.deepEqual(JSON.parse(storage.getItem(EDITOR_SESSION_KEY)).paths, ['/project/readme.md', actualPath, legacyPath]);
});

test('sessions from before the Browser plugin move web tabs out of the file tabs without losing any', () => {
  const storage = memory();
  const web = 'raticode-browser:a';
  const music = 'raticode-browser:b';
  storage.setItem(EDITOR_SESSION_KEY, JSON.stringify({ version: 2, paths: ['/p/a.py', web, '/p/b.md', music], activePath: music, activity: 'files', mainContent: 'editor', browserTabs: {
    [web]: { url: 'https://docs.test/', title: 'Docs', history: { index: 0, entries: [{ url: 'https://docs.test/' }] } },
    [music]: { url: 'https://music.test/', title: 'Music' },
  } }));
  const restored = loadEditorSession(storage);
  assert.deepEqual(restored.paths, ['/p/a.py', '/p/b.md']);
  assert.equal(restored.activePath, '/p/a.py');
  assert.deepEqual(restored.browserPaths, [web, music]);
  assert.equal(restored.browserActivePath, music);
  assert.equal(restored.browserTabs[web].history.entries[0].url, 'https://docs.test/');
  // A web tab was in front, so the Browser plugin is.
  assert.equal(restored.mainContent, 'browser');
  assert.equal(restored.activity, 'browser');
  // With a file tab in front, the File explorer stays in front.
  storage.setItem(EDITOR_SESSION_KEY, JSON.stringify({ version: 2, paths: ['/p/a.py', web], activePath: '/p/a.py', activity: 'files', mainContent: 'editor', browserTabs: { [web]: { url: 'https://docs.test/' } } }));
  const files = loadEditorSession(storage);
  assert.equal(files.mainContent, 'editor');
  assert.equal(files.activity, 'files');
  assert.equal(files.browserActivePath, web);
  // Saved with the Browser plugin, the choice of area is kept as saved.
  saveEditorSession({ paths: ['/p/a.py'], activePath: '/p/a.py', browserPaths: [web], browserActivePath: web, browserTabs: { [web]: { url: 'https://docs.test/' } }, activity: 'source-control', mainContent: 'editor' }, storage);
  assert.equal(loadEditorSession(storage).mainContent, 'editor');
  saveEditorSession({ paths: [], browserPaths: [web], browserActivePath: web, browserTabs: { [web]: { url: 'https://docs.test/' } }, activity: 'browser', mainContent: 'browser' }, storage);
  assert.equal(loadEditorSession(storage).mainContent, 'browser');
  assert.equal(loadEditorSession(storage).activity, 'browser');
});

test('orphan browser paths never restore as filesystem editors', () => {
  const storage = memory();
  saveEditorSession({ paths: ['raticode-browser:missing', 'browser:missing', '/file'], activePath: 'raticode-browser:missing', browserTabs: {} }, storage);
  assert.deepEqual(loadEditorSession(storage).paths, ['/file']);
  assert.equal(loadEditorSession(storage).activePath, '/file');
  assert.deepEqual(loadEditorSession(storage).browserPaths, []);
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
