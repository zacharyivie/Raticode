"""Collect installed, version-matched notices for frozen and desktop distributions."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import sys
import sysconfig
import tomllib
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def notice_file(path: Path) -> bool:
    return bool(
        re.search(r"(^|[._-])(licen[cs]es?|copying|notices?|copyright)([._-]|$)", path.name, re.I)
    )


def python_license_declaration(dist) -> str:
    classifiers = (
        dist.metadata.get_all("Classifier", []) if hasattr(dist.metadata, "get_all") else []
    )
    return (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or "; ".join(value for value in classifiers if value.startswith("License ::"))
    ).strip()


def verify_review(policy: dict, ecosystem: str, name: str, version: str, license: str) -> None:
    reviews = policy.get(ecosystem, {})
    expected = reviews.get(
        f"{name}@{version}", reviews.get(name) if ecosystem == "python" else None
    )
    if expected != {"version": version, "license": license}:
        raise ValueError(f"License review required: {ecosystem} {name} {version}: {license}")


def source_bytes(package: dict, cache: Path | None = None) -> bytes:
    """Read an optional cached sdist, always checking the locked source hash."""
    source = package["sdist"]
    if not source["url"].startswith("https://"):
        raise ValueError("Dependency source URL must use HTTPS")
    cached = cache / f"{package['name']}-{package['version']}.tar.gz" if cache else None
    if cached is not None and cached.is_file():
        content = cached.read_bytes()
    else:
        with urllib.request.urlopen(source["url"], timeout=60) as response:
            content = response.read()
    if "sha256:" + hashlib.sha256(content).hexdigest() != source["hash"]:
        raise ValueError(f"Source hash mismatch: {package['name']}")
    return content


def collect(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", output / "APPLICATION-LICENSE")
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("Python runtime LICENSE.txt is missing")
    shutil.copy2(python_license, output / "PYTHON-LICENSE.txt")
    shutil.copy2(ROOT / "packaging/licenses/README.md", output / "SOURCES.md")
    inventory = []
    policy = json.loads((ROOT / "packaging/licenses/reviewed.json").read_text())
    lock_packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    locked = {(re.sub(r"[-_.]+", "-", p["name"]).lower(), p["version"]) for p in lock_packages}
    required = set()
    pending = [
        ("gofer-flow", frozenset({"xlsx"})),
        ("pyinstaller", frozenset()),
        ("pyinstaller-hooks-contrib", frozenset()),
    ]
    visited = set()
    while pending:
        name, extras = pending.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        required.add(name)
        dist = metadata.distribution(name)
        for value in dist.requires or []:
            req = Requirement(value)
            if req.marker and not any(
                req.marker.evaluate({"extra": extra}) for extra in ("", *extras)
            ):
                continue
            dependency = re.sub(r"[-_.]+", "-", req.name).lower()
            pending.append((dependency, frozenset(req.extras)))
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized not in required or normalized == "gofer-flow":
            continue
        if (normalized, version) not in locked:
            raise ValueError(f"Python lock mismatch: {name} {version}")
        declaration = python_license_declaration(dist)
        verify_review(policy, "python", normalized, version, declaration)
        target = output / "python" / f"{normalized}-{version}"
        target.mkdir(parents=True)
        count = 0
        for file in dist.files or []:
            source = Path(dist.locate_file(file))
            if notice_file(source) and source.is_file():
                shutil.copy2(source, target / f"{count}-{source.name}")
                count += 1
        if normalized == "vosk":
            shutil.copy2(
                ROOT / "packaging/licenses" / f"vosk-{version}-COPYING", target / "COPYING"
            )
            count += 1
        for supplied in (ROOT / "packaging/licenses").glob(f"{normalized}-{version}-LICENSE"):
            shutil.copy2(supplied, target / supplied.name)
            count += 1
        license_text = dist.metadata.get("License", "")
        if not count and len(license_text) > 200:
            (target / "LICENSE").write_text(license_text, encoding="utf-8")
            count += 1
        if not count:
            # Some wheels keep their notice in package data rather than RECORD.
            for file in dist.files or []:
                if str(file).endswith("METADATA"):
                    (target / "METADATA").write_text(
                        dist.read_text("METADATA") or "", encoding="utf-8"
                    )
            raise ValueError(f"Missing Python license text: {name} {version}")
        inventory.append(
            {"ecosystem": "python", "name": name, "version": version, "license": declaration}
        )
    # Ship exact MPL source distributions with the notices, using lockfile hashes.
    cache_setting = os.environ.get("GOFER_LICENSE_SOURCE_CACHE")
    source_cache = Path(cache_setting).expanduser() if cache_setting else None
    for package in lock_packages:
        if package["name"] not in {"certifi", "tqdm"} or package["name"] not in required:
            continue
        content = source_bytes(package, source_cache)
        directory = output / "sources"
        directory.mkdir(exist_ok=True)
        (directory / f"{package['name']}-{package['version']}.tar.gz").write_bytes(content)
    (output / "source-inventory.json").write_text(
        json.dumps(
            [
                {"name": p["name"], "version": p["version"], "sdist": p.get("sdist")}
                for p in lock_packages
                if p["name"] in required
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    for location, entry in sorted(lock["packages"].items()):
        if (
            not location
            or entry.get("link")
            or (entry.get("dev") and location != "node_modules/electron")
        ):
            continue
        package = ROOT / "frontend" / location
        if not package.exists():
            if entry.get("optional") or entry.get("peer"):
                continue
            raise ValueError(f"Missing npm package: {location}")
        manifest = json.loads((package / "package.json").read_text())
        if manifest["version"] != entry["version"]:
            raise ValueError(f"npm lock mismatch: {location}")
        verify_review(
            policy, "npm", manifest["name"], manifest["version"], manifest.get("license", "")
        )
        target = output / "npm" / location.replace("/", "__")
        target.mkdir(parents=True)
        notices = [p for p in package.iterdir() if p.is_file() and notice_file(p)]
        # Include vendored native notices (node-pty, Electron, etc.).
        for folder in ("third_party", "deps", "dist"):
            if (package / folder).is_dir():
                notices.extend(
                    p for p in (package / folder).rglob("*") if p.is_file() and notice_file(p)
                )
        for index, source in enumerate(notices):
            shutil.copy2(source, target / f"{index}-{source.name}")
        if (
            manifest["name"] == "lodash.isequal"
            and entry.get("resolved") == "file:vendor/lodash.isequal"
        ):
            shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
            notices.append(ROOT / "LICENSE")
        if not notices:
            supplied = (
                ROOT / "packaging/licenses" / f"{manifest['name']}-{manifest['version']}-LICENSE"
            )
            if not supplied.is_file():
                raise ValueError(f"Missing npm license text: {location}")
            shutil.copy2(supplied, target / "LICENSE")
        (target / "package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        inventory.append(
            {
                "ecosystem": "npm",
                "name": manifest["name"],
                "version": manifest["version"],
                "license": manifest.get("license"),
            }
        )
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"Collected notices for {len(inventory)} installed packages in {output}")


if __name__ == "__main__":
    collect(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/third-party-licenses")
