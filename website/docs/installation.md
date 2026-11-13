---
title: Installation
description: Install Raticode on Windows, macOS, or Linux, or use the standalone CLI.
pageClass: docs-layout
---

# Installation

Install the desktop app for the full studio, or just the `Raticode` command for a server, container or CI machine.

## Choose a download

Get the files from the [download section](/#download), or from the [latest GitHub release](https://github.com/zacharyivie/Raticode/releases/latest), which lists every version, architecture, checksum and signing status.

| Platform | Desktop package | Architecture |
| --- | --- | --- |
| macOS | `.dmg` or `.zip` | Apple Silicon (arm64) |
| Windows | Setup `.exe` | x64 |
| Linux | AppImage, `.deb`, `.rpm` | x64 |

- There's no Intel Mac build. Only use an architecture the release lists.
- Raticode Mobile, the Android app, isn't part of these installers. It's a signed APK on its own `mobile-vX.Y.Z` release, also in the [download section](/#download). See [Install the app](./mobile/index.md#install-the-app).

## Windows

1. Run the setup `.exe` and choose a folder.
2. Keep **Add the Raticode CLI to my user PATH** on (the default). The installer puts `Raticode.exe` in its `resources\backend` folder.
3. Open a new PowerShell or Command Prompt window, so it picks up the new PATH:

```powershell
Raticode --help
```

Windows installers aren't code-signed yet, so Windows may show **Unknown publisher** or a SmartScreen prompt. Make sure you downloaded the exact release file, and follow your organization's install policy.

## macOS

Open the `.dmg`, drag Raticode into Applications, and open it from there.

| Version | Signing | Updates |
| --- | --- | --- |
| 0.5.3 and later | Signed with Raticode's Apple Developer ID, notarized, with Apple's ticket stapled. Opens without a Privacy & Security exception, even offline. | Installs updates itself |
| 0.5.2 and earlier | Not signed or notarized. If macOS blocks it, allow that app in **System Settings > Privacy & Security**. Don't turn off system-wide protection. | Offers **Download Mac installer** instead |

**Moving from 0.5.2 or earlier to a signed release** takes one manual install: quit Raticode, open the new `.dmg`, and drag Raticode into Applications to replace the old copy. Your projects, settings and conversations stay where they are. macOS asks once more for microphone and speech recognition access, because the signature changed. Later signed updates keep those permissions.

### Mac updates

A signed copy (0.5.3 or later) running from Applications updates itself in place, like the Linux AppImage, `.deb` and `.rpm`. There's no installer to open:

1. When a release is out, the top bar shows **Update** with the new version. Click it.
2. Raticode downloads the release's `.zip` and checks it against the SHA-512 hash in the release's `latest-mac.yml`. macOS then checks the new app is signed by the same Apple developer team. From 0.5.4 the button reads **Verifying update** meanwhile, and you can keep working.
3. If files have unsaved changes, Raticode asks about them. Then it quits, macOS replaces the app, and the new version opens.

If you cancel at the unsaved-changes question, the button reads **Restart to update**, and the update also installs the next time you quit.

Your data stays put. Projects, settings, conversations and Rem's history live in your project folders and `~/Library/Application Support` (or the data folder you chose), never inside the app; see [local data](./advanced/security.md). Microphone and speech permissions carry over, because the app's ID and signing team don't change between signed releases.

**When the app can't replace itself.** A copy running from the DMG, or from the read-only copy macOS makes when you open an app straight from Downloads, can't update in place. From 0.5.4 it offers **Download Mac installer** and its tooltip says why. Quit Raticode, drag it into Applications, and open it from there; later updates then install themselves.

**Download Mac installer** downloads the DMG for your Mac. Quit Raticode, open the DMG, and drag Raticode into Applications to replace the old copy.

**When an update fails**, Raticode says so instead of reporting that it's up to date, and nothing is installed. From 0.5.4 the message names the cause:

- no internet connection
- GitHub limiting requests from your network
- a download that doesn't match its published hash
- macOS refusing an update whose signature doesn't match

The full error is in the app log under **Settings > Developer**.

## Linux

AppImage (use the exact file name you downloaded):

```bash
chmod +x ./Raticode-0.5.4-x86_64.AppImage
./Raticode-0.5.4-x86_64.AppImage
```

Distribution packages:

```bash
# Debian or Ubuntu
sudo apt install ./Raticode-0.5.4-amd64.deb

# Fedora
sudo dnf install ./Raticode-0.5.4-x86_64.rpm
```

Change the version in the file name for another release. The AppImage, `.deb` and `.rpm` builds update themselves.

::: warning 0.5.3 and earlier `.deb` and `.rpm` files
These packages installed some of the app's files, including its main program, so that any account on the computer could change them. If other people have accounts on your computer, run this once after installing one of them:

```bash
sudo chmod -R go-w /opt/Raticode
```

Packages from 0.5.4 on install every file with the right permissions. If you ever installed 0.5.3 or earlier, run the command once anyway, even after updating. The AppImage, the Arch packages and the CLI-only packages aren't affected.
:::

### Arch Linux (AUR)

::: warning Not published yet
These two packages are ready in the repository but aren't on the AUR yet, so `yay` and `paru` won't find them. Use the AppImage until they are.
:::

Once published, install the desktop app, the command line tool, or both:

```bash
yay -S raticode-bin        # desktop app, started with: raticode
yay -S raticode-cli-bin    # command line tool: Raticode
```

- Both repackage the files of the matching GitHub release; nothing is compiled.
- `raticode-bin` installs the app in `/opt/raticode` with a `raticode` launcher and a menu entry.
- `raticode-cli-bin` installs `/usr/bin/Raticode`. The desktop app already bundles the CLI, so you only need this for `Raticode` in your terminal.

Pacman owns the files, so an AUR install doesn't update itself. When a release is out, the update button reads **Update 0.5.3 with pacman** (with the new version) and opens the release notes. Update with your AUR helper, such as `yay -Syu`, then restart Raticode.

## Standalone CLI

Download `raticode-cli-linux-x64`, `raticode-cli-macos-arm64` or `raticode-cli-windows-x64.exe` from the release.

- **Linux and macOS:** make it executable, rename it to `Raticode`, and put it in a folder on your PATH.
- **Windows:** rename it to `Raticode.exe` and add its folder to your user PATH.
- **Linux packages:** CLI-only `.deb` and `.rpm` files are in the release too.

```bash
Raticode --help
Raticode rattish --help
Raticode rattish docs --format json
```

The CLI includes the Python backend. Scripts and coding providers still need their own interpreters and CLIs on the machine that runs them.

::: info Upgrading from 0.5.1 or earlier
Those releases named the files `gof-*` and installed the command as `gof`. Later releases install `Raticode`, and their Linux packages replace the old CLI package on upgrade. Change scripts, scheduled jobs and aliases that call `gof`, and delete any old `gof` binary you installed by hand. Workflows, schedules and settings stay where they are.
:::

## Install from source

You need Python 3.11 or newer and the Node version in the repository's `.nvmrc` (currently Node 22.23.4 with npm 10.9.9).

```bash
git clone https://github.com/zacharyivie/Raticode.git
cd Raticode
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
source "$HOME/.nvm/nvm.sh"
nvm use
cd frontend
npm ci --include=dev
npm run electron:dev
```

- On Windows, activate the environment with `.venv\Scripts\Activate.ps1` and use a Node version manager for your shell.
- The `xlsx` extra adds Excel support for tabular loop sources.
- The `devices` extra adds device dependencies for source builds. Networking stays experimental and opt-in.

## Verify a download

Each release has a checksum file per platform: `checksums-linux.txt`, `checksums-macos.txt` and `checksums-windows.txt`. Download it from the same release, into the same folder as your file.

```bash
# Linux: prints OK for each file you downloaded
sha256sum --check --ignore-missing checksums-linux.txt

# macOS: compare the result with the file's line in checksums-macos.txt
shasum -a 256 Raticode-0.5.4-arm64.dmg
```

```powershell
# Windows: compare the result with the file's line in checksums-windows.txt
(Get-FileHash .\Raticode-Setup-0.5.4.exe).Hash.ToLower()
```

Release files also carry GitHub build provenance, which shows they were built by Raticode's release workflow from the repository's source. With the [GitHub CLI](https://cli.github.com/) signed in:

```bash
gh attestation verify ./Raticode-0.5.4-x86_64.AppImage --repo zacharyivie/Raticode
```

## Updates and backups

- Use the app's **Update** button, or watch the [release page](https://github.com/zacharyivie/Raticode/releases).
- On a Mac, see [Mac updates](#mac-updates). An AUR install updates through pacman ([Arch Linux](#arch-linux-aur)).
- Before moving between incompatible versions, back up your project files and Raticode's data folder. [Local data](./advanced/security.md#stored-data) lists where it lives.

Next: [Provider setup](./setup.md).
