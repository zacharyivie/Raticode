# Arch Packaging

This directory contains the AUR packaging files for the Raticode desktop app.

## Install

After the package is published to AUR:

```bash
yay -S raticode-desktop
```

The package installs the release AppImage to `/opt/raticode-desktop/Raticode.AppImage`,
adds a `/usr/bin/raticode-desktop` launcher, and installs desktop/icon metadata.
The command line tool is a separate package, `raticode-cli`, which installs
`/usr/bin/Raticode`.

## Build Locally

The `PKGBUILD` expects a GitHub release asset named:

```text
Raticode-0.5.1-x86_64.AppImage
```

For a local pre-release test, place that file beside the `PKGBUILD`, then run:

```bash
makepkg -f
```

To install the local package:

```bash
sudo pacman -U raticode-desktop-0.5.1-1-x86_64.pkg.tar.zst
```

## Release Checksums

```text
9e20abc5d50c308344a434b528b91e9f8a360f028248cb8b1d3cd0ad4440fb08  Raticode-0.5.1-x86_64.AppImage
```

Update `sha256sums_x86_64` in `PKGBUILD` and regenerate `.SRCINFO` whenever the
release AppImage changes:

```bash
updpkgsums
makepkg --printsrcinfo > .SRCINFO
```

## Official Arch Repo

If there is enough demand after AUR publication, pursue inclusion in the official
Arch repositories.
