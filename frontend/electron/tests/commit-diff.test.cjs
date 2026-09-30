const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { runGit, gitRepositoryAction } = require("../git-status.cjs");

test("commit diff reads historical changes, root commits and first-parent merges without changing local files", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-commit-diff-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  const commit = async message => {
    await git("add", ".");
    await git("commit", "-m", message);
    return String(await git("rev-parse", "HEAD")).trim();
  };
  const diff = hash => gitRepositoryAction(root, "commit-diff", { hash });
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "original.txt"), "original\n");
    const initial = await commit("Initial commit");
    const initialDiff = await diff(initial);
    assert.equal(initialDiff.parentHash, "");
    assert.deepEqual(initialDiff.files.map(file => [file.path, file.status, file.original, file.modified]), [["original.txt", "A", "", "original\n"]]);

    await git("mv", "original.txt", "renamed.txt");
    fs.writeFileSync(path.join(root, "binary.bin"), Buffer.from([0, 1, 2]));
    const renamed = await commit("Rename and binary");
    const renameDiff = await diff(renamed);
    assert.equal(renameDiff.parentHash, initial);
    assert.deepEqual(renameDiff.files.find(file => file.status === "R"), { oldPath: "original.txt", path: "renamed.txt", status: "R", oldMode: "100644", newMode: "100644", binary: false, submodule: false, original: "original\n", modified: "original\n" });
    assert.equal(renameDiff.files.find(file => file.path === "binary.bin").binary, true);
    assert.equal(renameDiff.files.find(file => file.path === "binary.bin").modified, null);
    fs.unlinkSync(path.join(root, "renamed.txt"));
    const deleted = await commit("Delete file");
    assert.deepEqual((await diff(deleted)).files.map(file => [file.status, file.original, file.modified]), [["D", "original\n", ""]]);
    await git("commit", "--allow-empty", "-m", "Empty commit");
    assert.deepEqual((await diff(String(await git("rev-parse", "HEAD")).trim())).files, []);

    await git("switch", "-c", "feature");
    fs.writeFileSync(path.join(root, "feature.txt"), "feature change\n");
    await commit("Feature change");
    await git("switch", "main");
    fs.writeFileSync(path.join(root, "main.txt"), "main change\n");
    const parent = await commit("Main change");
    await git("merge", "--no-ff", "feature", "-m", "Merge feature");
    const merged = String(await git("rev-parse", "HEAD")).trim();
    fs.writeFileSync(path.join(root, "main.txt"), "staged change\n");
    await git("add", "main.txt");
    fs.writeFileSync(path.join(root, "main.txt"), "unstaged change\n");
    const status = await git("status", "--porcelain=v1");
    const index = await git("write-tree");
    const mergeDiff = await diff(merged);
    assert.equal(mergeDiff.parentHash, parent);
    assert.equal(mergeDiff.subject, "Merge feature");
    assert.deepEqual(mergeDiff.files.map(file => [file.path, file.original, file.modified]), [["feature.txt", "", "feature change\n"]]);
    assert.deepEqual((await diff(renamed)).files, renameDiff.files);
    assert.equal(await git("status", "--porcelain=v1"), status);
    assert.equal(await git("write-tree"), index);
    assert.equal(String(await git("rev-parse", "HEAD")).trim(), merged);
    assert.equal(fs.readFileSync(path.join(root, "main.txt"), "utf8"), "unstaged change\n");
    await assert.rejects(diff("f".repeat(40)));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("commit diff rejects invalid revisions and reports oversized output", async () => {
  let calls = 0;
  const runGit = async args => {
    calls += 1;
    if (args.includes("--no-patch")) return `${"a".repeat(40)}\0\0Large commit\n`;
    throw Object.assign(new Error("maxBuffer exceeded"), { code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" });
  };
  for (const hash of ["HEAD", "--all", "abc", "a".repeat(40) + "^", null]) {
    await assert.rejects(gitRepositoryAction("/repo", "commit-diff", { hash }, { runGit }), /valid commit/);
  }
  assert.equal(calls, 0);
  await assert.rejects(gitRepositoryAction("/repo", "commit-diff", { hash: "a".repeat(40) }, { runGit }), /too large to display/);
});

test("commit diffs include complete blobs, exact unusual paths, whitespace and mode-only changes", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-commit-blobs-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  const name = "café\tline\nname.py";
  const original = Array.from({ length: 80 }, (_, i) => `value_${i} = ${i}\n`).join("");
  const modified = original.replace("value_40 = 40", "value_40 =  41");
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, name), original);
    fs.writeFileSync(path.join(root, "script.sh"), "#!/bin/sh\necho hello\n");
    await git("add", ".");
    await git("commit", "-m", "Initial files");
    const parent = String(await git("rev-parse", "HEAD")).trim();
    fs.writeFileSync(path.join(root, name), modified);
    await git("add", ".");
    await git("update-index", "--chmod=+x", "script.sh");
    await git("update-index", "--add", "--cacheinfo", `160000,${parent},dependency`);
    await git("commit", "-m", "Change text, mode and submodule");
    const hash = String(await git("rev-parse", "HEAD")).trim();
    const { files } = await gitRepositoryAction(root, "commit-diff", { hash });
    const changed = files.find(file => file.path === name);
    assert.equal(changed.original, original);
    assert.equal(changed.modified, modified);
    assert.equal(changed.oldPath, name);
    const script = files.find(file => file.path === "script.sh");
    assert.equal(script.oldMode, "100644");
    assert.equal(script.newMode, "100755");
    assert.equal(script.original, script.modified);
    const submodule = files.find(file => file.path === "dependency");
    assert.equal(submodule.submodule, true);
    assert.equal(submodule.original, "");
    assert.equal(submodule.modified, `Subproject commit ${parent}\n`);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
