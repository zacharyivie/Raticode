# Raticode CLI AUR Package

This directory contains the AUR metadata for `gofer-flow-cli`, the CLI-only
package. It installs the standalone Linux release binary as `/usr/bin/gof`
without the Electron frontend or desktop dependencies.

After publication, Arch users can install the CLI-only package with:

```bash
yay -S gofer-flow-cli
```

For a local package test, place the release artifact beside the `PKGBUILD`:

```bash
cp ../../frontend/release/gof-linux-x64 gof-linux-x64-0.1.0
makepkg -f
sudo pacman -U gofer-flow-cli-0.1.0-1-x86_64.pkg.tar.zst
```

Before publishing to AUR, replace `SKIP` in `PKGBUILD` and `.SRCINFO` with the
SHA-256 checksum from the matching GitHub release.
