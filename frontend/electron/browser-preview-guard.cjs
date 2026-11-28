// The local HTML preview's request guard: which local files a preview may
// read, and when it may load remote content. browser-sessions.cjs passes in
// its tabs, profiles and the store, and calls the functions this returns.
const fs = require("node:fs");
const path = require("node:path");

// What the preview profile's request guard decides: local files, and anything
// that would reach the network (ws: and wss: included).
const PREVIEW_GUARDED_URLS = ["file://*/*", "http://*/*", "https://*/*", "ws://*/*", "wss://*/*"];

function createPreviewGuard({
  profile,
  sessions,
  store,
  liveContents,
  emitState,
  reload,
  moveToProfile,
  sessionState,
  localFilePath,
  // Checks a preview's file:// request against its folder grant.
  resolvePreviewPath,
}) {
  // Local HTML previews run as file:// pages, and Electron lets those read any other
  // file:// URL (grantFileProtocolExtraPrivileges). Without a check a previewed page
  // could fetch ~/.ssh or list folders and send the result to a website (CWE-552).
  // Every file:// request in the preview profile, the page itself included, must be
  // inside the grant or folder of the preview that made it. Another open preview's
  // folder doesn't count, and a request no open preview made is refused.
  //
  // Previews are also offline: a page can't send what it read from its own folder
  // anywhere, or tell a website it was opened. A project file loads web content
  // only after the user chose "Load remote content" for that file; a file another
  // app handed to Raticode never does. Electron keeps one listener per profile,
  // so this one decides both.
  function installPreviewRequestGuard(kind) {
    const webRequest = profile(kind)?.webRequest;
    if (typeof webRequest?.onBeforeRequest !== "function") return;
    webRequest.onBeforeRequest({ urls: PREVIEW_GUARDED_URLS }, (details, callback) => {
      if (/^file:/i.test(String(details?.url ?? ""))) {
        callback({ cancel: !previewFileAllowed(details || {}) });
        return;
      }
      callback({ cancel: !previewRemoteRequestAllowed(details || {}) });
    });
  }

  // The open preview whose page made a request, if any.
  function previewForRequest(details) {
    const id = details?.webContentsId;
    if (typeof id !== "number") return null;
    return [...sessions.values()].find((item) => item.kind === "preview" && liveContents(item)?.id === id) || null;
  }

  function previewRemoteRequestAllowed(details) {
    const session = previewForRequest(details);
    if (!session) return false;
    if (remoteContentAllowed(session)) return true;
    // Counted for the page on screen; the bar appears with the first one.
    const url = liveContents(session)?.getURL() || "";
    if (session.remoteBlocked?.url !== url) session.remoteBlocked = { url, count: 0 };
    session.remoteBlocked.count += 1;
    if (session.remoteBlocked.count === 1) emitState(session);
    return false;
  }

  function remoteBlockedCount(session) {
    const url = liveContents(session)?.getURL() || "";
    return session.remoteBlocked?.url === url ? session.remoteBlocked.count : 0;
  }

  // The local file a preview shows, while it shows one.
  function previewFilePath(session) {
    const url = liveContents(session)?.getURL() || "";
    return localFilePath(url);
  }

  function remoteContentAllowed(session) {
    if (session.kind !== "preview" || session.external || session.online !== true) return false;
    const file = previewFilePath(session);
    return Boolean(file) && store.previewRemoteAllowed?.(file) === true;
  }

  // Whether a preview showing this file belongs in the online profile.
  function previewBelongsOnline(session, file) {
    return session.kind === "preview" && !session.external && Boolean(file) && store.previewRemoteAllowed?.(file) === true;
  }

  // "Load remote content" for the file a project preview shows, remembered by
  // its path. Every open preview of that file moves to the profile that
  // matches the new choice and loads the file again.
  function setPreviewRemote(session, allowed) {
    if (session.kind !== "preview") throw new Error("Only local HTML previews block remote content.");
    if (session.external) throw new Error("Files opened from another app never load remote content.");
    const file = previewFilePath(session);
    if (!file) throw new Error("This tab isn't showing a local file.");
    store.setPreviewRemote(file, allowed);
    for (const other of sessions.values()) {
      if (other.kind !== "preview" || other.external || previewFilePath(other) !== file) continue;
      other.remoteBlocked = null;
      if ((other.online === true) !== previewBelongsOnline(other, file)) moveToProfile(other, previewBelongsOnline(other, file));
      else reload(other, false);
      emitState(other);
    }
    return sessionState(session);
  }

  // A preview may read a local file only inside its folder grant or, without one, the
  // folder of the file it opened with. The real path counts, so links can't lead out.
  function previewMayRead(session, target) {
    if (session.grantId) {
      try {
        resolvePreviewPath(target, { grantId: session.grantId, mustExist: true });
        return true;
      } catch { /* Not inside this preview's grant. */ }
    }
    if (!session.previewFolder) return false;
    let real;
    try { real = fs.realpathSync(target); } catch { return false; }
    const relative = path.relative(session.previewFolder, real);
    return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
  }

  function previewFileAllowed(details) {
    const session = previewForRequest(details);
    if (!session) return false;
    const target = localFilePath(details.url);
    return Boolean(target) && previewMayRead(session, target);
  }

  return { installPreviewRequestGuard, remoteBlockedCount, remoteContentAllowed, previewBelongsOnline, setPreviewRemote, previewMayRead };
}

module.exports = { createPreviewGuard };
