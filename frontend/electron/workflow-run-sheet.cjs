// The Run sheet a workflow shortcut opens on top of whatever app is in front: the
// workflow's name, its text input filled from the clipboard, and a warning about
// instructions the user didn't write. Enter runs it; Escape closes it. One sheet at a
// time: another shortcut's press replaces it.
//
// The sheet's page only shows the state it is sent (textContent and a textarea value,
// never HTML) and sends back "run" with the text or "cancel". Requests from any other
// page are ignored.

const SHEET_SIZE = { width: 520, height: 400 };
const ACTION_CHANNEL = "gofer:workflow-run-sheet-action";
const STATE_CHANNEL = "gofer:workflow-run-sheet-state";

function placement(screen, size = SHEET_SIZE) {
  try {
    const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
    return {
      x: Math.round(area.x + (area.width - size.width) / 2),
      y: Math.round(area.y + Math.max(24, (area.height - size.height) / 3)),
    };
  } catch {
    return {};
  }
}

/**
 * deps: BrowserWindow, screen, ipcMain, pagePath, preloadPath. Returns
 * { open(state, handlers), update(partial), close(), isOpen(id), focus() }.
 */
function createRunSheet({ BrowserWindow, screen, ipcMain, pagePath, preloadPath, quitting = () => false }) {
  let window = null;
  let state = null;
  let handlers = null;

  function send() {
    if (!window || window.isDestroyed() || window.webContents.isLoading()) return;
    window.webContents.send(STATE_CHANNEL, state);
  }

  ipcMain.on("gofer:workflow-run-sheet-action", (event, request = {}) => {
    if (!window || window.isDestroyed() || event.sender !== window.webContents || !handlers) return;
    if (request?.action === "run" && !state?.busy) {
      const text = typeof request.text === "string" ? request.text : "";
      void Promise.resolve(handlers.onRun(text)).catch(() => {});
    } else if (request?.action === "cancel") {
      handlers.onCancel();
    }
  });

  function open(nextState, nextHandlers) {
    if (quitting()) return;
    state = { ...nextState };
    handlers = nextHandlers;
    if (!window || window.isDestroyed()) {
      window = new BrowserWindow({
        ...SHEET_SIZE,
        ...placement(screen),
        show: false,
        resizable: false,
        minimizable: false,
        maximizable: false,
        fullscreenable: false,
        alwaysOnTop: true,
        skipTaskbar: false,
        autoHideMenuBar: true,
        title: "Run a workflow – Raticode",
        backgroundColor: "#18181b",
        webPreferences: {
          preload: preloadPath,
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          spellcheck: true,
        },
      });
      window.setMenuBarVisibility?.(false);
      window.setAlwaysOnTop(true, "floating");
      window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      window.webContents.on("will-navigate", (event) => event.preventDefault());
      const own = window;
      own.on("closed", () => {
        if (window !== own) return;
        window = null;
        state = null;
        handlers = null;
      });
      own.on("focus", () => {
        if (window === own) handlers?.onFocus?.();
      });
      own.once("ready-to-show", () => {
        if (own.isDestroyed()) return;
        send();
        own.show();
        own.focus();
      });
      own.webContents.on("did-finish-load", send);
      void own.loadFile(pagePath);
    } else {
      send();
      window.show();
      window.focus();
    }
    window.setTitle(`Run ${state.workflowName} – Raticode`);
  }

  function update(partial) {
    if (!state) return;
    state = { ...state, ...partial };
    send();
  }

  function close() {
    const closing = window;
    window = null;
    state = null;
    handlers = null;
    if (closing && !closing.isDestroyed()) closing.destroy();
  }

  return {
    open,
    update,
    close,
    isOpen: (id) => Boolean(window && !window.isDestroyed() && state?.id === id),
    focus: () => { if (window && !window.isDestroyed()) { window.show(); window.focus(); } },
  };
}

module.exports = { ACTION_CHANNEL, SHEET_SIZE, STATE_CHANNEL, createRunSheet, placement };
