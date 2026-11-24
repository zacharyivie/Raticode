// Stand-in for monaco-editor's internal/common/workers.js, which the CSS, HTML, JSON and
// TypeScript language features import for createWebWorker. In Monaco 0.57 that module also
// imports nearly every editor feature (about 480 kB), registering them after editors exist.
// dev/monaco-patches.js points those imports here; this is the same helper without them.
import { editor } from "monaco-editor/editor/editor.api";

function startWorker({ label, createWorker }) {
  const environment = globalThis.MonacoEnvironment;
  if (typeof environment?.getWorker === "function") return environment.getWorker("workerMain.js", label);
  if (createWorker) return createWorker();
  throw new Error("MonacoEnvironment.getWorker is not defined");
}

export function createWebWorker(opts) {
  const worker = Promise.resolve(startWorker({
    label: opts.label ?? "monaco-editor-worker",
    createWorker: opts.createWorker,
  })).then(started => {
    started.postMessage("ignore");
    started.postMessage(opts.createData);
    return started;
  });
  return editor.createWebWorker({ worker, host: opts.host, keepIdleModels: opts.keepIdleModels });
}
