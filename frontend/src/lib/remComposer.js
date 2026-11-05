import { useSyncExternalStore } from "react";
import { readChatAttachments } from "./chatAttachments.js";
import { readFileAttachments } from "./fileAttachments.js";
import { focusRemComposer } from "./remHomeStore.js";

// Rem's unsent message lives here, not in its view, so it survives switching
// to another plugin. It stays out of the Rem store so typing doesn't re-render
// the sidebar, and out of App's chunk because only Rem's view needs it.
// replyTo is the preview of the message being replied to, sent as refers_to.
const emptyComposer = () => ({ draft: "", attachments: [], error: "", replyTo: null });
let composer = emptyComposer();
let appliedRequest = 0;
const listeners = new Set();

function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export const getRemComposer = () => composer;
export function useRemComposerState() { return useSyncExternalStore(subscribe, getRemComposer, getRemComposer); }
export function updateRemComposer(next) {
  composer = { ...composer, ...(typeof next === "function" ? next(composer) : next) };
  for (const listener of listeners) listener();
}
export function resetRemComposer() { appliedRequest = 0; updateRemComposer(emptyComposer()); }

export function attachToRemComposer(files) {
  updateRemComposer(current => {
    const result = readChatAttachments(Array.from(files || []), current.attachments);
    return { attachments: result.attachments, error: result.error };
  });
}

// "Ask Rem about this" reads the files and focuses the composer, even when Rem's view isn't open yet.
export async function attachPathsToRemComposer(paths) {
  try { attachToRemComposer(await readFileAttachments(paths)); }
  catch (error) { updateRemComposer({ error: error.message || "Could not attach the file." }); }
  focusRemComposer();
}

// Applies a store request (a suggestion, an edited message, a cleared history)
// once. Returns whether it is new, so returning to Rem doesn't replay it.
export function applyRemComposerRequest(request) {
  if (!request || request.id === appliedRequest) return false;
  appliedRequest = request.id;
  if (typeof request.text === "string") updateRemComposer({ draft: request.text });
  if (request.attachments?.length) updateRemComposer({ attachments: request.attachments });
  if ("replyTo" in request) updateRemComposer({ replyTo: request.replyTo || null });
  return true;
}

// Reply links the next message to this one and puts the cursor in the message box.
export function replyToRemMessage(reference) {
  updateRemComposer({ replyTo: reference });
  focusRemComposer();
}
