const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const { URL, fileURLToPath, pathToFileURL } = require("node:url");

const LOCAL_DEV_ORIGINS = new Set(["http://127.0.0.1:5173", "http://localhost:5173"]);
const SAFE_EXTERNAL_SCHEMES = new Set(["http:", "https:", "mailto:"]);

function createIpcSecurity({
  appRoot,
  appRoots,
  devServerUrl,
  getDataDir,
  getMainWebContents,
  isProduction = false,
  trustedRoots = [],
  persistTrustedRoot,
} = {}) {
  const grantedRoots = new Map();
  const userPaths = new Map();
  const offlineTrustedRoots = new Set();
  let knownDataDir;
  let canonicalDataDir;
  function approvedDataRoot() {
    const configured = getDataDir();
    if (configured !== knownDataDir) {
      canonicalDataDir = realpathForContainment(configured);
      knownDataDir = configured;
    }
    return canonicalDataDir;
  }
  approvedDataRoot();

  function assertTrustedSender(event) {
    const url = event?.senderFrame?.url || event?.sender?.getURL?.() || "";
    const expectedWebContents =
      typeof getMainWebContents === "function" ? getMainWebContents() : null;
    if (expectedWebContents) {
      if (event?.sender !== expectedWebContents) {
        throw new Error("Rejected IPC message from an unexpected window.");
      }
      if (
        expectedWebContents.mainFrame &&
        event?.senderFrame &&
        event.senderFrame !== expectedWebContents.mainFrame
      ) {
        throw new Error("Rejected IPC message from an unexpected frame.");
      }
    }
    if (!isTrustedSenderUrl(url, { appRoot, appRoots, devServerUrl, isProduction })) {
      throw new Error(`Rejected IPC message from untrusted sender: ${url || "unknown"}`);
    }
    return true;
  }

  function secureHandler(handler) {
    return async (event, ...args) => {
      assertTrustedSender(event);
      return handler(event, ...args);
    };
  }

  function grantPath(targetPath) {
    if (!targetPath || typeof targetPath !== "string") return "";
    const root = realpathExisting(path.resolve(targetPath));
    const existingGrantId = findGrantIdForRoot(root);
    if (existingGrantId) {
      return { grantId: existingGrantId, path: root };
    }
    const grantId = crypto.randomUUID();
    grantedRoots.set(grantId, root);
    return { grantId, path: root };
  }

  // User navigation stays local to Electron and never renews agent permissions.
  function grantUserPath(targetPath) {
    if (!targetPath || typeof targetPath !== "string") throw new Error("A path is required.");
    const canonical = realpathExisting(path.resolve(targetPath));
    const existing = grantForPath(canonical) || userGrantForPath(canonical);
    if (existing) return { path: canonical, grantId: existing };
    const grantId = crypto.randomUUID();
    // Include the containing directory for editor saves and preview assets.
    userPaths.set(grantId, fs.statSync(canonical).isFile() ? path.dirname(canonical) : canonical);
    return { path: canonical, grantId };
  }

  function userGrantForPath(targetPath) {
    const canonical = realpathForContainment(path.resolve(targetPath));
    for (const [grantId, candidate] of userPaths) {
      if (isPathInside(canonical, candidate)) return grantId;
    }
    return "";
  }

  function trustPath(targetPath) {
    const handle = grantPath(targetPath);
    if (handle && typeof persistTrustedRoot === "function") persistTrustedRoot(handle.path);
    return handle;
  }

  function renewPath(targetPath) {
    const candidate = path.resolve(targetPath);
    for (const root of offlineTrustedRoots) {
      if (isPathInside(candidate, path.resolve(root))) restoreTrustedPath(root);
    }
    const grantId = grantForPath(candidate);
    resolveAllowedPath(candidate, { grantId, mustExist: true });
    return grantId ? { path: candidate, grantId } : grantPath(candidate);
  }

  function restoreTrustedPath(root) {
    try {
      const handle = grantPath(root);
      offlineTrustedRoots.delete(root);
      return handle;
    } catch {
      offlineTrustedRoots.add(root);
      return null;
    }
  }

  for (const root of trustedRoots) restoreTrustedPath(root);

  function resolveAllowedPath(targetPath, { grantId = "", mustExist = false } = {}) {
    const dataDir = getDataDir();
    const candidate = resolveCandidatePath(targetPath, dataDir);
    const roots = [approvedDataRoot()].filter(Boolean);
    if (grantId) {
      const userPath = userPaths.get(grantId);
      if (userPath) {
        const canonical = mustExist ? realpathExisting(candidate) : realpathForContainment(candidate);
        if (!isPathInside(canonical, userPath)) throw new Error("Path is outside the selected directory.");
        return candidate;
      }
      const grantedRoot = grantedRoots.get(grantId);
      if (!grantedRoot) {
        throw new Error("Path grant is invalid or expired.");
      }
      roots.push(grantedRoot);
    }
    if (!isPathInsideAnyRoot(candidate, roots, { mustExist })) {
      throw new Error("Path is outside the approved Raticode desktop roots.");
    }
    return candidate;
  }

  // Desktop actions run as the user. Resolving their paths must not issue or
  // require the separate grants used by Rem and workflow agents.
  function resolveDesktopPath(targetPath, { mustExist = false } = {}) {
    const candidate = resolveCandidatePath(targetPath, getDataDir());
    return mustExist ? realpathExisting(candidate) : realpathForContainment(candidate);
  }

  function resolveAllowedChildPath(directory, name, { grantId = "", desktop = false } = {}) {
    if (!directory || typeof directory !== "string") {
      throw new Error("A directory is required.");
    }
    if (!name || typeof name !== "string") {
      throw new Error("A name is required.");
    }
    const cleanName = name.trim();
    if (!cleanName || cleanName.includes("/") || cleanName.includes("\\") || cleanName === "." || cleanName === "..") {
      throw new Error("Use a plain file or folder name.");
    }
    const resolve = desktop ? resolveDesktopPath : resolveAllowedPath;
    const parent = resolve(directory, { grantId, mustExist: true });
    return resolve(path.join(parent, cleanName), { grantId, mustExist: false });
  }

  function resolvePickerPath(currentPath, { grantId = "" } = {}) {
    const candidate = resolveCandidatePath(currentPath, getDataDir());
    if (grantId) {
      try {
        return existingPickerDirectory(
          resolveAllowedPath(currentPath, { grantId, mustExist: false }),
          getDataDir(),
        );
      } catch {
        // The picker is allowed to start outside approved roots; selecting a path
        // creates an explicit grant before any read/write operation can use it.
      }
    }
    return existingPickerDirectory(candidate, getDataDir());
  }

  function existingPickerDirectory(candidate, fallbackPath) {
    if (fs.existsSync(candidate)) {
      try {
        return fs.statSync(candidate).isDirectory() ? candidate : path.dirname(candidate);
      } catch {
        return path.dirname(candidate);
      }
    }
    let parent = path.dirname(candidate);
    while (parent && parent !== path.dirname(parent)) {
      if (fs.existsSync(parent)) {
        return realpathIfPossible(parent);
      }
      parent = path.dirname(parent);
    }
    return path.resolve(fallbackPath);
  }

  function grantForPath(targetPath) {
    const candidate = realpathForContainment(path.resolve(targetPath));
    for (const [grantId, root] of grantedRoots.entries()) {
      if (isPathInside(candidate, root)) {
        return grantId;
      }
    }
    return "";
  }

  function findGrantIdForRoot(root) {
    for (const [grantId, grantedRoot] of grantedRoots.entries()) {
      if (grantedRoot === root) {
        return grantId;
      }
    }
    return "";
  }

  return {
    assertTrustedSender,
    restoreTrustedPath,
    grantForPath,
    grantUserPath,
    userGrantForPath,
    isUserGrant: (grantId) => userPaths.has(grantId),
    grantPath,
    resolveDesktopPath,
    resolveAllowedChildPath,
    resolveAllowedPath,
    resolvePickerPath,
    secureHandler,
    trustPath,
    renewPath,
  };
}

function isTrustedSenderUrl(value, { appRoot, appRoots, devServerUrl, isProduction = false } = {}) {
  try {
    const url = new URL(value);
    if (url.protocol === "file:") {
      const roots = Array.isArray(appRoots) ? appRoots : [appRoot].filter(Boolean);
      if (!roots.length) return isProduction;
      const filePath = path.resolve(fileURLToPath(url));
      return roots.some((root) => isPathInside(filePath, path.resolve(root)));
    }
    if (isProduction) return false;
    if (devServerUrl) {
      const devUrl = new URL(devServerUrl);
      if (url.origin === devUrl.origin) return true;
    }
    return LOCAL_DEV_ORIGINS.has(url.origin);
  } catch {
    return false;
  }
}

function isSafeExternalUrl(value) {
  try {
    return SAFE_EXTERNAL_SCHEMES.has(new URL(value).protocol);
  } catch {
    return false;
  }
}

function resolveCandidatePath(currentPath, dataDir) {
  if (!currentPath || typeof currentPath !== "string") {
    return path.resolve(dataDir);
  }

  const candidate = currentPath.trim();
  if (!candidate) {
    return path.resolve(dataDir);
  }

  return path.isAbsolute(candidate)
    ? path.resolve(candidate)
    : path.resolve(dataDir, candidate);
}

function isPathInsideAnyRoot(candidate, roots, { mustExist = false } = {}) {
  const checkedPath = mustExist ? realpathExisting(candidate) : realpathForContainment(candidate);
  return roots.some((root) => isPathInside(checkedPath, root));
}

function isPathInside(child, root) {
  const relative = path.relative(root, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function realpathExisting(targetPath) {
  try {
    return fs.realpathSync.native(targetPath);
  } catch (error) {
    const message = ["ENOENT", "ENOTDIR"].includes(error.code)
      ? `Path does not exist: ${targetPath}`
      : `Unable to resolve path: ${targetPath}`;
    throw new Error(message, { cause: error });
  }
}

function realpathForContainment(targetPath) {
  // existsSync follows links, so it hides broken links. Only ENOENT from lstat
  // may represent a missing component; a link must resolve or fail closed.
  const missingSegments = [];
  let current = path.resolve(targetPath);
  while (true) {
    try {
      fs.lstatSync(current);
      return path.resolve(realpathExisting(current), ...missingSegments);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      const parent = path.dirname(current);
      if (parent === current) throw error;
      missingSegments.unshift(path.basename(current));
      current = parent;
    }
  }
}

function realpathIfPossible(targetPath) {
  try {
    return fs.realpathSync.native(targetPath);
  } catch {
    return path.resolve(targetPath);
  }
}

function fileUrlForPath(targetPath) {
  return pathToFileURL(targetPath).toString();
}

module.exports = {
  createIpcSecurity,
  fileUrlForPath,
  isSafeExternalUrl,
  isTrustedSenderUrl,
  realpathForContainment,
  isPathInside,
};
