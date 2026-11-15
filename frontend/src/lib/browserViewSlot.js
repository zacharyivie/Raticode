import { coverCutouts } from "./geckoView.js";

// A Raticode (chromium) tab's page is a view the main process owns and lays
// over Raticode's window (or the Browser's own window). The studio draws an
// empty slot where the page goes and reports where it is. A view always sits
// above the studio's own drawing, so while a menu, a dialog or a dragged tab is
// over the slot, the view steps aside and the slot shows a picture of the page.
// The page itself keeps running the whole time.

const CHECK_MS = 200;
const OVERLAYS = "[role=menu], [role=dialog], [role=alertdialog], [role=listbox], [role=tooltip]";

// Menus and dialogs that are open and overlap the slot. They may be small
// enough to sit between the points checked for covering.
export function openOverlays(rect, doc, view) {
  if (typeof doc?.querySelectorAll !== "function") return [];
  return [...doc.querySelectorAll(OVERLAYS)].filter((element) => {
    if (element.closest?.("[aria-hidden=true], [inert]") || view?.getComputedStyle?.(element)?.visibility === "hidden") return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.right > rect.left && box.left < rect.right && box.bottom > rect.top && box.top < rect.bottom;
  });
}

// Whether anything Raticode draws is over the slot.
export function slotCovered(slot, rect, doc, view) {
  const cutouts = coverCutouts(slot, rect, {
    elementAt: (x, y) => doc.elementFromPoint?.(x, y) ?? null,
    positionOf: (element) => view?.getComputedStyle?.(element)?.position || "",
    rectOf: (element) => element.getBoundingClientRect(),
    extra: openOverlays(rect, doc, view),
  });
  return cutouts === null || cutouts.length > 0;
}

export function samePlace(a, b) {
  return Boolean(a && b) && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height && a.visible === b.visible && a.host === b.host;
}

// Draws the slot and keeps the view on it. shown() says whether this tab is the
// one on screen in its pane; covered() forces the picture (a tab being dragged);
// host() names the window the Browser is in. Returns the slot element, whose
// focus() gives the page the keyboard and whose remove() lets the view go.
export function attachBrowserView(bridge, sessionId, container, viewRef, {
  clientId = "",
  host = () => "main",
  shown = () => true,
  covered = () => false,
  onUpdate = () => {},
  checkMs = CHECK_MS,
} = {}) {
  if (!container || typeof container.ownerDocument?.createElement !== "function") return null;
  const slot = container.ownerDocument.createElement("div");
  slot.setAttribute("data-testid", "browser-view-slot");
  slot.setAttribute("data-browser-view", sessionId);
  Object.assign(slot.style, { position: "absolute", inset: "0", overflow: "hidden", background: "#ffffff" });
  const picture = container.ownerDocument.createElement("img");
  picture.setAttribute("alt", "");
  picture.setAttribute("aria-hidden", "true");
  picture.setAttribute("draggable", "false");
  Object.assign(picture.style, { position: "absolute", inset: "0", width: "100%", height: "100%", display: "none", userSelect: "none" });
  slot.appendChild(picture);

  let disposed = false;
  let last = null;
  let picturing = false;
  let snapshotting = false;
  let watchedWindow = null;
  let observer = null;

  const showPicture = (source) => {
    if (!source) return;
    picture.src = source;
    picture.style.display = "block";
  };
  const hidePicture = () => {
    picture.style.display = "none";
    picture.removeAttribute("src");
  };
  const send = (next) => {
    if (samePlace(last, next)) return;
    last = next;
    void Promise.resolve(bridge.place?.(sessionId, next)).catch(() => {});
  };

  const report = () => {
    try {
      place();
    } catch {
      // A slot in a document that is going away; the next check tries again.
    }
  };
  const place = () => {
    if (disposed) return;
    const doc = slot.ownerDocument;
    const view = doc?.defaultView;
    if (!view) return;
    // The Browser moved to another window: watch that one instead.
    if (watchedWindow !== view) {
      observer?.disconnect();
      watchedWindow?.removeEventListener?.("resize", report);
      watchedWindow = view;
      view.addEventListener?.("resize", report);
      observer = typeof view.ResizeObserver === "function" ? new view.ResizeObserver(report) : null;
      observer?.observe(slot);
    }
    if (typeof slot.getBoundingClientRect !== "function") return;
    const rect = slot.getBoundingClientRect();
    const visibleInPage = slot.isConnected && typeof slot.checkVisibility === "function" ? slot.checkVisibility({ visibilityProperty: true }) : slot.isConnected;
    const onScreen = shown() && visibleInPage && rect.width > 1 && rect.height > 1 && doc.visibilityState !== "hidden";
    const where = { x: rect.left, y: rect.top, width: rect.width, height: rect.height, host: host() };
    if (!onScreen) {
      picturing = false;
      hidePicture();
      send({ ...(last || where), visible: false, host: where.host });
      return;
    }
    const cover = covered() || slotCovered(slot, rect, doc, view);
    if (cover && !picturing) {
      if (snapshotting) return;
      // Take the picture while the page still shows, then step the view aside.
      snapshotting = true;
      void Promise.resolve(bridge.viewSnapshot?.(sessionId))
        .then((result) => { if (!disposed) showPicture(result?.snapshot || ""); })
        .catch(() => {})
        .finally(() => {
          snapshotting = false;
          if (disposed) return;
          picturing = true;
          send({ ...where, visible: false });
        });
      return;
    }
    if (cover) {
      send({ ...where, visible: false });
      return;
    }
    if (picturing) {
      picturing = false;
      // The view is back on top; drop the picture a moment later so nothing flickers.
      view.setTimeout?.(() => { if (!picturing) hidePicture(); }, 80);
    }
    send({ ...where, visible: true });
  };

  const timer = setInterval(report, checkMs);
  const remove = typeof slot.remove === "function" ? slot.remove.bind(slot) : () => slot.parentNode?.removeChild?.(slot);
  slot.focus = () => { void Promise.resolve(bridge.focus?.(sessionId)).catch(() => {}); };
  slot.place = report;
  slot.remove = () => {
    if (disposed) return;
    disposed = true;
    clearInterval(timer);
    observer?.disconnect();
    watchedWindow?.removeEventListener?.("resize", report);
    void Promise.resolve(bridge.viewDetached?.(sessionId)).catch(() => {});
    remove();
  };
  viewRef.current = slot;
  container.appendChild(slot);
  report();
  void Promise.resolve(bridge.adopt?.(sessionId, -1, clientId))
    .then((state) => { if (state && viewRef.current === slot) onUpdate(state); })
    .catch((error) => {
      if (viewRef.current === slot) onUpdate({ error: error instanceof Error ? error.message : "Could not show the page.", loading: false });
    });
  return slot;
}
