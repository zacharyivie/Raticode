/* global __dirname, process, console, clearTimeout, window, performance, setTimeout */
// Real Monaco, production editor component, generated text and no provider processes.
const { app, BrowserWindow } = require("electron");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
const deadline = setTimeout(() => app.exit(1), 60000);
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  const baseline = process.env.GOFER_PERF_BASELINE;
  server = await createServer({ root, configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [
    {
      name: "editor-measurement",
      enforce: "pre",
      transform(source, id) {
        if (baseline && id.endsWith("/src/lib/mergeConflicts.js")) {
          return execFileSync("git", ["show", `${baseline}:frontend/src/lib/mergeConflicts.js`], { cwd: root, encoding: "utf8" });
        }
        if (!id.endsWith("/src/components/CodeWorkspace.jsx")) return null;
        const original = baseline ? execFileSync("git", ["show", `${baseline}:frontend/src/components/CodeWorkspace.jsx`], { cwd: root, encoding: "utf8" }) : source;
        return original.replace("const TextCodeEditor =", "export const TextCodeEditor =");
      },
      configureServer(dev) {
        dev.middlewares.use(async (req, res, next) => {
          if (req.url !== "/") return next();
          res.setHeader("Content-Type", "text/html");
          res.end(await dev.transformIndexHtml('/', '<html><body><div id="root"></div><script type="module" src="/browser-tests/editor-performance.jsx"></script></body></html>'));
        });
      },
    }, (await import("@vitejs/plugin-react")).default(),
  ] });
  await server.listen();
  const win = new BrowserWindow({ width: 1280, height: 800, show: true, webPreferences: { contextIsolation: true, sandbox: false } });
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") console.error(message); });
  await win.loadURL(server.resolvedUrls.local[0]);
  const evaluate = fn => win.webContents.executeJavaScript(`(${fn})()`);
  for (let i = 0; i < 200; i++) {
    if (await evaluate(() => window.perfReady)) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await evaluate(() => window.perfReady), "Editor must initialize");
  const result = await evaluate(async () => {
    const { model, editor, metrics } = window.editorPerf;
    // Warm Monaco before collecting allocations and callback counts.
    await new Promise(resolve => setTimeout(resolve, 300));
    let reads = 0;
    const getValue = model.getValue.bind(model);
    model.getValue = (...args) => { reads++; return getValue(...args); };
    const callbacksBefore = metrics.updates;
    const samples = [];
    const synchronous = [];
    for (let i = 0; i < 100; i++) {
      const start = performance.now();
      editor.executeEdits("performance-test", [{ range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 1 }, text: "a" }]);
      synchronous.push(performance.now() - start);
      await new Promise(resolve => setTimeout(resolve, 0));
      samples.push(performance.now() - start);
    }
    const typingReads = reads;
    const updates = metrics.updates - callbacksBefore;
    const callsBefore = metrics.gitCalls;
    await new Promise(resolve => setTimeout(resolve, 2200));
    const visiblePolls = metrics.gitCalls - callsBefore;
    window.editorPerf.hide();
    await new Promise(resolve => setTimeout(resolve, 100));
    const hiddenBefore = metrics.gitCalls;
    await new Promise(resolve => setTimeout(resolve, 2200));
    const hiddenPolls = metrics.gitCalls - hiddenBefore;
    await window.editorPerf.ref.current.save();
    const savedImmediately = metrics.saved.startsWith("a".repeat(100));
    editor.executeEdits("manual-revert", [{ range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 1 }, text: "x" }]);
    await new Promise(resolve => setTimeout(resolve, 0));
    editor.executeEdits("manual-revert", [{ range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 2 }, text: "" }]);
    await new Promise(resolve => setTimeout(resolve, 0));
    const manualRevertClean = !metrics.dirty;
    samples.sort((a, b) => a - b);
    synchronous.sort((a, b) => a - b);
    return { fileBytes: getValue().length, edits: 100, typingGetValueCalls: typingReads, parentUpdates: updates,
      synchronousMedianMs: synchronous[50], synchronousP95Ms: synchronous[95], editMedianMs: samples[50], editP95Ms: samples[95], visiblePolls, hiddenPolls, savedImmediately, manualRevertClean };
  });
  console.log(JSON.stringify(result, null, 2));
  assert.ok(result.manualRevertClean, "Typing and clearing back to saved text must clear dirty state");
  assert.ok(result.savedImmediately, "Immediate save must include the most recent edit");
  if (!baseline) {
    assert.ok(result.typingGetValueCalls < 10, "Typing must not materialize the whole document per edit");
    assert.ok(result.parentUpdates < 10, "Typing must not notify the workspace per edit");
    assert.equal(result.hiddenPolls, 0);
  }
  if (process.env.GOFER_PERF_OUTPUT) fs.writeFileSync(process.env.GOFER_PERF_OUTPUT, JSON.stringify(result, null, 2));
  win.destroy();
  await server.close();
  clearTimeout(deadline);
  app.quit();
}).catch(async error => { console.error(error); if (server) await server.close(); app.exit(1); });
