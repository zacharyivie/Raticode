// One Firefox tab, presented to the integrated browser with the part of
// Electron's webContents interface that browser-sessions.cjs and
// agent-browser.cjs use. Rem's site rules, approvals, pause and logging
// therefore apply to Firefox tabs through the same code as Raticode tabs.
//
// Firefox renders without a window. The tab streams screenshots of its
// viewport to the studio, which draws them on a canvas and sends the user's
// mouse and keyboard back as WebDriver BiDi input. That input is trusted, as
// is Rem's: Firefox treats both like a person using the page.
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const { watchSound } = require("./stream-sound.cjs");
const { mediaPageHook } = require("./browser-media.cjs");

const FALLBACK_VIEW = { width: 1280, height: 800, scale: 1 };
const MAX_VIEW = 4096;
const FRAME_QUALITY = 0.82;
// Frame pacing: fast while the user or the page is active, slow when idle.
const ACTIVE_FRAME_MS = 40;
const SETTLING_FRAME_MS = 100;
const IDLE_FRAME_MS = 500;
const ACTIVE_WINDOW_MS = 1500;
const SETTLING_WINDOW_MS = 4000;
const ACK_TIMEOUT_MS = 500;
const POPUP_URL_TIMEOUT_MS = 2000;
const MAX_TEXT_INPUT = 20000;
const TEXT_CHUNK = 200;
const CURSOR_INTERVAL_MS = 120;
const STATE_SANDBOX = "raticode-state";
const STATE_REFRESH_MS = 1000;
// Firefox's own error and warning pages. A certificate warning's "Accept the
// Risk and Continue", or a blocked site's way past the block, is the user's
// decision; Raticode's own tabs never let Rem past either.
const FIREFOX_ERROR_PAGE = /^about:(?:certerror|neterror|blocked|httpsonlyerror)\b/i;
// The commands Rem's page tools send that read or act on the page.
const PAGE_COMMANDS = new Set(["DOM.getDocument", "DOM.performSearch", "DOM.getSearchResults", "DOM.setFileInputFiles", "Runtime.evaluate"]);

// WebDriver's codes for keys that have no printable character.
const WEBDRIVER_KEYS = {
  Cancel: "", Help: "", Backspace: "", Tab: "", Clear: "",
  Enter: "", Shift: "", Control: "", Alt: "", Pause: "",
  Escape: "", " ": " ", Space: " ", PageUp: "", PageDown: "", End: "",
  Home: "", ArrowLeft: "", ArrowUp: "", ArrowRight: "", ArrowDown: "",
  Insert: "", Delete: "", F1: "", F2: "", F3: "", F4: "",
  F5: "", F6: "", F7: "", F8: "", F9: "", F10: "",
  F11: "", F12: "", Meta: "", OS: "", AltGraph: "",
};
const MODIFIER_KEYS = { ctrlKey: "", altKey: "", shiftKey: "", metaKey: "" };

function webdriverKey(key) {
  const value = String(key ?? "");
  if (Object.hasOwn(WEBDRIVER_KEYS, value)) return WEBDRIVER_KEYS[value];
  return [...value].length === 1 ? value : "";
}

// Turns a BiDi RemoteValue into the plain value Electron's
// executeJavaScript would return.
function fromRemoteValue(value) {
  if (!value || typeof value !== "object") return undefined;
  switch (value.type) {
    case "undefined": return undefined;
    case "null": return null;
    case "string":
    case "boolean": return value.value;
    case "number":
      if (typeof value.value === "number") return value.value;
      return { NaN: Number.NaN, "-0": -0, Infinity: Number.POSITIVE_INFINITY, "-Infinity": Number.NEGATIVE_INFINITY }[value.value];
    case "bigint": return String(value.value);
    case "date": return value.value;
    case "regexp": return `/${value.value?.pattern ?? ""}/${value.value?.flags ?? ""}`;
    case "array":
    case "set": return (value.value || []).map(fromRemoteValue);
    case "object":
    case "map": {
      const result = {};
      for (const [key, item] of value.value || []) result[typeof key === "string" ? key : String(fromRemoteValue(key))] = fromRemoteValue(item);
      return result;
    }
    default: return null;
  }
}

function remoteDescription(value) {
  if (!value || typeof value !== "object") return "";
  if (value.type === "error") return "Error";
  if (["node", "window", "function", "promise", "weakmap", "weakset", "proxy", "generator"].includes(value.type)) return value.type;
  return "";
}

// Matches a download's start to its end. Firefox names each download; older
// versions only gave the navigation, and neither may be set for a link with
// a download attribute.
function downloadKey(params = {}) {
  if (params.download) return `download:${params.download}`;
  if (params.navigation) return String(params.navigation);
  return `url:${String(params.url || "").slice(0, 4096)}`;
}

function clampView(view) {
  const { width, height, scale } = view || {};
  const w = Math.round(Number(width));
  const h = Math.round(Number(height));
  const s = Number(scale);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w < 50 || h < 50) return null;
  return {
    width: Math.min(w, MAX_VIEW),
    height: Math.min(h, MAX_VIEW),
    scale: Number.isFinite(s) && s >= 0.5 && s <= 4 ? s : 1,
  };
}

// Same-origin rules hide most of a tab's history from the page, so Raticode
// keeps its own list. New entries come from pushes; traversals Raticode asks
// for move the index; reloads and replacements keep it.
function createHistoryTracker() {
  let entries = [];
  let index = -1;
  let pendingDelta = 0;
  return {
    traverse(delta) { pendingDelta += delta; },
    commit(url, { kind = "navigate", historyLength = 0 } = {}) {
      if (!url || url === "about:blank" && index < 0) return;
      if (pendingDelta) {
        index = Math.min(Math.max(index + pendingDelta, 0), Math.max(entries.length - 1, 0));
        pendingDelta = 0;
        if (entries[index]) entries[index] = { ...entries[index], url };
        return;
      }
      if (kind === "reload" || kind === "replace") {
        if (index >= 0) entries[index] = { ...entries[index], url };
        else { entries = [{ url, title: "" }]; index = 0; }
        return;
      }
      if (kind === "back_forward") {
        const found = entries.findIndex((entry) => entry.url === url);
        if (found >= 0) index = found;
        return;
      }
      entries = [...entries.slice(0, index + 1), { url, title: "" }];
      // Firefox may hold earlier entries Raticode never saw, such as a restored page.
      if (historyLength > entries.length) entries = [...Array.from({ length: historyLength - entries.length }, () => ({ url: "", title: "" })), ...entries];
      index = entries.length - 1;
    },
    setTitle(title) { if (entries[index]) entries[index] = { ...entries[index], title }; },
    canGoBack: () => index > 0 && entries.slice(0, index).some((entry) => entry.url),
    canGoForward: () => index >= 0 && index < entries.length - 1,
    entries: () => entries.map((entry) => ({ ...entry })),
    index: () => Math.max(index, 0),
    length: () => entries.length,
  };
}

class FirefoxContents extends EventEmitter {
  constructor({ engine, kind = "user", nativeImage = null, clipboard = null, log = () => {} }) {
    super();
    this.engine = "firefox";
    this.kind = kind;
    this.firefox = engine;
    this.nativeImage = nativeImage;
    this.clipboard = clipboard;
    this.log = log;
    this.context = "";
    this.destroyed = false;
    this.dead = false;
    this.url = "about:blank";
    this.title = "";
    this.loading = true;
    this.focused = false;
    this.muted = false;
    this.pageZoom = 1;
    this.view = null;
    this.visible = false;
    this.history = createHistoryTracker();
    this.windowOpenHandler = () => ({ action: "deny" });
    this.waiters = new Set();
    this.inputQueue = Promise.resolve();
    this.pendingMove = null;
    this.pressedKeys = new Set();
    this.dialog = null;
    this.frame = { seq: 0, awaitingAck: false, ackDeadline: 0, lastAt: 0, lastHash: "", lastChange: 0, streaming: false, wake: null };
    this.activityAt = Date.now();
    this.cursorAt = 0;
    this.stats = { frames: 0, captureMs: 0, bytes: 0 };
    this.guardNavigation = false;
    this.mainFrame = {};
    this.debugger = createDebuggerShim(this);
    this.nativeInput = {
      click: (options) => this.nativeClick(options),
      hover: (options) => this.nativeHover(options),
      press: (key) => this.nativePress(key),
    };
    this.audible = false;
    this.sound = watchSound(this, { ready: () => Boolean(this.context), evaluate: (expression) => this.evaluateIn(STATE_SANDBOX, expression) });
    this.ready = this.start();
    this.ready.catch((error) => this.fail(error));
  }

  async start() {
    const { context, generation } = await this.firefox.createContext(this.kind);
    if (this.destroyed) {
      void this.firefox.send("browsingContext.close", { context, promptUnload: false }).catch(() => {});
      throw new Error("The tab closed.");
    }
    this.context = context;
    this.generation = generation;
    this.firefox.register(context, this);
    this.loading = false;
    // Remembers the page's previous and next handlers for the media bar.
    await this.command("script.addPreloadScript", { functionDeclaration: String(mediaPageHook), contexts: [context] }).catch((error) => {
      this.log("warn", `Firefox media controls are unavailable in this tab: ${error.message}`);
    });
    await this.applyViewport();
    if (this.guardNavigation) await this.installNavigationGuard();
    this.emit("firefox-ready");
  }

  fail(error) {
    this.loading = false;
    this.dead = true;
    this.lastError = error?.message || String(error);
    this.emit("did-fail-load", {}, -2, this.lastError, this.url, true);
    this.emit("did-stop-loading");
  }

  // -------------------------------------------------------------------------
  // webContents interface

  isDestroyed() { return this.destroyed; }
  getURL() { return this.url; }
  getTitle() { return this.title; }
  isLoading() { return this.loading; }
  isFocused() { return this.focused; }
  getType() { return "firefox"; }
  // The studio focuses its own view of the tab.
  focus() { this.emit("firefox-focus"); }
  setBackgroundThrottling() {}
  setBackgroundColor() {}
  setZoomFactor() {}
  setWindowOpenHandler(handler) { this.windowOpenHandler = typeof handler === "function" ? handler : this.windowOpenHandler; }
  isCurrentlyAudible() { return this.audible === true; }
  isDevToolsOpened() { return false; }
  closeDevTools() {}
  openDevTools() { throw new Error("Developer tools are not available in Firefox tabs."); }
  savePage() { return Promise.reject(new Error("Saving pages is not available in Firefox tabs. Save as PDF instead.")); }
  print() { throw new Error("Printing is not available in Firefox tabs. Save as PDF instead."); }
  async getPrintersAsync() { return []; }

  async printToPDF(options = {}) {
    await this.alive();
    const result = await this.command("browsingContext.print", {
      context: this.context,
      background: options.printBackground !== false,
      orientation: options.landscape ? "landscape" : "portrait",
    });
    return Buffer.from(result.data, "base64");
  }

  async loadURL(url) {
    await this.alive();
    this.lastError = "";
    const target = String(url);
    const waiter = this.waitForNavigation();
    // Report loading at once, as Chromium does, so callers that wait for the
    // page to settle do not return before Firefox announces the navigation.
    this.loading = true;
    try {
      await this.command("browsingContext.navigate", { context: this.context, url: target, wait: "none" });
    } catch (error) {
      waiter.cancel();
      this.loading = false;
      this.emit("did-fail-load", {}, -2, error.message, target, true);
      this.emit("did-stop-loading");
      throw new Error(`Firefox could not open ${target}: ${error.message}`);
    }
    return waiter.promise;
  }

  downloadURL(url) { void this.loadURL(url).catch(() => {}); }
  reload() { void this.reloadPage(false); }
  reloadIgnoringCache() { void this.reloadPage(true); }

  async reloadPage(ignoreCache) {
    if (this.dead) {
      await this.revive();
      if (this.url && this.url !== "about:blank") await this.loadURL(this.url).catch(() => {});
      return;
    }
    await this.alive();
    this.loading = true;
    // Firefox (158 and earlier) refuses the whole reload when it is given
    // ignoreCache, even false. A hard reload asks for it and falls back to a
    // plain reload where Firefox doesn't support it yet.
    const reload = (params) => this.command("browsingContext.reload", { context: this.context, wait: "none", ...params });
    try {
      if (ignoreCache) await reload({ ignoreCache: true }).catch((error) => {
        if (!/ignoreCache/.test(error.message)) throw error;
        return reload({});
      });
      else await reload({});
    } catch (error) {
      this.loading = false;
      this.emit("did-stop-loading");
      this.log("warn", `Firefox reload failed: ${error.message}`);
    }
  }

  stop() { void this.evaluateIn(STATE_SANDBOX, "window.stop()").catch(() => {}); }

  get navigationHistory() {
    const history = this.history;
    const traverse = (delta) => {
      if (!delta) return;
      history.traverse(delta);
      this.loading = true;
      const before = this.navigation;
      void this.alive().then(() => this.command("browsingContext.traverseHistory", { context: this.context, delta })).then(async () => {
        // Same-page entries and pages from the back-forward cache may finish
        // without load events; read the address Firefox now shows.
        await delay(50);
        const tree = await this.command("browsingContext.getTree", { root: this.context, maxDepth: 0 });
        const current = tree.contexts?.[0]?.url || "";
        if (current && current !== this.url) this.commitUrl(current);
        // Same-page entries and back-forward cache restores fire no load
        // event, so a finished document ends the loading state here.
        const ready = this.navigation === before ? "complete" : await this.evaluateIn(STATE_SANDBOX, "document.readyState").catch(() => "");
        if (ready === "complete" && this.loading) {
          this.loading = false;
          await this.refreshState();
          this.emit("did-stop-loading");
          this.settleWaiters();
        }
      }).catch((error) => {
        this.loading = false;
        this.log("warn", `Firefox history navigation failed: ${error.message}`);
      });
    };
    return {
      canGoBack: () => history.canGoBack(),
      canGoForward: () => history.canGoForward(),
      goBack: () => traverse(-1),
      goForward: () => traverse(1),
      goToIndex: (target) => traverse(target - history.index()),
      length: () => history.length(),
      getActiveIndex: () => history.index(),
      getAllEntries: () => history.entries(),
    };
  }

  // Each world gets its own Firefox sandbox: it shares the page's DOM but
  // none of its scripts, like Chromium's isolated worlds.
  async executeJavaScriptInIsolatedWorld(worldId, sources, userGesture = false) {
    this.refuseRemOnErrorPage();
    const code = (sources || []).map((item) => item.code).join(";\n");
    return this.evaluateIn(`raticode-world-${Number(worldId) || 0}`, code, { userActivation: userGesture === true });
  }

  // While Rem uses the tab, Firefox's error and warning pages refuse Rem's page
  // tools; Rem can still go back or open another page. The user's own tab
  // works as Firefox's does.
  refuseRemOnErrorPage() {
    if (FIREFOX_ERROR_PAGE.test(this.url) && this.debugger.isAttached()) {
      throw new Error(`Firefox is showing its own error or warning page for ${this.url}, such as a certificate warning. Rem can't act on it; go back or open another page, or ask the user.`);
    }
  }

  async evaluateIn(sandbox, expression, { userActivation = false } = {}) {
    await this.alive();
    const response = await this.command("script.evaluate", {
      expression,
      target: sandbox ? { context: this.context, sandbox } : { context: this.context },
      awaitPromise: true,
      resultOwnership: "none",
      userActivation,
      serializationOptions: { maxObjectDepth: 32, maxDomDepth: 0, includeShadowTree: "none" },
    });
    if (response.type === "exception") {
      const details = response.exceptionDetails || {};
      throw new Error(details.text || "The script threw an error.");
    }
    return fromRemoteValue(response.result);
  }

  async capturePage() {
    await this.alive();
    const { data } = await this.command("browsingContext.captureScreenshot", { context: this.context, origin: "viewport" });
    const buffer = Buffer.from(data, "base64");
    if (!this.nativeImage) return buffer;
    return this.nativeImage.createFromBuffer(buffer);
  }

  setAudioMuted(muted) {
    this.muted = muted === true;
    void this.applyMute();
  }

  async applyMute() {
    if (!this.context || this.dead) return;
    await this.evaluateIn(STATE_SANDBOX, `for (const media of document.querySelectorAll("audio,video")) media.muted = ${this.muted}; true`).catch(() => {});
    await this.sound.check();
  }

  // Firefox's window.find selects each match, so the highlight appears in the stream.
  findInPage(text, { forward = true, findNext = true } = {}) {
    const requestId = (this.findRequestId = (this.findRequestId || 0) + 1);
    const query = String(text);
    // A new search starts from the top of the page, not the previous match.
    const fresh = findNext !== false && this.findQuery !== query;
    void this.alive().then(async () => {
      const result = await this.evaluateIn(STATE_SANDBOX, `(() => {
        const query = ${JSON.stringify(query)};
        if (${fresh}) {
          // A focused control keeps Firefox's selection where it is, so no
          // match gets selected. The user is typing in Raticode's find bar.
          if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur?.();
          getSelection()?.removeAllRanges();
        }
        const haystack = (document.body?.innerText || "").toLowerCase();
        const needle = query.toLowerCase();
        let matches = 0;
        for (let at = haystack.indexOf(needle); needle && at >= 0; at = haystack.indexOf(needle, at + needle.length)) matches += 1;
        const found = matches ? window.find(query, false, ${forward === false}, true) : false;
        return { matches, found };
      })()`);
      if (requestId !== this.findRequestId) return;
      const matches = Number(result?.matches) || 0;
      if (findNext !== false || this.findQuery !== query) this.findOrdinal = matches ? 1 : 0;
      else if (matches) this.findOrdinal = ((this.findOrdinal || 0) + (forward === false ? matches - 1 : 1) - 1) % matches + 1;
      this.findQuery = query;
      this.emit("found-in-page", {}, { requestId, matches, activeMatchOrdinal: matches ? this.findOrdinal : 0, finalUpdate: true });
    }).catch(() => {});
    return requestId;
  }

  stopFindInPage() {
    this.findQuery = "";
    void this.evaluateIn(STATE_SANDBOX, "getSelection()?.removeAllRanges(); true").catch(() => {});
  }

  // -------------------------------------------------------------------------
  // Lifecycle

  async alive() {
    await this.ready;
    if (this.destroyed) throw new Error("The Firefox tab was closed.");
    if (this.dead) throw new Error(this.lastError || "Firefox stopped. Reload the tab to start it again.");
  }

  async revive() {
    if (this.destroyed) return;
    this.dead = false;
    this.lastError = "";
    this.loading = true;
    this.emit("did-start-loading");
    this.ready = this.start();
    await this.ready;
  }

  // Firefox stopped or crashed. The tab stays open and restarts on reload.
  markDead(reason = "Firefox stopped") {
    // The first reason given stays: "signing in in a window" beats the plain
    // "Firefox stopped" that follows when the process exits.
    if (this.destroyed || this.dead) return;
    this.dead = true;
    this.loading = false;
    this.lastError = `${reason}. Reload the tab to start Firefox again.`;
    if (this.context) this.firefox.unregister(this.context);
    this.context = "";
    for (const waiter of this.waiters) waiter.reject(new Error(this.lastError));
    this.waiters.clear();
    this.emit("render-process-gone", {}, { reason });
    this.emit("did-stop-loading");
  }

  close() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.visible = false;
    clearTimeout(this.stateTimer);
    this.wakeFrames();
    for (const waiter of this.waiters) waiter.reject(new Error("The Firefox tab was closed."));
    this.waiters.clear();
    if (this.context) {
      const context = this.context;
      this.firefox.unregister(context);
      void this.firefox.send("browsingContext.close", { context, promptUnload: false }).catch(() => {});
    }
    this.emit("destroyed");
    this.removeAllListeners();
  }

  command(method, params, options) {
    return this.firefox.send(method, params, options);
  }

  // Input that opens alert() or confirm() keeps Firefox's command waiting
  // until the dialog is answered. Return as soon as the dialog shows.
  input(actions) {
    const sent = this.command("input.performActions", { context: this.context, actions });
    sent.catch(() => {});
    if (this.dialog) return sent;
    let listener;
    const opened = new Promise((resolve) => { listener = resolve; this.once("firefox-dialog-opened", listener); });
    return Promise.race([sent, opened]).finally(() => this.off("firefox-dialog-opened", listener));
  }

  waitForNavigation() {
    let entry;
    const promise = new Promise((resolve, reject) => { entry = { resolve, reject }; });
    this.waiters.add(entry);
    return {
      promise: promise.finally(() => this.waiters.delete(entry)),
      cancel: () => { this.waiters.delete(entry); entry.resolve(); },
    };
  }

  settleWaiters(error) {
    for (const waiter of this.waiters) {
      if (error) waiter.reject(error); else waiter.resolve();
    }
    this.waiters.clear();
  }

  // -------------------------------------------------------------------------
  // Firefox events

  handleEvent(method, params) {
    if (this.destroyed) return;
    switch (method) {
      case "browsingContext.navigationStarted": {
        this.navigation = params.navigation || "";
        this.loading = true;
        this.lastError = "";
        this.markActive();
        this.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false, url: params.url }, params.url);
        this.emit("did-start-loading");
        break;
      }
      case "browsingContext.navigationCommitted":
        this.commitUrl(params.url);
        break;
      case "browsingContext.fragmentNavigated":
      case "browsingContext.historyUpdated":
        if (params.url) this.url = params.url;
        void this.refreshState({ sameDocument: true });
        this.emit("did-navigate-in-page", {}, this.url, true);
        this.markActive();
        break;
      case "browsingContext.domContentLoaded":
        if (params.url && params.url !== this.url) this.commitUrl(params.url);
        void this.refreshState();
        this.markActive();
        break;
      case "browsingContext.load":
        if (params.url && params.url !== this.url) this.commitUrl(params.url);
        this.loading = false;
        void this.refreshState().finally(() => {
          this.emit("did-stop-loading");
          this.settleWaiters();
        });
        // Only the tab's own mute is applied to a new page; a site's muted
        // autoplaying video stays muted.
        if (this.muted) void this.applyMute(); else void this.sound.check();
        this.markActive();
        break;
      case "browsingContext.navigationFailed":
        // A navigation replaced by a newer one, or one that became a
        // download, is not a failure of the page on screen.
        if (params.navigation && this.navigation && params.navigation !== this.navigation) break;
        if (params.navigation && this.downloads?.has(params.navigation)) {
          this.loading = false;
          this.emit("did-stop-loading");
          this.settleWaiters(new Error("ERR_ABORTED"));
          break;
        }
        this.loading = false;
        this.lastError = "Firefox could not load this page";
        this.emit("did-fail-load", {}, -2, this.lastError, params.url || this.url, true);
        this.emit("did-stop-loading");
        this.settleWaiters(new Error(`${this.lastError}: ${params.url || this.url}`));
        this.markActive();
        break;
      case "browsingContext.navigationAborted":
        this.loading = false;
        this.emit("did-stop-loading");
        this.settleWaiters(new Error("ERR_ABORTED"));
        break;
      case "browsingContext.userPromptOpened":
        this.dialog = { type: String(params.type || "alert"), message: String(params.message || "").slice(0, 2000), defaultValue: String(params.defaultValue || "").slice(0, 2000) };
        this.markActive();
        this.emit("firefox-dialog-opened");
        if (this.debugger.isAttached()) {
          this.debugger.emit("message", {}, "Page.javascriptDialogOpening", { type: this.dialog.type, message: this.dialog.message, defaultPrompt: this.dialog.defaultValue });
        } else {
          this.emit("firefox-dialog", { ...this.dialog });
        }
        break;
      case "browsingContext.userPromptClosed":
        this.dialog = null;
        if (this.debugger.isAttached()) this.debugger.emit("message", {}, "Page.javascriptDialogClosed", {});
        this.markActive();
        break;
      case "browsingContext.contextDestroyed":
        if (params.context === this.context && !this.destroyed) this.markDead("The Firefox tab closed");
        break;
      // A link with a download attribute, including blob: and data: links a
      // page builds itself, is no navigation: Firefox sends navigation null
      // and names the download by its own id instead.
      case "browsingContext.downloadWillBegin":
        if (params.navigation) this.downloads = new Set([...(this.downloads || [])].slice(-20)).add(params.navigation);
        this.emit("firefox-download-begin", { url: params.url, filename: params.suggestedFilename || "", navigation: downloadKey(params) });
        break;
      case "browsingContext.downloadEnd":
        this.emit("firefox-download-end", { url: params.url, status: params.status, filepath: params.filepath || "", navigation: downloadKey(params) });
        break;
      case "input.fileDialogOpened":
        this.emit("firefox-file-dialog", { element: params.element?.sharedId || "", multiple: params.multiple === true });
        break;
      case "network.beforeRequestSent":
        void this.checkRequest(params);
        break;
      case "popup":
        void this.handlePopup(params);
        break;
      default:
        break;
    }
  }

  commitUrl(url) {
    if (!url) return;
    this.url = url;
    this.pendingCommit = true;
    this.emit("did-navigate", {}, url);
    void this.refreshState();
  }

  // Title, favicon and history position, read in a sandbox the page cannot see.
  async refreshState({ sameDocument = false } = {}) {
    if (!this.context || this.dead || this.destroyed) return;
    let state;
    try {
      state = await this.evaluateIn(STATE_SANDBOX, `[history.length, performance.getEntriesByType("navigation")[0]?.type || "", document.title, document.querySelector('link[rel~="icon"]')?.href || ""]`);
    } catch {
      return;
    }
    if (!Array.isArray(state) || this.destroyed) return;
    const [historyLength, kind, title, favicon] = state;
    if (this.pendingCommit || sameDocument) {
      const previousLength = this.historyLength || 0;
      const pushed = sameDocument ? Number(historyLength) > previousLength : true;
      if (sameDocument && !pushed) this.history.commit(this.url, { kind: "replace" });
      else this.history.commit(this.url, { kind: sameDocument ? "navigate" : kind, historyLength: Number(historyLength) || 0 });
      this.pendingCommit = false;
    }
    this.historyLength = Number(historyLength) || 0;
    if (typeof title === "string" && title !== this.title) {
      this.title = title;
      this.history.setTitle(title);
      this.emit("page-title-updated", {}, title);
    }
    const icon = typeof favicon === "string" && /^https?:/i.test(favicon) ? favicon : this.url.startsWith("http") ? `${new URL(this.url).origin}/favicon.ico` : "";
    if (icon !== this.favicon) {
      this.favicon = icon;
      this.emit("page-favicon-updated", {}, icon ? [icon] : []);
    }
  }

  // Pages open pop-ups as Firefox windows. Raticode asks the same window-open
  // handler as for its own tabs, then closes the Firefox window.
  async handlePopup(params) {
    const popup = params.context;
    let url = params.url && params.url !== "about:blank" ? params.url : "";
    if (!url) {
      const deadline = Date.now() + POPUP_URL_TIMEOUT_MS;
      while (!url && Date.now() < deadline) {
        await delay(50);
        try {
          const tree = await this.command("browsingContext.getTree", { root: popup, maxDepth: 0 });
          const current = tree.contexts?.[0]?.url || "";
          if (current && current !== "about:blank") url = current;
        } catch { break; }
      }
    }
    void this.command("browsingContext.close", { context: popup, promptUnload: false }).catch(() => {});
    if (!url) return;
    const decision = this.windowOpenHandler({ url, disposition: "foreground-tab", features: "", postBody: null }) || {};
    if (decision.action === "allow") this.emit("firefox-open-tab", { url });
  }

  // Rem's own tabs check every navigation against its site rules before
  // the request leaves Firefox, as will-navigate does for Raticode tabs.
  async installNavigationGuard() {
    this.guardNavigation = true;
    if (!this.context || this.guardInstalled) return;
    this.guardInstalled = true;
    await this.command("session.subscribe", { events: ["network.beforeRequestSent"], contexts: [this.context] });
    await this.command("network.addIntercept", { phases: ["beforeRequestSent"], contexts: [this.context] });
  }

  async checkRequest(params) {
    if (!params.isBlocked) return;
    const request = params.request?.request;
    const url = params.request?.url || "";
    let allowed = true;
    if (params.navigation && params.context === this.context) {
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, url };
      this.emit(params.redirectCount ? "will-redirect" : "will-navigate", event, url);
      allowed = !event.defaultPrevented;
    }
    try {
      if (allowed) await this.command("network.continueRequest", { request });
      else {
        await this.command("network.failRequest", { request });
        // Firefox reports nothing more for a refused navigation, so the tab
        // says what happened and stops loading here.
        this.loading = false;
        this.lastError = "Rem's site rules in Settings > Browser blocked this page";
        this.emit("did-fail-load", {}, -20, this.lastError, url, true);
        this.emit("did-stop-loading");
        this.settleWaiters(new Error(`${this.lastError}: ${url}`));
        this.markActive();
      }
    } catch { /* The request ended first. */ }
  }

  // -------------------------------------------------------------------------
  // Viewport and frames

  setView(view, visible) {
    const next = clampView(view);
    if (next) this.view = next;
    this.visible = Boolean(visible && next);
    if (this.visible) {
      this.frame.lastHash = "";
      this.markActive();
      void this.applyViewport();
      void this.streamFrames().catch((error) => this.log("warn", `Firefox frame stream stopped: ${error.message}`));
    } else {
      this.wakeFrames();
    }
  }

  setPageZoom(factor) {
    const zoom = Number(factor);
    this.pageZoom = Number.isFinite(zoom) && zoom > 0.2 && zoom < 6 ? zoom : 1;
    void this.applyViewport();
    this.markActive();
  }

  // Layout happens at the view's CSS size divided by the page zoom; the
  // device pixel ratio keeps the image sharp on the studio's display.
  async applyViewport() {
    if (!this.context || this.dead) return;
    const view = this.view || FALLBACK_VIEW;
    const width = Math.max(100, Math.round(view.width / this.pageZoom));
    const height = Math.max(100, Math.round(view.height / this.pageZoom));
    const devicePixelRatio = Math.min(Math.max(view.scale * this.pageZoom, 0.5), 6);
    const key = `${width}x${height}@${devicePixelRatio}`;
    if (key === this.viewportKey) return;
    this.viewportKey = key;
    await this.command("browsingContext.setViewport", { context: this.context, viewport: { width, height }, devicePixelRatio }).catch((error) => {
      this.viewportKey = "";
      this.log("warn", `Firefox viewport change failed: ${error.message}`);
    });
  }

  // At most one read per interval, plus one after the last change.
  scheduleStateRefresh() {
    if (this.stateTimer) return;
    const wait = Math.max(0, (this.stateReadAt || 0) + STATE_REFRESH_MS - Date.now());
    this.stateTimer = setTimeout(() => {
      this.stateTimer = null;
      this.stateReadAt = Date.now();
      void this.refreshState();
    }, wait);
    this.stateTimer.unref?.();
  }

  markActive() {
    this.activityAt = Date.now();
    this.wakeFrames();
  }

  wakeFrames() {
    const wake = this.frame.wake;
    this.frame.wake = null;
    wake?.();
  }

  ackFrame(seq) {
    if (Number(seq) === this.frame.seq) this.frame.awaitingAck = false;
    this.wakeFrames();
  }

  async streamFrames() {
    const frame = this.frame;
    if (frame.streaming) return;
    frame.streaming = true;
    try {
      await this.ready;
      while (this.visible && !this.destroyed && !this.dead) {
        const now = Date.now();
        if (frame.awaitingAck && now < frame.ackDeadline) {
          await this.sleepFrames(frame.ackDeadline - now);
          continue;
        }
        const sinceActivity = now - Math.max(this.activityAt, frame.lastChange);
        const interval = sinceActivity < ACTIVE_WINDOW_MS || this.loading ? ACTIVE_FRAME_MS : sinceActivity < SETTLING_WINDOW_MS ? SETTLING_FRAME_MS : IDLE_FRAME_MS;
        const wait = frame.lastAt + interval - now;
        if (wait > 0) {
          await this.sleepFrames(wait);
          continue;
        }
        frame.lastAt = now;
        let data;
        const started = performance.now();
        try {
          ({ data } = await this.command("browsingContext.captureScreenshot", {
            context: this.context,
            origin: "viewport",
            format: { type: "image/jpeg", quality: FRAME_QUALITY },
          }, { timeout: 10000 }));
        } catch {
          await delay(250);
          continue;
        }
        if (typeof data !== "string" || !data) {
          await delay(250);
          continue;
        }
        const captureMs = performance.now() - started;
        const buffer = Buffer.from(data, "base64");
        const hash = crypto.createHash("sha1").update(buffer).digest("hex");
        if (hash === frame.lastHash) continue;
        frame.lastHash = hash;
        frame.lastChange = Date.now();
        // Pages change their title from script; follow while the page changes.
        this.scheduleStateRefresh();
        frame.seq += 1;
        frame.awaitingAck = true;
        frame.ackDeadline = Date.now() + ACK_TIMEOUT_MS;
        this.stats.frames += 1;
        this.stats.captureMs += captureMs;
        this.stats.bytes += buffer.length;
        const view = this.view || FALLBACK_VIEW;
        this.emit("firefox-frame", { seq: frame.seq, data: buffer, width: view.width, height: view.height, captureMs: Math.round(captureMs) });
      }
    } finally {
      frame.streaming = false;
    }
  }

  sleepFrames(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.frame.wake = null; resolve(); }, Math.max(1, ms));
      this.frame.wake = () => { clearTimeout(timer); resolve(); };
    });
  }

  // -------------------------------------------------------------------------
  // The user's input from the studio

  handleInput(events) {
    for (const item of Array.isArray(events) ? events.slice(0, 200) : []) {
      if (!item || typeof item !== "object") continue;
      if (item.t === "move") {
        // Only the latest pointer position matters while input is queued.
        const queued = this.pendingMove;
        this.pendingMove = item;
        if (queued) continue;
        this.enqueue(() => {
          const move = this.pendingMove;
          this.pendingMove = null;
          return move ? this.userInput(move) : undefined;
        });
        continue;
      }
      this.enqueue(() => this.userInput(item));
    }
    this.markActive();
  }

  enqueue(task) {
    this.inputQueue = this.inputQueue.then(task).catch((error) => {
      if (!this.destroyed && !this.dead) this.log("debug", `Firefox input failed: ${error.message}`);
    });
  }

  point(item) {
    const x = Math.round(Number(item.x) / this.pageZoom);
    const y = Math.round(Number(item.y) / this.pageZoom);
    const view = this.view || FALLBACK_VIEW;
    const maxX = Math.round(view.width / this.pageZoom) - 1;
    const maxY = Math.round(view.height / this.pageZoom) - 1;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x: Math.min(Math.max(x, 0), maxX), y: Math.min(Math.max(y, 0), maxY) };
  }

  pointer(actions) {
    return this.input([{ type: "pointer", id: "user-mouse", parameters: { pointerType: "mouse" }, actions }]);
  }

  async userInput(item) {
    await this.alive();
    switch (item.t) {
      case "move": {
        const at = this.point(item);
        if (!at) return;
        await this.pointer([{ type: "pointerMove", x: at.x, y: at.y, origin: "viewport" }]);
        if (Date.now() - this.cursorAt > CURSOR_INTERVAL_MS) {
          this.cursorAt = Date.now();
          void this.reportCursor(at);
        }
        return;
      }
      case "down":
      case "up": {
        const at = this.point(item);
        if (!at) return;
        const button = [0, 1, 2].includes(item.button) ? item.button : 0;
        this.emit("input-event", {}, { type: item.t === "down" ? "mouseDown" : "mouseUp" });
        await this.pointer([
          { type: "pointerMove", x: at.x, y: at.y, origin: "viewport" },
          { type: item.t === "down" ? "pointerDown" : "pointerUp", button },
        ]);
        if (item.t === "up" && button === 0) void this.reportSelect(at);
        return;
      }
      case "wheel": {
        const at = this.point(item);
        if (!at) return;
        const deltaX = Math.round(Math.max(-5000, Math.min(5000, Number(item.dx) || 0)) / this.pageZoom);
        const deltaY = Math.round(Math.max(-5000, Math.min(5000, Number(item.dy) || 0)) / this.pageZoom);
        if (!deltaX && !deltaY) return;
        await this.command("input.performActions", {
          context: this.context,
          actions: [{ type: "wheel", id: "user-wheel", actions: [{ type: "scroll", x: at.x, y: at.y, deltaX, deltaY, origin: "viewport" }] }],
        });
        return;
      }
      case "keydown":
      case "keyup": {
        const value = webdriverKey(item.key);
        if (!value) return;
        if (item.t === "keydown") {
          this.pressedKeys.add(value);
          this.emit("input-event", {}, { type: "keyDown" });
        } else {
          if (!this.pressedKeys.has(value)) return;
          this.pressedKeys.delete(value);
        }
        await this.input([{ type: "key", id: "user-keyboard", actions: [{ type: item.t === "keydown" ? "keyDown" : "keyUp", value }] }]);
        return;
      }
      case "text":
        this.emit("input-event", {}, { type: "char" });
        await this.typeText(String(item.text ?? ""));
        return;
      case "copy":
      case "cut":
        await this.copySelection();
        return;
      case "focus":
        this.focused = true;
        return;
      case "blur":
        this.focused = false;
        this.pressedKeys.clear();
        await this.command("input.releaseActions", { context: this.context }).catch(() => {});
        return;
      case "select-option":
        await this.chooseOption(Number(item.index));
        return;
      case "file-dialog":
        return;
      default:
        return;
    }
  }

  // Pasted or composed text arrives as trusted key presses.
  async typeText(text) {
    const characters = [...text.replace(/\r\n?/g, "\n")].slice(0, MAX_TEXT_INPUT);
    for (let start = 0; start < characters.length; start += TEXT_CHUNK) {
      const actions = characters.slice(start, start + TEXT_CHUNK).flatMap((character) => {
        const value = character === "\n" ? WEBDRIVER_KEYS.Enter : character === "\t" ? WEBDRIVER_KEYS.Tab : character;
        return [{ type: "keyDown", value }, { type: "keyUp", value }];
      });
      await this.input([{ type: "key", id: "user-keyboard", actions }]);
    }
  }

  // Firefox's clipboard is its own while it runs without a window, so copy
  // and cut put the page's selection on the system clipboard here.
  async copySelection() {
    const text = await this.evaluateIn(STATE_SANDBOX, `(() => {
      const active = document.activeElement;
      if (active && typeof active.selectionStart === "number" && /^(?:input|textarea)$/i.test(active.localName) && active.type !== "password") {
        return active.value.slice(active.selectionStart, active.selectionEnd);
      }
      return String(getSelection() || "");
    })()`).catch(() => "");
    if (text && this.clipboard) await Promise.resolve(this.clipboard.writeText(String(text))).catch(() => {});
  }

  async reportCursor(at) {
    const cursor = await this.evaluateIn(STATE_SANDBOX, `(() => {
      const element = document.elementFromPoint(${at.x}, ${at.y});
      if (!element) return "default";
      const style = getComputedStyle(element).cursor;
      if (style && style !== "auto") return style;
      if (element.closest("a[href],button,summary,label[for],select,[role=button],[role=link]")) return "pointer";
      if (element.isContentEditable || /^(?:textarea)$/i.test(element.localName) || element.localName === "input" && !/^(?:button|checkbox|radio|submit|reset|range|color|file|image)$/i.test(element.type)) return "text";
      return "default";
    })()`).catch(() => "");
    if (typeof cursor === "string" && cursor !== this.cursor) {
      this.cursor = cursor;
      this.emit("firefox-cursor", { cursor: cursor.slice(0, 40) });
    }
  }

  // A headless Firefox draws no drop-down for <select>, so the studio shows
  // the options itself after a click opens one.
  async reportSelect(at) {
    const choice = await this.evaluateIn(STATE_SANDBOX, `(() => {
      const element = document.activeElement;
      if (!element || element.localName !== "select" || element.multiple || element.size > 1 || element.disabled) return null;
      const rect = element.getBoundingClientRect();
      if (${at.x} < rect.left || ${at.x} > rect.right || ${at.y} < rect.top || ${at.y} > rect.bottom) return null;
      return {
        rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
        options: [...element.options].slice(0, 500).map((option, index) => ({ index, label: (option.label || option.text || "").slice(0, 200), disabled: option.disabled, selected: option.selected, group: option.parentElement?.localName === "optgroup" ? (option.parentElement.label || "").slice(0, 100) : "" })),
      };
    })()`).catch(() => null);
    if (!choice?.options?.length) return;
    const zoom = this.pageZoom;
    const rect = choice.rect || {};
    this.emit("firefox-select", {
      rect: { x: rect.x * zoom, y: rect.y * zoom, width: rect.width * zoom, height: rect.height * zoom },
      options: choice.options,
    });
  }

  async chooseOption(index) {
    if (!Number.isInteger(index) || index < 0) return;
    await this.evaluateIn(STATE_SANDBOX, `(() => {
      const element = document.activeElement;
      if (!element || element.localName !== "select") return false;
      const option = element.options[${index}];
      if (!option || option.disabled) return false;
      element.selectedIndex = ${index};
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`, { userActivation: true });
    this.markActive();
  }

  async setFiles(element, files) {
    await this.alive();
    await this.command("input.setFiles", { context: this.context, element: { sharedId: element }, files });
  }

  async answerDialog(accept, text) {
    await this.alive();
    await this.command("browsingContext.handleUserPrompt", {
      context: this.context,
      accept: accept === true,
      ...(typeof text === "string" ? { userText: text.slice(0, 10000) } : {}),
    });
  }

  // -------------------------------------------------------------------------
  // Rem's input, at the centre of the element the page agent located

  async nativeClick({ x, y, button = "left", clicks = 1 }) {
    await this.alive();
    this.refuseRemOnErrorPage();
    const code = { left: 0, middle: 1, right: 2 }[button] ?? 0;
    const at = { x: Math.round(x), y: Math.round(y) };
    const actions = [{ type: "pointerMove", x: at.x, y: at.y, origin: "viewport" }];
    for (let count = 0; count < (clicks === 2 ? 2 : 1); count += 1) actions.push({ type: "pointerDown", button: code }, { type: "pointerUp", button: code });
    await this.input([{ type: "pointer", id: "rem-mouse", parameters: { pointerType: "mouse" }, actions }]);
    this.markActive();
  }

  async nativeHover({ x, y }) {
    await this.alive();
    this.refuseRemOnErrorPage();
    await this.command("input.performActions", {
      context: this.context,
      actions: [{ type: "pointer", id: "rem-mouse", parameters: { pointerType: "mouse" }, actions: [{ type: "pointerMove", x: Math.round(x), y: Math.round(y), origin: "viewport" }] }],
    });
    this.markActive();
  }

  async nativePress(key) {
    await this.alive();
    this.refuseRemOnErrorPage();
    const value = webdriverKey(key.key === " " ? " " : key.key);
    if (!value) throw new Error(`Firefox cannot press ${key.key}.`);
    const modifiers = Object.entries(MODIFIER_KEYS).filter(([name]) => key[name]).map(([, code]) => code);
    const actions = [
      ...modifiers.map((code) => ({ type: "keyDown", value: code })),
      { type: "keyDown", value },
      { type: "keyUp", value },
      ...modifiers.reverse().map((code) => ({ type: "keyUp", value: code })),
    ];
    await this.input([{ type: "key", id: "rem-keyboard", actions }]);
    this.markActive();
  }
}

// agent-browser.cjs speaks a handful of Chrome DevTools Protocol commands.
// These are the Firefox equivalents over WebDriver BiDi.
function createDebuggerShim(contents) {
  const shim = new EventEmitter();
  let attached = false;
  const searches = new Map();
  let nextSearch = 0;
  shim.isAttached = () => attached;
  shim.attach = () => {
    if (contents.destroyed) throw new Error("the tab was closed");
    attached = true;
    // A dialog that opened before Rem attached still needs an answer.
    if (contents.dialog) {
      const dialog = contents.dialog;
      setImmediate(() => shim.emit("message", {}, "Page.javascriptDialogOpening", { type: dialog.type, message: dialog.message, defaultPrompt: dialog.defaultValue }));
    }
  };
  shim.detach = () => {
    if (!attached) return;
    attached = false;
    shim.emit("detach");
  };
  shim.sendCommand = async (method, params = {}) => {
    if (PAGE_COMMANDS.has(method)) contents.refuseRemOnErrorPage?.();
    switch (method) {
      // Nothing to do in Firefox. Its tabs keep their last on-screen size when
      // hidden, so Rem's fallback viewport is not needed either.
      case "Emulation.setFocusEmulationEnabled":
      case "Page.enable":
      case "DOM.enable":
      case "Overlay.enable":
      case "Overlay.hideHighlight":
      case "DOM.getDocument":
      case "Emulation.setDeviceMetricsOverride":
      case "Emulation.clearDeviceMetricsOverride":
        return {};
      case "Overlay.highlightRect":
        contents.emit("firefox-highlight", { x: params.x * contents.pageZoom, y: params.y * contents.pageZoom, width: params.width * contents.pageZoom, height: params.height * contents.pageZoom });
        return {};
      case "Runtime.evaluate": {
        await contents.alive();
        const response = await contents.command("script.evaluate", {
          expression: String(params.expression ?? ""),
          target: { context: contents.context },
          awaitPromise: params.awaitPromise !== false,
          resultOwnership: "none",
          userActivation: params.userGesture === true,
          serializationOptions: { maxObjectDepth: 32, maxDomDepth: 0, includeShadowTree: "none" },
        });
        if (response.type === "exception") {
          const details = response.exceptionDetails || {};
          return { exceptionDetails: { text: details.text || "The expression threw an error.", exception: { description: details.text || "" } } };
        }
        const value = fromRemoteValue(response.result);
        const description = remoteDescription(response.result);
        return { result: description ? { type: "object", description } : { type: value === undefined ? "undefined" : typeof value, value } };
      }
      case "DOM.performSearch": {
        await contents.alive();
        const { nodes = [] } = await contents.command("browsingContext.locateNodes", {
          context: contents.context,
          locator: { type: "css", value: String(params.query ?? "") },
          maxNodeCount: 50,
        });
        nextSearch += 1;
        const searchId = `firefox-${nextSearch}`;
        searches.set(searchId, nodes.map((node) => node.sharedId).filter(Boolean));
        return { searchId, resultCount: searches.get(searchId).length };
      }
      case "DOM.getSearchResults": {
        const found = searches.get(params.searchId) || [];
        const from = Math.max(0, Number(params.fromIndex) || 0);
        const to = Math.min(found.length, Number(params.toIndex) || found.length);
        return { nodeIds: found.slice(from, to).map((sharedId) => `${params.searchId}:${sharedId}`) };
      }
      case "DOM.discardSearchResults":
        searches.delete(params.searchId);
        return {};
      case "DOM.setFileInputFiles": {
        const sharedId = String(params.nodeId ?? "").split(":").slice(1).join(":");
        if (!sharedId) throw new Error("The file input disappeared. Take a new snapshot.");
        await contents.setFiles(sharedId, Array.isArray(params.files) ? params.files : []);
        return {};
      }
      case "Page.handleJavaScriptDialog":
        await contents.answerDialog(params.accept !== false, typeof params.promptText === "string" ? params.promptText : undefined);
        return {};
      default:
        throw new Error(`Firefox tabs do not support ${method}.`);
    }
  };
  return shim;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = {
  FALLBACK_VIEW,
  FirefoxContents,
  clampView,
  createDebuggerShim,
  createHistoryTracker,
  downloadKey,
  fromRemoteValue,
  webdriverKey,
};
