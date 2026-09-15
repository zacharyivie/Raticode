# Arch Packaging

This directory contains the AUR packaging files for the Raticode desktop app.

## Install

After the package is published to AUR:

```bash
yay -S gofer-flow
```

The package installs the release AppImage to `/opt/gofer-flow/gofer-flow.AppImage`,
adds a `/usr/bin/gofer-flow` launcher, and installs desktop/icon metadata.

## Build Locally

The `PKGBUILD` expects a GitHub release asset named:

```text
Raticode-0.1.0-x86_64.AppImage
```

For a local pre-release test, place that file beside the `PKGBUILD`, then run:

```bash
makepkg -f
```

To install the local package:

```bash
sudo pacman -U gofer-flow-0.1.0-1-x86_64.pkg.tar.zst
```

## Release Checksums

```text
4b25cea0265aeb1d7508ed3597c0955e51a0e77ddda40278834b75456d2f1696  Raticode-0.1.0-x86_64.AppImage
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
