const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { readGitBranches, readGitStatus, switchGitBranch, gitRepositoryAction, runGit } = require("../git-status.cjs");

test("branch lists and integrations preserve names that also identify tags", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-branch-names-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    await git("config", "commit.gpgsign", "false");
    await git("commit", "--allow-empty", "-m", "Initial");
    await git("branch", "release");
    await git("tag", "release");
    await git("tag", "main");
    assert.deepEqual((await readGitBranches(root)).branches, ["main", "release"]);
    assert.deepEqual((await readGitStatus(root)).branches, ["main", "release"]);
    assert.equal((await switchGitBranch(root, "release")).branch, "release");
    const preview = await gitRepositoryAction(root, "merge-preview", { source: "release", target: "main" });
    assert.equal(preview.blocked, false);
    assert.equal(await git("status", "--porcelain"), "");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("publishing a branch with a matching tag pushes only the branch", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-publish-branch-"));
  const root = path.join(directory, "repo");
  const remote = path.join(directory, "remote.git");
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await runGit(["init", "-b", "release", root]);
    await runGit(["init", "--bare", remote]);
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    await git("config", "commit.gpgsign", "false");
    await git("commit", "--allow-empty", "-m", "Initial");
    await git("tag", "release");
    await git("remote", "add", "origin", remote);
    const result = await gitRepositoryAction(root, "publish", "origin");
    assert.equal(result.branch, "release");
    assert.equal((await git("rev-parse", "--symbolic-full-name", "@{upstream}")).trim(), "refs/remotes/origin/release");
    const refs = await runGit(["-C", remote, "for-each-ref", "--format=%(refname)"]);
    assert.equal(String(refs).trim(), "refs/heads/release");
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
