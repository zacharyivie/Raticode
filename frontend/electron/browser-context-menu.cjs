// The integrated browser's right-click menu and the page actions it offers:
// print, Save page as…, Save as PDF…, Save … as and bookmarks.
// browser-sessions.cjs passes in what the menu needs from its tabs, and reads
// the browser policy through getPolicy() so this module keeps no state of its own.
const fs = require("node:fs");
const path = require("node:path");
const { sanitizeFilename } = require("./browser-policy.cjs");

const PDF_TIMEOUT_MS = 60 * 1000;
const SAVE_AS_TIMEOUT_MS = 30 * 1000;

function createBrowserContextMenu({
  Menu,
  app,
  clipboard,
  dialog,
  shell,
  getMainWindow,
  log = () => {},
  store,
  getPolicy,
  liveContents,
  isStreamed,
  engineLabel,
  openPageLink,
  reload,
  emitCommand,
  emitState,
  emitLibraryChanged,
}) {
  // Electron 44 made clipboard writes asynchronous.
  function copyText(text) {
    void Promise.resolve(clipboard.writeText(text)).catch((error) => log("warn", `Copy failed: ${error.message}`));
  }

  // Chromium's print dialog fails, and can crash, when the system has no
  // printers (no CUPS on Linux). Check first and offer a PDF instead.
  async function print(session) {
    const contents = liveContents(session);
    if (!contents) return;
    // A headless Firefox or Chrome cannot show a print dialog; a PDF is the closest.
    if (isStreamed(contents)) {
      await saveAsPdf(session);
      return;
    }
    let printers = [];
    try {
      printers = typeof contents.getPrintersAsync === "function" ? await contents.getPrintersAsync() : [];
    } catch (error) {
      log("warn", `Could not list printers: ${error.message}`);
    }
    if (!printers.length) {
      const choice = await showMessage({
        type: "info",
        message: "No printers are set up",
        detail: "Raticode could not find a printer on this computer. Set one up in your system settings (CUPS on Linux), or save the page as a PDF.",
        buttons: ["Save as PDF…", "Cancel"],
        defaultId: 0,
        cancelId: 1,
      });
      if (choice === 0) await saveAsPdf(session);
      return;
    }
    if (!liveContents(session)) return;
    try {
      contents.print({ printBackground: true }, (success, reason) => {
        if (!success && reason && !/cancel/i.test(reason)) {
          log("warn", `Print failed: ${reason}`);
          void showMessage({ type: "error", message: "Could not print this page", detail: `${reason}. You can save the page as a PDF instead.`, buttons: ["OK"] });
        }
      });
    } catch (error) {
      log("warn", `Print failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not print this page", detail: error.message, buttons: ["OK"] });
    }
  }

  async function showMessage(options) {
    const owner = getMainWindow();
    try {
      const result = owner && !owner.isDestroyed() ? await dialog.showMessageBox(owner, options) : await dialog.showMessageBox(options);
      return result?.response ?? -1;
    } catch (error) {
      log("warn", `Could not show a message: ${error.message}`);
      return -1;
    }
  }

  async function chooseSavePath(options) {
    const owner = getMainWindow();
    const result = owner && !owner.isDestroyed() ? await dialog.showSaveDialog(owner, options) : await dialog.showSaveDialog(options);
    return result.canceled || !result.filePath ? "" : result.filePath;
  }

  function showContextMenu(session, params) {
    const contents = liveContents(session);
    if (!contents) return;
    // Frames other than the main one do not report the element they show.
    if (params.frame && params.frame !== contents.mainFrame) session.contextElement = null;
    const element = session.contextElement || null;
    // Chrome and Edge tabs report the right-click themselves; their page,
    // clipboard and editing live in a browser without a window.
    const streamed = isStreamed(contents);
    const edit = (command) => () => {
      const text = command === "paste" ? clipboard.readText() : "";
      void contents.editCommand(command, text).catch((error) => log("warn", `${engineLabel(contents.engine)} could not ${command}: ${error.message}`));
    };
    const template = [];
    const separator = () => { if (template.length && template.at(-1).type !== "separator") template.push({ type: "separator" }); };
    if (params.misspelledWord) {
      for (const suggestion of (params.dictionarySuggestions || []).slice(0, 5)) {
        template.push({ label: suggestion, click: () => contents.replaceMisspelling(suggestion) });
      }
      if (!params.dictionarySuggestions?.length) template.push({ label: "No spelling suggestions", enabled: false });
      template.push({ label: "Add to dictionary", click: () => contents.session.addWordToSpellCheckerDictionary(params.misspelledWord) });
      separator();
    }
    if (params.linkURL && /^https?:/i.test(params.linkURL)) {
      template.push(
        { label: "Open link in new tab", click: () => openPageLink(session, params.linkURL, { background: true }) },
        { label: "Open link in default browser", click: () => void shell.openExternal(params.linkURL) },
        ...(streamed ? [] : [{ label: "Save link as…", click: () => saveLinkAs(session, params.linkURL) }]),
        { label: "Copy link address", click: () => copyText(params.linkURL) },
      );
      separator();
    } else if (!streamed && params.linkURL && /^(?:blob|data):/i.test(params.linkURL)) {
      // A file the page built itself, such as an exported chat. It only
      // exists inside this page, so it can be saved but not opened elsewhere.
      template.push({ label: "Save link as…", click: () => saveLinkAs(session, params.linkURL) });
      separator();
    }
    if (streamed && params.mediaType === "image" && params.srcURL) {
      template.push(
        { label: "Open image in new tab", click: () => openPageLink(session, params.srcURL, { background: true }) },
        { label: "Copy image address", click: () => copyText(params.srcURL) },
      );
      separator();
    } else if (params.mediaType === "image" && params.srcURL) {
      template.push(
        { label: "Save image as…", click: () => saveImageAs(session, params.srcURL) },
        { label: "Copy image", click: () => contents.copyImageAt(params.x, params.y) },
        { label: "Copy image address", click: () => copyText(params.srcURL) },
      );
      separator();
    }
    if (streamed && params.isEditable) {
      template.push(
        { label: "Undo", click: edit("undo") },
        { label: "Redo", click: edit("redo") },
        { type: "separator" },
        { label: "Cut", enabled: Boolean(params.selectionText), click: edit("cut") },
        { label: "Copy", enabled: Boolean(params.selectionText), click: edit("copy") },
        { label: "Paste", click: edit("paste") },
        { label: "Select all", click: edit("selectAll") },
      );
      separator();
    } else if (params.isEditable) {
      template.push({ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" });
      separator();
    } else if (params.selectionText) {
      const text = params.selectionText.trim().replace(/\s+/g, " ");
      template.push(
        streamed ? { label: "Copy", click: edit("copy") } : { role: "copy" },
        { label: `Search for “${text.length > 30 ? `${text.slice(0, 29)}…` : text}”`, click: () => openPageLink(session, getPolicy().searchUrl.replace("{query}", encodeURIComponent(text.slice(0, 500))), { background: false }) },
      );
      separator();
    }
    // Local files ask about the open page only; Rem gets no further file access.
    if (!session.privateBrowsing && (session.kind !== "preview" || /^file:/i.test(contents.getURL()))) {
      template.push({ label: "Ask Rem about this", click: () => emitCommand(session, "ask-rem", { selection: String(params.selectionText || "").slice(0, 20000), element }) });
      separator();
    }
    template.push(
      { label: "Back", enabled: contents.navigationHistory.canGoBack(), click: () => contents.navigationHistory.goBack() },
      { label: "Forward", enabled: contents.navigationHistory.canGoForward(), click: () => contents.navigationHistory.goForward() },
      { label: "Reload", click: () => reload(session, false) },
      { type: "separator" },
      ...(session.kind === "user" ? [{ label: store.bookmarkFor(contents.getURL()) ? "Remove bookmark" : "Bookmark this page", click: () => toggleBookmark(session) }] : []),
      ...(!streamed || contents.savePageFormat ? [{ label: "Save page as…", click: () => void savePage(session) }] : []),
      // A browser without a window prints only to PDF.
      ...(streamed ? [] : [{ label: "Print…", click: () => void print(session) }]),
      { label: "Save as PDF…", click: () => void saveAsPdf(session) },
      ...(streamed ? [] : [{ type: "separator" }, { label: "Inspect", click: () => contents.inspectElement(params.x, params.y) }]),
    );
    // The menu opens in the window the tab is in.
    const owner = session.viewWindow && !session.viewWindow.isDestroyed() ? session.viewWindow : getMainWindow();
    Menu.buildFromTemplate(template).popup(owner && !owner.isDestroyed() ? { window: owner } : {});
  }

  // The download that follows asks where to save, like Save page as….
  function saveImageAs(session, url) {
    startSaveAs(session, url, "Save image");
  }

  function saveLinkAs(session, url) {
    startSaveAs(session, url, "Save link");
  }

  function startSaveAs(session, url, title) {
    const contents = liveContents(session);
    if (!contents) return;
    session.pendingSaveAs = { url, title, at: Date.now() };
    contents.downloadURL(url);
  }

  // The save dialog's title when this download is the one Save … as asked for.
  function takePendingSaveAs(session, item) {
    const pending = session?.pendingSaveAs;
    if (!pending) return "";
    session.pendingSaveAs = null;
    const original = item.getURLChain?.()[0] || item.getURL();
    return pending.url === original && Date.now() - pending.at < SAVE_AS_TIMEOUT_MS ? pending.title || "Save download" : "";
  }

  async function savePage(session) {
    const contents = liveContents(session);
    if (!contents) return;
    try {
      // Chrome and Edge tabs save the page as one file with its pictures.
      const single = contents.savePageFormat === "mhtml";
      const filePath = await chooseSavePath({
        title: "Save page",
        defaultPath: path.join(app.getPath("downloads"), `${sanitizeFilename(contents.getTitle() || "page")}.${single ? "mhtml" : "html"}`),
        filters: single ? [{ name: "Web page, single file", extensions: ["mhtml", "mht"] }] : [{ name: "Web page", extensions: ["html", "htm"] }],
      });
      if (!filePath || !liveContents(session)) return;
      await contents.savePage(filePath, "HTMLComplete");
    } catch (error) {
      log("warn", `Save page failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not save this page", detail: error.message, buttons: ["OK"] });
    }
  }

  async function saveAsPdf(session) {
    const contents = liveContents(session);
    if (!contents) return;
    try {
      const filePath = await chooseSavePath({
        title: "Save as PDF",
        defaultPath: path.join(app.getPath("downloads"), `${sanitizeFilename(contents.getTitle() || "page")}.pdf`),
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      });
      if (!filePath || !liveContents(session)) return;
      const data = await withTimeout(contents.printToPDF({ printBackground: true, preferCSSPageSize: true }), PDF_TIMEOUT_MS, "The page did not finish rendering a PDF within a minute.");
      await fs.promises.writeFile(filePath, data);
      shell.showItemInFolder(filePath);
    } catch (error) {
      log("warn", `Save as PDF failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not save this page as a PDF", detail: error.message, buttons: ["OK"] });
    }
  }

  function toggleBookmark(session) {
    const contents = liveContents(session);
    if (!contents || session.kind !== "user") return null;
    const existing = store.bookmarkFor(contents.getURL());
    if (existing) store.removeBookmark(existing.id);
    else store.addBookmark({ url: contents.getURL(), title: contents.getTitle() });
    emitLibraryChanged("bookmarks");
    emitState(session);
    return !existing;
  }

  return { showContextMenu, print, savePage, saveAsPdf, toggleBookmark, showMessage, takePendingSaveAs };
}

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

module.exports = { createBrowserContextMenu };
