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
 * entry: { root: () => Element|null, toggle: () => void, press: () => void,
 *          release: () => void, available: () => boolean, dictating: () => boolean,
 *          lastUsed: number }
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
