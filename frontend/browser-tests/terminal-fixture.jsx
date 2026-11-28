import React from "react";
import { createRoot } from "react-dom/client";
import { Terminal } from "@xterm/xterm";
import TerminalWorkspace from "../src/components/TerminalWorkspace.jsx";
import { DEFAULT_APP_SETTINGS } from "../src/lib/settings.js";
import { TERMINAL_OPEN_FILE_EVENT } from "../src/lib/terminalLinks.js";
import "../src/styles/index.css";

// The real terminal pane, xterm and its addons, with stand-ins for the desktop's shell
// bridge and file checks. Keep each terminal so a test can read what xterm did.
window.terminals = [];
const open = Terminal.prototype.open;
Terminal.prototype.open = function openAndRecord(element) {
  window.terminals.push(this);
  return open.call(this, element);
};

let sendData = null;
window.shellInput = [];
window.goferTerminal = {
  close: async () => {},
  create: async () => ({ cwd: "/fixture", id: "fixture-session", shell: "bash" }),
  onData(listener) {
    sendData = listener;
    return () => { sendData = null; };
  },
  onExit: () => () => {},
  onOpenEditor: () => () => {},
  resize: async () => {},
  write: async (_id, data) => { window.shellInput.push(data); },
};
// Prints text in the terminal as if the shell wrote it, resolving once xterm parsed it.
window.printInTerminal = (data) => new Promise((resolve) => {
  const terminal = window.terminals.at(-1);
  const parsed = terminal.onWriteParsed(() => { parsed.dispose(); resolve(true); });
  sendData({ data, id: "fixture-session" });
});
window.sessionReady = () => Boolean(sendData && window.terminals.length);
// Terminal links: the one file that exists, and what Ctrl+click opened.
const existingFiles = new Set(["/fixture/src/a.py"]);
window.goferDesktop = { workspace: { missingRecentFiles: async (paths) => paths.filter((item) => !existingFiles.has(item)) } };
window.openedUrls = [];
window.open = (url) => { window.openedUrls.push(url); return null; };
window.openedFiles = [];
window.addEventListener(TERMINAL_OPEN_FILE_EVENT, (event) => window.openedFiles.push(event.detail));
// Project search listens on the window for Ctrl+Shift+F; the terminal must keep it.
window.keysReachingWindow = [];
window.addEventListener("keydown", (event) => window.keysReachingWindow.push(event.key));
window.keysPressed = [];
window.addEventListener("keydown", (event) => window.keysPressed.push(event.key), true);

createRoot(document.getElementById("root")).render(
  <div style={{ height: 420, position: "relative", width: 900 }}>
    <TerminalWorkspace active projectRoot="/fixture" settings={DEFAULT_APP_SETTINGS} theme="light" />
  </div>,
);
