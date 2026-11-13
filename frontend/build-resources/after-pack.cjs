// electron-builder afterPack hook: no packaged file may be writable by other accounts.
//
// Electron's release zip stores its files as 0777 and 0666 (the executable,
// libffmpeg.so, chrome-sandbox, the .pak files, the locales), and those modes survive
// extraction whatever the umask is. electron-builder copies them as they are, so the
// 0.5.3 .deb and .rpm installed /opt/Raticode/gofer-flow, libffmpeg.so and the rest of
// Electron's files writable by every account on the computer. This hook runs after the
// app folder is assembled and before any package is built from it, and on macOS before
// signing. Windows has no POSIX modes, so it is skipped there.
const fs = require("node:fs/promises");
const path = require("node:path");

const OTHERS_WRITE = 0o022;

// Removes group and world write bits below root. Symlinks are left alone: their
// own mode is never used. Returns the paths it changed, relative to root.
async function stripGroupAndWorldWrite(root) {
  const changed = [];
  async function visit(target) {
    const stats = await fs.lstat(target);
    if (stats.isSymbolicLink()) return;
    if (stats.mode & OTHERS_WRITE) {
      await fs.chmod(target, stats.mode & 0o7777 & ~OTHERS_WRITE);
      changed.push(path.relative(root, target) || ".");
    }
    if (stats.isDirectory()) {
      for (const entry of await fs.readdir(target)) await visit(path.join(target, entry));
    }
  }
  await visit(root);
  return changed;
}

async function afterPack(context) {
  if (context.electronPlatformName === "win32") return;
  const changed = await stripGroupAndWorldWrite(context.appOutDir);
  console.log(`afterPack: removed group and world write access from ${changed.length} packaged paths`);
}

module.exports = afterPack;
module.exports.default = afterPack;
module.exports.stripGroupAndWorldWrite = stripGroupAndWorldWrite;
