"""raticode.dev is the website's address everywhere the repository names a homepage, while
downloads, issues and source links stay on GitHub."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE = "https://raticode.dev"
REPO = "https://github.com/zacharyivie/Raticode"


def test_python_package_links_the_website() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf8"))["project"]
    urls = project["urls"]
    assert urls["Homepage"] == f"{SITE}/"
    assert urls["Documentation"] == f"{SITE}/docs/"
    assert urls["Repository"] == REPO
    assert urls["Issues"] == f"{REPO}/issues"


def test_desktop_package_links_the_website_and_files_issues_on_github() -> None:
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf8"))
    assert package["homepage"] == SITE
    assert package["bugs"]["url"] == f"{REPO}/issues"
    assert package["repository"]["url"] == f"git+{REPO}.git"
    # The crash screen's "Open an issue" builds its address from bugs.url, not homepage.
    crash = (REPO_ROOT / "frontend/src/components/AppCrashBoundary.jsx").read_text(encoding="utf8")
    assert "packageMetadata.bugs.url" in crash
    assert "packageMetadata.homepage" not in crash


@pytest.mark.parametrize("package", ["raticode-bin", "raticode-cli-bin"])
def test_aur_packages_name_the_website_and_download_from_github(package: str) -> None:
    folder = REPO_ROOT / "packaging" / "aur" / package
    pkgbuild = (folder / "PKGBUILD").read_text(encoding="utf8")
    assert f'url="{SITE}"' in pkgbuild
    assert f"\turl = {SITE}\n" in (folder / ".SRCINFO").read_text(encoding="utf8")
    # Release assets must not be fetched from the website address.
    assert "$url" not in pkgbuild
    sources = re.findall(r"::(https://[^\"]+)", pkgbuild)
    assert sources
    assert all(source.startswith(f"{REPO}/releases/download/v$pkgver/") for source in sources)


def test_phone_app_privacy_link_is_on_the_website() -> None:
    ui = REPO_ROOT / "mobile/android/app/src/main/java/dev/raticode/mobile/ui"
    screens = ui / "DesktopScreens.kt"
    assert f'PRIVACY_POLICY_URL = "{SITE}/docs/privacy"' in screens.read_text(encoding="utf8")


def test_issue_chooser_points_at_the_docs() -> None:
    import yaml

    config = yaml.safe_load(
        (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf8")
    )
    assert config["blank_issues_enabled"] is True
    assert all(link["url"].startswith(f"{SITE}/docs/") for link in config["contact_links"])


def test_no_tracked_file_links_the_old_project_pages_address() -> None:
    # The site moved from zacharyivie.github.io/Raticode/ to raticode.dev. GitHub still
    # redirects the old address, but nothing in the repository should depend on that.
    result = subprocess.run(
        ["git", "grep", "-l", "-F", "zacharyivie.github.io/Raticode", "--", ".", ":!tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout.split() == []
