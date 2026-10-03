const { fileURLToPath } = require("node:url");
const path = require("node:path");

function isStudioDocument(value, { indexPath, devServerUrl, isProduction }) {
  try {
    const url = new URL(value);
    if (url.protocol === "file:") return path.resolve(fileURLToPath(url)) === path.resolve(indexPath);
    return !isProduction && url.origin === new URL(devServerUrl).origin;
  } catch { return false; }
}

function installPermissionPolicy(remoteSession, studioSession, getStudio, options) {
  remoteSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  remoteSession.setPermissionCheckHandler(() => false);
  remoteSession.setDevicePermissionHandler(() => false);
  const allowed = (contents, permission, details = {}) => {
    if (!contents || contents !== getStudio() || !isStudioDocument(contents.getURL(), options)) return false;
    if (details.isMainFrame === false) return false;
    const requestingUrl = details.requestingUrl || details.securityOrigin;
    if (requestingUrl && requestingUrl !== "file:///" && requestingUrl !== "file://" && !isStudioDocument(requestingUrl, options)) return false;
    if (permission === "clipboard-sanitized-write") return true;
    if (permission !== "media") return false;
    // Transcription needs audio only. Electron's check callback uses mediaType,
    // while the request callback supplies mediaTypes.
    if (details.mediaType) return details.mediaType === "audio";
    return Array.isArray(details.mediaTypes) && details.mediaTypes.length > 0 && details.mediaTypes.every((type) => type === "audio");
  };
  studioSession.setPermissionRequestHandler((contents, permission, callback, details) => callback(allowed(contents, permission, details)));
  studioSession.setPermissionCheckHandler((contents, permission, _origin, details) => allowed(contents, permission, details));
  studioSession.setDevicePermissionHandler(() => false);
}

function studioCsp({ apiBaseUrl, devServerUrl, isProduction }) {
  const apiOrigin = new URL(apiBaseUrl).origin;
  const devOrigin = new URL(devServerUrl).origin;
  return [
    "default-src 'self'",
    `script-src 'self'${isProduction ? "" : " 'unsafe-inline'"}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https: http: raticode-media:",
    "font-src 'self' data:",
    `connect-src 'self' ${apiOrigin}${isProduction ? "" : ` ${devOrigin} ${devOrigin.replace(/^http/, "ws")}`}`,
    "worker-src 'self' blob:",
    "media-src 'self' blob: data: raticode-media:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'none'",
    "frame-src 'none'",
  ].join("; ");
}

module.exports = { installPermissionPolicy, isStudioDocument, studioCsp };
