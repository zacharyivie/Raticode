import { loadCodeDrafts, saveCodeDrafts } from "./codeDrafts.js";
import { replacePathPrefix, pathMatchesChange } from "./workspacePaths.js";

let persistenceTimer;
class DraftSessions extends Map {
  set(path, session) { super.set(path, session); this.schedule(); return this; }
  delete(path) { const deleted = super.delete(path); this.schedule(); return deleted; }
  clear() { super.clear(); this.schedule(); }
  schedule() {
    clearTimeout(persistenceTimer);
    if (typeof window !== "undefined") persistenceTimer = setTimeout(flushCodeDrafts, 250);
  }
}
export const textEditorSessions = new DraftSessions(loadCodeDrafts());
export function flushCodeDrafts() {
  clearTimeout(persistenceTimer);
  return saveCodeDrafts(textEditorSessions);
}
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", flushCodeDrafts);
  window.addEventListener("pagehide", flushCodeDrafts);
}
export const discardedSessionPaths = new Set();
export const FILE_AUTOSAVE_DELAY_MS = 1000;

export function codeCloseProtection(dirtyPaths, autosaveEnabled) {
  if (!dirtyPaths.length) return "close";
  return autosaveEnabled ? "confirm-discard" : "prompt-to-save";
}

export function hasUnsavedCodeChanges(rootPath) {
  return [...textEditorSessions].some(([path, session]) => pathMatchesChange(path, rootPath, true) && session.content !== session.savedContent);
}

export function applyCodeFilesystemChange(change) {
  if (change?.type === "git") {
    for (const [path, session] of textEditorSessions) {
      if (pathMatchesChange(path, change.rootPath, true) && session.content === session.savedContent) textEditorSessions.delete(path);
    }
    window.dispatchEvent(new CustomEvent("gofer:git-files-changed", { detail: change }));
    return;
  }
  if (!change?.path) return;
  if (change.replaced) {
    for (const path of [...textEditorSessions.keys()]) {
      if (!pathMatchesChange(path, change.path, true)) continue;
      textEditorSessions.delete(path);
      discardedSessionPaths.add(path);
    }
    window.dispatchEvent(new CustomEvent("gofer:code-files-changed", { detail: change }));
  }
  if (change.kind === "create") {
    discardedSessionPaths.delete(change.path);
    textEditorSessions.delete(change.path);
    return;
  }
  if (change.kind === "delete") {
    for (const path of textEditorSessions.keys()) {
      if (!pathMatchesChange(path, change.path, change.isDirectory)) continue;
      discardedSessionPaths.add(path);
      textEditorSessions.delete(path);
    }
    discardedSessionPaths.add(change.path);
    return;
  }
  if (change.kind !== "rename" || !change.sourcePath) return;
  for (const [path, session] of [...textEditorSessions.entries()]) {
    const nextPath = replacePathPrefix(path, change.sourcePath, change.path, change.isDirectory);
    if (nextPath === path) continue;
    discardedSessionPaths.add(path);
    discardedSessionPaths.delete(nextPath);
    textEditorSessions.delete(path);
    textEditorSessions.set(nextPath, session);
  }
}

