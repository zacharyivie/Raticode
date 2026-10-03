const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function createTrustedProjectStore(file) {
  function read() {
    try {
      const stored = JSON.parse(fs.readFileSync(file, "utf8"));
      return {
        roots: (Array.isArray(stored) ? stored : stored.roots || [])
          .filter((root) => typeof root === "string" && path.isAbsolute(root)),
        legacyRecentProjectsMigrated: stored.legacyRecentProjectsMigrated === true,
      };
    } catch (error) {
      if (error.code === "ENOENT") return { roots: [], legacyRecentProjectsMigrated: false };
      // Corrupt or unreadable authority must never reopen migration.
      throw error;
    }
  }

  function write(record) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.${crypto.randomUUID()}.tmp`;
    try {
      const descriptor = fs.openSync(temporary, "wx", 0o600);
      try {
        fs.writeFileSync(descriptor, JSON.stringify(record));
        fs.fsyncSync(descriptor);
      } finally { fs.closeSync(descriptor); }
      fs.renameSync(temporary, file);
      if (process.platform !== "win32") {
        const directory = fs.openSync(path.dirname(file), "r");
        try { fs.fsyncSync(directory); }
        finally { fs.closeSync(directory); }
      }
    } finally {
      fs.rmSync(temporary, { force: true });
    }
  }

  function add(roots) {
    const record = read();
    const nextRoots = [...new Set([...record.roots, ...roots])];
    // Background grant renewal must not rewrite and fsync unchanged trust data.
    if (nextRoots.length === record.roots.length && nextRoots.every((root, index) => root === record.roots[index])) return;
    write({ ...record, roots: nextRoots });
  }

  function migrate(recentProjects) {
    const record = read();
    if (record.legacyRecentProjectsMigrated) return [];
    let parsed;
    try { parsed = JSON.parse(recentProjects ?? "[]"); }
    catch { parsed = []; }
    const roots = Array.isArray(parsed)
      ? parsed.filter((root) => typeof root === "string" && path.isAbsolute(root) && !root.includes("\0"))
      : [];
    // Publish authority and completion together. Missing/offline folders retain
    // their record; security.cjs checks real filesystem containment on renewal.
    write({ roots: [...new Set([...record.roots, ...roots])], legacyRecentProjectsMigrated: true });
    return roots;
  }

  return { read, add, migrate };
}

module.exports = { createTrustedProjectStore };
