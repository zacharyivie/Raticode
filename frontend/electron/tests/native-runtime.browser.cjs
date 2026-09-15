const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs");
const { createRequire } = require("node:module");
const { app } = require("electron");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
let terminal;
const timeout = setTimeout(() => finish(new Error("Native terminal smoke timed out")), 20000);
function finish(error) {
  clearTimeout(timeout);
  try { terminal?.kill(); } catch { /* Already exited. */ }
  if (error) console.error(error);
  else console.log(`Native node-pty spawn, output and exit passed on ${process.platform}/${process.arch}.`);
  app.exit(error ? 1 : 0);
}
app.whenReady().then(async () => {
  const packagedRequire = createRequire(path.join(process.env.RATICODE_SMOKE_ASAR, "package.json"));
  const pty = packagedRequire("node-pty");
  const equal = packagedRequire("lodash.isequal");
  assert.equal(equal, packagedRequire("lodash/isEqual"));
  assert.equal(equal({ size: 0 }, { size: -0 }), true);
  const { DownloadedUpdateHelper } = packagedRequire("electron-updater/out/DownloadedUpdateHelper.js");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-packaged-update-"));
  try {
    const file = path.join(root, "fixture");
    fs.writeFileSync(file, "fixture");
    const helper = new DownloadedUpdateHelper(root);
    const info = { version: "1.2.3" };
    const metadata = { info: { sha512: "fixture" } };
    await helper.setDownloadedFile(file, null, info, metadata, "fixture", false);
    assert.equal(await helper.validateDownloadedPath(file, { ...info }, { info: { ...metadata.info } }, console), file);
    assert.equal(await helper.validateDownloadedPath(file, { version: "1.2.4" }, metadata, console), null);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
  console.log("Packaged updater metadata compatibility passed.");
  // This exercises the rebuilt native module with Electron's actual ABI.
  const windows = process.platform === "win32";
  terminal = pty.spawn(windows ? "cmd.exe" : "/bin/sh", windows
    ? ["/d", "/s", "/c", "echo RATICODE_PTY_OK"]
    : ["-c", "printf RATICODE_PTY_OK"], { cwd: os.tmpdir(), cols: 80, rows: 24 });
  let output = "";
  terminal.onData((data) => { output += data; });
  terminal.onExit(({ exitCode }) => {
    try {
      assert.equal(exitCode, 0);
      assert.match(output, /RATICODE_PTY_OK/);
      finish();
    } catch (error) { finish(error); }
  });
}).catch(finish);
