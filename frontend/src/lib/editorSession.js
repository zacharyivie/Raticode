export const EDITOR_SESSION_KEY = 'raticode.editorSession.v2';
const activities = ['workflows', 'files', 'search', 'source-control'];
const isBrowserPath = path => path.startsWith('raticode-browser:') || path.startsWith('browser:');
export function loadEditorSession(storage = globalThis.window?.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(EDITOR_SESSION_KEY) || 'null');
    if (value?.version !== 2 || !Array.isArray(value.paths)) return null;
    const paths = [...new Set(value.paths.filter(path => typeof path === 'string' && path.length && path.length < 8192))].slice(0, 100);
    const workflowTabs = Object.fromEntries(paths.filter(path => path.startsWith('workflow-graph:') && typeof value.workflowTabs?.[path]?.workflowId === 'string').map(path => [path, value.workflowTabs[path]]));
    const browserTabs = Object.fromEntries(paths.filter(path => isBrowserPath(path) && value.browserTabs?.[path]).map(path => [path, value.browserTabs[path]]));
    const retainedPaths = paths.filter(path => path.startsWith('workflow-graph:') ? workflowTabs[path] : isBrowserPath(path) ? browserTabs[path] : true);
    return { pinnedRun: typeof value.pinnedRun?.workflowId === "string" && typeof value.pinnedRun?.runId === "string" ? value.pinnedRun : null, paths: retainedPaths, activePath: retainedPaths.includes(value.activePath) ? value.activePath : retainedPaths[0] || '', workflowTabs, browserTabs, activity: activities.includes(value.activity) ? value.activity : 'workflows' };
  } catch { return null; }
}
export function saveEditorSession(session, storage = globalThis.window?.localStorage) {
  try { storage?.setItem(EDITOR_SESSION_KEY, JSON.stringify({ ...session, version: 2 })); } catch { /* Editing remains available when local storage is full. */ }
}
export function loadWorkflowDraft(path, storage = globalThis.window?.localStorage) {
  try {
    const legacyPath = path.replace(/\.rattish$/i, '.rad');
    const value = JSON.parse(storage?.getItem(`raticode.workflowDraft:${path}`)
      || (legacyPath !== path ? storage?.getItem(`raticode.workflowDraft:${legacyPath}`) : null) || 'null');
    return value && typeof value.source === 'string' ? value : null;
  } catch { return null; }
}
export function saveWorkflowDraft(path, document, storage = globalThis.window?.localStorage) {
  if (!document) return true;
  if (!path) return false;
  try {
    const key = `raticode.workflowDraft:${path}`;
    if (document.dirty) {
      if (typeof storage?.setItem !== 'function') return false;
      storage.setItem(key, JSON.stringify({ source: document.source, savedRevision: document.savedRevision, savedSource: document.savedSource }));
    } else {
      if (typeof storage?.removeItem !== 'function') return false;
      storage.removeItem(key);
      const legacyPath = path.replace(/\.rattish$/i, '.rad');
      if (legacyPath !== path) storage.removeItem(`raticode.workflowDraft:${legacyPath}`);
    }
    return true;
  } catch { return false; }
}
