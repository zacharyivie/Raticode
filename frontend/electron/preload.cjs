const { contextBridge, ipcRenderer, webFrame, webUtils } = require("electron");

const API_BASE_URL_ARG = "--gofer-api-base-url=";
const API_TOKEN_ARG = "--gofer-api-token=";
const BROWSER_PRELOAD_ARG = "--gofer-browser-preload=";
const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";
const LOCAL_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

// Capture pre-upgrade history before any page script runs. This capability stays
// in the isolated world and is never part of the public desktop bridge.
const migrationArgument = process.argv.find((value) => value.startsWith("--gofer-legacy-project-migration="));
if (migrationArgument) {
  const token = migrationArgument.slice("--gofer-legacy-project-migration=".length);
  let recentProjects = null;
  let unavailable = false;
  try { recentProjects = globalThis.window.localStorage.getItem("gofer.recentProjects"); }
  catch { unavailable = true; }
  ipcRenderer.sendSync("gofer:migrate-legacy-projects", { token, recentProjects, unavailable });
}

function readApiBaseUrl() {
  const arg = process.argv.find((value) => value.startsWith(API_BASE_URL_ARG));
  const value = arg ? arg.slice(API_BASE_URL_ARG.length) : DEFAULT_API_BASE_URL;

  return isSafeLocalHttpUrl(value) ? value : DEFAULT_API_BASE_URL;
}

function readApiToken() {
  const arg = process.argv.find((value) => value.startsWith(API_TOKEN_ARG));
  return arg ? arg.slice(API_TOKEN_ARG.length) : "";
}

function readBrowserPreloadPath() {
  const arg = process.argv.find((value) => value.startsWith(BROWSER_PRELOAD_ARG));
  const value = arg ? arg.slice(BROWSER_PRELOAD_ARG.length) : "";
  return value.startsWith("file:") ? value : "";
}

function isSafeLocalHttpUrl(value) {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      LOCAL_HOSTNAMES.has(url.hostname)
    );
  } catch {
    return false;
  }
}

contextBridge.exposeInMainWorld("goferApiBaseUrl", readApiBaseUrl());
contextBridge.exposeInMainWorld("goferApiToken", readApiToken());
const pathGrants = new Map();

function rememberPathGrant(payload) {
  if (!payload || typeof payload !== "object") return payload;
  if (typeof payload.path === "string" && typeof payload.grantId === "string") {
    pathGrants.set(payload.path, payload.grantId);
  }
  if (typeof payload.directory === "string" && typeof payload.grantId === "string") {
    pathGrants.set(payload.directory, payload.grantId);
  }
  if (Array.isArray(payload.entries)) {
    for (const entry of payload.entries) {
      rememberPathGrant(entry);
    }
  }
  if (Array.isArray(payload.worktrees)) {
    for (const worktree of payload.worktrees) {
      rememberPathGrant(worktree);
    }
  }
  return payload;
}

function stripGrantIds(value) {
  if (Array.isArray(value)) {
    return value.map((item) => stripGrantIds(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }

  const clean = {};
  const hasPathGrant = typeof value.path === "string" || typeof value.directory === "string";
  for (const [key, item] of Object.entries(value)) {
    if (hasPathGrant && key === "grantId") continue;
    clean[key] = stripGrantIds(item);
  }
  return clean;
}

function grantForPath(targetPath) {
  if (typeof targetPath !== "string") return "";
  const target = normalizeGrantPath(targetPath);
  let selectedGrantId = "";
  let selectedRootLength = -1;
  for (const [rootPath, grantId] of pathGrants.entries()) {
    const root = normalizeGrantPath(rootPath);
    if (!root) continue;
    const matchesRoot = target === root || target.startsWith(root.endsWith("/") ? root : `${root}/`);
    if (matchesRoot && root.length > selectedRootLength) {
      selectedGrantId = grantId;
      selectedRootLength = root.length;
    }
  }
  return selectedGrantId;
}

function normalizeGrantPath(targetPath) {
  let value = String(targetPath ?? "").trim();
  const windows = process.platform === "win32";
  if (windows) value = value.replace(/\\/g, "/").toLowerCase();
  const prefix = value.startsWith("//") && windows ? "//" : value.startsWith("/") ? "/" : "";
  const segments = [];
  for (const segment of value.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") { if (segments.length) segments.pop(); }
    else segments.push(segment);
  }
  return prefix + segments.join("/");
}

async function invokeDesktop(channel, payload = {}) {
  const result = await ipcRenderer.invoke(channel, payload);
  rememberPathGrant(result);
  return stripGrantIds(result);
}

contextBridge.exposeInMainWorld("goferDesktop", {
  apiSession: () => invokeDesktop("gofer:api-session"),
  developer: {
    info: () => invokeDesktop("gofer:developer-info"),
    action: (action) => invokeDesktop("gofer:developer-action", { action }),
    log: (message) => invokeDesktop("gofer:renderer-log", { message }),
  },
  rem: {
    settings: () => invokeDesktop("gofer:rem-settings"),
    configure: (key, value) => invokeDesktop("gofer:configure-rem", { key, value, grantId: typeof value === "string" ? grantForPath(value) : "" }),
    archive: (thread, messages, deleted = false) => invokeDesktop("gofer:archive-rem", { thread, messages, deleted }),
  },
  getDataDir: () => ipcRenderer.invoke("gofer:get-data-dir"),
  appearance: {
    setZoomFactor: (value) => {
      const factor = Number.isFinite(value) ? Math.min(1.5, Math.max(0.8, value)) : 1;
      webFrame.setZoomFactor(factor);
      void Promise.resolve(ipcRenderer.invoke("gofer:browser-owner-zoom", {})).catch(() => {});
      return factor;
    },
  },
  dataDirectory: {
    choose: async (options = {}) => {
      let selectedPath = null;
      try {
        selectedPath = await selectPath({
          currentPath:
            typeof options.currentPath === "string" ? options.currentPath : "",
          directoryOnly: true,
        });
      } catch {
        selectedPath = null;
      }
      if (!selectedPath) return null;
      return invokeDesktop("gofer:set-data-dir", {
        dataDir: selectedPath,
        grantId: grantForPath(selectedPath),
      });
    },
    get: () => ipcRenderer.invoke("gofer:get-data-dir"),
  },
  workspace: {
    replaceProject: (projectRoot, options = {}) => invokeDesktop("gofer:replace-project", { ...options, projectRoot, grantId: grantForPath(projectRoot) }),
    searchProject: (projectRoot, options = {}) => invokeDesktop("gofer:search-project", { ...options, projectRoot, grantId: grantForPath(projectRoot) }),
    listDirectory: (options = {}) =>
      listDirectory(options),
    openPath: (targetPath) =>
      openPath(targetPath),
    revealPath: (targetPath) =>
      revealPath(targetPath),
    missingThreadRoots: (paths) => invokeDesktop("gofer:missing-thread-roots", { paths }),
    missingRecentFiles: (paths) => invokeDesktop("gofer:missing-recent-files", { paths }),
    getPathInfo: (targetPath) =>
      getPathInfo(targetPath),
    gitRepoAction: (projectRoot, action, value) => invokeDesktop("gofer:git-repo-action", { projectRoot, action, value, grantId: grantForPath(projectRoot) }),
    gitFileAction: (projectRoot, relativePath, action) => invokeDesktop("gofer:git-file-action", { projectRoot, relativePath, action, grantId: grantForPath(projectRoot) }),
    gitSwitchBranch: (projectRoot, branch) => invokeDesktop("gofer:git-switch-branch", { projectRoot, branch, grantId: grantForPath(projectRoot) }),
    gitBranches: (projectRoot, force = false) => invokeDesktop("gofer:git-branches", { projectRoot, force, grantId: grantForPath(projectRoot) }),
    gitStatus: (projectRoot) =>
      gitStatus(projectRoot),
    gitFileBaseline: (targetPath, group) =>
      gitFileBaseline(targetPath, group),
    gitHistory: (projectRoot) => gitHistory(projectRoot),
    gitWorktrees: (projectRoot) => gitWorktrees(projectRoot),
    addWorktree: (options = {}) => addWorktree(options),
    removeWorktree: (options = {}) => removeWorktree(options),
    pathGrantForApi: (targetPath) =>
      grantForPath(targetPath),
    grantUserPath: (targetPath) => invokeDesktop("gofer:grant-user-path", { targetPath }),
    trustProjectRoot: (targetPath) =>
      trustProjectRoot(targetPath),
    copyPath: (options = {}) =>
      copyPath(options),
    deletePath: (targetPath) =>
      deletePath(targetPath),
    renamePath: (options = {}) =>
      renamePath(options),
    resolveProjectFile: (selectedPath) =>
      resolveProjectFile(selectedPath),
    createFile: (options = {}) =>
      createFile(options),
    createFolder: (options = {}) =>
      createFolder(options),
    selectPath: (options = {}) =>
      selectPath(options),
  },
  textFiles: {
    readPreview: (targetPath) =>
      readBinaryPreview(targetPath),
    read: (targetPath) =>
      readTextFile(targetPath),
    write: (options = {}) =>
      writeTextFile(options),
  },
  getDroppedFilePath: (file) => webUtils.getPathForFile(file) || "",
  grantDroppedPath: async (file) => {
    const targetPath = webUtils.getPathForFile(file) || "";
    if (!targetPath) return null;
    const payload = await invokeDesktop("gofer:grant-dropped-path", { targetPath });
    return payload?.path || targetPath;
  },
});

contextBridge.exposeInMainWorld("goferBrowser", {
  platform: process.platform,
  preloadPath: readBrowserPreloadPath(),
  create: (options = {}) => createBrowser(options),
  adopt: (id, webContentsId) => browserAction(id, "adopt", {
    webContentsId: Number.isFinite(webContentsId) ? webContentsId : -1,
  }),
  back: (id) => browserAction(id, "back"),
  close: (id) => browserAction(id, "close"),
  focus: (id) => browserAction(id, "focus"),
  forward: (id) => browserAction(id, "forward"),
  navigate: (id, url) => browserAction(id, "navigate", { url }),
  openExternal: (id) => browserAction(id, "open-external"),
  reload: (id) => browserAction(id, "reload"),
  setPreferences: (id, preferences = {}) => browserAction(id, "set-preferences", {
    ...(preferences.applicationKeybindings
      ? { applicationKeybindings: browserKeybindings(preferences.applicationKeybindings) }
      : {}),
    openBrowserBinding: typeof preferences.openBrowserBinding === "string"
      ? preferences.openBrowserBinding.slice(0, 120)
      : "",
  }),
  stop: (id) => browserAction(id, "stop"),
  onCommand: (callback) => subscribeToBrowserEvent("gofer:browser-command", callback),
  onOpenFile: (callback) => subscribeToBrowserEvent("gofer:browser-open-file", callback),
  onOpenTab: (callback) => subscribeToBrowserEvent("gofer:browser-open-tab", callback),
  onState: (callback) => subscribeToBrowserEvent("gofer:browser-state", callback),
});

contextBridge.exposeInMainWorld("goferTerminal", {
  create: (options = {}) => createTerminal(options),
  write: (id, data) =>
    ipcRenderer.invoke("gofer:terminal-write", {
      data: typeof data === "string" ? data : "",
      id: typeof id === "string" ? id : "",
    }),
  resize: (id, cols, rows) =>
    ipcRenderer.invoke("gofer:terminal-resize", {
      cols: Number.isFinite(cols) ? cols : 80,
      id: typeof id === "string" ? id : "",
      rows: Number.isFinite(rows) ? rows : 24,
    }),
  close: (id) =>
    ipcRenderer.invoke("gofer:terminal-close", {
      id: typeof id === "string" ? id : "",
    }),
  completeEditor: (requestId) =>
    ipcRenderer.invoke("gofer:terminal-editor-complete", {
      requestId: typeof requestId === "string" ? requestId : "",
    }),
  onData: (callback) => subscribeToTerminalEvent("gofer:terminal-data", callback),
  onOpenEditor: (callback) => subscribeToTerminalEvent("gofer:terminal-open-editor", callback),
  onExit: (callback) => subscribeToTerminalEvent("gofer:terminal-exit", callback),
});

async function createTerminal(options = {}) {
  const requestedCwd = typeof options.cwd === "string" ? options.cwd : "";
  if (requestedCwd && !grantForPath(requestedCwd)) {
    await trustProjectRoot(requestedCwd);
  }
  const grantId = grantForPath(requestedCwd);
  if (requestedCwd && !grantId) {
    throw new Error("The workflow project folder could not be trusted.");
  }
  return ipcRenderer.invoke("gofer:terminal-create", {
    cols: Number.isFinite(options.cols) ? options.cols : 80,
    cwd: requestedCwd,
    grantId,
    rows: Number.isFinite(options.rows) ? options.rows : 24,
  });
}

async function createBrowser(options = {}) {
  const targetPath = typeof options.path === "string" ? options.path : "";
  if (targetPath && !grantForPath(targetPath)) {
    await trustProjectRoot(targetPath);
  }
  return ipcRenderer.invoke("gofer:browser-create", {
    ...(options.applicationKeybindings
      ? { applicationKeybindings: browserKeybindings(options.applicationKeybindings) }
      : {}),
    clientId: typeof options.clientId === "string" ? options.clientId : "",
    grantId: grantForPath(targetPath),
    ...(typeof options.openBrowserBinding === "string" && options.openBrowserBinding
      ? { openBrowserBinding: options.openBrowserBinding.slice(0, 120) }
      : {}),
    path: targetPath,
    url: typeof options.url === "string" ? options.url : "",
  });
}

function browserKeybindings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([commandId, binding]) => (
    typeof commandId === "string" && commandId.length <= 120 && typeof binding === "string"
      ? [[commandId, binding.slice(0, 240)]]
      : []
  )));
}

function browserAction(id, action, extra = {}) {
  return ipcRenderer.invoke("gofer:browser-action", {
    ...extra,
    action,
    id: typeof id === "string" ? id : "",
  });
}

function subscribeToBrowserEvent(channel, callback) {
  if (typeof callback !== "function") return () => {};
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

async function trustProjectRoot(targetPath) {
  if (typeof targetPath !== "string" || !targetPath.trim()) return null;
  const previousGrantId = grantForPath(targetPath);
  try {
    const payload = await invokeDesktop("gofer:grant-path", { targetPath });
    if (payload?.missing) {
      const error = new Error(`Path does not exist: ${targetPath}`);
      error.code = "ENOENT";
      throw error;
    }
    return payload?.path || targetPath;
  } catch (error) {
    for (const [root, grantId] of pathGrants.entries()) {
      if (grantId === previousGrantId) pathGrants.delete(root);
    }
    throw error;
  }
}

function subscribeToTerminalEvent(channel, callback) {
  if (typeof callback !== "function") return () => {};
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

function listDirectory(options = {}) {
  return (
    invokeDesktop("gofer:list-directory", {
      currentPath: typeof options.currentPath === "string" ? options.currentPath : "",
      grantId: grantForPath(options.currentPath),
      create: options.create !== false,
    })
  );
}

function openPath(targetPath) {
  return invokeDesktop("gofer:open-path", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function revealPath(targetPath) {
  return invokeDesktop("gofer:reveal-path", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function getPathInfo(targetPath) {
  return invokeDesktop("gofer:path-info", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function gitStatus(projectRoot) {
  return invokeDesktop("gofer:git-status", {
    grantId: grantForPath(projectRoot),
    projectRoot: typeof projectRoot === "string" ? projectRoot : "",
  });
}

function gitFileBaseline(targetPath, group) {
  return invokeDesktop("gofer:git-file-baseline", {
    group,
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function gitHistory(projectRoot) {
  return invokeDesktop("gofer:git-history", { grantId: grantForPath(projectRoot), projectRoot });
}

function gitWorktrees(projectRoot) {
  return invokeDesktop("gofer:git-worktrees", { grantId: grantForPath(projectRoot), projectRoot });
}

function addWorktree(options = {}) {
  return invokeDesktop("gofer:git-worktree-add", {
    branch: typeof options.branch === "string" ? options.branch : "",
    createBranch: options.createBranch === true,
    startPoint: typeof options.startPoint === "string" ? options.startPoint : undefined,
    grantId: grantForPath(options.projectRoot),
    projectRoot: typeof options.projectRoot === "string" ? options.projectRoot : "",
    targetGrantId: grantForPath(options.targetPath),
    targetPath: typeof options.targetPath === "string" ? options.targetPath : "",
  });
}

function removeWorktree(options = {}) {
  return invokeDesktop("gofer:git-worktree-remove", {
    force: options.force === true,
    grantId: grantForPath(options.projectRoot),
    projectRoot: typeof options.projectRoot === "string" ? options.projectRoot : "",
    targetGrantId: grantForPath(options.targetPath),
    targetPath: typeof options.targetPath === "string" ? options.targetPath : "",
  });
}

function copyPath(options = {}) {
  return invokeDesktop("gofer:copy-path", {
    destinationGrantId: grantForPath(options.destinationPath),
    sourcePath:
      typeof options.sourcePath === "string" ? options.sourcePath : "",
    sourceGrantId: grantForPath(options.sourcePath),
    destinationPath:
      typeof options.destinationPath === "string" ? options.destinationPath : "",
  });
}

function deletePath(targetPath) {
  return invokeDesktop("gofer:delete-path", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function renamePath(options = {}) {
  return invokeDesktop("gofer:rename-path", {
    grantId: grantForPath(options.sourcePath),
    sourcePath:
      typeof options.sourcePath === "string" ? options.sourcePath : "",
    name: typeof options.name === "string" ? options.name : "",
  });
}

function resolveProjectFile(selectedPath) {
  return invokeDesktop("gofer:resolve-project-file", {
    grantId: grantForPath(selectedPath),
    selectedPath: typeof selectedPath === "string" ? selectedPath : "",
  });
}

function createFile(options = {}) {
  return invokeDesktop("gofer:create-file", {
    directory: typeof options.directory === "string" ? options.directory : "",
    grantId: grantForPath(options.directory),
    name: typeof options.name === "string" ? options.name : "",
  });
}

function createFolder(options = {}) {
  return invokeDesktop("gofer:create-folder", {
    directory: typeof options.directory === "string" ? options.directory : "",
    grantId: grantForPath(options.directory),
    name: typeof options.name === "string" ? options.name : "",
  });
}

function readTextFile(targetPath) {
  return invokeDesktop("gofer:read-text-file", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function readBinaryPreview(targetPath) {
  return invokeDesktop("gofer:read-binary-preview", {
    grantId: grantForPath(targetPath),
    targetPath: typeof targetPath === "string" ? targetPath : "",
  });
}

function writeTextFile(options = {}) {
  return invokeDesktop("gofer:write-text-file", {
    grantId: grantForPath(options.targetPath),
    targetPath:
      typeof options.targetPath === "string" ? options.targetPath : "",
    content: typeof options.content === "string" ? options.content : "",
  });
}

function selectPath(options = {}) {
  const payload = {
    currentPath:
      typeof options.currentPath === "string" ? options.currentPath : "",
    grantId: grantForPath(options.currentPath),
  };
  if (options.directoryOnly === true) {
    payload.directoryOnly = true;
  }
  if (options.fileOnly === true) {
    payload.fileOnly = true;
  }
  return invokeDesktop("gofer:select-path", payload).then((payload) =>
    payload && typeof payload.path === "string" ? payload.path : null
  );
}

contextBridge.exposeInMainWorld("goferUpdates", {
  check: () => ipcRenderer.invoke("gofer:check-for-updates"),
  downloadAndInstall: () => ipcRenderer.invoke("gofer:download-and-install-update"),
  installDownloaded: () => ipcRenderer.invoke("gofer:install-downloaded-update"),
  openRelease: () => ipcRenderer.invoke("gofer:open-update-release"),
  getState: () => ipcRenderer.invoke("gofer:get-update-state"),
  onState: (callback) => {
    if (typeof callback !== "function") return () => {};
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("gofer:update-state", listener);
    return () => ipcRenderer.removeListener("gofer:update-state", listener);
  },
});

globalThis.window?.addEventListener("error", (event) => { void ipcRenderer.invoke("gofer:renderer-log", { message: event.error?.stack || event.message || "Renderer error" }).catch(() => {}); });
globalThis.window?.addEventListener("unhandledrejection", (event) => { void ipcRenderer.invoke("gofer:renderer-log", { message: event.reason?.stack || String(event.reason) }).catch(() => {}); });
