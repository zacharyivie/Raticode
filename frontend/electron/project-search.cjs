const fs = require("node:fs/promises");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { createHash } = require("node:crypto");
const { Worker, isMainThread, parentPort, workerData } = require("node:worker_threads");
const run = promisify(execFile);
const digest = (buffer) => createHash("sha256").update(buffer).digest("hex");
const excluded = new Set([".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"]);

function compileRegex(source, flags, field) {
  try { return new RegExp(source, flags); }
  catch (error) { throw new Error(`${field}: ${error.message}`); }
}

function compileGlobs(value) {
  return value.split(",").map((value) => value.trim().replace(/\\/g, "/").replace(/\/$/, "")).filter(Boolean).map((glob) => {
    const source = glob.split(/(\*\*\/|\*\*|\*|\?)/).map((part) => part === "**/" ? "(?:.*/)?" : part === "**" ? ".*" : part === "*" ? "[^/]*" : part === "?" ? "[^/]" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
    return new RegExp(`${glob.includes("/") ? "^" : "(?:^|/)"}${source}(?:/|$)`);
  });
}

async function scanProject(root, options = {}) {
  const query = options.query;
  if (typeof query !== "string" || !query.length) return { files: [], count: 0, truncated: false };
  if (query.length > 1000 || /[\r\n]/.test(query)) throw new Error("Search must be a single line of at most 1,000 characters.");
  const needle = options.regex ? query : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = compileRegex(options.wholeWord ? `(?<![\\p{L}\\p{N}_])(?:${needle})(?![\\p{L}\\p{N}_])` : needle, options.matchCase ? "gu" : "giu", "Search expression");
  const exclusion = String(options.exclude || "");
  const ignores = options.excludeRegex
    ? (exclusion ? [compileRegex(exclusion, "u", "Files to exclude expression")] : [])
    : compileGlobs(exclusion);
  const includes = compileGlobs(String(options.include || ""));
  const result = { files: [], count: 0, truncated: false, skipped: 0 };
  const deadline = Date.now() + 10000;
  let candidates;
  try {
    const { stdout } = await run("git", ["-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."], { timeout: 3000, maxBuffer: 8 * 1024 * 1024 });
    candidates = [...new Set(stdout.split("\0").filter(Boolean))].sort();
  } catch {
    candidates = [];
    async function walk(directory) {
      for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
        if (Date.now() > deadline || candidates.length >= 20000) { result.truncated = true; return; }
        if (excluded.has(entry.name) || entry.isSymbolicLink()) continue;
        const target = path.join(directory, entry.name);
        if (entry.isDirectory()) await walk(target);
        else if (entry.isFile()) candidates.push(path.relative(root, target));
      }
    }
    await walk(root);
    candidates.sort();
  }
  // Opt in for native filesystem benchmarks; default remains serial until measured.
  const requestedConcurrency = Number(options.readConcurrency ?? process.env.TASKUROTTA_SEARCH_READ_CONCURRENCY ?? 1);
  const concurrency = Number.isInteger(requestedConcurrency) ? Math.max(1, Math.min(4, requestedConcurrency)) : 1;
  async function loadCandidate(relativePath) {
    if (Date.now() > deadline) return {};
    if (relativePath.split(/[\\/]/).some((part) => excluded.has(part))) return {};
    if (includes.length && !includes.some((pattern) => pattern.test(relativePath.replace(/\\/g, "/")))) return {};
    if (ignores.some((pattern) => pattern.test(relativePath.replace(/\\/g, "/")))) return {};
    const target = path.resolve(root, relativePath);
    const relative = path.relative(root, target);
    if (relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) return {};
    try {
      // Do not follow symlinks, including links in parent directories.
      if (await fs.realpath(target) !== target) return {};
      const stat = await fs.stat(target);
      if (!stat.isFile()) return {};
      if (stat.size > 2 * 1024 * 1024) { return { skipped: true }; }
      const buffer = await fs.readFile(target);
      if (buffer.includes(0) || !Buffer.from(buffer.toString("utf8")).equals(buffer)) return {};
      if (buffer.length > 2 * 1024 * 1024) return { skipped: true };
      return { buffer, target };
    } catch { return { skipped: true }; }
  }
  async function* readCandidates() {
    for (let offset = 0; offset < Math.min(candidates.length, 20000); offset += concurrency) {
      if (Date.now() > deadline) return;
      const batch = candidates.slice(offset, Math.min(offset + concurrency, 20000));
      const loaded = await Promise.all(batch.map(loadCandidate));
      for (let index = 0; index < batch.length; index++) yield { ...loaded[index], relativePath: batch[index] };
    }
  }
  let visited = 0;
  for await (const { relativePath, buffer, target, skipped } of readCandidates()) {
    if (result.count >= 1000 || Date.now() > deadline) { result.truncated = true; break; }
    visited++;
    if (skipped) result.skipped++;
    if (!buffer) continue;
    try {
      const matches = [];
      const lines = buffer.toString("utf8").split(/\r?\n/);
      for (let index = 0; index < lines.length; index++) {
        pattern.lastIndex = 0;
        for (const match of lines[index].matchAll(pattern)) {
          if (result.count >= 1000) { result.truncated = true; break; }
          const start = Math.max(0, match.index - 12);
          matches.push({ lineNumber: index + 1, column: match.index + 1, text: lines[index].slice(start, match.index + match[0].length + 120), offset: match.index - start, length: match[0].length });
          result.count++;
        }
        if (result.count >= 1000) { result.truncated = true; break; }
      }
      if (matches.length) {
        const file = { path: target, relativePath, matches, hash: digest(buffer) };
        if (typeof options.replacement === "string") {
          file.content = buffer.toString("utf8").split(/(\r?\n)/).map((line, index) => index % 2 ? line : line.replace(pattern, options.regex ? options.replacement : () => options.replacement)).join("");
        }
        result.files.push(file);
      }
    } catch { result.skipped++; }
  }
  if (visited < candidates.length) result.truncated = true;
  return result;
}
// Regex runs off the Electron main thread so pathological expressions can be stopped.
function searchProject(root, options = {}) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(__filename, { workerData: { root, options } });
    const timer = setTimeout(() => { void worker.terminate(); reject(new Error("Search timed out. Simplify the expression or exclude more files.")); }, 12000);
    worker.once("message", (message) => { clearTimeout(timer); if (message.error) reject(new Error(message.error)); else resolve(message.result); });
    worker.once("error", (error) => { clearTimeout(timer); reject(error); });
    worker.once("exit", (code) => { clearTimeout(timer); if (code) reject(new Error("Search worker stopped.")); });
  });
}
async function replaceProject(root, options = {}) {
  if (typeof options.replacement !== "string" || !Array.isArray(options.files) || !options.files.length) throw new Error("Search again before replacing.");
  const result = await searchProject(root, options);
  if (result.truncated) throw new Error("Narrow your search before replacing. Results are incomplete.");
  const files = options.files.map((expected) => {
    const file = result.files.find((entry) => entry.path === expected.path);
    if (!file || file.hash !== expected.hash) throw new Error("Files changed since the search. Refresh the results before replacing.");
    return file;
  });
  let count = 0;
  const changed = [];
  for (const file of files) {
    try {
      if (await fs.realpath(file.path) !== file.path) throw new Error("File path changed.");
      await require("./safe-files.cjs").writeFile(file.path, file.content, {
        expectedHash: file.hash,
        digest,
        authorize: (target) => {
          const relative = path.relative(root, target);
          if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error("File is outside the project.");
        },
      });
      count += file.matches.length;
      changed.push(file.path);
    } catch (error) { return { count, changed, error: `${file.relativePath}: ${error.message}` }; }
  }
  return { count, changed };
}
if (!isMainThread) scanProject(workerData.root, workerData.options).then((result) => parentPort.postMessage({ result }), (error) => parentPort.postMessage({ error: error.message }));
module.exports = { searchProject, replaceProject, scanProject };
