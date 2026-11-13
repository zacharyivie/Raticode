"""The AUR packages and the script that keeps them in step with published releases."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUR = REPO_ROOT / "packaging" / "aur"
SCRIPT = REPO_ROOT / "scripts" / "aur-packages.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aur_packages", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aur_packages"] = module
    spec.loader.exec_module(module)
    return module


aur = _module()


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "aur"
    shutil.copytree(AUR, target)
    return target


def _release(tmp_path: Path, version: str, deb: str = "d" * 64, cli: str = "c" * 64) -> Path:
    path = tmp_path / f"checksums-{version}.txt"
    path.write_text(
        f"{'a' * 64}  Raticode-{version}-x86_64.AppImage\n"
        f"{deb}  Raticode-{version}-amd64.deb\n"
        f"{'b' * 64}  raticode-cli_{version}_amd64.deb\n"
        f"{cli}  raticode-cli-linux-x64\n",
        encoding="utf8",
    )
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=False
    )


def test_committed_packages_are_ready_for_the_aur() -> None:
    result = _run("check")
    assert result.returncode == 0, result.stderr
    for name in aur.PACKAGES:
        folder = AUR / name
        build = aur.parse_pkgbuild(folder / "PKGBUILD")
        assert build.name == name and name.endswith("-bin")
        assert "replaces" not in build.arrays
        assert "SKIP" not in build.arrays["sha256sums"]
        assert build.arrays["license"] == ["AGPL-3.0-only"]
        assert build.arrays["provides"] == [name.removesuffix("-bin")]
        assert build.arrays["conflicts"] == [name.removesuffix("-bin")]
        assert "!strip" in build.arrays["options"]
        assert (
            (folder / "LICENSE")
            .read_text(encoding="utf8")
            .startswith("Copyright Arch Linux Contributors\n\nPermission to use, copy, modify")
        )
        reuse = (folder / "REUSE.toml").read_text(encoding="utf8")
        assert 'SPDX-License-Identifier = "0BSD"' in reuse
        # Every file the AUR repository holds is covered by REUSE.toml and kept by .gitignore.
        # REUSE reads the license text from LICENSES/; LICENSE is the copy people look for.
        assert (folder / "LICENSES" / "0BSD.txt").read_bytes() == (folder / "LICENSE").read_bytes()
        tracked = sorted(p.name for p in folder.iterdir() if p.name not in {"LICENSE", "LICENSES"})
        ignore = (folder / ".gitignore").read_text(encoding="utf8")
        for file in tracked:
            assert f'"{file}"' in reuse, file
            assert f"!{file}" in ignore, file
        maintainer = (folder / "PKGBUILD").read_text(encoding="utf8").splitlines()[0]
        assert maintainer.startswith("# Maintainer: ") and "@" not in maintainer


def test_desktop_package_marks_itself_as_pacman_managed() -> None:
    pkgbuild = (AUR / "raticode-bin" / "PKGBUILD").read_text(encoding="utf8")
    assert "printf 'pacman\\n' > \"$pkgdir/opt/raticode/resources/package-type\"" in pkgbuild
    assert 'chmod -R u+rwX,go+rX,go-w "$pkgdir/opt/raticode"' in pkgbuild
    # The app reads the same marker before letting electron-updater replace any file.
    main = (REPO_ROOT / "frontend" / "electron" / "main.js").read_text(encoding="utf8")
    assert 'path.join(process.resourcesPath, "package-type")' in main
    assert 'if (value === "pacman") installedPackageManager = value;' in main
    launcher = (AUR / "raticode-bin" / "raticode").read_text(encoding="utf8")
    assert "exec /opt/raticode/gofer-flow" in launcher


def test_update_takes_release_checksums_and_hashes_helpers(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "raticode-bin" / "raticode").write_text("#!/bin/sh\nexec true\n", encoding="utf8")
    release = _release(tmp_path, "9.8.7", deb="1" * 64, cli="2" * 64)

    result = _run(
        "--packages-dir", str(folder), "update", "--version", "9.8.7", "--checksums", str(release)
    )

    assert result.returncode == 0, result.stderr
    desktop = aur.parse_pkgbuild(folder / "raticode-bin" / "PKGBUILD")
    assert desktop.scalars["pkgver"] == "9.8.7" and desktop.scalars["pkgrel"] == "1"
    launcher = hashlib.sha256(b"#!/bin/sh\nexec true\n").hexdigest()
    assert desktop.arrays["sha256sums"][:2] == ["1" * 64, launcher]
    srcinfo = (folder / "raticode-bin" / ".SRCINFO").read_text(encoding="utf8")
    assert (
        "\tsource = raticode-9.8.7-amd64.deb::https://github.com/zacharyivie/Raticode/"
        "releases/download/v9.8.7/Raticode-9.8.7-amd64.deb"
    ) in srcinfo
    assert f"\tsha256sums = {'1' * 64}" in srcinfo
    cli = aur.parse_pkgbuild(folder / "raticode-cli-bin" / "PKGBUILD")
    assert cli.arrays["sha256sums"] == ["2" * 64]
    assert "pkgver = 9.8.7" in (folder / "raticode-cli-bin" / ".SRCINFO").read_text(encoding="utf8")
    check = _run("--packages-dir", str(folder), "check", "--checksums", str(release))
    assert check.returncode == 0, check.stderr


def test_pkgrel_resets_for_a_new_version_and_holds_for_a_rerun(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    release = _release(tmp_path, "9.8.7")
    args = [
        "--packages-dir",
        str(folder),
        "update",
        "--version",
        "9.8.7",
        "--checksums",
        str(release),
    ]
    assert _run(*args, "--pkgrel", "3").returncode == 0
    assert _run(*args).returncode == 0
    assert aur.parse_pkgbuild(folder / "raticode-cli-bin" / "PKGBUILD").scalars["pkgrel"] == "3"
    newer = _release(tmp_path, "9.8.8")
    assert (
        _run(
            "--packages-dir", str(folder), "update", "--version", "9.8.8", "--checksums", str(newer)
        ).returncode
        == 0
    )
    assert aur.parse_pkgbuild(folder / "raticode-cli-bin" / "PKGBUILD").scalars["pkgrel"] == "1"


def test_update_refuses_a_release_without_the_packaged_assets(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    before = {name: (folder / name / "PKGBUILD").read_bytes() for name in aur.PACKAGES}
    partial = tmp_path / "partial.txt"
    partial.write_text(f"{'a' * 64}  raticode-cli-linux-x64\n", encoding="utf8")

    result = _run(
        "--packages-dir", str(folder), "update", "--version", "9.8.7", "--checksums", str(partial)
    )

    assert result.returncode == 1
    assert "Raticode-9.8.7-amd64.deb is not in the release checksums" in result.stderr
    # The CLI asset was present, but neither package moves unless both can.
    assert {name: (folder / name / "PKGBUILD").read_bytes() for name in aur.PACKAGES} == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--version", "1.2"], "is not X.Y.Z"),
        (["--version", "1.2.3", "--pkgrel", "0"], "pkgrel must be 1 or more"),
    ],
)
def test_update_rejects_bad_arguments(tmp_path: Path, args: list[str], message: str) -> None:
    release = _release(tmp_path, "1.2.3")
    result = _run(
        "--packages-dir", str(_copy(tmp_path)), "update", *args, "--checksums", str(release)
    )
    assert result.returncode == 1
    assert message in result.stderr


def test_check_catches_stale_and_unsafe_packages(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "raticode-bin" / "raticode.desktop").write_text("[Desktop Entry]\n", encoding="utf8")
    cli = folder / "raticode-cli-bin" / "PKGBUILD"
    text = cli.read_text(encoding="utf8")
    text = text.replace(
        "# Maintainer: Zack Ivie <publisheddoonk at gmail dot com>",
        "# Maintainer: Raticode <x@raticode.local>",
    )
    text = text.replace(
        "conflicts=('raticode-cli')", "conflicts=('raticode-cli')\nreplaces=('gofer-flow-cli')"
    )
    cli.write_text(text, encoding="utf8")
    other = _release(tmp_path, "0.5.2", deb="e" * 64, cli="f" * 64)

    result = _run("--packages-dir", str(folder), "check", "--checksums", str(other))

    assert result.returncode == 1
    assert "raticode-bin: raticode.desktop sha256 is" in result.stderr
    assert "raticode-bin: raticode-0.5.2-amd64.deb sha256 is" in result.stderr
    assert "raticode-cli-bin: replaces= is only for renaming" in result.stderr
    assert "raticode-cli-bin: first line must name a real maintainer" in result.stderr
    assert "raticode-cli-bin: .SRCINFO is out of date" in result.stderr


def test_check_rejects_skipped_checksums(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    cli = folder / "raticode-cli-bin" / "PKGBUILD"
    text = cli.read_text(encoding="utf8")
    start = text.index("sha256sums=(")
    end = text.index(")", start)
    cli.write_text(text[:start] + "sha256sums=('SKIP'" + text[end:], encoding="utf8")
    result = _run("--packages-dir", str(folder), "check")
    assert result.returncode == 1
    assert "raticode-cli-0.5.2 has no real sha256 (SKIP)" in result.stderr


def test_parser_refuses_shell_it_cannot_follow(tmp_path: Path) -> None:
    path = tmp_path / "PKGBUILD"
    path.write_text("pkgname=x\npkgver=$(date)\npkgrel=1\n", encoding="utf8")
    with pytest.raises(aur.PackagingError, match="command substitution"):
        aur.parse_pkgbuild(path)
    path.write_text(
        'pkgname=x\npkgver=1.0.0\npkgrel=1\nsource=("$missing/file")\n', encoding="utf8"
    )
    with pytest.raises(aur.PackagingError, match=r"\$missing is not defined"):
        aur.parse_pkgbuild(path)


@pytest.mark.skipif(shutil.which("makepkg") is None, reason="makepkg is not installed")
def test_srcinfo_matches_makepkg_for_rendered_packages(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    release = _release(tmp_path, "9.8.7")
    assert (
        _run(
            "--packages-dir",
            str(folder),
            "update",
            "--version",
            "9.8.7",
            "--checksums",
            str(release),
        ).returncode
        == 0
    )
    for name in aur.PACKAGES:
        generated = subprocess.run(
            ["makepkg", "--printsrcinfo"],
            cwd=folder / name,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert generated == (folder / name / ".SRCINFO").read_text(encoding="utf8")


def _workflow() -> dict[Any, Any]:
    document = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "aur.yml").read_text(encoding="utf8")
    )
    return cast(dict[Any, Any], document)


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_aur_workflow_runs_after_publication_and_checks_before_pushing() -> None:
    workflow = _workflow()
    triggers = workflow[True]  # YAML 1.1 reads the key "on" as true
    assert triggers["workflow_run"] == {"workflows": ["Publish release"], "types": ["completed"]}
    assert set(triggers) == {"workflow_run", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert "github.repository == 'zacharyivie/Raticode'" in jobs["render"]["if"]
    assert "workflow_run.conclusion == 'success'" in jobs["render"]["if"]
    verify = _step(jobs["render"], "Download and verify the release's checksum file")["run"]
    assert "gh attestation verify release/checksums-linux.txt" in verify
    assert "/.github/workflows/release-candidate.yml" in verify
    render = _step(jobs["render"], "Render the AUR packages")["run"]
    assert (
        'aur-packages.py update --version "$VERSION" --checksums release/checksums-linux.txt'
        in render
    )
    assert (
        _step(jobs["render"], "Keep the rendered packages for the next jobs")["with"][
            "include-hidden-files"
        ]
        is True
    )
    assert jobs["verify"]["container"].startswith("archlinux:base-devel@sha256:")
    build = _step(jobs["verify"], "Build each package and run namcap")["run"]
    assert "makepkg --printsrcinfo | diff -u .SRCINFO -" in build
    assert "makepkg --noconfirm --nodeps" in build
    publish = jobs["publish"]
    assert publish["needs"] == ["render", "verify"]
    assert publish["environment"] == "aur-publish"
    # Opt-in by variable, so the environment's required reviewer is asked only once
    # publishing is set up, not after every release.
    assert publish["if"] == "vars.AUR_PUBLISH == 'true'"
    assert "permissions" not in publish  # inherits contents: read only
    # The key reaches only the push step, never the jobs that run release code.
    for name, job in jobs.items():
        text = yaml.safe_dump(job)
        assert ("AUR_SSH_PRIVATE_KEY" in text) == (name == "publish"), name


def test_pinned_aur_host_keys_match_the_published_fingerprints() -> None:
    published = {
        "ssh-ed25519": "RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4",
        "ecdsa-sha2-nistp256": "uTa/0PndEgPZTf76e1DFqXKJEXKsn7m9ivhLQtzGOCI",
        "ssh-rsa": "5s5cIyReIfNNVGRFdDbe3hdYiI5OelHGpw2rOUud3Q8",
    }
    pinned = {}
    for line in (AUR / "known_hosts").read_text(encoding="utf8").splitlines():
        if line and not line.startswith("#"):
            host, kind, key = line.split()
            assert host == "aur.archlinux.org"
            digest = hashlib.sha256(base64.b64decode(key)).digest()
            pinned[kind] = base64.b64encode(digest).decode().rstrip("=")
    assert pinned == published


def _push_script(tmp_path: Path, key: str) -> tuple[subprocess.CompletedProcess[str], str]:
    step = _step(_workflow()["jobs"]["publish"], "Push changed packages")
    workspace = tmp_path / "workspace"
    shutil.copytree(AUR, workspace / "aur")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    fake_git = bin_dir / "git"
    # Records every call; "clone" makes the target folder and "diff" reports a change.
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$* | $GIT_SSH_COMMAND" >> "{log}"\n'
        'for arg; do last="$arg"; done\n'
        'case " $* " in *" clone "*) mkdir -p "$last/.git";; *" diff "*) exit 1;; esac\n',
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "AUR_SSH_PRIVATE_KEY": key,
        "AUR_COMMIT_NAME": "Test",
        "AUR_COMMIT_EMAIL": "test@example.com",
        "VERSION": "9.8.7",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_WORKSPACE": str(workspace),
    }
    result = subprocess.run(
        ["bash", "-c", step["run"]], env=env, text=True, capture_output=True, check=False
    )
    return result, log.read_text(encoding="utf8") if log.exists() else ""


def test_push_step_does_nothing_without_the_ssh_key(tmp_path: Path) -> None:
    result, calls = _push_script(tmp_path, "")
    assert result.returncode == 0, result.stderr
    assert "AUR publishing is off" in result.stdout
    assert calls == ""
    assert not (tmp_path / "runner").exists()


def test_push_step_trusts_only_the_pinned_host_keys(tmp_path: Path) -> None:
    result, calls = _push_script(tmp_path, "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n")
    assert result.returncode == 0, result.stderr
    lines = calls.splitlines()
    for pkg in aur.PACKAGES:
        assert any(f"clone ssh://aur@aur.archlinux.org/{pkg}.git" in line for line in lines)
    pushes = [line for line in lines if "push origin HEAD:master" in line]
    assert len(pushes) == 2
    for line in lines:
        assert "-o StrictHostKeyChecking=yes" in line
        assert f"-o UserKnownHostsFile={tmp_path}/workspace/aur/known_hosts" in line
    assert (tmp_path / "runner" / "aur-ssh" / "id").stat().st_mode & 0o077 == 0
    assert any("commit -m Update to 9.8.7" in line for line in lines)
