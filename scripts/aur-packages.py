#!/usr/bin/env python3
"""Keep the AUR packages in packaging/aur in step with a published release.

Each folder under packaging/aur is an exact copy of one AUR repository
(raticode-bin and raticode-cli-bin). Their checksums must describe the files a
release actually published, so this script takes them only from that release's
checksums-linux.txt, which the release build computes from the built packages.
Version bumps never touch these folders: they describe the newest published
release until this script moves them to the next one.

  update  Set pkgver, take release-asset checksums from checksums-linux.txt, hash
          the local helper files, and rewrite PKGBUILD and .SRCINFO.
  check   Confirm every checksum and .SRCINFO is current. With --checksums, also
          confirm the release assets match that file.

.SRCINFO is generated here in makepkg's own field order, so this runs on any
system. When makepkg is installed, both commands also compare the result with
`makepkg --printsrcinfo` and fail on any difference.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = ROOT / "packaging" / "aur"
PACKAGES = ("raticode-bin", "raticode-cli-bin")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

SINGLE = ("pkgdesc", "pkgver", "pkgrel", "epoch", "url", "install", "changelog")
HASHES = tuple(
    f"{algo}sums" for algo in ("ck", "md5", "sha1", "sha224", "sha256", "sha384", "sha512", "b2")
)
MULTI = (
    "arch",
    "groups",
    "license",
    "checkdepends",
    "makedepends",
    "depends",
    "optdepends",
    "provides",
    "conflicts",
    "replaces",
    "noextract",
    "options",
    "backup",
    "source",
    "validpgpkeys",
    *HASHES,
)
ARCH_MULTI = (
    "source",
    "provides",
    "conflicts",
    "depends",
    "replaces",
    "optdepends",
    "makedepends",
    "checkdepends",
    *HASHES,
)
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
FUNCTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{\s*$")
VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


class PackagingError(Exception):
    pass


@dataclass
class Pkgbuild:
    path: Path
    scalars: dict[str, str]
    arrays: dict[str, list[str]]

    @property
    def name(self) -> str:
        return self.scalars["pkgname"]

    def sources(self) -> list[tuple[str, str]]:
        """(local file name, location) for every source, in .SRCINFO order."""
        result = []
        for entry in self.arrays.get("source", []):
            name, _, location = entry.partition("::")
            if not location:
                location = name
                name = location.rsplit("/", 1)[-1]
            result.append((name, location))
        return result


def parse_pkgbuild(path: Path, text: str | None = None) -> Pkgbuild:
    """Read the plain assignments of a PKGBUILD written in this repository's style.

    Supports scalars, single- or multi-line arrays of quoted words and $var or
    ${var} references to earlier scalars. Anything more needs bash, so it is refused.
    """
    scalars: dict[str, str] = {}
    arrays: dict[str, list[str]] = {}
    lines = (path.read_text(encoding="utf8") if text is None else text).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        if FUNCTION.match(line):
            depth = 1
            while index < len(lines) and depth:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue
        match = ASSIGNMENT.match(line)
        if not match:
            raise PackagingError(f"{path}: unsupported line: {line}")
        key, value = match.groups()
        if value.startswith("("):
            text = value[1:]
            while not _closes_array(text):
                if index >= len(lines):
                    raise PackagingError(f"{path}: unterminated array {key}")
                text += "\n" + lines[index]
                index += 1
            body = text[: text.rstrip().rfind(")")]
            arrays[key] = [
                _expand(word, scalars, path) for word in shlex.split(body, comments=True)
            ]
        else:
            words = shlex.split(value, comments=True)
            if len(words) > 1:
                raise PackagingError(f"{path}: {key} must be a single word")
            scalars[key] = _expand(words[0] if words else "", scalars, path)
    for required in ("pkgname", "pkgver", "pkgrel"):
        if required not in scalars:
            raise PackagingError(f"{path}: missing {required}")
    return Pkgbuild(path, scalars, arrays)


def _closes_array(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped.endswith(")"):
        return False
    try:
        shlex.split(stripped[:-1], comments=True)
    except ValueError:
        return False
    return True


def _expand(word: str, scalars: dict[str, str], path: Path) -> str:
    if "`" in word or "$(" in word:
        raise PackagingError(f"{path}: command substitution is not supported: {word}")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name not in scalars:
            raise PackagingError(f"{path}: ${name} is not defined before use")
        return scalars[name]

    return VARIABLE.sub(replace, word)


def srcinfo(build: Pkgbuild) -> str:
    """Render .SRCINFO exactly as makepkg --printsrcinfo does for a single package."""
    out = [f"pkgbase = {build.scalars.get('pkgbase', build.name)}"]

    def attr(name: str, values: list[str]) -> None:
        for value in values:
            out.append(f"\t{name} = {' '.join(value.split())}")

    for name in SINGLE:
        if name in build.scalars:
            attr(name, [build.scalars[name]])
    for name in MULTI:
        if name in build.arrays:
            attr(name, build.arrays[name])
    for arch in build.arrays.get("arch", []):
        if arch == "any":
            continue
        for name in ARCH_MULTI:
            key = f"{name}_{arch}"
            if key in build.arrays:
                attr(key, build.arrays[key])
    out += ["", f"pkgname = {build.name}", ""]
    return "\n".join(out)


def read_checksums(path: Path) -> dict[str, str]:
    """Parse a sha256sum file such as the release's checksums-linux.txt."""
    sums: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf8").splitlines(), 1):
        if not line.strip():
            continue
        match = re.match(r"^([0-9a-fA-F]{64}) [ *](.+)$", line)
        if not match:
            raise PackagingError(f"{path}:{number}: not a sha256sum line")
        name = match.group(2).strip()
        if name in sums:
            raise PackagingError(f"{path}: {name} is listed twice")
        sums[name] = match.group(1).lower()
    return sums


def expected_sums(build: Pkgbuild, release: dict[str, str] | None) -> list[str | None]:
    """sha256 for each source: local helpers are hashed, release assets come from `release`.

    Returns None for a release asset when no checksum file was given.
    """
    folder = build.path.parent
    result: list[str | None] = []
    for name, location in build.sources():
        if "://" in location:
            asset = location.rsplit("/", 1)[-1]
            if release is None:
                result.append(None)
            elif asset not in release:
                raise PackagingError(f"{build.name}: {asset} is not in the release checksums")
            else:
                result.append(release[asset])
        else:
            local = folder / location
            if not local.is_file():
                raise PackagingError(f"{build.name}: local source {name} is missing")
            result.append(hashlib.sha256(local.read_bytes()).hexdigest())
    return result


def _set_scalar(text: str, key: str, value: str) -> str:
    updated, count = re.subn(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.M)
    if count != 1:
        raise PackagingError(f"PKGBUILD has no {key}=")
    return updated


def _set_sums(text: str, sums: list[str]) -> str:
    indent = " " * len("sha256sums=(")
    block = "sha256sums=(" + f"\n{indent}".join(f"'{value}'" for value in sums) + ")"
    updated, count = re.subn(
        r"^sha256sums=\([^)]*\)", lambda _match: block, text, count=1, flags=re.M
    )
    if count != 1:
        raise PackagingError("PKGBUILD has no sha256sums=( ) array")
    return updated


def makepkg_srcinfo(folder: Path) -> str | None:
    if shutil.which("makepkg") is None:
        return None
    result = subprocess.run(
        ["makepkg", "--printsrcinfo"], cwd=folder, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise PackagingError(f"makepkg --printsrcinfo failed in {folder}: {result.stderr.strip()}")
    return result.stdout


def render(
    folder: Path, version: str, release: dict[str, str], pkgrel: int | None
) -> tuple[str, str]:
    """New PKGBUILD and .SRCINFO text for `folder`, without writing anything."""
    path = folder / "PKGBUILD"
    current = parse_pkgbuild(path)
    if pkgrel is None:
        pkgrel = int(current.scalars["pkgrel"]) if current.scalars["pkgver"] == version else 1
    text = path.read_text(encoding="utf8")
    text = _set_scalar(text, "pkgver", version)
    text = _set_scalar(text, "pkgrel", str(pkgrel))
    sums = [
        value for value in expected_sums(parse_pkgbuild(path, text), release) if value is not None
    ]
    text = _set_sums(text, sums)
    return text, srcinfo(parse_pkgbuild(path, text))


def check(folder: Path, release: dict[str, str] | None) -> list[str]:
    problems: list[str] = []
    build = parse_pkgbuild(folder / "PKGBUILD")
    if not VERSION.match(build.scalars["pkgver"]):
        problems.append(f"{build.name}: pkgver {build.scalars['pkgver']} is not X.Y.Z")
    if build.name != folder.name:
        problems.append(f"{build.name}: folder is named {folder.name}")
    sums = build.arrays.get("sha256sums", [])
    sources = build.sources()
    if len(sums) != len(sources):
        problems.append(f"{build.name}: {len(sources)} sources but {len(sums)} sha256sums")
    for (name, _location), actual, expected in zip(sources, sums, expected_sums(build, release)):
        if actual == "SKIP" or not SHA256.match(actual):
            problems.append(f"{build.name}: {name} has no real sha256 ({actual})")
        elif expected is not None and actual != expected:
            problems.append(f"{build.name}: {name} sha256 is {actual}, expected {expected}")
    if "replaces" in build.arrays:
        problems.append(f"{build.name}: replaces= is only for renaming an existing AUR package")
    maintainer = build.path.read_text(encoding="utf8").splitlines()[0]
    if not maintainer.startswith("# Maintainer: ") or ".local" in maintainer:
        problems.append(f"{build.name}: first line must name a real maintainer")
    for required in ("LICENSE", "LICENSES/0BSD.txt", "REUSE.toml", ".SRCINFO"):
        if not (folder / required).is_file():
            problems.append(f"{build.name}: {required} is missing")
    if (folder / ".SRCINFO").is_file():
        recorded = (folder / ".SRCINFO").read_text(encoding="utf8")
        if recorded != srcinfo(build):
            problems.append(f"{build.name}: .SRCINFO is out of date; run update")
        generated = makepkg_srcinfo(folder)
        if generated is not None and generated != recorded:
            problems.append(f"{build.name}: .SRCINFO differs from makepkg --printsrcinfo")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--packages-dir", type=Path, default=PACKAGES_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("update", help="move the packages to a published release")
    up.add_argument("--version", required=True)
    up.add_argument(
        "--checksums", type=Path, required=True, help="the release's checksums-linux.txt"
    )
    up.add_argument("--pkgrel", type=int, help="default: 1 for a new version, unchanged otherwise")
    ck = commands.add_parser("check", help="confirm the packages are consistent")
    ck.add_argument("--checksums", type=Path, help="also compare release assets with this file")
    args = parser.parse_args(argv)

    try:
        folders = [args.packages_dir / name for name in PACKAGES]
        release: dict[str, str] | None
        if args.command == "update":
            if not VERSION.match(args.version):
                raise PackagingError(f"Version {args.version} is not X.Y.Z")
            if args.pkgrel is not None and args.pkgrel < 1:
                raise PackagingError("pkgrel must be 1 or more")
            release = read_checksums(args.checksums)
            # Render both packages before writing either, so a missing asset changes nothing.
            rendered = [
                (folder, *render(folder, args.version, release, args.pkgrel)) for folder in folders
            ]
            for folder, pkgbuild, info in rendered:
                (folder / "PKGBUILD").write_text(pkgbuild, encoding="utf8")
                (folder / ".SRCINFO").write_text(info, encoding="utf8")
                print(f"Updated {folder.name} to {args.version}")
            problems = [p for folder in folders for p in check(folder, release)]
        else:
            release = read_checksums(args.checksums) if args.checksums else None
            problems = [p for folder in folders for p in check(folder, release)]
    except (OSError, PackagingError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems and args.command == "check":
        print("AUR packages are consistent.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
