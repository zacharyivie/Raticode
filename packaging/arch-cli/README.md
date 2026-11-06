# Raticode CLI AUR Package

This directory contains the AUR metadata for `raticode-cli`, the CLI-only
package. It installs the standalone Linux release binary as `/usr/bin/Raticode`
without the Electron frontend or desktop dependencies.

After publication, Arch users can install the CLI-only package with:

```bash
yay -S raticode-cli
```

For a local package test, place the release artifact beside the `PKGBUILD`:

```bash
cp ../../frontend/release/raticode-cli-linux-x64 raticode-cli-linux-x64-0.5.1
makepkg -f
sudo pacman -U raticode-cli-0.5.1-1-x86_64.pkg.tar.zst
```

Before publishing to AUR, replace `SKIP` in `PKGBUILD` and `.SRCINFO` with the
SHA-256 checksum from the matching GitHub release.
