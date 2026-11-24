// Workflow shortcuts: a system-wide key that runs one workflow from any app while
// Raticode is running. The backend keeps the list (gofer.rattish.workflow_shortcuts,
// shared with `Raticode rattish shortcuts`); this registers the keys and does what a
// press asks:
//
// - A workflow with no text input runs straight away, as a run the user started.
// - A workflow with a plain text input opens the Run sheet (workflow-run-sheet.cjs),
//   filled from the clipboard, with a warning that copied text can hold instructions
//   the user didn't write. Enter runs it.
// - A desktop notice says when the run started and reports its result; clicking it
//   opens the run in Raticode.
// - A shortcut runs only the workflow version the user approved. When the workflow or
//   a file it runs as code changed, nothing starts: a notice says so and clicking it
//   opens Review changes for that shortcut in Settings > Devices.
//
// Keys are registered like the dictation shortcuts: Electron's globalShortcut on
// Windows, macOS and X11, and the desktop's own shortcut service on a Wayland session
// (linux-shortcuts.cjs: KDE, the portal, GNOME, or a command bound by hand). A key the
// dictation shortcuts use is never taken: they come first.

const { toAccelerator } = require("./global-dictation.cjs");

const DESKTOP_PREFIX = "workflow-";
const RUN_FLAG = "--run-workflow-shortcut";
const ID_PATTERN = /^[a-f0-9]{12}$/;
// The Run sheet takes at most this much text, the same limit the backend keeps.
const MAX_TEXT_CHARS = 100_000;
// How often to look at a run: every 2 s for the first half minute, then every 5 s,
// then every 15 s after five minutes. A run longer than a day isn't followed.
const POLL_STEPS = [[30_000, 2_000], [300_000, 5_000], [Infinity, 15_000]];
const FOLLOW_LIMIT_MS = 24 * 60 * 60 * 1000;
// How often the list is read again, so `rattish shortcuts remove` takes effect.
const RESYNC_MS = 30_000;
const CAUTION = "Copied text can contain instructions you didn't write. Read it before you run: the workflow treats it as your input.";

const DESKTOP_PROBLEMS = {
  approval: "Waiting for you to allow it in your desktop's prompt.",
  declined: "Your desktop's request to add this shortcut was declined.",
  unassigned: "Your desktop added this shortcut without a key. Choose one in your desktop's shortcut settings.",
  manual: "Your desktop doesn't let apps add shortcuts. Bind the command below to a key in its keyboard settings.",
};

function desktopId(id) {
  return `${DESKTOP_PREFIX}${id}`;
}

/** The shortcut ID a `--run-workflow-shortcut=ID` argument names, or "". */
function shortcutFromArgv(argv) {
  if (!Array.isArray(argv)) return "";
  for (const arg of argv) {
    const text = String(arg);
    if (!text.startsWith(`${RUN_FLAG}=`)) continue;
    const id = text.slice(RUN_FLAG.length + 1);
    return ID_PATTERN.test(id) ? id : "";
  }
  return "";
}

function pollDelay(elapsed) {
  for (const [until, delay] of POLL_STEPS) if (elapsed < until) return delay;
  return POLL_STEPS.at(-1)[1];
}

// The backend's answer when a press finds the workflow changed since it was approved.
const CHANGED_CODE = "shortcut_changed";
// Approval states that keep a shortcut from running until the user reviews the change.
const NEEDS_REVIEW = new Set(["changed", "unapproved"]);

/** The notice's title, the same words as the backend's (workflow_shortcuts.changed_title). */
function changedTitle(name, state) {
  const workflow = name || "The workflow";
  return state === "unapproved"
    ? `${workflow} needs your approval before its shortcut runs it`
    : `${workflow} changed since you added its shortcut`;
}

function message(error) {
  return String(error instanceof Error ? error.message : error || "").slice(0, 300);
}

/**
 * deps:
 *   globalShortcut     Electron's globalShortcut (or a test double)
 *   desktopShortcuts   createLinuxShortcuts() on a Wayland session, else null
 *   api(method, path, body)  resolves to the backend's JSON; rejects with an Error whose
 *                      .status is the HTTP status, or none when the backend isn't there
 *   readClipboard()    the clipboard's text
 *   sheet              { open(state, { onRun(text), onCancel() }), update(state), close(), isOpen(id) }
 *   notify({ title, body, onClick })  shows a desktop notice; returns { close() } or null
 *   openRun(run)       shows Raticode and opens { workflowId, runId, status, projectRoot, workflowName }
 *   openReview(review) shows Raticode and opens Review changes for { shortcutId, workflowName }
 *   showApp()          shows Raticode's window
 *   commandFor(id)     the command a desktop's keyboard settings can run for shortcut id
 */
function createWorkflowShortcuts({
  globalShortcut,
  desktopShortcuts = null,
  api,
  readClipboard = () => "",
  sheet = null,
  notify = () => null,
  openRun = () => {},
  openReview = () => {},
  showApp = () => {},
  commandFor = () => "",
  now = Date.now,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  // id -> { id, workflowId, workflowName, binding, accelerator, error, registered, desktop, lastPressedAt }
  let entries = new Map();
  // accelerator -> the feature that has it, such as "Dictate to Rem from any app".
  let reserved = new Map();
  let suspended = false;
  let loadError = "";
  let disposed = false;
  let resyncTimer = null;
  // Shortcut ids whose press is being handled, so a held key doesn't start twice.
  const busy = new Set();
  // Runs being followed: runId -> { timer }
  const followed = new Map();
  let lastList = [];

  function status() {
    return {
      support: desktopShortcuts ? "desktop" : "native",
      suspended,
      error: loadError,
      shortcuts: [...entries.values()].map((entry) => {
        const desktopState = entry.desktop?.state || "";
        return {
          id: entry.id,
          workflowId: entry.workflowId,
          workflowName: entry.workflowName,
          binding: entry.binding,
          accelerator: entry.accelerator,
          registered: desktopShortcuts ? desktopState === "registered" && !entry.error : entry.registered,
          error: entry.error || (desktopShortcuts ? desktopProblem(entry.desktop) : ""),
          state: desktopState,
          route: entry.desktop?.route || "",
          trigger: entry.desktop?.trigger || "",
          command: desktopShortcuts && ["manual", "declined", "failed"].includes(desktopState) ? commandFor(entry.id) : "",
          lastPressedAt: entry.lastPressedAt || 0,
        };
      }),
    };
  }

  function desktopProblem(desktop) {
    if (!desktop?.state) return "";
    if (desktop.state === "failed") return desktop.message || "Your desktop didn't add this shortcut.";
    if (desktop.state === "unassigned") return desktop.message || DESKTOP_PROBLEMS.unassigned;
    return DESKTOP_PROBLEMS[desktop.state] || "";
  }

  function unregisterNative(entry) {
    if (!entry?.registered || !entry.accelerator) return;
    try { globalShortcut.unregister(entry.accelerator); } catch { /* Already gone. */ }
    entry.registered = false;
  }

  /** Applies a list from the backend. Returns status(), or a promise of it on Wayland. */
  function apply(list = []) {
    lastList = Array.isArray(list) ? list : [];
    const next = new Map();
    const taken = new Map();
    for (const item of lastList) {
      if (!item || !ID_PATTERN.test(String(item.id || ""))) continue;
      const previous = entries.get(item.id);
      const wanted = toAccelerator(item.binding);
      const entry = {
        id: item.id,
        workflowId: String(item.workflowId || ""),
        workflowName: String(item.workflowName || item.workflowId || "Workflow").slice(0, 120),
        binding: String(item.binding || ""),
        accelerator: wanted.accelerator || "",
        error: wanted.error || "",
        registered: false,
        desktop: previous?.desktop || null,
        lastPressedAt: previous?.lastPressedAt || 0,
      };
      if (!entry.error && item.available === false) {
        entry.error = "This workflow isn't in an open project any more. Open its project, or remove the shortcut.";
      }
      if (!entry.error && reserved.has(entry.accelerator)) {
        entry.error = `${reserved.get(entry.accelerator)} uses this key. Choose another key for this workflow.`;
      }
      if (!entry.error && taken.has(entry.accelerator)) {
        entry.error = `The shortcut for ${taken.get(entry.accelerator)} already uses this key.`;
      }
      if (!entry.error) taken.set(entry.accelerator, entry.workflowName);
      next.set(entry.id, entry);
    }
    const previous = entries;
    entries = next;
    if (desktopShortcuts) return applyDesktop(previous);
    applyNative(previous);
    return status();
  }

  function applyNative(previous) {
    for (const [id, old] of previous) {
      const entry = entries.get(id);
      // Keep a key that stays the same, so a press in progress isn't lost.
      if (entry && old.registered && !entry.error && !suspended && entry.accelerator === old.accelerator) {
        entry.registered = true;
        continue;
      }
      unregisterNative(old);
    }
    if (suspended) return;
    for (const entry of entries.values()) {
      if (entry.error || entry.registered || !entry.accelerator) continue;
      let ok = false;
      try {
        ok = globalShortcut.register(entry.accelerator, () => void press(entry.id));
      } catch (failure) {
        entry.error = message(failure);
      }
      if (ok) entry.registered = true;
      else if (!entry.error) entry.error = "Another app already uses this shortcut, or the system reserves it. Choose a different key.";
    }
  }

  function applyDesktop(previous) {
    const updates = [];
    for (const id of previous.keys()) {
      const entry = entries.get(id);
      if (!entry || entry.error) {
        updates.push(desktopShortcuts.update(desktopId(id), null).catch(() => {}));
        if (entry) entry.desktop = null;
      }
    }
    for (const entry of entries.values()) {
      if (entry.error) continue;
      if (!entry.desktop) entry.desktop = { state: "working" };
      const shown = { description: `Run ${entry.workflowName}`, accelerator: entry.accelerator };
      updates.push(desktopShortcuts.update(desktopId(entry.id), shown).then((result) => {
        if (entries.get(entry.id) === entry) entry.desktop = { ...result };
      }, (failure) => {
        if (entries.get(entry.id) === entry) entry.desktop = { state: "failed", message: message(failure) };
      }));
    }
    return Promise.all(updates).then(() => status());
  }

  /** The desktop answered later (a prompt, a key changed in its settings). */
  function desktopChanged(linux = {}) {
    if (!desktopShortcuts) return status();
    for (const entry of entries.values()) {
      const shortcut = linux.shortcuts?.[desktopId(entry.id)];
      if (shortcut && !entry.error) entry.desktop = { route: linux.route, ...shortcut };
    }
    return status();
  }

  /** Keys other Raticode features use: { accelerator: label }. Applied at once. */
  function reserve(keys = {}) {
    const next = new Map(Object.entries(keys).filter(([key, label]) => key && label));
    const same = next.size === reserved.size && [...next].every(([key, label]) => reserved.get(key) === label);
    if (same) return status();
    reserved = next;
    // Let go of a clashing key before the dictation shortcut asks for it.
    for (const entry of entries.values()) {
      if (reserved.has(entry.accelerator)) unregisterNative(entry);
    }
    return apply(lastList);
  }

  /** Reads the list from the backend and registers it. Resolves to status(). */
  async function sync() {
    if (disposed) return status();
    try {
      const payload = await api("GET", "/api/workflow-shortcuts");
      loadError = "";
      const list = Array.isArray(payload?.shortcuts) ? payload.shortcuts : [];
      // Nothing changed: keep what is registered as it is.
      if (JSON.stringify(list) === JSON.stringify(lastList) && entries.size === list.length) return status();
      return await apply(list);
    } catch (failure) {
      // An unreachable backend keeps the keys it had; the next sync tries again.
      loadError = message(failure);
      return status();
    } finally {
      scheduleResync();
    }
  }

  function scheduleResync() {
    clearTimer(resyncTimer);
    if (disposed) return;
    resyncTimer = setTimer(() => { resyncTimer = null; void sync(); }, RESYNC_MS);
    resyncTimer?.unref?.();
  }

  /** While Settings records a key, the shortcuts let keys through. */
  function suspend(on) {
    suspended = on === true;
    if (desktopShortcuts) return status();
    if (suspended) {
      for (const entry of entries.values()) unregisterNative(entry);
      return status();
    }
    return apply(lastList);
  }

  // --- A press ---------------------------------------------------------------------------

  /** The key was pressed, or `--run-workflow-shortcut=ID` ran. Resolves to whether it acted. */
  async function press(id) {
    if (disposed || !ID_PATTERN.test(String(id || ""))) return false;
    const entry = entries.get(id);
    if (entry) entry.lastPressedAt = now();
    if (sheet?.isOpen?.(id)) {
      sheet.focus?.();
      return true;
    }
    if (busy.has(id)) return false;
    busy.add(id);
    try {
      let shortcut;
      try {
        shortcut = (await api("GET", `/api/workflow-shortcuts/${id}`))?.shortcut;
      } catch (failure) {
        if (failure?.status === 404) {
          void sync();
          return false;
        }
        notice({ title: "Raticode couldn't run the workflow", body: message(failure) || "Raticode's backend isn't ready yet. Try again in a moment." });
        return false;
      }
      const target = shortcut?.target || {};
      const name = target.workflowName || shortcut?.workflowName || entry?.workflowName || "the workflow";
      const approval = shortcut?.approval?.state;
      if (NEEDS_REVIEW.has(approval)) {
        changedNotice(id, name, approval);
        return false;
      }
      if (target.mode === "run") return await start(id, name, {});
      if (target.mode === "text") {
        openSheet(id, name, target);
        return true;
      }
      notice({ title: `Can't run ${name}`, body: target.problem || "This shortcut can't run its workflow now.", onClick: showApp });
      return false;
    } finally {
      busy.delete(id);
    }
  }

  function openSheet(id, name, target) {
    let text;
    try { text = String(readClipboard() ?? ""); } catch { text = ""; }
    const cut = text.length > MAX_TEXT_CHARS;
    const state = {
      id,
      workflowName: name,
      inputLabel: target.inputLabel || target.input || "Text",
      text: cut ? text.slice(0, MAX_TEXT_CHARS) : text,
      cut,
      caution: CAUTION,
      busy: false,
      error: "",
    };
    sheet?.open(state, {
      // A Wayland desktop may only hand the clipboard to the app in front, so an empty
      // clipboard is read again once the sheet has the focus.
      onFocus: () => {
        if (state.text || state.refilled) return;
        state.refilled = true;
        let again;
        try { again = String(readClipboard() ?? ""); } catch { again = ""; }
        if (!again) return;
        const longer = again.length > MAX_TEXT_CHARS;
        sheet.update({ text: again.slice(0, MAX_TEXT_CHARS), cut: longer, refill: true });
      },
      onRun: async (value) => {
        sheet.update({ busy: true, error: "" });
        const result = await start(id, name, { text: String(value ?? "") }, { quiet: true });
        if (result) sheet.close();
        else if (sheet.isOpen?.(id) !== false) sheet.update({ busy: false, error: lastStartError || "The workflow didn't start." });
        return result;
      },
      onCancel: () => sheet.close(),
    });
  }

  let lastStartError = "";
  async function start(id, name, body, { quiet = false } = {}) {
    lastStartError = "";
    let run;
    try {
      run = (await api("POST", `/api/workflow-shortcuts/${id}/run`, body))?.run;
    } catch (failure) {
      lastStartError = message(failure) || "The workflow didn't start.";
      if (failure?.code === CHANGED_CODE) {
        // The workflow changed after the press opened the sheet: review it instead.
        if (sheet?.isOpen?.(id)) sheet.close();
        changedNotice(id, failure.workflowName || name, failure.state);
        return false;
      }
      if (!quiet) notice({ title: `${name} didn't start`, body: lastStartError, onClick: showApp });
      return false;
    }
    if (!run?.runId) {
      lastStartError = "The backend didn't say which run started.";
      if (!quiet) notice({ title: `${name} didn't start`, body: lastStartError, onClick: showApp });
      return false;
    }
    const record = { workflowId: run.workflowId, runId: run.runId, status: "running", projectRoot: run.projectRoot || "", workflowName: run.workflowName || name };
    const started = notice({ title: `Running ${record.workflowName}`, body: "Raticode tells you when it finishes. Click to watch the run.", onClick: () => openRun(record) });
    follow(record, started);
    return true;
  }

  /** The workflow differs from the approved version: say so, and review it on a click. */
  function changedNotice(id, name, state) {
    notice({
      title: changedTitle(name, state),
      body: "It didn't run. Click to review the changes; the shortcut runs it again once you approve them.",
      onClick: () => openReview({ shortcutId: id, workflowName: name }),
    });
  }

  // --- Following a run -------------------------------------------------------------------

  function follow(record, started) {
    const begun = now();
    let askedApproval = false;
    const check = async () => {
      followed.delete(record.runId);
      if (disposed) return;
      let run = null;
      try {
        const payload = await api("GET", `/api/rattish/runs?workflow=${encodeURIComponent(record.workflowId)}&limit=50`);
        run = (payload?.runs || []).find((item) => item.id === record.runId) || null;
      } catch { /* The backend restarted or is busy; look again. */ }
      if (run && run.status !== "running") {
        started?.close?.();
        finished({ ...record, status: run.status, projectRoot: run.projectRoot || record.projectRoot }, run.error);
        return;
      }
      if (run?.awaitingApproval && !askedApproval) {
        askedApproval = true;
        notice({ title: `${record.workflowName} is waiting for you`, body: "A step needs your approval. Click to open the run.", onClick: () => openRun(record) });
      }
      const elapsed = now() - begun;
      if (elapsed >= FOLLOW_LIMIT_MS) return;
      schedule(pollDelay(elapsed));
    };
    const schedule = (delay) => {
      const timer = setTimer(() => void check(), delay);
      timer?.unref?.();
      followed.set(record.runId, timer);
    };
    schedule(pollDelay(0));
  }

  function finished(record, error = "") {
    const name = record.workflowName;
    const open = () => openRun(record);
    if (record.status === "success") {
      notice({ title: `${name} finished`, body: "Click to open the run.", onClick: open });
    } else if (record.status === "stopped") {
      notice({ title: `${name} stopped`, body: "Click to open the run.", onClick: open });
    } else {
      notice({ title: `${name} failed`, body: error ? `${String(error).slice(0, 200)} Click to open the run.` : "Click to open the run.", onClick: open });
    }
  }

  function notice(options) {
    try { return notify(options) || null; } catch { return null; }
  }

  function dispose() {
    disposed = true;
    clearTimer(resyncTimer);
    for (const timer of followed.values()) clearTimer(timer);
    followed.clear();
    if (!desktopShortcuts) for (const entry of entries.values()) unregisterNative(entry);
    sheet?.close?.();
  }

  return { apply, sync, status, reserve, suspend, press, desktopChanged, dispose };
}

/** The command a desktop's keyboard settings run for shortcut id (Wayland's last resort). */
function runCommand(baseCommand, id, toggleFlag = "--toggle-dictation") {
  if (!baseCommand || !ID_PATTERN.test(String(id || ""))) return "";
  return baseCommand.endsWith(toggleFlag)
    ? `${baseCommand.slice(0, -toggleFlag.length)}${RUN_FLAG}=${id}`
    : `${baseCommand} ${RUN_FLAG}=${id}`;
}

module.exports = {
  CAUTION,
  DESKTOP_PREFIX,
  MAX_TEXT_CHARS,
  RUN_FLAG,
  changedTitle,
  createWorkflowShortcuts,
  desktopId,
  pollDelay,
  runCommand,
  shortcutFromArgv,
};
