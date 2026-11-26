// How workflow nodes and inspector fields show a path relative to the workflow's folder.
export function isUrlPath(pathValue = "") {
  return /^[a-z][a-z0-9+.-]*:/i.test(String(pathValue));
}

function isAbsolutePath(pathValue = "") {
  const value = String(pathValue);
  return (
    value.startsWith("/") ||
    value.startsWith("\\\\") ||
    /^[A-Za-z]:[\\/]/.test(value)
  );
}

export function joinPath(basePath = "", pathValue = "") {
  const base = String(basePath);
  const value = String(pathValue);
  const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  if (!base) return value;
  if (value === ".") return base;
  return `${base.replace(/[\\/]+$/, "")}${separator}${value.replace(/^[\\/]+/, "")}`;
}

function normalizeDisplayPath(pathValue = "") {
  const value = String(pathValue);
  if (!value) return "";
  return value.replace(/\/\.(?=\/|$)/g, "").replace(/\\/g, "\\");
}

export function resolveDisplayPath(pathValue = "", basePath = "") {
  const value = String(pathValue ?? "").trim();
  if (!value || isUrlPath(value) || isAbsolutePath(value)) {
    return value;
  }
  return normalizeDisplayPath(joinPath(basePath, value));
}
