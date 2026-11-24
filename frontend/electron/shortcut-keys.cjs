// One shortcut, written the way each Linux desktop wants it. The input is an Electron
// accelerator from toAccelerator() ("Control+Alt+Space", "Super+Shift+D", "F9"), which has
// already been checked, so anything that can't be turned into a desktop's format here is
// a gap in these tables, not a user error.

// Electron's key name -> X keysym name (the XDG shortcuts format and GNOME's) and Qt key code
// (KDE's kglobalaccel).
const KEYS = {
  Space: ["space", 0x20],
  Enter: ["Return", 0x01000004],
  Tab: ["Tab", 0x01000001],
  Backspace: ["BackSpace", 0x01000003],
  Delete: ["Delete", 0x01000007],
  Insert: ["Insert", 0x01000006],
  Home: ["Home", 0x01000010],
  End: ["End", 0x01000011],
  PageUp: ["Page_Up", 0x01000016],
  PageDown: ["Page_Down", 0x01000017],
  Up: ["Up", 0x01000013],
  Down: ["Down", 0x01000015],
  Left: ["Left", 0x01000012],
  Right: ["Right", 0x01000014],
  "`": ["grave", 0x60],
  "-": ["minus", 0x2d],
  "=": ["equal", 0x3d],
  "[": ["bracketleft", 0x5b],
  "]": ["bracketright", 0x5d],
  "\\": ["backslash", 0x5c],
  ";": ["semicolon", 0x3b],
  "'": ["apostrophe", 0x27],
  ",": ["comma", 0x2c],
  ".": ["period", 0x2e],
  "/": ["slash", 0x2f],
};

// Qt::KeyboardModifier bits.
const QT_MODIFIERS = { Shift: 0x02000000, Control: 0x04000000, Alt: 0x08000000, Super: 0x10000000 };
const QT_KEYPAD = 0x20000000;

function parse(accelerator) {
  const parts = String(accelerator || "").split("+");
  // "Control++" can't happen: toAccelerator never makes a "+" key.
  const key = parts.pop() || "";
  const modifiers = parts.map((part) => (part === "Command" ? "Super" : part));
  if (!key || modifiers.some((name) => !(name in QT_MODIFIERS))) return null;
  let keysym;
  let qt;
  if (/^[A-Z]$/.test(key)) {
    keysym = key.toLowerCase();
    qt = key.charCodeAt(0);
  } else if (/^[0-9]$/.test(key)) {
    keysym = key;
    qt = key.charCodeAt(0);
  } else if (/^num[0-9]$/.test(key)) {
    keysym = `KP_${key.slice(3)}`;
    qt = QT_KEYPAD | (0x30 + Number(key.slice(3)));
  } else if (/^F([1-9]|1[0-9]|2[0-4])$/.test(key)) {
    keysym = key;
    qt = 0x01000030 + Number(key.slice(1)) - 1;
  } else if (KEYS[key]) {
    [keysym, qt] = KEYS[key];
  } else {
    return null;
  }
  return { modifiers, keysym, qt };
}

/** "Control+Alt+Space" -> "CTRL+ALT+space", the XDG shortcuts format the portal takes. */
function portalTrigger(accelerator) {
  const parsed = parse(accelerator);
  if (!parsed) return "";
  const names = { Control: "CTRL", Alt: "ALT", Shift: "SHIFT", Super: "LOGO" };
  return [...parsed.modifiers.map((name) => names[name]), parsed.keysym].join("+");
}

/** "Control+Alt+Space" -> [0x0c000020], one Qt key combination for kglobalaccel. */
function kdeKeys(accelerator) {
  const parsed = parse(accelerator);
  if (!parsed) return [];
  return [parsed.modifiers.reduce((bits, name) => bits | QT_MODIFIERS[name], parsed.qt)];
}

/** "Control+Alt+Space" -> "<Control><Alt>space", GNOME's keybinding format. */
function gnomeBinding(accelerator) {
  const parsed = parse(accelerator);
  if (!parsed) return "";
  return `${parsed.modifiers.map((name) => `<${name}>`).join("")}${parsed.keysym}`;
}

/**
 * A desktop's own description of a key ("Ctrl+Alt+Space", "CTRL+ALT+space",
 * "Meta+Space") made readable, for Settings. It is shown, never parsed for a key.
 */
function readableTrigger(text) {
  const value = String(text || "").trim().slice(0, 80);
  if (!value) return "";
  return value
    .split("+")
    .map((part) => {
      const lower = part.trim().toLowerCase();
      return { ctrl: "Ctrl", control: "Ctrl", alt: "Alt", shift: "Shift", logo: "Meta", super: "Meta", meta: "Meta", space: "Space" }[lower]
        ?? (part.trim().length === 1 ? part.trim().toUpperCase() : part.trim());
    })
    .join("+");
}

module.exports = { gnomeBinding, kdeKeys, portalTrigger, readableTrigger };
