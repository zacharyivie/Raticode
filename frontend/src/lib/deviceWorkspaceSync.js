import { mergeDeviceMessages, phoneSettings } from "./deviceMessages.js";
import { reportThemeContext } from "./reportThemes.js";
import { apiUrl } from "./api.js";
import { inspectThreadScopes, organizeThreads, threadIndexEntry } from "./threadActivity.js";
import { conversationRepository } from "./conversationRepository.js";

const indexKey = "gofer-flow-chat-threads";
const metaKey = id => `gofer-flow-chat-thread-meta:${id}`;
const read = (storage, key, fallback) => { try { return JSON.parse(storage.getItem(key)) || fallback; } catch { return fallback; } };

// Phone updates append stable message IDs. Desktop edits made during a poll win
// over the older mirror; only an explicit phone setting change replaces settings.
export function reconcileDesktopThread(local, remote) {
  if (!local) return remote.metadata;
  const base = remote.base_metadata || remote.metadata;
  const fields = ["provider", "model", "effort", "projectRoot", "projectName", "scopeMode", "permissionsByProvider", "resources"];
  const unchanged = fields.every(key => JSON.stringify(local[key]) === JSON.stringify(base[key]));
  let result = remote.context_modified && unchanged
    ? { ...local, ...Object.fromEntries(fields.filter(key => key in remote.metadata).map(key => [key, remote.metadata[key]])) }
    : local;
  // Placement and read state reconcile separately: reading on the phone must not
  // undo a desktop pin, and a desktop read must not be undone by the phone.
  for (const organization of [["pinned", "archived", "updatedAt"], ["unread", "readAt"]]) {
    if (remote.organization_modified && organization.every(key => JSON.stringify(local[key]) === JSON.stringify(base[key]))) {
      result = { ...result, ...Object.fromEntries(organization.filter(key => key in remote.metadata).map(key => [key, remote.metadata[key]])) };
    }
  }
  if (remote.title_modified && local.title === base.title) result = { ...result, title: remote.metadata.title };
  return result;
}

// Re-exported for existing callers; App loads only deviceMessages.js eagerly.
export { applyPhoneSettings, mergeDeviceMessages, orderDeviceMessages, phoneSettings } from "./deviceMessages.js";
// A Rem task thread changes on nearly every poll while its worker runs, and each
// exchange sends its whole transcript, which the backend decrypts, stores and
// returns. The phone shows live task progress from Rem itself, so while a task
// thread keeps running in the same place, its shared copy is refreshed at most
// this often. Starting, finishing and moving between groups go at once.
export const RUNNING_TASK_RESEND_MS = 30_000;
// A thread the desktop refuses to share, such as one too long for the phone, is
// retried when it changes or after this long. It used to be retried every 5
// seconds, and it stopped every thread after it.
export const REFUSED_THREAD_RETRY_MS = 10 * 60_000;
// Once a phone's history is full, every thread that grows is refused, so after one
// such refusal only threads that don't grow are sent to that phone for this long.
// Retrying each thread on its own made about 150 refusals after every restart and
// one for each change of a running task.
export const FULL_PHONE_RETRY_MS = REFUSED_THREAD_RETRY_MS;
const PHONE_FULL = "workspace_history_quota";

export function startDeviceWorkspaceSync({ storage = window.localStorage, repository = conversationRepository(), interval = 5000, getContext = () => ({}), deleteThread = null, now = Date.now } = {}) {
  const host = window;
  if (!host.goferDesktop?.workspace?.pathGrantForApi) return () => {};
  const emit = (name, detail) => { if (!stopped && host.dispatchEvent) host.dispatchEvent(new CustomEvent(name, { detail })); };
  let stopped = false;
  let timer;
  const controller = new AbortController();
  const sent = new Map();
  // When each thread was last sent, and whether it was running and in which group.
  const sentState = new Map();
  const peers = new Map();
  const runningByThread = new Map();
  // Threads the desktop refused to share: the fingerprint refused, when, and why.
  const refused = new Map();
  // Phones whose history was full: when the desktop last heard so, and the reason.
  const full = new Map();
  // The size of what was last sent for each thread, so a full phone still gets
  // changes that don't grow a thread, such as pinning or reading it.
  const sentSize = new Map();
  // Rem home reports when its task threads run. Without this, a running task
  // thread past the Read limit would reach the phone as archived.
  const runningTaskThreads = new Set();
  const taskThreadSync = ({ detail }) => {
    if (!detail?.metadata?.managedRunId || typeof detail.running !== "boolean") return;
    if (detail.running) runningTaskThreads.add(detail.metadata.id);
    else runningTaskThreads.delete(detail.metadata.id);
  };
  host.addEventListener?.("gofer:device-thread-sync", taskThreadSync);
  let appliedSettings = [];
  // Pages phones sent to this window's browser: opened ones are acknowledged on the
  // next exchange, and remembered so a repeated offer never opens a second tab.
  let openedPages = [];
  const openedPageIds = new Set();
  async function action(body) {
    const response = await fetch(apiUrl("/devices"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Desktop thread sync failed.");
      error.status = response.status;
      error.code = result.code;
      throw error;
    }
    return result;
  }
  async function openProjects() {
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
    return { index, desktop, settings, memory, assistant, workspace, projects };
  }
  async function importThread(remote) {
    if (!remote.metadata || remote.revoked) return;
    const id = remote.metadata.id;
    const local = read(storage, metaKey(id), null);
    let metadata = reconcileDesktopThread(local, remote);
    // Rem home runs task threads and reports their state. The phone mirror
    // never runs them, so its "not running" would flip them to unread.
    const running = metadata.managedRunId ? undefined : remote.running;
    const current = await repository.all(id);
    const byId = new Map(current.map(m => [String(m.id), m]));
    const merged = mergeDeviceMessages(current, remote.messages);
    const additions = merged.filter(m => JSON.stringify(m) !== JSON.stringify(byId.get(String(m.id))));
    if (additions.length) {
      await repository.save(id, merged, current, current);
    }
    const submitted = additions.some(m => m.role === "user" && !byId.has(String(m.id)));
    const completed = running === false && (runningByThread.get(id) === true
      || additions.some(m => m.kind === "final" || m.kind === "error" || (m.kind === "turn-summary" && m.completedAt)));
    if (typeof running === "boolean") runningByThread.set(id, running);
    // A turn sent or finished from another device is new activity, which brings
    // an archived thread back. The window marks a finished turn unread.
    if (submitted || completed) {
      metadata = { ...metadata, archived: false, updatedAt: new Date().toISOString() };
    }
    if (JSON.stringify(metadata) !== JSON.stringify(local) || additions.length || typeof running === "boolean") {
      storage.setItem(metaKey(id), JSON.stringify(metadata));
      const index = read(storage, indexKey, []);
      const entry = threadIndexEntry(metadata);
      const nextIndex = index.some(t => t.id === id) ? index.map(t => t.id === id ? entry : t) : [entry, ...index];
      storage.setItem(indexKey, JSON.stringify(nextIndex.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")))));
      emit("gofer:device-thread-sync", { metadata, messages: additions, running, activeTurn: remote.active_turn, completed });
    }
  }
  // The status only shows while Settings is open; the log keeps each new problem once.
  // The cause decides what is new, so a changing thread count doesn't log again.
  let reported = "";
  function report(message, { warn = true, cause = message } = {}) {
    emit("gofer:device-sync-status", message);
    if (message && cause !== reported && warn) console.warn(`Phone thread sync: ${message}`);
    reported = cause;
  }
  // Removing a thread frees space, so a full phone gets another try at once.
  async function remove(peer, thread) {
    await action({ action: "workspace_remove", device_id: peer, thread_id: thread });
    if (full.delete(peer)) for (const key of refused.keys()) if (key.startsWith(`${peer}:`)) refused.delete(key);
  }
  async function tick() {
    // Threads this round left off a full phone, or behind on it, until it has room.
    const waiting = new Set();
    try {
      const response = await fetch(apiUrl("/devices"), { signal: controller.signal, cache: "no-store" });
      if (!response.ok) {
        // 4xx means paired devices are off or not allowed here, which is routine.
        report(`Desktop device status unavailable (HTTP ${response.status})`, { warn: response.status >= 500 });
        return;
      }
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
            await remove(peer, remote.thread_id);
            threads.delete(remote.metadata.id);
            emit("gofer:device-thread-deleted", { id: remote.metadata.id });
            continue;
          }
          const present = remote.metadata && read(storage, metaKey(remote.metadata.id), null);
          if (!present && remote.metadata && !remote.mobile_created) {
            await remove(peer, remote.thread_id);
          } else await importThread(remote);
          if (remote.sync_token) known[remote.thread_id] = remote.sync_token;
        }
        for (const [native, wire] of threads) {
          if (!read(storage, metaKey(native), null)) {
            await remove(peer, wire);
            threads.delete(native);
          }
        }
        const { index, settings, memory, assistant, workspace, projects } = await openProjects();
        const stored = index.map(entry => read(storage, metaKey(entry.id), entry));
        const scopes = await inspectThreadScopes(stored, workspace);
        const running = new Set([...runningTaskThreads, ...[...runningByThread].filter(([, active]) => active).map(([id]) => id)]);
        const groups = organizeThreads(stored, { running, ...scopes });
        const pinnedIds = new Set(groups.pinned.map(entry => entry.id));
        const archivedIds = new Set(groups.archived.map(entry => entry.id));
        const listed = new Set(index.map(entry => `${peer}:${entry.id}`));
        for (const map of [refused, sentSize]) for (const key of map.keys()) if (key.startsWith(`${peer}:`) && !listed.has(key)) map.delete(key);
        for (const entry of index) {
          if (stopped) return;
          const metadata = read(storage, metaKey(entry.id), typeof entry.title === "string" ? entry : null);
          if (!metadata) continue;
          const workflow = {
            remThreads: { projects }, remResources: metadata.resources || assistant.resources || {},
            remSwarmAccess: { enabled: assistant.swarmAccessEnabled !== false,
              grantId: workspace.pathGrantForApi(metadata.projectRoot),
              workspaceGrants: Object.fromEntries(projects.filter(p => p.grantId).map(p => [p.root, p.grantId])) },
            remReportTheme: reportThemeContext(memory),
            remBrowser: { target: settings.browser?.agentBrowser || "raticode" },
            remSecondBrain: { enabled: memory.secondBrainEnabled === true, root: memory.secondBrainRoot,
              format: memory.secondBrainFormat,
              grantId: workspace.pathGrantForApi(memory.secondBrainRoot) },
          };
          const mobileGroup = pinnedIds.has(entry.id) ? "pinned" : archivedIds.has(entry.id) ? "archived" : "active";
          const sharedMetadata = { ...metadata, mobileGroup };
          const fingerprint = JSON.stringify({ metadata, workflow, mobileGroup, revision: revisions.get(entry.id) });
          const key = `${peer}:${entry.id}`;
          if (sent.get(key) === fingerprint) continue;
          const last = sentState.get(key);
          if (metadata.managedRunId && running.has(entry.id) && last?.running && last.group === mobileGroup
            && now() - last.at < RUNNING_TASK_RESEND_MS) continue;
          const refusal = refused.get(key);
          if (refusal?.fingerprint === fingerprint && now() - refusal.at < REFUSED_THREAD_RETRY_MS) continue;
          const phoneFull = full.has(peer) && now() - full.get(peer).at < FULL_PHONE_RETRY_MS;
          // Without an earlier size there is nothing to compare, and it would be refused.
          if (phoneFull && !sentSize.has(key)) { waiting.add(key); continue; }
          const messages = (await repository.all(entry.id)).filter(m => ["user", "assistant", "system"].includes(m.role)).map(m => ({ id: String(m.id), role: m.role, body: m.body, ...Object.fromEntries(["kind", "groupId", "trace", "running", "startedAt", "completedAt", "durationMs", "attachments"].filter(key => m[key] != null).map(key => [key, m[key]])) }));
          const size = JSON.stringify({ metadata: sharedMetadata, workflow, messages }).length;
          if (phoneFull && size > sentSize.get(key)) { waiting.add(key); continue; }
          let result;
          try {
            result = await action({ action: "workspace_exchange", device_id: peer, metadata: sharedMetadata, workflow, messages,
              revision: revisions.get(entry.id), grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(metadata.projectRoot) });
          } catch (error) {
            // The desktop refused this thread (HTTP 400). Skip it and keep syncing
            // the rest; network and server failures still stop this round.
            if (error?.status !== 400) throw error;
            refused.set(key, { fingerprint, at: now(), message: error.message });
            if (error.code === PHONE_FULL) full.set(peer, { at: now(), message: error.message });
            continue;
          }
          refused.delete(key);
          sentSize.set(key, size);
          await importThread(result);
          revisions.set(entry.id, result.revision);
          sent.set(key, JSON.stringify({ metadata: read(storage, metaKey(entry.id), metadata), workflow, mobileGroup, revision: result.revision }));
          sentState.set(key, { at: now(), running: running.has(entry.id), group: mobileGroup });
        }
      }
      // Paired desktops this one can run threads on, for the composer's Run on menu.
      emit("gofer:device-machines", (status.peers || [])
        .filter(peer => peer.role === "desktop" && peer.state === "active")
        .map(peer => ({ id: peer.device_id, name: peer.name || "Paired desktop" })));
      // Desktops allowed to run threads here see this computer's open projects.
      if ((status.thread_host_peers || []).length) {
        const { syncThreadHosting } = await import("./deviceThreadHosting.js");
        await syncThreadHosting(status.thread_host_peers, await openProjects(), action, sent);
      }
      if ((status.desktop_control_peers || []).length) {
        const desktop = getContext();
        if (desktop.settings) {
          const { changes = [] } = await action({ action: "settings_exchange", settings: phoneSettings(desktop.settings), applied: appliedSettings });
          // Applying is idempotent; acknowledge after the window saved the change.
          appliedSettings = [];
          if (changes.length && desktop.updateSettings) {
            desktop.updateSettings(changes);
            appliedSettings = changes.map(change => change.id);
          }
        }
        if (desktop.browser) {
          const { opens = [] } = await action({ action: "browser_exchange", tabs: desktop.browser.tabs(), search_url: desktop.browser.searchUrl || "", engine: desktop.browser.engine || "raticode", opened: openedPages });
          openedPages = [];
          for (const page of opens) {
            if (typeof page?.id !== "string" || typeof page.url !== "string") continue;
            if (!openedPageIds.has(page.id)) {
              openedPageIds.add(page.id);
              if (openedPageIds.size > 200) openedPageIds.delete(openedPageIds.values().next().value);
              desktop.browser.open(page.url);
            }
            openedPages.push(page.id);
          }
        }
      }
      const behind = new Set([...refused.keys(), ...waiting]).size;
      const reason = [...full.values()].find(entry => now() - entry.at < FULL_PHONE_RETRY_MS)?.message || refused.values().next().value?.message;
      report(behind
        ? `${reason} ${behind === 1 ? "1 thread isn't" : `${behind} threads aren't`} up to date on the phone.`
        : "", { cause: behind ? reason : "" });
    } catch (error) {
      if (stopped || error?.name === "AbortError") return;
      report(error.message);
    } finally { if (!stopped) timer = setTimeout(tick, interval); }
  }
  tick();
  return () => { stopped = true; clearTimeout(timer); controller.abort(); host.removeEventListener?.("gofer:device-thread-sync", taskThreadSync); };
}
