// The macOS menu bar's Help menu. Each item asks the window to run the same
// action as Help in the window's own menu (AppChrome.jsx), so the links, the
// Report a Problem details and About Raticode come from one place.
const HELP_MENU_ITEMS = [
  ["help.documentation", "Documentation"],
  ["help.keyboardShortcuts", "Keyboard Shortcuts"],
  ["help.troubleshooting", "Troubleshooting"],
  null,
  ["help.reportProblem", "Report a Problem..."],
  null,
  ["help.whatsNew", "What's New"],
  ["help.updates", "Check for Updates..."],
  ["help.settings", "Settings"],
  null,
  ["help.about", "About Raticode"],
];

// send(item) delivers { action: "help-menu", item } to the main window.
function helpMenuTemplate(send) {
  return {
    role: "help",
    submenu: HELP_MENU_ITEMS.map((entry) => entry
      ? { label: entry[1], click: () => send(entry[0]) }
      : { type: "separator" }),
  };
}

module.exports = { helpMenuTemplate };
