// Desktop notifications for things that finish while Raticode is in the background:
// a thread's turn, Rem's reply, an approval Rem waits on in a tab, and Rem tasks.
// Electron grants web notifications; a browser only shows them once allowed.
// A notice names the thread or tab, never the agent's words, like the phone's default.

let enabled = true;
// Tags already shown, so a thread notifies once per turn and an approval once per request.
const shownTags = new Set();
const REMEMBERED_TAGS = 500;

// Settings > General > Notify me when Raticode is in the background.
export function setBackgroundNotices(on) { enabled = on !== false; }

// True while a notice would show: the switch is on, notifications are allowed and
// the Raticode window isn't focused.
export function backgroundNoticesAllowed() {
  return enabled && globalThis.Notification?.permission === "granted" && globalThis.document?.hasFocus?.() === false;
}

export function showBackgroundNotice({ title, body = "", tag = "", open }) {
  if (!title || !backgroundNoticesAllowed() || (tag && shownTags.has(tag))) return null;
  try {
    const shown = new globalThis.Notification(title, { body, tag: tag || undefined });
    if (tag) {
      shownTags.add(tag);
      if (shownTags.size > REMEMBERED_TAGS) shownTags.delete(shownTags.values().next().value);
    }
    shown.onclick = () => {
      globalThis.focus?.();
      open?.();
    };
    return shown;
  } catch {
    // Notifications are a convenience; the result is already in Raticode.
    return null;
  }
}

// A thread's turn ended: "done" or "failed". Clicking opens the thread.
export function notifyThreadTurn({ threadId, title, outcome, turn }) {
  if (!threadId || (outcome !== "done" && outcome !== "failed")) return null;
  const name = String(title || "").trim() || "a thread";
  return showBackgroundNotice({
    title: outcome === "failed" ? `Turn failed in ${name}` : `Reply in ${name}`,
    tag: `thread-${threadId}-${turn || ""}`,
    open: () => window.dispatchEvent(new CustomEvent("gofer:rem-open-thread", { detail: { threadId } })),
  });
}

// Rem's newest replies in its own chat after a poll, given the messages shown before.
// Task updates are left to the Rem task notices.
export function newRemReplies(previous, messages) {
  if (!Array.isArray(previous) || !Array.isArray(messages)) return [];
  const known = new Set(previous.map(message => message?.id));
  return messages.filter(message => message?.id && message.role === "rem" && message.kind !== "task_update" && !message.pending && !known.has(message.id));
}

// Rem approval requests in tabs that weren't there before, given the request IDs seen.
export function newRemApprovals(seen, approvals) {
  return (approvals || []).filter(item => item?.requestId && !seen.has(item.requestId));
}
