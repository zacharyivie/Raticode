const test = require("node:test");
const assert = require("node:assert/strict");
const { readGitStatus, readGitHistory } = require("../git-status.cjs");

function failingGit(stderr, extra = {}) {
  return async () => { throw Object.assign(new Error(`Command failed: git\n${stderr}`), { stderr, ...extra }); };
}

test("git failures other than 'not a repository' are reported once per folder and reason", async () => {
  const logs = [];
  const log = (message) => logs.push(message);
  const dubious = failingGit("fatal: detected dubious ownership in repository at '/srv/shared'\nTo add an exception run git config");
  for (let i = 0; i < 3; i++) {
    assert.deepEqual(await readGitStatus("/srv/shared", { runGit: dubious, log }), { active: false, entries: [], root: "" });
  }
  assert.deepEqual(logs, ["Git status failed in /srv/shared: fatal: detected dubious ownership in repository at '/srv/shared'"]);

  await readGitStatus("/home/me/notes", { runGit: failingGit("fatal: not a git repository (or any of the parent directories): .git"), log });
  await readGitStatus("/gone", { runGit: failingGit("", { code: "ENOENT" }), log });
  assert.equal(logs.length, 1, "normal answers stay quiet");

  await readGitHistory("/srv/other", { runGit: failingGit("", { code: "ENOENT", message: "spawn git ENOENT" }), log });
  assert.match(logs.at(-1), /^Git history failed in \/srv\/other: /, "a missing git program is reported");
});
