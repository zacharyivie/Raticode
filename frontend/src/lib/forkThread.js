import { apiUrl } from "./api.js";

export async function copyForkAttachments(sourceThreadId, threadId, messages, fetchImpl = fetch) {
  const attachments = [...new Map(messages.flatMap(message => message.attachments || [])
    .map(attachment => [attachment.storageName, attachment])).values()];
  for (let index = 0; index < attachments.length; index += 5) {
    const response = await fetchImpl(apiUrl("/chat/attachments/copy"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sourceThreadId, threadId, attachments: attachments.slice(index, index + 5) }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || "Could not copy this thread's attachments.");
    }
  }
}

export function forkThreadHistory(parent, history, throughId, newId, now = new Date().toISOString()) {
  const boundary = history.findIndex(message => message.id === throughId);
  if (boundary < 0) throw new Error("This message is no longer available to fork.");
  const messages = structuredClone(history.slice(0, boundary + 1));
  for (const message of messages) {
    message.running = false;
    if (message.changes) {
      message.changes.undoable = false;
      message.changes.changing = false;
      message.changes.undoUnavailableReason = "These changes belong to the original thread.";
    }
  }
  const thread = {
    id: newId, title: `${parent.title || "Thread"} (fork)`, createdAt: now, updatedAt: now,
    forkedFromThreadId: parent.id, forkedFromMessageId: throughId,
  };
  for (const field of ["provider", "model", "effort", "resources", "permissionsByProvider", "projectRoot", "projectName", "projectBranch", "workflowId", "workflowName", "scopeMode"]) {
    if (parent[field] !== undefined) thread[field] = structuredClone(parent[field]);
  }
  return { thread, messages };
}
