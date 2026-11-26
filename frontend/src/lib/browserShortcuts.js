import { browserKeyBinding } from "../../electron/browser-keys.js";

// Shortcuts handled by the browser chrome in the studio window, from the table shared
// with the Electron side (electron/browser-keys.js). Standard browser keys that would
// collide with app bindings (Ctrl+L, Ctrl+P, ...) apply only while focus is inside this
// browser ("scoped").
export function browserChromeShortcutAction(event, platform = "", { scoped = false } = {}) {
  if (event.defaultPrevented || event.target?.closest?.("[data-terminal-workspace], .xterm")) return null;
  if (event.repeat) return "";
  const binding = browserKeyBinding(
    { key: event.key, ctrl: event.ctrlKey, meta: event.metaKey, alt: event.altKey, shift: event.shiftKey },
    platform,
  );
  if (!binding || (!scoped && !binding.outsideBrowser)) return "";
  return binding.action;
}
