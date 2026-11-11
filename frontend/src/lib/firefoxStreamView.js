// The view of a Firefox tab. Firefox runs without a window in the main
// process's care; this draws the frames it streams on a canvas and sends the
// user's mouse, wheel and keyboard back. A hidden text field takes keyboard
// focus so typing, pasting and input-method composition work as on a page.

const SAFE_CURSORS = new Set([
  "default", "pointer", "text", "vertical-text", "crosshair", "move", "grab", "grabbing", "not-allowed",
  "no-drop", "wait", "progress", "help", "cell", "context-menu", "alias", "copy", "all-scroll", "zoom-in",
  "zoom-out", "col-resize", "row-resize", "n-resize", "e-resize", "s-resize", "w-resize", "ne-resize",
  "nw-resize", "se-resize", "sw-resize", "ew-resize", "ns-resize", "nesw-resize", "nwse-resize", "none",
]);
const LINE_PIXELS = 16;
const HIGHLIGHT_MS = 900;

// What a key press in the view should do: forward it to Firefox, let the
// browser produce a paste event, or copy/cut the page's selection first.
export function firefoxKeyAction(event, platform = "") {
  if (!event || event.isComposing || event.key === "Process" || event.keyCode === 229) return "ignore";
  const mod = platform === "darwin" ? event.metaKey : event.ctrlKey;
  const key = String(event.key || "").toLowerCase();
  if (mod && !event.altKey && key === "v") return "paste";
  if (mod && !event.altKey && key === "c") return "copy";
  if (mod && !event.altKey && key === "x") return "cut";
  if (!event.key || event.key === "Unidentified" || event.key === "Dead") return "ignore";
  return "forward";
}

// Wheel deltas in CSS pixels, whatever unit the device reported.
export function wheelPixels(event, pageHeight = 800) {
  const unit = event.deltaMode === 1 ? LINE_PIXELS : event.deltaMode === 2 ? pageHeight : 1;
  return { dx: (Number(event.deltaX) || 0) * unit, dy: (Number(event.deltaY) || 0) * unit };
}

export function safeCursor(value) {
  const cursor = String(value || "").trim().toLowerCase();
  return SAFE_CURSORS.has(cursor) ? cursor : "default";
}

// The same view draws Chrome tabs, which stream the same way; `engine` and
// `label` name the browser.
export function attachFirefoxView(bridge, sessionId, container, viewRef, { clientId = "", onUpdate = () => {}, engine = "firefox", label = "Firefox" } = {}) {
  if (!container || typeof document?.createElement !== "function") return;
  const doc = container.ownerDocument || document;
  const win = doc.defaultView || window;
  const wrapper = doc.createElement("div");
  wrapper.dataset.engine = engine;
  wrapper.dataset.testid = "firefox-view";
  Object.assign(wrapper.style, { position: "absolute", inset: "0", overflow: "hidden", background: "#ffffff" });
  const canvas = doc.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  Object.assign(canvas.style, { position: "absolute", left: "0", top: "0", display: "block", touchAction: "none" });
  const keyboard = doc.createElement("textarea");
  keyboard.setAttribute("aria-label", `${label} page`);
  keyboard.setAttribute("autocomplete", "off");
  keyboard.setAttribute("autocapitalize", "off");
  keyboard.spellcheck = false;
  Object.assign(keyboard.style, {
    position: "absolute", left: "0", top: "0", width: "1px", height: "1px", opacity: "0",
    border: "0", padding: "0", resize: "none", pointerEvents: "none", caretColor: "transparent",
  });
  const status = doc.createElement("p");
  status.setAttribute("role", "status");
  status.textContent = `Starting ${label}…`;
  Object.assign(status.style, {
    position: "absolute", inset: "0", display: "grid", placeItems: "center", margin: "0",
    font: "12px system-ui, sans-serif", color: "#64748b", pointerEvents: "none",
  });
  wrapper.append(canvas, keyboard, status);

  const context = canvas.getContext("2d");
  let disposed = false;
  let frameSize = { width: 0, height: 0 };
  let pendingMove = null;
  let pendingWheel = null;
  let flushFrame = 0;
  let viewFrame = 0;
  let menu = null;
  // Held keys by physical key: releasing Shift first changes a letter's key value.
  const forwardedKeys = new Map();

  const send = (events) => {
    if (disposed || !events.length) return;
    void Promise.resolve(bridge.firefoxInput?.(sessionId, events)).catch(() => {});
  };

  function reportView() {
    win.cancelAnimationFrame(viewFrame);
    viewFrame = win.requestAnimationFrame(() => {
      if (disposed) return;
      const width = wrapper.clientWidth;
      const height = wrapper.clientHeight;
      const visible = width > 0 && height > 0 && doc.visibilityState !== "hidden";
      void Promise.resolve(bridge.firefoxView?.(sessionId, { width, height, scale: win.devicePixelRatio || 1, visible })).catch(() => {});
    });
  }

  // Pointer moves and wheel turns are merged to one message per frame.
  function scheduleFlush() {
    if (flushFrame) return;
    flushFrame = win.requestAnimationFrame(() => {
      flushFrame = 0;
      const events = [];
      if (pendingMove) events.push(pendingMove);
      if (pendingWheel) events.push(pendingWheel);
      pendingMove = null;
      pendingWheel = null;
      send(events);
    });
  }

  function position(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: Math.round(event.clientX - rect.left), y: Math.round(event.clientY - rect.top) };
  }

  function flushNow(extra) {
    win.cancelAnimationFrame(flushFrame);
    flushFrame = 0;
    const events = [];
    if (pendingMove) events.push(pendingMove);
    if (pendingWheel) events.push(pendingWheel);
    pendingMove = null;
    pendingWheel = null;
    send([...events, ...extra]);
  }

  canvas.addEventListener("pointermove", (event) => {
    pendingMove = { t: "move", ...position(event) };
    scheduleFlush();
  });
  canvas.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    closeMenu();
    keyboard.focus({ preventScroll: true });
    try { canvas.setPointerCapture(event.pointerId); } catch { /* Capture is optional. */ }
    flushNow([{ t: "down", ...position(event), button: event.button }]);
  });
  canvas.addEventListener("pointerup", (event) => {
    try { canvas.releasePointerCapture(event.pointerId); } catch { /* Capture is optional. */ }
    flushNow([{ t: "up", ...position(event), button: event.button }]);
  });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const { dx, dy } = wheelPixels(event, wrapper.clientHeight);
    const at = position(event);
    pendingWheel = pendingWheel
      ? { ...pendingWheel, ...at, dx: pendingWheel.dx + dx, dy: pendingWheel.dy + dy }
      : { t: "wheel", ...at, dx, dy };
    scheduleFlush();
  }, { passive: false });

  keyboard.addEventListener("keydown", (event) => {
    const action = firefoxKeyAction(event, bridge.platform);
    if (action === "ignore" || action === "paste") return;
    event.preventDefault();
    // Plain typing belongs to the page. Shortcuts with modifiers still reach
    // the studio's own key bindings.
    if (!event.ctrlKey && !event.metaKey && !event.altKey) event.stopPropagation();
    // Copy and cut put the page's selection on the system clipboard first,
    // then reach the page like any other key.
    const events = action === "copy" || action === "cut" ? [{ t: action }] : [];
    forwardedKeys.set(event.code || event.key, event.key);
    events.push({ t: "keydown", key: event.key });
    if (menu && event.key === "Escape") closeMenu();
    send(events);
  });
  keyboard.addEventListener("keyup", (event) => {
    const code = event.code || event.key;
    if (!forwardedKeys.has(code)) return;
    const key = forwardedKeys.get(code);
    forwardedKeys.delete(code);
    event.preventDefault();
    send([{ t: "keyup", key }]);
  });
  keyboard.addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    if (text) send([{ t: "text", text }]);
  });
  keyboard.addEventListener("compositionend", (event) => {
    if (event.data) send([{ t: "text", text: event.data }]);
    keyboard.value = "";
  });
  keyboard.addEventListener("input", (event) => {
    if (!event.isComposing) keyboard.value = "";
  });
  keyboard.addEventListener("focus", () => send([{ t: "focus" }]));
  keyboard.addEventListener("blur", () => {
    // Keys held when focus leaves are released in Firefox too.
    forwardedKeys.clear();
    send([{ t: "blur" }]);
  });

  async function drawFrame(payload) {
    if (disposed || payload?.id !== sessionId) return;
    try {
      const blob = new Blob([payload.data], { type: "image/jpeg" });
      const bitmap = await win.createImageBitmap(blob);
      if (disposed) { bitmap.close?.(); return; }
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
      }
      if (frameSize.width !== payload.width || frameSize.height !== payload.height) {
        frameSize = { width: payload.width, height: payload.height };
        canvas.style.width = `${payload.width}px`;
        canvas.style.height = `${payload.height}px`;
      }
      context?.drawImage(bitmap, 0, 0);
      bitmap.close?.();
      status.hidden = true;
      wrapper.dataset.frames = String((Number(wrapper.dataset.frames) || 0) + 1);
    } catch {
      // A frame that fails to decode is replaced by the next one.
    } finally {
      void Promise.resolve(bridge.frameAck?.(sessionId, payload.seq)).catch(() => {});
    }
  }

  function closeMenu() {
    menu?.remove();
    menu = null;
  }

  // Firefox draws no <select> drop-down without a window, so the view does.
  function openMenu(details) {
    closeMenu();
    const options = Array.isArray(details.options) ? details.options : [];
    if (!options.length) return;
    const rect = details.rect || {};
    menu = doc.createElement("div");
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", "Choose an option");
    const top = Math.max(0, (Number(rect.y) || 0) + (Number(rect.height) || 0));
    Object.assign(menu.style, {
      position: "absolute", left: `${Math.max(0, Number(rect.x) || 0)}px`, top: `${top}px`,
      minWidth: `${Math.max(80, Number(rect.width) || 0)}px`, maxHeight: `${Math.max(120, wrapper.clientHeight - top - 8)}px`,
      overflowY: "auto", background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: "4px",
      boxShadow: "0 6px 16px rgba(15, 23, 42, 0.18)", font: "13px system-ui, sans-serif", zIndex: "5",
    });
    let group = "";
    for (const option of options) {
      if (option.group && option.group !== group) {
        group = option.group;
        const heading = doc.createElement("div");
        heading.textContent = group;
        Object.assign(heading.style, { padding: "4px 8px", fontWeight: "600", color: "#475569" });
        menu.append(heading);
      }
      const item = doc.createElement("button");
      item.type = "button";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", option.selected ? "true" : "false");
      item.disabled = option.disabled === true;
      item.textContent = option.label || " ";
      Object.assign(item.style, {
        display: "block", width: "100%", textAlign: "left", padding: `4px ${option.group ? 16 : 8}px`, border: "0",
        background: option.selected ? "#e0f2fe" : "transparent", color: option.disabled ? "#94a3b8" : "#0f172a", cursor: "default",
      });
      item.addEventListener("pointerdown", (event) => event.preventDefault());
      item.addEventListener("click", () => {
        closeMenu();
        send([{ t: "select-option", index: option.index }]);
        keyboard.focus({ preventScroll: true });
      });
      menu.append(item);
    }
    wrapper.append(menu);
  }

  function highlight(area) {
    const box = doc.createElement("div");
    Object.assign(box.style, {
      position: "absolute", left: `${Number(area.x) || 0}px`, top: `${Number(area.y) || 0}px`,
      width: `${Number(area.width) || 0}px`, height: `${Number(area.height) || 0}px`,
      background: "rgba(62, 232, 181, 0.18)", outline: "2px solid rgba(3, 92, 103, 0.95)", pointerEvents: "none", zIndex: "4",
    });
    wrapper.append(box);
    win.setTimeout(() => box.remove(), HIGHLIGHT_MS);
  }

  const unsubscribeFrame = bridge.onFrame?.((payload) => void drawFrame(payload));
  const unsubscribeFirefox = bridge.onFirefox?.((payload) => {
    if (disposed || payload?.id !== sessionId) return;
    if (payload.kind === "cursor") canvas.style.cursor = safeCursor(payload.cursor);
    else if (payload.kind === "select") openMenu(payload);
    else if (payload.kind === "highlight") highlight(payload);
    else if (payload.kind === "focus") keyboard.focus({ preventScroll: true });
  });
  const observer = typeof win.ResizeObserver === "function" ? new win.ResizeObserver(reportView) : null;
  observer?.observe(wrapper);
  doc.addEventListener("visibilitychange", reportView);

  const remove = wrapper.remove.bind(wrapper);
  wrapper.focus = () => keyboard.focus({ preventScroll: true });
  wrapper.remove = () => {
    if (disposed) return;
    disposed = true;
    observer?.disconnect();
    doc.removeEventListener("visibilitychange", reportView);
    win.cancelAnimationFrame(flushFrame);
    win.cancelAnimationFrame(viewFrame);
    unsubscribeFrame?.();
    unsubscribeFirefox?.();
    void Promise.resolve(bridge.viewDetached?.(sessionId)).catch(() => {});
    remove();
  };
  viewRef.current = wrapper;
  container.appendChild(wrapper);
  reportView();
  void Promise.resolve(bridge.adopt?.(sessionId, -1, clientId))
    .then((state) => { if (state && viewRef.current === wrapper) onUpdate(state); })
    .catch((error) => {
      if (viewRef.current === wrapper) onUpdate({ error: error instanceof Error ? error.message : `Could not open the ${label} tab.`, loading: false });
    });
}
