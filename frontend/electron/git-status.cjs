const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const GIT_OUTPUT_LIMIT = 16 * 1024 * 1024;

const repositoryLocations = new Map();
const statusReads = new Map();
const worktreeReads = new WeakMap();
const metadataCaches = new WeakMap();
const METADATA_MAX_AGE_MS = 15000;

function runnerMap(collection, runner) {
  if (!collection.has(runner)) collection.set(runner, new Map());
  return collection.get(runner);
}

function isGitRead(args) {
  const offset = args[0] === "-C" ? 2 : 0;
  const [command, subcommand] = args.slice(offset);
  return ["status", "rev-parse", "for-each-ref", "rev-list", "show", "diff", "log", "check-ref-format"].includes(command)
    || (command === "branch" && subcommand === "--show-current")
    || (command === "remote" && !subcommand)
    || (command === "stash" && subcommand === "list")
    || (command === "worktree" && subcommand === "list");
}

async function readSlowMetadata(projectRoot, branch, runner, options) {
  const cache = runnerMap(metadataCaches, runner);
  const key = path.resolve(projectRoot);
  const now = (options.now || Date.now)();
  const existing = cache.get(key);
  if (!options.forceMetadata && existing?.generation === gitReadGeneration
      && existing.branch === branch && now - existing.at < METADATA_MAX_AGE_MS) return existing.pending;
  const generation = gitReadGeneration;
  const pending = (async () => {
    const [refs, remoteNames, stashes] = await Promise.allSettled([
      runner(["-C", projectRoot, "for-each-ref", "--format=%(refname:short)", "refs/heads/"]),
      runner(["-C", projectRoot, "remote"]),
      runner(["-C", projectRoot, "stash", "list", "--format=%gd"]),
    ]);
    const lines = result => result.status === "fulfilled" ? String(result.value).trim().split("\n").filter(Boolean) : [];
    return { branches: lines(refs), remotes: lines(remoteNames), stashCount: lines(stashes).length,
      ...(refs.status === "rejected" ? { branchesUnavailable: true } : {}) };
  })();
  cache.delete(key);
  cache.set(key, { branch, generation, at: now, pending });
  if (cache.size > 64) cache.delete(cache.keys().next().value);
  return pending;
}
let gitReadGeneration = 0;

async function repositoryLocation(projectRoot, runner) {
  if (runner !== runGit) return { root: String(await runner(["-C", projectRoot, "rev-parse", "--show-toplevel"])).trim() };
  let root = path.resolve(projectRoot);
  while (true) {
    const marker = path.join(root, ".git");
    try {
      const info = await fs.promises.stat(marker);
      const revision = `${info.dev}:${info.ino}:${info.isFile() ? `${info.mtimeMs}:${info.size}` : "directory"}`;
      const existing = repositoryLocations.get(marker);
      if (existing?.revision === revision) return existing;
      const [gitDir, repositoryRoot] = (await Promise.all([
        runner(["-C", projectRoot, "rev-parse", "--absolute-git-dir"]),
        runner(["-C", projectRoot, "rev-parse", "--show-toplevel"]),
      ])).map(value => String(value).trim());
      const location = { root: repositoryRoot, gitDir, revision };
      repositoryLocations.set(marker, location);
      if (repositoryLocations.size > 64) repositoryLocations.delete(repositoryLocations.keys().next().value);
      return location;
    } catch (error) {
      if (error.code !== "ENOENT" && error.code !== "ENOTDIR") throw error;
    }
    const parent = path.dirname(root);
    if (parent === root) throw new Error("Not a Git repository");
    root = parent;
  }
}

const branchCaches = new WeakMap();
async function readGitBranches(projectRoot, options = {}) {
  const runner = options.runner || runGit;
  const cache = runnerMap(branchCaches, runner);
  const key = path.resolve(projectRoot);
  const now = (options.now || Date.now)();
  const existing = cache.get(key);
  if (existing && existing.generation === gitReadGeneration && (!existing.settled || (!options.force && now - existing.at < METADATA_MAX_AGE_MS))) return existing.pending;
  const pending = (async () => {
    const { root } = await repositoryLocation(projectRoot, runner);
    const [refs, current] = await Promise.all([
      runner(["-C", root, "for-each-ref", "--format=%(refname:short)", "refs/heads/"]),
      runner(["-C", root, "branch", "--show-current"]),
    ]);
    return { active: true, root, branch: String(current).trim(), branches: String(refs).trim().split("\n").filter(Boolean) };
  })();
  const record = { pending, at: now, generation: gitReadGeneration };
  cache.set(key, record);
  if (cache.size > 64) cache.delete(cache.keys().next().value);
  try { return await pending; }
  catch (error) { if (cache.get(key) === record) cache.delete(key); throw error; }
  finally { record.settled = true; }
}

function readPorcelain(projectRoot, runner, includeBranch = false) {
  const read = () => runner(["-C", projectRoot, "status", "--porcelain=v2", ...(includeBranch ? ["--branch"] : []), "-z", "--untracked-files=all", "--", "."]);
  if (runner !== runGit) return read();
  const base = `${gitReadGeneration}:${projectRoot}`;
  // Baselines can share a pending branch read, but need no ahead/behind traversal themselves.
  if (!includeBranch && statusReads.has(`${base}:true`)) return statusReads.get(`${base}:true`);
  const key = `${base}:${includeBranch}`;
  if (statusReads.has(key)) return statusReads.get(key);
  const pending = read().finally(() => statusReads.delete(key));
  statusReads.set(key, pending);
  return pending;
}

function runGit(args, options = {}) {
  const execFileImpl = options.execFileImpl || execFile;
  const mutates = !isGitRead(args);
  if (mutates) gitReadGeneration += 1;
  return new Promise((resolve, reject) => {
    execFileImpl(
      "git",
      args,
      {
        cwd: options.cwd,
        encoding: options.encoding || "utf8",
        maxBuffer: GIT_OUTPUT_LIMIT,
        windowsHide: true,
        timeout: 120000,
        env: { ...process.env, ...options.env, GIT_TERMINAL_PROMPT: "0", ...(!mutates ? { GIT_OPTIONAL_LOCKS: "0" } : {}) },
      },
      (error, stdout) => {
        if (mutates) gitReadGeneration += 1;
        if (error) {
          reject(error);
          return;
        }
        resolve(stdout);
      },
    );
  });
}

function sourceControlStatus(xy) {
  if (["DD", "AU", "UD", "UA", "DU", "AA", "UU"].includes(xy)) return "!";
  if (xy === "??") return "U";
  if (xy === "!!") return "";
  if (xy.includes("D")) return "D";
  if (/[ARC]/.test(xy)) return "A";
  return "M";
}

function parseGitStatus(output = "") {
  const records = String(output).split("\0");
  const entries = [];
  for (let index = 0; index < records.length; index += 1) {
    let record = records[index];
    if (!record || record.startsWith("# ")) continue;
    // v2 has fixed metadata fields, followed by the unquoted path. Keep spaces/newlines.
    if (/^[12u] /.test(record)) {
      const fields = record[0] === "1" ? 8 : record[0] === "2" ? 9 : 10;
      const parts = record.split(" ");
      record = `${parts[1].replaceAll(".", " ")} ${parts.slice(fields).join(" ")}`;
    } else if (record.startsWith("? ")) record = `?? ${record.slice(2)}`;
    else if (record.startsWith("! ")) continue;
    if (record.length < 4) continue;
    const xy = record.slice(0, 2);
    const relativePath = record.slice(3);
    const status = sourceControlStatus(xy);
    const originalPath = /[RC]/.test(xy) ? records[index + 1] : undefined;
    if (status && relativePath) entries.push({ path: relativePath, status,
      indexStatus: xy[0], worktreeStatus: xy[1],
      staged: status !== "!" && xy[0] !== " " && xy !== "??",
      unstaged: status === "!" || xy[1] !== " ",
      ...(originalPath ? { originalPath } : {}),
    });
    if (/[RC]/.test(xy)) index += 1;
  }
  return entries;
}

async function readGitStatus(projectRoot, options = {}) {
  const runner = options.runGit || runGit;
  try {
    const { root, gitDir } = await repositoryLocation(projectRoot, runner);
    const output = await readPorcelain(projectRoot, runner, true);
    const projectPrefix = path.relative(root, projectRoot).replaceAll("\\", "/");
    const entries = parseGitStatus(output).flatMap((entry) => {
      if (!projectPrefix) return [entry];
      const prefix = `${projectPrefix}/`;
      return entry.path.startsWith(prefix)
        ? [{ ...entry, path: entry.path.slice(prefix.length), ...(entry.originalPath ? { originalPath: path.relative(projectRoot, path.join(root, entry.originalPath)) } : {}) }]
        : [];
    });
    let branch = "", ahead = null, behind = null;
    for (const record of String(output).split("\0")) {
      if (record.startsWith("# branch.head ")) {
        branch = record.slice(14);
        if (branch === "(detached)") branch = "";
      }
      const counts = /^# branch\.ab \+(\d+) -(\d+)$/.exec(record);
      if (counts) { ahead = Number(counts[1]); behind = Number(counts[2]); }
    }
    const { branches, remotes, stashCount, branchesUnavailable } = await readSlowMetadata(projectRoot, branch, runner, options);
    let operation;
    for (const [marker, kind] of [["rebase-merge", "rebase"], ["rebase-apply", "rebase"], ["MERGE_HEAD", "merge"]]) {
      try {
        const markerPath = gitDir ? path.join(gitDir, marker) : String(await runner(["-C", projectRoot, "rev-parse", "--git-path", marker])).trim();
        if (markerPath && fs.existsSync(path.resolve(projectRoot, markerPath))) { operation = kind; break; }
      } catch { /* Optional operation metadata. */ }
    }
    return { active: true, entries, root, branch, branches, ahead, behind, remotes, stashCount, ...(branchesUnavailable ? { branchesUnavailable } : {}), ...(operation ? { operation } : {}) };
  } catch {
    return { active: false, entries: [], root: "" };
  }
}

// Only literal paths from a fresh status snapshot may be mutated.
async function changeGitFile(projectRoot, relativePath, action, options = {}) {
  const runner = options.runGit || runGit;
  if (!["stage", "unstage", "revert", "revert-staged"].includes(action)) throw new Error("Unknown Git action.");
  const snapshot = await readGitStatus(projectRoot, { ...options, forceMetadata: true });
  const entry = snapshot.entries.find((item) => item.path === relativePath);
  if (!entry) throw new Error("This change is no longer present. Refresh source control.");
  const paths = [entry.path, ...(entry.originalPath && action !== "revert" ? [entry.originalPath] : [])];
  if (paths.some((name) => { const target = path.resolve(projectRoot, name); return !target.startsWith(path.resolve(projectRoot) + path.sep); })) throw new Error("This rename crosses the project boundary. Open the repository root to change it.");
  const literalPaths = paths.map((name) => `:(literal)${name}`);
  const git = (...args) => runner(["-C", projectRoot, ...args]);
  if (entry.status === "!" && action !== "stage") throw new Error("Resolve this conflict and stage the result, or abort the merge or rebase.");
  if (action === "stage") {
    if (entry.status === "!" && fs.existsSync(path.resolve(projectRoot, entry.path))) {
      const content = await fs.promises.readFile(path.resolve(projectRoot, entry.path), "utf8");
      if (/^(<{7}|={7}|>{7})(?: |$)/m.test(content)) throw new Error("Remove the conflict markers before marking this file resolved.");
    }
    await git("add", "--", ...literalPaths);
  }
  if (action === "unstage") {
    let hasHead = true;
    try { await git("rev-parse", "--verify", "HEAD"); } catch { hasHead = false; }
    if (hasHead) await git("restore", "--staged", "--", ...literalPaths);
    else await git("rm", "--cached", "--", ...literalPaths);
  }
  if (action === "revert" || action === "revert-staged") {
    if (action === "revert-staged" && entry.unstaged) throw new Error("This file also has unstaged edits. Unstage it first to preserve those edits, or discard the unstaged edits before reverting the staged change.");
    if (entry.indexStatus === "?" || (action === "revert-staged" && entry.indexStatus === "A")) {
      if (!options.trashItem) throw new Error("Trash is unavailable for this file.");
      // New files have no HEAD version. Unstage first, then use recoverable OS trash.
      if (entry.staged) await git("rm", "--cached", "--", ...literalPaths);
      const target = path.resolve(projectRoot, entry.path);
      if (!target.startsWith(path.resolve(projectRoot) + path.sep)) throw new Error("Invalid Git path.");
      await options.trashItem(target);
    } else if (action === "revert") {
      await git("restore", "--worktree", "--", ...literalPaths);
    } else {
      await git("restore", "--source=HEAD", "--staged", "--worktree", "--", ...literalPaths);
    }
  }
  return readGitStatus(projectRoot, options);
}

async function switchGitBranch(projectRoot, branch, options = {}) {
  const runner = options.runGit || runGit;
  const snapshot = await readGitStatus(projectRoot, { ...options, forceMetadata: true });
  if (!snapshot.branches?.includes(branch) || branch.startsWith("-")) throw new Error("Choose an existing local branch.");
  try {
    await runner(["-C", projectRoot, "switch", "--no-guess", branch]);
  } catch (error) {
    if (/overwritten|local changes|untracked working tree/i.test(error.message)) {
      return { ...snapshot, switchBlocked: true, requestedBranch: branch,
        notice: "Commit your changes, or stash them before switching branches." };
    }
    throw error;
  }
  return readGitStatus(projectRoot, options);
}

async function gitRepositoryAction(projectRoot, action, value = "", options = {}) {
  if (/^(merge-|rebase-|stash-(list|preview|drop|clear|apply-selected)$)/.test(action)) {
    return require("./git-integration.cjs").integrationAction(projectRoot, action, value, options);
  }
  const runner = options.runGit || runGit;
  const git = (...args) => runner(["-C", projectRoot, ...args]);
  if (["staged-diff", "reset-soft", "reset-hard", "detach-commit", "branch-commit"].includes(action)) {
    const root = String(await git("rev-parse", "--show-toplevel")).trim();
    if (path.resolve(root) !== path.resolve(projectRoot)) throw new Error("Open the repository root to review all affected changes first.");
  }
  if (action === "staged-diff") {
    const conflicts = String(await git("diff", "--name-only", "--diff-filter=U"));
    if (conflicts.trim()) throw new Error("Resolve and stage conflicts before generating a commit message.");
    const tree = String(await git("write-tree")).trim();
    let diff;
    try {
      diff = String(await git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color"));
    } catch (error) {
      if (error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") return { tree, inspectStaged: true };
      throw error;
    }
    if (diff.length > 120000) return { tree, inspectStaged: true };
    if (!diff.trim()) throw new Error("Stage changes before generating a commit message.");
    return { diff, tree };
  } else if (["reset-soft", "reset-hard", "detach-commit", "branch-commit"].includes(action)) {
    if (!value || !/^[0-9a-f]{40,64}$/.test(value.hash)) throw new Error("Choose a valid commit.");
    await git("rev-parse", "--verify", `${value.hash}^{commit}`);
    const snapshot = await readGitStatus(projectRoot, { ...options, forceMetadata: true });
    if (snapshot.operation || snapshot.entries.some(entry => entry.status === "!")) throw new Error("Finish or abort the current operation first.");
    if (action.startsWith("reset-")) await git("reset", action === "reset-soft" ? "--soft" : "--hard", value.hash);
    else if (action === "detach-commit") await git("switch", "--detach", value.hash);
    else {
      if (typeof value.branch !== "string" || value.branch.startsWith("-")) throw new Error("Enter a valid branch name.");
      await git("check-ref-format", "--branch", value.branch);
      await git("switch", "-c", value.branch, value.hash);
    }
  } else if (action === "branch-delete" || action === "branch-delete-force") {
    if (typeof value !== "string" || !value || value.startsWith("-")) throw new Error("Choose an existing local branch.");
    await git("check-ref-format", "--branch", value);
    await git("show-ref", "--verify", `refs/heads/${value}`);
    // Even forced deletion refuses branches checked out in any worktree.
    try {
      await runner(["-C", projectRoot, "branch", action === "branch-delete-force" ? "-D" : "--delete", "--", value], { env: { LC_ALL: "C" } });
    } catch (error) {
      if (action === "branch-delete" && /not fully merged/.test(String(error.stderr || error.message))) {
        return { branchDeleteUnmerged: true };
      }
      throw error;
    }
    return readGitStatus(projectRoot, { ...options, forceMetadata: true });
  } else if (action === "commit") {
    if (typeof value !== "string" || !value.trim() || value.length > 72000) throw new Error("Enter a commit message.");
    const repositoryRoot = String(await git("rev-parse", "--show-toplevel")).trim();
    if (path.resolve(repositoryRoot) !== path.resolve(projectRoot)) throw new Error("Open the repository root to review and commit all staged changes.");
    const staged = String(await git("diff", "--cached", "--name-only"));
    if (!staged.trim()) throw new Error("Stage changes before committing.");
    await git("commit", "-m", value);
  } else if (action === "pull") {
    await git("pull", "--ff-only");
  } else if (action === "push") {
    await git("push");
  } else if (action === "publish") {
    const snapshot = await readGitStatus(projectRoot, { ...options, forceMetadata: true });
    if (!snapshot.branch) throw new Error("Switch to a branch before publishing.");
    const remotes = String(await git("remote")).trim().split("\n").filter(Boolean);
    if (!remotes.includes(value)) throw new Error("Choose an existing remote.");
    await git("push", "--set-upstream", value, snapshot.branch);
  } else if (action === "stash-switch") {
    const snapshot = await readGitStatus(projectRoot, { ...options, forceMetadata: true });
    if (!snapshot.branches?.includes(value) || value.startsWith("-")) throw new Error("Choose an existing local branch.");
    // Leave the stash intact even if switching fails. Never pop onto another branch automatically.
    await git("stash", "push", "--include-untracked", "-m", `Raticode: before switching from ${snapshot.branch} to ${value}`);
    const result = await switchGitBranch(projectRoot, value, options);
    return { ...result, notice: result.switchBlocked ? result.notice : "Branch switched. Your changes are saved in the stash." };
  } else if (action === "stash-apply") {
    await git("stash", "apply", "--index", "stash@{0}");
  } else throw new Error("Unknown repository action.");
  return readGitStatus(projectRoot, options);
}

function parseGitHistory(output = "") {
  return String(output)
    .split("\0")
    .filter(Boolean)
    .map((record) => {
      const normalizedRecord = record.replace(/^\n+/, "");
      const [hash = "", shortHash = "", author = "", authoredAt = "", subject = "", message = "", refsAndStats = ""] = normalizedRecord.split("\x1f");
      const [refs = "", ...statLines] = refsAndStats.split("\n");
      let binaryFiles = 0;
      let insertions = 0;
      let deletions = 0;
      for (const line of statLines) {
        const [added, deleted] = line.split("\t");
        if (added === "-" && deleted === "-") binaryFiles += 1;
        if (/^\d+$/.test(added)) insertions += Number(added);
        if (/^\d+$/.test(deleted)) deletions += Number(deleted);
      }
      return {
        author,
        authoredAt,
        binaryFiles,
        deletions,
        hash,
        insertions,
        message: message.trimEnd(),
        refs,
        shortHash,
        subject,
      };
    })
    .filter((entry) => entry.hash);
}

async function readGitHistory(projectRoot, options = {}) {
  const runner = options.runGit || runGit;
  try {
    const root = String(await runner(["-C", projectRoot, "rev-parse", "--show-toplevel"])).trim();
    const output = await runner([
      "-C", projectRoot, "log", "--max-count=100", "--date=iso-strict", "--numstat", "--diff-merges=first-parent",
      "--pretty=format:%x00%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1f%B%x1f%D",
    ]);
    return { active: true, commits: parseGitHistory(output), root };
  } catch {
    return { active: false, commits: [], root: "" };
  }
}

function parseGitWorktrees(output = "") {
  const worktrees = [];
  let current = null;
  for (const line of String(output).split("\n")) {
    if (!line) {
      if (current?.path) worktrees.push(current);
      current = null;
      continue;
    }
    const [key, ...rest] = line.split(" ");
    const value = rest.join(" ");
    if (key === "worktree") current = { bare: false, branch: "", detached: false, head: "", locked: false, path: value, prunable: false };
    else if (!current) continue;
    else if (key === "HEAD") current.head = value;
    else if (key === "branch") current.branch = value.replace(/^refs\/heads\//, "");
    else if (key === "bare") current.bare = true;
    else if (key === "detached") current.detached = true;
    else if (key === "locked") current.locked = true;
    else if (key === "prunable") current.prunable = true;
  }
  if (current?.path) worktrees.push(current);
  return worktrees;
}

async function readGitWorktrees(projectRoot, options = {}) {
  const runner = options.runGit || runGit;
  try {
    const { root, gitDir } = await repositoryLocation(projectRoot, runner);
    let repository = root;
    if (gitDir) {
      let commonDir = "";
      try { commonDir = (await fs.promises.readFile(path.join(gitDir, "commondir"), "utf8")).trim(); } catch { /* Main worktree. */ }
      repository = commonDir ? path.resolve(gitDir, commonDir) : gitDir;
    }
    // Share only pending reads. Every later listing sees externally changed worktrees.
    const reads = runnerMap(worktreeReads, runner);
    const key = `${gitReadGeneration}:${repository}`;
    let pending = reads.get(key);
    if (!pending) {
      pending = Promise.resolve().then(() => runner(["-C", root, "worktree", "list", "--porcelain"]))
        .finally(() => { if (reads.get(key) === pending) reads.delete(key); });
      reads.set(key, pending);
    }
    const output = await pending;
    return {
      active: true,
      root,
      worktrees: parseGitWorktrees(output).filter((worktree) => fs.existsSync(worktree.path)),
    };
  } catch {
    return { active: false, root: "", worktrees: [] };
  }
}

async function addGitWorktree(projectRoot, targetPath, branch, options = {}) {
  const runner = options.runGit || runGit;
  const args = ["-C", projectRoot, "worktree", "add"];
  if (typeof branch !== "string" || branch.startsWith("-")) throw new Error("Invalid branch name.");
  await runner(["check-ref-format", "--branch", branch]);
  if (options.startPoint) {
    if (!/^[0-9a-f]{40,64}$/.test(options.startPoint) || !options.createBranch) throw new Error("Choose a commit and a new branch for the worktree.");
    await runner(["-C", projectRoot, "rev-parse", "--verify", `${options.startPoint}^{commit}`]);
  }
  if (options.createBranch === true) args.push("-b", branch);
  args.push(targetPath);
  if (options.startPoint) args.push(options.startPoint);
  if (options.createBranch !== true && branch) args.push(branch);
  gitReadGeneration += 1;
  await runner(args);
  gitReadGeneration += 1;
  return readGitWorktrees(projectRoot, options);
}

async function removeGitWorktree(projectRoot, targetPath, options = {}) {
  const runner = options.runGit || runGit;
  gitReadGeneration += 1;
  if (fs.existsSync(targetPath)) {
    const args = ["-C", projectRoot, "worktree", "remove"];
    if (options.force === true) args.push("--force");
    try {
      await runner([...args, targetPath]);
    } catch (error) {
      if (options.force !== true && /contains modified or untracked files/i.test(String(error.stderr || error.message))) {
        return { requiresForce: true };
      }
      throw error;
    }
  } else {
    await runner(["-C", projectRoot, "worktree", "prune", "--expire", "now"]);
  }
  gitReadGeneration += 1;
  return readGitWorktrees(projectRoot, options);
}

function parseGitDiffHunks(output = "") {
  const hunks = [];
  for (const line of String(output).split("\n")) {
    const match = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/);
    if (!match) continue;
    const startLine = Number(match[1]);
    const lineCount = match[2] == null ? 1 : Number(match[2]);
    hunks.push({
      endLine: lineCount > 0 ? startLine + lineCount - 1 : Math.max(1, startLine),
      startLine: Math.max(1, startLine),
    });
  }
  return hunks;
}

const baselineCache = new Map();
let baselineCacheBytes = 0;
const BASELINE_CACHE_LIMIT = 16 * 1024 * 1024;

async function baselineRevision(gitDir, targetPath) {
  let commonDir = gitDir;
  try { commonDir = path.resolve(gitDir, (await fs.promises.readFile(path.join(gitDir, "commondir"), "utf8")).trim()); } catch { /* Ordinary repository. */ }
  const head = await fs.promises.readFile(path.join(gitDir, "HEAD"), "utf8");
  const ref = head.startsWith("ref: ") ? path.join(commonDir, head.slice(5).trim()) : path.join(gitDir, "HEAD");
  const stamps = await Promise.all([targetPath, path.join(gitDir, "index"), ref, path.join(commonDir, "packed-refs")].map(async (file) => {
    try { const s = await fs.promises.stat(file, { bigint: true }); return `${s.dev}:${s.ino}:${s.size}:${s.mtimeNs}:${s.ctimeNs}`; }
    catch (error) { if (error.code === "ENOENT") return "missing"; throw error; }
  }));
  return `${gitReadGeneration}:${head}:${stamps.join("|")}`;
}

async function readGitFileBaseline(targetPath, options = {}) {
  const runner = options.runGit || runGit;
  // Deleted folders may no longer exist; locate the nearest existing ancestor.
  let directory = path.dirname(targetPath);
  while (!fs.existsSync(directory) && path.dirname(directory) !== directory) directory = path.dirname(directory);
  try {
    const { root, gitDir } = await repositoryLocation(directory, runner);
    const relativePath = path.relative(root, targetPath).replaceAll("\\", "/");
    if (!relativePath || relativePath.startsWith("../")) return { changed: false, content: "", hunks: [], tracked: false };
    const git = (...args) => runner(["-C", root, ...args]);
    const status = parseGitStatus(await readPorcelain(root, runner));
    const entry = status.find((item) => item.path === relativePath);
    const group = options.group;
    const cacheKey = `${root}:${targetPath}:${group || ""}`;
    const revision = gitDir ? `${await baselineRevision(gitDir, targetPath)}:${JSON.stringify(entry)}` : null;
    const cached = baselineCache.get(cacheKey);
    if (revision && cached?.revision === revision) return structuredClone(cached.result);
    const original = entry?.status === "!" ? `:2:${relativePath}` : group === "unstaged" ? `:${relativePath}` : `HEAD:${entry?.originalPath || relativePath}`;
    const readVersion = async (spec) => {
      try { const output = await runner(["-C", root, "show", spec], { encoding: "buffer" }); return Buffer.isBuffer(output) ? output : Buffer.from(String(output)); }
      catch { return Buffer.alloc(0); }
    };
    const deleted = !fs.existsSync(targetPath);
    // These reads share the same baseline revision, but none needs another's output.
    const [originalBytes, incomingBytes, modifiedBytes, diff] = await Promise.all([
      readVersion(original),
      entry?.status === "!" ? readVersion(`:3:${relativePath}`) : null,
      (async () => {
        if (group === "staged") return readVersion(`:${relativePath}`);
        if (deleted) return Buffer.alloc(0);
        const stat = await fs.promises.stat(targetPath);
        if (stat.size > GIT_OUTPUT_LIMIT) throw new Error("File is too large to compare.");
        return fs.promises.readFile(targetPath);
      })(),
      (async () => {
        try {
          return String(await git("diff", ...(group === "staged" ? ["--cached"] : group === "unstaged" ? [] : ["HEAD"]), "--no-color", "--no-ext-diff", "--unified=0", "--", `:(literal)${relativePath}`));
        } catch { return ""; /* New repository. */ }
      })(),
    ]);
    const content = originalBytes.toString("utf8");
    const modifiedContent = modifiedBytes.toString("utf8");
    const binary = originalBytes.includes(0) || modifiedBytes.includes(0) || /\.(avif|png|jpe?g|gif|webp|ico|bmp|pdf)$/i.test(relativePath);
    const result = { ...(entry?.status === "!" ? { conflict: true, incomingContent: incomingBytes.toString("utf8") } : {}), changed: Boolean(entry && (group ? entry[group] : true)) || !originalBytes.equals(modifiedBytes), content: binary ? "" : content, modifiedContent: binary ? "" : modifiedContent, deleted, hunks: parseGitDiffHunks(diff), tracked: Boolean(entry) || content.length > 0,
      ...(binary ? { binary: true, originalBytes: originalBytes.length, modifiedBytes: modifiedBytes.length,
        originalData: originalBytes.toString("base64"), modifiedData: modifiedBytes.toString("base64") } : {}) };
    if (revision && revision === `${await baselineRevision(gitDir, targetPath)}:${JSON.stringify(entry)}`) {
      const bytes = Buffer.byteLength(JSON.stringify(result), "utf8");
      if (cached) { baselineCacheBytes -= cached.bytes; baselineCache.delete(cacheKey); }
      if (bytes <= BASELINE_CACHE_LIMIT) {
        while (baselineCache.size && (baselineCacheBytes + bytes > BASELINE_CACHE_LIMIT || baselineCache.size >= 64)) {
          const key = baselineCache.keys().next().value;
          baselineCacheBytes -= baselineCache.get(key).bytes;
          baselineCache.delete(key);
        }
        baselineCache.set(cacheKey, { revision, result: structuredClone(result), bytes });
        baselineCacheBytes += bytes;
      }
    }
    return result;
  } catch {
    return { changed: false, content: "", hunks: [], tracked: false };
  }
}

module.exports = {
  gitRepositoryAction,
  changeGitFile,
  switchGitBranch,
  parseGitStatus,
  parseGitDiffHunks,
  parseGitHistory,
  readGitFileBaseline,
  parseGitWorktrees,
  readGitHistory,
  readGitStatus,
  readGitBranches,
  readGitWorktrees,
  addGitWorktree,
  removeGitWorktree,
  runGit,
  sourceControlStatus,
};
