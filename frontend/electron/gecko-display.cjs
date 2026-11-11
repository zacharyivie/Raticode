// Which display system Raticode needs when Gecko is the web browser. Gecko's
// window can only be placed inside Raticode's over X11; Wayland keeps each
// program's windows apart. On a Wayland desktop that means running Raticode
// through XWayland, which has to be decided before Chromium starts.
const fs = require("node:fs");
const path = require("node:path");

const X11_SWITCH = "--ozone-platform=x11";

function markerFile(userData) {
  return path.join(userData, "browser", "gecko-uses-x11");
}

function wantsX11(userData, { exists = fs.existsSync } = {}) {
  return Boolean(userData) && exists(markerFile(userData));
}

function setWantsX11(userData, wanted) {
  const file = markerFile(userData);
  if (wanted) {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(file, "", { mode: 0o600 });
  } else {
    fs.rmSync(file, { force: true });
  }
}

function waylandDesktop(env = process.env) {
  return Boolean(env.WAYLAND_DISPLAY) || env.XDG_SESSION_TYPE === "wayland";
}

// Whether this run has to start again on X11 before doing anything else.
function relaunchNeeded({ userData, argv = process.argv, env = process.env, platform = process.platform, exists }) {
  return platform === "linux" && waylandDesktop(env) && Boolean(env.DISPLAY)
    && !argv.includes(X11_SWITCH) && wantsX11(userData, { exists });
}

module.exports = { X11_SWITCH, relaunchNeeded, setWantsX11, waylandDesktop, wantsX11 };
