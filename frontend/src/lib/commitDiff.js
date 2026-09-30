const PREFIX = "raticode-commit-diff:";

// Paths are repository-relative. Bare patterns match a basename at any depth.
function pathPatterns(value) {
  return value.split(",").map(part => part.trim().replace(/^\.\//, "")).filter(Boolean).map(pattern => {
    if (pattern.endsWith("/")) pattern += "**";
    let source = "";
    for (let index = 0; index < pattern.length; index++) {
      const char = pattern[index];
      if (char === "*" && pattern[index + 1] === "*") {
        index++;
        if (pattern[index + 1] === "/") { source += "(?:.*/)?"; index++; }
        else source += ".*";
      } else if (char === "*") source += "[^/]*";
      else if (char === "?") source += "[^/]";
      else source += char.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
    return new RegExp(`${pattern.includes("/") ? "^" : "(?:^|/)"}${source}$`);
  });
}

export function searchCommitFiles(files, query = "") {
  const needle = query.toLowerCase();
  if (!needle) return files;
  return files.filter(file => [file.path, file.oldPath, file.original, file.modified]
    .some(value => typeof value === "string" && value.toLowerCase().includes(needle)));
}

export function filterCommitFiles(files, { query = "", include = "", exclude = "" } = {}) {
  const includes = pathPatterns(include);
  const excludes = pathPatterns(exclude);
  const matchingPaths = files.filter(file => {
    const paths = [file.path, file.oldPath].filter(Boolean);
    const matches = patterns => patterns.some(pattern => paths.some(path => pattern.test(path)));
    if (includes.length && !matches(includes)) return false;
    if (matches(excludes)) return false;
    return true;
  });
  return searchCommitFiles(matchingPaths, query);
}

// Index literal, case-insensitive matches once per query, independently of path
// filters and Monaco's lazy editor lifecycle. Columns use UTF-16, as Monaco does.
export function indexCommitMatches(files, query = "") {
  const index = new Map();
  if (!query) return index;
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  for (const file of files) {
    const matches = [];
    const title = file.oldPath && file.oldPath !== file.path ? `${file.oldPath} → ${file.path}` : file.path;
    for (const [side, text] of [["path", title], ["original", file.original], ["modified", file.modified]]) {
      if (typeof text !== "string" || (file.binary && side !== "path")) continue;
      let cursor = 0, line = 1, lineStart = 0;
      pattern.lastIndex = 0;
      for (const match of text.matchAll(pattern)) {
        const start = match.index, end = start + match[0].length;
        for (; cursor < start; cursor++) if (text[cursor] === "\n") { line++; lineStart = cursor + 1; }
        const startLineNumber = line, startColumn = start - lineStart + 1;
        for (; cursor < end; cursor++) if (text[cursor] === "\n") { line++; lineStart = cursor + 1; }
        matches.push({ file, side, start, end, range: { startLineNumber, startColumn, endLineNumber: line, endColumn: end - lineStart + 1 } });
      }
    }
    index.set(file, matches);
  }
  return index;
}

export function commitDiffPath(projectRoot, hash) {
  return `${PREFIX}${encodeURIComponent(projectRoot)}:${hash}`;
}

export function parseCommitDiffPath(path = "") {
  if (!path.startsWith(PREFIX)) return null;
  const separator = path.lastIndexOf(":");
  const hash = path.slice(separator + 1);
  if (!/^[0-9a-f]{40,64}$/.test(hash)) return null;
  try {
    const projectRoot = decodeURIComponent(path.slice(PREFIX.length, separator));
    return projectRoot ? { projectRoot, hash } : null;
  } catch { return null; }
}
