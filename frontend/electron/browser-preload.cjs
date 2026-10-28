/* global window */

const { ipcRenderer } = require("electron");

const LINK_CHANNEL = "gofer:browser-link-clicked";
const NAVIGATION_CHANNEL = "gofer:browser-navigation";
const ZOOM_CHANNEL = "gofer:browser-zoom";

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

function isEditableNode(node) {
  return Boolean(
    node?.isContentEditable
    || ["INPUT", "SELECT", "TEXTAREA"].includes(node?.tagName),
  );
}
