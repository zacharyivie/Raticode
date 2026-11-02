// Layout choices shared by Settings, the editor and the bottom panel.

// The editor tab that holds every terminal when terminals open beside files.
export const TERMINAL_TAB_PATH = "raticode-terminal:workspace";
// detail: { newTerminal?: boolean, background?: boolean }
export const OPEN_TERMINAL_TAB_EVENT = "gofer:open-terminal-tab";

export function isTerminalTabPath(path) {
  return path === TERMINAL_TAB_PATH;
}

export function terminalInEditor(settings) {
  return settings?.layout?.terminalLocation === "editor";
}

export function openTerminalTab(detail = {}) {
  window.dispatchEvent(new CustomEvent(OPEN_TERMINAL_TAB_EVENT, { detail }));
}

// The bottom panel's first state. A terminal that lives in the editor opens
// there instead, so the panel stays collapsed.
export function bottomPanelStartState(layout = {}) {
  const tab = layout.bottomPanelAtStartup;
  if (!tab || tab === "collapsed" || (tab === "terminal" && layout.terminalLocation === "editor")) {
    return { collapsed: true, tab: "timeline", explicit: false };
  }
  return { collapsed: false, tab, explicit: true };
}
