"""Exercise CLIs from final distributions on a disposable native CI runner.

This covers package extraction/installation and backend startup, not desktop UI
or upgrades from a previous release. No workflows are executed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "frontend/release"
VERSION = json.loads((ROOT / "frontend/package.json").read_text())["version"]


def run(*args: str | Path, cwd: Path | None = None) -> str:
    command = [str(arg) for arg in args]
    print(f"Running: {command!r}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1", "NO_COLOR": "1"},
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {command!r} exited with status {exc.returncode}:\n{exc.stdout}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command {command!r} timed out after {exc.timeout}s:\n{exc.stdout!r}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start {command!r}: {exc}") from exc
    return result.stdout


def probe(binary: Path) -> None:
    if not binary.is_file():
        raise RuntimeError(f"Packaged backend missing: {binary}")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise RuntimeError(f"Packaged backend is not executable: {binary}")
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(binary))
    entries = {name.replace("\\", "/"): name for name in archive.toc}
    required = "third-party-licenses/inventory.json"
    if required not in entries:
        raise RuntimeError(f"Packaged backend has no third-party notices: {binary}")
    inventory = json.loads(archive.extract(entries[required]))
    if not inventory:
        raise RuntimeError(f"Packaged backend has an empty license inventory: {binary}")
    for required in (
        "third-party-licenses/PYTHON-LICENSE.txt",
        "third-party-licenses/native-inventory.json",
    ):
        if required not in entries:
            raise RuntimeError(f"Packaged backend missing {required}: {binary}")
    # Probe away from the source checkout to catch accidental source-file dependencies.
    with tempfile.TemporaryDirectory(prefix="gof-probe-") as temporary:
        output = run(binary.resolve(), "--version", cwd=Path(temporary))
        run(binary.resolve(), "--help", cwd=Path(temporary))
        run(binary.resolve(), "ui", "serve", "--help", cwd=Path(temporary))
    if output.strip() != f"gof {VERSION}":
        raise RuntimeError(f"Wrong packaged version for {binary}: {output}")
    print(f"Verified {binary.name}: {output.strip()}")


def probe_tree(root: Path) -> None:
    binaries = [p for p in root.rglob("gof*") if p.name in {"gof", "gof.exe"} and p.is_file()]
    if not binaries:
        raise RuntimeError(f"No backend found in extracted distribution: {root}")
    for binary in binaries:
        probe(binary)


def linux_packages() -> tuple[list[Path], list[Path]]:
    # Require both desktop and standalone CLI packages, not merely any matching file.
    packages = []
    for pattern in (
        f"Raticode-{VERSION}-amd64.deb",
        f"gofer-flow-cli_{VERSION}_amd64.deb",
        f"Raticode-{VERSION}-x86_64.rpm",
        f"gofer-flow-cli-{VERSION}-1*.x86_64.rpm",
    ):
        matches = sorted(RELEASE.glob(pattern))
        if len(matches) != 1 or not matches[0].is_file():
            raise RuntimeError(
                f"Expected exactly one release package matching {pattern}: {matches}"
            )
        packages.extend(matches)
    return packages[:2], packages[2:]


def main() -> None:
    platform = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    evidence = RELEASE / f"package-tests-{platform}.json"
    evidence.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="raticode-package-test-") as temporary:
        root = Path(temporary)
        if sys.platform == "win32":
            probe(RELEASE / "gof-windows-x64.exe")
            installer = RELEASE / f"Raticode-Setup-{VERSION}.exe"
            target = root / "installed"
            # NSIS requires /D to be the final argument. No shell quoting involved.
            run(installer, "/S", f"/D={target}")
            probe_tree(target)
        elif sys.platform == "darwin":
            probe(RELEASE / "gof-macos-arm64")
            archive = RELEASE / f"Raticode-{VERSION}-arm64.zip"
            run("ditto", "-x", "-k", archive, root / "zip")
            probe_tree(root / "zip")
            mount = root / "dmg"
            run(
                "hdiutil",
                "attach",
                RELEASE / f"Raticode-{VERSION}-arm64.dmg",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                mount,
            )
            try:
                # Copy out before probing so the readonly mount is never modified.
                run("ditto", mount / "Raticode.app", root / "dmg-app/Raticode.app")
                probe_tree(root / "dmg-app")
            finally:
                run("hdiutil", "detach", mount)
        else:
            debs, rpms = linux_packages()
            probe(RELEASE / "gof-linux-x64")
            appimage = RELEASE / f"Raticode-{VERSION}-x86_64.AppImage"
            appimage.chmod(appimage.stat().st_mode | 0o111)
            run(appimage, "--appimage-extract", cwd=root)
            probe_tree(root / "squashfs-root")
            for package in debs:
                target = root / package.name
                run("dpkg-deb", "-x", package, target)
                probe_tree(target)
            for package in rpms:
                target = root / package.name
                target.mkdir()
                # bsdtar reads RPM payloads without invoking a shell pipeline.
                run("bsdtar", "-xf", package, "-C", target)
                probe_tree(target)
    evidence.write_text(
        json.dumps(
            {
                "status": "passed",
                "version": VERSION,
                "platform": platform,
                "scope": "Final distribution extraction/installation and backend version/help",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Final distribution backend smoke tests passed for {sys.platform}.")


if __name__ == "__main__":
    if os.environ.get("CI") != "true":
        raise SystemExit("Run only on disposable CI runners; Windows installs a package.")
    main()
