// Vite's default cache is frontend/node_modules/.vite. Worktrees link node_modules
// to the main checkout, so a test's dev server would rewrite the optimized
// dependencies of the developer's running app. Give each test its own cache and
// remove it when the process exits (/tmp is often RAM-backed).
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function testViteCacheDir(label = "vite") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `raticode-test-${label}-`));
  process.once("exit", () => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

module.exports = { testViteCacheDir };
