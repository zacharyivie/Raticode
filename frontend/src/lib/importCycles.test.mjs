import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

// Two modules that import each other load in an order that depends on which one a page
// reaches first, so one of them sees the other's exports as undefined. This walks the
// static relative imports under frontend/src and fails on any cycle. Dynamic import()
// calls are left out: they run after both modules have loaded.

const SRC = path.resolve(import.meta.dirname, "..");
const CODE = /\.(?:jsx?|mjs|cjs)$/;
const IMPORT_FROM = /^\s*import\s+(?:type\s+)?[\w$*{}\s,]+?\s+from\s*["']([^"']+)["']/gm;
const BARE_IMPORT = /^\s*import\s*["']([^"']+)["']/gm;
const EXPORT_FROM = /^\s*export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s*from\s*["']([^"']+)["']/gm;

function codeFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "node_modules" ? [] : codeFiles(full);
    return CODE.test(entry.name) ? [full] : [];
  });
}

function resolveImport(fromFile, specifier) {
  if (!specifier.startsWith("./") && !specifier.startsWith("../")) return null;
  const target = path.resolve(path.dirname(fromFile), specifier.replace(/[?#].*$/, ""));
  for (const candidate of [target, `${target}.js`, `${target}.jsx`, path.join(target, "index.js"), path.join(target, "index.jsx")]) {
    if (CODE.test(candidate) && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function staticImports(file) {
  const source = fs.readFileSync(file, "utf8");
  const found = new Set();
  for (const pattern of [IMPORT_FROM, BARE_IMPORT, EXPORT_FROM]) {
    for (const match of source.matchAll(pattern)) {
      const target = resolveImport(file, match[1]);
      if (target) found.add(target);
    }
  }
  return [...found];
}

// Each cycle as the list of files it passes through, relative to root, first file repeated last.
function findImportCycles(root) {
  const graph = new Map(codeFiles(root).map(file => [file, staticImports(file)]));
  const state = new Map();
  const stack = [];
  const cycles = [];
  function visit(file) {
    state.set(file, "open");
    stack.push(file);
    for (const next of graph.get(file) ?? []) {
      if (state.get(next) === "open") {
        cycles.push([...stack.slice(stack.indexOf(next)), next].map(name => path.relative(root, name).split(path.sep).join("/")));
      } else if (!state.has(next)) {
        visit(next);
      }
    }
    stack.pop();
    state.set(file, "done");
  }
  for (const file of [...graph.keys()].sort()) if (!state.has(file)) visit(file);
  return cycles;
}

function staticClosure(file) {
  const seen = new Set();
  const pending = [file];
  while (pending.length) {
    const next = pending.pop();
    if (seen.has(next)) continue;
    seen.add(next);
    pending.push(...staticImports(next));
  }
  return seen;
}

test("the import cycle guard finds a planted two-file cycle", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "import-cycles-"));
  try {
    fs.writeFileSync(path.join(root, "a.jsx"), 'import { b } from "./b.js";\nexport const a = () => b;\n');
    fs.writeFileSync(path.join(root, "b.js"), 'export { a as b } from "./a.jsx";\n');
    fs.writeFileSync(path.join(root, "c.js"), 'import "./a.jsx";\nexport const c = () => import("./c.js");\n');
    assert.deepEqual(findImportCycles(root), [["a.jsx", "b.js", "a.jsx"]]);
    fs.writeFileSync(path.join(root, "b.js"), 'export const b = 1;\n');
    assert.deepEqual(findImportCycles(root), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("frontend modules import each other without cycles", () => {
  assert.deepEqual(findImportCycles(SRC), [], "Move what both files need into a third module that neither of them imports.");
});

test("the bottom panel's run timeline loads without the graph canvas", () => {
  const timeline = path.join(SRC, "components/RunTimelinePanel.jsx");
  const canvas = path.join(SRC, "components/DagCanvas.jsx");
  assert.equal(staticClosure(timeline).has(canvas), false);
  const panel = fs.readFileSync(path.join(SRC, "components/UnifiedBottomPanel.jsx"), "utf8");
  assert.match(panel, /lazy\(\(\) => import\("\.\/RunTimelinePanel\.jsx"\)/);
  assert.doesNotMatch(panel, /["']\.\/DagCanvas(?:\.jsx)?["']/);
});
