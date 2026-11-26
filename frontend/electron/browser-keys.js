// Standard browser keys, shared by the two places that read them: the Electron main
// process while a web page has focus (browser-utils.cjs, which require()s this ES module)
// and the browser toolbar in the studio window (src/lib/browserShortcuts.js).
//
// Each binding names the keys it accepts, written as KeyboardEvent.key (Electron's
// input.key is the same value), and the modifiers held with them. "Mod" is Cmd on macOS
// and Ctrl elsewhere. Modifiers listed in `optional` may be up or down; every modifier
// not listed must be up. `mac: true` or `mac: false` limits a binding to one side.
// `outsideBrowser: true` marks keys the toolbar also takes while focus is elsewhere in
// the studio, because no app shortcut uses them.
export const BROWSER_KEYS = [
  { action: "focus-location", keys: ["d"], modifiers: ["Alt"], outsideBrowser: true },
  { action: "focus-location", keys: ["l"], modifiers: ["Mod"] },
  { action: "find", keys: ["f"], modifiers: ["Mod"], outsideBrowser: true },
  { action: "reload", keys: ["r"], modifiers: ["Mod"], outsideBrowser: true },
  { action: "reload", keys: ["F5"], modifiers: [] },
  { action: "reload-hard", keys: ["r", "F5"], modifiers: ["Mod", "Shift"] },
  { action: "reload-hard", keys: ["F5"], modifiers: ["Mod"] },
  { action: "reload-hard", keys: ["F5"], modifiers: ["Shift"] },
  { action: "back", keys: ["ArrowLeft"], modifiers: ["Alt"], outsideBrowser: true },
  { action: "forward", keys: ["ArrowRight"], modifiers: ["Alt"], outsideBrowser: true },
  { action: "back", keys: ["["], modifiers: ["Mod"], mac: true },
  { action: "forward", keys: ["]"], modifiers: ["Mod"], mac: true },
  { action: "toggle-devtools", keys: ["F12"], modifiers: [] },
  { action: "toggle-devtools", keys: ["i"], modifiers: ["Ctrl", "Shift"], mac: false },
  { action: "toggle-devtools", keys: ["i"], modifiers: ["Meta", "Alt"], mac: true },
  { action: "print", keys: ["p"], modifiers: ["Mod"] },
  { action: "bookmark", keys: ["d"], modifiers: ["Mod"] },
  { action: "history", keys: ["h"], modifiers: ["Ctrl"], mac: false },
  { action: "history", keys: ["y"], modifiers: ["Meta"], mac: true },
  { action: "reopen-closed-tab", keys: ["t"], modifiers: ["Mod", "Shift"] },
  { action: "new-private-tab", keys: ["n"], modifiers: ["Mod", "Shift"] },
  // "+" is Shift+= on most layouts.
  { action: "zoom-in", keys: ["=", "+"], modifiers: ["Mod"], optional: ["Shift"] },
  { action: "zoom-out", keys: ["-"], modifiers: ["Mod"] },
  { action: "zoom-reset", keys: ["0"], modifiers: ["Mod"] },
];

const MODIFIERS = ["Ctrl", "Meta", "Alt", "Shift"];

// press: { key, ctrl, meta, alt, shift }. Returns the matching binding or null.
export function browserKeyBinding(press = {}, platform = "") {
  const mac = platform === "darwin";
  const key = String(press.key ?? "").toLowerCase();
  if (!key) return null;
  const held = { Ctrl: Boolean(press.ctrl), Meta: Boolean(press.meta), Alt: Boolean(press.alt), Shift: Boolean(press.shift) };
  const named = (names = []) => new Set(names.map((name) => (name === "Mod" ? (mac ? "Meta" : "Ctrl") : name)));
  return BROWSER_KEYS.find((binding) => {
    if (binding.mac !== undefined && binding.mac !== mac) return false;
    if (!binding.keys.some((candidate) => candidate.toLowerCase() === key)) return false;
    const required = named(binding.modifiers);
    const optional = named(binding.optional);
    return MODIFIERS.every((modifier) => optional.has(modifier) || held[modifier] === required.has(modifier));
  }) ?? null;
}
