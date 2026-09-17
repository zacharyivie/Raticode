import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const {
  app,
  BrowserWindow,
  Menu,
  dialog,
  ipcMain,
  shell,
  session,
  webContents,
} = require("electron");
const { autoUpdater } = require("electron-updater");
const { raticodeReleaseSigning } = require("../package.json");
const pty = require("node-pty");
const { previousBrands: LEGACY_BRANDS } = require("./brand-compat.json");
// Keep existing packaged profiles, including trusted folders and browser state.
// An explicit --user-data-dir always takes precedence.
const currentProfile = app.getPath("userData");
const previousProfile = LEGACY_BRANDS.map((item) => path.join(app.getPath("appData"), item.productName))
  .find((profile) => fs.existsSync(profile));
if (!app.commandLine.hasSwitch("user-data-dir") && !fs.existsSync(currentProfile)
    && previousProfile) {
  app.setPath("userData", previousProfile);
}
const {
  gitRepositoryAction,
  changeGitFile,
  switchGitBranch,
  addGitWorktree,
  readGitFileBaseline,
  readGitHistory,
  readGitStatus,
  readGitWorktrees,
  removeGitWorktree,
} = require("./git-status.cjs");
const {
  RATICODE_HOME_URL,
  browserCommandRequiresOwnerFocus,
  browserContentZoomFactor,
  browserLoadUrl,
  browserSessionShortcutAction,
  normalizeBrowserUrl,
} = require("./browser-utils.cjs");
const { searchProject, replaceProject } = require("./project-search.cjs");
const { createAppLog } = require("./app-log.cjs");
const { createArchiveQueue } = require("./archive-queue.cjs");
const conversationArchives = createArchiveQueue();
let archivesDrained = false;
let logsDrained = false;
let logsClosing = false;
const REPORT_THEMES = ["auto", "light", "dark", "sepia", "vaporwave", "steam", "carbon", "botanical", "blueprint", "arcade", "sakura", "deep-sea", "solarpunk", "noir", "candy-lab", "cosmic"];
let applicationLog;
const { registerIpcHandlers } = require("./ipc-handlers.cjs");
const { inspectPath, missingRecentFiles, missingThreadRoots } = require("./path-info.cjs");
const safeFiles = require("./safe-files.cjs");
const { migrateSource } = require("./rattish-migration.cjs");
const { installPermissionPolicy, isStudioDocument, studioCsp } = require("./studio-policy.cjs");
const { createIpcSecurity, isSafeExternalUrl } = require("./security.cjs");
const { createTrustedProjectStore } = require("./trusted-projects.cjs");
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const distIndexPath = path.join(__dirname, "..", "dist", "index.html");
const backendErrorHtmlPath = path.join(__dirname, "backend-error.html");
const browserPreloadPath = path.join(__dirname, "browser-preload.cjs");

if (process.platform === "linux" && !process.env.GTK_USE_PORTAL) {
  process.env.GTK_USE_PORTAL = "0";
}

if (
  process.env.GOFER_ELECTRON_SMOKE_TEST === "1" ||
  process.env.GOFER_DISABLE_HARDWARE_ACCELERATION === "1"
) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
}
if (process.env.GOFER_ELECTRON_SMOKE_TEST === "1") {
  app.commandLine.appendSwitch("no-sandbox");
}

const VITE_DEV_SERVER_URL =
  process.env.GOFER_VITE_DEV_SERVER_URL ||
  process.env.VITE_DEV_SERVER_URL ||
  "http://127.0.0.1:5173";
const BACKEND_READY_PREFIX = "GOFER_UI_READY ";
const BACKEND_START_TIMEOUT_MS = 15000;
const ELECTRON_READY_MESSAGE = "GOFER_ELECTRON_READY";
const BACKEND_EXECUTABLE_NAME = process.platform === "win32" ? "gof.exe" : "gof";
const LATEST_RELEASE_URL =
  "https://api.github.com/repos/zacharyivie/gofer-flow/releases/latest";
const isProduction =
  app.isPackaged || process.env.GOFER_ELECTRON_MODE === "production";
const isSmokeTest = process.env.GOFER_ELECTRON_SMOKE_TEST === "1";
let backendProcess;
let backendLogStream;
const desktopGrantSecret = crypto.randomBytes(32).toString("hex");
const expectedBackendStops = new WeakSet();
let isQuitting = false;
let activeApiBaseUrl;
let activeUiApiToken;
let selectedDataDir;
let mainWindow;
let backendErrorWindow;
let ipcSecurity;
let backendErrorIpcSecurity;
let legacyProjectMigrationToken = "";
const terminalSessions = new Map();
const browserSessions = new Map();
const terminalEditorRequests = new Map();
const browserOwnersWithCleanup = new WeakSet();
let terminalEditorServer;
let terminalEditorEndpoint = "";
let updateState = {
  available: false,
  checking: false,
  downloading: false,
  downloaded: false,
  error: "",
  info: null,
  progress: null,
};
let installUpdateAfterDownload = false;

const singleInstanceLock = isSmokeTest || app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
  process.exit(0);
}

function createWindow(apiBaseUrl, apiToken = "") {
  legacyProjectMigrationToken = "";
  try {
    if (!trustedProjectStore().read().legacyRecentProjectsMigrated) legacyProjectMigrationToken = crypto.randomUUID();
  } catch { writeBackendLog("TRUSTED_PROJECT_REGISTRY_UNAVAILABLE\n"); }
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 980,
    minHeight: 640,
    title: "Raticode",
    icon: path.join(__dirname, app.isPackaged ? "../dist/icon.png" : "../public/icon.png"),
    backgroundColor: "#1f1f1f",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: !isSmokeTest,
      webviewTag: true,
      additionalArguments: [
        `--gofer-api-base-url=${apiBaseUrl}`,
        `--gofer-api-token=${apiToken}`,
        `--gofer-browser-preload=${pathToFileURL(browserPreloadPath)}`,
        ...(legacyProjectMigrationToken ? [`--gofer-legacy-project-migration=${legacyProjectMigrationToken}`] : []),
      ],
    },
  });
  const terminalOwnerId = mainWindow.webContents.id;
  const studioOptions = { indexPath: distIndexPath, devServerUrl: VITE_DEV_SERVER_URL, isProduction };
  installPermissionPolicy(session.fromPartition("persist:raticode-browser"), mainWindow.webContents.session,
    () => mainWindow?.webContents, studioOptions);
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isStudioDocument(url, studioOptions)) event.preventDefault();
  });
  mainWindow.webContents.on("will-redirect", (event, url) => {
    if (!isStudioDocument(url, studioOptions)) event.preventDefault();
  });
  // Apply CSP to the studio document only; isolated local reports keep their CSS.
  mainWindow.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...details.responseHeaders };
    if (details.webContentsId === mainWindow?.webContents.id && details.resourceType === "mainFrame"
      && isStudioDocument(details.url, studioOptions)) {
      responseHeaders["Content-Security-Policy"] = [studioCsp({ ...studioOptions, apiBaseUrl })];
    }
    callback({ responseHeaders });
  });

  mainWindow.webContents.on("will-attach-webview", (event, webPreferences, params) => {
    if (
      params.partition !== "persist:raticode-browser"
      || !isPendingBrowserSessionSrc(terminalOwnerId, params.src)
    ) {
      event.preventDefault();
      return;
    }
    delete webPreferences.preloadURL;
    webPreferences.preload = browserPreloadPath;
    webPreferences.backgroundThrottling = true;
    webPreferences.contextIsolation = true;
    webPreferences.nodeIntegration = false;
    webPreferences.nodeIntegrationInSubFrames = false;
    webPreferences.sandbox = true;
    webPreferences.webSecurity = true;
    webPreferences.allowRunningInsecureContent = false;
  });
  mainWindow.webContents.on("did-attach-webview", (_event, guestContents) => {
    guestContents.setWindowOpenHandler(() => ({ action: "deny" }));
  });

  mainWindow.webContents.once("did-finish-load", () => {
    if (!isSmokeTest) return;

    console.log(ELECTRON_READY_MESSAGE);
    setTimeout(() => app.quit(), 250);
  });

  mainWindow.webContents.once(
    "did-fail-load",
    (_event, errorCode, errorDescription, validatedUrl) => {
      if (!isSmokeTest) return;

      console.error(
        `GOFER_ELECTRON_LOAD_FAILED ${JSON.stringify({
          errorCode,
          errorDescription,
          url: validatedUrl,
        })}`,
      );
      app.exit(1);
    },
  );

  if (isProduction) {
    mainWindow.loadFile(distIndexPath);
  } else {
    mainWindow.loadURL(VITE_DEV_SERVER_URL);
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url) && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send("gofer:browser-open-tab", { url });
    }
    return { action: "deny" };
  });

  if (process.env.GOFER_ELECTRON_DEVTOOLS === "1") {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  mainWindow.on("closed", () => {
    closeBrowsersForOwner(terminalOwnerId);
    closeTerminalsForOwner(terminalOwnerId);
    mainWindow = undefined;
  });
}

function startBackend() {
  const manualApiBaseUrl = process.env.GOFER_API_BASE_URL || process.env.VITE_API_BASE_URL;
  if (manualApiBaseUrl) {
    return Promise.resolve({
      apiBaseUrl: manualApiBaseUrl,
      apiToken: process.env.GOFER_UI_API_TOKEN || "",
    });
  }

  return new Promise((resolve, reject) => {
    const backendCommand = getBackendCommand();
    const args = [
      ...backendCommand.args,
      "ui",
      "serve",
      "--port",
      "0",
      "--data-dir",
      getGoferDataDir(),
    ];

    const child = spawn(backendCommand.command, args, {
      cwd: repoRoot,
      env: {
        ...process.env,
        GOFER_DESKTOP_GRANT_SECRET: desktopGrantSecret,
        GOFER_UI_EMIT_READY_TOKEN: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    backendProcess = child;
    backendLogStream = createBackendLogStream();

    let settled = false;
    let stdoutBuffer = "";
    let stderrBuffer = "";
    const timeoutId = setTimeout(() => {
      fail(new Error("Timed out waiting for the Raticode backend to start."));
    }, BACKEND_START_TIMEOUT_MS);

    function succeed(apiBaseUrl, apiToken = "") {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      writeBackendLog(`READY ${apiBaseUrl}\n`);
      resolve({ apiBaseUrl, apiToken });
    }

    function fail(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      stopBackend();
      reject(error);
    }

    function handleOutput(chunk) {
      stdoutBuffer += chunk.toString();
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith(BACKEND_READY_PREFIX)) {
          writeBackendLog(`${line}\n`);
          console.log(`[gofer-backend] ${line}`);
          continue;
        }

        try {
          const payload = JSON.parse(line.slice(BACKEND_READY_PREFIX.length));
          const host = payload.host || "127.0.0.1";
          const port = Number(payload.port);
          const apiToken = typeof payload.apiToken === "string" ? payload.apiToken : "";
          if (!port) {
            throw new Error("Ready payload did not include a valid port.");
          }
          console.log(`[gofer-backend] GOFER_UI_READY ${JSON.stringify({ host, port })}`);
          succeed(`http://${host}:${port}`, apiToken);
        } catch (error) {
          fail(error);
        }
      }
    }

    child.stdout.on("data", handleOutput);
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderrBuffer = (stderrBuffer + text).slice(-65536);
      writeBackendLog(text);
      process.stderr.write(`[gofer-backend] ${text}`);
    });
    child.on("error", (error) => {
      fail(error);
    });
    child.on("exit", (code, signal) => {
      writeBackendLog(`BACKEND_EXIT ${JSON.stringify({ code, signal, expected: isQuitting || expectedBackendStops.has(child) })}\n`);
      if (backendProcess === child) {
        backendProcess = undefined;
        closeBackendLogStream();
      }
      if (isQuitting || expectedBackendStops.has(child)) {
        return;
      }
      const detail = stderrBuffer.trim() || `Backend exited with code ${code ?? signal}.`;
      if (!settled) {
        fail(new Error(detail));
        return;
      }

      showBackendCrash(new Error(detail));
    });
  });
}

function getBackendCommand() {
  if (!isProduction) {
    return {
      command: "uv",
      args: ["run", "gof"],
    };
  }

  return {
    command: process.env.GOFER_BACKEND_PATH || defaultPackagedBackendPath(),
    args: [],
  };
}

function defaultPackagedBackendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend", BACKEND_EXECUTABLE_NAME);
  }

  return path.join(repoRoot, "dist", BACKEND_EXECUTABLE_NAME);
}

function getGoferDataDir() {
  if (selectedDataDir) {
    return selectedDataDir;
  }

  if (process.env.GOFER_DATA_DIR) {
    return process.env.GOFER_DATA_DIR;
  }

  const persistedDataDir = readPersistedDataDir();
  if (persistedDataDir) {
    selectedDataDir = persistedDataDir;
    process.env.GOFER_DATA_DIR = persistedDataDir;
    return persistedDataDir;
  }

  if (process.platform === "win32") {
    return path.join(app.getPath("appData"), "gofer");
  }

  if (process.platform === "darwin") {
    return path.join(app.getPath("appData"), "gofer");
  }

  return path.join(
    process.env.XDG_DATA_HOME || path.join(app.getPath("home"), ".local", "share"),
    "gofer",
  );
}

function readPersistedDataDir() {
  try {
    const payload = JSON.parse(fs.readFileSync(dataDirConfigPath(), "utf8"));
    return typeof payload.dataDir === "string" && payload.dataDir.trim()
      ? payload.dataDir
      : "";
  } catch {
    return "";
  }
}

function writePersistedDataDir(dataDir) {
  const configPath = dataDirConfigPath();
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  fs.writeFileSync(
    configPath,
    `${JSON.stringify({ dataDir }, null, 2)}\n`,
    "utf8",
  );
}

function dataDirConfigPath() {
  return path.join(app.getPath("userData"), "settings.json");
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) return;

  const child = backendProcess;
  backendProcess = undefined;
  expectedBackendStops.add(child);
  closeBackendLogStream();
  child.kill("SIGTERM");
  setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
  }, 3000).unref();
}

function createBackendLogStream() {
  return null;
}

function writeBackendLog(message) {
  applicationLog?.write("info", "backend", message);
  if (!backendLogStream) return;
  backendLogStream.write(message);
}

function closeBackendLogStream() {
  if (!backendLogStream) return;
  backendLogStream.end();
  backendLogStream = undefined;
}

function showBackendCrash(error) {
  if (isSmokeTest) {
    console.error(
      `GOFER_ELECTRON_BACKEND_FAILED ${JSON.stringify({
        message: error instanceof Error ? error.message : String(error),
      })}`,
    );
    app.exit(1);
    return;
  }

  createBackendErrorWindow(error, { title: "Raticode backend stopped" });
}

function createBackendErrorWindow(error, { title = "Raticode backend did not start" } = {}) {
  const message = error instanceof Error ? error.message : String(error);
  if (backendErrorWindow && !backendErrorWindow.isDestroyed()) {
    backendErrorWindow.focus();
    return;
  }

  const errorWindow = new BrowserWindow({
    width: 720,
    height: 420,
    title: "Raticode Backend Error",
    backgroundColor: "#1f1f1f",
    webPreferences: {
      preload: path.join(__dirname, "error-preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: !isSmokeTest,
    },
  });
  backendErrorWindow = errorWindow;

  errorWindow.loadFile(backendErrorHtmlPath, {
    query: {
      message,
      title,
    },
  });
  errorWindow.on("closed", () => {
    backendErrorWindow = undefined;
    backendErrorIpcSecurity = undefined;
  });
}

app.whenReady().then(async () => {
  applicationLog = createAppLog(app.getPath("logs"));
  applicationLog.write("info", "desktop", `Starting Raticode ${app.getVersion()} on ${process.platform} ${process.arch}`);
  process.on("uncaughtExceptionMonitor", (error) => applicationLog.emergency(error.stack || error.message));
  process.on("unhandledRejection", (error) => applicationLog.write("error", "desktop", error?.stack || error));
  app.on("web-contents-created", (_event, contents) => {
    contents.on("console-message", (details) => {
      if (contents !== mainWindow?.webContents && contents !== backendErrorWindow?.webContents) return;
      const { level, message } = details;
      if (["warning", "error"].includes(level)) applicationLog.write(level === "error" ? "error" : "warn", "renderer", message);
    });
  });
  app.on("render-process-gone", (_event, _contents, details) => applicationLog.write("error", "renderer", JSON.stringify(details)));
  Menu.setApplicationMenu(null);
  setupIpcHandlers();
  setupAutoUpdater();
  try {
    await startTerminalEditorServer();
    const backend = await startBackend();
    activeApiBaseUrl = backend.apiBaseUrl;
    activeUiApiToken = backend.apiToken;
    createWindow(backend.apiBaseUrl, backend.apiToken);
  } catch (error) {
    if (isSmokeTest) {
      console.error(
        `GOFER_ELECTRON_BACKEND_FAILED ${JSON.stringify({
          message: error instanceof Error ? error.message : String(error),
        })}`,
      );
      app.exit(1);
      return;
    }

    createBackendErrorWindow(error);
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && activeApiBaseUrl) {
      createWindow(activeApiBaseUrl, activeUiApiToken);
    }
  });
});

app.on("second-instance", () => {
  if (!mainWindow) return;

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
});

app.on("before-quit", (event) => {
  if (!archivesDrained) {
    event.preventDefault();
    void conversationArchives.close().finally(() => { archivesDrained = true; app.quit(); });
    return;
  }
  isQuitting = true;
  closeAllBrowsers();
  closeTerminalEditorServer();
  closeAllTerminals();
  stopBackend();
  if (!logsDrained && applicationLog) {
    event.preventDefault();
    if (!logsClosing) {
      logsClosing = true;
      void applicationLog.close().finally(() => { logsDrained = true; app.quit(); });
    }
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

function setupIpcHandlers() {
  ipcMain.on("gofer:migrate-legacy-projects", migrateLegacyProjects);
  ipcSecurity = createIpcSecurity({
    appRoots: [path.dirname(distIndexPath)],
    devServerUrl: VITE_DEV_SERVER_URL,
    getDataDir: getGoferDataDir,
    getMainWebContents: () =>
      mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null,
    isProduction,
    trustedRoots: readTrustedRoots(),
    persistTrustedRoot,
  });
  backendErrorIpcSecurity = createIpcSecurity({
    appRoots: [path.dirname(backendErrorHtmlPath)],
    devServerUrl: VITE_DEV_SERVER_URL,
    getDataDir: getGoferDataDir,
    getMainWebContents: () =>
      backendErrorWindow && !backendErrorWindow.isDestroyed()
        ? backendErrorWindow.webContents
        : null,
    isProduction,
  });
  registerIpcHandlers(ipcMain, {
    developerInfo,
    developerAction,
    rendererLog,
    remSettings,
    configureRem,
    archiveRem,
    checkForUpdates,
    copyPath,
    browserAction,
    browserOwnerZoom,
    createBrowser,
    createFile,
    createFolder,
    createTerminal,
    deletePath,
    downloadAndInstallUpdate,
    getGoferDataDir,
    gitRepoAction,
    gitFileAction,
    gitSwitchBranch,
    gitStatus,
    gitFileBaseline,
    gitHistory,
    gitWorktrees,
    gitWorktreeAdd,
    gitWorktreeRemove,
    grantPath,
    grantDroppedPath,
    grantUserPath,
    getUpdateState,
    apiSession: () => ({ apiBaseUrl: activeApiBaseUrl, apiToken: activeUiApiToken }),
    installDownloadedUpdate,
    listDirectory,
    searchProject: (_event, options = {}) => searchProject(resolveExactPath(options.projectRoot, { grantId: options.grantId, mustExist: true }), options),
    replaceProject: (_event, options = {}) => replaceProject(resolveExactPath(options.projectRoot, { grantId: options.grantId, mustExist: true }), options),
    openLogsFolder,
    openPath,
    openUpdateRelease,
    pathInfo,
    missingRecentFiles: (_event, options = {}) => missingRecentFiles(options.paths),
    missingThreadRoots: (_event, options = {}) => missingThreadRoots(options.paths),
    readBinaryPreview,
    readTextFile,
    resizeTerminal,
    renamePath,
    resolveProjectFile,
    restartBackend,
    revealPath,
    selectPath,
    setDataDir,
    closeTerminal,
    completeTerminalEditor,
    writeTerminal,
    writeTextFile,
  }, {
    secureHandler: (handler, channel) => async (event, ...args) => {
      if (
        (channel === "gofer:restart-backend" || channel === "gofer:open-logs") &&
        backendErrorWindow &&
        !backendErrorWindow.isDestroyed()
      ) {
        return backendErrorIpcSecurity.secureHandler(handler)(event, ...args);
      }
      try { return await ipcSecurity.secureHandler(handler)(event, ...args); }
      catch (error) {
        applicationLog?.write("error", "ipc", `${channel}: ${error.message}`);
        throw error;
      }
    },
  });
}

function createBrowser(event, options = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    throw new Error("The browser window is unavailable.");
  }
  const requestedPath = typeof options.path === "string" ? options.path.trim() : "";
  let url = "about:blank";
  let grantId = "";
  if (requestedPath) {
    const targetPath = resolveExactPath(requestedPath, {
      grantId: options.grantId,
      mustExist: true,
    });
    if (!fs.statSync(targetPath).isFile()) {
      throw new Error(`Browser preview path is not a file: ${targetPath}`);
    }
    url = pathToFileURL(targetPath).toString();
    grantId = typeof options.grantId === "string" ? options.grantId : "";
  } else if (options.url) {
    url = browserLoadUrl(normalizeBrowserUrl(options.url));
  }

  const id = crypto.randomUUID();
  const session = {
    applicationChord: null,
    applicationKeybindings: sanitizeBrowserKeybindings(options.applicationKeybindings),
    clientId: typeof options.clientId === "string" ? options.clientId : "",
    contents: null,
    error: "",
    favicon: "",
    grantId,
    id,
    initialUrl: url,
    localNavigation: url.startsWith("file:"),
    internalHomeUrl: url.startsWith("data:text/html") ? url : "",
    owner: event.sender,
    ownerId: event.sender.id,
    openBrowserBinding: typeof options.openBrowserBinding === "string"
      ? options.openBrowserBinding.slice(0, 120)
      : "Mod+Alt+Slash",
    ownerZoomFactor: 1,
    pageZoomFactor: 1,
    pendingSrc: url,
    projectChordDeadline: 0,
  };
  browserSessions.set(id, session);
  registerBrowserOwnerCleanup(event.sender);
  return { ...browserSessionState(session, url), src: url };
}

function isPendingBrowserSessionSrc(ownerId, src) {
  const requested = String(src ?? "");
  for (const session of browserSessions.values()) {
    if (session.ownerId === ownerId && session.pendingSrc && session.pendingSrc === requested) {
      return true;
    }
  }
  return requested === "about:blank";
}

function adoptBrowserContents(event, session, webContentsId) {
  const guest = webContents.fromId(Number(webContentsId));
  if (
    !guest
    || guest.isDestroyed()
    || guest.hostWebContents !== event.sender
    || guest.getType() !== "webview"
  ) {
    throw new Error("Browser view is unavailable.");
  }
  if (session.contents === guest) return;
  if (session.contents) throw new Error("Browser session already has a view.");
  session.contents = guest;
  session.pendingSrc = "";
  if (typeof guest.setBackgroundColor === "function") guest.setBackgroundColor("#ffffff");
  guest.once("destroyed", () => closeBrowserSession(session));
  configureBrowserSession(session);
  session.ownerZoomFactor = session.owner.getZoomFactor();
  syncBrowserContentZoom(session);
  emitBrowserState(session);
}

function browserAction(event, options = {}) {
  // Destruction of a guest can precede the renderer's cleanup IPC.
  if (["close", "adopt", "focus", "set-preferences"].includes(options.action) && !browserSessions.has(options.id)) {
    return { closed: true, ready: false };
  }
  const session = ownedBrowserSession(event, options.id);
  if (options.action === "adopt") {
    adoptBrowserContents(event, session, options.webContentsId);
    return browserSessionState(session);
  }
  if (options.action === "close") {
    closeBrowserSession(session);
    return { closed: true };
  }
  if (options.action === "set-preferences") {
    if (options.applicationKeybindings) {
      session.applicationKeybindings = sanitizeBrowserKeybindings(options.applicationKeybindings);
    }
    session.openBrowserBinding = typeof options.openBrowserBinding === "string"
      && options.openBrowserBinding
      ? options.openBrowserBinding.slice(0, 120)
      : session.openBrowserBinding;
    return browserSessionState(session);
  }
  const contents = browserSessionContents(session);
  if (!contents && options.action === "focus") return browserSessionState(session);
  if (!contents) throw new Error("Browser view is unavailable.");
  switch (options.action) {
    case "back":
      if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack();
      break;
    case "focus":
      contents.focus();
      break;
    case "forward":
      if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward();
      break;
    case "navigate":
      session.error = "";
      {
        const normalizedUrl = normalizeBrowserUrl(options.url);
        const loadUrl = browserLoadUrl(normalizedUrl);
        session.internalHomeUrl = normalizedUrl === RATICODE_HOME_URL ? loadUrl : "";
        session.localNavigation = loadUrl.startsWith("file:");
        void runBrowserOperation(session, () => contents.loadURL(loadUrl));
      }
      break;
    case "open-external": {
      const url = contents.getURL();
      if (isSafeExternalUrl(url)) void runBrowserOperation(session, () => shell.openExternal(url));
      break;
    }
    case "reload":
      contents.reload();
      break;
    case "stop":
      contents.stop();
      break;
    default:
      throw new Error(`Unknown browser action: ${options.action || "missing"}`);
  }
  return browserSessionState(session);
}

async function runBrowserOperation(session, operation) {
  try {
    await operation();
  } catch (error) {
    session.error = error instanceof Error ? error.message : String(error);
    // The view or its owner may have closed while navigation was pending.
    try { emitBrowserState(session); } catch { /* No live view to notify. */ }
  }
}

function configureBrowserSession(session) {
  const contents = session.contents;
  const update = () => emitBrowserState(session);
  contents.on("did-start-loading", () => {
    session.error = "";
    update();
  });
  contents.on("did-stop-loading", update);
  contents.on("did-navigate", () => {
    session.favicon = "";
    update();
  });
  contents.on("did-navigate-in-page", update);
  contents.on("page-title-updated", update);
  contents.on("page-favicon-updated", (_event, favicons) => {
    session.favicon = Array.isArray(favicons) ? String(favicons[0] || "") : "";
    update();
  });
  contents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
    if (!isMainFrame || errorCode === -3) return;
    session.error = `${errorDescription}: ${validatedUrl}`;
    update();
  });
  contents.on("render-process-gone", (_event, details) => {
    session.error = `Browser renderer stopped: ${details.reason}`;
    update();
  });
  contents.on("will-navigate", (event, url) => {
    if (!isAllowedBrowserNavigation(session, url)) event.preventDefault();
  });
  contents.on("ipc-message", (event, channel, payload) => {
    if (event.senderFrame !== contents.mainFrame) return;
    if (channel === "gofer:browser-link-clicked") openBrowserLink(session, payload?.url);
    if (
      channel === "gofer:browser-zoom"
      && (payload?.direction === 1 || payload?.direction === -1)
    ) {
      session.pageZoomFactor = 1;
      emitBrowserCommand(session, "text-zoom", { direction: payload.direction });
    }
    if (
      channel === "gofer:browser-navigation"
      && payload?.action === "back"
      && contents.navigationHistory.canGoBack()
    ) {
      contents.navigationHistory.goBack();
    }
  });
  contents.on("before-input-event", (event, input) => {
    const action = browserSessionShortcutAction(session, input, process.platform);
    if (!action) return;
    event.preventDefault();
    if (action === "chord-pending") return;
    if (action.startsWith("command:")) {
      emitBrowserCommand(session, "application-shortcut", {
        commandId: action.slice("command:".length),
      });
      return;
    }
    if ([
      "focus-location",
      "close",
      "next-tab",
      "previous-tab",
      "new-tab",
      "settings-open",
      "file-open",
      "project-open",
      "panel-toggle",
      "project-pane-toggle",
      "assistant-pane-toggle",
    ].includes(action)) {
      emitBrowserCommand(session, action);
      return;
    }
    if (action === "open-browser") {
      emitBrowserCommand(session, action);
      return;
    }
    if (action === "reload") contents.reload();
    if (action === "back" && contents.navigationHistory.canGoBack()) {
      contents.navigationHistory.goBack();
    }
    if (action === "forward" && contents.navigationHistory.canGoForward()) {
      contents.navigationHistory.goForward();
    }
    if (action === "zoom-in" || action === "zoom-out") {
      event.preventDefault();
      session.pageZoomFactor = 1;
      emitBrowserCommand(session, "text-zoom", {
        direction: action === "zoom-in" ? 1 : -1,
      });
    }
    if (action === "zoom-reset") {
      session.pageZoomFactor = 1;
      syncBrowserContentZoom(session);
      emitBrowserCommand(session, "text-zoom", { reset: true });
    }
  });
  if (session.grantId) {
    contents.on("before-mouse-event", (event, mouse) => {
      if (mouse.type !== "mouseDown" || mouse.button !== "left" || mouse.clickCount !== 2) return;
      event.preventDefault();
      emitBrowserCommand(session, "edit-local-html");
    });
  }
  contents.setWindowOpenHandler(({ url }) => {
    if ((session.grantId || session.localNavigation) && /^file:/i.test(contents.getURL())) {
      openBrowserLink(session, url);
      return { action: "deny" };
    }
    if (isAllowedBrowserNavigation(session, url)) {
      session.error = "";
      void runBrowserOperation(session, () => contents.loadURL(url));
    }
    return { action: "deny" };
  });
  contents.on("context-menu", (_event, params) => showBrowserContextMenu(session, params));
}

function openBrowserLink(session, value) {
  if (session.owner.isDestroyed()) return;
  let url;
  try { url = new URL(value); } catch { return; }
  if (url.protocol === "file:") {
    // Local previews hand links to the studio, which checks the destination's
    // own path grant and reports missing files. Never grant remote pages this route.
    if (!(session.grantId || session.localNavigation) || !/^file:/i.test(browserSessionContents(session)?.getURL() || "")) return;
    session.owner.send("gofer:browser-open-file", { href: url.toString() });
    return;
  }
  if (!["http:", "https:"].includes(url.protocol)) return;
  session.owner.send("gofer:browser-open-tab", { url: url.toString() });
}

function showBrowserContextMenu(session, params) {
  const contents = session.contents;
  const template = [];
  if (params.linkURL && isSafeExternalUrl(params.linkURL)) {
    template.push({
      label: "Open link in new browser tab",
      click: () => session.owner.send("gofer:browser-open-tab", { url: params.linkURL }),
    });
    template.push({ type: "separator" });
  }
  if (params.isEditable) {
    template.push({ role: "cut" }, { role: "copy" }, { role: "paste" }, { type: "separator" });
  } else if (params.selectionText) {
    template.push({ role: "copy" }, { type: "separator" });
  }
  template.push(
    {
      label: "Back",
      enabled: contents.navigationHistory.canGoBack(),
      click: () => contents.navigationHistory.goBack(),
    },
    {
      label: "Forward",
      enabled: contents.navigationHistory.canGoForward(),
      click: () => contents.navigationHistory.goForward(),
    },
    { label: "Reload", click: () => contents.reload() },
    { type: "separator" },
    { label: "Inspect", click: () => contents.inspectElement(params.x, params.y) },
  );
  Menu.buildFromTemplate(template).popup({ window: mainWindow });
}

function browserOwnerZoom(event) {
  const ownerZoomFactor = event.sender.getZoomFactor();
  for (const session of browserSessions.values()) {
    if (session.ownerId !== event.sender.id) continue;
    if (session.ownerZoomFactor === ownerZoomFactor) continue;
    session.ownerZoomFactor = ownerZoomFactor;
    syncBrowserContentZoom(session);
  }
  return { zoomFactor: ownerZoomFactor };
}

function syncBrowserContentZoom(session) {
  const contents = browserSessionContents(session);
  if (!contents) return;
  contents.setZoomFactor(browserContentZoomFactor(
    session.ownerZoomFactor,
    session.pageZoomFactor,
  ));
}

function browserSessionState(session, fallbackUrl = "") {
  const fallback = fallbackUrl || session.initialUrl || "about:blank";
  const contents = browserSessionContents(session);
  if (!contents) {
    if (!browserSessions.has(session.id)) {
      return {
        canGoBack: false,
        canGoForward: false,
        clientId: session.clientId,
        error: session.error || "Browser view is unavailable.",
        favicon: session.favicon || "",
        id: session.id,
        loading: false,
        title: "",
        url: fallback,
      };
    }
    return {
      ready: false,
      canGoBack: false,
      canGoForward: false,
      clientId: session.clientId,
      error: session.error,
      favicon: session.favicon || "",
      id: session.id,
      loading: !session.error,
      title: "",
      url: session.internalHomeUrl ? RATICODE_HOME_URL : fallback,
    };
  }
  const currentUrl = contents.getURL() || fallback;
  return {
    ready: true,
    canGoBack: contents.navigationHistory.canGoBack(),
    canGoForward: contents.navigationHistory.canGoForward(),
    clientId: session.clientId,
    error: session.error,
    favicon: session.favicon || "",
    id: session.id,
    loading: contents.isLoading(),
    title: contents.getTitle(),
    url: session.internalHomeUrl && currentUrl === session.internalHomeUrl
      ? RATICODE_HOME_URL
      : currentUrl,
  };
}

function emitBrowserState(session) {
  try {
    if (
      browserSessions.get(session.id) === session
      && browserSessionContents(session)
      && !session.owner.isDestroyed()
    ) {
      session.owner.send("gofer:browser-state", browserSessionState(session));
    }
  } catch (error) {
    // Electron can destroy either contents between the checks and the send.
    session.error = error instanceof Error ? error.message : String(error);
  }
}

function emitBrowserCommand(session, action, details = {}) {
  if (!session.owner.isDestroyed()) {
    if (browserCommandRequiresOwnerFocus(action)) session.owner.focus();
    session.owner.send("gofer:browser-command", {
      action,
      clientId: session.clientId,
      ...details,
      id: session.id,
    });
  }
}

function sanitizeBrowserKeybindings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([commandId, binding]) => (
    typeof commandId === "string" && commandId.length <= 120 && typeof binding === "string"
      ? [[commandId, binding.slice(0, 240)]]
      : []
  )));
}

function ownedBrowserSession(event, id) {
  const session = typeof id === "string" ? browserSessions.get(id) : null;
  if (!session || session.ownerId !== event.sender.id) {
    throw new Error("Browser session was not found.");
  }
  return session;
}

function closeBrowserSession(session) {
  if (!session || !browserSessions.has(session.id)) return;
  const contents = browserSessionContents(session);
  const restoreOwnerFocus = contents?.isFocused() === true;
  browserSessions.delete(session.id);
  session.contents = null;
  if (restoreOwnerFocus && !session.owner.isDestroyed()) session.owner.focus();
}

function browserSessionContents(session) {
  const contents = session?.contents;
  return contents && !contents.isDestroyed() ? contents : null;
}

function registerBrowserOwnerCleanup(owner) {
  if (browserOwnersWithCleanup.has(owner)) return;
  browserOwnersWithCleanup.add(owner);
  const ownerId = owner.id;
  owner.once("destroyed", () => closeBrowsersForOwner(ownerId));
}

function closeBrowsersForOwner(ownerId) {
  for (const session of [...browserSessions.values()]) {
    if (session.ownerId === ownerId) closeBrowserSession(session);
  }
}

function closeAllBrowsers() {
  for (const session of [...browserSessions.values()]) closeBrowserSession(session);
}

function isAllowedBrowserNavigation(session, value) {
  try {
    const url = new URL(value);
    if (["http:", "https:", "about:", "blob:", "data:"].includes(url.protocol)) return true;
    if (url.protocol !== "file:" || !(session.grantId || session.localNavigation)) return false;
    resolveExactPath(fileURLToPath(url), { grantId: session.grantId, mustExist: true });
    return true;
  } catch {
    return false;
  }
}

function createTerminal(event, options = {}) {
  const requestedCwd = typeof options.cwd === "string" ? options.cwd.trim() : "";
  const cwd = requestedCwd
    ? resolveExactPath(requestedCwd, { grantId: options.grantId, mustExist: true })
    : getGoferDataDir();
  if (!fs.statSync(cwd).isDirectory()) {
    throw new Error(`Terminal directory is not a folder: ${cwd}`);
  }

  const shell = terminalShell();
  const id = crypto.randomUUID();
  const editorToken = crypto.randomBytes(32).toString("hex");
  const terminal = pty.spawn(shell.command, shell.args, {
    cols: terminalDimension(options.cols, 80, 2, 500),
    cwd,
    env: {
      ...process.env,
      ...(shell.env ?? {}),
      COLORTERM: "truecolor",
      GIT_EDITOR: terminalEditorCommand(),
      TERM: "xterm-256color",
      RATICODE_EDITOR_CWD: cwd,
      RATICODE_EDITOR_ENDPOINT: terminalEditorEndpoint,
      RATICODE_EDITOR_RUNTIME: process.execPath,
      RATICODE_EDITOR_SCRIPT: path.join(__dirname, "terminal-editor.cjs"),
      RATICODE_EDITOR_TOKEN: editorToken,
      RATICODE_TERMINAL_ID: id,
      VISUAL: terminalEditorCommand(),
    },
    name: "xterm-256color",
    rows: terminalDimension(options.rows, 24, 1, 200),
  });
  const session = {
    id,
    editorToken,
    ownerId: event.sender.id,
    terminal,
  };
  terminalSessions.set(id, session);
  terminal.onData((data) => {
    if (!event.sender.isDestroyed()) {
      event.sender.send("gofer:terminal-data", { data, id });
    }
  });
  terminal.onExit(({ exitCode, signal }) => {
    terminalSessions.delete(id);
    if (!event.sender.isDestroyed()) {
      event.sender.send("gofer:terminal-exit", { exitCode, id, signal });
    }
  });

  return {
    cwd,
    id,
    pid: terminal.pid,
    shell: shell.label,
  };
}

function writeTerminal(event, options = {}) {
  const session = ownedTerminalSession(event, options.id);
  if (typeof options.data !== "string" || options.data.length > 64 * 1024) {
    throw new Error("Terminal input must be a string smaller than 64 KB.");
  }
  session.terminal.write(options.data);
  return { written: true };
}

function resizeTerminal(event, options = {}) {
  const session = ownedTerminalSession(event, options.id);
  session.terminal.resize(
    terminalDimension(options.cols, 80, 2, 500),
    terminalDimension(options.rows, 24, 1, 200),
  );
  return { resized: true };
}

function closeTerminal(event, options = {}) {
  const session = ownedTerminalSession(event, options.id);
  terminalSessions.delete(session.id);
  cancelTerminalEditorRequests(session.id, "The terminal was closed.");
  session.terminal.kill();
  return { closed: true };
}

function completeTerminalEditor(event, options = {}) {
  const request = typeof options.requestId === "string"
    ? terminalEditorRequests.get(options.requestId)
    : null;
  if (!request || request.ownerId !== event.sender.id) {
    throw new Error("Git editor request was not found.");
  }
  finishTerminalEditorRequest(request, { ok: true });
  return { completed: true };
}

function terminalEditorCommand() {
  const launcherPath = terminalEditorLauncherPath();
  return process.platform === "win32" ? `"${launcherPath}"` : shellQuote(launcherPath);
}

function terminalEditorLauncherPath() {
  const directory = path.join(app.getPath("userData"), "terminal-editor");
  fs.mkdirSync(directory, { recursive: true });
  if (process.platform === "win32") {
    const launcherPath = path.join(directory, "raticode-editor.cmd");
    fs.writeFileSync(launcherPath, [
      "@echo off",
      "set ELECTRON_RUN_AS_NODE=1",
      '"%RATICODE_EDITOR_RUNTIME%" "%RATICODE_EDITOR_SCRIPT%" %*',
      "",
    ].join("\r\n"), "utf8");
    return launcherPath;
  }
  const launcherPath = path.join(directory, "raticode-editor");
  fs.writeFileSync(launcherPath, [
    "#!/bin/sh",
    "export ELECTRON_RUN_AS_NODE=1",
    'exec "$RATICODE_EDITOR_RUNTIME" "$RATICODE_EDITOR_SCRIPT" "$@"',
    "",
  ].join("\n"), { encoding: "utf8", mode: 0o700 });
  fs.chmodSync(launcherPath, 0o700);
  return launcherPath;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`;
}

function startTerminalEditorServer() {
  if (terminalEditorServer) return Promise.resolve();
  terminalEditorEndpoint = process.platform === "win32"
    ? `\\\\.\\pipe\\raticode-editor-${process.pid}-${crypto.randomUUID()}`
    : path.join(os.tmpdir(), `raticode-editor-${process.pid}-${crypto.randomUUID()}.sock`);
  terminalEditorServer = net.createServer(handleTerminalEditorConnection);
  return new Promise((resolve, reject) => {
    terminalEditorServer.once("error", reject);
    terminalEditorServer.listen(terminalEditorEndpoint, () => {
      terminalEditorServer.off("error", reject);
      resolve();
    });
  });
}

function handleTerminalEditorConnection(socket) {
  socket.setEncoding("utf8");
  let requestText = "";
  const fail = (error) => {
    socket.end(`${JSON.stringify({ error, ok: false })}\n`);
  };
  socket.on("data", async (chunk) => {
    requestText += chunk;
    if (requestText.length > 16 * 1024) {
      fail("Git editor request was too large.");
      return;
    }
    const newline = requestText.indexOf("\n");
    if (newline < 0) return;
    socket.removeAllListeners("data");
    try {
      const payload = JSON.parse(requestText.slice(0, newline));
      const session = terminalSessions.get(payload.terminalId);
      if (!session || payload.token !== session.editorToken) {
        fail("Git editor request was rejected.");
        return;
      }
      const targetPath = path.resolve(String(payload.targetPath || ""));
      const stat = await fs.promises.stat(targetPath);
      if (!stat.isFile() || stat.size > 2 * 1024 * 1024) {
        fail("Git editor target is not an editable text file.");
        return;
      }
      const content = await fs.promises.readFile(targetPath);
      if (content.includes(0)) {
        fail("Git editor target is not a text file.");
        return;
      }
      const handle = pathHandle(targetPath);
      if (!getIpcSecurity().isUserGrant(handle.grantId)) await registerBackendPathGrant(handle);
      const request = {
        id: crypto.randomUUID(),
        ownerId: session.ownerId,
        socket,
        targetPath: handle.path,
        terminalId: session.id,
      };
      terminalEditorRequests.set(request.id, request);
      const owner = mainWindow?.webContents;
      if (!owner || owner.isDestroyed() || owner.id !== session.ownerId) {
        finishTerminalEditorRequest(request, { error: "Raticode's editor is unavailable.", ok: false });
        return;
      }
      owner.send("gofer:terminal-open-editor", { path: handle.path, requestId: request.id });
    } catch (error) {
      fail(error instanceof Error ? error.message : String(error));
    }
  });
}

function finishTerminalEditorRequest(request, response) {
  if (!request || !terminalEditorRequests.delete(request.id)) return;
  request.socket.end(`${JSON.stringify(response)}\n`);
}

function cancelTerminalEditorRequests(terminalId, error) {
  for (const request of [...terminalEditorRequests.values()]) {
    if (request.terminalId === terminalId) {
      finishTerminalEditorRequest(request, { error, ok: false });
    }
  }
}

function closeTerminalEditorServer() {
  for (const request of [...terminalEditorRequests.values()]) {
    finishTerminalEditorRequest(request, { error: "Raticode was closed.", ok: false });
  }
  terminalEditorServer?.close();
  terminalEditorServer = undefined;
  if (process.platform !== "win32" && terminalEditorEndpoint) {
    try { fs.unlinkSync(terminalEditorEndpoint); } catch {
      // The operating system may already have removed the socket.
    }
  }
  terminalEditorEndpoint = "";
}

function ownedTerminalSession(event, id) {
  const session = typeof id === "string" ? terminalSessions.get(id) : null;
  if (!session || session.ownerId !== event.sender.id) {
    throw new Error("Terminal session was not found.");
  }
  return session;
}

function terminalShell() {
  if (process.platform === "win32") {
    return {
      args: ["-NoLogo", "-NoExit", "-Command", powershellIntegrationCommand()],
      command: path.join(
        process.env.SystemRoot || "C:\\Windows",
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
      ),
      label: "PowerShell",
    };
  }
  const integrationPath = bashIntegrationPath();
  if (integrationPath) {
    return {
      args: ["--rcfile", integrationPath],
      command: "/bin/bash",
      label: "bash",
    };
  }
  return {
    args: [],
    command: "/bin/bash",
    env: {
      PROMPT_COMMAND: [
        "printf '\\033]633;P;Cwd=%s\\007' \"$PWD\"",
        process.env.PROMPT_COMMAND,
      ].filter(Boolean).join(";"),
    },
    label: "bash",
  };
}

function bashIntegrationPath() {
  try {
    const integrationDirectory = path.join(app.getPath("userData"), "shell-integration");
    const integrationPath = path.join(integrationDirectory, "bash.sh");
    const source = [
      'if [ -f "$HOME/.bashrc" ]; then',
      '  . "$HOME/.bashrc"',
      "fi",
      "__raticode_report_cwd() {",
      "  printf '\\033]633;P;Cwd=%s\\007' \"$PWD\"",
      "}",
      'case "$(declare -p PROMPT_COMMAND 2>/dev/null)" in',
      '  "declare -a"*) PROMPT_COMMAND[${#PROMPT_COMMAND[@]}]=__raticode_report_cwd ;;',
      '  *) if [ -n "$PROMPT_COMMAND" ]; then',
      '       PROMPT_COMMAND="__raticode_report_cwd;$PROMPT_COMMAND"',
      "     else",
      '       PROMPT_COMMAND="__raticode_report_cwd"',
      "     fi ;;",
      "esac",
      "",
    ].join("\n");
    fs.mkdirSync(integrationDirectory, { recursive: true });
    fs.writeFileSync(integrationPath, source, { encoding: "utf8", mode: 0o600 });
    return integrationPath;
  } catch {
    return "";
  }
}

function powershellIntegrationCommand() {
  return [
    "$global:__RaticodeOriginalPrompt = $function:prompt",
    'function global:prompt { $cwdPath = $executionContext.SessionState.Path.CurrentLocation.Path; [Console]::Write("$([char]27)]633;P;Cwd=$cwdPath$([char]7)"); if ($global:__RaticodeOriginalPrompt) { & $global:__RaticodeOriginalPrompt } else { "PS $cwdPath> " } }',
  ].join("; ");
}

function terminalDimension(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
}

function closeTerminalsForOwner(ownerId) {
  if (!ownerId) return;
  for (const [id, session] of terminalSessions.entries()) {
    if (session.ownerId !== ownerId) continue;
    terminalSessions.delete(id);
    cancelTerminalEditorRequests(id, "The terminal was closed.");
    session.terminal.kill();
  }
}

function closeAllTerminals() {
  for (const [id, session] of terminalSessions.entries()) {
    terminalSessions.delete(id);
    cancelTerminalEditorRequests(id, "The terminal was closed.");
    session.terminal.kill();
  }
}

function supportsAutoUpdates() {
  return app.isPackaged && !isSmokeTest
    && (process.platform !== "darwin" || raticodeReleaseSigning === "signed");
}

function setupAutoUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;

  autoUpdater.on("checking-for-update", () => {
    setUpdateState({ checking: true, error: "" });
  });
  autoUpdater.on("update-available", (info) => {
    setUpdateState({
      available: true,
      checking: false,
      downloaded: false,
      downloading: false,
      error: "",
      info: updateInfoPayload(info),
      progress: null,
    });
  });
  autoUpdater.on("update-not-available", (info) => {
    setUpdateState({
      available: false,
      checking: false,
      downloaded: false,
      downloading: false,
      error: "",
      info: updateInfoPayload(info),
      progress: null,
    });
  });
  autoUpdater.on("download-progress", (progress) => {
    setUpdateState({
      downloading: true,
      progress: {
        percent: Number(progress.percent || 0),
        transferred: Number(progress.transferred || 0),
        total: Number(progress.total || 0),
        bytesPerSecond: Number(progress.bytesPerSecond || 0),
      },
    });
  });
  autoUpdater.on("update-downloaded", (info) => {
    setUpdateState({
      available: true,
      checking: false,
      downloaded: true,
      downloading: false,
      error: "",
      info: updateInfoPayload(info),
      progress: { percent: 100 },
    });
    if (installUpdateAfterDownload) {
      installUpdateAfterDownload = false;
      setImmediate(() => {
        isQuitting = true;
        stopBackend();
        autoUpdater.quitAndInstall(false, true);
      });
    }
  });
  autoUpdater.on("error", (error) => {
    installUpdateAfterDownload = false;
    if (isNoPublishedVersionsError(error)) {
      setNoReleasesUpdateState();
      return;
    }
    setUpdateState({
      checking: false,
      downloading: false,
      error: error instanceof Error ? error.message : String(error),
    });
  });
}

async function checkForUpdates() {
  try {
    setUpdateState({ checking: true, error: "" });
    if (!supportsAutoUpdates()) {
      setUpdateState(await checkLatestReleaseFallback());
    } else {
      await autoUpdater.checkForUpdates();
    }
  } catch (error) {
    if (isNoPublishedVersionsError(error)) {
      setNoReleasesUpdateState();
      return getUpdateState();
    }
    setUpdateState({
      checking: false,
      downloading: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
  return getUpdateState();
}

async function downloadAndInstallUpdate() {
  if (!supportsAutoUpdates()) {
    await openUpdateRelease();
    return getUpdateState();
  }

  installUpdateAfterDownload = true;
  setUpdateState({ downloading: true, error: "" });
  await autoUpdater.downloadUpdate();
  return getUpdateState();
}

function installDownloadedUpdate() {
  if (!supportsAutoUpdates()) {
    return getUpdateState();
  }
  isQuitting = true;
  stopBackend();
  autoUpdater.quitAndInstall(false, true);
  return getUpdateState();
}

async function openUpdateRelease() {
  await shell.openExternal("https://github.com/zacharyivie/gofer-flow/releases/latest");
  return { opened: true };
}

function getUpdateState() {
  return {
    ...updateState,
    currentVersion: app.getVersion(),
    platform: process.platform,
    arch: process.arch,
    supported: supportsAutoUpdates(),
  };
}

function setUpdateState(patch) {
  updateState = { ...updateState, ...patch };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("gofer:update-state", getUpdateState());
  }
}

function setNoReleasesUpdateState() {
  setUpdateState({
    available: false,
    checking: false,
    downloaded: false,
    downloading: false,
    error: "",
    info: {
      noReleases: true,
      releaseName: "No published releases yet",
      version: app.getVersion(),
    },
    progress: null,
  });
}

function updateInfoPayload(info) {
  if (!info) return null;
  return {
    version: info.version || "",
    releaseName: info.releaseName || "",
    releaseDate: info.releaseDate || "",
  };
}

async function checkLatestReleaseFallback() {
  const currentVersion = app.getVersion();
  if (isSmokeTest) {
    return {
      available: false,
      checking: false,
      downloading: false,
      downloaded: false,
      error: "",
      info: { version: currentVersion },
      progress: null,
    };
  }

  const response = await fetch(LATEST_RELEASE_URL, {
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": `Raticode/${app.getVersion()}`,
    },
  });
  if (response.status === 404) {
    return {
      available: false,
      checking: false,
      downloading: false,
      downloaded: false,
      error: "",
      info: {
        noReleases: true,
        releaseName: "No published releases yet",
        version: currentVersion,
      },
      progress: null,
    };
  }
  if (!response.ok) {
    throw new Error(`GitHub releases API returned ${response.status}`);
  }
  const release = await response.json();
  return {
    available: compareVersions(normalizeVersion(release.tag_name), normalizeVersion(currentVersion)) > 0,
    checking: false,
    downloading: false,
    downloaded: false,
    error: "",
    info: {
      version: normalizeVersion(release.tag_name || release.name || ""),
      releaseName: release.name || release.tag_name || "",
      releaseDate: release.published_at || "",
    },
    progress: null,
  };
}

function isNoPublishedVersionsError(error) {
  const message = error instanceof Error ? error.message : String(error || "");
  return (
    message.includes("No published versions on GitHub") ||
    message.includes("ERR_XML_MISSED_ELEMENT")
  );
}

function normalizeVersion(value) {
  const match = String(value || "").trim().match(/v?(\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?)/);
  return match ? match[1] : "0.0.0";
}

function compareVersions(left, right) {
  const leftParts = versionParts(left);
  const rightParts = versionParts(right);
  for (let index = 0; index < 3; index += 1) {
    if (leftParts[index] > rightParts[index]) return 1;
    if (leftParts[index] < rightParts[index]) return -1;
  }
  return 0;
}

function versionParts(version) {
  return normalizeVersion(version)
    .split(/[.-]/)
    .slice(0, 3)
    .map((part) => Number.parseInt(part, 10) || 0);
}

async function openPath(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }

  const result = await shell.openPath(resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: true,
  }));
  if (result) {
    throw new Error(result);
  }
  return { opened: true };
}

async function revealPath(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }

  const targetPath = resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: true,
  });
  if (fs.existsSync(targetPath)) {
    shell.showItemInFolder(targetPath);
    return { opened: true };
  }

  const parentPath = path.dirname(targetPath);
  if (fs.existsSync(parentPath)) {
    const result = await shell.openPath(parentPath);
    if (result) {
      throw new Error(result);
    }
    return { opened: true };
  }

  throw new Error(`Path does not exist: ${targetPath}`);
}

async function pathInfo(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }

  const targetPath = resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: false,
  });
  return inspectPath(targetPath);
}

async function resolveProjectFile(_event, options = {}) {
  const selectedPath = resolveExactPath(options.selectedPath, {
    grantId: options.grantId,
    mustExist: true,
  });
  const stat = await fs.promises.stat(selectedPath);
  const selectedDirectory = stat.isDirectory() ? selectedPath : path.dirname(selectedPath);
  const discoveredRoot = nearestProjectRoot(selectedDirectory);
  const projectRoot = getIpcSecurity().grantForPath(discoveredRoot) ? discoveredRoot : selectedDirectory;
  const handle = pathHandle(projectRoot);
  if (!getIpcSecurity().isUserGrant(handle.grantId)) await registerBackendPathGrant(handle);
  return {
    directory: projectRoot,
    grantId: handle.grantId,
    selectedPath,
  };
}

function nearestProjectRoot(startDirectory) {
  let current = path.resolve(startDirectory);
  const fallback = current;
  while (true) {
    if (
      fs.existsSync(path.join(current, ".raticode"))
      || LEGACY_BRANDS.some((item) => fs.existsSync(path.join(current, item.workspaceDirectory)))
      || fs.existsSync(path.join(current, ".git"))
    ) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) return fallback;
    current = parent;
  }
}

function grantUserPath(_event, options = {}) {
  return getIpcSecurity().grantUserPath(options.targetPath);
}

async function grantPath(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }
  await restoreBackendTrustedRoots();
  let handle;
  try {
    handle = getIpcSecurity().renewPath(options.targetPath);
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { missing: true };
    }
    throw error;
  }
  await registerBackendPathGrant(handle);
  return handle;
}

// This channel is exposed only through preload's webUtils.getPathForFile(File).
// Synthetic renderer Files have no native path and cannot invoke it.
async function grantDroppedPath(_event, options = {}) {
  const selectedPath = path.resolve(options.targetPath);
  const stat = await fs.promises.stat(selectedPath);
  const root = stat.isFile() ? nearestProjectRoot(path.dirname(selectedPath)) : selectedPath;
  const handle = getIpcSecurity().trustPath(root);
  await registerBackendPathGrant(handle);
  return { path: selectedPath, grantId: handle.grantId };
}

async function copyPath(_event, options = {}) {
  if (!options.sourcePath || typeof options.sourcePath !== "string") {
    throw new Error("A source path is required.");
  }
  if (!options.destinationPath || typeof options.destinationPath !== "string") {
    throw new Error("A destination path is required.");
  }

  const sourcePath = resolveExactPath(options.sourcePath, {
    grantId: options.sourceGrantId,
    mustExist: true,
  });
  const destinationPath = resolveExactPath(options.destinationPath, {
    grantId: options.destinationGrantId,
  });
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`Path does not exist: ${sourcePath}`);
  }
  if (fs.existsSync(destinationPath)) {
    throw new Error(`Destination already exists: ${destinationPath}`);
  }
  await safeFiles.copyPath(sourcePath, destinationPath, {
    authorizeSource: (target) => resolveExactPath(target, { grantId: options.sourceGrantId, mustExist: true }),
    authorizeDestination: (target) => resolveExactPath(target, { grantId: options.destinationGrantId }),
  });
  return pathHandle(destinationPath);
}

async function deletePath(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }

  const targetPath = resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: true,
  });
  if (!fs.existsSync(targetPath)) {
    throw new Error(`Path does not exist: ${targetPath}`);
  }
  if (typeof shell.trashItem !== "function") {
    throw new Error("Trash is not available on this platform.");
  }
  await shell.trashItem(targetPath);
  return { deleted: true };
}

async function renamePath(_event, options = {}) {
  if (!options.sourcePath || typeof options.sourcePath !== "string") {
    throw new Error("A source path is required.");
  }
  if (!options.name || typeof options.name !== "string") {
    throw new Error("A new name is required.");
  }

  const sourcePath = resolveExactPath(options.sourcePath, {
    grantId: options.grantId,
    mustExist: true,
  });
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`Path does not exist: ${sourcePath}`);
  }
  const destinationPath = resolveNewChildPath(path.dirname(sourcePath), options.name, options.grantId);
  if (fs.existsSync(destinationPath)) {
    throw new Error(`Destination already exists: ${destinationPath}`);
  }
  await safeFiles.renamePath(sourcePath, destinationPath, (target) => resolveExactPath(target, { grantId: options.grantId }));
  return pathHandle(destinationPath);
}

async function createFile(_event, options = {}) {
  const filePath = resolveNewChildPath(options.directory, options.name, options.grantId);
  await safeFiles.writeFile(filePath, "", { exclusive: true, authorize: (target) => resolveExactPath(target, { grantId: options.grantId }) });
  return pathHandle(filePath);
}

async function createFolder(_event, options = {}) {
  const folderPath = resolveNewChildPath(options.directory, options.name, options.grantId);
  await safeFiles.createDirectory(folderPath, (target) => resolveExactPath(target, { grantId: options.grantId }));
  return pathHandle(folderPath);
}

async function readTextFile(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }

  const targetPath = resolveWorkflowSource(options.targetPath, options.grantId);
  const stat = await fs.promises.stat(targetPath);
  if (!stat.isFile()) {
    throw new Error(`Path is not a file: ${targetPath}`);
  }
  if (stat.size > 2 * 1024 * 1024) {
    throw new Error("File is too large to edit in Raticode.");
  }
  const content = await fs.promises.readFile(targetPath);
  if (content.includes(0)) {
    throw new Error("Binary files cannot be opened in the code editor.");
  }
  return {
    content: content.toString("utf-8"),
    ...pathHandle(targetPath),
  };
}

async function readBinaryPreview(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }
  const targetPath = resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: true,
  });
  const stat = await fs.promises.stat(targetPath);
  if (!stat.isFile()) throw new Error(`Path is not a file: ${targetPath}`);
  if (stat.size > 25 * 1024 * 1024) {
    throw new Error("File is too large to preview in Raticode.");
  }
  const mimeType = imageMimeType(targetPath);
  if (!mimeType) throw new Error("This file type does not have an image preview.");
  const content = await fs.promises.readFile(targetPath);
  return {
    dataUrl: `data:${mimeType};base64,${content.toString("base64")}`,
    ...pathHandle(targetPath),
  };
}

function imageMimeType(targetPath) {
  const types = {
    ".avif": "image/avif", ".bmp": "image/bmp", ".gif": "image/gif",
    ".ico": "image/x-icon", ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
  };
  return types[path.extname(targetPath).toLowerCase()] ?? "";
}

async function writeTextFile(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }
  if (typeof options.content !== "string") {
    throw new Error("File content is required.");
  }

  const targetPath = resolveWorkflowSource(options.targetPath, options.grantId);
  await safeFiles.writeFile(targetPath, options.content, { authorize: (target) => resolveExactPath(target, { grantId: options.grantId }) });
  return pathHandle(targetPath);
}

function resolveNewChildPath(directory, name, grantId = "") {
  return getIpcSecurity().resolveAllowedChildPath(directory, name, { grantId, desktop: true });
}

async function setDataDir(_event, options = {}) {
  if (!options.dataDir || typeof options.dataDir !== "string") {
    throw new Error("A data directory path is required.");
  }

  selectedDataDir = resolveExactPath(options.dataDir, {
    grantId: options.grantId,
    mustExist: true,
  });
  process.env.GOFER_DATA_DIR = selectedDataDir;
  fs.mkdirSync(selectedDataDir, { recursive: true });
  getIpcSecurity().grantPath(selectedDataDir);
  writePersistedDataDir(selectedDataDir);
  await restartBackend();
  return { dataDir: selectedDataDir };
}

async function listDirectory(_event, options = {}) {
  try {
    const directory = resolveExactPath(options.currentPath, {
      grantId: options.grantId,
      mustExist: true,
    });
    let entries = await fs.promises.readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && path.extname(entry.name).toLowerCase() === ".rad") {
        resolveWorkflowSource(path.join(directory, entry.name), options.grantId);
      }
    }
    entries = await fs.promises.readdir(directory, { withFileTypes: true });

    return {
      ...pathHandle(directory),
      directory,
      parent: path.dirname(directory) === directory ? null : path.dirname(directory),
      entries: entries
        .map((entry) => ({
          hidden: entry.name.startsWith("."),
          isDirectory: entry.isDirectory(),
          isFile: entry.isFile(),
          name: entry.name,
          ...entryPathHandle(path.join(directory, entry.name)),
        }))
        .sort((left, right) => {
          if (left.isDirectory !== right.isDirectory) {
            return left.isDirectory ? -1 : 1;
          }
          return left.name.localeCompare(right.name);
        }),
    };
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { directory: options.currentPath, entries: [], missing: true };
    }
    throw error;
  }
}

function developerInfo() {
  return { version: app.getVersion(), platform: `${process.platform} ${process.arch}`, electron: process.versions.electron,
    node: process.versions.node, dataDir: getGoferDataDir(), userData: app.getPath("userData"), logs: app.getPath("logs"),
    appLog: applicationLog?.file || "", backend: backendProcess && !backendProcess.killed ? "Running" : "Stopped",
    archiveFolder: remSettings().archiveFolder };
}
async function developerAction(_event, { action } = {}) {
  if (action === "logs") return shell.openPath(app.getPath("logs"));
  if (action === "data") return shell.openPath(getGoferDataDir());
  if (action === "user-data") return shell.openPath(app.getPath("userData"));
  if (action === "devtools") { mainWindow?.webContents.openDevTools({ mode: "detach" }); return; }
  if (action === "restart") return restartBackend();
  if (action === "read-log") {
    const file = applicationLog?.file;
    if (!file || !fs.existsSync(file)) return "";
    const size = fs.statSync(file).size;
    const fd = fs.openSync(file, "r");
    try { const buffer = Buffer.alloc(Math.min(size, 64000)); fs.readSync(fd, buffer, 0, buffer.length, Math.max(0, size - buffer.length)); return buffer.toString("utf8"); }
    finally { fs.closeSync(fd); }
  }
  throw new Error("Unknown developer action.");
}
function rendererLog(_event, { message } = {}) {
  applicationLog?.write("error", "renderer", String(message || ""));
}
function remSettings() {
  try { return JSON.parse(fs.readFileSync(path.join(app.getPath("userData"), "rem-settings.json"), "utf8")); }
  catch (error) { if (error.code !== "ENOENT") throw error; return { archiveFolder: "", secondBrainEnabled: false, secondBrainRoot: "", secondBrainFormat: "md", secondBrainTheme: "auto" }; }
}
async function configureRem(_event, options = {}) {
  const config = remSettings();
  if (options.key === "reset") Object.assign(config, { archiveFolder: "", secondBrainEnabled: false, secondBrainRoot: "", secondBrainFormat: "md", secondBrainTheme: "auto" });
  else if (["archiveFolder", "secondBrainRoot"].includes(options.key)) {
    const folder = options.value ? getIpcSecurity().resolveAllowedPath(options.value, { grantId: options.grantId, mustExist: true }) : "";
    if (folder && !(await fs.promises.stat(folder)).isDirectory()) throw new Error("Choose a folder.");
    config[options.key] = folder;
  } else if (options.key === "secondBrainEnabled") {
    if (options.value && !config.secondBrainRoot) throw new Error("Choose a Second Brain folder first.");
    config.secondBrainEnabled = options.value === true;
  } else if (options.key === "secondBrainFormat" && ["md", "html"].includes(options.value)) config.secondBrainFormat = options.value;
  else if (options.key === "secondBrainTheme" && REPORT_THEMES.includes(options.value)) config.secondBrainTheme = options.value;
  else throw new Error("Unknown Rem setting.");
  if (!config.secondBrainRoot) config.secondBrainEnabled = false;
  const file = path.join(app.getPath("userData"), "rem-settings.json");
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(config), { mode: 0o600 });
  fs.renameSync(`${file}.tmp`, file);
  return config;
}
async function archiveRem(_event, { thread, messages, deleted } = {}) {
  const config = remSettings();
  if (!config.archiveFolder) return { skipped: true };
  const result = await conversationArchives.archive(config.archiveFolder, thread, messages, { dataDir: getGoferDataDir(), deleted: deleted === true });
  for (const warning of result.warnings || []) applicationLog?.write("warn", "archive", warning);
  return result;
}

async function gitRepoAction(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  try { return await gitRepositoryAction(projectRoot, options.action, options.value, { authorizeTarget: (target) => resolveGitProjectDirectory({ projectRoot: target, grantId: getIpcSecurity().grantForPath(target) }) }); }
  catch (error) { return { ...await readGitStatus(projectRoot), error: String(error.stderr || error.message).replace(/^Command failed:[^\n]*\n?/, "").trim() }; }
}

async function gitFileAction(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  return changeGitFile(projectRoot, options.relativePath, options.action, { trashItem: (target) => shell.trashItem(target) });
}

async function gitSwitchBranch(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  return switchGitBranch(projectRoot, options.branch);
}

async function gitStatus(_event, options = {}) {
  try {
    const projectRoot = resolveExactPath(options.projectRoot, {
      grantId: options.grantId,
      mustExist: true,
    });
    const stat = await fs.promises.stat(projectRoot);
    if (!stat.isDirectory()) {
      throw new Error(`Git status path is not a folder: ${projectRoot}`);
    }
    return await readGitStatus(projectRoot);
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { active: false, entries: [], missing: true };
    }
    throw error;
  }
}

async function gitFileBaseline(_event, options = {}) {
  const targetPath = resolveExactPath(options.targetPath, { grantId: options.grantId, mustExist: false });
  return readGitFileBaseline(targetPath, { group: options.group });
}

async function gitHistory(_event, options = {}) {
  try {
    const projectRoot = await resolveGitProjectDirectory(options);
    return await readGitHistory(projectRoot);
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { active: false, commits: [], missing: true };
    }
    throw error;
  }
}

async function gitWorktrees(_event, options = {}) {
  try {
    const projectRoot = await resolveGitProjectDirectory(options);
    const result = await readGitWorktrees(projectRoot);
    // Enumeration is desktop navigation. Register backend access only when a
    // project is selected, through trustProjectRoot, not for every listed tree.
    const worktrees = result.worktrees.map((worktree) => {
      if (worktree.missing) return worktree;
      const handle = entryPathHandle(worktree.path);
      return { ...worktree, grantId: handle.grantId, path: handle.path };
    });
    return { ...result, worktrees };
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { active: false, worktrees: [], missing: true };
    }
    throw error;
  }
}

async function gitWorktreeAdd(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  if (typeof options.targetPath !== "string" || !options.targetPath.trim()) throw new Error("A worktree folder is required.");
  if (typeof options.branch !== "string" || !options.branch.trim()) throw new Error("A branch is required.");
  const targetPath = resolveExactPath(options.targetPath, { grantId: options.targetGrantId, mustExist: true });
  const targetStat = await fs.promises.stat(targetPath);
  if (!targetStat.isDirectory()) throw new Error("The worktree target must be a folder.");
  const result = await addGitWorktree(projectRoot, targetPath, options.branch.trim(), { createBranch: options.createBranch === true, startPoint: options.startPoint });
  const handle = pathHandle(targetPath);
  if (!getIpcSecurity().isUserGrant(handle.grantId)) await registerBackendPathGrant(handle);
  return { ...result, createdPath: targetPath, grantId: handle.grantId };
}

async function gitWorktreeRemove(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  const listed = await readGitWorktrees(projectRoot);
  const listedWorktree = listed.worktrees.find(
    (worktree) => path.resolve(worktree.path) === path.resolve(String(options.targetPath || "")),
  );
  if (!listedWorktree) throw new Error("The selected path is not a registered worktree.");
  const targetPath = listedWorktree.missing
    ? listedWorktree.path
    : resolveExactPath(options.targetPath, {
        grantId: options.targetGrantId,
        mustExist: true,
      });
  if (path.resolve(targetPath) === path.resolve(projectRoot)) throw new Error("The current worktree cannot be removed.");
  return removeGitWorktree(projectRoot, targetPath, { force: options.force === true });
}

async function resolveGitProjectDirectory(options = {}) {
  const projectRoot = resolveExactPath(options.projectRoot, { grantId: options.grantId, mustExist: true });
  const stat = await fs.promises.stat(projectRoot);
  if (!stat.isDirectory()) throw new Error(`Git project path is not a folder: ${projectRoot}`);
  return projectRoot;
}

function resolveWorkflowSource(currentPath, grantId) {
  const authorize = (target) => resolveExactPath(target, { grantId });
  return migrateSource(authorize(currentPath), authorize);
}

function resolveExactPath(currentPath, options = {}) {
  return getIpcSecurity().resolveDesktopPath(currentPath, options);
}

async function selectPath(_event, options = {}) {
  const parentWindow =
    mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
  const defaultPath = resolvePickerDefaultPath(options.currentPath, options.grantId);
  const properties = options.directoryOnly === true
    ? ["openDirectory", "showHiddenFiles", "createDirectory"]
    : options.fileOnly === true
      ? ["openFile", "showHiddenFiles"]
      : ["openFile", "openDirectory", "showHiddenFiles", "createDirectory"];
  const result = await dialog.showOpenDialog(parentWindow, {
    defaultPath,
    properties,
    ...(options.directoryOnly === true ? { buttonLabel: "Open Folder", title: "Open Folder" } : {}),
    ...(options.fileOnly === true ? { buttonLabel: "Open File", title: "Open File" } : {}),
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  const selectedPath = result.filePaths[0];
  const selectedStat = await fs.promises.stat(selectedPath);
  // Selecting a project file has always opened its project. Derive that root
  // from the native picker result, never from a renderer-supplied path.
  const root = selectedStat.isFile() ? nearestProjectRoot(path.dirname(selectedPath)) : selectedPath;
  const trusted = getIpcSecurity().trustPath(root);
  const handle = { path: selectedPath, grantId: trusted.grantId };
  await registerBackendPathGrant(handle);
  return handle;
}

function resolvePickerDefaultPath(currentPath, grantId = "") {
  return getIpcSecurity().resolvePickerPath(currentPath, { grantId });
}

function pathHandle(targetPath) {
  const security = getIpcSecurity();
  const existingGrantId = security.grantForPath(targetPath) || security.userGrantForPath(targetPath);
  if (existingGrantId) {
    return { grantId: existingGrantId, path: targetPath };
  }
  return security.grantUserPath(targetPath);
}

function entryPathHandle(targetPath) {
  try { return pathHandle(targetPath); }
  catch { return { path: targetPath, grantId: "" }; }
}

function trustedProjectStore() {
  return createTrustedProjectStore(path.join(app.getPath("userData"), "trusted-projects.json"));
}

function migrateLegacyProjects(event, payload) {
  event.returnValue = false;
  try {
    getIpcSecurity().assertTrustedSender(event);
    if (!legacyProjectMigrationToken || payload?.token !== legacyProjectMigrationToken) return;
    // Only the isolated preload's initial snapshot may use this launch token.
    // Reloads and later renderer edits cannot add new trusted roots.
    legacyProjectMigrationToken = "";
    if (payload.unavailable) return;
    if (payload.recentProjects !== null && typeof payload.recentProjects !== "string") return;
    const roots = trustedProjectStore().migrate(payload.recentProjects);
    for (const root of roots) {
      getIpcSecurity().restoreTrustedPath(root);
    }
    event.returnValue = true;
  } catch {
    writeBackendLog("LEGACY_PROJECT_MIGRATION_FAILED\n");
  }
}

function readTrustedRoots() {
  let roots = [];
  try { roots = trustedProjectStore().read().roots; }
  catch { writeBackendLog("TRUSTED_PROJECT_REGISTRY_UNAVAILABLE\n"); }
  // These paths were accepted and persisted by main after native selection.
  // Restore them during migration without exposing a new grant to the renderer.
  try {
    const settings = remSettings();
    for (const key of ["archiveFolder", "secondBrainRoot"]) {
      if (typeof settings[key] === "string" && settings[key]) roots.push(settings[key]);
    }
  } catch { /* Invalid settings remain unavailable. */ }
  return [...new Set(roots)];
}

function persistTrustedRoot(root) {
  trustedProjectStore().add([...readTrustedRoots(), root]);
}

let restoredBackendRootsFor = "";
async function restoreBackendTrustedRoots() {
  if (!activeApiBaseUrl || !activeUiApiToken || restoredBackendRootsFor === activeUiApiToken) return;
  const response = await fetch(`${activeApiBaseUrl}/api/desktop/trusted-roots`, {
    headers: { Authorization: `Bearer ${activeUiApiToken}`, "X-Gofer-Desktop-Grant-Secret": desktopGrantSecret },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Could not restore trusted project folders.");
  const payload = await response.json();
  for (const root of payload.roots || []) {
    try { getIpcSecurity().trustPath(root); } catch { /* Removed projects remain unavailable. */ }
  }
  restoredBackendRootsFor = activeUiApiToken;
}

async function registerBackendPathGrant(handle) {
  if (getIpcSecurity().isUserGrant(handle?.grantId)) {
    throw new Error("User file navigation does not grant agent access.");
  }
  const startedAt = Date.now();
  let reason = "backend-unavailable";
  let status = null;
  try {
    if (!activeApiBaseUrl || !desktopGrantSecret) throw new Error("Backend unavailable");
    reason = "invalid-handle";
    if (!handle || typeof handle.path !== "string" || !handle.path
      || typeof handle.grantId !== "string" || !handle.grantId) throw new Error("Invalid folder handle");
    const headers = {
      "Content-Type": "application/json",
      "X-Gofer-Desktop-Grant-Secret": desktopGrantSecret,
    };
    if (activeUiApiToken) {
      headers.Authorization = `Bearer ${activeUiApiToken}`;
    }
    reason = "network-error";
    const response = await fetch(`${activeApiBaseUrl}/api/desktop/path-grants`, {
      method: "POST",
      headers,
      body: JSON.stringify({ grantId: handle.grantId, path: handle.path }),
      signal: AbortSignal.timeout(10000),
    });
    status = response.status;
    reason = "http-error";
    if (!response.ok) throw new Error("Registration rejected");
    reason = "invalid-response";
    const payload = await response.json();
    if (payload?.grantId !== handle.grantId || typeof payload?.path !== "string" || !payload.path) {
      throw new Error("Registration acknowledgment does not match");
    }
    writeBackendLog(`PATH_GRANT_REGISTERED ${JSON.stringify({ path: handle.path, status, durationMs: Date.now() - startedAt })}\n`);
  } catch (error) {
    if (error?.name === "TimeoutError" || error?.name === "AbortError") reason = "timeout";
    // Do not log headers, grant IDs, response bodies, or raw network errors.
    writeBackendLog(`PATH_GRANT_REGISTER_FAILED ${JSON.stringify({ path: handle?.path, reason, status, durationMs: Date.now() - startedAt })}\n`);
    const detail = reason === "timeout" ? "The backend timed out."
      : reason === "http-error" ? `The backend rejected registration with HTTP ${status}.`
        : reason === "backend-unavailable" ? "The backend is not ready."
          : "The backend could not confirm folder access.";
    throw new Error(`Could not renew Raticode folder access for ${JSON.stringify(handle?.path || "the selected folder")}. ${detail} Retry the action. If it keeps failing, restart Raticode.`, { cause: error });
  }
}

function getIpcSecurity() {
  if (!ipcSecurity) {
    ipcSecurity = createIpcSecurity({
      appRoot: path.dirname(distIndexPath),
      devServerUrl: VITE_DEV_SERVER_URL,
      getDataDir: getGoferDataDir,
      getMainWebContents: () =>
        mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null,
      isProduction,
      trustedRoots: readTrustedRoots(),
      persistTrustedRoot,
    });
  }
  return ipcSecurity;
}

async function restartBackend() {
  stopBackend();
  try {
    const backend = await startBackend();
    activeApiBaseUrl = backend.apiBaseUrl;
    activeUiApiToken = backend.apiToken;
    if (backendErrorWindow && !backendErrorWindow.isDestroyed()) {
      backendErrorWindow.close();
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.close();
    }
    createWindow(backend.apiBaseUrl, backend.apiToken);
  } catch (error) {
    createBackendErrorWindow(error);
  }
}

function openLogsFolder() {
  fs.mkdirSync(app.getPath("logs"), { recursive: true });
  shell.openPath(app.getPath("logs"));
}
