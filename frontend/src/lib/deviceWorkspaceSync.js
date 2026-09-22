import { apiUrl } from "./api.js";
import { inspectThreadScopes, threadIsArchived } from "./threadActivity.js";
import { conversationRepository } from "./conversationRepository.js";

const indexKey = "gofer-flow-chat-threads";
const metaKey = id => `gofer-flow-chat-thread-meta:${id}`;
const read = (storage, key, fallback) => { try { return JSON.parse(storage.getItem(key)) || fallback; } catch { return fallback; } };

// Phone updates append stable message IDs. Desktop edits made during a poll win
// over the older mirror; only an explicit phone setting change replaces settings.
export function reconcileDesktopThread(local, remote) {
  if (!local) return remote.metadata;
  const base = remote.base_metadata || remote.metadata;
  const fields = ["provider", "model", "effort", "projectRoot", "projectName", "scopeMode", "permissionsByProvider"];
  const unchanged = fields.every(key => JSON.stringify(local[key]) === JSON.stringify(base[key]));
  let result = remote.context_modified && unchanged
    ? { ...local, ...Object.fromEntries(fields.filter(key => key in remote.metadata).map(key => [key, remote.metadata[key]])) }
    : local;
  const organization = ["pinned", "archived", "updatedAt"];
  if (remote.organization_modified && organization.every(key => JSON.stringify(local[key]) === JSON.stringify(base[key]))) {
    result = { ...result, ...Object.fromEntries(organization.filter(key => key in remote.metadata).map(key => [key, remote.metadata[key]])) };
  }
  if (remote.title_modified && local.title === base.title) result = { ...result, title: remote.metadata.title };
  return result;
}

// Keep replayed diagnostics next to their originating user message, including
// turns completed before the renderer was opened. Native messages keep their order.
export function orderDeviceMessages(messages) {
  const groups = new Map();
  for (const message of messages) if (message.deviceRequestId) {
    const group = groups.get(message.deviceRequestId) || [];
    group.push(message); groups.set(message.deviceRequestId, group);
  }
  for (const group of groups.values()) group.sort((a, b) => a.deviceSequence - b.deviceSequence);
  const result = [];
  const emitted = new Set();
  const present = new Set(messages.map(message => message.id));
  for (const message of messages) {
    if (message.deviceRequestId) {
      if (present.has(message.deviceRequestId) || emitted.has(message.deviceRequestId)) continue;
      result.push(...groups.get(message.deviceRequestId));
      emitted.add(message.deviceRequestId);
      continue;
    }
    result.push(message);
    if (groups.has(message.id)) result.push(...groups.get(message.id));
  }
  return result;
}

export function mergeDeviceMessages(current, incoming) {
  const byId = new Map(current.map(message => [String(message.id), message]));
  for (const message of incoming) {
    const old = byId.get(String(message.id));
    if (!old || message.deviceRequestId) byId.set(String(message.id), { ...old, ...message });
    else if (message.origin === "phone") byId.set(String(message.id), { ...old, origin: "phone", ...(message.attachments?.length ? { attachments: message.attachments } : {}) });
  }
  return orderDeviceMessages([...byId.values()]);
}

export function startDeviceWorkspaceSync({ storage = window.localStorage, repository = conversationRepository(), interval = 5000, getContext = () => ({}), deleteThread = null } = {}) {
  const host = window;
  if (!host.goferDesktop?.workspace?.pathGrantForApi) return () => {};
  const emit = (name, detail) => { if (!stopped && host.dispatchEvent) host.dispatchEvent(new CustomEvent(name, { detail })); };
  let stopped = false;
  let timer;
  const controller = new AbortController();
  const sent = new Map();
  const peers = new Map();
  async function action(body) {
    const response = await fetch(apiUrl("/devices"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Desktop thread sync failed.");
    return result;
  }
  async function importThread(remote) {
    if (!remote.metadata || remote.revoked) return;
    const id = remote.metadata.id;
    const local = read(storage, metaKey(id), null);
    let metadata = reconcileDesktopThread(local, remote);
    const current = await repository.all(id);
    const byId = new Map(current.map(m => [String(m.id), m]));
    const merged = mergeDeviceMessages(current, remote.messages);
    const additions = merged.filter(m => JSON.stringify(m) !== JSON.stringify(byId.get(String(m.id))));
    if (additions.length) {
      await repository.save(id, merged, current, current);
      metadata = { ...metadata, updatedAt: new Date().toISOString() };
    }
    if (JSON.stringify(metadata) !== JSON.stringify(local) || additions.length || typeof remote.running === "boolean") {
      storage.setItem(metaKey(id), JSON.stringify(metadata));
      const index = read(storage, indexKey, []);
      storage.setItem(indexKey, JSON.stringify([{ id, updatedAt: metadata.updatedAt, projectRoot: metadata.projectRoot || "", scopeIndexed: true }, ...index.filter(t => t.id !== id)]));
      emit("gofer:device-thread-sync", { metadata, messages: additions, running: remote.running, activeTurn: remote.active_turn });
    }
  }
  async function tick() {
    try {
      const response = await fetch(apiUrl("/devices"), { signal: controller.signal, cache: "no-store" });
      if (!response.ok) return;
      const status = await response.json();
      for (const peer of status.workspace_peers || []) {
        if (!peers.has(peer)) peers.set(peer, { revisions: new Map(), known: {}, threads: new Map() });
        const { revisions, known, threads } = peers.get(peer);
        const snapshot = await action({ action: "workspace_poll", device_id: peer, known });
        for (const remote of snapshot.threads || []) {
          if (remote.metadata) {
            revisions.set(remote.metadata.id, remote.revision);
            threads.set(remote.metadata.id, remote.thread_id);
          }
          if (remote.delete_requested && remote.metadata) {
            if (!deleteThread) throw new Error("Open the desktop thread view to finish deleting this thread.");
            await importThread(remote);
            await deleteThread(remote.metadata.id);
            await action({ action: "workspace_remove", device_id: peer, thread_id: remote.thread_id });
            threads.delete(remote.metadata.id);
            emit("gofer:device-thread-deleted", { id: remote.metadata.id });
            continue;
          }
          const present = remote.metadata && read(storage, metaKey(remote.metadata.id), null);
          if (!present && remote.metadata && !remote.mobile_created) {
            await action({ action: "workspace_remove", device_id: peer, thread_id: remote.thread_id });
          } else await importThread(remote);
          if (remote.sync_token) known[remote.thread_id] = remote.sync_token;
        }
        for (const [native, wire] of threads) {
          if (!read(storage, metaKey(native), null)) {
            await action({ action: "workspace_remove", device_id: peer, thread_id: wire });
            threads.delete(native);
          }
        }
        const index = read(storage, indexKey, []);
        const desktop = getContext();
        const settings = desktop.settings || {};
        const memory = settings.memory || {};
        const assistant = settings.assistant || {};
        const workspace = host.goferDesktop.workspace;
        const roots = [...new Set([...(desktop.roots || []), ...index.map(entry => read(storage, metaKey(entry.id), entry)?.projectRoot)].filter(Boolean))];
        const labels = read(storage, "gofer.projectLabels", {});
        const projects = [];
        for (const root of roots.slice(0, 100)) {
          if (workspace.getPathInfo && !(await workspace.getPathInfo(root))?.isDirectory) continue;
          await workspace.trustProjectRoot?.(root);
          projects.push({ root, name: labels[root] || root.split(/[\\/]/).pop(), grantId: workspace.pathGrantForApi(root) });
        }
        if (memory.secondBrainEnabled) await workspace.trustProjectRoot?.(memory.secondBrainRoot);
        const scopes = await inspectThreadScopes(index.map(entry => read(storage, metaKey(entry.id), entry)), workspace);
        for (const entry of index) {
          if (stopped) return;
          const metadata = read(storage, metaKey(entry.id), typeof entry.title === "string" ? entry : null);
          if (!metadata) continue;
          const workflow = {
            remThreads: { projects }, remResources: metadata.resources || assistant.resources || {},
            remSwarmAccess: { enabled: assistant.swarmAccessEnabled !== false,
              grantId: workspace.pathGrantForApi(metadata.projectRoot),
              workspaceGrants: Object.fromEntries(projects.filter(p => p.grantId).map(p => [p.root, p.grantId])) },
            remSecondBrain: { enabled: memory.secondBrainEnabled === true, root: memory.secondBrainRoot,
              format: memory.secondBrainFormat, theme: memory.secondBrainTheme,
              grantId: workspace.pathGrantForApi(memory.secondBrainRoot) },
          };
          const mobileGroup = metadata.pinned && !metadata.archived ? "pinned" : threadIsArchived(metadata, scopes.missingRoots, scopes.branches) ? "archived" : "active";
          const sharedMetadata = { ...metadata, mobileGroup };
          const fingerprint = JSON.stringify({ metadata, workflow, mobileGroup, revision: revisions.get(entry.id) });
          const key = `${peer}:${entry.id}`;
          if (sent.get(key) === fingerprint) continue;
          const messages = (await repository.all(entry.id)).filter(m => ["user", "assistant", "system"].includes(m.role)).map(m => ({ id: String(m.id), role: m.role, body: m.body, ...Object.fromEntries(["kind", "groupId", "trace", "running", "startedAt", "completedAt", "durationMs", "attachments"].filter(key => m[key] != null).map(key => [key, m[key]])) }));
          const result = await action({ action: "workspace_exchange", device_id: peer, metadata: sharedMetadata, workflow, messages,
            revision: revisions.get(entry.id), grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(metadata.projectRoot) });
          await importThread(result);
          revisions.set(entry.id, result.revision);
          sent.set(key, JSON.stringify({ metadata: read(storage, metaKey(entry.id), metadata), workflow, mobileGroup, revision: result.revision }));
        }
      }
      emit("gofer:device-sync-status", "");
    } catch (error) {
      emit("gofer:device-sync-status", error.message);
    } finally { if (!stopped) timer = setTimeout(tick, interval); }
  }
  tick();
  return () => { stopped = true; clearTimeout(timer); controller.abort(); };
}
