from __future__ import annotations

import json
import os
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
    for package_dir in ("arch", "arch-cli"):
        target = repo / "packaging" / package_dir
        target.mkdir()
        for manifest in ("PKGBUILD", ".SRCINFO"):
            shutil.copy2(
                REPO_ROOT / "packaging" / package_dir / manifest,
                target / manifest,
            )
    return repo


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


def test_bump_version_refreshes_checksums_for_existing_version(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    version = _project_version(repo)
    appimage_sha = "a" * 64
    cli_sha = "b" * 64
    args = [
        "node",
        "scripts/bump-version.cjs",
        version,
        "--appimage-sha256",
        appimage_sha,
        "--cli-sha256",
        cli_sha,
    ]
    for _ in range(2):
        result = _run(args, cwd=repo)
        assert result.returncode == 0, result.stderr
        for package, checksum in (("arch", appimage_sha), ("arch-cli", cli_sha)):
            for manifest in ("PKGBUILD", ".SRCINFO"):
                assert checksum in (repo / "packaging" / package / manifest).read_text()


def test_release_metadata_uses_agpl_3_only() -> None:
    license_id = "AGPL-3.0-only"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    assert f'license = "{license_id}"' in pyproject
    assert _read_json(REPO_ROOT / "frontend" / "package.json")["license"] == license_id
    package_lock = _read_json(REPO_ROOT / "frontend" / "package-lock.json")
    assert package_lock["packages"][""]["license"] == license_id
    assert f'license=("{license_id}")' in (REPO_ROOT / "packaging" / "arch" / "PKGBUILD").read_text(
        encoding="utf8"
    )
    assert f'license=("{license_id}")' in (
        REPO_ROOT / "packaging" / "arch-cli" / "PKGBUILD"
    ).read_text(encoding="utf8")
    assert f"License:        {license_id}" in (
        REPO_ROOT / "scripts" / "package-cli-linux.sh"
    ).read_text(encoding="utf8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (REPO_ROOT / "LICENSE").read_text(encoding="utf8")


def test_release_metadata_uses_canonical_repository() -> None:
    repository = "https://github.com/zacharyivie/gofer-flow"
    frontend_package = _read_json(REPO_ROOT / "frontend" / "package.json")

    assert frontend_package["homepage"] == repository
    assert frontend_package["build"]["publish"] == [
        {"provider": "github", "owner": "zacharyivie", "repo": "gofer-flow"}
    ]
    assert repository in (REPO_ROOT / "scripts" / "package-cli-linux.sh").read_text(encoding="utf8")


def test_bump_version_updates_manifests_and_checksums_in_fixture(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    appimage_sha = "A" * 64
    cli_sha = "b" * 64

    result = _run(
        [
            "node",
            "scripts/bump-version.cjs",
            "1.2.3",
            "--appimage-sha256",
            appimage_sha,
            "--cli-sha256",
            cli_sha,
        ],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text(encoding="utf8")
    assert 'name = "gofer-flow"\nversion = "1.2.3"' in (repo / "uv.lock").read_text(encoding="utf8")
    assert _read_json(repo / "frontend" / "package.json")["version"] == "1.2.3"
    package_lock = _read_json(repo / "frontend" / "package-lock.json")
    assert package_lock["version"] == "1.2.3"
    assert package_lock["packages"][""]["version"] == "1.2.3"

    arch_pkgbuild = (repo / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf8")
    assert "pkgver=1.2.3" in arch_pkgbuild
    assert "Raticode-${pkgver}-x86_64.AppImage" in arch_pkgbuild
    assert appimage_sha.lower() in arch_pkgbuild

    arch_srcinfo = (repo / "packaging" / "arch" / ".SRCINFO").read_text(encoding="utf8")
    assert "pkgver = 1.2.3" in arch_srcinfo
    assert (
        "source_x86_64 = Raticode-1.2.3-x86_64.AppImage::"
        "https://github.com/zacharyivie/gofer-flow/releases/download/v1.2.3/"
        "Raticode-1.2.3-x86_64.AppImage"
    ) in arch_srcinfo
    assert f"sha256sums_x86_64 = {appimage_sha.lower()}" in arch_srcinfo

    cli_pkgbuild = (repo / "packaging" / "arch-cli" / "PKGBUILD").read_text(encoding="utf8")
    assert "pkgver=1.2.3" in cli_pkgbuild
    assert cli_sha in cli_pkgbuild

    cli_srcinfo = (repo / "packaging" / "arch-cli" / ".SRCINFO").read_text(encoding="utf8")
    assert "pkgver = 1.2.3" in cli_srcinfo
    assert (
        "source_x86_64 = gof-linux-x64-1.2.3::"
        "https://github.com/zacharyivie/gofer-flow/releases/download/v1.2.3/gof-linux-x64"
    ) in cli_srcinfo
    assert f"sha256sums_x86_64 = {cli_sha}" in cli_srcinfo


def test_bump_version_without_checksums_keeps_existing_checksum_values(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    arch_pkgbuild_path = repo / "packaging" / "arch" / "PKGBUILD"
    cli_srcinfo_path = repo / "packaging" / "arch-cli" / ".SRCINFO"
    original_arch_pkgbuild = arch_pkgbuild_path.read_text(encoding="utf8")
    original_cli_srcinfo = cli_srcinfo_path.read_text(encoding="utf8")

    result = _run(["node", "scripts/bump-version.cjs", "1.2.4"], cwd=repo)

    assert result.returncode == 0, result.stderr
    assert "Note: Arch AppImage checksum was not changed" in result.stdout
    assert "Note: Arch CLI checksum was not changed" in result.stdout
    assert "pkgver=1.2.4" in arch_pkgbuild_path.read_text(encoding="utf8")
    assert "sha256sums_x86_64 = SKIP" in cli_srcinfo_path.read_text(encoding="utf8")
    assert _extract_sha_lines(original_arch_pkgbuild) == _extract_sha_lines(
        arch_pkgbuild_path.read_text(encoding="utf8"),
    )
    assert _extract_sha_lines(original_cli_srcinfo) == _extract_sha_lines(
        cli_srcinfo_path.read_text(encoding="utf8"),
    )


def _extract_sha_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "sha256sums_x86_64" in line]


def test_bump_version_rejects_invalid_arguments_before_mutating_fixture(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    original_pyproject = (repo / "pyproject.toml").read_text(encoding="utf8")
    cases = [
        ["node", "scripts/bump-version.cjs", "1"],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--appimage-sha256"],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--appimage-sha256", "abc"],
        ["node", "scripts/bump-version.cjs", "1.2.3", "--cli-sha256", "0" * 63],
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
    binary = repo / "dist" / "gof"
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
test -f "$deb_root/usr/share/doc/gofer-flow-cli/third-party-licenses/inventory.json"
test -f "$deb_root/usr/share/doc/gofer-flow-cli/third-party-licenses/LICENSE"
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
touch "$topdir/RPMS/x86_64/gofer-flow-cli-{current_version}-1.mock.x86_64.rpm"
""",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = _run(
        ["bash", "scripts/package-cli-linux.sh", str(binary), str(output_dir)],
        cwd=repo,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / f"gofer-flow-cli_{current_version}_amd64.deb").exists()
    assert (output_dir / f"gofer-flow-cli-{current_version}-1.mock.x86_64.rpm").exists()
    command_log = log_path.read_text(encoding="utf8")
    assert "dpkg-deb --build --root-owner-group" in command_log
    assert "Package: gofer-flow-cli" in command_log
    assert f"Version: {current_version}" in command_log
    assert "Architecture: amd64" in command_log
    assert "rpmbuild --define _topdir " in command_log
    assert "Name:           gofer-flow-cli" in command_log
    assert "BuildArch:      x86_64" in command_log
    assert "/usr/bin/gof" in command_log


def test_package_cli_linux_fails_early_for_missing_binary(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)

    result = _run(["bash", "scripts/package-cli-linux.sh", str(repo / "dist" / "gof")], cwd=repo)

    assert result.returncode == 1
    assert "CLI binary is missing or not executable" in result.stderr


def test_package_cli_linux_fails_when_package_builder_is_missing(tmp_path: Path) -> None:
    repo = _copy_release_fixture(tmp_path)
    binary = repo / "dist" / "gof"
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
        "args=run --locked --extra xlsx --extra devices pyinstaller --clean --noconfirm gof.spec"
        in log
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
        "args=run --locked --extra xlsx --extra devices pyinstaller --clean --noconfirm gof.spec"
        in log
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
