/* global document, process, window */
// The command palette and quick open with real key presses: Ctrl+P opens a
// workflow, Ctrl+Shift+P then names that workflow before Run, and both keys give
// way inside the code editor.
const assert = require("node:assert/strict");
const fs = require("node:fs");

module.exports = async function exerciseCommandPalette({ windowRef, evaluate, waitFor, pressNativeKey, wait }) {
  windowRef.show();
  windowRef.focus();
  windowRef.webContents.focus();
  await waitFor(() => evaluate(() => document.hasFocus()), 25, "window focus");
  // Start from the page, not the code editor, which keeps the palette keys.
  await evaluate(() => { document.activeElement?.blur?.(); document.body.focus(); });
  const paletteMode = () => evaluate(() => document.querySelector("[data-command-palette]")?.getAttribute("data-command-palette") || "");
  const rows = () => evaluate(() => [...document.querySelectorAll("[data-command-palette] [role='option']")].map((row) => ({
    id: row.getAttribute("data-palette-item"), text: row.textContent, selected: row.getAttribute("aria-selected") === "true", disabled: row.getAttribute("aria-disabled") === "true",
  })));
  const footer = () => evaluate(() => document.querySelector("[data-command-palette] [role='status']")?.textContent || "");
  async function type(text) {
    for (const char of text) {
      windowRef.webContents.sendInputEvent({ type: "char", keyCode: char });
      await wait(15);
    }
    await wait(60);
  }

  // Quick open finds the workflow by name and opens its graph.
  await pressNativeKey("P", ["control"]);
  await waitFor(async () => (await paletteMode()) === "files", 25, "quick open");
  assert.equal(await evaluate(() => document.activeElement?.getAttribute("role")), "combobox", "typing goes to the search field");
  await waitFor(() => evaluate(() => (window.__goferBridgeCalls || []).some((call) => call.method === "workspace.listProjectFiles")), 25, "project files listed");
  await type("rattish editor");
  await waitFor(async () => (await rows())[0]?.id === "workflow:rattish-editor", 25, "workflow match first");
  await pressNativeKey("Return");
  await waitFor(async () => (await paletteMode()) === "", 25, "quick open closes");

  // The command palette lists commands with their keys and names the workflow Run starts.
  await pressNativeKey("P", ["control", "shift"]);
  await waitFor(async () => (await paletteMode()) === "commands", 25, "command palette");
  const all = await rows();
  assert.match(all.find((row) => row.id === "command:settings.open").text, /Ctrl\+,/);
  for (const id of ["action:workflow.new", "action:workflow.allRuns", "settings:devices", "action:open.browser"]) {
    assert.ok(all.some((row) => row.id === id), `missing ${id}`);
  }
  await type("run workflow");
  await waitFor(async () => (await rows())[0]?.id === "command:workflow.run", 25, "Run workflow first");
  const run = (await rows())[0];
  assert.equal(run.disabled, false, `Run workflow is available: ${run.text}`);
  assert.match(run.text, /Runs Rattish editor/);
  assert.equal(await footer(), "Enter runs Rattish editor");
  const shot = process.env.RATICODE_PALETTE_SCREENSHOT;
  if (shot) fs.writeFileSync(shot, (await windowRef.webContents.capturePage()).toPNG());
  await pressNativeKey("Escape");
  await waitFor(async () => (await paletteMode()) === "", 25, "palette closes on Escape");

  // Quick open puts the chosen workflow first next time, then finds files.
  await pressNativeKey("P", ["control"]);
  await waitFor(async () => (await rows())[0]?.id === "workflow:rattish-editor", 25, "recent workflow first");
  assert.match((await rows())[0].text, /recent/);
  await type("settingsjs");
  await waitFor(async () => (await rows())[0]?.id === "file:/workspace/current-worktree/src/lib/settings.js", 25, "file match");
  await pressNativeKey("Return");
  await waitFor(() => evaluate(() => (window.__goferBridgeCalls || []).some((call) => call.method === "textFiles.read" && String(call.payload).endsWith("src/lib/settings.js"))), 25, "file opened");

  // Inside the code editor both keys belong to the editor.
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".monaco-editor textarea"))), 25, "code editor");
  await evaluate(() => document.querySelector(".monaco-editor textarea").focus());
  await pressNativeKey("P", ["control"]);
  await pressNativeKey("P", ["control", "shift"]);
  await wait(150);
  assert.equal(await paletteMode(), "", "the palette gives way to the code editor");
};
