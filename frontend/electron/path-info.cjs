const fs = require("node:fs");
const path = require("node:path");

async function inspectPath(targetPath) {
  try {
    const stat = await fs.promises.stat(targetPath);
    return pathInfoFromStat(targetPath, stat);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return {
      basename: path.basename(targetPath),
      exists: false,
      extension: path.extname(targetPath),
      isDirectory: false,
      isFile: false,
      path: targetPath,
    };
  }
}

function pathInfoFromStat(targetPath, stat) {
  return {
    basename: path.basename(targetPath),
    exists: true,
    extension: path.extname(targetPath),
    isDirectory: stat.isDirectory(),
    isFile: stat.isFile(),
    path: targetPath,
  };
}

// Recent history spans projects and standalone files. Inspect metadata only;
// this does not create path grants or read file contents.
async function missingRecentFiles(paths, stat = fs.promises.stat) {
  if (!Array.isArray(paths)) return [];
  const candidates = [...new Set(paths.filter(item => typeof item === "string" && path.isAbsolute(item)))].slice(0, 8);
  const missing = await Promise.all(candidates.map(async target => {
    try {
      return (await stat(target)).isFile() ? null : target;
    } catch (error) {
      return ["ENOENT", "ENOTDIR"].includes(error?.code) ? target : null;
    }
  }));
  return missing.filter(Boolean);
}

module.exports = { inspectPath, pathInfoFromStat, missingRecentFiles };

// Like recent-file cleanup, only inspect existence. No content access or grants.
async function missingThreadRoots(paths, stat = fs.promises.stat) {
  if (!Array.isArray(paths)) return [];
  const roots = [...new Set(paths.filter(item => typeof item === "string" && path.isAbsolute(item)))].slice(0, 100);
  const missing = await Promise.all(roots.map(async root => {
    try { return (await stat(root)).isDirectory() ? null : root; }
    catch (error) { return ["ENOENT", "ENOTDIR"].includes(error?.code) ? root : null; }
  }));
  return missing.filter(Boolean);
}
module.exports.missingThreadRoots = missingThreadRoots;
