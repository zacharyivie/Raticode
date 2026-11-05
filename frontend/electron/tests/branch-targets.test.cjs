const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { gitRepositoryAction, readGitStatus, runGit } = require("../git-status.cjs");

// An isolated clone of a local "origin" with no user or system Git config, so the
// developer's own kept-branch settings never change what is recommended.
async function clonedRepository(callback) {
  const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "raticode-branch-targets-")));
  const saved = { global: process.env.GIT_CONFIG_GLOBAL, nosystem: process.env.GIT_CONFIG_NOSYSTEM };
  process.env.GIT_CONFIG_GLOBAL = path.join(directory, "empty-gitconfig");
  process.env.GIT_CONFIG_NOSYSTEM = "1";
  fs.writeFileSync(process.env.GIT_CONFIG_GLOBAL, "");
  const origin = path.join(directory, "origin");
  const root = path.join(directory, "clone");
  const configure = async repo => {
    for (const [key, value] of [["user.name", "Test"], ["user.email", "test@example.invalid"], ["commit.gpgsign", "false"]]) await runGit(["-C", repo, "config", key, value]);
  };
  try {
    await runGit(["init", "-b", "main", origin]);
    await configure(origin);
    const upstream = (...args) => runGit(["-C", origin, ...args]);
    fs.writeFileSync(path.join(origin, "base.txt"), "base\n");
    await upstream("add", "."); await upstream("commit", "-m", "Base");
    for (const name of ["dev", "Zed", "aa-remote", "release/2", "release/10"]) await upstream("branch", name);
    await runGit(["clone", "--quiet", origin, root]);
    await configure(root);
    const git = (...args) => runGit(["-C", root, ...args]);
    await git("switch", "--quiet", "-c", "topic", "--track", "origin/dev");
    fs.writeFileSync(path.join(root, "topic.txt"), "topic\n");
    await git("add", "."); await git("commit", "-m", "Topic work");
    await git("switch", "--quiet", "main");
    for (const name of ["zeta", "Alpha", "beta"]) await git("branch", name);
    await callback({ root, origin, git, upstream });
  } finally {
    for (const [key, value] of [["GIT_CONFIG_GLOBAL", saved.global], ["GIT_CONFIG_NOSYSTEM", saved.nosystem]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test("branch targets list local branches then remote-tracking branches, alphabetized, with recommendations first", async () => {
  await clonedRepository(async ({ root, git }) => {
    await git("config", "--add", "raticode.cleanupKeep", "beta");
    const targets = await gitRepositoryAction(root, "integration-targets", { source: "topic" });
    assert.deepEqual(targets.local, ["Alpha", "beta", "main", "zeta"], "local branches, alphabetized ignoring case, without the source");
    assert.deepEqual(targets.remote, ["origin/aa-remote", "origin/dev", "origin/main", "origin/release/2", "origin/release/10", "origin/Zed"],
      "remote-tracking branches, alphabetized with numbers in order, without origin/HEAD");
    assert.deepEqual(targets.recommended, [
      { name: "main", type: "local" }, // checked out
      { name: "origin/dev", type: "remote" }, // topic's upstream
      { name: "beta", type: "local" }, // always kept by branch cleanup
      { name: "origin/main", type: "remote" }, // origin's default branch
    ]);
    // Reading targets changes nothing.
    assert.equal(await git("status", "--porcelain"), "");
    assert.equal((await git("branch", "--show-current")).trim(), "main");
  });
});

test("a branch can be rebased onto origin/main after a safe preview", async () => {
  await clonedRepository(async ({ root, git, upstream }) => {
    // origin/main moves ahead while local main stays behind.
    fs.writeFileSync(path.join(path.dirname(root), "origin", "upstream.txt"), "upstream\n");
    await upstream("add", "."); await upstream("commit", "-m", "Upstream work");
    await git("fetch", "--quiet", "origin");
    const originMain = (await git("rev-parse", "refs/remotes/origin/main")).trim();
    const localMain = (await git("rev-parse", "refs/heads/main")).trim();
    assert.notEqual(originMain, localMain);
    const head = (await git("rev-parse", "HEAD")).trim();

    const value = { source: "topic", target: "origin/main", targetType: "remote" };
    const preview = await gitRepositoryAction(root, "rebase-preview", value);
    assert.equal(preview.blocked, false);
    assert.deepEqual(preview.conflicts, []);
    assert.equal(preview.targetHash, originMain);
    assert.equal((await git("rev-parse", "HEAD")).trim(), head, "preview leaves the checkout alone");
    assert.equal(await git("status", "--porcelain"), "");

    const result = await gitRepositoryAction(root, "rebase-branch", { ...value, sourceHash: preview.sourceHash, targetHash: preview.targetHash });
    assert.equal(result.error, undefined);
    assert.match(result.notice, /Rebased topic onto origin\/main/);
    assert.equal((await git("rev-parse", "topic~1")).trim(), originMain, "topic now sits on origin/main");
    assert.equal((await git("rev-parse", "refs/heads/main")).trim(), localMain, "local main is untouched");
    assert.equal((await git("rev-parse", "refs/remotes/origin/main")).trim(), originMain, "the remote-tracking branch is untouched");
    assert.equal((await git("rev-parse", "HEAD")).trim(), (await git("rev-parse", "refs/heads/topic")).trim(), "the rebase checked out the rebased branch, as local rebases do");
  });
});

test("remote targets keep the existing integration safeguards", async () => {
  await clonedRepository(async ({ root, git, upstream }) => {
    const remote = { source: "topic", target: "origin/main", targetType: "remote" };
    for (const action of ["merge-preview", "merge-branch"]) {
      await assert.rejects(gitRepositoryAction(root, action, remote), /Remote branches can only be rebase targets/);
    }
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { ...remote, target: "origin/missing" }), /existing remote branch/);
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { ...remote, target: "origin/HEAD" }), /existing remote branch/);
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { ...remote, target: "--upload-pack=x" }), /existing remote branch/);
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { ...remote, source: "origin/dev" }), /existing local branch/);
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { ...remote, targetType: "tag" }), /local or remote branch/);
    // Without targetType a remote name is still refused as a local branch.
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", { source: "topic", target: "origin/main" }), /two different local branches/);

    // A preview goes stale when the remote-tracking branch moves (for example after a fetch).
    const preview = await gitRepositoryAction(root, "rebase-preview", remote);
    fs.writeFileSync(path.join(path.dirname(root), "origin", "later.txt"), "later\n");
    await upstream("add", "."); await upstream("commit", "-m", "Later");
    await git("fetch", "--quiet", "origin");
    await assert.rejects(gitRepositoryAction(root, "rebase-branch", { ...remote, sourceHash: preview.sourceHash, targetHash: preview.targetHash }), /changed since the preview/);

    // Uncommitted changes still block the rebase before anything moves.
    const topic = (await git("rev-parse", "refs/heads/topic")).trim();
    await git("switch", "--quiet", "topic");
    fs.writeFileSync(path.join(root, "topic.txt"), "unsaved\n");
    await assert.rejects(gitRepositoryAction(root, "rebase-preview", remote), /Commit or stash changes/);
    assert.equal((await git("rev-parse", "refs/heads/topic")).trim(), topic);
    assert.equal((await readGitStatus(root)).operation, undefined);
  });
});

test("a local branch named like a remote branch stays distinct from the remote-tracking branch", async () => {
  await clonedRepository(async ({ root, git, upstream }) => {
    fs.writeFileSync(path.join(path.dirname(root), "origin", "upstream.txt"), "upstream\n");
    await upstream("add", "."); await upstream("commit", "-m", "Upstream work");
    await git("fetch", "--quiet", "origin");
    await git("branch", "origin/main", "refs/heads/main");
    const targets = await gitRepositoryAction(root, "integration-targets", { source: "topic" });
    assert.ok(targets.local.includes("origin/main"));
    assert.ok(targets.remote.includes("origin/main"));
    const local = await gitRepositoryAction(root, "rebase-preview", { source: "topic", target: "origin/main" });
    const remote = await gitRepositoryAction(root, "rebase-preview", { source: "topic", target: "origin/main", targetType: "remote" });
    assert.equal(local.targetHash, (await git("rev-parse", "refs/heads/origin/main")).trim());
    assert.equal(remote.targetHash, (await git("rev-parse", "refs/remotes/origin/main")).trim());
    assert.notEqual(local.targetHash, remote.targetHash);
  });
});
