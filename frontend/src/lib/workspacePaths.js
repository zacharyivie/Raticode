// Lexical UI identity only. Filesystem access and trust checks still belong to
// the backend. Do not resolve '..' here: a component may be a symlink.
export function displayPath(value = "") {
  let path = String(value ?? "");
  const windows = /^[a-z]:[\\/]/i.test(path) || /^[\\/]{2}[^\\/]/.test(path);
  if (windows) path = path.replaceAll("\\", "/");
  if (path.startsWith("//?/UNC/")) path = `//${path.slice(8)}`;
  else if (/^\/\/\?\/[a-z]:\//i.test(path)) path = path.slice(4);
  const prefix = path.startsWith("//") ? "//" : path.startsWith("/") ? "/" : "";
  path = prefix + path.slice(prefix.length).replace(/\/+/g, "/");
  if (/^[a-z]:\/$/i.test(path)) return path;
  return path.replace(/\/+$/, "") || prefix;
}

export function pathKey(value) {
  const path = displayPath(value);
  return /^[a-z]:(?:\/|$)/i.test(path) || path.startsWith("//") ? path.toLowerCase() : path;
}

export function samePath(left, right) {
  if (left == null || right == null) return left === right;
  return pathKey(left) === pathKey(right);
}

export function pathWithin(path, root) {
  const key = pathKey(path);
  const parent = pathKey(root);
  return Boolean(parent) && (key === parent || key.startsWith(parent.endsWith("/") ? parent : `${parent}/`));
}

export function uniquePaths(paths) {
  const seen = new Set();
  return paths.filter(path => {
    const key = pathKey(path);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function pathValue(values, path) {
  const key = Object.keys(values).find(candidate => samePath(candidate, path));
  return key === undefined ? undefined : values[key];
}

export function withPathValue(values, path, value) {
  const next = Object.fromEntries(Object.entries(values).filter(([key]) => !samePath(key, path)));
  if (value !== undefined) next[path] = value;
  return next;
}

export function pathMatchesChange(path, changedPath, isDirectory) {
  return Boolean(path && changedPath) && (isDirectory ? pathWithin(path, changedPath) : samePath(path, changedPath));
}

export function replacePathPrefix(path, sourcePath, destinationPath, isDirectory) {
  if (!destinationPath || !pathMatchesChange(path, sourcePath, isDirectory)) return path;
  if (!isDirectory || samePath(path, sourcePath)) return destinationPath;
  const suffix = displayPath(path).slice(displayPath(sourcePath).length).replace(/^\/+/, "");
  const separator = destinationPath.includes("\\") && !destinationPath.includes("/") ? "\\" : "/";
  return `${destinationPath.replace(/[\\/]+$/, "")}${separator}${suffix.replaceAll("/", separator)}`;
}

export function mergeCodeOpenPaths(current = [], additions = []) {
  return [...new Set([...current, ...additions].filter(Boolean))];
}

export function joinWorkspacePath(directory = "", name = "") {
  const separator = directory.includes("\\") && !directory.includes("/") ? "\\" : "/";
  return `${directory.replace(/[\\/]+$/, "")}${separator}${name.replace(/^[\\/]+/, "")}`;
}

export function parentWorkspacePath(value = "") {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (index <= 0) return normalized.slice(0, Math.max(index, 1)) || normalized;
  return normalized.slice(0, index);
}

export function workspaceBasename(value = "") {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  return normalized.slice(index + 1);
}
