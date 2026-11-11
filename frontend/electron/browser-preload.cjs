/* global window */

const { contextBridge, ipcRenderer } = require("electron");

const LINK_CHANNEL = "gofer:browser-link-clicked";
const NAVIGATION_CHANNEL = "gofer:browser-navigation";
const ZOOM_CHANNEL = "gofer:browser-zoom";
const CONTEXT_ELEMENT_CHANNEL = "gofer:browser-context-element";

// Remembers the page's previous and next handlers for the media bar. An exact
// copy of mediaPageHook in browser-media.cjs (a sandboxed preload cannot load
// other files); read the explanation there. A test keeps the two equal.
function mediaPageHook() {
  const proto = globalThis.MediaSession?.prototype;
  const descriptor = proto && Object.getOwnPropertyDescriptor(proto, "setActionHandler");
  if (typeof descriptor?.value !== "function") return;
  const handlers = new Map();
  const wrapped = new Proxy(descriptor.value, {
    apply(target, session, args) {
      const result = Reflect.apply(target, session, args);
      if (typeof args[1] === "function") handlers.set(args[0], args[1]);
      else handlers.delete(args[0]);
      return result;
    },
  });
  Object.defineProperty(proto, "setActionHandler", { ...descriptor, value: wrapped });
  for (const action of ["play", "pause", "previoustrack", "nexttrack"]) {
    window.addEventListener(`raticode-media-has-${action}`, (event) => {
      if (handlers.has(action)) event.preventDefault();
    }, true);
    window.addEventListener(`raticode-media-run-${action}`, (event) => {
      const handler = handlers.get(action);
      if (!handler) return;
      event.preventDefault();
      handler({ action });
    }, true);
  }
}

try {
  contextBridge.executeInMainWorld({ func: mediaPageHook });
} catch { /* The media bar then shows no previous or next button for this page. */ }

// Every handler below ignores events a page script dispatched itself
// (isTrusted is false). Only real user input may open tabs, zoom or go back.

window.addEventListener("wheel", (event) => {
  if (
    !event.isTrusted
    || event.defaultPrevented
    || (!event.ctrlKey && !event.metaKey)
    || event.altKey
    || event.deltaY === 0
  ) return;
  event.preventDefault();
  event.stopPropagation();
  ipcRenderer.send(ZOOM_CHANNEL, {
    direction: event.deltaY < 0 ? 1 : -1,
  });
}, { capture: true, passive: false });

function linkFor(event) {
  const anchor = event.composedPath().find((node) => node?.tagName === "A" || node?.tagName === "AREA");
  if (!anchor || anchor.hasAttribute("download")) return null;
  const rawHref = String(anchor.getAttribute?.("href") || "").trim();
  const localPreview = /^file:/i.test(window.location.href);
  const url = localPreview && rawHref.startsWith("//")
    ? `https:${rawHref}`
    : String(anchor.href || "").trim();
  if (!/^(?:https?|file):/i.test(url)) return null;
  return { anchor, url, localPreview };
}

function opensNewContext(anchor) {
  const target = String(anchor.getAttribute?.("target") || "").trim().toLowerCase();
  return Boolean(target) && !["_self", "_parent", "_top"].includes(target)
    // A named target that matches a frame on this page stays in that frame.
    && (target === "_blank" || !window.frames?.[target]);
}

function handleLinkClick(event, background) {
  if (!event.isTrusted || event.defaultPrevented) return;
  const link = linkFor(event);
  if (!link) return;
  const { anchor, url, localPreview } = link;
  const modified = event.ctrlKey || event.metaKey;
  const localFile = /^file:/i.test(url);
  // Keep same-document anchors in the preview, including full file URLs.
  const sameDocument = url.split("#", 1)[0] === window.location.href.split("#", 1)[0];
  const websiteFromPreview = localPreview && /^https?:/i.test(url);
  const newContext = opensNewContext(anchor) || event.shiftKey;
  if (!background && !modified && !websiteFromPreview && !newContext && (!localFile || sameDocument)) return;
  event.preventDefault();
  event.stopPropagation();
  ipcRenderer.send(LINK_CHANNEL, {
    url,
    disposition: background || (modified && !event.shiftKey) ? "background" : "foreground",
  });
}

window.addEventListener("click", (event) => {
  if (event.button !== 0) return;
  handleLinkClick(event, false);
}, true);

// Middle-click opens links in a background tab, as in other browsers.
window.addEventListener("auxclick", (event) => {
  if (event.button !== 1) return;
  handleLinkClick(event, true);
}, true);

window.addEventListener("keydown", (event) => {
  if (
    !event.isTrusted
    || event.defaultPrevented
    || event.repeat
    || event.key !== "Backspace"
    || event.altKey
    || event.ctrlKey
    || event.metaKey
    || event.shiftKey
    || event.composedPath().some(isEditableNode)
  ) return;
  event.preventDefault();
  ipcRenderer.send(NAVIGATION_CHANNEL, { action: "back" });
}, true);

// Ask Rem about this: describe the element the user right-clicked.
window.addEventListener("contextmenu", (event) => {
  if (!event.isTrusted) return;
  ipcRenderer.send(CONTEXT_ELEMENT_CHANNEL, describeElement(event.composedPath()[0] || event.target));
}, true);

function describeElement(node) {
  const element = node?.nodeType === 1 ? node : node?.parentElement;
  if (!element?.tagName) return null;
  const attribute = (name) => String(element.getAttribute?.(name) || "");
  return {
    tag: element.tagName.toLowerCase(),
    path: elementPath(element),
    role: attribute("role"),
    label: attribute("aria-label"),
    alt: attribute("alt"),
    title: attribute("title"),
    href: String(element.closest?.("a[href]")?.href || ""),
    src: String(element.currentSrc || element.src || ""),
    text: String(element.innerText || element.textContent || "").trim().slice(0, 8000),
    html: String(element.outerHTML || "").slice(0, 8000),
  };
}

function elementPath(element) {
  const parts = [];
  for (let current = element; current?.tagName && parts.length < 6; current = current.parentElement) {
    const id = current.id ? `#${current.id}` : "";
    const classes = [...(current.classList || [])].slice(0, 3).map((name) => `.${name}`).join("");
    parts.unshift(`${current.tagName.toLowerCase()}${id}${classes}`);
    if (id) break;
  }
  return parts.join(" > ");
}

function isEditableNode(node) {
  return Boolean(
    node?.isContentEditable
    || ["INPUT", "SELECT", "TEXTAREA"].includes(node?.tagName),
  );
}
