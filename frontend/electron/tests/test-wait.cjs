// Shared helpers for the Electron tests: polling until a condition holds, and
// finding the Chromium build Playwright downloaded to stand in for Chrome or Edge.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

// Calls check until it returns something truthy and returns that value. Throws
// an Error with message once timeout milliseconds pass. With retryErrors, a check
// that throws is tried again, and the last error's text is added to the message.
async function waitFor(check, message, { timeout = 10000, interval = 100, retryErrors = false } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await check();
      if (last) return last;
    } catch (error) {
      if (!retryErrors) throw error;
      last = error;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error(`${message}${last instanceof Error ? ` (${last.message})` : ""}`);
}

const PLAYWRIGHT_CACHE = path.join(os.homedir(), ".cache", "ms-playwright");

// The newest Chromium build in Playwright's download folder, or "" when there is
// none. Newer builds unpack to chrome-linux64, older ones to chrome-linux.
function chromiumForTesting(root = PLAYWRIGHT_CACHE) {
  let builds;
  try {
    builds = fs.readdirSync(root).filter((name) => /^chromium-\d+$/.test(name));
  } catch {
    return ""; // No Playwright downloads.
  }
  const revision = (name) => Number(name.slice("chromium-".length));
  for (const entry of builds.sort((a, b) => revision(b) - revision(a))) {
    for (const folder of ["chrome-linux64", "chrome-linux"]) {
      const candidate = path.join(root, entry, folder, "chrome");
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return "";
}

module.exports = { chromiumForTesting, waitFor };
