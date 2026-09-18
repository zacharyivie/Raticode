const desktopIpcHandlers = [
  ["gofer:api-session", "apiSession"],
  ["gofer:developer-info", "developerInfo"],
  ["gofer:developer-action", "developerAction"],
  ["gofer:renderer-log", "rendererLog"],
  ["gofer:rem-settings", "remSettings"],
  ["gofer:configure-rem", "configureRem"],
  ["gofer:archive-rem", "archiveRem"],
  ["gofer:restart-backend", "restartBackend"],
  ["gofer:open-logs", "openLogsFolder"],
  ["gofer:get-data-dir", "getGoferDataDir"],
  ["gofer:grant-path", "grantPath"],
  ["gofer:grant-user-path", "grantUserPath"],
  ["gofer:grant-dropped-path", "grantDroppedPath"],
  ["gofer:list-directory", "listDirectory"],
  ["gofer:search-project", "searchProject"],
  ["gofer:replace-project", "replaceProject"],
  ["gofer:open-path", "openPath"],
  ["gofer:reveal-path", "revealPath"],
  ["gofer:path-info", "pathInfo"],
  ["gofer:missing-recent-files", "missingRecentFiles"],
  ["gofer:missing-thread-roots", "missingThreadRoots"],
  ["gofer:resolve-project-file", "resolveProjectFile"],
  ["gofer:git-repo-action", "gitRepoAction"],
  ["gofer:git-status", "gitStatus"],
  ["gofer:git-branches", "gitBranches"],
  ["gofer:git-file-action", "gitFileAction"],
  ["gofer:git-switch-branch", "gitSwitchBranch"],
  ["gofer:git-file-baseline", "gitFileBaseline"],
  ["gofer:git-history", "gitHistory"],
  ["gofer:git-worktrees", "gitWorktrees"],
  ["gofer:git-worktree-add", "gitWorktreeAdd"],
  ["gofer:git-worktree-remove", "gitWorktreeRemove"],
  ["gofer:copy-path", "copyPath"],
  ["gofer:delete-path", "deletePath"],
  ["gofer:rename-path", "renamePath"],
  ["gofer:create-file", "createFile"],
  ["gofer:create-folder", "createFolder"],
  ["gofer:read-text-file", "readTextFile"],
  ["gofer:read-binary-preview", "readBinaryPreview"],
  ["gofer:write-text-file", "writeTextFile"],
  ["gofer:browser-create", "createBrowser"],
  ["gofer:browser-action", "browserAction"],
  ["gofer:browser-owner-zoom", "browserOwnerZoom"],
  ["gofer:terminal-create", "createTerminal"],
  ["gofer:terminal-write", "writeTerminal"],
  ["gofer:terminal-resize", "resizeTerminal"],
  ["gofer:terminal-close", "closeTerminal"],
  ["gofer:terminal-editor-complete", "completeTerminalEditor"],
  ["gofer:set-data-dir", "setDataDir"],
  ["gofer:select-path", "selectPath"],
];

const updateIpcHandlers = [
  ["gofer:check-for-updates", "checkForUpdates"],
  ["gofer:download-and-install-update", "downloadAndInstallUpdate"],
  ["gofer:install-downloaded-update", "installDownloadedUpdate"],
  ["gofer:open-update-release", "openUpdateRelease"],
  ["gofer:get-update-state", "getUpdateState"],
];

const ipcHandlerDefinitions = [...desktopIpcHandlers, ...updateIpcHandlers];

function registerIpcHandlers(ipcMain, handlers, options = {}) {
  const wrapHandler =
    typeof options.secureHandler === "function" ? options.secureHandler : (handler) => handler;
  for (const [channel, handlerName] of ipcHandlerDefinitions) {
    const handler = handlers[handlerName];
    if (typeof handler !== "function") {
      throw new Error(`Missing IPC handler: ${handlerName}`);
    }
    ipcMain.handle(channel, wrapHandler(handler, channel));
  }
}

module.exports = {
  desktopIpcHandlers,
  ipcHandlerDefinitions,
  registerIpcHandlers,
  updateIpcHandlers,
};
