const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { backgroundLaunch } = require("../electron/tests/background-display.cjs");
const env = { ...process.env, ELECTRON_DISABLE_SANDBOX: "1", LIBGL_ALWAYS_SOFTWARE: "1" };
delete env.ELECTRON_RUN_AS_NODE;
const launch = backgroundLaunch(require("electron"), [path.join(__dirname, "organizations-home.browser.cjs")], env);
const result = spawnSync(launch.command, launch.args, { env: launch.env, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
