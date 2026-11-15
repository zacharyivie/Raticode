// "Show Rem your screen": one screenshot, taken only when you ask, for one Rem message.
//
// Off until Settings > Devices turns it on. Nothing here runs in the background: a picture
// is taken only when the Rem message box's Screen button is used, or when the "Ask Rem
// about your screen" shortcut is pressed. The picture goes to the window in memory and is
// never written to disk or logged here; the message box shows it so it can be removed
// before sending.
//
// How a picture is taken depends on the desktop:
// - "list": Windows, macOS and Linux on X11. Raticode lists screens and windows itself.
//   macOS asks once for Screen Recording permission.
// - "portal": Linux on Wayland. The desktop's own screen-sharing dialog asks which screen or
//   window to share, every time.
// - "xwayland": Raticode runs as an X11 app on a Wayland desktop (the Raticode (gecko) browser
//   needs that). Only other X11 apps can be seen; screens are usually black.
// - "unsupported": anything else.

const { toAccelerator } = require("./global-dictation.cjs");

// Rem's chat accepts images of up to 5 MB (rem_home.CHAT_IMAGE_LIMIT); stay under it.
const MAX_BYTES = 4_800_000;
// Chat models scale big pictures down anyway; this keeps text readable on large screens.
const MAX_SIDE = 2560;
const FALLBACK_SIDE = 1920;
const LIST_THUMBNAIL = { width: 320, height: 200 };
const MAX_NAME = 80;

const MAC_PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

function captureSupport({ platform = process.platform, env = process.env, ozonePlatform = "" } = {}) {
  if (platform === "win32" || platform === "darwin") return { kind: "list" };
  if (platform !== "linux") return { kind: "unsupported" };
  const wayland = Boolean(env.WAYLAND_DISPLAY) || env.XDG_SESSION_TYPE === "wayland";
  if (!wayland) return { kind: "list" };
  const hint = String(env.ELECTRON_OZONE_PLATFORM_HINT || "").toLowerCase();
  const x11 = ozonePlatform === "x11" || (!ozonePlatform && hint === "x11");
  return x11 ? { kind: "xwayland" } : { kind: "portal" };
}

// Control and invisible characters could hide part of a name from the person reading it.
function cleanName(value, fallback) {
  const text = Array.from(String(value ?? ""), (char) => (hiddenCharacter(char.codePointAt(0)) ? " " : char))
    .join("")
    .replace(/\s+/g, " ")
    .trim();
  return (text || fallback).slice(0, MAX_NAME);
}

// Control characters, zero-width marks and direction overrides.
function hiddenCharacter(code) {
  return code < 0x20 || code === 0x7f
    || (code >= 0x200b && code <= 0x200f)
    || (code >= 0x202a && code <= 0x202e)
    || (code >= 0x2066 && code <= 0x2069)
    || code === 0xfeff;
}

function sourceKind(id) {
  return String(id).startsWith("screen:") ? "screen" : "window";
}

// The size to ask for: the largest screen in real pixels, at most MAX_SIDE on its long side.
function captureSize(screen) {
  let width = 1920;
  let height = 1080;
  try {
    const displays = screen.getAllDisplays();
    if (displays.length) {
      width = Math.max(...displays.map((display) => Math.round(display.size.width * (display.scaleFactor || 1))));
      height = Math.max(...displays.map((display) => Math.round(display.size.height * (display.scaleFactor || 1))));
    }
  } catch { /* The default size is fine. */ }
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

// PNG keeps text sharp; a busy screen that won't fit becomes a JPEG, then a smaller one.
function encode(image) {
  const png = image.toPNG();
  if (png.length && png.length <= MAX_BYTES) return { type: "image/png", data: png, extension: "png" };
  const jpeg = image.toJPEG(85);
  if (jpeg.length && jpeg.length <= MAX_BYTES) return { type: "image/jpeg", data: jpeg, extension: "jpg" };
  const size = image.getSize();
  const scale = FALLBACK_SIDE / Math.max(size.width, size.height, 1);
  const smaller = scale < 1 ? image.resize({ width: Math.round(size.width * scale), height: Math.round(size.height * scale), quality: "good" }) : image;
  const last = smaller.toJPEG(70);
  if (last.length && last.length <= MAX_BYTES) return { type: "image/jpeg", data: last, extension: "jpg" };
  return null;
}

function failure(code, error) {
  return { ok: false, code, error };
}

const MESSAGES = {
  off: "Turn on Show Rem your screen in Settings > Devices first.",
  unsupported: "Raticode can't take a screenshot on this system.",
  busy: "A screenshot is already being taken.",
  cancelled: "No screenshot was taken.",
  gone: "That screen or window isn't there any more. Choose again.",
  empty: "The screenshot came back empty. The screen may be locked, or the window minimized.",
  tooBig: "The screenshot is too large to send to Rem.",
  failed: "Raticode couldn't take a screenshot.",
  macAsk: "macOS asked whether Raticode may record your screen. Allow it in System Settings > Privacy & Security > Screen & System Audio Recording, then quit and reopen Raticode.",
  macDenied: "macOS doesn't let Raticode see your screen. Allow it in System Settings > Privacy & Security > Screen & System Audio Recording, then quit and reopen Raticode.",
  portal: "Your desktop's screen-sharing dialog was closed or isn't available, so no screenshot was taken. On Wayland, Raticode needs xdg-desktop-portal and PipeWire to see other apps.",
  xwayland: "Raticode is running as an X11 app on a Wayland desktop (the Raticode (gecko) browser needs this), so it can only see other X11 apps. The screenshot came back empty.",
};

/**
 * deps:
 *   desktopCapturer      Electron's desktopCapturer (or a test double)
 *   screen               Electron's screen
 *   systemPreferences    Electron's systemPreferences (macOS permission)
 *   platform             process.platform
 *   support              captureSupport() for this session
 *   ownSourceIds()       media source ids of Raticode's own windows, left out of the list
 */
function createScreenContext({ desktopCapturer, screen, systemPreferences = null, platform = process.platform, support = { kind: "list" }, ownSourceIds = () => [] }) {
  let enabled = false;
  let capturing = false;

  function permission() {
    if (platform !== "darwin" || typeof systemPreferences?.getMediaAccessStatus !== "function") return "granted";
    try { return String(systemPreferences.getMediaAccessStatus("screen") || "unknown"); } catch { return "unknown"; }
  }

  function status() {
    return { enabled, support: support.kind, permission: permission() };
  }

  function configure({ enabled: on = false } = {}) {
    enabled = on === true;
    return status();
  }

  // macOS: asking for sources the first time shows the system prompt; until it's allowed,
  // windows come back as wallpaper only, so nothing is captured.
  async function allowed() {
    const state = permission();
    if (state === "granted" || state === "unknown") return null;
    if (state === "not-determined") {
      try { await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: 1, height: 1 } }); } catch { /* The prompt is what matters. */ }
      return failure("permission", MESSAGES.macAsk);
    }
    return failure("permission", MESSAGES.macDenied);
  }

  function ready() {
    if (!enabled) return failure("off", MESSAGES.off);
    if (support.kind === "unsupported") return failure("unsupported", MESSAGES.unsupported);
    if (capturing) return failure("busy", MESSAGES.busy);
    return null;
  }

  async function guarded(work) {
    const refused = ready();
    if (refused) return refused;
    capturing = true;
    try {
      const denied = await allowed();
      if (denied) return denied;
      return await work();
    } catch {
      // The reason may name windows; it is never logged or passed on.
      return failure("failed", support.kind === "portal" ? MESSAGES.portal : MESSAGES.failed);
    } finally {
      capturing = false;
    }
  }

  /** Screens and windows to choose from, with small previews. Not used with the portal. */
  function list() {
    if (support.kind === "portal") return Promise.resolve({ ok: true, portal: true, sources: [] });
    return guarded(async () => {
      const own = new Set(ownSourceIds().filter(Boolean));
      const sources = await desktopCapturer.getSources({ types: ["screen", "window"], thumbnailSize: LIST_THUMBNAIL, fetchWindowIcons: false });
      let screens = 0;
      const items = [];
      for (const source of sources) {
        if (own.has(source.id)) continue;
        const kind = sourceKind(source.id);
        if (kind === "screen") screens += 1;
        const thumbnail = source.thumbnail && !source.thumbnail.isEmpty() ? source.thumbnail.toDataURL() : "";
        // macOS without permission, minimized windows: nothing to show, nothing to choose.
        if (kind === "window" && !thumbnail) continue;
        items.push({
          id: String(source.id),
          kind,
          name: cleanName(source.name, kind === "screen" ? `Screen ${screens}` : "Window"),
          thumbnail,
        });
      }
      items.sort((left, right) => (left.kind === right.kind ? 0 : left.kind === "screen" ? -1 : 1));
      return { ok: true, portal: false, sources: items };
    });
  }

  // The screen the pointer is on, for the shortcut, where Raticode can see screens itself.
  function pointerScreen(sources) {
    const screens = sources.filter((source) => sourceKind(source.id) === "screen");
    if (screens.length <= 1) return screens[0] ?? null;
    try {
      const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
      const match = screens.find((source) => String(source.display_id) === String(display.id));
      if (match) return match;
      const index = screen.getAllDisplays().findIndex((item) => item.id === display.id);
      return screens[index] ?? screens[0];
    } catch {
      return screens[0];
    }
  }

  /**
   * One screenshot. target: { sourceId } from list(), or { pointer: true } for the screen the
   * pointer is on. With the portal, the desktop's dialog chooses. Resolves to
   * { ok, name, kind, type, data (bytes), width, height } or { ok: false, code, error }.
   */
  function capture(target = {}) {
    return guarded(async () => {
      const size = captureSize(screen);
      let source = null;
      if (support.kind === "portal") {
        const sources = await desktopCapturer.getSources({ types: ["screen", "window"], thumbnailSize: size, fetchWindowIcons: false });
        source = sources[0] ?? null;
        if (!source) return failure("cancelled", MESSAGES.cancelled);
      } else {
        const sourceId = typeof target.sourceId === "string" ? target.sourceId : "";
        const types = sourceId ? [sourceKind(sourceId)] : ["screen"];
        const sources = await desktopCapturer.getSources({ types, thumbnailSize: size, fetchWindowIcons: false });
        source = sourceId ? sources.find((item) => item.id === sourceId) ?? null : pointerScreen(sources);
        if (!source) return failure(sourceId ? "gone" : "failed", sourceId ? MESSAGES.gone : MESSAGES.failed);
      }
      const image = source.thumbnail;
      if (!image || image.isEmpty()) return failure("empty", support.kind === "xwayland" ? MESSAGES.xwayland : MESSAGES.empty);
      const encoded = encode(image);
      if (!encoded) return failure("tooBig", MESSAGES.tooBig);
      const kind = sourceKind(source.id);
      const { width, height } = image.getSize();
      return {
        ok: true,
        name: cleanName(source.name, kind === "screen" ? "Screen" : "Window"),
        kind,
        type: encoded.type,
        extension: encoded.extension,
        // A copy the window owns; Electron passes Uint8Array without extra encoding.
        data: new Uint8Array(encoded.data),
        width,
        height,
      };
    });
  }

  return { configure, status, list, capture };
}

/**
 * The "Ask Rem about your screen" shortcut: a screenshot of the screen the pointer is on (or
 * the desktop's own dialog on Wayland), then dictation to Rem. It works only while both
 * Show Rem your screen and Dictate to Rem from any app are on.
 *   onPress()   takes the screenshot and starts dictation
 */
function createScreenShortcut({ globalShortcut, onPress, now = Date.now }) {
  let registered = "";
  let accelerator = "";
  let error = "";
  let enabled = false;
  let lastPressedAt = 0;

  function unregister() {
    if (registered) {
      try { globalShortcut.unregister(registered); } catch { /* Already gone. */ }
    }
    registered = "";
  }

  function status() {
    return { enabled, accelerator, registered: Boolean(registered), error, lastPressedAt };
  }

  function configure({ enabled: on = false, binding = "" } = {}) {
    enabled = on === true;
    error = "";
    const wanted = enabled ? toAccelerator(binding) : { accelerator: "" };
    if (wanted.error) {
      unregister();
      accelerator = "";
      error = wanted.error;
      return status();
    }
    if (registered && wanted.accelerator === registered) return status();
    unregister();
    accelerator = wanted.accelerator;
    if (!enabled || !accelerator) return status();
    let ok = false;
    try {
      ok = globalShortcut.register(accelerator, () => {
        lastPressedAt = now();
        onPress();
      });
    } catch (failed) {
      error = failed instanceof Error ? failed.message : String(failed);
    }
    if (ok) registered = accelerator;
    else if (!error) error = "Another app already uses this shortcut, or the system reserves it. Choose a different one in Settings > Keybindings.";
    return status();
  }

  return { configure, status, dispose: unregister };
}

module.exports = { MAC_PRIVACY_URL, MAX_BYTES, captureSize, captureSupport, cleanName, createScreenContext, createScreenShortcut, encode };
