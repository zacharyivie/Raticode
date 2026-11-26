import { useSyncExternalStore } from "react";

// Show Rem your screen: a screenshot taken only when you ask, for one Rem message.
// It stays in this window's memory until the message is sent; Rem uses it for that one
// reply and keeps only its name (see rem_screen.py). Turned on in Settings > Devices.

export const SCREEN_COMMAND_ID = "voice.askAboutScreen";
// Rem's chat takes one picture of up to 5 MB; the desktop app keeps under it.
export const SCREEN_MAX_BYTES = 5 * 1024 * 1024;

export function screenBridge() {
  return typeof window === "undefined" ? null : window.goferDesktop?.screenContext ?? null;
}

// A file name for the picture: what it shows, made safe for any file system.
export function screenFileName(name, type = "image/png") {
  const safe = Array.from(String(name || "Screen"), (char) => (char.codePointAt(0) < 0x20 || '\\/:*?"<>|'.includes(char) ? " " : char)).join("");
  const base = safe.replace(/\s+/g, " ").trim().slice(0, 60) || "Screen";
  return `Screen - ${base}.${type === "image/jpeg" ? "jpg" : "png"}`;
}

/**
 * The desktop app's capture result ({ name, kind, type, data, width, height }) as the
 * message box's screenshot: { id, file, name, kind, size, type, width, height }.
 */
export function screenFromCapture(capture, { fromShortcut = false } = {}) {
  const bytes = capture?.data;
  const length = bytes?.byteLength ?? 0;
  if (!length || length > SCREEN_MAX_BYTES) return null;
  const type = capture.type === "image/jpeg" ? "image/jpeg" : "image/png";
  const name = String(capture.name || "Screen").slice(0, 80);
  const file = new File([bytes], screenFileName(name, type), { type });
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return {
    id: `screen:${random}`,
    file,
    name,
    kind: capture.kind === "window" ? "window" : "screen",
    size: file.size,
    type,
    width: Number(capture.width) || 0,
    height: Number(capture.height) || 0,
    fromShortcut: fromShortcut === true,
  };
}

/** The picture for POST /api/rem/messages: { name, data } with base64 bytes. */
export async function encodeScreen(screen) {
  const buffer = new Uint8Array(await screen.file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < buffer.length; offset += 0x8000) {
    binary += String.fromCharCode(...buffer.subarray(offset, offset + 0x8000));
  }
  return { name: screen.name, data: btoa(binary) };
}

// The desktop app's answer to Settings: { enabled, support, permission, shortcut }.
let status = null;
const listeners = new Set();
export function setScreenStatus(next) {
  status = next && typeof next === "object" ? next : null;
  for (const listener of listeners) listener();
}
export function useScreenStatus() {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, () => status, () => status);
}

// Whether the Screen button shows: on in Settings, in the desktop app.
let enabled = false;
const enabledListeners = new Set();
export function setScreenContextEnabled(next) {
  enabled = next === true;
  for (const listener of enabledListeners) listener();
}
export function useScreenContextEnabled() {
  return useSyncExternalStore((listener) => {
    enabledListeners.add(listener);
    return () => enabledListeners.delete(listener);
  }, () => enabled, () => enabled);
}
