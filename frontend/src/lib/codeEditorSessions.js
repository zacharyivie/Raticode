import { replacePathPrefix, pathMatchesChange } from "./workspacePaths.js";

export const textEditorSessions = new Map();
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

