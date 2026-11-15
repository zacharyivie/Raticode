// The listening overlay's bridge: it receives the dictation's phase and can ask to stop
// listening or to open Raticode. It never receives audio or the dictated words.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dictationOverlay", {
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("gofer:dictation-overlay-state", listener);
    return () => ipcRenderer.removeListener("gofer:dictation-overlay-state", listener);
  },
  stop: () => ipcRenderer.send("gofer:dictation-overlay-action", "stop"),
  open: () => ipcRenderer.send("gofer:dictation-overlay-action", "open"),
});
