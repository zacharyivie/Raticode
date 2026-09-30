const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { gitRepositoryAction, parseGitWorktrees, readGitWorktrees, runGit } = require("../git-status.cjs");

test("worktree records preserve newlines and metadata-like text inside paths and lock reasons", () => {
  const target = "/repo/feature\nworktree decoy";
  const parsed = parseGitWorktrees(`worktree ${target}\0HEAD abc\0branch refs/heads/feature\0locked reason\nworktree fake\0\0worktree /repo/main\0HEAD def\0branch refs/heads/main\0\0`);
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].path, target);
  assert.equal(parsed[0].branch, "feature");
  assert.equal(parsed[0].locked, true);
  assert.equal(parsed[1].path, "/repo/main");
});

for (const name of ["feature with spaces", "feature\nworktree decoy"]) {
  test(`worktree listing and integration preserve ${JSON.stringify(name)}`, {
    skip: process.platform === "win32" && name.includes("\n"),
  }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-worktree-paths-"));
    const root = path.join(directory, "repo");
    const target = path.join(directory, name);
    fs.mkdirSync(root);
    const git = (...args) => runGit(["-C", root, ...args]);
    try {
      await git("init", "-b", "main");
      await git("config", "user.name", "Test");
      await git("config", "user.email", "test@example.invalid");
      await git("config", "commit.gpgsign", "false");
      await git("commit", "--allow-empty", "-m", "Initial");
      await git("worktree", "add", "-b", "feature", target);
      const result = await readGitWorktrees(root);
      assert.equal(result.worktrees.find(item => item.branch === "feature")?.path, target);
      const authorized = [];
      const preview = await gitRepositoryAction(root, "merge-preview", {
        source: "main", target: "feature",
      }, { authorizeTarget: async value => { authorized.push(value); } });
      assert.equal(preview.destinationRoot, target);
      assert.equal(preview.blocked, false);
      assert.ok(authorized.includes(target));
      assert.equal(await git("status", "--porcelain"), "");
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}
