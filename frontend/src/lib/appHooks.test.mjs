import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { parse } from "@babel/parser";

// App() keeps settings, updates, devices and browser tabs in their own hooks, so the
// component doesn't grow back past the size it had before they moved out.
const APP_MAX_LINES = 4351;
const HOOKS = {
  useAppSettings: "useAppSettings.js",
  useAppUpdates: "useAppUpdates.js",
  useDevicesAndDictation: "useDevicesAndDictation.js",
  useBrowserTabs: "useBrowserTabs.js",
};
const MOVED = [
  /const checkForUpdates\b/,
  /function applyUpdate\b/,
  /function handleTextZoomKeydown\b/,
  /function openIntegratedBrowser\b/,
  /function closeBrowserTabs\b/,
  /function openExternalItem\b/,
  /deviceSyncContext = useRef/,
];

const appSource = fs.readFileSync(new URL("../pages/App.jsx", import.meta.url), "utf8");
const program = parse(appSource, { sourceType: "module", plugins: ["jsx"] }).program;

test("App() stays at most 4,351 lines", () => {
  const app = program.body.find((node) => node.type === "ExportDefaultDeclaration" && node.declaration.id?.name === "App");
  assert.ok(app, "App.jsx has no export default function App");
  const lines = app.declaration.loc.end.line - app.declaration.loc.start.line + 1;
  assert.ok(lines <= APP_MAX_LINES, `App() is ${lines} lines, over ${APP_MAX_LINES}`);
});

test("App() gets settings, updates, devices and browser tabs from their hook files", () => {
  for (const [hook, file] of Object.entries(HOOKS)) {
    const imported = program.body.some((node) => node.type === "ImportDeclaration"
      && node.source.value === `../lib/${file}`
      && node.specifiers.some((specifier) => specifier.imported?.name === hook));
    assert.ok(imported, `App.jsx doesn't import ${hook} from ../lib/${file}`);
    assert.equal(appSource.split(`${hook}(`).length - 1, 1, `App.jsx should call ${hook} exactly once`);
    const hookSource = fs.readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    assert.match(hookSource, new RegExp(`^export function ${hook}\\(`, "m"), `${file} doesn't export ${hook}`);
    assert.doesNotMatch(hookSource, /from "\.\.\/pages\/App\.jsx"/, `${file} must not import App.jsx`);
  }
});

test("code that moved into the hooks isn't defined in App.jsx again", () => {
  for (const pattern of MOVED) assert.doesNotMatch(appSource, pattern, `App.jsx defines ${pattern.source} again`);
});
