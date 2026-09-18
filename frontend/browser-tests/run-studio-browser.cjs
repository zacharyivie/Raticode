const { spawn } = require("node:child_process");
const path = require("node:path");
const electronPath = require("electron");
const childEnv = {
  ...process.env,
  ELECTRON_DISABLE_SANDBOX: "1",
  LIBGL_ALWAYS_SOFTWARE: process.env.LIBGL_ALWAYS_SOFTWARE || "1",
};
delete childEnv.ELECTRON_RUN_AS_NODE;

function runBrowserTest(script, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(electronPath, [script], { env: { ...childEnv, ...extraEnv }, stdio: "inherit" });
    child.on("exit", (code, signal) => {
      if (code === 0 && !signal) resolve();
      else reject(new Error(`Browser regression ${path.basename(script)} exited with ${signal || code}.`));
    });
    child.on("error", reject);
  });
}

async function main() {
  await runBrowserTest(path.join(__dirname, "studio.browser.cjs"), { GOFER_EMPTY_WORKSPACE_ONLY: "1" });
  await runBrowserTest(path.join(__dirname, "studio.browser.cjs"));
  await runBrowserTest(path.join(__dirname, "studio.browser.cjs"), { GOFER_TERMINAL_ONLY: "1" });
  await runBrowserTest(path.join(__dirname, "workflow-tabs.browser.cjs"));
  await runBrowserTest(path.join(__dirname, "studio.browser.cjs"), { GOFER_SWARM_ONLY: "1" });
  await runBrowserTest(path.join(__dirname, "../electron/tests/studio-policy.browser.cjs"));
  await runBrowserTest(path.join(__dirname, "../electron/tests/conversation-storage.browser.cjs"));
  await runBrowserTest(path.join(__dirname, "../electron/tests/background-lifecycle.browser.cjs"));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
