"""Exercise release probes before they reach the native packaging jobs."""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "package_smoke", ROOT / "scripts/test-release-packages.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_option_matches_release_metadata(smoke: ModuleType) -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"gof {smoke.VERSION}"
    assert 'copy_metadata("gofer-flow")' in (ROOT / "gof.spec").read_text()


@pytest.mark.parametrize("args", [[], ["--help"], ["ui", "serve", "--help"]])
def test_root_callback_preserves_help(args: list[str]) -> None:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == (2 if not args else 0)
    assert "Usage:" in result.output


def test_failed_command_reports_backend_output(smoke: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="status 2") as error:
        smoke.run(
            sys.executable, "-c", "import sys; print('No such option: --version'); sys.exit(2)"
        )
    assert "No such option: --version" in str(error.value)


def test_timeout_reports_partial_output(smoke: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(side_effect=subprocess.TimeoutExpired(["tool"], 180, output=b"starting"))
    monkeypatch.setattr(smoke.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="starting"):
        smoke.run("tool")


def test_probe_checks_archive_and_runs_outside_checkout(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readers = importlib.import_module("PyInstaller.archive.readers")

    binary = tmp_path / "gof"
    binary.touch(mode=0o755)
    entries = [
        "vosk/"
        + {"darwin": "libvosk.dyld", "win32": "libvosk.dll"}.get(sys.platform, "libvosk.so"),
        "third-party-licenses/inventory.json",
        "gofer/devices/protocol/v2/event.schema.json",
        "third-party-licenses/PYTHON-LICENSE.txt",
        "third-party-licenses/native-inventory.json",
    ]
    archive = Mock(toc=dict.fromkeys(entries), extract=Mock(return_value=b'[{"name":"fixture"}]'))
    monkeypatch.setattr(readers, "CArchiveReader", Mock(return_value=archive))
    calls = Mock(return_value=f"gof {smoke.VERSION}\n")
    monkeypatch.setattr(smoke, "run", calls)
    smoke.probe(binary)
    assert [c.args[1:] for c in calls.call_args_list] == [
        ("--version",),
        ("--help",),
        ("ui", "serve", "--help"),
    ]
    assert all(c.kwargs["cwd"] != ROOT for c in calls.call_args_list)
    calls.return_value = "gof 0.0.0"
    with pytest.raises(RuntimeError, match="Wrong packaged version"):
        smoke.probe(binary)
    schema = "gofer/devices/protocol/v2/event.schema.json"
    archive.toc.pop(schema)
    calls.reset_mock()
    with pytest.raises(RuntimeError, match="event.schema.json"):
        smoke.probe(binary)
    calls.assert_not_called()

    archive.toc[schema] = None
    archive.toc.pop(entries[-1])
    calls.reset_mock()
    with pytest.raises(RuntimeError, match="native-inventory"):
        smoke.probe(binary)
    calls.assert_not_called()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_probe_rejects_missing_speech_library(smoke, tmp_path, monkeypatch, platform):
    readers = importlib.import_module("PyInstaller.archive.readers")
    binary = tmp_path / "gof"
    binary.touch(mode=0o755)
    archive = Mock(
        toc={"third-party-licenses/inventory.json": None},
        extract=Mock(return_value=b'[{"name":"vosk"}]'),
    )
    monkeypatch.setattr(readers, "CArchiveReader", Mock(return_value=archive))
    monkeypatch.setattr(smoke.sys, "platform", platform)
    with pytest.raises(RuntimeError, match="Vosk speech library"):
        smoke.probe(binary)


def test_missing_backend_is_rejected(smoke: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="No backend found"):
        smoke.probe_tree(tmp_path)


def linux_files(version: str) -> list[str]:
    return [
        f"Raticode-{version}-amd64.deb",
        f"gofer-flow-cli_{version}_amd64.deb",
        f"Raticode-{version}-x86_64.rpm",
        f"gofer-flow-cli-{version}-1.x86_64.rpm",
    ]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_platform_extraction_and_evidence(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(smoke, "RELEASE", tmp_path)
    monkeypatch.setattr(smoke.sys, "platform", platform)
    for name in linux_files(smoke.VERSION) + [f"Raticode-{smoke.VERSION}-x86_64.AppImage"]:
        (tmp_path / name).touch()
    run = Mock(return_value="")
    probe = Mock()
    trees = Mock()
    monkeypatch.setattr(smoke, "run", run)
    monkeypatch.setattr(smoke, "probe", probe)
    monkeypatch.setattr(smoke, "probe_tree", trees)
    smoke.main()
    assert probe.call_count == 1
    assert trees.call_count == {"linux": 5, "darwin": 2, "win32": 1}[platform]
    commands = [c.args for c in run.call_args_list]
    if platform == "darwin":
        assert commands[-1][:2] == ("hdiutil", "detach")
    elif platform == "win32":
        assert commands[0][1] == "/S"
        assert commands[0][-1].startswith("/D=")
    else:
        assert sum(c[0] == "dpkg-deb" for c in commands) == 2
        assert sum(c[0] == "bsdtar" for c in commands) == 2
    evidence = next(tmp_path.glob("package-tests-*.json"))
    assert json.loads(evidence.read_text())["status"] == "passed"
    probe.side_effect = RuntimeError("broken backend")
    with pytest.raises(RuntimeError, match="broken backend"):
        smoke.main()
    assert not evidence.exists()


def test_macos_unmounts_after_probe_failure(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "RELEASE", tmp_path)
    monkeypatch.setattr(smoke.sys, "platform", "darwin")
    monkeypatch.setattr(smoke, "probe", Mock())
    monkeypatch.setattr(smoke, "probe_tree", Mock(side_effect=[None, RuntimeError("bad dmg")]))
    run = Mock()
    monkeypatch.setattr(smoke, "run", run)
    with pytest.raises(RuntimeError, match="bad dmg"):
        smoke.main()
    assert run.call_args.args[:2] == ("hdiutil", "detach")
    assert not list(tmp_path.glob("package-tests-*.json"))


@pytest.mark.parametrize("missing", range(4))
def test_linux_requires_each_current_package(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: int
) -> None:
    monkeypatch.setattr(smoke, "RELEASE", tmp_path)
    for i, name in enumerate(linux_files(smoke.VERSION)):
        if i != missing:
            (tmp_path / name).touch()
    (tmp_path / "old-release.deb").touch()
    with pytest.raises(RuntimeError, match="Expected exactly one"):
        smoke.linux_packages()


def test_child_process_resets_inherited_pyinstaller_environment(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")
    output = smoke.run(
        sys.executable,
        "-c",
        "import os; print(os.environ['PYINSTALLER_RESET_ENVIRONMENT'])",
    )
    assert output.strip() == "1"
