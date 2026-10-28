// Shortcuts handled by the browser chrome in the studio window. Standard
// browser keys that would collide with app bindings (Ctrl+L, Ctrl+P, ...)
// apply only while focus is inside this browser ("scoped").
export function browserChromeShortcutAction(event, platform = "", { scoped = false } = {}) {
  if (event.defaultPrevented || event.target?.closest?.("[data-terminal-workspace], .xterm")) return null;
  if (event.repeat) return "";
  const key = String(event.key ?? "").toLowerCase();
  const mac = platform === "darwin";
  const primary = mac ? event.metaKey : event.ctrlKey;
  const plain = !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey;
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "d") {
    return "focus-location";
  }
  if (primary && !event.altKey && !event.shiftKey && key === "f") return "find";
  if (primary && !event.altKey && !event.shiftKey && key === "r") return "reload";
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "arrowleft") {
    return "back";
  }
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "arrowright") {
    return "forward";
  }
  if (!scoped) return "";
  if (primary && !event.altKey && !event.shiftKey && key === "l") return "focus-location";
  if ((primary && event.shiftKey && key === "r") || (key === "f5" && (event.shiftKey || primary))) return "reload-hard";
  if (plain && key === "f5") return "reload";
  if ((plain && key === "f12") || (!mac && event.ctrlKey && event.shiftKey && key === "i") || (mac && event.metaKey && event.altKey && key === "i")) return "toggle-devtools";
  if (primary && !event.altKey && !event.shiftKey && key === "p") return "print";
  if (primary && !event.altKey && !event.shiftKey && key === "d") return "bookmark";
  if ((!mac && event.ctrlKey && !event.altKey && !event.shiftKey && key === "h") || (mac && event.metaKey && !event.shiftKey && key === "y")) return "history";
  if (primary && event.shiftKey && key === "t") return "reopen-closed-tab";
  if (primary && !event.altKey && (key === "=" || key === "+")) return "zoom-in";
  if (primary && !event.altKey && key === "-") return "zoom-out";
  if (primary && !event.altKey && key === "0") return "zoom-reset";
  return "";
}
