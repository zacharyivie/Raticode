const { execFile } = require("node:child_process");
const os = require("node:os");
const path = require("node:path");

// Finder and the Dock do not inherit a terminal's shell startup environment.
// Import only PATH, leaving app configuration and credentials untouched.
async function restoreShellPath({ platform = process.platform, env = process.env, home = os.homedir(), run = execFile, loginShell } = {}) {
  if (platform !== "darwin") return;
  let shell = loginShell || env.SHELL;
  if (!shell) {
    try { shell = os.userInfo().shell; } catch { /* Fall back to the macOS default. */ }
  }
  if (!shell || !path.isAbsolute(shell)) shell = "/bin/zsh";
  const shellPath = await new Promise(resolve => {
    run(shell, ["-ilc", "printf '\\0RATICODE_PATH\\0%s\\0' \"$PATH\""], {
      cwd: home, env, encoding: "utf8", timeout: 5000, killSignal: "SIGKILL", maxBuffer: 1024 * 1024,
    }, (error, stdout) => {
      const match = !error && String(stdout).match(/\0RATICODE_PATH\0([^\0]+)\0/);
      resolve(match ? match[1] : "");
    });
  });
  const fallbacks = [path.join(home, ".local/bin"), path.join(home, ".cursor/bin"), path.join(home, ".bun/bin"), "/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"];
  env.PATH = [...new Set([...shellPath.split(":"), ...(env.PATH || "").split(":"), ...fallbacks].filter(entry => path.isAbsolute(entry)))].join(":");
}

module.exports = { restoreShellPath };
