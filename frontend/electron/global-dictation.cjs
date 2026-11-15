// "Dictate to Rem from any app": a system-wide shortcut that starts and stops dictation
// into Rem's message box while Raticode is in the background, minimized or hidden.
//
// The main process owns the shortcut and a small listening overlay; the window's renderer
// does the recording, exactly as the mic button does. Nothing here sees audio or words:
// only the dictation's phase ("listening", "tidying"...) and a short result message.
//
// It is opt-in (Settings > Devices). With it off, nothing is registered, and the
// command-line trigger (`--toggle-dictation`, for desktops that block app shortcuts) is
// ignored.

const TOGGLE_FLAG = "--toggle-dictation";

// Settings bindings use KeyboardEvent.code names; Electron accelerators use their own.
const KEY_NAMES = {
  Space: "Space",
  Enter: "Enter",
  Tab: "Tab",
  Backspace: "Backspace",
  Delete: "Delete",
  Insert: "Insert",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Backquote: "`",
  Minus: "-",
  Equal: "=",
  BracketLeft: "[",
  BracketRight: "]",
  Backslash: "\\",
  Semicolon: ";",
  Quote: "'",
  Comma: ",",
  Period: ".",
  Slash: "/",
};

/**
 * Turns a Settings binding ("Ctrl+Alt+Space", "Mod+Shift+KeyD") into an Electron
 * accelerator, or explains why it can't be a system-wide shortcut.
 * Returns { accelerator } or { error }.
 */
function toAccelerator(binding, platform = process.platform) {
  const value = String(binding ?? "").trim();
  if (!value) return { error: "No shortcut is assigned. Choose one in Settings > Keybindings." };
  if (/\s/.test(value)) return { error: "A shortcut used in every app must be a single key combination, not a sequence." };
  const parts = value.split("+").filter(Boolean);
  const code = parts.pop();
  const mac = platform === "darwin";
  const modifiers = [];
  for (const part of parts) {
    const name = { Mod: mac ? "Command" : "Control", Ctrl: "Control", Alt: "Alt", Shift: "Shift", Meta: mac ? "Command" : "Super" }[part];
    if (!name) return { error: `Unknown modifier "${part}".` };
    if (!modifiers.includes(name)) modifiers.push(name);
  }
  let key = "";
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3);
  else if (/^Digit[0-9]$/.test(code)) key = code.slice(5);
  else if (/^Numpad[0-9]$/.test(code)) key = `num${code.slice(6)}`;
  else if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) key = code;
  else key = KEY_NAMES[code] ?? "";
  if (!key) return { error: `The key "${code}" can't be used for a shortcut in every app.` };
  // Without Ctrl, Alt or the system key, the shortcut would take an ordinary key away from
  // every app you type in. Function keys are the exception.
  const strong = modifiers.some((name) => name !== "Shift");
  if (!strong && !/^F\d+$/.test(key)) {
    return { error: "Add Ctrl, Alt or another modifier, so the shortcut doesn't take a key you type with in other apps." };
  }
  return { accelerator: [...modifiers, key].join("+") };
}

/**
 * How this desktop delivers system-wide shortcuts. kind is:
 * - "native": Windows, macOS and X11 sessions; the app registers the key directly.
 * - "portal": a Wayland session; Electron asks the desktop's GlobalShortcuts portal, which
 *   may ask you to confirm the key, may give you another key, or may not exist at all.
 * - "xwayland": Raticode runs as an X11 app inside a Wayland session (the Raticode (gecko)
 *   browser needs that); most Wayland desktops only send such apps keys while one of them
 *   is focused.
 */
function shortcutSupport({ platform = process.platform, env = process.env, ozonePlatform = "" } = {}) {
  if (platform !== "linux") return { kind: "native" };
  const wayland = Boolean(env.WAYLAND_DISPLAY) || env.XDG_SESSION_TYPE === "wayland";
  if (!wayland) return { kind: "native" };
  const hint = String(env.ELECTRON_OZONE_PLATFORM_HINT || "").toLowerCase();
  const x11 = ozonePlatform === "x11" || (!ozonePlatform && hint === "x11");
  return x11 ? { kind: "xwayland" } : { kind: "portal" };
}

// The command a desktop's own shortcut settings can run when it won't let apps register
// keys. It reaches the running Raticode (single instance) and does nothing else.
function toggleCommand({ execPath = process.execPath, appImage = process.env.APPIMAGE, packaged = true, appPath = "", configHome = "" } = {}) {
  const parts = [];
  // A dev or custom profile lives under XDG_CONFIG_HOME; the command must find the same one.
  if (configHome) parts.push("env", `XDG_CONFIG_HOME=${quote(configHome)}`);
  parts.push(quote(appImage || execPath));
  if (!packaged && appPath) parts.push(quote(appPath));
  parts.push(TOGGLE_FLAG);
  return parts.join(" ");
}

function quote(value) {
  const text = String(value);
  return /^[\w@%+=:,./-]+$/.test(text) ? text : `'${text.replace(/'/g, "'\\''")}'`;
}

function hasToggleFlag(argv) {
  return Array.isArray(argv) && argv.includes(TOGGLE_FLAG);
}

const PHASES = new Set(["starting", "listening", "paused", "finishing", "tidying", "done", "error", "idle"]);

/**
 * The shortcut and the overlay. deps:
 *   globalShortcut      Electron's globalShortcut (or a test double)
 *   support             shortcutSupport() for this session
 *   send(action)        delivers { action } to the window's renderer; false when there is none
 *   overlay             { show(state), hide() } or null
 *   onListening(bool)   lets the tray say Raticode is listening
 *   now()               clock, for tests
 */
function createGlobalDictation({ globalShortcut, support = { kind: "native" }, send, overlay = null, onListening = () => {}, now = Date.now, command = "" }) {
  let enabled = false;
  let accelerator = "";
  let registered = "";
  let error = "";
  let lastPressedAt = 0;
  // Whether the dictation in progress was started from outside Raticode, so the overlay
  // follows it. A dictation started with the mic button never shows the overlay.
  let active = false;
  let label = "";
  let binding = "";
  // A line about what goes with the words, such as a screenshot from "Ask Rem about your
  // screen". It lasts until that dictation ends.
  let note = "";

  function unregister() {
    if (registered) {
      try { globalShortcut.unregister(registered); } catch { /* Already gone. */ }
    }
    registered = "";
  }

  function status() {
    return {
      enabled,
      accelerator,
      registered: Boolean(registered),
      error,
      support: support.kind,
      command,
      lastPressedAt,
    };
  }

  /** Applies Settings: { enabled, binding, label }. Returns status(). */
  function configure({ enabled: on = false, binding: wantedBinding = "", label: shown = "" } = {}) {
    enabled = on === true;
    binding = String(wantedBinding || "");
    label = String(shown || "");
    error = "";
    const wanted = enabled ? toAccelerator(binding) : { accelerator: "" };
    if (wanted.error) {
      unregister();
      accelerator = "";
      error = wanted.error;
      return status();
    }
    if (wanted.accelerator === registered && registered) {
      accelerator = registered;
      return status();
    }
    unregister();
    accelerator = wanted.accelerator;
    if (!enabled || !accelerator) {
      if (active) stopOverlay();
      return status();
    }
    let ok = false;
    try {
      ok = globalShortcut.register(accelerator, () => trigger("shortcut"));
    } catch (failure) {
      ok = false;
      error = failure instanceof Error ? failure.message : String(failure);
    }
    if (ok) registered = accelerator;
    else if (!error) {
      error = "Another app already uses this shortcut, or the system reserves it. Choose a different one in Settings > Keybindings.";
    }
    return status();
  }

  /** The shortcut was pressed, or the toggle command ran. Returns whether it was used. */
  function trigger(source = "shortcut") {
    if (!enabled) return false;
    lastPressedAt = now();
    const delivered = send({ action: "toggle", source });
    if (!delivered) {
      overlay?.show({ phase: "error", message: "Raticode's window isn't ready yet. Try again in a moment.", label });
      return false;
    }
    return true;
  }

  function stopOverlay() {
    active = false;
    onListening(false);
    overlay?.hide();
  }

  /**
   * The renderer reports a dictation's phase. global says whether this dictation was
   * started from outside Raticode. message is a short status line, never the words.
   */
  function report({ phase, global = false, message = "", sent = false } = {}) {
    if (!PHASES.has(phase)) return false;
    // A dictation from another app that couldn't start (Rem busy, no microphone) still
    // reports its error, so pressing the shortcut never does nothing silently.
    if (global) active = true;
    if (!active) return false;
    onListening(phase === "starting" || phase === "listening");
    if (phase === "idle") {
      note = "";
      stopOverlay();
      return true;
    }
    overlay?.show({ phase, message: String(message || "").slice(0, 300), sent: sent === true, label, note });
    if (phase === "done" || phase === "error") {
      note = "";
      active = false;
      onListening(false);
    }
    return true;
  }

  /** What goes with the next dictation's words, shown in the overlay (never the words). */
  function setNote(text = "") {
    note = String(text || "").slice(0, 160);
  }

  function suspend(on) {
    try { globalShortcut.setSuspended?.(on === true); } catch { /* Not available on this build. */ }
    // Resuming re-registers a binding that was changed while suspended.
    if (!on && enabled && !registered) configure({ enabled, binding, label });
    return status();
  }

  function dispose() {
    unregister();
    overlay?.hide();
  }

  return { configure, trigger, report, setNote, status, suspend, dispose };
}

module.exports = {
  TOGGLE_FLAG,
  createGlobalDictation,
  hasToggleFlag,
  shortcutSupport,
  toAccelerator,
  toggleCommand,
};
