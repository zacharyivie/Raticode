#!/usr/bin/env python3
"""Audit first-party names and packaging consistency without building or publishing."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY_FILES = {
    "frontend/electron/brand-compat.json",
    "src/gofer/utils/brand_compat.py",
}


def main() -> int:
    legacy = json.loads((ROOT / "frontend/electron/brand-compat.json").read_text())
    brands = [item["brand"] for item in legacy["previousBrands"]]
    pattern = re.compile(
        "|".join(re.escape(brand).replace("\\-", "[ _-]?") for brand in brands),
        re.IGNORECASE,
    )
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    failures: list[str] = []
    exceptions: list[str] = []
    checked = 0
    for name in sorted(set(paths)):
        path = ROOT / name
        if not name or not path.is_file():
            continue
        if pattern.search(name):
            failures.append(f"Previous brand in filename: {name}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        checked += 1
        for line, value in enumerate(text.splitlines(), 1):
            if pattern.search(value):
                (exceptions if name in COMPATIBILITY_FILES else failures).append(f"{name}:{line}")

    package = json.loads((ROOT / "frontend/package.json").read_text())
    build = package["build"]
    for value in (build["productName"], build["linux"]["desktop"]["entry"]["Name"]):
        if value != "Raticode":
            failures.append(f"Inconsistent product name: {value}")
    for platform in ("linux", "win", "mac", "dmg"):
        if not build[platform]["artifactName"].startswith("Raticode-"):
            failures.append(f"Inconsistent {platform} artifact prefix")
    for schema in (ROOT / "rattish/schemas").glob("*.schema.json"):
        if not json.loads(schema.read_text())["$id"].startswith("urn:raticode:rattish:schema:"):
            failures.append(f"Inconsistent schema identifier: {schema.name}")
    for name in ("gofer-flow", "gofer-flow.desktop", "gofer-flow.svg"):
        digest = hashlib.sha256((ROOT / "packaging/arch" / name).read_bytes()).hexdigest()
        for metadata in ("PKGBUILD", ".SRCINFO"):
            if digest not in (ROOT / "packaging/arch" / metadata).read_text():
                failures.append(f"Stale Arch checksum: {metadata}: {name}")
    print(json.dumps({
        "ok": not failures,
        "text_files_checked": checked,
        "compatibility_references": exceptions,
        "failures": failures,
    }, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
