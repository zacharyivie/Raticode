// Multiple-thread selection for the thread list. The same rules drive the
// desktop list and are mirrored by the phone's ThreadSelection.kt.

export const emptyThreadSelection = Object.freeze({ ids: new Set(), anchor: null });

// A click selects one thread, Ctrl or Cmd toggles one, and Shift extends from
// the last thread clicked through the threads in display order.
export function nextThreadSelection({ ids, anchor }, id, order, { toggle = false, range = false } = {}) {
  if (range && anchor && order.includes(anchor) && order.includes(id)) {
    const [from, to] = [order.indexOf(anchor), order.indexOf(id)].sort((a, b) => a - b);
    return { ids: new Set([...(toggle ? ids : []), ...order.slice(from, to + 1)]), anchor };
  }
  if (toggle) {
    const next = new Set(ids);
    if (next.has(id)) next.delete(id); else next.add(id);
    return { ids: next, anchor: id };
  }
  return { ids: new Set([id]), anchor: id };
}

// Activating a section header selects every thread in it; doing it again
// while exactly those threads are selected clears the selection.
export function selectThreadSection({ ids }, sectionIds) {
  const exact = sectionIds.length > 0 && ids.size === sectionIds.length && sectionIds.every(id => ids.has(id));
  return exact ? { ids: new Set(), anchor: null } : { ids: new Set(sectionIds), anchor: sectionIds[0] ?? null };
}

// Threads that disappeared, such as deleted ones, leave the selection.
export function presentThreadSelection(selection, present) {
  const ids = new Set([...selection.ids].filter(id => present.has(id)));
  return ids.size === selection.ids.size ? selection : { ids, anchor: present.has(selection.anchor) ? selection.anchor : null };
}

// Only actions that would change at least one selected thread are offered.
export function threadSelectionActions(threads, { isUnread = thread => Boolean(thread.unread) } = {}) {
  const any = test => threads.some(test);
  return [
    any(isUnread) && { action: "read", label: "Mark as read" },
    any(thread => !isUnread(thread)) && { action: "unread", label: "Mark as unread" },
    any(thread => !thread.pinned) && { action: "pin", label: "Pin" },
    any(thread => thread.pinned) && { action: "unpin", label: "Unpin" },
    any(thread => !thread.archived) && { action: "archive", label: "Archive" },
    any(thread => thread.archived) && { action: "restore", label: "Restore" },
    threads.length > 0 && { action: "delete", label: "Delete", danger: true },
  ].filter(Boolean);
}

// The change each action makes to a thread's saved state. Archiving is a
// decision about the thread, so it also marks it read.
export function threadActionPatch(action, now = new Date().toISOString()) {
  const read = { unread: false, readAt: now };
  return {
    read,
    unread: { unread: true },
    pin: { pinned: true, archived: false },
    unpin: { pinned: false },
    archive: { archived: true, pinned: false, ...read },
    restore: { archived: false },
  }[action] || null;
}
