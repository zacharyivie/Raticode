// The Run sheet's bridge: it receives the sheet's state and sends back "run" with the
// text, or "cancel". It can't reach anything else in Raticode.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("workflowRunSheet", {
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("gofer:workflow-run-sheet-state", listener);
    return () => ipcRenderer.removeListener("gofer:workflow-run-sheet-state", listener);
  },
  run: (text) => ipcRenderer.send("gofer:workflow-run-sheet-action", { action: "run", text: String(text ?? "") }),
  cancel: () => ipcRenderer.send("gofer:workflow-run-sheet-action", { action: "cancel" }),
});
