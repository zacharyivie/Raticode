// Screens ask in Raticode's own ConfirmDialog, never the browser's native confirm,
// alert or prompt box. eslint.config.js bans those in app code; tests may stub them.
const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");

const frontend = path.join(__dirname, "..", "..");

async function lint(code, file) {
  const { ESLint } = await import("eslint");
  const eslint = new ESLint({ cwd: frontend });
  const [result] = await eslint.lintText(code, { filePath: path.join(frontend, file) });
  return result.messages.map((message) => `${message.ruleId}: ${message.message}`);
}

const PLANTED = [
  "window.confirm(\"Delete it?\")",
  "window.alert(\"Done\")",
  "window.prompt(\"Name?\")",
  "globalThis.confirm(\"Delete it?\")",
  "confirm(\"Delete it?\")",
  "alert(\"Done\")",
  "prompt(\"Name?\")",
];

for (const call of PLANTED) {
  test(`lint rejects ${call} in a component`, async () => {
    const messages = await lint(`export function ask() { return ${call}; }\n`, "src/components/LintProbe.jsx");
    assert.ok(messages.some((message) => /ConfirmDialog/.test(message)), messages.join("\n") || "no lint messages");
  });
}

test("lint rejects window.confirm in a library file", async () => {
  const messages = await lint("export const ask = () => window.confirm(\"Delete it?\");\n", "src/lib/lintProbe.js");
  assert.ok(messages.some((message) => /^no-restricted-properties: .*ConfirmDialog/.test(message)), messages.join("\n") || "no lint messages");
});

test("a local confirm from useConfirm is allowed", async () => {
  const messages = await lint(`import { useConfirm } from "./ConfirmDialog.jsx";
export function Probe() {
  const [confirm, confirmDialog] = useConfirm();
  return <div><button type="button" onClick={async () => { if (await confirm({ message: "Delete it?", confirmLabel: "Delete", danger: true })) void 0; }}>Delete</button>{confirmDialog}</div>;
}
`, "src/components/LintProbe.jsx");
  assert.deepEqual(messages, []);
});

test("test files may still stub window.confirm", async () => {
  const messages = await lint("window.confirm = () => true;\nwindow.alert = () => {};\n", "src/pages/LintProbe.test.js");
  assert.deepEqual(messages, []);
});
