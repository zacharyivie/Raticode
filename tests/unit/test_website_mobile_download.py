"""The website's Raticode Mobile download. The site always links the signed APK of the version
in mobile/android/version.properties; `npm run check:downloads` waits (exit 3) until that
release is public, so the deploy never ships a link to a missing file."""

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
COMPONENTS = WEBSITE / ".vitepress" / "theme" / "components"
CERT = (
    "97:13:FF:FF:09:A4:30:E1:75:B4:B7:68:39:2B:52:A4:"
    "B6:BA:96:56:BB:EC:CE:E7:35:93:08:63:E6:FA:E7:BC"
)
CERT_HEX = CERT.replace(":", "").lower()


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
CHECKS_URL = "https://example.invalid/mobile-v0.33.0/apk-checks.json"


def _check(
    mobile: dict[str, Any], responses: dict[str, Any], downloads: dict[str, Any] | None = None
) -> dict[str, Any]:
    assert NODE is not None
    module = (WEBSITE / "scripts" / "mobile-download.mjs").as_uri()
    code = (
        f"import {{ checkMobile }} from {json.dumps(module)};"
        "const [mobile, responses, downloads] = JSON.parse(process.argv[1]);"
        "const get = async (url) => (url in responses ? responses[url] : null);"
        "const download = async (url) => {"
        "  if (!(url in downloads)) throw new Error(`unexpected download ${url}`);"
        "  return downloads[url];"
        "};"
        "console.log(JSON.stringify(await checkMobile(mobile, get, download)));"
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", code, json.dumps([mobile, responses, downloads or {}])],
        capture_output=True,
        text=True,
        check=True,
    )
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _mobile(certificate: str = CERT) -> dict[str, Any]:
    return {
        "mobileRelease": {"version": "0.33.0", "tag": "mobile-v0.33.0"},
        "mobileApk": {
            "assetName": "raticode-mobile-0.33.0.apk",
            "checksums": "checksums-android.txt",
        },
        "mobileSigningCertificate": certificate,
    }


def _release(tag: str, assets: list[str], draft: bool = False) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": False,
        "assets": [
            {"name": name, "browser_download_url": f"https://example.invalid/{tag}/{name}"}
            for name in assets
        ],
    }


ASSETS = ["raticode-mobile-0.33.0.apk", "checksums-android.txt", "apk-checks.json"]
GOOD = _release("mobile-v0.33.0", ASSETS)
SIGNED = {"signed": True, "version": "0.33.0", "certificate_sha256": CERT_HEX}


@pytest.mark.parametrize("release", [None, {**GOOD, "draft": True}])
@needs_node
def test_an_unpublished_mobile_release_waits_instead_of_failing(
    release: dict[str, Any] | None,
) -> None:
    responses = {f"{API}/tags/mobile-v0.33.0": release} if release else {}
    result = _check(_mobile(), responses)
    assert result["problems"] == []
    assert result["waiting"] is True
    assert result["checked"] == 0
    assert "mobile-v0.33.0 has no public release yet" in result["note"]


@needs_node
def test_a_public_release_with_the_signed_apk_passes() -> None:
    result = _check(
        _mobile(),
        {f"{API}/tags/mobile-v0.33.0": GOOD, f"{API}?per_page=50": [GOOD]},
        {CHECKS_URL: SIGNED},
    )
    assert result == {
        "problems": [],
        "waiting": False,
        "checked": 3,
        "note": "The signed APK and its checksums exist on mobile-v0.33.0.",
    }


@needs_node
def test_a_public_release_must_have_its_apk_checksums_and_checks() -> None:
    missing = _release("mobile-v0.33.0", ["checksums-android.txt"])
    result = _check(_mobile(), {f"{API}/tags/mobile-v0.33.0": missing})
    assert result["waiting"] is False
    assert result["problems"] == [
        "missing on mobile-v0.33.0: raticode-mobile-0.33.0.apk",
        "missing on mobile-v0.33.0: apk-checks.json, which records the signing certificate",
    ]


@pytest.mark.parametrize(
    ("checks", "problem"),
    [
        (
            {**SIGNED, "certificate_sha256": "ab" * 32},
            f"mobile-v0.33.0 is signed with certificate {'ab' * 32}, but data/releases.ts shows "
            f"{CERT_HEX}. Fix mobileSigningCertificate.",
        ),
        (
            {"signed": False, "version": "0.33.0", "certificate_sha256": None},
            "mobile-v0.33.0: apk-checks.json doesn't record a signed APK",
        ),
        (
            {**SIGNED, "version": "0.32.0"},
            "mobile-v0.33.0: apk-checks.json describes version 0.32.0",
        ),
    ],
)
@needs_node
def test_the_shown_certificate_must_match_the_published_apk(
    checks: dict[str, Any], problem: str
) -> None:
    result = _check(
        _mobile(),
        {f"{API}/tags/mobile-v0.33.0": GOOD, f"{API}?per_page=50": [GOOD]},
        {CHECKS_URL: checks},
    )
    assert result["problems"] == [problem]


@needs_node
def test_a_malformed_certificate_in_the_manifest_is_a_problem_even_before_release() -> None:
    result = _check(_mobile("not-a-fingerprint"), {})
    assert result["problems"] == [
        "data/releases.ts: mobileSigningCertificate is not a SHA-256 fingerprint"
    ]


@needs_node
def test_a_newer_published_mobile_release_is_reported() -> None:
    newer = _release("mobile-v0.34.0", [])
    result = _check(
        _mobile(),
        {f"{API}/tags/mobile-v0.33.0": GOOD, f"{API}?per_page=50": [newer, GOOD]},
        {CHECKS_URL: SIGNED},
    )
    assert result["problems"] == [
        "data/releases.ts names mobile-v0.33.0, but the newest published mobile release is "
        "mobile-v0.34.0"
    ]


def _releases_ts() -> str:
    return (WEBSITE / "data" / "releases.ts").read_text(encoding="utf8")


def test_the_site_names_the_phone_version_the_release_uses() -> None:
    entry = re.search(r"^export const mobileRelease = \{(.*)\}$", _releases_ts(), re.M)
    assert entry, "mobileRelease must stay on one line for set-release"
    version = re.search(
        r"^versionName=(\S+)$",
        (REPO_ROOT / "mobile/android/version.properties").read_text(encoding="utf8"),
        re.M,
    )
    assert version
    name = version.group(1)
    assert entry.group(1).strip() == f"version: '{name}', tag: 'mobile-v{name}'"
    # There is no "published" switch: the deploy waits for the release instead.
    assert "published" not in entry.group(1)


def test_the_site_shows_the_release_signing_certificate() -> None:
    match = re.search(r"export const mobileSigningCertificate =\s*'([0-9A-F:]+)'", _releases_ts())
    assert match and match.group(1) == CERT


def test_no_page_says_the_phone_app_is_coming_soon_or_unpublished() -> None:
    sources = [
        *COMPONENTS.rglob("*.vue"),
        *(WEBSITE / "docs").rglob("*.md"),
        WEBSITE / "index.md",
        REPO_ROOT / "README.md",
    ]
    stale = re.compile(
        r"coming soon|mobile[^.\n]{0,60}(isn't|not) (released|published)|no public download",
        re.I,
    )
    hits = [
        f"{path.relative_to(REPO_ROOT)}: {line.strip()}"
        for path in sources
        for line in path.read_text(encoding="utf8").splitlines()
        if stale.search(line)
    ]
    assert hits == []


def test_the_download_card_always_links_the_apk_with_install_steps() -> None:
    card = (COMPONENTS / "landing" / "DownloadSection.vue").read_text(encoding="utf8")
    assert "mobileRelease.published" not in card and "v-else" not in card
    assert ':href="mobileAssetUrl(mobileApk.assetName)"' in card
    assert ':href="mobileAssetUrl(mobileApk.checksums)"' in card
    assert "{{ mobileSigningCertificate }}" in card
    assert "Android 10 or newer" in card
    assert "install unknown apps" in card
    assert "Phone and desktop pairing" in card
    assert "/docs/mobile/#install-the-app" in card
    docs = (WEBSITE / "docs" / "mobile" / "index.md").read_text(encoding="utf8")
    assert "## Install the app" in docs and "<MobileDownload />" in docs


def _desktop_version() -> str:
    """The desktop version the site names now; moves with every release."""
    match = re.search(r"^  version: '([0-9.]+)',$", _releases_ts(), re.M)
    assert match, "desktopRelease.version not found in data/releases.ts"
    return match.group(1)


def _next_desktop_version() -> str:
    major, minor, patch = (int(part) for part in _desktop_version().split("."))
    return f"{major}.{minor}.{patch + 1}"


def _site_copy(tmp_path: Path, mobile_version: str = "0.33.0") -> Path:
    """A copy of the site's release files inside a fake repository with its own versions."""
    repo = tmp_path / "repo"
    site = repo / "website"
    (site / "data").mkdir(parents=True)
    (site / "docs").mkdir()
    (site / "scripts").mkdir()
    for name in ("set-release.mjs", "versions.mjs", "check-versions.mjs"):
        shutil.copy(WEBSITE / "scripts" / name, site / "scripts" / name)
    (site / "data" / "releases.ts").write_text(_releases_ts(), encoding="utf8")
    (site / "index.md").write_text("# Home\n", encoding="utf8")
    (site / "docs" / "installation.md").write_text(
        f"./Raticode-{_desktop_version()}-x86_64.AppImage\n", encoding="utf8"
    )
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package.json").write_text(
        json.dumps({"version": _desktop_version()}), encoding="utf8"
    )
    (repo / "mobile" / "android").mkdir(parents=True)
    (repo / "mobile" / "android" / "version.properties").write_text(
        f"versionName={mobile_version}\nversionCode=49\n", encoding="utf8"
    )
    return site


def _node_run(site: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    return subprocess.run(
        [NODE, str(site / "scripts" / args[0]), *args[1:]],
        capture_output=True,
        text=True,
        check=False,
    )


@needs_node
def test_set_release_mobile_follows_version_properties(tmp_path: Path) -> None:
    site = _site_copy(tmp_path)
    original = (site / "data" / "releases.ts").read_text(encoding="utf8")
    result = _node_run(site, "set-release.mjs", "--mobile")
    assert result.returncode == 0, result.stderr
    changed = (site / "data" / "releases.ts").read_text(encoding="utf8")
    assert "export const mobileRelease = { version: '0.33.0', tag: 'mobile-v0.33.0' }" in changed
    # The desktop entry and the certificate are untouched.
    desktop = re.compile(r"export const desktopRelease = \{.*?\n\}", re.S)
    first, second = desktop.search(original), desktop.search(changed)
    assert first and second and first.group(0) == second.group(0)
    assert CERT in changed

    assert _node_run(site, "set-release.mjs", "--mobile", "0.33.0").returncode == 0
    for bad in ("0.34.0", "latest"):
        refused = _node_run(site, "set-release.mjs", "--mobile", bad)
        assert refused.returncode == 2, bad
    assert (site / "data" / "releases.ts").read_text(encoding="utf8") == changed


@needs_node
def test_a_desktop_release_also_points_the_site_at_the_phone_version(tmp_path: Path) -> None:
    site = _site_copy(tmp_path)
    upcoming = _next_desktop_version()
    (site.parent / "frontend" / "package.json").write_text(
        json.dumps({"version": upcoming}), encoding="utf8"
    )
    result = _node_run(site, "set-release.mjs", upcoming, "--date", "2026-11-14")
    assert result.returncode == 0, result.stderr
    assert "Raticode Mobile download: mobile-v0.33.0." in result.stdout
    changed = (site / "data" / "releases.ts").read_text(encoding="utf8")
    assert f"  version: '{upcoming}',\n  tag: 'v{upcoming}'," in changed
    assert "export const mobileRelease = { version: '0.33.0', tag: 'mobile-v0.33.0' }" in changed
    checked = _node_run(site, "check-versions.mjs")
    assert checked.returncode == 0, checked.stderr


@needs_node
def test_a_prerelease_phone_version_leaves_the_download_alone(tmp_path: Path) -> None:
    site = _site_copy(tmp_path, mobile_version="0.35.0-rc.1")
    before = re.search(r"^export const mobileRelease.*$", _releases_ts(), re.M)
    assert before
    result = _node_run(site, "set-release.mjs", _desktop_version())
    assert result.returncode == 0, result.stderr
    assert "prerelease 0.35.0-rc.1" in result.stdout
    assert before.group(0) in (site / "data" / "releases.ts").read_text(encoding="utf8")
    assert _node_run(site, "set-release.mjs", "--mobile").returncode == 2


@needs_node
def test_check_versions_refuses_a_site_that_drifted_from_the_release_sources(
    tmp_path: Path,
) -> None:
    site = _site_copy(tmp_path)
    assert _node_run(site, "set-release.mjs", "--mobile").returncode == 0
    assert _node_run(site, "check-versions.mjs").returncode == 0

    props = site.parent / "mobile" / "android" / "version.properties"
    props.write_text("versionName=0.34.0\nversionCode=50\n", encoding="utf8")
    upcoming = _next_desktop_version()
    (site.parent / "frontend" / "package.json").write_text(
        json.dumps({"version": upcoming}), encoding="utf8"
    )
    result = _node_run(site, "check-versions.mjs")
    assert result.returncode == 1
    assert f"frontend/package.json is {upcoming}" in result.stderr
    assert "mobile/android/version.properties is 0.34.0" in result.stderr


@needs_node
def test_check_versions_passes_on_the_real_repository() -> None:
    result = _node_run(WEBSITE, "check-versions.mjs")
    assert result.returncode == 0, result.stderr


# check-downloads.mjs end to end, with fetch replaced by a fake GitHub.

FAKE_FETCH = """
import { cliBinaries, desktopPlatforms, desktopRelease, mobileApk, mobileRelease } from 'RELEASES'
const scenario = JSON.parse(process.env.FAKE_GITHUB)
const api = 'https://api.github.com/repos/zacharyivie/Raticode/releases'
const desktopAssets = [
  ...desktopPlatforms.flatMap((p) => [
    p.primary.assetName, ...p.alternates.map((a) => a.assetName), p.checksums,
  ]),
  ...cliBinaries.map((b) => b.assetName),
]
const mobileAssets = [mobileApk.assetName, mobileApk.checksums, 'apk-checks.json']
  .filter((name) => name !== scenario.dropMobileAsset)
const release = (tag, names) => ({
  tag_name: tag, draft: false, prerelease: false,
  assets: names.map((name) => ({
    name, browser_download_url: `https://downloads.invalid/${tag}/${name}`,
  })),
})
const desktop = scenario.desktop === 'public' ? release(desktopRelease.tag, desktopAssets) : null
const mobile =
  scenario.mobile === 'public' ? release(mobileRelease.tag, mobileAssets)
  : scenario.mobile === 'draft' ? { ...release(mobileRelease.tag, mobileAssets), draft: true }
  : null
const routes = {
  [`${api}/tags/${desktopRelease.tag}`]: desktop,
  [`${api}/tags/${mobileRelease.tag}`]: mobile,
  [`${api}?per_page=20`]: desktop ? [desktop] : [],
  [`${api}?per_page=50`]: [mobile, desktop].filter(Boolean),
  [`https://downloads.invalid/${mobileRelease.tag}/apk-checks.json`]: {
    signed: true, version: mobileRelease.version, certificate_sha256: scenario.certificate,
  },
}
globalThis.fetch = async (url, options = {}) => {
  if (String(url).startsWith('https://downloads.invalid/') && options.headers?.Authorization) {
    throw new Error('the token must not be sent with asset downloads')
  }
  const body = routes[url]
  if (body === undefined) throw new Error(`unexpected fetch ${url}`)
  const status = body === null ? 404 : 200
  const text = async () => JSON.stringify(body)
  return { status, ok: status === 200, json: async () => body, text }
}
"""


def _check_downloads(tmp_path: Path, **scenario: Any) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    preload = tmp_path / "fake-github.mjs"
    preload.write_text(
        FAKE_FETCH.replace("'RELEASES'", json.dumps((WEBSITE / "data" / "releases.ts").as_uri())),
        encoding="utf8",
    )
    scenario.setdefault("certificate", CERT_HEX)
    return subprocess.run(
        [NODE, "--import", preload.as_uri(), str(WEBSITE / "scripts" / "check-downloads.mjs")],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "FAKE_GITHUB": json.dumps(scenario), "GITHUB_TOKEN": "t"},
    )


@pytest.mark.parametrize(
    ("scenario", "code", "message"),
    [
        ({"desktop": "public", "mobile": "public"}, 0, "The signed APK and its checksums exist"),
        ({"desktop": "public", "mobile": "missing"}, 3, "has no public release yet"),
        ({"desktop": "public", "mobile": "draft"}, 3, "has no public release yet"),
        ({"desktop": "missing", "mobile": "public"}, 3, "has no published release yet"),
        ({"desktop": "missing", "mobile": "missing"}, 3, "every release it links is published"),
        (
            {"desktop": "public", "mobile": "public", "dropMobileAsset": "checksums-android.txt"},
            1,
            "missing on mobile-v",
        ),
        (
            {"desktop": "public", "mobile": "public", "certificate": "cd" * 32},
            1,
            "Fix mobileSigningCertificate",
        ),
    ],
)
@needs_node
def test_check_downloads_waits_for_every_release_and_fails_on_real_problems(
    tmp_path: Path, scenario: dict[str, Any], code: int, message: str
) -> None:
    assert NODE is not None
    result = _check_downloads(tmp_path, **scenario)
    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stdout + result.stderr
