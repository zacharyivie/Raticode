import { normalizeReportThemes, reportOutputFormat, REPORT_FORMATS } from "./report-themes.js";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { unixTerminalShell } = require("./terminal-shell.cjs");
const { restoreShellPath } = require("./shell-path.cjs");
const { startReportPdfService } = require("./report-pdf.cjs");
const shellPathReady = restoreShellPath();
const {
  app,
  clipboard,
  BrowserWindow,
  Menu,
  Tray,
  nativeImage,
  dialog,
  ipcMain,
  shell,
  session,
  protocol,
  systemPreferences,
  webContents,
} = require("electron");
protocol.registerSchemesAsPrivileged([{ scheme: "raticode-media", privileges: { standard: true, secure: true, stream: true } }]);
const { createMediaPreviews } = require("./media-preview.cjs");
const mediaPreviews = createMediaPreviews();
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
  readGitBranches,
  readGitWorktrees,
  readGitLocation,
  invalidateGitReads,
  removeGitWorktree,
} = require("./git-status.cjs");
const { createGitWatcher } = require("./git-watch.cjs");
const gitWatcher = createGitWatcher({ locate: (projectRoot) => readGitLocation(projectRoot) });
const {
  createAgentBrowserController,
  createCapabilityRegistry,
  removeLegacyAgentBrowserConnection,
  startAgentBrowserService,
} = require("./agent-browser.cjs");
const { createBrowserSessions } = require("./browser-sessions.cjs");
const { createBrowserStore } = require("./browser-store.cjs");
const { createOpenWith } = require("./open-with.cjs");
const { PARTITIONS: BROWSER_PARTITIONS } = require("./browser-policy.cjs");
const { searchProject, replaceProject } = require("./project-search.cjs");
const { createAppLog, redactLog } = require("./app-log.cjs");
const { createBackendOutput } = require("./backend-log.cjs");
const util = require("node:util");
const { createArchiveQueue } = require("./archive-queue.cjs");
const conversationArchives = createArchiveQueue();
const { createPathGrantQueue } = require("./path-grant-queue.cjs");
const backendPathGrants = createPathGrantQueue();
// Folder access is renewed every 14 minutes; the log notes each folder once per backend.
const loggedPathGrants = new Set();
let archivesDrained = false;
let logsDrained = false;
let logsClosing = false;
const REPORT_THEMES = ["auto", "light", "dark", "sepia", "vaporwave", "steam", "carbon", "botanical", "blueprint", "arcade", "sakura", "deep-sea", "solarpunk", "noir", "candy-lab", "cosmic"];
let applicationLog;
const { registerIpcHandlers } = require("./ipc-handlers.cjs");
const { createFirefoxEngine } = require("./firefox-engine.cjs");
const { createGeckoWindow } = require("./gecko-window.cjs");
const { X11_SWITCH, relaunchNeeded } = require("./gecko-display.cjs");
const { CHROME, EDGE, createChromiumEngine } = require("./chromium-engine.cjs");
const { BACKEND_FEATURES, featureEnv, featureStates, readFeatureChoices, writeFeatureChoice } = require("./feature-preview.cjs");
const { inspectPath, missingRecentFiles, missingThreadRoots } = require("./path-info.cjs");
const safeFiles = require("./safe-files.cjs");
const { migrateSource } = require("./rattish-migration.cjs");
const { installPermissionPolicy, isStudioDocument, studioCsp } = require("./studio-policy.cjs");
const { createSystemMicrophoneGate, describeSpeechHelper } = require("./voice-diagnostics.cjs");
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
const ELECTRON_READY_MESSAGE = "GOFER_ELECTRON_READY";
const BACKEND_EXECUTABLE_NAME = process.platform === "win32" ? "Raticode.exe" : "Raticode";
// Release downloads avoid the REST API's 60 requests/hour limit for anonymous callers.
const RELEASES_URL = "https://github.com/zacharyivie/Raticode/releases";
const isProduction =
  app.isPackaged || process.env.GOFER_ELECTRON_MODE === "production";
const isSmokeTest = process.env.GOFER_ELECTRON_SMOKE_TEST === "1";
let backendReady;
let backgroundTray;
let backendProcess;
let reportPdfService;
let agentBrowserService;
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
const terminalEditorRequests = new Map();
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

// Gecko's window can only be placed inside Raticode's over X11. The dev runner
// adds the switch itself, since it has to stay Electron's parent.
if (!isSmokeTest && !process.env.VITE_DEV_SERVER_URL && relaunchNeeded({ userData: app.getPath("userData") })) {
  app.relaunch({ args: [...process.argv.slice(1), X11_SWITCH] });
  app.exit(0);
}

const singleInstanceLock = isSmokeTest || app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  if (!isProduction) {
    // The running window may come from another checkout with different code.
    // Say so instead of silently focusing it as if this launch had worked.
    console.error(
      `Raticode is already running with the profile ${app.getPath("userData")}.\n`
      + `The existing window was focused; ${repoRoot} did not start.\n`
      + "Close the other Raticode development window, then launch again.",
    );
  }
  app.quit();
  process.exit(isProduction ? 0 : 1);
}

let lastWindowArgs = null;
const shownWindows = new WeakSet();
function createWindow(apiBaseUrl, apiToken = "", { show = true } = {}) {
  lastWindowArgs = [apiBaseUrl, apiToken];
  legacyProjectMigrationToken = "";
  try {
    if (!trustedProjectStore().read().legacyRecentProjectsMigrated) legacyProjectMigrationToken = crypto.randomUUID();
  } catch (error) { logTrustedRegistryFailure(error); }
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 980,
    minHeight: 640,
    show: false,
    title: "Raticode",
    icon: path.join(__dirname, app.isPackaged ? "../dist/icon.png" : "../public/icon.png"),
    backgroundColor: "#1f1f1f",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: !isSmokeTest,
      webviewTag: true,
      backgroundThrottling: false,
      additionalArguments: [
        `--gofer-api-base-url=${apiBaseUrl}`,
        `--gofer-api-token=${apiToken}`,
        `--gofer-browser-preload=${pathToFileURL(browserPreloadPath)}`,
        ...(legacyProjectMigrationToken ? [`--gofer-legacy-project-migration=${legacyProjectMigrationToken}`] : []),
      ],
    },
  });
  // Set the launch size before showing the startup shell to avoid a small-window flash.
  // maximize() also shows a hidden window, so a background window waits until opened.
  if (show) {
    mainWindow.maximize();
    mainWindow.show();
    shownWindows.add(mainWindow);
  }
  const window = mainWindow;
  const terminalOwnerId = mainWindow.webContents.id;
  const studioOptions = { indexPath: distIndexPath, devServerUrl: VITE_DEV_SERVER_URL, isProduction };
  browsers.install();
  const systemMicrophone = createSystemMicrophoneGate({
    systemPreferences,
    helperPath: macosSpeechHelperPath,
    log: (level, message) => applicationLog?.write(level, "desktop", message),
  });
  installPermissionPolicy(session.fromPartition(BROWSER_PARTITIONS.preview), mainWindow.webContents.session,
    () => mainWindow?.webContents, { ...studioOptions, systemMicrophone });
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
    if (!browsers.isPendingAttach(terminalOwnerId, params)) {
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
  mainWindow.webContents.on("did-attach-webview", (_event, guestContents) => browsers.hardenGuest(guestContents));

  mainWindow.on("unresponsive", () => applicationLog?.write("warn", "renderer", "The Raticode window stopped responding"));
  mainWindow.on("responsive", () => applicationLog?.write("info", "renderer", "The Raticode window is responding again"));
  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
    // -3 is an aborted load, which a normal navigation or reload causes.
    if (!isMainFrame || errorCode === -3) return;
    applicationLog?.write("error", "renderer", `The Raticode window could not load ${String(validatedUrl || "").split(/[?#]/)[0]}: ${errorDescription} (${errorCode})`);
  });

  mainWindow.webContents.once("did-finish-load", () => {
    if (!isSmokeTest) return;

    void backendReady.then(() => {
      console.log(ELECTRON_READY_MESSAGE);
      setTimeout(() => app.quit(), 250);
    }).catch(() => {});
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

  mainWindow.on("close", (event) => {
    if (isQuitting || isSmokeTest) return;
    event.preventDefault();
    mainWindow.hide();
  });


  mainWindow.on("closed", () => {
    browsers.closeForOwner(terminalOwnerId);
    closeTerminalsForOwner(terminalOwnerId);
    if (mainWindow !== window) return; // Already replaced, for example by a backend restart.
    mainWindow = undefined;
    // A page-initiated close destroys the window instead of hiding it. Raticode keeps
    // running in the tray, so restore a hidden window to keep phone sync and archives going.
    if (!isQuitting && !isSmokeTest) setTimeout(() => {
      if (!isQuitting && !mainWindow && lastWindowArgs) createWindow(...lastWindowArgs, { show: false });
    }, 1000);
  });

  // The hidden window keeps phone thread sync and archives running in the
  // background. Reload a crashed renderer, a few times per hour at most.
  const rendererRestarts = [];
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    if (isQuitting || isSmokeTest || details.reason === "clean-exit") return;
    const now = Date.now();
    while (rendererRestarts.length && now - rendererRestarts[0] > 60 * 60 * 1000) rendererRestarts.shift();
    if (rendererRestarts.length >= 3) return;
    rendererRestarts.push(now);
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed()) mainWindow.webContents.reload();
    }, 2000);
  });
}

// Choose the API origin before loading the document so CSP remains restricted
// to one port. A bind conflict is surfaced by normal backend startup handling.
function allocateBackendPort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const port = probe.address().port;
      probe.close(error => error ? reject(error) : resolve(port));
    });
  });
}

function startBackend(port = 0) {
  backendPathGrants.reset();
  loggedPathGrants.clear();
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
      String(port),
      "--data-dir",
      getGoferDataDir(),
    ];

    const child = spawn(backendCommand.command, args, {
      cwd: repoRoot,
      env: {
        ...featureEnv(featureChoices(), process.env),
        ...reportPdfService?.env,
        GOFER_DESKTOP_GRANT_SECRET: desktopGrantSecret,
        GOFER_UI_EMIT_READY_TOKEN: "1",
        // Backend log records arrive on stderr as JSON lines for Settings > Developer.
        GOFER_LOG_FORMAT: "desktop",
        GOFER_LOG_LEVEL: process.env.GOFER_LOG_LEVEL || "debug",
        // Paired phones read the same log from its file (Developer log on the phone).
        ...(applicationLog?.file ? { GOFER_APP_LOG: applicationLog.file } : {}),
        // Dictation on macOS uses Apple's on-device recognizer through this helper.
        ...(process.platform === "darwin" ? { GOFER_SPEECH_HELPER: macosSpeechHelperPath() } : {}),
        // Lets the backend grant and revoke Rem's per-turn browser access.
        ...(agentBrowserService ? {
          GOFER_BROWSER_SERVICE_URL: agentBrowserService.url,
          GOFER_BROWSER_ADMIN_TOKEN: agentBrowserService.adminToken,
        } : {}),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    backendProcess = child;
    backendLogStream = createBackendLogStream();
    const backendStarted = Date.now();
    applicationLog?.write("info", "backend", `Starting backend: ${path.basename(backendCommand.command)} ${args.slice(0, backendCommand.args.length + 2).join(" ")} (process ${child.pid ?? "not started"})`);

    let settled = false;
    let stdoutBuffer = "";
    let stderrBuffer = "";
    // Cold starts can take minutes on slow machines. Wait for the ready message
    // or a real process failure; elapsed time must not kill a healthy backend.

    function succeed(apiBaseUrl, apiToken = "") {
      if (settled) return;
      settled = true;
      applicationLog?.write("info", "backend", `Backend ready at ${apiBaseUrl} after ${((Date.now() - backendStarted) / 1000).toFixed(1)} s`);
      resolve({ apiBaseUrl, apiToken });
    }

    function fail(error) {
      if (settled) return;
      settled = true;
      stopBackend();
      reject(error);
    }

    function handleOutput(chunk) {
      stdoutBuffer += chunk.toString();
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith(BACKEND_READY_PREFIX)) {
          backendOutput.stdoutLine(line);
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

    const backendOutput = createBackendOutput({
      write: (level, source, message, component) => applicationLog?.write(level, source, message, component),
      echo: (line) => {
        stderrBuffer = (stderrBuffer + line + "\n").slice(-65536);
        process.stderr.write(`[gofer-backend] ${line}\n`);
      },
    });
    child.stdout.on("data", handleOutput);
    // Decode across reads so a character split between chunks stays intact.
    child.stderr.setEncoding?.("utf8");
    child.stderr.on("data", (text) => backendOutput.stderr(text));
    child.on("error", (error) => {
      applicationLog?.write("error", "backend", `Could not start the backend (${error.code || error.name}): ${backendCommand.command}`);
      fail(error);
    });
    child.on("exit", (code, signal) => {
      backendOutput.end();
      const expected = isQuitting || expectedBackendStops.has(child);
      const ran = ((Date.now() - backendStarted) / 1000).toFixed(1);
      applicationLog?.write(expected ? "info" : "error", "backend", expected
        ? `Backend stopped after ${ran} s (${signal ? `signal ${signal}` : `exit code ${code}`})`
        : `Backend ${settled ? "stopped unexpectedly" : "exited before it was ready"} after ${ran} s (${signal ? `signal ${signal}` : `exit code ${code}`})`);
      if (backendProcess === child) {
        backendPathGrants.reset();
        loggedPathGrants.clear();
        backendProcess = undefined;
        closeBackendLogStream();
      }
      if (isQuitting || expectedBackendStops.has(child)) {
        return;
      }
      const detail = stderrBuffer.trim() || `Backend exited with code ${code ?? signal}.`;
      // The exit and its output are already in the log above.
      const exitError = Object.assign(new Error(detail), { backendExit: true });
      if (!settled) {
        fail(exitError);
        return;
      }

      showBackendCrash(exitError);
    });
  });
}

function getBackendCommand() {
  if (!isProduction) {
    return {
      command: "uv",
      args: ["run", "Raticode"],
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

function macosSpeechHelperPath() {
  if (app.isPackaged) return path.join(process.resourcesPath, "speech", "raticode-speech");
  // Development builds: `node scripts/build-macos-speech-helper.cjs` puts it here.
  return path.join(repoRoot, "dist", "macos-speech", "raticode-speech");
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
  backendPathGrants.reset();
  loggedPathGrants.clear();
  // Turns end with their backend, and so does their browser access.
  agentBrowserCapabilities.revokeAll();
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

// Main-process warnings and errors also go to the app log, which is the only place
// they can be read in a packaged app started from Finder or the Dock.
function captureMainConsole(log) {
  for (const method of ["warn", "error"]) {
    const original = console[method].bind(console);
    console[method] = (...args) => {
      original(...args);
      try { log.write(method, "desktop", util.format(...args)); } catch { /* logging must never throw. */ }
    };
  }
}

function logTrustedRegistryFailure(error) {
  writeBackendLog(`Could not read the list of trusted project folders (${error?.code || error?.name || "error"}). Folders you opened before may ask for access again.`, "warn");
}

function writeBackendLog(message, level = "info") {
  applicationLog?.write(level, "desktop", String(message).trimEnd());
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
  // Startup failures outside the backend process (a port, the PDF or browser
  // service, a bad ready message) were only ever shown in this window.
  if (!error?.backendExit) applicationLog?.write("error", "desktop", `${title}: ${error?.stack || error}`);
  const message = redactLog(error instanceof Error ? error.message : String(error));
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
  protocol.handle("raticode-media", request => mediaPreviews.handle(request));
  applicationLog = createAppLog(app.getPath("logs"));
  applicationLog.write("info", "desktop", `Starting Raticode ${app.getVersion()} on ${process.platform} ${process.arch}`);
  if (process.platform === "darwin") {
    // A copy built without its helper dictates with the offline model; say so up front.
    const helper = describeSpeechHelper(macosSpeechHelperPath());
    applicationLog.write(helper.startsWith("present") ? "info" : "warn", "desktop", `macOS speech helper ${helper}`);
  }
  process.on("uncaughtExceptionMonitor", (error) => applicationLog.emergency(error.stack || error.message));
  process.on("unhandledRejection", (error) => applicationLog.write("error", "desktop", `Unhandled rejection: ${error?.stack || util.inspect(error, { depth: 3 })}`));
  app.on("web-contents-created", (_event, contents) => {
    contents.once("destroyed", () => { mediaPreviews.closeOwner(contents.id); gitWatcher.closeOwner(contents.id); });
    contents.on("render-process-gone", () => { mediaPreviews.closeOwner(contents.id); gitWatcher.closeOwner(contents.id); });
    // A reload drops the page's listeners, so its repository watches go too.
    contents.on("did-navigate", () => gitWatcher.closeOwner(contents.id));
    contents.on("console-message", (details) => {
      if (contents !== mainWindow?.webContents && contents !== backendErrorWindow?.webContents) return;
      const { level, message } = details;
      // The main window's preload already reports uncaught errors with their stack.
      if (contents === mainWindow?.webContents && /^Uncaught /.test(String(message))) return;
      if (["warning", "error"].includes(level)) applicationLog.write(level === "error" ? "error" : "warn", "renderer", message);
    });
  });
  app.on("render-process-gone", (_event, contents, details) => {
    const own = contents === mainWindow?.webContents;
    // A clean exit is routine; a crashed browser tab matters less than the app window.
    const level = details.reason === "clean-exit" ? "debug" : own ? "error" : "warn";
    applicationLog.write(level, "renderer", `${own ? "The Raticode window's page" : "A page"} stopped: ${details.reason}, exit code ${details.exitCode}`);
  });
  app.on("child-process-gone", (_event, details) => applicationLog.write(details.reason === "clean-exit" ? "debug" : "error", "desktop", `Electron ${details.type}${details.name ? ` (${details.name})` : ""} process ended: ${details.reason}, exit code ${details.exitCode}`));
  captureMainConsole(applicationLog);
  Menu.setApplicationMenu(process.platform === "darwin" ? Menu.buildFromTemplate([
    { role: "appMenu" },
    { role: "editMenu" },
    { label: "View", submenu: [
      { role: "togglefullscreen" },
      { label: "Find in Page", accelerator: "Command+F", registerAccelerator: false,
        click: () => {
          const active = browsers.focusedSession();
          if (active) browsers.emitCommand(active, "find");
          else mainWindow?.webContents.send("gofer:browser-command", { action: "editor-find" });
        } },
    ] },
    { role: "windowMenu" },
  ]) : null);
  setupIpcHandlers();
  setupAutoUpdater();
  try {
    createBackgroundTray();
    const backendPort = await allocateBackendPort();
    backendReady = shellPathReady.then(async () => {
      reportPdfService = await startReportPdfService({ BrowserWindow, session });
      agentBrowserService = await startAgentBrowserService(agentBrowser.handle, agentBrowserCapabilities);
      // In-process Electron tests reach the service without exporting its token.
      if (process.env.GOFER_ELECTRON_TEST_HOOKS === "1") globalThis.__raticodeTestHooks = { agentBrowserService };
      removeLegacyAgentBrowserConnection(getGoferDataDir());
      return startBackend(backendPort);
    });
    // Handlers await readiness; neither Python nor terminal setup gates first paint.
    createWindow(process.env.GOFER_API_BASE_URL || process.env.VITE_API_BASE_URL || `http://127.0.0.1:${backendPort}`);
    void startTerminalEditorServer().catch(error => applicationLog.write("error", "terminal", error.message));
    const backend = await backendReady;
    activeApiBaseUrl = backend.apiBaseUrl;
    activeUiApiToken = backend.apiToken;
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

});

app.on("activate", showMainWindow);

function showMainWindow() {
  if ((!mainWindow || mainWindow.isDestroyed()) && lastWindowArgs && !isQuitting) createWindow(...lastWindowArgs);
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!shownWindows.has(mainWindow)) {
    mainWindow.maximize();
    shownWindows.add(mainWindow);
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createBackgroundTray() {
  const iconPath = path.join(__dirname, app.isPackaged ? "../dist/icon.png" : "../public/icon.png");
  backgroundTray = new Tray(nativeImage.createFromPath(iconPath).resize({ width: 20, height: 20 }));
  backgroundTray.setToolTip("Raticode · running in background");
  backgroundTray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Raticode", click: showMainWindow },
    { type: "separator" },
    { label: "Quit Raticode and stop background work", click: () => app.quit() },
  ]));
  backgroundTray.on("click", showMainWindow);
  backgroundTray.on("double-click", showMainWindow);
}

app.on("second-instance", showMainWindow);

let lifecycleApproved = false;
let lifecyclePending = null;
let lifecycleOwnerId = 0;
function completeLifecycle(event, { requestId, approved, ready } = {}) {
  if (requestId === "renderer-ready") { lifecycleOwnerId = ready === true ? event.sender.id : 0; return true; }
  if (!lifecyclePending || lifecyclePending.requestId !== requestId || lifecyclePending.ownerId !== event.sender.id) return false;
  lifecyclePending.resolve(approved === true);
  lifecyclePending = null;
  return true;
}
function prepareLifecycle(reason) {
  if (!mainWindow || mainWindow.isDestroyed() || isSmokeTest || lifecycleOwnerId !== mainWindow.webContents.id) return Promise.resolve(true);
  if (lifecyclePending) return lifecyclePending.promise;
  const requestId = crypto.randomUUID();
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  lifecyclePending = { requestId, ownerId: mainWindow.webContents.id, resolve, promise };
  showMainWindow();
  mainWindow.webContents.send("gofer:prepare-lifecycle", { requestId, reason });
  // A crashed/unresponsive renderer must not silently approve losing drafts.
  const owner = mainWindow.webContents;
  const cancel = () => {
    if (lifecyclePending?.requestId === requestId) { lifecyclePending.resolve(false); lifecyclePending = null; }
  };
  owner.once("destroyed", cancel);
  return promise.finally(() => owner.removeListener("destroyed", cancel));
}
app.on("before-quit", (event) => {
  if (!lifecycleApproved && mainWindow && !mainWindow.isDestroyed() && !isSmokeTest) {
    event.preventDefault();
    void prepareLifecycle("quit Raticode").then(approved => {
      if (approved) { lifecycleApproved = true; app.quit(); }
    });
    return;
  }
  isQuitting = true;
  if (!archivesDrained) {
    event.preventDefault();
    void conversationArchives.close().finally(() => { archivesDrained = true; app.quit(); });
    return;
  }
  browsers.closeAll();
  geckoWindow.close();
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
  // The desktop host owns backend services until an explicit Quit.
  if (isQuitting || isSmokeTest) app.quit();
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
    completeLifecycle,
    readClipboard: () => clipboard.readText(),
    editFocused: (event, { action } = {}) => {
      const method = { undo: "undo", redo: "redo", cut: "cut", copy: "copy", paste: "paste", selectAll: "selectAll" }[action];
      if (!method) throw new Error("Unknown editing action.");
      event.sender[method]();
      return true;
    },
    developerInfo,
    developerAction,
    developerLog,
    featurePreview,
    setFeaturePreview,
    rendererLog,
    remSettings,
    configureRem,
    archiveRem,
    forgetRem,
    checkForUpdates,
    copyPath,
    movePath,
    browserAction: (event, options) => browsers.browserAction(event, options),
    browserLibrary: (event, options) => browsers.browserLibrary(event, options),
    browserOwnerZoom: (event) => browsers.ownerZoom(event),
    gecko: (_event, options = {}) => geckoWindow.handle(String(options?.action || ""), options),
    browserPolicy: (event, options) => browsers.setPolicy(event, options?.policy),
    createBrowser: (event, options) => browsers.createBrowser(event, options),
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
    gitBranches,
    gitFileBaseline,
    gitHistory,
    gitWorktrees,
    gitWatch,
    gitUnwatch,
    gitWorktreeAdd,
    gitWorktreeRemove,
    grantPath,
    grantDroppedPath,
    grantUserPath,
    getUpdateState,
    apiSession: async () => {
      let backend;
      try { backend = await backendReady; }
      catch (error) {
        // The startup failure is logged once when it happens; every poll asks again.
        throw Object.assign(new Error(error?.message || "The backend did not start."), { expected: true });
      }
      return { apiBaseUrl: backend.apiBaseUrl, apiToken: backend.apiToken };
    },
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
    readAttachmentFile,
    openMediaPreview,
    closeMediaPreview: (event, options = {}) => mediaPreviews.close(options.id, event.sender.id),
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
        // Expected answers, such as a terminal that already exited, are routine.
        applicationLog?.write(error?.expected ? "debug" : "error", "ipc", `${channel}: ${error.message}`);
        throw error;
      }
    },
  });
}

// Firefox mode's headless Firefox, in a profile only Raticode uses.
const firefoxEngine = createFirefoxEngine({
  dataDir: app.getPath("userData"),
  stagingDir: path.join(app.getPath("userData"), "browser", "firefox-downloads"),
  log: (level, message) => applicationLog?.write(level, "browser", message),
});

// Chrome mode's headless Google Chrome, in a profile only Raticode uses.
const chromeEngine = createChromiumEngine({
  browser: CHROME,
  dataDir: app.getPath("userData"),
  stagingDir: path.join(app.getPath("userData"), "browser", "chrome-downloads"),
  log: (level, message) => applicationLog?.write(level, "browser", message),
});

// Edge mode's headless Microsoft Edge, on the same engine as Chrome mode, in
// a profile only Raticode uses.
const edgeEngine = createChromiumEngine({
  browser: EDGE,
  dataDir: app.getPath("userData"),
  stagingDir: path.join(app.getPath("userData"), "browser", "edge-downloads"),
  log: (level, message) => applicationLog?.write(level, "browser", message),
});

const browsers = createBrowserSessions({
  app,
  Menu,
  clipboard,
  dialog,
  shell,
  session,
  webContents,
  getMainWindow: () => (mainWindow && !mainWindow.isDestroyed() ? mainWindow : null),
  resolvePath: (targetPath, options) => resolveExactPath(targetPath, options),
  store: createBrowserStore(path.join(app.getPath("userData"), "browser")),
  log: (level, message) => applicationLog?.write(level, "browser", message),
  openWith: createOpenWith(),
  quarantineDir: path.join(app.getPath("userData"), "browser", "quarantine"),
  firefox: firefoxEngine,
  chrome: chromeEngine,
  chromium: { edge: edgeEngine },
  nativeImage,
});
firefoxEngine.events.on("exit", () => browsers.firefoxStopped());
// Firefox tabs come back once the user closes the window they signed in with.
firefoxEngine.events.on("sign-in-window-closed", () => void browsers.reviveFirefoxTabs());
chromeEngine.events.on("exit", () => browsers.streamStopped("chrome"));
chromeEngine.events.on("sign-in-window-closed", () => void browsers.reviveStreamTabs("chrome"));
edgeEngine.events.on("exit", () => browsers.streamStopped("edge"));
edgeEngine.events.on("sign-in-window-closed", () => void browsers.reviveStreamTabs("edge"));

// Gecko: real Firefox, its window placed inside Raticode's.
const geckoWindow = createGeckoWindow({
  dataDir: app.getPath("userData"),
  getWindow: () => (mainWindow && !mainWindow.isDestroyed() ? mainWindow : null),
  log: (level, message) => applicationLog?.write(level, "browser", message),
  x11Switch: app.commandLine.getSwitchValue("ozone-platform") === "x11",
  ask: async ({ message, detail }) => (await dialog.showMessageBox(mainWindow, { type: "question", buttons: ["Import", "Cancel"], defaultId: 1, cancelId: 1, message, detail })).response === 0,
  onState: (state) => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("gofer:gecko-state", state); },
  relaunch: async () => {
    if (process.env.VITE_DEV_SERVER_URL) return { manual: true };
    if (!await prepareLifecycle("restart Raticode")) return { cancelled: true };
    lifecycleApproved = true;
    app.relaunch({ args: [...process.argv.slice(1).filter((arg) => arg !== X11_SWITCH), X11_SWITCH] });
    app.quit();
    return { restarting: true };
  },
});

const agentBrowserCapabilities = createCapabilityRegistry();
const agentBrowser = createAgentBrowserController({
  openTab: (details) => browsers.openAgentTab(details),
  agentTabs: (agent) => browsers.agentTabs(agent),
  allAgentTabs: () => browsers.allAgentTabs(),
  closeTab: (tab) => browsers.closeAgentTab(tab),
  normalizeUrl: (value) => browsers.normalizeUrl(value),
  policy: () => browsers.policy(),
  confirm: (tab, details) => browsers.confirmAgent(tab, details),
  record: (tab, entry) => browsers.recordAgentActivity(tab, entry),
});
browsers.setViewportListener((tab) => void agentBrowser.viewportChanged(tab));
setInterval(() => agentBrowser.sweep(), 60 * 1000).unref();

app.on("certificate-error", (event, contents, url, error, certificate, callback, isMainFrame) => {
  browsers.handleCertificateError(event, contents, url, error, certificate, callback, isMainFrame);
});
app.on("login", (event, contents, details, authInfo, callback) => {
  browsers.handleLogin(event, contents, details, authInfo, callback);
});
app.on("select-client-certificate", (event, contents, url, list, callback) => {
  browsers.handleSelectClientCertificate(event, contents, url, list, callback);
});

async function createTerminal(event, options = {}) {
  const requestedCwd = typeof options.cwd === "string" ? options.cwd.trim() : "";
  const cwd = requestedCwd
    ? resolveExactPath(requestedCwd, { grantId: options.grantId, mustExist: true })
    : getGoferDataDir();
  if (!(await fs.promises.stat(cwd)).isDirectory()) {
    throw new Error(`Terminal directory is not a folder: ${cwd}`);
  }

  const [shell, editorCommand] = await Promise.all([terminalShell(options.shell), terminalEditorCommand()]);
  if (event.sender.isDestroyed()) throw new Error("Terminal owner was closed.");
  const id = crypto.randomUUID();
  const editorToken = crypto.randomBytes(32).toString("hex");
  const terminal = pty.spawn(shell.command, shell.args, {
    cols: terminalDimension(options.cols, 80, 2, 500),
    cwd,
    env: {
      ...process.env,
      ...(shell.env ?? {}),
      COLORTERM: "truecolor",
      GIT_EDITOR: editorCommand,
      TERM: "xterm-256color",
      RATICODE_EDITOR_CWD: cwd,
      RATICODE_EDITOR_ENDPOINT: terminalEditorEndpoint,
      RATICODE_EDITOR_RUNTIME: process.execPath,
      RATICODE_EDITOR_SCRIPT: path.join(__dirname, "terminal-editor.cjs"),
      RATICODE_EDITOR_TOKEN: editorToken,
      RATICODE_TERMINAL_ID: id,
      VISUAL: editorCommand,
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

let terminalEditorCommandPromise;
function terminalEditorCommand() {
  terminalEditorCommandPromise ??= terminalEditorLauncherPath().then(launcherPath => (
    process.platform === "win32" ? `"${launcherPath}"` : shellQuote(launcherPath)
  )).catch(error => {
    terminalEditorCommandPromise = undefined;
    throw error;
  });
  return terminalEditorCommandPromise;
}

async function terminalEditorLauncherPath() {
  const directory = path.join(app.getPath("userData"), "terminal-editor");
  await fs.promises.mkdir(directory, { recursive: true });
  if (process.platform === "win32") {
    const launcherPath = path.join(directory, "raticode-editor.cmd");
    await fs.promises.writeFile(launcherPath, [
      "@echo off",
      "set ELECTRON_RUN_AS_NODE=1",
      '"%RATICODE_EDITOR_RUNTIME%" "%RATICODE_EDITOR_SCRIPT%" %*',
      "",
    ].join("\r\n"), "utf8");
    return launcherPath;
  }
  const launcherPath = path.join(directory, "raticode-editor");
  await fs.promises.writeFile(launcherPath, [
    "#!/bin/sh",
    "export ELECTRON_RUN_AS_NODE=1",
    'exec "$RATICODE_EDITOR_RUNTIME" "$RATICODE_EDITOR_SCRIPT" "$@"',
    "",
  ].join("\n"), { encoding: "utf8", mode: 0o700 });
  await fs.promises.chmod(launcherPath, 0o700);
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
    // Resizes and closes routinely arrive after a shell exits.
    throw Object.assign(new Error("Terminal session was not found."), { expected: true });
  }
  return session;
}

async function terminalShell(choice = "system") {
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
  const selected = await unixTerminalShell({ choice, directory: path.join(app.getPath("userData"), "shell-integration") });
  if (selected.label !== "bash") return selected;
  const integrationPath = await bashIntegrationPath();
  if (integrationPath) {
    return {
      args: ["--rcfile", integrationPath, "-i"],
      command: selected.command,
      label: "bash",
    };
  }
  return {
    args: ["-il"],
    command: selected.command,
    env: {
      PROMPT_COMMAND: [
        "printf '\\033]633;P;Cwd=%s\\007' \"$PWD\"",
        process.env.PROMPT_COMMAND,
      ].filter(Boolean).join(";"),
    },
    label: "bash",
  };
}

let bashIntegrationPromise;
function bashIntegrationPath() {
  bashIntegrationPromise ??= writeBashIntegration().then(result => {
    if (!result) bashIntegrationPromise = undefined;
    return result;
  });
  return bashIntegrationPromise;
}

async function writeBashIntegration() {
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
    await fs.promises.mkdir(integrationDirectory, { recursive: true });
    await fs.promises.writeFile(integrationPath, source, { encoding: "utf8", mode: 0o600 });
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

// The Arch package (raticode-bin) writes "pacman" to resources/package-type. Pacman owns
// every file of that copy, so Raticode must never replace them: updates come from the
// package manager instead. The .deb and .rpm builds write "deb" and "rpm" and keep
// updating themselves through electron-updater.
let installedPackageManager;
function systemPackageManager() {
  if (installedPackageManager === undefined) {
    installedPackageManager = "";
    if (process.platform === "linux" && app.isPackaged) {
      try {
        const value = fs.readFileSync(path.join(process.resourcesPath, "package-type"), "utf8").trim();
        if (value === "pacman") installedPackageManager = value;
      } catch {
        // AppImage, unpacked and source builds have no marker.
      }
    }
  }
  return installedPackageManager;
}

function supportsAutoUpdates() {
  return app.isPackaged && !isSmokeTest && !systemPackageManager()
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
      setImmediate(() => { void installDownloadedUpdate().catch(error => { logUpdaterError(error); setUpdateState({ error: error.message }); }); });
    }
  });
  autoUpdater.on("error", (error) => {
    installUpdateAfterDownload = false;
    if (isNoPublishedVersionsError(error)) {
      setNoReleasesUpdateState();
      return;
    }
    logUpdaterError(error);
    setUpdateState({
      checking: false,
      downloading: false,
      error: error instanceof Error ? error.message : String(error),
    });
  });
}

function logUpdaterError(error) {
  applicationLog?.write("warn", "updater", error instanceof Error ? (error.stack || error.message) : String(error));
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
    logUpdaterError(error);
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
    if (process.platform === "darwin" && app.isPackaged && !isSmokeTest) {
      const installerUrl = updateState.info?.installerUrl;
      if (!installerUrl) throw new Error("No compatible Mac installer was found. Check for updates again or open the release page.");
      await shell.openExternal(installerUrl);
    } else {
      await openUpdateRelease();
    }
    return getUpdateState();
  }

  installUpdateAfterDownload = true;
  setUpdateState({ downloading: true, error: "" });
  try {
    await autoUpdater.downloadUpdate();
  } catch (error) {
    installUpdateAfterDownload = false;
    setUpdateState({ downloading: false, error: error instanceof Error ? error.message : String(error) });
    throw error;
  }
  return getUpdateState();
}

async function installDownloadedUpdate() {
  if (!supportsAutoUpdates()) {
    return getUpdateState();
  }
  if (!await prepareLifecycle("install the update")) return getUpdateState();
  lifecycleApproved = true;
  isQuitting = true;
  stopBackend();
  autoUpdater.quitAndInstall(false, true);
  return getUpdateState();
}

async function openUpdateRelease() {
  await shell.openExternal(`${RELEASES_URL}/latest`);
  return { opened: true };
}

function getUpdateState() {
  return {
    ...updateState,
    currentVersion: app.getVersion(),
    platform: process.platform,
    arch: process.arch,
    supported: supportsAutoUpdates(),
    managedBy: systemPackageManager(),
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

  let response;
  try {
    response = await fetch(`${RELEASES_URL}/latest/download/${updateMetadataFileName()}`, {
      headers: { "User-Agent": `Raticode/${currentVersion}` },
    });
  } catch (error) {
    const reason = error?.cause?.message || error?.message || String(error);
    throw new Error(`Could not reach GitHub to check for updates: ${reason}`);
  }
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
    throw new Error(updateCheckHttpError(response));
  }
  const release = parseUpdateMetadata(await response.text());
  if (!release.version) {
    throw new Error("GitHub returned update information without a version.");
  }
  return {
    available: compareVersions(release.version, normalizeVersion(currentVersion)) > 0,
    checking: false,
    downloading: false,
    downloaded: false,
    error: "",
    info: {
      version: release.version,
      releaseName: release.releaseName || `v${release.version}`,
      releaseDate: release.releaseDate,
      installerUrl: macInstallerUrl(release),
    },
    progress: null,
  };
}

function updateMetadataFileName() {
  if (process.platform === "darwin") return "latest-mac.yml";
  if (process.platform === "linux") return "latest-linux.yml";
  return "latest.yml";
}

// electron-builder writes flat metadata; read only the fields the manual check needs.
function parseUpdateMetadata(text) {
  const field = (name) => {
    const match = String(text).match(new RegExp(`^${name}:[ \\t]*['"]?([^'"\\r\\n]*)['"]?[ \\t]*$`, "m"));
    return match ? match[1].trim() : "";
  };
  const version = field("version");
  return {
    version: /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version) ? version : "",
    releaseName: field("releaseName"),
    releaseDate: field("releaseDate"),
    files: Array.from(String(text).matchAll(/^[ \t]*-[ \t]+url:[ \t]*['"]?([^'"\r\n]+?)['"]?[ \t]*$/gm), match => match[1]),
  };
}

function updateCheckHttpError(response) {
  const header = (name) => response.headers?.get?.(name) || "";
  if (response.status === 403 || response.status === 429) {
    const resetSeconds = Number(header("x-ratelimit-reset"));
    if (header("x-ratelimit-remaining") === "0" && resetSeconds > 0) {
      const resetTime = new Date(resetSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      return `GitHub's rate limit for this network was reached. Try again after ${resetTime}.`;
    }
    const retryAfter = Number(header("retry-after"));
    if (retryAfter > 0) {
      return `GitHub is limiting update checks from this network. Try again in ${Math.ceil(retryAfter / 60)} minute${retryAfter > 60 ? "s" : ""}.`;
    }
    return `GitHub refused the update check (HTTP ${response.status}). A VPN, proxy or firewall may be blocking it, or GitHub may be limiting requests from this network.`;
  }
  return `GitHub returned HTTP ${response.status} while checking for updates.`;
}

function macInstallerUrl(release) {
  if (process.platform !== "darwin" || !release.version) return "";
  const name = `Raticode-${release.version}-${process.arch}.dmg`;
  if (!Array.isArray(release.files) || !release.files.includes(name)) return "";
  return `${RELEASES_URL}/download/v${release.version}/${name}`;
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
  const projectRoot = discoveredRoot;
  const handle = pathHandle(projectRoot);
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
  let handle;
  try {
    // The desktop user may select any OS-accessible project, including sibling worktrees.
    handle = getIpcSecurity().trustPath(options.targetPath);
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
  const destinationPath = options.replace === true
    ? path.join(resolveExactPath(path.dirname(options.destinationPath), { grantId: options.destinationGrantId, mustExist: true }), path.basename(options.destinationPath))
    : resolveExactPath(options.destinationPath, { grantId: options.destinationGrantId });
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`Path does not exist: ${sourcePath}`);
  }
  if (fs.existsSync(destinationPath) && options.replace !== true) {
    throw new Error(`Destination already exists: ${destinationPath}`);
  }
  await safeFiles.copyPath(sourcePath, destinationPath, {
    replace: options.replace === true,
    authorizeSource: (target) => resolveExactPath(target, { grantId: options.sourceGrantId, mustExist: true }),
    authorizeDestination: (target) => resolveExactPath(target, { grantId: options.destinationGrantId }),
  });
  return pathHandle(destinationPath);
}

async function movePath(_event, options = {}) {
  if (!options.sourcePath || typeof options.sourcePath !== "string" || !options.destinationPath || typeof options.destinationPath !== "string") throw new Error("Source and destination paths are required.");
  const sourcePath = resolveExactPath(options.sourcePath, { grantId: options.sourceGrantId, mustExist: true });
  const destinationPath = options.replace === true
    ? path.join(resolveExactPath(path.dirname(options.destinationPath), { grantId: options.destinationGrantId, mustExist: true }), path.basename(options.destinationPath))
    : resolveExactPath(options.destinationPath, { grantId: options.destinationGrantId });
  await safeFiles.movePath(sourcePath, destinationPath, {
    replace: options.replace === true,
    authorizeSource: target => resolveExactPath(target, { grantId: options.sourceGrantId, mustExist: true }),
    authorizeDestination: target => resolveExactPath(target, { grantId: options.destinationGrantId }),
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
  let content;
  try {
    content = await safeFiles.readFile(targetPath, { maxBytes: 2 * 1024 * 1024 });
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) {
      return { content: null, missing: true, path: targetPath };
    }
    if (error.code === "ERR_FILE_TOO_LARGE") throw new Error("File is too large to edit in Raticode.");
    throw error;
  }
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
  const mimeType = imageMimeType(targetPath);
  if (!mimeType) throw new Error("This file type does not have an image preview.");
  const content = await safeFiles.readFile(targetPath, { maxBytes: 25 * 1024 * 1024 }).catch(error => {
    if (error.code === "ERR_FILE_TOO_LARGE") throw new Error("File is too large to preview in Raticode.");
    throw error;
  });
  return {
    dataUrl: `data:${mimeType};base64,${content.toString("base64")}`,
    ...pathHandle(targetPath),
  };
}

// Chats keep showing pictures and videos attached long ago, so a file that has since
// been moved or deleted is an answer, not a failed IPC call that logs a stack trace.
function openMediaPreview(event, options = {}) {
  try {
    const targetPath = resolveExactPath(options.targetPath, { grantId: options.grantId, mustExist: true });
    return mediaPreviews.open(targetPath, event.sender.id);
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) return { missing: true };
    throw error;
  }
}

// Reads any file for a chat attachment. The limit matches the chat's per-file limit.
async function readAttachmentFile(_event, options = {}) {
  if (!options.targetPath || typeof options.targetPath !== "string") {
    throw new Error("A path is required.");
  }
  const targetPath = resolveExactPath(options.targetPath, {
    grantId: options.grantId,
    mustExist: true,
  });
  const name = path.basename(targetPath);
  const content = await safeFiles.readFile(targetPath, { maxBytes: 20 * 1024 * 1024 }).catch(error => {
    if (error.code === "ERR_FILE_TOO_LARGE") throw new Error(`${name} is larger than 20 MB.`);
    throw error;
  });
  const type = imageMimeType(targetPath)
    || (path.extname(targetPath).toLowerCase() === ".pdf" ? "application/pdf" : "")
    || (content.subarray(0, 8192).includes(0) ? "application/octet-stream" : "text/plain");
  return { name, type, data: content.toString("base64"), ...pathHandle(targetPath) };
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

  if (!await prepareLifecycle("change the data directory")) return { dataDir: getGoferDataDir(), cancelled: true };
  selectedDataDir = resolveExactPath(options.dataDir, {
    grantId: options.grantId,
    mustExist: true,
  });
  process.env.GOFER_DATA_DIR = selectedDataDir;
  fs.mkdirSync(selectedDataDir, { recursive: true });
  getIpcSecurity().grantPath(selectedDataDir);
  writePersistedDataDir(selectedDataDir);
  await replaceBackendWindow();
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
    archiveFolder: remSettings().archiveFolder, packaged: app.isPackaged,
    backendLogLevel: process.env.GOFER_LOG_LEVEL || "debug",
    externalBackend: Boolean(process.env.GOFER_API_BASE_URL || process.env.VITE_API_BASE_URL) };
}
// The Developer tab's live log. Reads come from memory; `history` reads the end of
// the log files written before this session once.
async function developerLog(_event, { after = 0, limit = 1000, history = false } = {}) {
  if (!applicationLog) return { entries: [], cursor: 0, latest: 0, missed: 0, counts: {}, history: null };
  const live = applicationLog.read({ after, limit });
  return { ...live, history: history ? await applicationLog.history() : null };
}
async function developerAction(_event, { action } = {}) {
  if (action === "logs") return shell.openPath(app.getPath("logs"));
  if (action === "data") return shell.openPath(getGoferDataDir());
  if (action === "user-data") return shell.openPath(app.getPath("userData"));
  if (action === "devtools") { mainWindow?.webContents.openDevTools({ mode: "detach" }); return; }
  if (action === "restart") return restartBackend();
  throw new Error("Unknown developer action.");
}
function featurePreviewFile() {
  return path.join(app.getPath("userData"), "feature-preview.json");
}
function featureChoices() {
  return readFeatureChoices(featurePreviewFile());
}
function featurePreview() {
  return featureStates(featureChoices(), process.env);
}
// The backend reads these features at startup, so a change restarts it.
async function setFeaturePreview(_event, { id, enabled } = {}) {
  if (!Object.hasOwn(BACKEND_FEATURES, id) || typeof enabled !== "boolean") throw new Error("Unknown feature preview.");
  if (featurePreview()[id] === enabled) return { features: featurePreview() };
  if (!await prepareLifecycle("change a feature preview")) return { cancelled: true, features: featurePreview() };
  writeFeatureChoice(featurePreviewFile(), id, enabled);
  await replaceBackendWindow();
  return { features: featurePreview() };
}
// Aborted requests, cancelled editor work and the ResizeObserver notice are routine.
const ROUTINE_RENDERER_ERRORS = /^(?:AbortError\b|Canceled\b|Error: Canceled\b|ResizeObserver loop)|\bThe (?:operation|user) (?:was )?aborted\b/;
function rendererLog(_event, { message } = {}) {
  const text = String(message || "");
  applicationLog?.write(ROUTINE_RENDERER_ERRORS.test(text) ? "debug" : "error", "renderer", text);
}
function remSettings() {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(app.getPath("userData"), "rem-settings.json"), "utf8"));
    return { ...config, reportFormat: reportOutputFormat(config) };
  }
  catch (error) { if (error.code !== "ENOENT") throw error; return { archiveFolder: "", secondBrainEnabled: false, secondBrainRoot: "", secondBrainFormat: "md", secondBrainTheme: "auto" }; }
}
async function configureRem(_event, options = {}) {
  const config = remSettings();
  if (options.key === "reset") Object.assign(config, { reportFormat: "md", reportThemes: normalizeReportThemes(null), archiveFolder: "", secondBrainEnabled: false, secondBrainRoot: "", secondBrainFormat: "md", secondBrainTheme: "auto" });
  else if (["archiveFolder", "secondBrainRoot"].includes(options.key)) {
    const folder = options.value ? getIpcSecurity().resolveDesktopPath(options.value, { mustExist: true }) : "";
    if (folder && !(await fs.promises.stat(folder)).isDirectory()) throw new Error("Choose a folder.");
    config[options.key] = folder;
  } else if (options.key === "secondBrainEnabled") {
    if (options.value && !config.secondBrainRoot) throw new Error("Choose a Second Brain folder first.");
    config.secondBrainEnabled = options.value === true;
  } else if (options.key === "reportFormat" && REPORT_FORMATS.includes(options.value)) config.reportFormat = options.value;
  else if (options.key === "secondBrainFormat" && ["md", "html"].includes(options.value)) config.secondBrainFormat = options.value;
  else if (options.key === "reportThemes") config.reportThemes = normalizeReportThemes(options.value);
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

async function forgetRem(_event, { threadIds } = {}) {
  const config = remSettings();
  if (!config.archiveFolder) return { skipped: true };
  return conversationArchives.forget(config.archiveFolder, threadIds);
}

async function gitRepoAction(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  try { return await gitRepositoryAction(projectRoot, options.action, options.value, { authorizeTarget: (target) => resolveGitProjectDirectory({ projectRoot: target, grantId: getIpcSecurity().grantForPath(target) }), managedFolders: ["rem-worktrees", "organization-worktrees", "workspaces"].map(folder => path.join(getGoferDataDir(), folder)) }); }
  catch (error) {
    const reason = String(error.stderr || error.message).replace(/^Command failed:[^\n]*\n?/, "").trim();
    // Shown to the user, but returned rather than thrown, so the IPC wrapper never logged it.
    applicationLog?.write("warn", "git", `Git ${options.action || "action"} failed in ${projectRoot}: ${reason.split("\n")[0].slice(0, 300)}`);
    return { ...await readGitStatus(projectRoot), error: reason };
  }
  // Commits, branch deletes and cleanups change what other open views show. Previews only read.
  finally { if (!/(^|-)preview$|^commit-diff$|^stash-list$|^integration-targets$/.test(String(options.action))) notifyGitChanged(projectRoot, ["index", "refs", "worktrees"]); }
}

async function gitFileAction(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  return changeGitFile(projectRoot, options.relativePath, options.action, { trashItem: (target) => shell.trashItem(target) });
}

async function gitSwitchBranch(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  return switchGitBranch(projectRoot, options.branch);
}

async function gitBranches(_event, options = {}) {
  try {
    const projectRoot = await resolveGitProjectDirectory(options);
    return await readGitBranches(projectRoot, { force: options.force === true });
  } catch (error) {
    if (["ENOENT", "ENOTDIR"].includes(error.code || error.cause?.code)) return { active: false, missing: true };
    throw error;
  }
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
    return await readGitHistory(projectRoot, { ref: options.ref, refs: options.refs, limit: options.limit });
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

async function gitWatch(event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  const sender = event.sender;
  try {
    const result = await gitWatcher.watch(projectRoot, {
      ownerId: sender.id,
      onChange: (kinds, watchId) => {
        // Branch lists are cached for a few seconds. Agents and terminals change
        // them outside this process, so the next read must see the new state.
        if (kinds.includes("refs") || kinds.includes("worktrees")) invalidateGitReads();
        if (!sender.isDestroyed()) sender.send("gofer:git-changed", { watchId, kinds });
      },
    });
    return { active: true, ...result };
  } catch {
    return { active: false };
  }
}

function gitUnwatch(event, options = {}) {
  gitWatcher.unwatch(typeof options.watchId === "string" ? options.watchId : "", event.sender.id);
  return { ok: true };
}

function notifyGitChanged(projectRoot, kinds) {
  void gitWatcher.notify(projectRoot, kinds).catch(() => {});
}

async function gitWorktreeAdd(_event, options = {}) {
  const projectRoot = await resolveGitProjectDirectory(options);
  if (typeof options.targetPath !== "string" || !options.targetPath.trim()) throw new Error("A worktree folder is required.");
  if (typeof options.branch !== "string" || !options.branch.trim()) throw new Error("A branch is required.");
  const targetPath = resolveExactPath(options.targetPath, { grantId: options.targetGrantId, mustExist: true });
  const targetStat = await fs.promises.stat(targetPath);
  if (!targetStat.isDirectory()) throw new Error("The worktree target must be a folder.");
  const result = await addGitWorktree(projectRoot, targetPath, options.branch.trim(), { createBranch: options.createBranch === true, startPoint: options.startPoint });
  notifyGitChanged(projectRoot, ["refs", "worktrees"]);
  const handle = pathHandle(targetPath);
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
  const result = await removeGitWorktree(projectRoot, targetPath, { force: options.force === true });
  if (!result?.requiresForce) notifyGitChanged(projectRoot, ["refs", "worktrees"]);
  return result;
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
  if (options.save === true) {
    const result = await dialog.showSaveDialog(parentWindow, { defaultPath, title: "Save export" });
    if (result.canceled || !result.filePath) return null;
    const root = path.dirname(result.filePath);
    const trusted = getIpcSecurity().trustPath(root);
    return { path: result.filePath, grantId: trusted.grantId };
  }
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
  } catch (error) {
    writeBackendLog(`Could not move the recent projects list from the old settings (${error?.code || error?.name || "error"}). Reopen those projects from File > Open Folder.`, "error");
  }
}

function readTrustedRoots() {
  let roots = [];
  try { roots = trustedProjectStore().read().roots; }
  catch (error) { logTrustedRegistryFailure(error); }
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

async function registerBackendPathGrant(handle) {
  await backendReady;
  if (getIpcSecurity().isUserGrant(handle?.grantId)) {
    throw new Error("User file navigation does not grant agent access.");
  }
  return backendPathGrants.register(handle || {}, () => sendBackendPathGrant(handle));
}

async function sendBackendPathGrant(handle) {
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
    if (payload?.grantId !== handle.grantId || typeof payload?.path !== "string" || payload.path !== handle.path) {
      throw new Error("Registration acknowledgment does not match");
    }
    if (!loggedPathGrants.has(handle.path)) {
      loggedPathGrants.add(handle.path);
      writeBackendLog(`Gave the backend access to ${handle.path} (renewed quietly every 14 minutes)`, "debug");
    }
  } catch (error) {
    if (error?.name === "TimeoutError" || error?.name === "AbortError") reason = "timeout";
    // Do not log headers, grant IDs, response bodies, or raw network errors.
    writeBackendLog(`Could not give the backend access to ${handle?.path || "a folder"}: ${reason.replace(/-/g, " ")}${status ? ` (HTTP ${status})` : ""} after ${Date.now() - startedAt} ms`, "warn");
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
  if (!await prepareLifecycle("restart the backend")) return { cancelled: true };
  return replaceBackendWindow();
}

async function replaceBackendWindow() {
  stopBackend();
  try {
    removeLegacyAgentBrowserConnection(getGoferDataDir());
    backendReady = startBackend();
    const backend = await backendReady;
    activeApiBaseUrl = backend.apiBaseUrl;
    activeUiApiToken = backend.apiToken;
    if (backendErrorWindow && !backendErrorWindow.isDestroyed()) {
      backendErrorWindow.close();
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.destroy();
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
