// What a streamed tab does the same way in Chrome, Edge and Firefox: the
// webContents methods that need no browser protocol, the scripts it runs in
// the page's private world (find, copy, cursor, <select> drop-downs), the
// queue of the user's input from the studio and the waits for a page to load.
// ChromiumContents (DevTools) and FirefoxContents (WebDriver BiDi) extend
// StreamTab and supply the protocol through hooks: start, isRunning,
// evaluateIn(world, expression, { userGesture }), dispatchInput (which input
// wraps), screenshot, reloadDocument, stopFrames, releasePage(closing),
// zoomChanged, titleChanged and, for the user's input, moveUserPointer,
// userButton, userWheel, userKey, sendUserKey, releaseUserInput and typeText.
// Events for the studio are named `${eventPrefix}-…`: stream-* for Chrome and
// Edge, firefox-* for Firefox.
const { EventEmitter } = require("node:events");
const { watchSound } = require("./stream-sound.cjs");

const FALLBACK_VIEW = { width: 1280, height: 800, scale: 1 };
const MAX_VIEW = 4096;
const CURSOR_INTERVAL_MS = 120;
const STATE_WORLD = "raticode-state";
const STATE_REFRESH_MS = 1000;

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

class StreamTab extends EventEmitter {
  constructor({ kind = "user", nativeImage = null, clipboard = null, log = () => {}, eventPrefix }) {
    super();
    this.eventPrefix = eventPrefix;
    this.kind = kind;
    this.nativeImage = nativeImage;
    this.clipboard = clipboard;
    this.log = log;
    this.destroyed = false;
    this.dead = false;
    this.url = "about:blank";
    this.title = "";
    this.loading = true;
    // True from the start of a main-frame navigation until the new document
    // fires DOMContentLoaded. See isLoadingMainFrame().
    this.documentLoading = true;
    this.focused = false;
    this.muted = false;
    this.pageZoom = 1;
    this.view = null;
    this.visible = false;
    this.windowOpenHandler = () => ({ action: "deny" });
    this.waiters = new Set();
    this.inputQueue = Promise.resolve();
    this.pendingMove = null;
    this.pressedKeys = new Set();
    this.dialog = null;
    this.cursorAt = 0;
    this.guardNavigation = false;
    this.mainFrame = {};
    this.nativeInput = {
      click: (options) => this.nativeClick(options),
      hover: (options) => this.nativeHover(options),
      press: (key) => this.nativePress(key),
    };
    this.audible = false;
    this.sound = watchSound(this, { ready: () => this.isRunning(), evaluate: (expression) => this.evaluateIn(STATE_WORLD, expression) });
  }

  // Called by each subclass once its own fields are set.
  boot() {
    this.ready = this.start();
    this.ready.catch((error) => this.fail(error));
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
  // As Electron's method of the same name, but true only until the main
  // document's DOM is ready, so a frame or image that never finishes doesn't
  // keep it true.
  isLoadingMainFrame() { return this.loading && this.documentLoading; }
  isFocused() { return this.focused; }
  getType() { return this.engine; }
  // The studio focuses its own view of the tab.
  focus() { this.emit(`${this.eventPrefix}-focus`); }
  setBackgroundThrottling() {}
  setBackgroundColor() {}
  setZoomFactor() {}
  setWindowOpenHandler(handler) { this.windowOpenHandler = typeof handler === "function" ? handler : this.windowOpenHandler; }
  isCurrentlyAudible() { return this.audible === true; }
  isDevToolsOpened() { return false; }
  closeDevTools() {}
  openDevTools() { throw new Error(`Developer tools are not available in ${this.name} tabs.`); }
  print() { throw new Error(`Printing is not available in ${this.name} tabs. Save as PDF instead.`); }
  async getPrintersAsync() { return []; }
  // The start of loadURL. Loading is reported at once, as Electron does, so
  // callers that wait for the page to settle don't return before the browser
  // announces the navigation.
  async startNavigation() {
    await this.alive();
    this.lastError = "";
    const waiter = this.waitForNavigation();
    this.loading = true;
    this.documentLoading = true;
    return waiter;
  }

  // The browser refused to start the navigation; returns the error to throw.
  navigationRefused(waiter, target, error) {
    waiter.cancel();
    this.loading = false;
    this.emit("did-fail-load", {}, -2, error.message, target, true);
    this.emit("did-stop-loading");
    return new Error(`${this.name} could not open ${target}: ${error.message}`, { cause: error });
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
    this.documentLoading = true;
    await this.reloadDocument(ignoreCache);
  }

  // Each world shares the page's DOM but none of its scripts, as Raticode's
  // own tabs' isolated worlds do.
  async executeJavaScriptInIsolatedWorld(worldId, sources, userGesture = false) {
    this.refuseRemOnErrorPage();
    const code = (sources || []).map((item) => item.code).join(";\n");
    return this.evaluateIn(`raticode-world-${Number(worldId) || 0}`, code, { userGesture: userGesture === true });
  }

  async capturePage() {
    await this.alive();
    const buffer = await this.screenshot();
    if (!this.nativeImage) return buffer;
    return this.nativeImage.createFromBuffer(buffer);
  }

  setAudioMuted(muted) {
    this.muted = muted === true;
    void this.applyMute();
  }

  async applyMute() {
    if (!this.isRunning() || this.dead) return;
    await this.evaluateIn(STATE_WORLD, `for (const media of document.querySelectorAll("audio,video")) media.muted = ${this.muted}; true`).catch(() => {});
    await this.sound.check();
  }

  // window.find selects each match, so the highlight appears in the stream.
  findInPage(text, { forward = true, findNext = true } = {}) {
    const requestId = (this.findRequestId = (this.findRequestId || 0) + 1);
    const query = String(text);
    // A new search starts from the top of the page, not the previous match.
    const fresh = findNext !== false && this.findQuery !== query;
    void this.alive().then(async () => {
      const result = await this.evaluateIn(STATE_WORLD, `(() => {
        const query = ${JSON.stringify(query)};
        if (${fresh}) {
          // A focused control keeps the browser's selection where it is, so
          // no match gets selected. The user is typing in Raticode's find bar.
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
    void this.evaluateIn(STATE_WORLD, "getSelection()?.removeAllRanges(); true").catch(() => {});
  }

  // -------------------------------------------------------------------------
  // Lifecycle

  async alive() {
    await this.ready;
    if (this.destroyed) throw new Error(`The ${this.name} tab was closed.`);
    if (this.dead) throw new Error(this.lastError || `${this.name} stopped. Reload the tab to start it again.`);
  }

  async revive() {
    if (this.destroyed) return;
    this.dead = false;
    this.lastError = "";
    this.loading = true;
    this.documentLoading = true;
    // The new page needs Rem's site rules installed again.
    this.guardInstalled = false;
    this.emit("did-start-loading");
    this.ready = this.start();
    await this.ready;
  }

  // The browser stopped or crashed. The tab stays open and restarts on reload.
  markDead(reason = `${this.name} stopped`) {
    // The first reason given stays: Firefox's "signing in in a window" beats
    // the plain "Firefox stopped" that follows when the process exits.
    if (this.destroyed || this.dead) return;
    this.dead = true;
    this.loading = false;
    this.lastError = `${reason}. Reload the tab to start ${this.name} again.`;
    this.stopFrames();
    this.releasePage(false);
    this.settleWaiters(new Error(this.lastError));
    this.emit("render-process-gone", {}, { reason });
    this.emit("did-stop-loading");
  }

  close() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.visible = false;
    clearTimeout(this.stateTimer);
    this.stopFrames();
    this.settleWaiters(new Error(`The ${this.name} tab was closed.`));
    this.releasePage(true);
    this.emit("destroyed");
    this.removeAllListeners();
  }

  // Input that opens alert() or confirm() keeps the browser's command waiting
  // until the dialog is answered. Return as soon as the dialog shows.
  input(...args) {
    const sent = this.dispatchInput(...args);
    sent.catch(() => {});
    if (this.dialog) return sent;
    const event = `${this.eventPrefix}-dialog-opened`;
    let listener;
    const opened = new Promise((resolve) => { listener = resolve; this.once(event, listener); });
    return Promise.race([sent, opened]).finally(() => this.off(event, listener));
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

  // Title and favicon, read in a world the page cannot see.
  showPageState(title, favicon) {
    if (typeof title === "string" && title !== this.title) {
      this.title = title;
      this.titleChanged(title);
      this.emit("page-title-updated", {}, title);
    }
    let icon = typeof favicon === "string" && /^https?:/i.test(favicon) ? favicon : "";
    if (!icon && /^https?:/i.test(this.url)) {
      try { icon = `${new URL(this.url).origin}/favicon.ico`; } catch { icon = ""; }
    }
    if (icon !== this.favicon) {
      this.favicon = icon;
      this.emit("page-favicon-updated", {}, icon ? [icon] : []);
    }
  }

  // Pages open pop-ups as windows of their own; the subclass closes them and
  // Raticode asks the same window-open handler as for its own tabs.
  offerPopup(url) {
    const decision = this.windowOpenHandler({ url, disposition: "foreground-tab", features: "", postBody: null }) || {};
    if (decision.action === "allow") this.emit(`${this.eventPrefix}-open-tab`, { url });
  }

  // -------------------------------------------------------------------------
  // Viewport

  setPageZoom(factor) {
    const zoom = Number(factor);
    this.pageZoom = Number.isFinite(zoom) && zoom > 0.2 && zoom < 6 ? zoom : 1;
    this.zoomChanged();
  }

  // Layout happens at the view's CSS size divided by the page zoom; the
  // device pixel ratio keeps the image sharp on the studio's display.
  viewport() {
    const view = this.view || FALLBACK_VIEW;
    return {
      width: Math.max(100, Math.round(view.width / this.pageZoom)),
      height: Math.max(100, Math.round(view.height / this.pageZoom)),
      deviceScaleFactor: Math.min(Math.max(view.scale * this.pageZoom, 0.5), 6),
    };
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

  // Something on the page or from the user changed. Firefox streams faster for a while.
  markActive() {}

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
      if (!this.destroyed && !this.dead) this.log("debug", `${this.name} input failed: ${error.message}`);
    });
  }

  point(item) {
    const x = Math.round(Number(item.x) / this.pageZoom);
    const y = Math.round(Number(item.y) / this.pageZoom);
    const { width, height } = this.viewport();
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x: Math.min(Math.max(x, 0), width - 1), y: Math.min(Math.max(y, 0), height - 1) };
  }

  async userInput(item) {
    await this.alive();
    switch (item.t) {
      case "move": {
        const at = this.point(item);
        if (!at) return;
        await this.moveUserPointer(at);
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
        const index = [0, 1, 2].includes(item.button) ? item.button : 0;
        this.emit("input-event", {}, { type: item.t === "down" ? "mouseDown" : "mouseUp" });
        await this.userButton(item.t === "down", index, at);
        if (item.t === "up" && index === 0) void this.reportSelect(at);
        return;
      }
      case "wheel": {
        const at = this.point(item);
        if (!at) return;
        const deltaX = Math.round(Math.max(-5000, Math.min(5000, Number(item.dx) || 0)) / this.pageZoom);
        const deltaY = Math.round(Math.max(-5000, Math.min(5000, Number(item.dy) || 0)) / this.pageZoom);
        if (!deltaX && !deltaY) return;
        await this.userWheel(at, deltaX, deltaY);
        return;
      }
      case "keydown":
      case "keyup": {
        const key = this.userKey(item.key);
        if (!key) return;
        if (item.t === "keydown") {
          this.pressedKeys.add(key);
          this.emit("input-event", {}, { type: "keyDown" });
        } else {
          if (!this.pressedKeys.has(key)) return;
          this.pressedKeys.delete(key);
        }
        await this.sendUserKey(key, item.t === "keydown");
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
        await this.releaseUserInput();
        return;
      case "select-option":
        await this.chooseOption(Number(item.index));
        return;
      default:
        return;
    }
  }

  // A browser without a window has its own clipboard or none, so copy and cut
  // put the page's selection on the system clipboard here.
  async copySelection() {
    const text = await this.evaluateIn(STATE_WORLD, `(() => {
      const active = document.activeElement;
      if (active && typeof active.selectionStart === "number" && /^(?:input|textarea)$/i.test(active.localName) && active.type !== "password") {
        return active.value.slice(active.selectionStart, active.selectionEnd);
      }
      return String(getSelection() || "");
    })()`).catch(() => "");
    if (text && this.clipboard) await Promise.resolve(this.clipboard.writeText(String(text))).catch(() => {});
  }

  async reportCursor(at) {
    const cursor = await this.evaluateIn(STATE_WORLD, `(() => {
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
      this.emit(`${this.eventPrefix}-cursor`, { cursor: cursor.slice(0, 40) });
    }
  }

  // A browser without a window draws no drop-down for <select> where the
  // stream can see it, so the studio shows the options itself after a click
  // opens one.
  async reportSelect(at) {
    const choice = await this.evaluateIn(STATE_WORLD, `(() => {
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
    await this.closeNativeSelect();
    const zoom = this.pageZoom;
    const rect = choice.rect || {};
    this.emit(`${this.eventPrefix}-select`, {
      rect: { x: rect.x * zoom, y: rect.y * zoom, width: rect.width * zoom, height: rect.height * zoom },
      options: choice.options,
    });
  }

  // Chrome's own (invisible) drop-down keeps the keyboard until it closes.
  async closeNativeSelect() {}

  async chooseOption(index) {
    if (!Number.isInteger(index) || index < 0) return;
    await this.evaluateIn(STATE_WORLD, `(() => {
      const element = document.activeElement;
      if (!element || element.localName !== "select") return false;
      const option = element.options[${index}];
      if (!option || option.disabled) return false;
      element.selectedIndex = ${index};
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`, { userGesture: true });
    this.markActive();
  }
}

module.exports = { FALLBACK_VIEW, STATE_WORLD, StreamTab, clampView };
