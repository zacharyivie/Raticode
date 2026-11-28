// Every Vite dev server a test starts must load its config without writing into
// node_modules. With neither option, Vite bundles vite.config.js into
// node_modules/.vite-temp first, which fails with EROFS when node_modules is read-only
// (as it is for the reviewer's sandbox). configLoader: "runner" loads the same config,
// with the same plugins, in memory; configFile: false skips the config file entirely.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = path.join(__dirname, "..", "..");
const DIRS = ["src", "electron", "browser-tests"];
const SOURCE = /\.(?:c|m)?jsx?$|\.tsx?$/;
// Vite's createServer bound by an import, a dynamic import or a require, or called on
// a Vite namespace.
const USES_VITE_CREATE_SERVER = [
  /import\s*\{[^}]*\bcreateServer\b[^}]*\}\s*from\s*["']vite["']/,
  /\{[^}]*\bcreateServer\b[^}]*\}\s*=\s*(?:await\s+)?(?:import|require)\(\s*["']vite["']\s*\)/,
  /\bvite\.createServer\s*\(|import\(\s*["']vite["']\s*\)\s*\)\.createServer/,
];
const usesViteCreateServer = (source) => USES_VITE_CREATE_SERVER.some((pattern) => pattern.test(source));
// A bare createServer( call, or one on a Vite namespace. http.createServer and
// net.createServer have a different object before the dot and are left alone.
const VITE_CALL = /(?:(?<![\w$.])|(?:\bvite|import\(\s*["']vite["']\s*\)\s*\))\.)createServer\s*\(/g;
const SAFE_OPTION = /^(?:configFile\s*:\s*false|configLoader\s*:\s*["']runner["'])(?![\w$])/;

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) return [];
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    // This file's own patterns and fixtures name createServer too.
    return SOURCE.test(entry.name) && full !== __filename ? [full] : [];
  });
}

// Scans the argument list that starts at `start` (just after "createServer(") and
// returns whether its options object sets a safe option as a top-level key. Strings, template literals and comments are
// skipped so braces inside them don't count.
function setsSafeOption(source, start) {
  let depth = 0; // ( [ { nesting inside the argument list
  let objectDepth = -1; // depth of the options object once it opens
  let safe = false;
  const templates = []; // brace depth at each open ${ inside a template literal
  for (let i = start; i < source.length; i += 1) {
    const ch = source[i];
    const next = source[i + 1];
    if (ch === "/" && next === "/") { i = source.indexOf("\n", i); if (i < 0) break; continue; }
    if (ch === "/" && next === "*") { i = source.indexOf("*/", i + 2) + 1; if (i <= 0) break; continue; }
    if (ch === "\"" || ch === "'") {
      for (i += 1; i < source.length && source[i] !== ch; i += 1) if (source[i] === "\\") i += 1;
      continue;
    }
    if (ch === "`") {
      for (i += 1; i < source.length && source[i] !== "`"; i += 1) {
        if (source[i] === "\\") i += 1;
        else if (source[i] === "$" && source[i + 1] === "{") { templates.push(depth); depth += 1; i += 1; break; }
      }
      continue;
    }
    if (ch === "}" && templates.length && templates.at(-1) === depth - 1) {
      // End of a ${...}: resume the template literal.
      templates.pop();
      depth -= 1;
      for (i += 1; i < source.length && source[i] !== "`"; i += 1) {
        if (source[i] === "\\") i += 1;
        else if (source[i] === "$" && source[i + 1] === "{") { templates.push(depth); depth += 1; i += 1; break; }
      }
      continue;
    }
    if (ch === "(" || ch === "[" || ch === "{") {
      if (ch === "{" && objectDepth < 0 && depth === 0) objectDepth = depth + 1;
      depth += 1;
      continue;
    }
    if (ch === ")" || ch === "]" || ch === "}") {
      if (depth === 0) return safe;
      depth -= 1;
      continue;
    }
    if (depth === objectDepth && /[A-Za-z]/.test(ch) && !/[\w$]/.test(source[i - 1]) && SAFE_OPTION.test(source.slice(i, i + 40))) safe = true;
  }
  return safe;
}

function unsafeViteServers(files) {
  const found = [];
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    if (!usesViteCreateServer(source)) continue;
    for (const match of source.matchAll(VITE_CALL)) {
      // A function declared with that name is not a call.
      if (/\bfunction\s+$/.test(source.slice(Math.max(0, match.index - 20), match.index))) continue;
      if (!setsSafeOption(source, match.index + match[0].length)) {
        const line = source.slice(0, match.index).split("\n").length;
        found.push(`${path.relative(FRONTEND, file).split(path.sep).join("/")}:${line}`);
      }
    }
  }
  return found.sort();
}

test("every Vite test server loads its config without writing into node_modules", () => {
  const files = DIRS.flatMap((dir) => sourceFiles(path.join(FRONTEND, dir)));
  const servers = files.filter((file) => usesViteCreateServer(fs.readFileSync(file, "utf8")));
  assert.ok(servers.length >= 25, `found only ${servers.length} files that start a Vite server`);
  assert.deepEqual(
    unsafeViteServers(files),
    [],
    "These Vite createServer calls pass neither configFile: false nor configLoader: \"runner\", so Vite writes a bundled vite.config.js into node_modules/.vite-temp (EROFS when node_modules is read-only). Add configLoader: \"runner\" to keep vite.config.js, or configFile: false to skip it.",
  );
});

test("the Vite server guard tells Vite's createServer from node's", () => {
  const dir = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "vite-guard-"));
  try {
    // The fixtures say VITE for the module name so the cache-dir guard in
    // background-display.test.cjs doesn't take this file for a Vite test server.
    const write = (name, body) => { fs.writeFileSync(path.join(dir, name), body.replaceAll("VITE", "vite")); return path.join(dir, name); };
    const files = [
      write("bad.cjs", "const http = require('node:http');\nhttp.createServer(() => {});\nasync function f() {\n  const { createServer } = await import('VITE');\n  await createServer({ root: '.', plugins: [{ name: 'x', transform(code) { return `${code}}{${'('}` + \"configLoader: 'runner'\"; } }] });\n}\n"),
      write("nested.mjs", "import { createServer } from 'VITE';\nawait createServer({ server: { configFile: false } });\n"),
      write("good.mjs", "import { createServer } from 'VITE';\nawait createServer({\n  // configFile: true\n  configLoader: \"runner\",\n});\nawait createServer({ root: '/', configFile: false });\n"),
      write("node.cjs", "const { createServer } = require('node:http');\ncreateServer(() => {});\n"),
    ];
    assert.deepEqual(unsafeViteServers(files).map((entry) => entry.replace(/^.*\//, "")), ["bad.cjs:5", "nested.mjs:2"]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
