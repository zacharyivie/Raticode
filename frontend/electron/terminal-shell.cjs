const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");

async function unixTerminalShell({ choice = "system", env = process.env, platform = process.platform, userShell, directory }) {
  if (!["system", "bash", "zsh"].includes(choice)) throw new Error("Choose the system shell, bash, or zsh.");
  if (!userShell) { try { userShell = os.userInfo().shell; } catch { /* Use the environment/default below. */ } }
  const command = choice === "system" ? userShell || env.SHELL || (platform === "darwin" ? "/bin/zsh" : "/bin/bash") : `/bin/${choice}`;
  if (!path.isAbsolute(command)) throw new Error("The login shell must be an absolute path.");
  const label = path.basename(command);
  if (label !== "zsh") return { command, label, args: ["-il"] };
  const dotdir = path.join(directory, "zsh");
  await fs.mkdir(dotdir, { recursive: true, mode: 0o700 });
  // Forward startup files to the user's real ZDOTDIR. The final rcfile restores
  // it before registering a precmd hook, preserving aliases and prompt hooks.
  const startup = name => `if [ -f "$RATICODE_USER_ZDOTDIR/${name}" ]; then source "$RATICODE_USER_ZDOTDIR/${name}"; fi\n`;
  await fs.writeFile(path.join(dotdir, ".zshenv"), [
    'ZDOTDIR="$RATICODE_USER_ZDOTDIR"', startup(".zshenv"),
    'RATICODE_USER_ZDOTDIR="${ZDOTDIR:-$HOME}"', 'ZDOTDIR="$RATICODE_INTEGRATION_ZDOTDIR"', "",
  ].join("\n"), { mode: 0o600 });
  await fs.writeFile(path.join(dotdir, ".zprofile"), [
    'ZDOTDIR="$RATICODE_USER_ZDOTDIR"', startup(".zprofile"),
    'RATICODE_USER_ZDOTDIR="${ZDOTDIR:-$HOME}"', 'ZDOTDIR="$RATICODE_INTEGRATION_ZDOTDIR"', "",
  ].join("\n"), { mode: 0o600 });
  await fs.writeFile(path.join(dotdir, ".zshrc"), [
    'ZDOTDIR="$RATICODE_USER_ZDOTDIR"', startup(".zshrc"),
    "__raticode_report_cwd() { printf '\\033]633;P;Cwd=%s\\007' \"$PWD\"; }",
    'typeset -ga precmd_functions', 'precmd_functions+=(__raticode_report_cwd)', "",
  ].join("\n"), { mode: 0o600 });
  return { command, label, args: ["-il"], env: {
    ZDOTDIR: dotdir, RATICODE_USER_ZDOTDIR: env.ZDOTDIR || os.homedir(), RATICODE_INTEGRATION_ZDOTDIR: dotdir,
  } };
}
module.exports = { unixTerminalShell };
