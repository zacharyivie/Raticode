// The sections of Settings, in the order its sidebar lists them. The command
// palette and quick open offer each one, so both read this list.
export const SETTINGS_SECTIONS = Object.freeze([
  { id: "general", label: "General" },
  { id: "devices", label: "Devices" },
  { id: "computers", label: "Computers" },
  { id: "appearance", label: "Appearance" },
  { id: "editor", label: "Editor" },
  { id: "browser", label: "Browser" },
  { id: "terminal", label: "Terminal" },
  { id: "providers", label: "Providers" },
  { id: "usage", label: "Usage" },
  { id: "assistant", label: "Rem" },
  { id: "memory", label: "Memory" },
  { id: "developer", label: "Developer" },
  { id: "layout", label: "Layout" },
  { id: "plugins", label: "Plugins" },
  { id: "keybindings", label: "Keybindings" },
].map(Object.freeze));
