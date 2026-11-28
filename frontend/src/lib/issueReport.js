import packageMetadata from "../../package.json";

// Help › Report a Problem and the crash screens' Open an issue build their GitHub
// link here. Only the details below go into a report: never paths, project names
// or settings, which developer.info() also returns.
export const DOCUMENTATION_URL = "https://raticode.dev/docs/getting-started";
export const TROUBLESHOOTING_URL = "https://raticode.dev/docs/reference/troubleshooting";

const ISSUE_URL = `${packageMetadata.bugs.url}/new`;
const OS_NAMES = { darwin: "macOS", linux: "Linux", win32: "Windows" };

export function newIssueUrl({ title = "", body }) {
  const query = [
    title ? `title=${encodeURIComponent(title)}` : "",
    `body=${encodeURIComponent(body)}`,
  ].filter(Boolean).join("&");
  return `${ISSUE_URL}?${query}`;
}

// developer.info() gives platform as "linux x64"; a browser build has no bridge.
export function appDetails(info = null) {
  const [platform = "", arch = ""] = String(info?.platform || "").split(" ");
  return {
    version: packageMetadata.version,
    os: OS_NAMES[platform] || platform || "Unavailable",
    arch: arch || "Unavailable",
    electron: String(info?.electron || "") || "Not running in the desktop app",
  };
}

export async function loadAppDetails() {
  try {
    return appDetails(await window.goferDesktop?.developer?.info?.());
  } catch {
    return appDetails();
  }
}

export function formatAppDetails(details) {
  return [
    `Raticode: ${details.version}`,
    `OS: ${details.os}`,
    `Architecture: ${details.arch}`,
    `Electron: ${details.electron}`,
  ].join("\n");
}

function reportProblemUrl(details) {
  const body = [
    "### What happened",
    "",
    "",
    "",
    "### Raticode details",
    "",
    "```text",
    formatAppDetails(details),
    "```",
  ].join("\n");
  return newIssueUrl({ body });
}

export function openHelpLink(url) {
  window.open(url, "_blank", "noopener,noreferrer");
}

export async function openReportProblem() {
  openHelpLink(reportProblemUrl(await loadAppDetails()));
}
