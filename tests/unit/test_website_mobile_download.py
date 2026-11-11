"""The website's Raticode Mobile download: the APK link appears, and is checked, only once
data/releases.ts marks that mobile release as published."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBSITE = REPO_ROOT / "website"


def _node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    # Node strips TypeScript types from imported .ts files from 22.18 on.
    module = (WEBSITE / "data" / "releases.ts").as_uri()
    probe = subprocess.run(
        [node, "--input-type=module", "-e", f"await import({json.dumps(module)})"],
        capture_output=True,
        text=True,
        check=False,
    )
    return node if probe.returncode == 0 else None


NODE = _node()
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node 22.18 or newer")

API = "https://api.github.com/repos/zacharyivie/Raticode/releases"


def _check(mobile: dict[str, Any], responses: dict[str, Any]) -> dict[str, Any]:
    assert NODE is not None
    module = (WEBSITE / "scripts" / "mobile-download.mjs").as_uri()
    code = (
        f"import {{ checkMobile }} from {json.dumps(module)};"
        "const [mobile, responses] = JSON.parse(process.argv[1]);"
        "const get = async (url) => (url in responses ? responses[url] : null);"
        "console.log(JSON.stringify(await checkMobile(mobile, get)));"
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", code, json.dumps([mobile, responses])],
        capture_output=True,
        text=True,
        check=True,
    )
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _mobile(published: bool) -> dict[str, Any]:
    return {
        "mobileRelease": {"version": "0.32.0", "tag": "mobile-v0.32.0", "published": published},
        "mobileApk": {
            "assetName": "raticode-mobile-0.32.0.apk",
            "checksums": "checksums-android.txt",
        },
    }


def _release(tag: str, assets: list[str], draft: bool = False) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": False,
        "assets": [{"name": name} for name in assets],
    }


GOOD = _release("mobile-v0.32.0", ["raticode-mobile-0.32.0.apk", "checksums-android.txt"])


@needs_node
def test_an_unpublished_mobile_release_is_not_checked_or_linked() -> None:
    result = _check(_mobile(False), {})
    assert result["problems"] == []
    assert result["checked"] == 0
    assert "not published yet" in result["note"]


@needs_node
def test_a_published_mobile_release_must_have_its_apk_and_checksums() -> None:
    ok = _check(_mobile(True), {f"{API}/tags/mobile-v0.32.0": GOOD, f"{API}?per_page=50": [GOOD]})
    assert ok["problems"] == [] and ok["checked"] == 2

    missing = _release("mobile-v0.32.0", ["checksums-android.txt"])
    result = _check(_mobile(True), {f"{API}/tags/mobile-v0.32.0": missing})
    assert result["problems"] == ["missing on mobile-v0.32.0: raticode-mobile-0.32.0.apk"]


@pytest.mark.parametrize("release", [None, {**GOOD, "draft": True}])
@needs_node
def test_published_flag_without_a_public_release_fails(release: dict[str, Any] | None) -> None:
    responses = {f"{API}/tags/mobile-v0.32.0": release} if release else {}
    result = _check(_mobile(True), responses)
    assert len(result["problems"]) == 1
    assert "has no public release" in result["problems"][0]


@needs_node
def test_a_newer_published_mobile_release_is_reported() -> None:
    newer = _release("mobile-v0.33.0", [])
    result = _check(
        _mobile(True), {f"{API}/tags/mobile-v0.32.0": GOOD, f"{API}?per_page=50": [newer, GOOD]}
    )
    assert result["problems"] == [
        "data/releases.ts names mobile-v0.32.0, but the newest published mobile release is "
        "mobile-v0.33.0"
    ]


def test_the_site_starts_with_the_phone_app_unpublished_and_matching_its_version() -> None:
    releases = (WEBSITE / "data" / "releases.ts").read_text(encoding="utf8")
    entry = re.search(r"^export const mobileRelease = \{(.*)\}$", releases, re.M)
    assert entry, "mobileRelease must stay on one line for set-release"
    version = re.search(
        r"^versionName=(\S+)$",
        (REPO_ROOT / "mobile/android/version.properties").read_text(encoding="utf8"),
        re.M,
    )
    assert version
    assert f"version: '{version.group(1)}'" in entry.group(1)
    assert f"tag: 'mobile-v{version.group(1)}'" in entry.group(1)
    # No signed phone release exists yet, so the site must not link an APK.
    assert "published: false" in entry.group(1)


@needs_node
def test_set_release_mobile_switches_only_the_mobile_entry(tmp_path: Path) -> None:
    assert NODE is not None
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "scripts").mkdir()
    shutil.copy(WEBSITE / "scripts" / "set-release.mjs", site / "scripts" / "set-release.mjs")
    original = (WEBSITE / "data" / "releases.ts").read_text(encoding="utf8")
    (site / "data" / "releases.ts").write_text(original, encoding="utf8")
    result = subprocess.run(
        [NODE, str(site / "scripts" / "set-release.mjs"), "--mobile", "0.33.0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    changed = (site / "data" / "releases.ts").read_text(encoding="utf8")
    assert (
        "export const mobileRelease = { version: '0.33.0', tag: 'mobile-v0.33.0', published: true }"
        in changed
    )
    # The desktop entry is untouched.
    desktop = re.compile(r"export const desktopRelease = \{.*?\n\}", re.S)
    first, second = desktop.search(original), desktop.search(changed)
    assert first and second and first.group(0) == second.group(0)
    bad = subprocess.run(
        [NODE, str(site / "scripts" / "set-release.mjs"), "--mobile", "latest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2
