#!/usr/bin/env python3
"""Audit first-party names and packaging consistency without building or publishing."""

from __future__ import annotations

import json
import re
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY_FILES = {
    "frontend/electron/brand-compat.json",
    "src/gofer/utils/brand_compat.py",
    # The checker lists former names to find them.
    "scripts/check-branding.py",
}

# Prose and examples readers follow: repository Markdown, the website and the
# packaged workflow-builder skill. Raw test evidence keeps its recorded output.
DOC_SUFFIXES = {".md", ".vue", ".ts"}
DOC_PREFIXES = ("website/", "skills/", "rattish/")
DOC_EXCLUDED_PREFIXES = ("docs/security/evidence/", "website/node_modules/")
FORMER_NAMES = re.compile(
    r"gofer[ _-]?flow|goferflow|gopher[ _-]?flow|go[ _-]?for[ _-]?flow"
    r"|taskurotta|taskerota|murina[ _-]?code",
    re.IGNORECASE,
)
FORMER_SHORT_NAME = re.compile(r"(?<![\w.-])Gofer(?![\w.-])")
OLD_COMMAND = re.compile(r"(?<![\w.-])gof(?![\w.-])|\bgof-(?:linux|macos|windows)-")
# Released changelog entries, migration notes and the pinned website release
# manifest name the earlier command on purpose.
OLD_COMMAND_ALLOWED = {
    "CHANGELOG.md",
    "docs/branding.md",
    "website/docs/installation.md",
    "website/data/releases.ts",
}


def is_documentation(name: str) -> bool:
    if name.startswith(DOC_EXCLUDED_PREFIXES):
        return False
    suffix = Path(name).suffix
    if suffix == ".md":
        return True
    return name.startswith(DOC_PREFIXES) and suffix in DOC_SUFFIXES


def documentation_failures(name: str, text: str) -> list[str]:
    failures = []
    for line, value in enumerate(text.splitlines(), 1):
        if FORMER_NAMES.search(value) or FORMER_SHORT_NAME.search(value):
            if name != "website/data/releases.ts":
                failures.append(f"Former product name in documentation: {name}:{line}")
        if OLD_COMMAND.search(value) and name not in OLD_COMMAND_ALLOWED:
            failures.append(f"Former gof command in documentation: {name}:{line}")
    return failures


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
    docs: list[str] = []
    checked = 0
    docs_checked = 0
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
        if is_documentation(name):
            docs_checked += 1
            docs.extend(documentation_failures(name, text))
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
    aur_packages = runpy.run_path(str(ROOT / "scripts/aur-packages.py"))
    for name in aur_packages["PACKAGES"]:
        for problem in aur_packages["check"](ROOT / "packaging/aur" / name, None):
            failures.append(f"AUR package: {problem}")
    if "--docs-only" in sys.argv[1:]:
        print(json.dumps({
            "ok": not docs,
            "documentation_files_checked": docs_checked,
            "failures": docs,
        }, indent=2))
        return int(bool(docs))
    failures.extend(docs)
    print(json.dumps({
        "ok": not failures,
        "text_files_checked": checked,
        "documentation_files_checked": docs_checked,
        "compatibility_references": exceptions,
        "failures": failures,
    }, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
