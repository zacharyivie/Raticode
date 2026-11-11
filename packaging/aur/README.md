# AUR packages

Each folder here is an exact copy of one AUR repository:

| Folder | AUR package | Installs |
| --- | --- | --- |
| `raticode-bin` | desktop app | the release `.deb`'s app in `/opt/raticode`, `/usr/bin/raticode`, menu entry and icons |
| `raticode-cli-bin` | command line tool | the release's `raticode-cli-linux-x64` as `/usr/bin/Raticode` |

Both use prebuilt release files, so the AUR rules require the `-bin` suffix. Each
folder's `LICENSE` (0BSD, copied to `LICENSES/0BSD.txt` for REUSE) and `REUSE.toml`
license the packaging files themselves;
the software is AGPL-3.0-only. `known_hosts` pins aur.archlinux.org's SSH host keys
for `.github/workflows/aur.yml` and is not part of either package.

Never edit `pkgver`, `pkgrel`, `sha256sums` or `.SRCINFO` by hand. After a release is
published:

```bash
gh release download vX.Y.Z -R zacharyivie/Raticode -p checksums-linux.txt -D /path/to/scratch
python3 scripts/aur-packages.py update --version X.Y.Z --checksums /path/to/scratch/checksums-linux.txt
python3 scripts/aur-packages.py check
```

To test a build locally, copy a folder somewhere outside the repository and run
`makepkg -f` there, then `namcap PKGBUILD` and `namcap *.pkg.tar.zst`. The desktop
package always draws an `/opt` ELF error and RELRO/PIE warnings from namcap; they
come from the prebuilt Electron and PyInstaller files.

User-facing install and update steps are on the website (Installation > Arch Linux).
Release and automation steps are in `docs/releasing.md` under "AUR packages".
