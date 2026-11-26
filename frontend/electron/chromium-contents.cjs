// One tab of a headless Chromium-family browser (Google Chrome), presented
// to the integrated browser with the part of Electron's webContents interface
// that browser-sessions.cjs and agent-browser.cjs use. Rem's site rules,
// approvals, pause and logging therefore apply to Chrome tabs through the same
// code as Raticode and Firefox tabs.
//
// Chrome renders without a window. The tab streams its viewport to the
// studio with the DevTools screencast, which the studio draws on a canvas;
// the user's mouse and keyboard come back as DevTools input. Both the user's
// and Rem's input are trusted: Chrome treats them like a person using the page.
//
// Events for the studio use the stream-* names (stream-frame, stream-cursor
// and so on), the same messages Firefox tabs send as firefox-*. What doesn't
// depend on DevTools is in stream-tab.cjs.
const { EventEmitter } = require("node:events");
const pageMenu = require("./stream-page-menu.cjs");
const { FALLBACK_VIEW, STATE_WORLD, StreamTab, clampView } = require("./stream-tab.cjs");
const { mediaPageHook } = require("./browser-media.cjs");

const FRAME_QUALITY = 82;
const ACK_TIMEOUT_MS = 500;
const POPUP_URL_TIMEOUT_MS = 2000;
const MAX_TEXT_INPUT = 20000;
const DOUBLE_CLICK_MS = 500;
// Chromium's own pages, under its own schemes and those of browsers built on it.
const INTERNAL_PAGE = /^\s*(?:about:(?!blank(?:[?#]|$)|srcdoc(?:[?#]|$))|chrome:|chrome-extension:|chrome-untrusted:|chrome-search:|devtools:|view-source:|edge:|extension:)/i;

function isInternalPage(url) {
  return INTERNAL_PAGE.test(String(url ?? ""));
}

// DevTools key codes for keys that have no printable character.
const NAMED_KEYS = {
  Backspace: [8, "Backspace"], Tab: [9, "Tab"], Enter: [13, "Enter", "\r"], Shift: [16, "ShiftLeft"],
  Control: [17, "ControlLeft"], Alt: [18, "AltLeft"], Pause: [19, "Pause"], CapsLock: [20, "CapsLock"],
  Escape: [27, "Escape"], " ": [32, "Space", " "], PageUp: [33, "PageUp"], PageDown: [34, "PageDown"],
  End: [35, "End"], Home: [36, "Home"], ArrowLeft: [37, "ArrowLeft"], ArrowUp: [38, "ArrowUp"],
  ArrowRight: [39, "ArrowRight"], ArrowDown: [40, "ArrowDown"], Insert: [45, "Insert"], Delete: [46, "Delete"],
  Meta: [91, "MetaLeft"], OS: [91, "MetaLeft"], ContextMenu: [93, "ContextMenu"], AltGraph: [225, "AltRight"],
  F1: [112, "F1"], F2: [113, "F2"], F3: [114, "F3"], F4: [115, "F4"], F5: [116, "F5"], F6: [117, "F6"],
  F7: [118, "F7"], F8: [119, "F8"], F9: [120, "F9"], F10: [121, "F10"], F11: [122, "F11"], F12: [123, "F12"],
};
NAMED_KEYS.Space = NAMED_KEYS[" "];
const MODIFIER_BITS = { Alt: 1, AltGraph: 1, Control: 2, Meta: 4, OS: 4, Shift: 8 };
const PUNCTUATION_CODES = {
  "-": [189, "Minus"], "_": [189, "Minus"], "=": [187, "Equal"], "+": [187, "Equal"], "[": [219, "BracketLeft"], "{": [219, "BracketLeft"],
  "]": [221, "BracketRight"], "}": [221, "BracketRight"], "\\": [220, "Backslash"], "|": [220, "Backslash"], ";": [186, "Semicolon"],
  ":": [186, "Semicolon"], "'": [222, "Quote"], "\"": [222, "Quote"], ",": [188, "Comma"], "<": [188, "Comma"], ".": [190, "Period"],
  ">": [190, "Period"], "/": [191, "Slash"], "?": [191, "Slash"], "`": [192, "Backquote"], "~": [192, "Backquote"],
};
const SHIFTED_DIGITS = { "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0" };
// Chromium errors by name, for the error codes Raticode's tabs report. A
// certificate error is reported as a plain load failure: Chrome shows its own
// warning page, and Raticode's certificate exceptions don't reach Chrome.
const NET_ERRORS = {
  ERR_ABORTED: -3, ERR_BLOCKED_BY_CLIENT: -20, ERR_BLOCKED_BY_RESPONSE: -27, ERR_CONNECTION_CLOSED: -100,
  ERR_CONNECTION_RESET: -101, ERR_CONNECTION_REFUSED: -102, ERR_CONNECTION_ABORTED: -103, ERR_CONNECTION_FAILED: -104,
  ERR_NAME_NOT_RESOLVED: -105, ERR_INTERNET_DISCONNECTED: -106, ERR_SSL_PROTOCOL_ERROR: -107, ERR_ADDRESS_INVALID: -108,
  ERR_ADDRESS_UNREACHABLE: -109, ERR_CONNECTION_TIMED_OUT: -118, ERR_NAME_RESOLUTION_FAILED: -137,
  ERR_TOO_MANY_REDIRECTS: -310, ERR_UNSAFE_REDIRECT: -311, ERR_UNSAFE_PORT: -312, ERR_INVALID_RESPONSE: -320,
  ERR_EMPTY_RESPONSE: -324, ERR_HTTP_RESPONSE_CODE_FAILURE: -379, ERR_INVALID_URL: -300, ERR_DISALLOWED_URL_SCHEME: -301,
};

function netErrorCode(errorText) {
  const name = String(errorText || "").replace(/^net::/, "");
  return NET_ERRORS[name] ?? -2;
}

// What DevTools needs to press `key`: its key code, physical key and the
// text it types, if any.
function keyDefinition(key) {
  const value = String(key ?? "");
  if (Object.hasOwn(NAMED_KEYS, value)) {
    const [keyCode, code, text = ""] = NAMED_KEYS[value];
    return { key: value === "Space" ? " " : value, code, keyCode, text };
  }
  if ([...value].length !== 1) return null;
  if (/^[a-z]$/i.test(value)) return { key: value, code: `Key${value.toUpperCase()}`, keyCode: value.toUpperCase().charCodeAt(0), text: value };
  if (/^[0-9]$/.test(value)) return { key: value, code: `Digit${value}`, keyCode: value.charCodeAt(0), text: value };
  if (SHIFTED_DIGITS[value]) return { key: value, code: `Digit${SHIFTED_DIGITS[value]}`, keyCode: SHIFTED_DIGITS[value].charCodeAt(0), text: value };
  if (PUNCTUATION_CODES[value]) {
    const [keyCode, code] = PUNCTUATION_CODES[value];
    return { key: value, code, keyCode, text: value };
  }
  // Any other character, such as É or a symbol, types itself.
  return { key: value, code: "", keyCode: 0, text: value };
}

function modifierBits(keys) {
  let bits = 0;
  for (const key of keys) bits |= MODIFIER_BITS[key] || 0;
  return bits;
}

// macOS gives editing shortcuts to the window, which a headless browser has
// not got; DevTools runs the editing command directly instead.
function macCommands(key, modifiers) {
  if (!(modifiers & 4) || modifiers & 3) return [];
  const lower = String(key).toLowerCase();
  if (lower === "a") return ["selectAll"];
  if (lower === "z") return modifiers & 8 ? ["redo"] : ["undo"];
  return [];
}

// Chrome keeps the real back-forward list. The about:blank page every tab
// starts on is not part of it as far as the user is concerned.
function visibleHistory(history) {
  const entries = Array.isArray(history?.entries) ? history.entries : [];
  const offset = entries.length > 1 && entries[0]?.url === "about:blank" ? 1 : 0;
  const visible = entries.slice(offset);
  const index = Math.max(0, Math.min(visible.length - 1, Number(history?.currentIndex ?? 0) - offset));
  return { entries: visible, index: visible.length ? index : 0 };
}

class ChromiumContents extends StreamTab {
  constructor({ engine, kind, nativeImage, clipboard, log, platform = process.platform }) {
    super({ kind, nativeImage, clipboard, log, eventPrefix: "stream" });
    this.browser = engine.browser || { id: "chrome", name: "Chrome" };
    this.engine = this.browser.id;
    this.chromium = engine;
    this.platform = platform;
    this.targetId = "";
    this.sessionId = "";
    this.mainFrameId = "";
    this.loaderId = "";
    this.frames = new Set();
    this.history = { entries: [], index: 0 };
    this.worlds = new Map();
    this.buttons = 0;
    this.lastPress = { at: 0, x: 0, y: 0, button: "", count: 0 };
    this.popups = new Set();
    this.frame = { seq: 0, ack: "", ackTimer: null, casting: false };
    this.stats = { frames: 0, bytes: 0 };
    this.debugger = createDebuggerPassthrough(this);
    this.boot();
  }

  get name() { return this.browser.name; }
  isRunning() { return Boolean(this.sessionId); }

  async start() {
    const { targetId, sessionId, generation, userAgent } = await this.chromium.createContext(this.kind);
    if (this.destroyed) {
      void this.chromium.send("Target.closeTarget", { targetId }).catch(() => {});
      throw new Error("The tab closed.");
    }
    this.targetId = targetId;
    this.sessionId = sessionId;
    this.generation = generation;
    this.worlds.clear();
    this.chromium.register(sessionId, this);
    await this.command("Page.enable");
    const { frameTree } = await this.command("Page.getFrameTree");
    this.mainFrameId = frameTree?.frame?.id || targetId;
    this.loaderId = frameTree?.frame?.loaderId || "";
    this.frames = new Set([this.mainFrameId]);
    // Headless Chrome names itself HeadlessChrome; pages get the name of the
    // Chrome it is.
    if (/HeadlessChrome\//.test(userAgent || "")) {
      await this.command("Emulation.setUserAgentOverride", { userAgent: userAgent.replace("HeadlessChrome/", "Chrome/") }).catch(() => {});
    }
    // Pages behave as if their window had focus, so typing reaches them.
    await this.command("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
    // A windowless Chrome can't show a file chooser; Raticode asks instead.
    await this.command("Page.setInterceptFileChooserDialog", { enabled: true }).catch(() => {});
    // Remembers the page's previous and next handlers for the media bar.
    await this.command("Page.addScriptToEvaluateOnNewDocument", { source: `(${mediaPageHook})();` }).catch((error) => {
      this.log("warn", `${this.name} media controls are unavailable in this tab: ${error.message}`);
    });
    this.loading = false;
    this.viewportKey = "";
    await this.applyViewport();
    if (this.guardNavigation) await this.installNavigationGuard();
    if (this.visible) void this.startCast();
    this.emit("stream-ready");
  }

  // -------------------------------------------------------------------------
  // webContents interface

  // One file with the page and its pictures and styles (MHTML), which the
  // browser saves for itself; Raticode's "HTMLComplete" folder isn't offered.
  get savePageFormat() { return "mhtml"; }

  async savePage(filePath) {
    await this.alive();
    const { data } = await this.command("Page.captureSnapshot", { format: "mhtml" }, { timeout: 120000 });
    await require("node:fs").promises.writeFile(filePath, String(data || ""), { mode: 0o600 });
  }

  async printToPDF(options = {}) {
    await this.alive();
    const result = await this.command("Page.printToPDF", {
      printBackground: options.printBackground !== false,
      landscape: options.landscape === true,
    }, { timeout: 120000 });
    return Buffer.from(result.data, "base64");
  }

  async loadURL(url) {
    const target = String(url);
    // Chrome's own pages can change how Chrome runs, and Chrome opens
    // about:settings and similar as chrome:// pages. Only a Chrome window the
    // user opens shows them.
    if (INTERNAL_PAGE.test(target)) throw new Error(`${this.name}'s own pages can't be opened in Raticode tabs. Open them from Settings > Browser > ${this.name}.`);
    const waiter = await this.startNavigation();
    let result;
    try {
      result = await this.command("Page.navigate", { url: target });
    } catch (error) {
      throw this.navigationRefused(waiter, target, error);
    }
    if (result.isDownload) {
      waiter.cancel();
      this.loading = false;
      this.emit("did-stop-loading");
      throw new Error("ERR_ABORTED");
    }
    if (result.errorText) {
      waiter.cancel();
      this.loading = false;
      const name = String(result.errorText).replace(/^net::/, "");
      if (name !== "ERR_ABORTED") {
        // Chrome shows its error page for this address in a moment.
        this.errorPage = true;
        this.lastError = `${this.name} could not load this page (${name})`;
        this.emit("did-fail-load", {}, netErrorCode(name), name, target, true);
      }
      this.emit("did-stop-loading");
      throw new Error(`${name}: ${target}`);
    }
    // A same-page navigation has no new document to wait for.
    if (!result.loaderId) {
      waiter.cancel();
      this.loading = false;
      this.emit("did-stop-loading");
      return undefined;
    }
    return waiter.promise;
  }

  async reloadDocument(ignoreCache) {
    await this.command("Page.reload", { ignoreCache }).catch((error) => this.log("warn", `${this.name} reload failed: ${error.message}`));
  }

  stop() { void this.command("Page.stopLoading").catch(() => {}); }

  get navigationHistory() {
    const traverse = (target) => {
      const entry = this.history.entries[target];
      if (!entry || target === this.history.index) return;
      this.loading = true;
      this.documentLoading = true;
      void this.alive()
        .then(() => this.command("Page.navigateToHistoryEntry", { entryId: entry.id }))
        .then(() => this.refreshHistory())
        .catch((error) => {
          this.loading = false;
          this.log("warn", `${this.name} history navigation failed: ${error.message}`);
        });
    };
    return {
      canGoBack: () => this.history.index > 0,
      canGoForward: () => this.history.index < this.history.entries.length - 1,
      goBack: () => traverse(this.history.index - 1),
      goForward: () => traverse(this.history.index + 1),
      goToIndex: (target) => traverse(Number(target)),
      length: () => this.history.entries.length,
      getActiveIndex: () => this.history.index,
      getAllEntries: () => this.history.entries.map((entry) => ({ url: entry.url, title: entry.title || "" })),
    };
  }

  async refreshHistory() {
    if (!this.sessionId || this.dead || this.destroyed) return;
    const history = await this.command("Page.getNavigationHistory").catch(() => null);
    if (history) this.history = visibleHistory(history);
  }

  // While Rem drives this tab, Chrome's error and warning pages are off
  // limits: their "Proceed to … (unsafe)" link would let Rem past a
  // certificate error, which Raticode's own tabs never allow. Rem can still
  // go back or open another page.
  refuseRemOnErrorPage() {
    if (this.errorPage && this.debugger.isAttached()) {
      throw new Error(`${this.name} is showing its own error or warning page for ${this.url}, such as a certificate warning. Rem can't act on it; go back or open another page, or ask the user.`);
    }
  }

  // Each world is a Chrome isolated world.
  async worldContext(world, fresh = false) {
    const cached = this.worlds.get(world);
    if (!fresh && cached && cached.loaderId === this.loaderId) return cached.contextId;
    const { executionContextId } = await this.command("Page.createIsolatedWorld", { frameId: this.mainFrameId, worldName: world, grantUniveralAccess: false });
    this.worlds.set(world, { contextId: executionContextId, loaderId: this.loaderId });
    return executionContextId;
  }

  async evaluateIn(world, expression, { userGesture = false } = {}) {
    await this.alive();
    const evaluate = async (fresh) => this.command("Runtime.evaluate", {
      expression,
      contextId: await this.worldContext(world, fresh),
      awaitPromise: true,
      returnByValue: true,
      userGesture,
    });
    let response;
    try {
      response = await evaluate(false);
    } catch (error) {
      // The page moved on and took the world with it.
      if (!/context/i.test(error.message)) throw error;
      response = await evaluate(true);
    }
    if (response.exceptionDetails) {
      const details = response.exceptionDetails;
      throw new Error(details.exception?.description?.split("\n")[0] || details.text || "The script threw an error.");
    }
    return response.result?.value;
  }

  async screenshot() {
    const { data } = await this.command("Page.captureScreenshot", { format: "png" });
    return Buffer.from(data, "base64");
  }

  // -------------------------------------------------------------------------
  // Lifecycle

  stopFrames() { this.stopCast(); }

  // Chrome stopped or crashed, or the tab closed.
  releasePage(closing) {
    if (this.sessionId) {
      this.chromium.unregister(this.sessionId);
      if (closing) void this.chromium.send("Target.closeTarget", { targetId: this.targetId }).catch(() => {});
    }
    this.sessionId = "";
    this.targetId = "";
  }

  command(method, params = {}, options = {}) {
    if (!this.sessionId) return Promise.reject(new Error(`The ${this.name} tab is not running.`));
    return this.chromium.send(method, params, { ...options, sessionId: this.sessionId });
  }

  dispatchInput(method, params) {
    return this.command(method, params);
  }

  ownsFrame(frameId) {
    return Boolean(frameId && this.frames.has(frameId));
  }

  // -------------------------------------------------------------------------
  // Chrome events

  handleEvent(method, params) {
    if (this.destroyed) return;
    // Rem's tools see this tab's DevTools events while attached, as they
    // would through Electron's debugger.
    if (this.debugger.isAttached() && !method.startsWith("Browser.") && !method.startsWith("Target.") && method !== "popup" && method !== "Page.screencastFrame") {
      this.debugger.emit("message", {}, method, params);
    }
    const main = params.frameId === this.mainFrameId || params.frame?.id === this.mainFrameId;
    switch (method) {
      case "Page.frameAttached":
        if (params.frameId) this.frames.add(params.frameId);
        break;
      case "Page.frameDetached":
        if (params.frameId !== this.mainFrameId) this.frames.delete(params.frameId);
        break;
      case "Page.frameStartedNavigating": {
        if (!main) break;
        const sameDocument = /sameDocument/i.test(String(params.navigationType || ""));
        if (!sameDocument) this.documentLoading = true;
        this.emit("did-start-navigation", { isMainFrame: true, isSameDocument: sameDocument, url: params.url }, params.url);
        break;
      }
      case "Page.frameStartedLoading":
        if (!main) break;
        this.loading = true;
        this.documentLoading = true;
        this.lastError = "";
        this.emit("did-start-loading");
        break;
      case "Page.frameNavigated": {
        const frame = params.frame || {};
        if (frame.id) this.frames.add(frame.id);
        if (frame.parentId || frame.id !== this.mainFrameId) break;
        this.loaderId = frame.loaderId || this.loaderId;
        // A new document committed; its DOM is not ready yet.
        this.documentLoading = true;
        this.worlds.clear();
        // Chrome's own error and warning pages, such as "Your connection is
        // not private", which can let the reader past a certificate error.
        this.errorPage = Boolean(frame.unreachableUrl) || /^chrome-error:/i.test(String(frame.url || ""));
        if (frame.unreachableUrl) {
          this.commitUrl(frame.unreachableUrl);
          if (!this.lastError) {
            this.lastError = `${this.name} could not load this page`;
            this.emit("did-fail-load", {}, -2, this.lastError, frame.unreachableUrl, true);
          }
        } else {
          this.commitUrl(`${frame.url || ""}${frame.urlFragment || ""}`);
        }
        break;
      }
      case "Page.navigatedWithinDocument":
        if (!main) break;
        if (params.url) this.url = params.url;
        void this.refreshHistory().then(() => this.refreshState());
        this.emit("did-navigate-in-page", {}, this.url, true);
        break;
      // Chrome sends this for the tab's main frame only.
      case "Page.domContentEventFired":
        this.documentLoading = false;
        void this.refreshState();
        break;
      case "Page.loadEventFired":
        this.finishLoading();
        break;
      case "Page.frameStoppedLoading":
        if (main && this.loading) this.finishLoading();
        break;
      case "Page.javascriptDialogOpening":
        this.dialog = { type: String(params.type || "alert"), message: String(params.message || "").slice(0, 2000), defaultValue: String(params.defaultPrompt || "").slice(0, 2000) };
        this.emit("stream-dialog-opened");
        if (!this.debugger.isAttached()) this.emit("stream-dialog", { ...this.dialog });
        break;
      case "Page.javascriptDialogClosed":
        this.dialog = null;
        break;
      case "Page.fileChooserOpened":
        if (Number.isInteger(params.backendNodeId)) this.emit("stream-file-dialog", { element: params.backendNodeId, multiple: params.mode === "selectMultiple" });
        break;
      case "Page.screencastFrame":
        this.receiveFrame(params);
        break;
      case "Fetch.requestPaused":
        void this.checkRequest(params);
        break;
      case "Inspector.targetCrashed":
      case "Target.targetCrashed":
        this.markDead(`The ${this.name} tab crashed`);
        break;
      case "Target.targetDestroyed":
        this.markDead(`The ${this.name} tab closed`);
        break;
      case "Target.targetInfoChanged":
        this.scheduleStateRefresh();
        break;
      case "Browser.downloadWillBegin":
        // A navigation that became a download leaves the current page in place.
        this.documentLoading = false;
        this.emit("stream-download-begin", { url: params.url, filename: params.suggestedFilename || "", navigation: params.guid || "" });
        break;
      case "Browser.downloadProgress":
        this.emit("stream-download-end", { status: params.state === "completed" ? "complete" : "canceled", filepath: params.filepath || "", navigation: params.guid || "" });
        break;
      case "popup":
        this.handlePopup(params);
        break;
      default:
        break;
    }
  }

  finishLoading() {
    this.loading = false;
    this.documentLoading = false;
    void this.evaluateIn(STATE_WORLD, pageMenu.WATCH).catch(() => {});
    void this.refreshHistory().then(() => this.refreshState()).finally(() => {
      this.emit("did-stop-loading");
      this.settleWaiters();
    });
    // Only the tab's own mute is applied to a new page; a site's muted
    // autoplaying video stays muted.
    if (this.muted) void this.applyMute(); else void this.sound.check();
  }

  commitUrl(url) {
    if (!url) return;
    this.url = url;
    this.emit("did-navigate", {}, url);
    void this.refreshHistory().then(() => this.refreshState());
  }

  // Title and favicon, read in a world the page cannot see.
  async refreshState() {
    if (!this.sessionId || this.dead || this.destroyed) return;
    let state;
    try {
      state = await this.evaluateIn(STATE_WORLD, `[document.title, document.querySelector('link[rel~="icon"]')?.href || ""]`);
    } catch {
      return;
    }
    if (!Array.isArray(state) || this.destroyed) return;
    this.showPageState(state[0], state[1]);
  }

  titleChanged(title) {
    const entry = this.history.entries[this.history.index];
    if (entry) entry.title = title;
  }

  // Pages open pop-ups as Chrome windows, which Raticode closes.
  handlePopup(info) {
    const id = info.targetId;
    if (!id || this.popups.has(id)) return;
    const url = info.url && info.url !== "about:blank" ? info.url : "";
    if (!url) {
      // The address arrives in a later update; give up after a while.
      if (!this.popupTimers) this.popupTimers = new Map();
      if (!this.popupTimers.has(id)) {
        const timer = setTimeout(() => {
          this.popupTimers.delete(id);
          if (this.popups.has(id)) return;
          this.popups.add(id);
          void this.chromium.send("Target.closeTarget", { targetId: id }).catch(() => {});
        }, POPUP_URL_TIMEOUT_MS);
        timer.unref?.();
        this.popupTimers.set(id, timer);
      }
      return;
    }
    this.popups.add(id);
    if (this.popupTimers?.has(id)) { clearTimeout(this.popupTimers.get(id)); this.popupTimers.delete(id); }
    if (this.popups.size > 200) this.popups = new Set([...this.popups].slice(-100));
    void this.chromium.send("Target.closeTarget", { targetId: id }).catch(() => {});
    this.offerPopup(url);
  }

  // Rem's own tabs check every page navigation against its site rules before
  // the request leaves Chrome, as will-navigate does for Raticode tabs.
  async installNavigationGuard() {
    this.guardNavigation = true;
    if (!this.sessionId || this.guardInstalled) return;
    this.guardInstalled = true;
    await this.command("Fetch.enable", { patterns: [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }] });
  }

  async checkRequest(params) {
    const url = params.request?.url || "";
    let allowed = true;
    if (params.frameId === this.mainFrameId) {
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, url };
      this.emit(params.redirectedRequestId ? "will-redirect" : "will-navigate", event, url);
      allowed = !event.defaultPrevented;
    }
    try {
      if (allowed) await this.command("Fetch.continueRequest", { requestId: params.requestId });
      else {
        // An aborted navigation leaves the current page in place, where a
        // blocked one would replace it with Chrome's error page.
        await this.command("Fetch.failRequest", { requestId: params.requestId, errorReason: "Aborted" });
        this.loading = false;
        this.lastError = "Rem's site rules in Settings > Browser blocked this page";
        this.emit("did-fail-load", {}, -20, this.lastError, url, true);
        this.emit("did-stop-loading");
        this.settleWaiters(new Error(`${this.lastError}: ${url}`));
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
      void this.applyViewport().then(() => this.startCast());
    } else {
      this.stopCast();
    }
  }

  zoomChanged() {
    void this.applyViewport().then(() => { if (this.visible) return this.restartCast(); return undefined; });
  }

  async applyViewport() {
    if (!this.sessionId || this.dead) return;
    const { width, height, deviceScaleFactor } = this.viewport();
    const key = `${width}x${height}@${deviceScaleFactor}`;
    if (key === this.viewportKey) return;
    const changed = Boolean(this.viewportKey);
    this.viewportKey = key;
    await this.command("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor, mobile: false, screenWidth: width, screenHeight: height }).catch((error) => {
      this.viewportKey = "";
      this.log("warn", `${this.name} viewport change failed: ${error.message}`);
    });
    if (changed && this.frame.casting) await this.restartCast();
  }

  async startCast() {
    if (!this.sessionId || this.dead || this.destroyed || !this.visible || this.frame.casting) return;
    this.frame.casting = true;
    const { width, height, deviceScaleFactor } = this.viewport();
    await this.command("Page.startScreencast", {
      format: "jpeg",
      quality: FRAME_QUALITY,
      maxWidth: Math.round(width * deviceScaleFactor),
      maxHeight: Math.round(height * deviceScaleFactor),
      everyNthFrame: 1,
    }).catch((error) => {
      this.frame.casting = false;
      this.log("warn", `${this.name} frame stream stopped: ${error.message}`);
    });
  }

  stopCast() {
    this.ackChrome();
    if (!this.frame.casting) return;
    this.frame.casting = false;
    if (this.sessionId) void this.command("Page.stopScreencast").catch(() => {});
  }

  async restartCast() {
    this.stopCast();
    await this.startCast();
  }

  // Chrome sends the next frame only after the last one is acknowledged, and
  // Raticode acknowledges once the studio has drawn it (or after a moment).
  receiveFrame(params) {
    if (!this.visible || !this.frame.casting) {
      void this.command("Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
      return;
    }
    this.ackChrome();
    const buffer = Buffer.from(String(params.data || ""), "base64");
    if (!buffer.length) return;
    this.frame.seq += 1;
    this.frame.ack = params.sessionId;
    this.frame.ackTimer = setTimeout(() => this.ackChrome(), ACK_TIMEOUT_MS);
    this.frame.ackTimer.unref?.();
    this.stats.frames += 1;
    this.stats.bytes += buffer.length;
    // Pages change their title from script; follow while the page changes.
    this.scheduleStateRefresh();
    const view = this.view || FALLBACK_VIEW;
    this.emit("stream-frame", { seq: this.frame.seq, data: buffer, width: view.width, height: view.height, captureMs: 0 });
  }

  ackChrome() {
    clearTimeout(this.frame.ackTimer);
    this.frame.ackTimer = null;
    const ack = this.frame.ack;
    this.frame.ack = "";
    if (ack !== "" && ack !== undefined && this.sessionId) void this.command("Page.screencastFrameAck", { sessionId: ack }).catch(() => {});
  }

  ackFrame(seq) {
    if (Number(seq) === this.frame.seq) this.ackChrome();
  }

  // -------------------------------------------------------------------------
  // The user's input from the studio

  modifiers() {
    return modifierBits(this.pressedKeys);
  }

  mouse(type, at, extra = {}) {
    return this.input("Input.dispatchMouseEvent", { type, x: at.x, y: at.y, modifiers: this.modifiers(), buttons: this.buttons, button: "none", ...extra });
  }

  clickCount(at, button) {
    const last = this.lastPress;
    const now = Date.now();
    const count = last.button === button && now - last.at < DOUBLE_CLICK_MS && Math.abs(last.x - at.x) < 5 && Math.abs(last.y - at.y) < 5 ? Math.min(last.count + 1, 3) : 1;
    this.lastPress = { at: now, x: at.x, y: at.y, button, count };
    return count;
  }

  moveUserPointer(at) {
    return this.mouse("mouseMoved", at);
  }

  async userButton(down, index, at) {
    const button = ["left", "middle", "right"][index];
    const bit = [1, 4, 2][index];
    if (down) {
      const clickCount = this.clickCount(at, button);
      this.buttons |= bit;
      await this.mouse("mousePressed", at, { button, clickCount });
      return;
    }
    this.buttons &= ~bit;
    await this.mouse("mouseReleased", at, { button, clickCount: this.lastPress.count || 1 });
    if (index === 2) void this.reportMenu(at);
  }

  userWheel(at, deltaX, deltaY) {
    return this.command("Input.dispatchMouseEvent", { type: "mouseWheel", x: at.x, y: at.y, deltaX, deltaY, modifiers: this.modifiers() });
  }

  userKey(key) {
    return keyDefinition(key)?.key || "";
  }

  sendUserKey(key, down) {
    return this.pressKey(keyDefinition(key), down ? "down" : "up");
  }

  async releaseUserInput() {
    for (const key of [...this.pressedKeys]) {
      const definition = keyDefinition(key);
      this.pressedKeys.delete(key);
      if (definition) await this.pressKey(definition, "up").catch(() => {});
    }
  }

  // A printable key types its character unless Control, Alt or Meta is held.
  pressKey(definition, direction, modifiers = this.modifiers()) {
    const base = {
      key: definition.key,
      code: definition.code,
      windowsVirtualKeyCode: definition.keyCode,
      nativeVirtualKeyCode: definition.keyCode,
      modifiers,
    };
    if (direction === "up") return this.input("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    const types = definition.text && !(modifiers & 7);
    const commands = this.platform === "darwin" ? macCommands(definition.key, modifiers) : [];
    return this.input("Input.dispatchKeyEvent", {
      type: types ? "keyDown" : "rawKeyDown",
      ...base,
      ...(types ? { text: definition.text, unmodifiedText: definition.text } : {}),
      ...(commands.length ? { commands } : {}),
    });
  }

  // Pasted or composed text goes in as typed text, as an input method would.
  async typeText(text) {
    const value = [...text.replace(/\r\n?/g, "\n")].slice(0, MAX_TEXT_INPUT).join("");
    if (value) await this.input("Input.insertText", { text: value });
  }

  // The user's right-click: Raticode's menu, unless the page showed its own.
  async reportMenu(at) {
    const seen = await this.evaluateIn(STATE_WORLD, pageMenu.read(at.x, at.y)).catch(() => null);
    const params = pageMenu.menuParams(seen, this.pageZoom);
    if (params && !this.destroyed) this.emit("context-menu", {}, params);
  }

  // Menu commands for the field or selection the user right-clicked.
  // The rest are the browser's own editing commands, sent with a key event
  // as the browser's menu would, so they act where the user's cursor is.
  async editCommand(command, text = "") {
    if (!Object.hasOwn(pageMenu.EDIT_COMMANDS, command) && !["copy", "paste"].includes(command)) throw new Error(`Unknown edit command: ${command}`);
    await this.alive();
    if (command === "copy") return this.copySelection();
    if (command === "paste") return this.typeText(String(text || ""));
    if (command === "cut") await this.copySelection();
    const key = { key: "Unidentified", code: "", windowsVirtualKeyCode: 0, nativeVirtualKeyCode: 0, modifiers: 0 };
    await this.input("Input.dispatchKeyEvent", { type: "rawKeyDown", ...key, commands: [pageMenu.EDIT_COMMANDS[command]] });
    await this.input("Input.dispatchKeyEvent", { type: "keyUp", ...key });
    return undefined;
  }

  async closeNativeSelect() {
    await this.pressKey(keyDefinition("Escape"), "down", 0).catch(() => {});
    await this.pressKey(keyDefinition("Escape"), "up", 0).catch(() => {});
  }

  async setFiles(backendNodeId, files) {
    await this.alive();
    await this.command("DOM.setFileInputFiles", { backendNodeId: Number(backendNodeId), files });
  }

  async answerDialog(accept, text) {
    await this.alive();
    await this.command("Page.handleJavaScriptDialog", {
      accept: accept === true,
      ...(typeof text === "string" ? { promptText: text.slice(0, 10000) } : {}),
    });
  }

  // -------------------------------------------------------------------------
  // Rem's input, at the centre of the element the page agent located

  async nativeClick({ x, y, button = "left", clicks = 1 }) {
    await this.alive();
    this.refuseRemOnErrorPage();
    const name = ["left", "middle", "right"].includes(button) ? button : "left";
    const bit = { left: 1, middle: 4, right: 2 }[name];
    const at = { x: Math.round(x), y: Math.round(y) };
    await this.input("Input.dispatchMouseEvent", { type: "mouseMoved", x: at.x, y: at.y, button: "none", buttons: 0 });
    for (let count = 1; count <= (clicks === 2 ? 2 : 1); count += 1) {
      await this.input("Input.dispatchMouseEvent", { type: "mousePressed", x: at.x, y: at.y, button: name, buttons: bit, clickCount: count });
      if (this.dialog) return;
      await this.input("Input.dispatchMouseEvent", { type: "mouseReleased", x: at.x, y: at.y, button: name, buttons: 0, clickCount: count });
      if (this.dialog) return;
    }
  }

  async nativeHover({ x, y }) {
    await this.alive();
    this.refuseRemOnErrorPage();
    await this.input("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.round(x), y: Math.round(y), button: "none", buttons: 0 });
  }

  async nativePress(key) {
    await this.alive();
    this.refuseRemOnErrorPage();
    const definition = keyDefinition(key.key === "Space" ? " " : key.key);
    if (!definition) throw new Error(`${this.name} cannot press ${key.key}.`);
    const modifiers = (key.altKey ? 1 : 0) | (key.ctrlKey ? 2 : 0) | (key.metaKey ? 4 : 0) | (key.shiftKey ? 8 : 0);
    const held = [["altKey", "Alt"], ["ctrlKey", "Control"], ["metaKey", "Meta"], ["shiftKey", "Shift"]].filter(([flag]) => key[flag]).map(([, name]) => keyDefinition(name));
    let bits = 0;
    for (const modifier of held) {
      bits |= MODIFIER_BITS[modifier.key];
      await this.pressKey(modifier, "down", bits);
    }
    await this.pressKey(definition, "down", modifiers);
    if (!this.dialog) await this.pressKey(definition, "up", modifiers);
    for (const modifier of held.reverse()) {
      bits &= ~MODIFIER_BITS[modifier.key];
      await this.pressKey(modifier, "up", bits);
    }
  }
}

// agent-browser.cjs drives tabs with a handful of DevTools commands. Chrome
// speaks DevTools itself, so those go straight to the tab's session; only the
// commands Rem's tools use are let through. The viewport stays Raticode's:
// Chrome tabs keep their on-screen size while hidden.
const PASSTHROUGH = new Set([
  "Emulation.setFocusEmulationEnabled",
  "Page.enable",
  "DOM.enable",
  "Overlay.enable",
  "Overlay.highlightRect",
  "Overlay.hideHighlight",
  "DOM.getDocument",
  "DOM.performSearch",
  "DOM.getSearchResults",
  "DOM.discardSearchResults",
  "DOM.setFileInputFiles",
  "Runtime.evaluate",
  "Page.handleJavaScriptDialog",
]);
// Commands that read or change the page; refused on Chrome's own error pages.
const PAGE_COMMANDS = new Set(["DOM.getDocument", "DOM.performSearch", "DOM.getSearchResults", "DOM.setFileInputFiles", "Runtime.evaluate"]);

function createDebuggerPassthrough(contents) {
  const shim = new EventEmitter();
  let attached = false;
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
    if (method === "Emulation.setDeviceMetricsOverride" || method === "Emulation.clearDeviceMetricsOverride") return {};
    if (!PASSTHROUGH.has(method)) throw new Error(`${contents.name} tabs do not support ${method}.`);
    await contents.alive();
    if (PAGE_COMMANDS.has(method)) contents.refuseRemOnErrorPage?.();
    return contents.command(method, params);
  };
  return shim;
}

module.exports = {
  ChromiumContents,
  NET_ERRORS,
  isInternalPage,
  PASSTHROUGH,
  createDebuggerPassthrough,
  keyDefinition,
  macCommands,
  modifierBits,
  netErrorCode,
  visibleHistory,
};
