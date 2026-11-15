import { createContext, useContext } from "react";

// Where the Browser plugin is drawn: Raticode's window, or the Browser's own
// window. Either way its components run in Raticode's page; only their DOM
// moves. Code in the Browser listens on this window and document instead of
// the global ones, so keys, clicks and visibility follow the Browser.
export const BrowserHostContext = createContext(null);

const DOCKED = {
  poppedOut: false,
  // Bumped each time the Browser moves to another window.
  key: 0,
  get window() { return typeof window === "undefined" ? undefined : window; },
  get document() { return typeof document === "undefined" ? undefined : document; },
  register: () => () => {},
  popOut: () => {},
  popIn: () => {},
  available: false,
};

export function useBrowserHost() {
  return useContext(BrowserHostContext) || DOCKED;
}

// What moving windows does to the tabs.
export const MOVE_NOTE = "Every tab keeps running while the browser changes windows: pages don't reload, and sound and video keep playing.";

// Copies the studio's styles and theme onto the Browser's window, and keeps
// them in step: Vite adds stylesheets as lazy parts load, and the theme
// lives in classes and attributes on <html> and <body>.
export function mirrorStudioStyles(source, target) {
  const copied = new Map();
  const copy = (node) => {
    if (copied.has(node)) return;
    const isSheet = node.tagName === "STYLE" || (node.tagName === "LINK" && /\bstylesheet\b/i.test(node.getAttribute?.("rel") || ""));
    if (!isSheet) return;
    const clone = node.cloneNode(true);
    // The window's blank page has no address of its own to resolve links against.
    if (node.tagName === "LINK" && node.href) clone.setAttribute("href", node.href);
    copied.set(node, clone);
    target.head.appendChild(clone);
  };
  const syncAttributes = (from, to) => {
    for (const { name } of [...to.attributes]) if (!from.hasAttribute(name)) to.removeAttribute(name);
    for (const { name, value } of [...from.attributes]) if (to.getAttribute(name) !== value) to.setAttribute(name, value);
  };
  for (const node of source.head.querySelectorAll("style, link")) copy(node);
  syncAttributes(source.documentElement, target.documentElement);
  syncAttributes(source.body, target.body);
  const View = source.defaultView;
  if (typeof View?.MutationObserver !== "function") return () => {};
  const heads = new View.MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) copy(node);
      for (const node of record.removedNodes) {
        copied.get(node)?.remove();
        copied.delete(node);
      }
      // A <style> Vite rewrites in place while developing.
      if (record.type === "characterData" || (record.type === "childList" && record.target?.tagName === "STYLE")) {
        const sheet = record.target?.tagName === "STYLE" ? record.target : record.target?.parentNode;
        const clone = copied.get(sheet);
        if (clone) clone.textContent = sheet.textContent;
      }
    }
  });
  heads.observe(source.head, { childList: true, subtree: true, characterData: true });
  const themes = new View.MutationObserver(() => {
    syncAttributes(source.documentElement, target.documentElement);
    syncAttributes(source.body, target.body);
  });
  themes.observe(source.documentElement, { attributes: true });
  themes.observe(source.body, { attributes: true });
  return () => {
    heads.disconnect();
    themes.disconnect();
  };
}
