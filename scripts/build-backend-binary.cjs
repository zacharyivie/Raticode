const { spawnSync } = require("node:child_process");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const env = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(repoRoot, ".uv-cache"),
};

const uvCommand = process.platform === "win32" ? "uv.exe" : "uv";
const result = spawnSync(
  uvCommand,
  ["run", "--locked", "--extra", "xlsx", "--extra", "devices", "pyinstaller", "--clean", "--noconfirm", "gof.spec"],
  {
    cwd: repoRoot,
    env,
    stdio: "inherit",
  },
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
