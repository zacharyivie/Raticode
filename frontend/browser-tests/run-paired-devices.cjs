const { spawnSync } = require("node:child_process");
const path = require("node:path");
const env = { ...process.env, ELECTRON_DISABLE_SANDBOX: "1", LIBGL_ALWAYS_SOFTWARE: "1" };
delete env.ELECTRON_RUN_AS_NODE;
const result = spawnSync(require("electron"), [path.join(__dirname, "paired-devices.browser.cjs")], { env, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
