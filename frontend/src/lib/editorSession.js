export const EDITOR_SESSION_KEY = 'raticode.editorSession.v2';
const activities = ['workflows', 'files', 'search', 'source-control', 'organizations', 'second-brain', 'browser'];
// Main content kept across restarts; swarms and organizations need an item chosen again.
const mainContents = ['editor', 'rem', 'second-brain', 'browser'];
export const isBrowserPath = path => typeof path === 'string' && (path.startsWith('raticode-browser:') || path.startsWith('browser:'));
const cleanPaths = list => [...new Set((Array.isArray(list) ? list : []).filter(path => typeof path === 'string' && path.length && path.length < 8192))].slice(0, 100);

// Web tabs live in the Browser plugin, file tabs in the File explorer. Sessions
// saved before the Browser plugin kept web tabs among the file tabs in `paths`;
// they move to `browserPaths` with their order and selection kept.
export function loadEditorSession(storage = globalThis.window?.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(EDITOR_SESSION_KEY) || 'null');
    if (value?.version !== 2 || !Array.isArray(value.paths)) return null;
    const paths = cleanPaths(value.paths);
    const savedBrowserPaths = Array.isArray(value.browserPaths) ? cleanPaths(value.browserPaths).filter(isBrowserPath) : null;
    const workflowTabs = Object.fromEntries(paths.filter(path => path.startsWith('workflow-graph:') && typeof value.workflowTabs?.[path]?.workflowId === 'string').map(path => [path, value.workflowTabs[path]]));
    const browserCandidates = cleanPaths([...(savedBrowserPaths || []), ...paths.filter(isBrowserPath)]);
    const browserTabs = Object.fromEntries(browserCandidates.filter(path => value.browserTabs?.[path] && typeof value.browserTabs[path] === 'object').map(path => [path, value.browserTabs[path]]));
    const browserPaths = browserCandidates.filter(path => browserTabs[path]);
    const filePaths = paths.filter(path => path.startsWith('workflow-graph:') ? workflowTabs[path] : !isBrowserPath(path));
    const browserActivePath = browserPaths.includes(value.browserActivePath) ? value.browserActivePath
      : browserPaths.includes(value.activePath) ? value.activePath : browserPaths.at(-1) || '';
    // Before the Browser plugin, a selected web tab meant the browser was in front.
    const legacyBrowserInFront = !savedBrowserPaths && browserPaths.includes(value.activePath) && (value.mainContent === 'editor' || !value.mainContent);
    return {
      pinnedRun: typeof value.pinnedRun?.workflowId === "string" && typeof value.pinnedRun?.runId === "string" ? value.pinnedRun : null,
      paths: filePaths,
      activePath: filePaths.includes(value.activePath) ? value.activePath : filePaths[0] || '',
      workflowTabs,
      browserTabs,
      browserPaths,
      browserActivePath,
      activity: legacyBrowserInFront ? 'browser' : activities.includes(value.activity) ? value.activity : 'workflows',
      mainContent: legacyBrowserInFront ? 'browser' : mainContents.includes(value.mainContent) ? value.mainContent : typeof value.mainContent === 'string' ? 'editor' : '',
    };
  } catch { return null; }
}

// Web tabs are also listed in `paths`, after the file tabs, so a Raticode
// version from before the Browser plugin still restores them.
export function saveEditorSession(session, storage = globalThis.window?.localStorage) {
  const browserPaths = Array.isArray(session?.browserPaths) ? session.browserPaths : [];
  const paths = [...new Set([...(Array.isArray(session?.paths) ? session.paths : []), ...browserPaths])];
  try { storage?.setItem(EDITOR_SESSION_KEY, JSON.stringify({ ...session, paths, browserPaths, version: 2 })); } catch { /* Editing remains available when local storage is full. */ }
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
