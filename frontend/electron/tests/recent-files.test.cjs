const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { missingRecentFiles } = require("../path-info.cjs");

test("recent files prune deleted files, stale extensions, directories and invalid parents", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "recent-files-"));
  try {
    const legacy = path.join(root, "workflow.rad");
    const current = path.join(root, "workflow.rattish");
    const other = path.join(root, "other.md");
    await fs.writeFile(legacy, "Rattish: 1");
    await fs.rename(legacy, current);
    await fs.writeFile(other, "keep");
    const invalid = path.join(current, "child.md");
    assert.deepEqual(await missingRecentFiles([legacy, current, other, root, invalid]), [legacy, root, invalid]);
    await fs.unlink(other);
    assert.deepEqual(await missingRecentFiles([current, other]), [other]);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("recent history survives permission and transient failures", async () => {
  for (const code of ["EACCES", "EPERM", "EIO"]) {
    assert.deepEqual(await missingRecentFiles([path.resolve("unreadable.md")], async () => {
      throw Object.assign(new Error("Cannot inspect"), { code });
    }), []);
  }
});

test("thread workspace cleanup distinguishes deleted directories from access errors", async () => {
  const { missingThreadRoots } = require("../path-info.cjs");
  const root = path.resolve("thread-scopes");
  const roots = ["live", "missing", "denied", "file"].map(name => path.join(root, name));
  const missing = await missingThreadRoots(roots, async target => {
    if (target.endsWith("missing")) throw Object.assign(new Error(), { code: "ENOENT" });
    if (target.endsWith("denied")) throw Object.assign(new Error(), { code: "EACCES" });
    return { isDirectory: () => !target.endsWith("file") };
  });
  assert.deepEqual(missing, [roots[1], roots[3]]);
});
