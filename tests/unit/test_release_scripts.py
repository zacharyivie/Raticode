from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_release_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "packaging").mkdir()

    for script in (
        "bump-version.cjs",
        "build-backend-binary.cjs",
        "build-backend-binary.sh",
        "check-frontend-build.sh",
        "package-cli-linux.sh",
    ):
        shutil.copy2(REPO_ROOT / "scripts" / script, repo / "scripts" / script)

    shutil.copy2(REPO_ROOT / "pyproject.toml", repo / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "uv.lock", repo / "uv.lock")
    shutil.copy2(REPO_ROOT / "LICENSE", repo / "LICENSE")
    shutil.copy2(REPO_ROOT / "frontend" / "package.json", repo / "frontend" / "package.json")
    shutil.copy2(
        REPO_ROOT / "frontend" / "package-lock.json",
        repo / "frontend" / "package-lock.json",
    )
    shutil.copytree(REPO_ROOT / "packaging" / "aur", repo / "packaging" / "aur")
    return repo


def _aur_snapshot(repo: Path) -> dict[str, bytes]:
    root = repo / "packaging" / "aur"
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_mock_bin(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf8")
    path.chmod(0o755)


def _read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf8")))


def _project_version(repo: Path) -> str:
    document = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf8"))
    return cast(str, document["project"]["version"])


def test_release_metadata_uses_agpl_3_only() -> None:
    license_id = "AGPL-3.0-only"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    assert f'license = "{license_id}"' in pyproject
    assert _read_json(REPO_ROOT / "frontend" / "package.json")["license"] == license_id
    package_lock = _read_json(REPO_ROOT / "frontend" / "package-lock.json")
    assert package_lock["packages"][""]["license"] == license_id
    for package in ("raticode-bin", "raticode-cli-bin"):
        pkgbuild = REPO_ROOT / "packaging" / "aur" / package / "PKGBUILD"
        assert f"license=('{license_id}')" in pkgbuild.read_text(encoding="utf8")
    assert f"License:        {license_id}" in (
        REPO_ROOT / "scripts" / "package-cli-linux.sh"
    ).read_text(encoding="utf8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (REPO_ROOT / "LICENSE").read_text(encoding="utf8")


def test_release_metadata_uses_canonical_repository() -> None:
    repository = "https://github.com/zacharyivie/Raticode"
    frontend_package = _read_json(REPO_ROOT / "frontend" / "package.json")

    assert frontend_package["homepage"] == repository
    assert frontend_package["build"]["publish"] == [
        {"provider": "github", "owner": "zacharyivie", "repo": "Raticode"}
    ]
    assert repository in (REPO_ROOT / "scripts" / "package-cli-linux.sh").read_text(encoding="utf8")
    assert f'RELEASES_URL = "{repository}/releases"' in (
        REPO_ROOT / "frontend" / "electron" / "main.js"
    ).read_text(encoding="utf8")

    # Every repository link a release ships or publishes must name the same
    # repository, so updates, packages and crash reports never split.
    release_files = [
        "frontend/package.json",
        "frontend/electron/main.js",
        "scripts/bump-version.cjs",
        "scripts/package-cli-linux.sh",
        "packaging/aur/raticode-bin/PKGBUILD",
        "packaging/aur/raticode-bin/.SRCINFO",
        "packaging/aur/raticode-cli-bin/PKGBUILD",
        "packaging/aur/raticode-cli-bin/.SRCINFO",
        "scripts/aur-packages.py",
        ".github/workflows/aur.yml",
        "packaging/licenses/README.md",
        "docs/releasing.md",
        "docs/security/release-verification.md",
        "website/.vitepress/config.ts",
        "website/data/releases.ts",
    ]
    for name in release_files:
        text = (REPO_ROOT / name).read_text(encoding="utf8")
        for owner_repo in re.findall(
            r"github\.com/zacharyivie/([A-Za-z0-9_.-]*[A-Za-z0-9_-])", text
        ):
            assert owner_repo.removesuffix(".git") == "Raticode", f"{name}: {owner_repo}"
        assert "--repo zacharyivie/" not in text or "--repo zacharyivie/Raticode" in text, name


def test_bump_version_updates_manifests_and_leaves_aur_packages_alone(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    aur_before = _aur_snapshot(repo)

    result = _run(["node", "scripts/bump-version.cjs", "1.2.3"], cwd=repo)

    assert result.returncode == 0, result.stderr
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text(encoding="utf8")
    assert 'name = "raticode"\nversion = "1.2.3"' in (repo / "uv.lock").read_text(encoding="utf8")
    assert _read_json(repo / "frontend" / "package.json")["version"] == "1.2.3"
    package_lock = _read_json(repo / "frontend" / "package-lock.json")
    assert package_lock["version"] == "1.2.3"
    assert package_lock["packages"][""]["version"] == "1.2.3"
    # The AUR packages describe the last published release until its checksum file exists.
    assert _aur_snapshot(repo) == aur_before
    assert "still describe the last published release" in result.stdout


def test_bump_version_refuses_typed_in_checksums(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    original_pyproject = (repo / "pyproject.toml").read_text(encoding="utf8")
    for flag in ("--appimage-sha256", "--cli-sha256"):
        result = _run(["node", "scripts/bump-version.cjs", "1.2.3", flag, "a" * 64], cwd=repo)

        assert result.returncode == 1
        assert f"{flag} is no longer accepted" in result.stderr
        assert "scripts/aur-packages.py update" in result.stderr
        assert (repo / "pyproject.toml").read_text(encoding="utf8") == original_pyproject


def test_bump_version_rejects_invalid_arguments_before_mutating_fixture(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    original_pyproject = (repo / "pyproject.toml").read_text(encoding="utf8")
    cases = [
        ["node", "scripts/bump-version.cjs", "1"],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--appimage-sha256"],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--cli-sha256", "0" * 64],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--surprise"],
    ]

    for args in cases:
        result = _run(args, cwd=repo)

        assert result.returncode != 0
        assert (repo / "pyproject.toml").read_text(encoding="utf8") == original_pyproject


def test_bump_version_fails_when_expected_manifest_pattern_is_missing(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    current_version = _project_version(repo)
    pyproject_path = repo / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf8").replace(
            f'version = "{current_version}"',
            f"version: {current_version}",
        ),
        encoding="utf8",
    )

    result = _run(["node", "scripts/bump-version.cjs", "1.2.3"], cwd=repo)

    assert result.returncode == 1
    assert "Could not find pyproject.toml project version" in result.stderr


def test_package_cli_linux_uses_mocked_package_builders(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    current_version = _project_version(repo)
    binary = repo / "dist" / "Raticode"
    binary.parent.mkdir()
    binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf8")
    binary.chmod(0o755)
    notices = binary.parent / "third-party-licenses"
    notices.mkdir()
    (notices / "inventory.json").write_text('[{"name":"fixture","version":"1"}]')
    (notices / "LICENSE").write_text("fixture notice")
    output_dir = repo / "release"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "commands.log"

    _write_mock_bin(
        bin_dir,
        "dpkg-deb",
        f"""echo "dpkg-deb $*" >>"{log_path}"
deb_root=""
for arg in "$@"; do
  if [[ "$arg" == */deb ]]; then
    deb_root="$arg"
  fi
done
cat "$deb_root/DEBIAN/control" >>"{log_path}"
test -f "$deb_root/usr/share/doc/raticode-cli/third-party-licenses/inventory.json"
test -f "$deb_root/usr/share/doc/raticode-cli/third-party-licenses/LICENSE"
out="${{@: -1}}"
mkdir -p "$(dirname "$out")"
touch "$out"
""",
    )
    _write_mock_bin(
        bin_dir,
        "rpmbuild",
        f"""echo "rpmbuild $*" >>"{log_path}"
topdir=""
spec_path="${{@: -1}}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--define" ]]; then
    shift
    topdir="${{1#_topdir }}"
  fi
  shift || true
done
cat "$spec_path" >>"{log_path}"
test -f "$topdir/SOURCES/third-party-licenses/inventory.json"
test -f "$topdir/SOURCES/third-party-licenses/LICENSE"
mkdir -p "$topdir/RPMS/x86_64"
touch "$topdir/RPMS/x86_64/raticode-cli-{current_version}-1.mock.x86_64.rpm"
""",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = _run(
        ["bash", "scripts/package-cli-linux.sh", str(binary), str(output_dir)],
        cwd=repo,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / f"raticode-cli_{current_version}_amd64.deb").exists()
    assert (output_dir / f"raticode-cli-{current_version}-1.mock.x86_64.rpm").exists()
    command_log = log_path.read_text(encoding="utf8")
    assert "dpkg-deb --build --root-owner-group" in command_log
    assert "Package: raticode-cli" in command_log
    assert f"Version: {current_version}" in command_log
    assert "Architecture: amd64" in command_log
    assert "rpmbuild --define _topdir " in command_log
    assert "Name:           raticode-cli" in command_log
    assert "BuildArch:      x86_64" in command_log
    assert "/usr/bin/Raticode" in command_log
    assert "Conflicts: gofer-flow-cli" in command_log
    assert "Replaces: gofer-flow-cli" in command_log
    assert "Obsoletes:      gofer-flow-cli < %{version}" in command_log
    assert "/usr/bin/gof\n" not in command_log


def test_package_cli_linux_fails_early_for_missing_binary(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)

    result = _run(
        ["bash", "scripts/package-cli-linux.sh", str(repo / "dist" / "Raticode")], cwd=repo
    )

    assert result.returncode == 1
    assert "CLI binary is missing or not executable" in result.stderr


def test_package_cli_linux_fails_when_package_builder_is_missing(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    binary = repo / "dist" / "Raticode"
    binary.parent.mkdir()
    binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf8")
    binary.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_mock_bin(
        bin_dir,
        "dpkg-deb",
        "echo dpkg-deb should not run >&2\nexit 99\n",
    )
    _write_mock_bin(
        bin_dir,
        "dirname",
        'target="${1%/*}"\n[[ "$target" == "$1" ]] && target="."\necho "$target"\n',
    )
    env = {"PATH": str(bin_dir)}

    result = _run(["/bin/bash", "scripts/package-cli-linux.sh", str(binary)], cwd=repo, env=env)

    assert result.returncode == 1
    assert "rpmbuild is required to create the RPM package" in result.stderr


def test_build_backend_binary_cjs_invokes_uv_with_pyinstaller_and_cache(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "uv.log"
    _write_mock_bin(
        bin_dir,
        "uv",
        f"""echo "cwd=$PWD" >"{log_path}"
echo "UV_CACHE_DIR=${{UV_CACHE_DIR}}" >>"{log_path}"
echo "args=$*" >>"{log_path}"
""",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("UV_CACHE_DIR", None)

    result = _run(["node", "scripts/build-backend-binary.cjs"], cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    log = log_path.read_text(encoding="utf8")
    assert f"cwd={repo}" in log
    assert f"UV_CACHE_DIR={repo / '.uv-cache'}" in log
    assert (
        "args=run --locked --extra xlsx --extra devices pyinstaller --clean --noconfirm "
        "raticode.spec" in log
    )


def test_build_backend_binary_sh_invokes_uv_with_tmp_cache(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "uv.log"
    _write_mock_bin(
        bin_dir,
        "uv",
        f"""echo "cwd=$PWD" >"{log_path}"
echo "UV_CACHE_DIR=${{UV_CACHE_DIR}}" >>"{log_path}"
echo "args=$*" >>"{log_path}"
""",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("UV_CACHE_DIR", None)

    result = _run(["bash", "scripts/build-backend-binary.sh"], cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    log = log_path.read_text(encoding="utf8")
    assert f"cwd={repo}" in log
    assert "UV_CACHE_DIR=/tmp/uv-cache" in log
    assert (
        "args=run --locked --extra xlsx --extra devices pyinstaller --clean --noconfirm "
        "raticode.spec" in log
    )


def test_check_frontend_build_invokes_npm_in_frontend(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "npm.log"
    _write_mock_bin(
        bin_dir,
        "npm",
        f"""echo "cwd=$PWD" >"{log_path}"
echo "args=$*" >>"{log_path}"
""",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = _run(["bash", "scripts/check-frontend-build.sh"], cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    log = log_path.read_text(encoding="utf8")
    assert f"cwd={repo / 'frontend'}" in log
    assert "args=run check:build" in log


def test_desktop_packages_exclude_electron_test_code() -> None:
    files = _read_json(REPO_ROOT / "frontend/package.json")["build"]["files"]
    assert "electron/**/*" in files
    assert "!electron/tests/**/*" in files
    assert "!electron/**/*.test.cjs" in files


def test_macos_speech_dependency_and_permissions_are_packaged() -> None:
    import plistlib

    from packaging.requirements import Requirement

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    speech = [
        Requirement(value)
        for value in project["project"]["dependencies"]
        if value.startswith("vosk")
    ]
    for platform, version in (("darwin", "0.3.44"), ("linux", "0.3.45"), ("win32", "0.3.45")):
        selected = [
            req for req in speech if req.marker and req.marker.evaluate({"sys_platform": platform})
        ]
        assert len(selected) == 1
        assert version in selected[0].specifier
    mac = _read_json(REPO_ROOT / "frontend/package.json")["build"]["mac"]
    assert "Rem" in mac["extendInfo"]["NSMicrophoneUsageDescription"]
    # macOS charges speech recognition to Raticode.app, which launches the helper.
    assert "speech recognition" in mac["extendInfo"]["NSSpeechRecognitionUsageDescription"]
    assert {
        "from": "../dist/macos-speech/raticode-speech",
        "to": "speech/raticode-speech",
    } in mac["extraResources"]
    scripts = _read_json(REPO_ROOT / "frontend/package.json")["scripts"]
    assert "build-macos-speech-helper.cjs --require" in scripts["dist:mac"]
    for field in ("entitlements", "entitlementsInherit"):
        entitlements = plistlib.loads((REPO_ROOT / "frontend" / mac[field]).read_bytes())
        assert entitlements["com.apple.security.device.audio-input"] is True
        assert entitlements["com.apple.security.cs.allow-jit"] is True
    assert '"*.dyld"' in (REPO_ROOT / "raticode.spec").read_text()
