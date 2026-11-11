// The right-click menu of a streamed Chrome or Edge tab. Raticode's own tabs
// get Electron's context-menu event; a browser running without a window has
// no menu of its own, so the tab works out what the user right-clicked.
//
// WATCH runs in the tab's private script world, which shares the page's
// DOM but none of its scripts. It remembers the last right-click, and whether
// the page handled it with a menu of its own (preventDefault). READ returns
// that, or, when the watcher wasn't in place yet, what is under the pointer.

const DESCRIBE = `function describe(target, prevented, x, y) {
  const element = target instanceof Element ? target : target?.parentElement || null;
  const link = element?.closest?.("a[href], area[href]");
  const media = element?.closest?.("img, video, audio");
  const field = element?.closest?.("input, textarea, [contenteditable]");
  const editable = Boolean(field) && !field.disabled && !field.readOnly && (field.isContentEditable || field.localName === "textarea"
    || field.localName === "input" && !/^(?:button|checkbox|radio|submit|reset|range|color|file|image|hidden)$/i.test(field.type || ""));
  let selection = String(getSelection() || "");
  if (editable && typeof field.selectionStart === "number" && field.type !== "password") selection = field.value.slice(field.selectionStart, field.selectionEnd);
  return {
    at: Date.now(),
    prevented: Boolean(prevented),
    x, y,
    linkURL: link ? String(link.href || "") : "",
    srcURL: media ? String(media.currentSrc || media.src || "") : "",
    mediaType: media ? (media.localName === "img" ? "image" : media.localName) : "none",
    selectionText: selection.slice(0, 20000),
    isEditable: editable,
  };
}`;

const WATCH = `(() => {
  if (window.__raticodeMenuWatch) return true;
  window.__raticodeMenuWatch = true;
  ${DESCRIBE}
  // Bubbling to the window, after the page's own handlers have run.
  addEventListener("contextmenu", (event) => {
    window.__raticodeMenu = describe(event.target, event.defaultPrevented, event.clientX, event.clientY);
  }, false);
  return true;
})()`;

function read(x, y) {
  return `(() => {
    ${DESCRIBE}
    const seen = window.__raticodeMenu;
    window.__raticodeMenu = null;
    if (seen && Date.now() - seen.at < 3000) return seen;
    return describe(document.elementFromPoint(${Number(x) || 0}, ${Number(y) || 0}), false, ${Number(x) || 0}, ${Number(y) || 0});
  })()`;
}

// Editing commands from the menu, by the name the browser's own editing
// command has. Copying goes through the system clipboard instead.
const EDIT_COMMANDS = { cut: "delete", selectAll: "selectAll", undo: "undo", redo: "redo" };

// What Electron's context-menu event would have reported, scaled to the
// studio's view of the tab.
function menuParams(seen, zoom = 1) {
  if (!seen || typeof seen !== "object" || seen.prevented) return null;
  const http = (value) => (/^https?:/i.test(String(value || "")) ? String(value).slice(0, 4096) : "");
  return {
    x: Math.round((Number(seen.x) || 0) * zoom),
    y: Math.round((Number(seen.y) || 0) * zoom),
    linkURL: http(seen.linkURL),
    srcURL: http(seen.srcURL),
    mediaType: ["image", "video", "audio"].includes(seen.mediaType) ? seen.mediaType : "none",
    selectionText: String(seen.selectionText || "").slice(0, 20000),
    isEditable: seen.isEditable === true,
  };
}

module.exports = { EDIT_COMMANDS, WATCH, menuParams, read };
