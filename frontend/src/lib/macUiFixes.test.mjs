import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { loadCodeDrafts, saveCodeDrafts, CODE_DRAFTS_KEY } from "./codeDrafts.js";
import { normalizedBinding, normalizeAppSettings, keybindingConflictIds, matchesKeybinding, DEFAULT_APP_SETTINGS } from "./settings.js";
import { pasteIntoEditor } from "./editorCommands.js";

function storage() { const values = new Map(); return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; }

test("dirty file drafts survive renderer recreation and saved/discarded drafts disappear", () => {
  const disk = storage();
  const sessions = new Map([["/project/file.ts", { content: "unsaved", savedContent: "disk", viewState: { cursorState: [] } }], ["/project/clean.ts", { content: "clean", savedContent: "clean" }]]);
  assert.equal(saveCodeDrafts(sessions, disk), true);
  const restored = new Map(loadCodeDrafts(disk));
  assert.equal(restored.size, 1);
  assert.equal(restored.get("/project/file.ts").content, "unsaved");
  restored.get("/project/file.ts").savedContent = "unsaved";
  assert.equal(saveCodeDrafts(restored, disk), true);
  assert.equal(disk.getItem(CODE_DRAFTS_KEY), undefined);
  sessions.delete("/project/file.ts");
  saveCodeDrafts(sessions, disk);
  assert.deepEqual(loadCodeDrafts(disk), []);
});
test("corrupt drafts and quota failures do not claim successful preservation", () => {
  assert.deepEqual(loadCodeDrafts({ getItem: () => '{oops' }), []);
  assert.equal(saveCodeDrafts(new Map([["/file", { content: "dirty", savedContent: "" }]]), { setItem: () => { throw Error("quota"); } }), false);
});
test("unsupported chords cannot fire from their last key or remain in settings", () => {
  const gesture = { code: "KeyF", metaKey: true };
  assert.equal(matchesKeybinding(gesture, "Mod+KeyK Mod+KeyF", "MacIntel"), false);
  const settings = normalizeAppSettings({ keybindings: { "editor.find": "Mod+KeyK Mod+KeyF", "project.open": "Mod+KeyK Mod+KeyO" } });
  assert.equal(settings.keybindings["editor.find"], "Mod+KeyF");
  assert.equal(settings.keybindings["project.open"], "Mod+KeyK Mod+KeyO");
});
test("Mac aliases and chord prefixes warn about conflicting commands in overlapping scopes", () => {
  const settings = normalizeAppSettings({ keybindings: { "browser.open": "Meta+KeyF" } });
  assert.ok(keybindingConflictIds(settings, "editor.find", "Mod+KeyF", "MacIntel").includes("browser.open"));
  assert.equal(normalizedBinding("Shift+Mod+KeyF", "MacIntel"), normalizedBinding("Meta+Shift+KeyF", "MacIntel"));
  assert.ok(keybindingConflictIds(settings, "file.open", "Mod+KeyK", "MacIntel").includes("project.open"));
  assert.deepEqual(keybindingConflictIds(DEFAULT_APP_SETTINGS, "editor.find", "Mod+KeyF", "MacIntel"), []);
});
test("explicit native menu paste edits every selection with an undo boundary and respects read-only files", async () => {
  let content = "", boundaries = 0;
  const editor = { getModel: () => ({ isDisposed: () => false }), focus() {}, pushUndoStop: () => boundaries++, getSelections: () => [{ startLineNumber: 1 }], executeEdits: (_source, edits) => { content = edits[0].text; } };
  await pasteIntoEditor(editor, async () => "clipboard sentinel");
  assert.equal(content, "clipboard sentinel"); assert.equal(boundaries, 2);
  await assert.rejects(pasteIntoEditor(editor, async () => "overwrite", true), /read only/);
  await assert.rejects(pasteIntoEditor(editor), /unavailable/);
});

const appSource = fs.readFileSync(new URL("../pages/App.jsx", import.meta.url), "utf8");
function updateCheck(response) {
  const state = { supported: true, platform: "darwin", downloaded: false, info: null };
  const notices = [];
  const context = { useCallback: fn => fn, window: { goferUpdates: { check: async () => response } }, Error,
    setUpdateState: patch => Object.assign(state, typeof patch === "function" ? patch(state) : patch), setUpdateNotice: notice => notices.push(notice) };
  vm.createContext(context);
  vm.runInContext(appSource.slice(appSource.indexOf("  const checkForUpdates = useCallback"), appSource.indexOf("  useEffect(() => {\n    if (!window.goferUpdates?.onState")) + "\nglobalThis.check = checkForUpdates;", context);
  return { state, notices, check: context.check };
}
test("failed update responses remain errors; manual and ready states preserve their full contract", async () => {
  const failed = updateCheck({ available: false, error: "GitHub releases API returned 403" });
  await failed.check();
  assert.equal(failed.state.error, "GitHub releases API returned 403");
  assert.equal(failed.notices.at(-1).type, "error");
  const manual = updateCheck({ available: true, supported: false, platform: "darwin", info: { version: "0.4.0", installerUrl: "trusted" }, progress: null });
  await manual.check();
  assert.equal(manual.state.supported, false); assert.equal(manual.state.info.version, "0.4.0");
  assert.match(manual.notices.at(-1).message, /0.4.0/);
  const ready = updateCheck({ available: true, downloaded: true, supported: true, info: { version: "0.4.0" }, progress: { percent: 100 } });
  await ready.check(); assert.equal(ready.state.downloaded, true); assert.equal(ready.state.progress.percent, 100);
});

test("graph trackpad scrolling pans both axes; explicit zoom ignores a zero vertical delta", () => {
  const source = fs.readFileSync(new URL("../components/DagCanvas.jsx", import.meta.url), "utf8");
  let viewport = { x: 0, y: 0, scale: 1 }, zooms = 0;
  const context = { setViewport: fn => { viewport = fn(viewport); }, zoomViewportAtPoint: () => zooms++ };
  vm.runInNewContext(source.slice(source.indexOf("  function handleCanvasWheel("), source.indexOf("  function zoomViewportAtPoint(")), context);
  const base = { preventDefault() {}, currentTarget: { clientHeight: 100, getBoundingClientRect: () => ({ left: 0, top: 0 }) }, clientX: 100, clientY: 100, deltaMode: 0, deltaX: 120, deltaY: 0 };
  context.handleCanvasWheel(base); assert.equal(viewport.scale, 1); assert.equal(viewport.x, -120); assert.equal(zooms, 0);
  context.handleCanvasWheel({ ...base, deltaX: 0, deltaY: 40 }); assert.equal(viewport.y, -40); assert.equal(zooms, 0);
  context.handleCanvasWheel({ ...base, ctrlKey: true }); assert.equal(zooms, 0);
  context.handleCanvasWheel({ ...base, ctrlKey: true, deltaY: -10 }); assert.equal(zooms, 1);
});

test("studio reset zoom handles Command+0 and Ctrl+0 outside graph focus", () => {
  let zoom = 140, prevented = 0;
  const context = { matchesKeybinding: (event, binding) => matchesKeybinding(event, binding, "MacIntel"), setTextZoom: value => { zoom = value; }, eventTargetsGraphVisualization: () => false, textZoomDirection: () => 0 };
  vm.runInNewContext(appSource.slice(appSource.indexOf("    function handleTextZoomKeydown("), appSource.indexOf("    function handleTextZoomWheel(")), context);
  for (const modifier of ["metaKey", "ctrlKey"]) {
    zoom = 140;
    context.handleTextZoomKeydown({ code: "Digit0", [modifier]: true, preventDefault: () => prevented++ });
    assert.equal(zoom, 100);
  }
  assert.equal(prevented, 2);
});
