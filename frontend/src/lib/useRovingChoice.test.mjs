import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

// Every radio group and tab list on the desktop moves with the arrow keys through the shared
// useRovingChoice helper (src/lib/useRovingChoice.js). A file listed here keeps its own
// keyboard handling, for the reason given.
const KEEPS_OWN = new Map([
  ["components/RattishInspector.jsx", "node inspector tabs use handleInspectorTabKeyDown"],
  ["components/CodeFileExplorer.jsx", "the vertical sidebar view rail skips locked plugins"],
  ["components/CodeWorkspace.jsx", "editor tabs are draggable, scroll into view and carry close buttons"],
  ["components/OrganizationHome.jsx", "section tabs focus by id after the section changes"],
  ["components/SourceControlPanel.jsx", "source control tabs move by id"],
  ["components/RemMemoryPanel.jsx", "native radio inputs, which the browser moves between"],
  ["components/SecondBrainDialogs.jsx", "native radio inputs, which the browser moves between"],
]);

const SRC = path.resolve(import.meta.dirname, "..");
const GROUP = /role=(?:\{\s*)?["'`](radiogroup|tablist)["'`]/;
const USES_HELPER = /import\s*\{[^}]*\buseRovingChoice\b[^}]*\}\s*from\s*["'][./]*(?:lib\/)?useRovingChoice\.js["']/;

function jsxFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return jsxFiles(full);
    return entry.name.endsWith(".jsx") ? [full] : [];
  });
}

const files = jsxFiles(SRC).map(file => ({ name: path.relative(SRC, file).split(path.sep).join("/"), text: fs.readFileSync(file, "utf8") }));

test("every .jsx radio group and tab list uses the shared roving-focus helper", () => {
  const missing = files
    .filter(file => GROUP.test(file.text) && !KEEPS_OWN.has(file.name))
    .filter(file => !USES_HELPER.test(file.text) || !/\buseRovingChoice\(/.test(file.text))
    .map(file => file.name);
  assert.deepEqual(missing, [], "These files have role=\"radiogroup\" or role=\"tablist\" without useRovingChoice. Use the helper, or add the file to KEEPS_OWN with the reason it keeps its own arrow keys.");
});

test("the helper allow-list names only files that still keep their own groups", () => {
  for (const [name, reason] of KEEPS_OWN) {
    const file = files.find(item => item.name === name);
    assert.ok(file, `${name} is in KEEPS_OWN but doesn't exist`);
    assert.match(file.text, GROUP, `${name} is in KEEPS_OWN (${reason}) but has no radio group or tab list now`);
    assert.doesNotMatch(file.text, USES_HELPER, `${name} uses useRovingChoice now; take it out of KEEPS_OWN`);
  }
});

test("the guard sees the groups it is meant to catch", () => {
  assert.match('<div role="radiogroup" aria-label="x">', GROUP);
  assert.match("<nav role='tablist'>", GROUP);
  assert.match("<div role={\"tablist\"}>", GROUP);
  assert.doesNotMatch('<div role="group">', GROUP);
  assert.match('import { useRovingChoice } from "../lib/useRovingChoice.js";', USES_HELPER);
  assert.ok(files.length > 50, "the guard reads the frontend's .jsx files");
});
