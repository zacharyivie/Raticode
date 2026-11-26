// Workflow shortcuts: a system-wide key that runs a workflow from any app while
// Raticode is running. The backend keeps the list (/api/workflow-shortcuts, also
// `Raticode rattish shortcuts`); the desktop app registers the keys. Added from a
// workflow's menu (Add shortcut…) and listed in Settings > Devices.

import { useSyncExternalStore } from "react";

import { apiJson } from "./api.js";
import { formatKeybinding, normalizedBinding, settingBinding } from "./settings.js";

export const WORKFLOW_SHORTCUTS_CHANGED = "gofer:workflow-shortcuts-changed";
// A changed workflow's notice was clicked: Settings > Devices opens that shortcut's review.
export const WORKFLOW_SHORTCUT_REVIEW = "gofer:workflow-shortcut-review";
// The dictation shortcuts, which a workflow shortcut may not take.
export const RESERVED_SHORTCUT_COMMANDS = [
  ["voice.dictateAnywhere", "Dictate to Rem from any app"],
  ["voice.askAboutScreen", "Ask Rem about your screen"],
];

const FUNCTION_KEY = /^F([1-9]|1[0-9]|2[0-4])$/;
const KEY = /^(Key[A-Z]|Digit[0-9]|Numpad[0-9]|F([1-9]|1[0-9]|2[0-4])|Space|Enter|Tab|Backspace|Delete|Insert|Home|End|PageUp|PageDown|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Backquote|Minus|Equal|BracketLeft|BracketRight|Backslash|Semicolon|Quote|Comma|Period|Slash)$/;

function bridge() {
  return typeof window === "undefined" ? null : window.goferDesktop?.workflowShortcuts ?? null;
}

// Approve this version names the files it couldn't show when it refuses (error.unseen).
const request = (path, options = {}) => apiJson(path, { ...options, failure: status => `Workflow shortcuts returned ${status}` });

/** Why a recorded binding can't be a shortcut in every app, or "". Same rules as the app's. */
export function bindingProblem(binding) {
  const value = String(binding ?? "").trim();
  if (!value) return "Press a key combination.";
  if (/\s/.test(value)) return "Use one key combination, not a sequence.";
  const parts = value.split("+").filter(Boolean);
  const key = parts.pop();
  if (!KEY.test(key)) return "This key can't be used for a shortcut in every app.";
  if (!parts.some((part) => part !== "Shift") && !FUNCTION_KEY.test(key)) {
    return "Add Ctrl, Alt or another modifier, so the shortcut doesn't take a key you type with in other apps.";
  }
  return "";
}

/**
 * What the key clashes with: a dictation shortcut, or another workflow's shortcut.
 * Returns { label, kind } or null. own is the shortcut being changed, if any.
 */
export function shortcutClash(binding, { settings, shortcuts = [], own = "" } = {}, platform) {
  if (!binding) return null;
  const wanted = normalizedBinding(binding, platform);
  for (const [commandId, label] of RESERVED_SHORTCUT_COMMANDS) {
    const taken = settingBinding(settings, commandId);
    if (taken && normalizedBinding(taken, platform) === wanted) return { kind: "dictation", label };
  }
  const other = shortcuts.find((item) => item.id !== own && normalizedBinding(item.binding, platform) === wanted);
  return other ? { kind: "workflow", label: other.workflowName || other.workflowId } : null;
}

export function clashMessage(clash, binding) {
  if (!clash) return "";
  const key = formatKeybinding(binding);
  return clash.kind === "dictation"
    ? `${key} is the ${clash.label} shortcut. Choose another key, or change that one in Settings > Keybindings.`
    : `${key} already runs ${clash.label}. Choose another key, or remove that shortcut in Settings > Devices.`;
}

export async function listWorkflowShortcuts({ details = false } = {}) {
  const payload = await request(`/workflow-shortcuts${details ? "?details=1" : ""}`);
  return Array.isArray(payload.shortcuts) ? payload.shortcuts : [];
}

/** What a shortcut for this workflow would do, and the one it has now. */
export function workflowShortcutTarget(workflowId) {
  return request(`/workflow-shortcuts/target?workflowId=${encodeURIComponent(workflowId)}`);
}

function changed() {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(WORKFLOW_SHORTCUTS_CHANGED));
  return syncDesktopShortcuts();
}

export async function saveWorkflowShortcut({ workflowId, binding, input = null }) {
  const payload = await request("/workflow-shortcuts", {
    method: "POST",
    body: { workflowId, binding, ...(input ? { input } : {}) },
  });
  await changed();
  return payload.shortcut;
}

export async function removeWorkflowShortcut(id) {
  const payload = await request(`/workflow-shortcuts/${encodeURIComponent(id)}`, { method: "DELETE" });
  await changed();
  return payload.removed;
}

// --- Only the approved version runs -------------------------------------------------------

// Approval states that keep a shortcut from running until you review the change.
const NEEDS_REVIEW = new Set(["changed", "unapproved"]);

/** Whether the shortcut waits for you to review a change before it runs its workflow. */
export function needsReview(entry) {
  return NEEDS_REVIEW.has(entry?.approval?.state);
}

/** The Settings row's words for a shortcut waiting for review, or "". */
export function reviewLabel(entry) {
  const state = entry?.approval?.state;
  if (state === "changed") return "Changed: review";
  if (state === "unapproved") return "Not approved: review";
  return "";
}

/**
 * Review changes for one shortcut (ChangesReview's ``source``): what differs from the
 * approved version, and approving exactly the version reviewed. acceptUnseen is the
 * second confirmation for changes that weren't shown in full.
 */
export function shortcutReviewSource(id) {
  const path = `/workflow-shortcuts/${encodeURIComponent(id)}`;
  return {
    changes: async () => (await request(`${path}/changes`)).changes,
    approve: async (fingerprint, { acceptUnseen = false } = {}) => {
      const payload = await request(`${path}/approve`, {
        method: "POST",
        body: acceptUnseen ? { fingerprint, acceptUnseen: true } : { fingerprint },
      });
      await changed();
      return payload.shortcut;
    },
  };
}

let pendingReview = "";

/** Asks Settings > Devices to open this shortcut's Review changes, now or when it opens. */
export function requestShortcutReview(id) {
  pendingReview = String(id || "");
  if (typeof window !== "undefined") window.dispatchEvent?.(new CustomEvent(WORKFLOW_SHORTCUT_REVIEW, { detail: { id: pendingReview } }));
}

/** The shortcut whose review was asked for, once. */
export function takeShortcutReview() {
  const id = pendingReview;
  pendingReview = "";
  return id;
}

// --- The desktop app's registration status -----------------------------------------------

let desktopStatus = null;
const listeners = new Set();
let subscribed = false;

function setDesktopStatus(next) {
  desktopStatus = next && typeof next === "object" ? next : null;
  for (const listener of listeners) listener();
}

function subscribeDesktop() {
  if (subscribed) return;
  const desktop = bridge();
  if (!desktop?.onEvent) return;
  subscribed = true;
  desktop.onEvent((message) => {
    if (message?.action === "status") setDesktopStatus(message.status);
  });
}

/** Asks the desktop app to register the list again. Resolves to its status, or null. */
export async function syncDesktopShortcuts() {
  const desktop = bridge();
  if (!desktop?.sync) return null;
  subscribeDesktop();
  try {
    const status = await desktop.sync();
    setDesktopStatus(status);
    return status;
  } catch {
    return null;
  }
}

/** The desktop app's status for each shortcut ({ shortcuts: [...] }), or null outside it. */
export function useWorkflowShortcutStatus() {
  return useSyncExternalStore((listener) => {
    subscribeDesktop();
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, () => desktopStatus, () => null);
}

export function hasDesktopShortcuts() {
  return Boolean(bridge()?.sync);
}

/**
 * While a key is being recorded, the system-wide shortcuts let keys through, so the
 * key reaches the recorder instead of running a workflow or starting dictation.
 */
export function suspendSystemShortcuts(on) {
  const desktop = typeof window === "undefined" ? null : window.goferDesktop;
  void desktop?.globalDictation?.suspend?.(on)?.catch?.(() => {});
  void desktop?.workflowShortcuts?.suspend?.(on)?.catch?.(() => {});
}

/** One line for a shortcut's state on this computer. */
export function shortcutStateLine(entry, status) {
  if (!status) return { tone: "muted", text: "" };
  if (status.error) return { tone: "warn", text: status.error };
  if (status.registered) {
    const key = status.trigger || formatKeybinding(entry.binding);
    return { tone: "muted", text: status.state && status.trigger && status.trigger !== formatKeybinding(entry.binding) ? `Ready: your desktop uses ${key} for it.` : `Ready in every app.` };
  }
  if (status.state === "approval") return { tone: "warn", text: "Your desktop is asking whether Raticode may add this key. Answer its prompt." };
  if (status.state === "working" || (!status.state && !status.registered)) return { tone: "muted", text: "Setting up the key…" };
  return { tone: "warn", text: "This key isn't working in other apps yet." };
}
