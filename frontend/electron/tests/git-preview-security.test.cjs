const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { gitRepositoryAction, readGitFileBaseline, readGitStatus, runGit } = require("../git-status.cjs");
const { scanProject } = require("../project-search.cjs");

async function repository(callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-preview-security-"));
  const root = path.join(directory, "repo");
  fs.mkdirSync(root);
  const git = (...args) => runGit(["-C", root, ...args]);
  const marker = path.join(directory, "converter-ran");
  const converter = path.join(directory, "converter.cjs");
  // Git executes textconv through a shell on each platform. Keep the fixture
  // outside the checkout so it cannot affect the preview's clean-tree checks.
  fs.writeFileSync(converter, `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'ran'); process.stdout.write('hidden changes\\n');`);
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    await git("config", "commit.gpgsign", "false");
    await git("config", "diff.preview.textconv", `${quote(process.execPath.replaceAll("\\", "/"))} ${quote(converter.replaceAll("\\", "/"))}`);
    fs.writeFileSync(path.join(root, ".gitattributes"), "file.txt diff=preview\n");
    fs.writeFileSync(path.join(root, "file.txt"), "original\n");
    await git("add", ".");
    await git("commit", "-m", "Initial");
    await callback({ root, git, marker });
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test("editor line previews use file contents without running text converters", async () => {
  await repository(async ({ root, marker }) => {
    const file = path.join(root, "file.txt");
    fs.writeFileSync(file, "changed\n");
    const result = await readGitFileBaseline(file, { group: "unstaged" });
    assert.equal(fs.existsSync(marker), false, "preview executed a repository text converter");
    assert.equal(result.content, "original\n");
    assert.equal(result.modifiedContent, "changed\n");
    assert.deepEqual(result.hunks, [{ startLine: 1, endLine: 1 }]);
  });
});

test("automatic status refresh ignores executable filesystem monitors", async () => {
  await repository(async ({ root, git, marker }) => {
    const monitor = path.join(path.dirname(root), "monitor.cjs");
    fs.writeFileSync(monitor, `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'ran'); process.stdout.write('token\\0');`);
    const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
    await git("config", "core.fsmonitor", `${quote(process.execPath.replaceAll("\\", "/"))} ${quote(monitor.replaceAll("\\", "/"))}`);
    fs.writeFileSync(path.join(root, "file.txt"), "changed\n");
    const status = await readGitStatus(root);
    assert.equal(fs.existsSync(marker), false, "status refresh executed a repository monitor");
    assert.ok(status.entries.some(entry => entry.path === "file.txt" && entry.unstaged));
    const baseline = await readGitFileBaseline(path.join(root, "file.txt"), { group: "unstaged" });
    assert.equal(fs.existsSync(marker), false, "editor preview executed a repository monitor");
    assert.equal(baseline.modifiedContent, "changed\n");
    const search = await scanProject(root, { query: "changed" });
    assert.equal(fs.existsSync(marker), false, "project search executed a repository monitor");
    assert.equal(search.count, 1);
    await git("-c", "core.fsmonitor=false", "stash", "push", "-m", "Saved change");
    const hash = String(await git("rev-parse", "refs/stash")).trim();
    const preview = await gitRepositoryAction(root, "stash-preview", { hash });
    assert.equal(fs.existsSync(marker), false, "stash preview executed a repository monitor");
    assert.match(preview.diff, /\+changed/);
  });
});

for (const action of ["merge-preview", "rebase-preview"]) {
  test(`${action} shows raw changes without running text converters`, async () => {
    await repository(async ({ root, git, marker }) => {
      await git("switch", "-c", "feature");
      fs.writeFileSync(path.join(root, "file.txt"), "changed\n");
      await git("commit", "-am", "Change");
      await git("switch", "main");
      const head = await git("rev-parse", "HEAD");
      const result = await gitRepositoryAction(root, action, { source: "feature", target: "main" });
      assert.equal(fs.existsSync(marker), false, "preview executed a repository text converter");
      assert.match(result.diff, /\+changed/);
      assert.equal(result.blocked, false);
      assert.equal(await git("rev-parse", "HEAD"), head);
      assert.equal(await git("status", "--porcelain"), "");
    });
  });
}

test("stash preview shows raw changes without running text converters", async () => {
  await repository(async ({ root, git, marker }) => {
    fs.writeFileSync(path.join(root, "file.txt"), "changed\n");
    await git("stash", "push", "-m", "Saved change");
    const hash = String(await git("rev-parse", "refs/stash")).trim();
    const result = await gitRepositoryAction(root, "stash-preview", { hash });
    assert.equal(fs.existsSync(marker), false, "preview executed a repository text converter");
    assert.match(result.diff, /\+changed/);
    assert.equal(result.blocked, false);
    assert.equal(await git("status", "--porcelain"), "");
    assert.equal(String(await git("rev-parse", "refs/stash")).trim(), hash);
  });
});
