// The browser's downloads: Raticode (chromium) downloads, the files Firefox and
// Chrome tabs finish in their staging folders, the quarantine for files whose
// type can run code, and the Downloads list's actions. browser-sessions.cjs
// passes in its tabs, the store and the policy, and calls the functions this
// returns.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { isDangerousDownload, sanitizeFilename, uniqueDownloadPath } = require("./browser-policy.cjs");

function createBrowserDownloads({
  getPolicy,
  sessions,
  store,
  log = () => {},
  app,
  shell,
  getMainWindow,
  openWith = null,
  quarantineDir,
  recordAgentActivity,
  sessionForContents,
  liveContents,
  takePendingSaveAs,
  engineLabel,
}) {
  // Raticode (chromium) downloads still running, by id.
  const downloads = new Map();
  // Firefox and Chrome downloads still running, by the navigation that began them.
  const firefoxDownloads = new Map();

  // Rem downloads only when Settings > Browser lets it. A blocked download
  // shows in Rem's activity for that tab.
  function remDownloadBlocked(session, filename, byRem) {
    const blocked = byRem && getPolicy().remDownloads !== "allow";
    if (blocked && session) {
      session.lastBlockedDownload = filename;
      recordAgentActivity(session, { action: "download blocked", detail: filename, at: Date.now() });
    }
    return blocked;
  }

  // Where a download whose type can run code waits for review.
  function quarantinePathFor(id) {
    fs.mkdirSync(quarantineDir, { recursive: true, mode: 0o700 });
    return path.join(quarantineDir, `${id}.download`);
  }

  // Nothing lists a private download after this, so an unfinished one stops
  // and a dangerous one still waiting for review doesn't stay on disk.
  function forgetPrivateDownloads() {
    for (const [id, live] of downloads) {
      if (!live.entry.private) continue;
      live.entry.forgotten = true;
      downloads.delete(id);
      if (live.entry.state === "progressing") {
        try { live.item.cancel(); } catch { /* It has just finished. */ }
      }
    }
    for (const entry of store.listDownloads()) {
      if (entry.private && entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
    }
    store.forgetPrivateDownloads();
    emitDownloads();
  }

  // A private download left waiting for review when Raticode last stopped has
  // no list entry to review it from.
  function removeStrayQuarantine() {
    try {
      const listed = new Set(store.listDownloads().map((item) => item.quarantinePath).filter(Boolean));
      for (const name of fs.readdirSync(quarantineDir)) {
        const file = path.join(quarantineDir, name);
        if (name.endsWith(".download") && !listed.has(file)) fs.rmSync(file, { force: true });
      }
    } catch { /* No quarantine folder yet. */ }
  }

  function handleDownload(event, item, contents, kind) {
    const session = sessionForContents(contents);
    const byRem = kind === "rem" || Boolean(session?.agent && !session.agent.shared);
    const filename = sanitizeFilename(item.getFilename());
    if (remDownloadBlocked(session, filename, byRem)) {
      event.preventDefault();
      return;
    }
    const id = crypto.randomUUID();
    const dangerous = isDangerousDownload(filename);
    const directory = app.getPath("downloads");
    const saveAs = takePendingSaveAs(session, item);
    const askWhere = !dangerous && !byRem && (getPolicy().askWhereToSave || Boolean(saveAs));
    let finalPath = "";
    if (dangerous) {
      item.setSavePath(quarantinePathFor(id));
    } else if (askWhere) {
      item.setSaveDialogOptions({ defaultPath: path.join(directory, filename), title: saveAs || "Save download" });
    } else {
      finalPath = uniqueDownloadPath(directory, filename, (candidate) => fs.existsSync(candidate) || [...downloads.values()].some((value) => value.entry.path === candidate));
      item.setSavePath(finalPath);
    }
    const entry = {
      id,
      url: String(item.getURL()).slice(0, 4096),
      filename,
      path: finalPath,
      mimeType: item.getMimeType(),
      state: "progressing",
      paused: false,
      receivedBytes: 0,
      totalBytes: item.getTotalBytes(),
      startedAt: Date.now(),
      dangerous,
      byRem,
      ...(session?.privateBrowsing ? { private: true } : {}),
    };
    downloads.set(id, { item, entry });
    store.upsertDownload(entry);
    emitDownloads();
    let lastEmit = 0;
    item.on("updated", (_event, state) => {
      if (entry.forgotten) return;
      entry.state = state === "interrupted" ? "interrupted" : "progressing";
      entry.paused = item.isPaused();
      entry.receivedBytes = item.getReceivedBytes();
      entry.totalBytes = item.getTotalBytes();
      if (!entry.path && !dangerous) entry.path = item.getSavePath();
      const time = Date.now();
      if (time - lastEmit > 250) {
        lastEmit = time;
        store.upsertDownload(entry);
        emitDownloads();
      }
    });
    item.once("done", (_event, state) => {
      if (entry.forgotten) return;
      // Cancelling the save dialog is not a download worth listing.
      if (askWhere && state === "cancelled" && !item.getSavePath()) {
        downloads.delete(id);
        store.removeDownload(id);
        emitDownloads();
        return;
      }
      entry.receivedBytes = item.getReceivedBytes();
      entry.paused = false;
      if (!entry.path && !dangerous) entry.path = item.getSavePath();
      entry.state = state === "completed" ? (dangerous ? "quarantined" : "completed") : state;
      entry.finishedAt = Date.now();
      if (dangerous && state === "completed") entry.quarantinePath = item.getSavePath();
      store.upsertDownload(entry);
      emitDownloads();
    });
  }

  function finishedDownloadPath(entry) {
    if (entry?.state !== "completed" || !entry.path) throw new Error("Only finished downloads can be opened.");
    if (!fs.existsSync(entry.path)) throw new Error("The file was moved or deleted.");
    return entry.path;
  }

  async function downloadApps(id) {
    const entry = store.listDownloads().find((item) => item.id === id);
    if (!entry) throw new Error("That download is no longer listed.");
    const filePath = finishedDownloadPath(entry);
    try {
      return { apps: openWith ? await openWith.listApps(filePath) : [] };
    } catch (error) {
      log("warn", `Listing apps for a download failed: ${error.message}`);
      return { apps: [] };
    }
  }

  async function openDownloadWith(entry, appId) {
    const filePath = finishedDownloadPath(entry);
    if (!openWith) throw new Error("Open with is not supported on this system.");
    await openWith.openWith(filePath, appId);
    return { downloads: store.listDownloads() };
  }

  function downloadAction(action, id, options = {}) {
    const live = downloads.get(id);
    const entry = live?.entry || store.listDownloads().find((item) => item.id === id);
    if (!entry && action !== "clear") throw new Error("That download is no longer listed.");
    switch (action) {
      case "pause": live?.item.pause(); break;
      case "resume": if (live?.item.canResume()) live.item.resume(); break;
      case "cancel": live?.item.cancel(); break;
      case "open":
        if (entry.state !== "completed" || !entry.path) throw new Error("Only finished downloads can be opened.");
        void shell.openPath(entry.path);
        break;
      case "open-with":
        return openDownloadWith(entry, String(options.appId || ""));
      case "show":
        if (entry.path && fs.existsSync(entry.path)) shell.showItemInFolder(entry.path);
        else throw new Error("The file was moved or deleted.");
        break;
      case "keep": {
        if (entry.state !== "quarantined" || !entry.quarantinePath) throw new Error("This download is not waiting for review.");
        const target = uniqueDownloadPath(app.getPath("downloads"), entry.filename, fs.existsSync);
        moveFile(entry.quarantinePath, target);
        Object.assign(entry, { state: "completed", path: target, quarantinePath: "", dangerous: false, kept: true });
        store.upsertDownload(entry);
        break;
      }
      case "discard":
        if (entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
        Object.assign(entry, { state: "cancelled", quarantinePath: "" });
        store.upsertDownload(entry);
        break;
      case "retry": {
        const owner = getMainWindow();
        if (!/^https?:/i.test(entry.url) || !owner) throw new Error("This download cannot be retried.");
        const userTab = [...sessions.values()].find((item) => item.kind === "user" && Boolean(item.privateBrowsing) === Boolean(entry.private) && liveContents(item));
        if (!userTab) throw new Error("Open a browser tab to retry this download.");
        liveContents(userTab).downloadURL(entry.url);
        store.removeDownload(id);
        break;
      }
      case "remove":
        if (entry.state === "progressing") live?.item.cancel();
        if (entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
        downloads.delete(id);
        store.removeDownload(id);
        break;
      case "clear":
        for (const item of store.listDownloads()) {
          if (item.state === "progressing" || item.state === "quarantined") continue;
          downloads.delete(item.id);
        }
        store.clearDownloads();
        break;
      default:
        throw new Error(`Unknown download action: ${action}`);
    }
    emitDownloads();
    return { downloads: store.listDownloads() };
  }

  function emitDownloads() {
    const owner = getMainWindow();
    if (owner && !owner.isDestroyed()) owner.webContents.send("gofer:browser-downloads", { downloads: store.listDownloads() });
  }

  // Firefox and Chrome save into Raticode's staging folders. Finished files
  // move to Downloads, or to quarantine when their type can run code.
  function firefoxDownloadBegan(session, details) {
    const byRem = Boolean(session.agent && !session.agent.shared);
    const filename = sanitizeFilename(details.filename || "download");
    if (remDownloadBlocked(session, filename, byRem)) return;
    const entry = {
      id: crypto.randomUUID(),
      url: String(details.url || "").slice(0, 4096),
      filename,
      path: "",
      mimeType: "",
      state: "progressing",
      paused: false,
      receivedBytes: 0,
      totalBytes: 0,
      startedAt: Date.now(),
      dangerous: isDangerousDownload(filename),
      byRem,
    };
    firefoxDownloads.set(details.navigation || entry.id, entry);
    store.upsertDownload(entry);
    emitDownloads();
  }

  function firefoxDownloadEnded(details, session = null) {
    const key = details.navigation || "";
    const entry = firefoxDownloads.get(key);
    if (!entry) {
      if (details.filepath) fs.rmSync(details.filepath, { force: true });
      return;
    }
    firefoxDownloads.delete(key);
    entry.finishedAt = Date.now();
    try {
      if (details.status !== "complete" || !details.filepath) {
        entry.state = "cancelled";
      } else if (entry.dangerous) {
        const target = quarantinePathFor(entry.id);
        moveFile(details.filepath, target);
        Object.assign(entry, { state: "quarantined", quarantinePath: target, receivedBytes: fs.statSync(target).size });
      } else {
        const target = uniqueDownloadPath(app.getPath("downloads"), entry.filename, (candidate) => fs.existsSync(candidate));
        moveFile(details.filepath, target);
        Object.assign(entry, { state: "completed", path: target, receivedBytes: fs.statSync(target).size });
      }
      entry.totalBytes = entry.receivedBytes;
    } catch (error) {
      entry.state = "interrupted";
      log("warn", `Could not finish a download from ${/^[aeiou]/i.test(engineLabel(session?.engine)) ? "an" : "a"} ${engineLabel(session?.engine)} tab: ${error.message}`);
    }
    store.upsertDownload(entry);
    emitDownloads();
  }

  return {
    downloadAction,
    downloadApps,
    firefoxDownloadBegan,
    firefoxDownloadEnded,
    forgetPrivateDownloads,
    handleDownload,
    removeStrayQuarantine,
  };
}

function moveFile(source, target) {
  try {
    fs.renameSync(source, target);
  } catch (error) {
    if (error.code !== "EXDEV") throw error;
    fs.copyFileSync(source, target);
    fs.rmSync(source, { force: true });
  }
}

module.exports = { createBrowserDownloads };
