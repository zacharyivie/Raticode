/* global localStorage, window */
require('./studio-preload-mock.cjs');
const roots = ['/workspace/atlas', '/workspace/beacon'];
localStorage.setItem('raticode.settings.v1', JSON.stringify({
  version: 2,
  general: {
    autosave: false,
    defaultView: 'graph',
    initialActivity: 'workflows'
  },
  appearance: {
    theme: 'light'
  }
}));
localStorage.setItem('gofer.recentProjects', JSON.stringify(roots));
localStorage.setItem('raticode.studioSession.v1', JSON.stringify({
  projectRoot: roots[0],
  workflowId: 'atlas-a',
  view: 'graph'
}));
const files = new Map();
window.goferDesktop.workspace.trustProjectRoot = async () => true;
window.goferDesktop.workspace.gitStatus = async () => ({
  active: false,
  entries: []
});
window.goferDesktop.workspace.gitWorktrees = async root => ({
  root,
  worktrees: [{
    path: root,
    branch: 'main'
  }, ...(root === '/workspace/beacon' ? [{ path: '/worktrees/beacon-fix', branch: 'fix' }] : [])]
});
window.goferDesktop.workspace.listDirectory = async ({
  currentPath
}) => ({
  directory: currentPath,
  entries: [{
    name: 'config.txt',
    path: currentPath + '/config.txt',
    isDirectory: false
  }]
});
window.goferDesktop.workspace.getPathInfo = async path => ({
  path,
  basename: path.split('/').pop(),
  exists: true,
  isDirectory: !path.endsWith('.rattish') && !path.endsWith('.txt'),
  isFile: path.endsWith('.rattish') || path.endsWith('.txt')
});
window.goferDesktop.textFiles.read = async path => ({
  content: files.get(path) || 'original config\n'
});
window.goferDesktop.textFiles.write = async ({
  path,
  content
}) => {
  files.set(path, content);
  return {
    bytesWritten: content.length
  };
};
window.confirm = message => {
  window.__lastConfirm = message;
  return false;
};
