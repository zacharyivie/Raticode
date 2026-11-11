---
title: Installation
description: Install Raticode on Windows, macOS, or Linux, or use the standalone CLI.
pageClass: docs-layout
---

# Installation

Choose the desktop app for the studio, or install only `Raticode` for a server, container, or CI machine.

## Choose a download

Use the [download section](/#download) for the website's reviewed artifacts. The [latest GitHub release](https://github.com/zacharyivie/Raticode/releases/latest) is the authority for published versions, architectures, checksums, and signing status.

| Platform | Desktop package | Current published architecture |
| --- | --- | --- |
| macOS | `.dmg` or `.zip` | Apple Silicon, arm64 |
| Windows | Setup `.exe` | x64 |
| Linux | AppImage, `.deb`, `.rpm` | x64 |

Do not substitute an Apple Silicon package for an Intel Mac build. Only use an architecture actually listed in the release. Raticode Mobile, the Android app, has no public release yet. It will ship as a signed APK on its own `mobile-vX.Y.Z` releases; see [Raticode Mobile](./mobile/index.md). These desktop installers do not install it.

## Windows

Run the setup executable and choose an installation directory. The installer includes the `Raticode.exe` CLI in its `resources\backend` folder. Its **Add the Raticode CLI to my user PATH** option is enabled by default. Open a new PowerShell or Command Prompt window to pick up that PATH change:

```powershell
Raticode --help
```

Windows installers are not code-signed yet, so Windows may show an Unknown publisher or SmartScreen prompt. Confirm you obtained the exact release artifact and follow your organization's installation policy.

## macOS

Open the `.dmg`, copy Raticode to Applications, and launch it.

Releases up to 0.5.2 are not signed or notarized. If macOS blocks one, review the app-specific prompt in **System Settings > Privacy & Security**. Do not disable system-wide protection. These copies don't install updates themselves: Raticode offers **Download Mac installer** instead.

Starting with 0.5.3, the Mac app and its DMG are signed with Raticode's Apple Developer ID, notarized by Apple, and have Apple's ticket stapled to them, so they open without a Privacy & Security exception, even offline. Signed copies download and install later updates themselves.

To move from 0.5.2 or earlier to a signed release, install it by hand once: quit Raticode, open the new `.dmg`, and drag Raticode into Applications to replace the old copy. Your projects, settings and conversations stay where they are. macOS asks once more for microphone and speech recognition access, because the signature changed; after that, the permissions carry over to later signed updates.

## Linux

For an AppImage, use the exact filename you downloaded:

```bash
chmod +x ./Raticode-0.5.3-x86_64.AppImage
./Raticode-0.5.3-x86_64.AppImage
```

For a distribution package:

```bash
# Debian or Ubuntu
sudo apt install ./Raticode-0.5.3-amd64.deb

# Fedora
sudo dnf install ./Raticode-0.5.3-x86_64.rpm
```

Replace the version and filename when using another release.

### Arch Linux (AUR)

::: warning Not published yet
These two packages are ready in the repository but are not on the AUR yet. Until they are, `yay` and `paru` won't find them; use the AppImage above.
:::

Once published, install the desktop app, the command line tool, or both with an AUR helper:

```bash
yay -S raticode-bin        # desktop app, started with: raticode
yay -S raticode-cli-bin    # command line tool: Raticode
```

Both repackage the files of the matching GitHub release; nothing is compiled. `raticode-bin` installs the app in `/opt/raticode` with a `raticode` launcher and a menu entry, and `raticode-cli-bin` installs `/usr/bin/Raticode`. The desktop app bundles its own copy of the command line tool, so install `raticode-cli-bin` only if you want `Raticode` in your terminal.

Pacman owns the files of an AUR install, so Raticode doesn't update itself there. When a new release is out, its update button reads **Update 0.5.3 with pacman** (with the new version number) and opens the release notes. Update with your AUR helper instead, such as `yay -Syu`, then restart Raticode. The AppImage, `.deb` and `.rpm` builds keep updating themselves.

## Standalone CLI

Download `raticode-cli-linux-x64`, `raticode-cli-macos-arm64`, or `raticode-cli-windows-x64.exe` from the release. On Unix, give the file execute permission, rename it to `Raticode`, and put it in a directory on your PATH. On Windows, rename it to `Raticode.exe` and add its folder to your user PATH. Linux CLI-only `.deb` and `.rpm` packages are also listed in the release.

```bash
Raticode --help
Raticode rattish --help
Raticode rattish docs --format json
```

Releases up to 0.5.1 named these files `gof-*` and installed the command as `gof`. Later releases install `Raticode`, and their Linux packages replace the earlier CLI package on upgrade. Change scripts, scheduled jobs, and shell aliases that call `gof` to call `Raticode`. If you installed an earlier standalone binary by hand, delete it from your PATH. Workflows, schedules, and settings stay where they are.

The CLI binary includes the Python backend. Commands that invoke scripts or coding providers still need those interpreters and provider CLIs on the execution machine.

## Install from source

Source development needs Python 3.11 or newer and the Node version in the repository's `.nvmrc`. The current checkout selects Node 22.23.4 and npm 10.9.9.

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

On Windows, activate the environment with `.venv\Scripts\Activate.ps1` and use a Node version manager appropriate to your shell. The `xlsx` extra adds Excel support for tabular loop sources. The `devices` extra supplies device dependencies for source builds; networking remains experimental and requires opt-in.

## Updates and verification

Use the app's update controls or check the release page. An AUR install updates through pacman instead ([Arch Linux](#arch-linux-aur)). Verify an artifact against the checksum file for that platform. Keep a backup of project files and local application data before moving between incompatible versions. The [local data guide](./advanced/security.md) explains what to preserve.

Next: [Provider setup](./setup.md).

