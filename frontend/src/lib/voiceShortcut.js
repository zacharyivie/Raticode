// The keyboard side of dictation. Every chat box registers here; the "Dictate a message"
// shortcut (Ctrl+Alt+V, or Command+Option+V on a Mac) goes to the right one: the box
// you're typing in, else one that is already dictating, else the visible box you used
// last. How it listens follows the dictation mode in Settings > Devices: press to start
// and press again to stop, or hold to talk and let go to stop. Stopping either way goes
// straight on to the words, tidied when tidying is on.

import { useSyncExternalStore } from "react";

import { formatKeybinding } from "./settings.js";

export const VOICE_COMMAND_ID = "voice.dictate";
export const DEFAULT_VOICE_BINDING = "Mod+Alt+KeyV";
// Dictate to Rem from any app: a system-wide shortcut, off until Settings > Devices turns
// it on. It is separate from the in-app shortcut so Hold to talk keeps working inside
// Raticode (a system-wide key can't tell when it is let go).
export const ANYWHERE_COMMAND_ID = "voice.dictateAnywhere";

// "toggle": press once to start and again to stop. "hold": listen only while held.
export const DICTATION_MODES = [["toggle", "Press to start and stop"], ["hold", "Hold to talk"]];
export const DEFAULT_DICTATION_MODE = "toggle";

const composers = new Set();
let binding = DEFAULT_VOICE_BINDING;
let mode = DEFAULT_DICTATION_MODE;
// Send the message as soon as dictation finishes, for hands-free use. Off by default.
let autoSend = false;
const listeners = new Set();
// A shortcut press still waiting for Rem's chat box to appear; letting go cancels it.
let waiting = null;

/**
 * entry: { root: () => Element|null, toggle: (options?) => void, press: () => void,
 *          release: () => void, available: () => boolean, dictating: () => boolean,
 *          rem?: () => boolean, lastUsed: number }
 * rem says the box talks to Rem; only those take dictation from other apps.
 */
export function registerVoiceComposer(entry) {
  composers.add(entry);
  return () => composers.delete(entry);
}

function visible(element) {
  if (!element || element.isConnected === false) return false;
  // A box inside a hidden pane (Rem toggled off, a background tab) doesn't take the shortcut.
  // Panes mark themselves aria-hidden; getClientRects catches anything else CSS hides.
  for (let node = element; node?.nodeType === 1; node = node.parentNode) {
    if (node.getAttribute?.("aria-hidden") === "true") return false;
  }
  const rects = element.getClientRects?.();
  return rects ? rects.length > 0 : true;
}

export function pickVoiceComposer(active = globalThis.document?.activeElement) {
  const entries = [...composers];
  const focused = entries.find((entry) => active && entry.root()?.contains?.(active));
  if (focused) return focused;
  const dictating = entries.find((entry) => entry.dictating());
  if (dictating) return dictating;
  return entries
    .filter((entry) => entry.available() && visible(entry.root()))
    .sort((left, right) => right.lastUsed - left.lastUsed)[0] ?? null;
}

/**
 * Starts or stops dictation. kind is "toggle" (the menu, or a shortcut that can't report
 * being let go), "press" (the shortcut went down) or "release" (it came up). With no chat
 * box on screen, reveal() shows Rem and the shortcut waits briefly for its box to appear.
 * Resolves to whether a box took it.
 */
export function requestVoiceDictation({ reveal = () => {}, waitMs = 1500, stepMs = 50, kind = "toggle" } = {}) {
  if (kind === "release") {
    if (waiting) waiting.cancelled = true;
    waiting = null;
    const entry = [...composers].find((candidate) => candidate.dictating());
    entry?.release?.();
    return Promise.resolve(Boolean(entry));
  }
  const act = (entry) => (kind === "press" && entry.press ? entry.press() : entry.toggle());
  const now = pickVoiceComposer();
  if (now) {
    act(now);
    return Promise.resolve(true);
  }
  reveal();
  const token = { cancelled: false };
  waiting = token;
  return new Promise((resolve) => {
    const deadline = Date.now() + waitMs;
    const retry = () => {
      if (token.cancelled) {
        resolve(false);
        return;
      }
      const entry = pickVoiceComposer();
      if (entry) {
        if (waiting === token) waiting = null;
        act(entry);
        resolve(true);
      } else if (Date.now() >= deadline) {
        if (waiting === token) waiting = null;
        resolve(false);
      } else {
        later(retry, stepMs);
      }
    };
    later(retry, stepMs);
  });
}

// Dictation from another app goes to Rem: the Rem box already dictating, else the Rem box
// you're typing in, else a visible Rem box, else Rem's box even while Rem isn't on screen
// (its words wait there, or are sent with Send after dictation on). Most recently used first.
export function pickRemComposer(active = globalThis.document?.activeElement) {
  const entries = [...composers].filter((entry) => entry.rem?.());
  const dictating = entries.find((entry) => entry.dictating());
  if (dictating) return dictating;
  const usable = entries.filter((entry) => entry.available());
  const focused = usable.find((entry) => active && entry.root()?.contains?.(active));
  if (focused) return focused;
  return usable
    .map((entry) => ({ entry, shown: visible(entry.root()) }))
    .sort((left, right) => Number(right.shown) - Number(left.shown) || right.entry.lastUsed - left.entry.lastUsed)[0]?.entry ?? null;
}

/**
 * The system-wide shortcut was pressed while Raticode may be in the background: start
 * dictating into Rem's box, or stop the dictation in progress. Never moves the window
 * forward or changes what it shows; reveal() only loads Rem's chat when it hasn't been
 * opened yet. Resolves to { ok, message }; message says why nothing started.
 */
export function requestAnywhereDictation({ reveal = () => {}, waitMs = 5000, stepMs = 50 } = {}) {
  const now = pickRemComposer();
  if (now) {
    now.toggle({ anywhere: true });
    return Promise.resolve({ ok: true });
  }
  const busy = [...composers].some((entry) => entry.rem?.() && !entry.available());
  if (busy) return Promise.resolve({ ok: false, message: "Rem is still answering in the open chat. Try again when it's done." });
  reveal();
  return new Promise((resolve) => {
    const deadline = Date.now() + waitMs;
    const retry = () => {
      const entry = pickRemComposer();
      if (entry) {
        entry.toggle({ anywhere: true });
        resolve({ ok: true });
      } else if (Date.now() >= deadline) {
        resolve({ ok: false, message: "Rem's chat didn't load. Open Rem in Raticode, then try again." });
      } else {
        later(retry, stepMs);
      }
    };
    later(retry, stepMs);
  });
}

/** Stops a dictation that was started from another app (the overlay's Stop button). */
export function stopAnywhereDictation() {
  const entry = [...composers].find((candidate) => candidate.dictating() && candidate.anywhere?.());
  entry?.toggle();
  return Boolean(entry);
}

// What a dictation started from another app is doing, for the desktop's listening
// overlay: { phase, global, message, sent }. Never the words.
const phaseListeners = new Set();
export function onVoicePhase(listener) {
  phaseListeners.add(listener);
  return () => phaseListeners.delete(listener);
}
export function announceVoicePhase(state) {
  for (const listener of phaseListeners) {
    try { listener(state); } catch { /* A listener's failure doesn't stop dictation. */ }
  }
}

// The system-wide shortcut's state from the desktop app, for Settings > Devices.
let anywhereStatus = null;
const statusListeners = new Set();
export function setAnywhereStatus(next) {
  anywhereStatus = next && typeof next === "object" ? next : null;
  for (const listener of statusListeners) listener();
}
export function useAnywhereStatus() {
  return useSyncExternalStore((listener) => {
    statusListeners.add(listener);
    return () => statusListeners.delete(listener);
  }, () => anywhereStatus, () => anywhereStatus);
}

// window.setTimeout, as elsewhere in the app, so tests can drive the wait.
function later(callback, ms) {
  const host = typeof globalThis.window?.setTimeout === "function" ? globalThis.window : globalThis;
  return host.setTimeout(callback, ms);
}

export function setVoiceBinding(next) {
  const value = typeof next === "string" ? next : DEFAULT_VOICE_BINDING;
  if (value === binding) return;
  binding = value;
  for (const listener of listeners) listener();
}

export function setDictationMode(next) {
  const value = DICTATION_MODES.some(([id]) => id === next) ? next : DEFAULT_DICTATION_MODE;
  if (value === mode) return;
  mode = value;
  for (const listener of listeners) listener();
}

/** "toggle" or "hold", from Settings > Devices. */
export function useDictationMode() {
  return useSyncExternalStore(subscribe, () => mode, () => mode);
}

export function setDictationAutoSend(next) {
  const value = next === true;
  if (value === autoSend) return;
  autoSend = value;
  for (const listener of listeners) listener();
}

/** Whether a finished dictation sends itself, from Settings > Devices. */
export function useDictationAutoSend() {
  return useSyncExternalStore(subscribe, () => autoSend, () => autoSend);
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The current binding, so chat boxes can show it in titles and hints. */
export function useVoiceBinding() {
  return useSyncExternalStore(subscribe, () => binding, () => binding);
}

/** "Ctrl+Alt+V" or "Command+Option+V"; empty when unassigned. */
export function voiceShortcutLabel(value = binding, platform = globalThis.navigator?.platform ?? "") {
  return value ? formatKeybinding(value, platform) : "";
}

/** The aria-keyshortcuts form: "Control+Alt+V" or "Meta+Alt+V". */
export function ariaKeyshortcuts(value = binding, platform = globalThis.navigator?.platform ?? "") {
  if (!value || /\s/.test(value.trim())) return undefined;
  const mac = /Mac|iPhone|iPad/i.test(platform);
  return value.split("+").map((part) => {
    if (part === "Mod") return mac ? "Meta" : "Control";
    if (part === "Ctrl") return "Control";
    return part.replace(/^Key/, "").replace(/^Digit/, "");
  }).join("+");
}
