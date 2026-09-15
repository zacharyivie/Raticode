/* global process */

const bridgeCalls = [];

function recordBridgeCall(method, payload) {
  bridgeCalls.push({ method, payload });
}

const selectedPath = "/workspace/inputs";
const apiBaseArgument = process.argv.find((value) => value.startsWith("--gofer-api-base-url="));

if (apiBaseArgument) {
  window.goferApiBaseUrl = apiBaseArgument.slice("--gofer-api-base-url=".length);
}

window.__goferBridgeCalls = bridgeCalls;

window.goferDesktop = {
  getDataDir: async () => {
    recordBridgeCall("getDataDir");
    return "/workspace";
  },
  getDroppedFilePath: (file) => {
    recordBridgeCall("getDroppedFilePath", { name: file?.name ?? "" });
    return file?.path || selectedPath;
  },
  grantDroppedPath: async (file) => {
    recordBridgeCall("grantDroppedPath", { name: file?.name ?? "" });
    return selectedPath;
  },
  workspace: {
    copyPath: async (options) => {
      recordBridgeCall("workspace.copyPath", options);
      return true;
    },
    createFile: async (options) => {
      recordBridgeCall("workspace.createFile", options);
      return { path: `${options.directory}/${options.name}` };
    },
    createFolder: async (options) => {
      recordBridgeCall("workspace.createFolder", options);
      return { path: `${options.directory}/${options.name}` };
    },
    deletePath: async (targetPath) => {
      recordBridgeCall("workspace.deletePath", targetPath);
      return true;
    },
    getPathInfo: async (targetPath) => {
      recordBridgeCall("workspace.getPathInfo", targetPath);
      return {
        basename: targetPath.split("/").filter(Boolean).at(-1) || "workspace",
        isDirectory: !targetPath.endsWith(".json") && !targetPath.endsWith(".txt"),
        isFile: targetPath.endsWith(".json") || targetPath.endsWith(".txt"),
        path: targetPath,
      };
    },
    openPath: async (targetPath) => {
      recordBridgeCall("workspace.openPath", targetPath);
      return true;
    },
    pathGrantForApi: (targetPath) => {
      recordBridgeCall("workspace.pathGrantForApi", targetPath);
      return targetPath === selectedPath ? "test-grant" : "";
    },
    renamePath: async (options) => {
      recordBridgeCall("workspace.renamePath", options);
      return { path: options.nextPath };
    },
    revealPath: async (targetPath) => {
      recordBridgeCall("workspace.revealPath", targetPath);
      return true;
    },
    selectPath: async (options = {}) => {
      recordBridgeCall("workspace.selectPath", options);
      return selectedPath;
    },
  },
  textFiles: {
    read: async (targetPath) => {
      recordBridgeCall("textFiles.read", targetPath);
      return { content: targetPath.endsWith("workflow.rattish")
        ? "Rattish: 1\n\nWorkflow:\n  name: Rattish editor\n\nNode prepare:\n  type: bash-command\n  command: echo ready\n"
        : "{\n  \"enabled\": true\n}" };
    },
    write: async (options) => {
      recordBridgeCall("textFiles.write", options);
      return { bytesWritten: options?.content?.length ?? 0 };
    },
  },
};

const terminalDataListeners = new Set();
const terminalExitListeners = new Set();
let nextTerminalId = 1;

window.goferTerminal = {
  create: async (options = {}) => {
    const id = `browser-terminal-${nextTerminalId}`;
    nextTerminalId += 1;
    recordBridgeCall("terminal.create", options);
    setTimeout(() => {
      for (const listener of terminalDataListeners) {
        listener({ data: "Raticode browser terminal\r\n$ ", id });
      }
    }, 0);
    return { cwd: options.cwd || "/workspace", id, pid: nextTerminalId, shell: "bash" };
  },
  write: async (id, data) => {
    recordBridgeCall("terminal.write", { data, id });
    for (const listener of terminalDataListeners) listener({ data, id });
    return { written: true };
  },
  resize: async (id, cols, rows) => {
    recordBridgeCall("terminal.resize", { cols, id, rows });
    return { resized: true };
  },
  close: async (id) => {
    recordBridgeCall("terminal.close", { id });
    for (const listener of terminalExitListeners) listener({ exitCode: 0, id, signal: 0 });
    return { closed: true };
  },
  onData: (listener) => {
    terminalDataListeners.add(listener);
    return () => terminalDataListeners.delete(listener);
  },
  onExit: (listener) => {
    terminalExitListeners.add(listener);
    return () => terminalExitListeners.delete(listener);
  },
};
