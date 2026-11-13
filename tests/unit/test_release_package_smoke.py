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
    assert result.output.strip() == f"Raticode {smoke.VERSION}"
    assert 'copy_metadata("raticode")' in (ROOT / "raticode.spec").read_text()


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
    calls = Mock(return_value=f"Raticode {smoke.VERSION}\n")
    monkeypatch.setattr(smoke, "run", calls)
    smoke.probe(binary)
    assert [c.args[1:] for c in calls.call_args_list] == [
        ("--version",),
        ("--help",),
        ("ui", "serve", "--help"),
    ]
    assert all(c.kwargs["cwd"] != ROOT for c in calls.call_args_list)
    calls.return_value = "Raticode 0.0.0"
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


def test_probe_tree_skips_the_desktop_executable_with_the_same_name(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = tmp_path / "Raticode.app/Contents/MacOS/Raticode"
    backend = tmp_path / "Raticode.app/Contents/Resources/backend/Raticode"
    windows_desktop = tmp_path / "installed/Raticode.exe"
    windows_backend = tmp_path / "installed/resources/backend/Raticode.exe"
    linux_cli = tmp_path / "deb/usr/bin/Raticode"
    for path in (desktop, backend, windows_desktop, windows_backend, linux_cli):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o755)
    probed = Mock()
    monkeypatch.setattr(smoke, "probe", probed)

    smoke.probe_tree(tmp_path)

    assert sorted(c.args[0] for c in probed.call_args_list) == sorted(
        [backend, windows_backend, linux_cli]
    )


def linux_files(version: str) -> list[str]:
    return [
        f"Raticode-{version}-amd64.deb",
        f"raticode-cli_{version}_amd64.deb",
        f"Raticode-{version}-x86_64.rpm",
        f"raticode-cli-{version}-1.x86_64.rpm",
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
    headless = Mock(return_value={"checks": ["fixture"]})
    trees = Mock()
    speech = Mock()
    monkeypatch.setattr(smoke, "run", run)
    monkeypatch.setattr(smoke, "probe", probe)
    monkeypatch.setattr(smoke, "probe_headless", headless)
    monkeypatch.setattr(smoke, "probe_tree", trees)
    monkeypatch.setattr(smoke, "probe_speech_helper", speech)
    app_signing, dmg_signing, signing = Mock(), Mock(), Mock()
    update_feed = Mock(return_value="github:zacharyivie/Raticode")
    monkeypatch.setattr(smoke, "check_mac_update_feed", update_feed)
    monkeypatch.setattr(smoke, "check_mac_app_signing", app_signing)
    monkeypatch.setattr(smoke, "check_mac_dmg_signing", dmg_signing)
    monkeypatch.setattr(smoke, "mac_signing", Mock(return_value=signing))
    monkeypatch.setenv("RATICODE_MACOS_SIGNING", "developer-id")
    monkeypatch.setenv("RATICODE_MACOS_TEAM_ID", "ABCDE12345")
    smoke.main()
    assert probe.call_count == 1
    # The standalone CLI also runs as a headless backend and as its own client.
    assert headless.call_args_list == [((probe.call_args.args[0],),)]
    assert trees.call_count == {"linux": 5, "darwin": 2, "win32": 1}[platform]
    # Both the zip and the dmg copy of Raticode.app must carry the dictation helper.
    assert speech.call_count == (2 if platform == "darwin" else 0)
    # Both copies, and the DMG and CLI, are checked against the build's signing policy.
    checked_apps = [c.args[0] for c in app_signing.call_args_list]
    if platform == "darwin":
        assert [p.parent.name for p in checked_apps] == ["zip", "dmg-app"]
        assert all(
            c.args[1:3] == ("developer-id", "ABCDE12345") for c in app_signing.call_args_list
        )
        assert dmg_signing.call_args.args[0].name.endswith("-arm64.dmg")
        signing.require_developer_id.assert_called_once()
        # Squirrel.Mac updates both copies from the feed baked into each app.
        assert [c.args[0].parent.name for c in update_feed.call_args_list] == ["zip", "dmg-app"]
    else:
        assert checked_apps == [] and not dmg_signing.called and not update_feed.called
    commands = [c.args for c in run.call_args_list]
    if platform == "darwin":
        assert commands[-1][:2] == ("hdiutil", "detach")
    elif platform == "win32":
        assert commands[0][1] == "/S"
        assert commands[0][-1].startswith("/D=")
    else:
        # Each package's modes are listed first, then the package is extracted.
        assert [c[:2] for c in commands if c[0] == "dpkg-deb"] == [
            ("dpkg-deb", "--contents"),
            ("dpkg-deb", "--contents"),
            ("dpkg-deb", "-x"),
            ("dpkg-deb", "-x"),
        ]
        assert [c[:2] for c in commands if c[0] == "bsdtar"] == [
            ("bsdtar", "-tvf"),
            ("bsdtar", "-tvf"),
            ("bsdtar", "-xf"),
            ("bsdtar", "-xf"),
        ]
        assert "no group or world writable files" in recorded_scope(tmp_path)
    evidence = next(tmp_path.glob("package-tests-*.json"))
    recorded = json.loads(evidence.read_text())
    assert recorded["status"] == "passed"
    assert recorded["headless"] == {"checks": ["fixture"]}
    assert "headless backend and client mode" in recorded["scope"]
    if platform == "darwin":
        assert (recorded["signing"], recorded["team_id"]) == ("developer-id", "ABCDE12345")
        assert recorded["update_feed"] == "github:zacharyivie/Raticode"
    else:
        assert "signing" not in recorded and "update_feed" not in recorded
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
    monkeypatch.setattr(smoke, "probe_headless", Mock(return_value={}))
    monkeypatch.setattr(smoke, "probe_tree", Mock(side_effect=[None, RuntimeError("bad dmg")]))
    monkeypatch.setattr(smoke, "probe_speech_helper", Mock())
    monkeypatch.setattr(smoke, "check_mac_app_signing", Mock())
    monkeypatch.setattr(smoke, "check_mac_update_feed", Mock())
    monkeypatch.setattr(smoke, "check_mac_dmg_signing", Mock())
    monkeypatch.setattr(smoke, "mac_signing", Mock())
    monkeypatch.setenv("RATICODE_MACOS_SIGNING", "unsigned")
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


def _fake_app(root: Path, script: str) -> Path:
    helper = root / "Raticode.app/Contents/Resources/speech/raticode-speech"
    helper.parent.mkdir(parents=True)
    helper.write_text(f"#!{sys.executable}\n{script}\n")
    helper.chmod(0o755)
    return helper


def test_the_mac_speech_helper_probe_needs_a_running_bundled_helper(
    smoke: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError, match="missing its macOS speech helper"):
        smoke.probe_speech_helper(tmp_path)
    helper = _fake_app(
        tmp_path / "good",
        'import json; print(json.dumps({"type": "status", "available": False}))',
    )
    smoke.probe_speech_helper(tmp_path / "good")
    helper.chmod(0o644)
    with pytest.raises(RuntimeError, match="not executable"):
        smoke.probe_speech_helper(tmp_path / "good")
    _fake_app(tmp_path / "silent", "print('hello')")
    with pytest.raises(RuntimeError, match="gave no status"):
        smoke.probe_speech_helper(tmp_path / "silent")
    _fake_app(tmp_path / "crash", "import os; os.abort()")
    with pytest.raises(RuntimeError, match="exited with status"):
        smoke.probe_speech_helper(tmp_path / "crash")


def _source_cli(root: Path) -> Path:
    """An executable that runs this checkout's CLI, standing in for the frozen binary."""
    cli = root / "bin" / "Raticode"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "from gofer.cli.main import app\n"
        "app(prog_name='Raticode')\n"
    )
    cli.chmod(0o755)
    return cli


@pytest.mark.skipif(sys.platform == "win32", reason="the source stand-in is a shebang script")
def test_headless_probe_runs_a_real_backend_and_client(smoke: ModuleType, tmp_path: Path) -> None:
    cli = _source_cli(tmp_path)
    scratch = tmp_path / "probe"
    scratch.mkdir()
    result = smoke.probe_headless(cli, scratch)
    assert result["exit_status"] == 0
    assert result["endpoint"]["host"] == "127.0.0.1"
    assert int(result["endpoint"]["port"]) > 0
    assert len(result["checks"]) == 7
    # Everything the backend wrote stayed in the scratch folder.
    assert (scratch / "data" / "headless").is_dir()
    assert not (scratch / "data" / "headless" / "endpoint.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-ins are shebang scripts")
def test_headless_probe_reports_a_backend_that_cannot_start(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "Raticode"
    broken.write_text(f"#!{sys.executable}\nimport sys\nprint('boom: no backend')\nsys.exit(3)\n")
    broken.chmod(0o755)
    with pytest.raises(RuntimeError, match="exited with status 3(.|\\n)*boom: no backend"):
        smoke.probe_headless(broken, tmp_path / "a")
    silent = tmp_path / "Silent"
    silent.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    silent.chmod(0o755)
    monkeypatch.setattr(smoke, "HEADLESS_START_SECONDS", 1)
    with pytest.raises(RuntimeError, match="published no endpoint"):
        smoke.probe_headless(silent, tmp_path / "b")


def test_isolated_env_hides_the_callers_raticode_settings(
    smoke: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", "owner-secret")
    monkeypatch.setenv("RATICODE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    env = smoke.isolated_env(tmp_path)
    assert not {key for key in env if key.startswith(("RATICODE_", "GOFER_UI"))}
    assert env["GOFER_DATA_DIR"] == str(tmp_path / "data")
    assert env["HOME"] == str(tmp_path / "home")


def test_stopping_the_headless_backend_ends_its_whole_process_tree_on_windows(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-file Windows CLI's backend runs as a child of its launcher."""
    calls: list[list[str]] = []
    monkeypatch.setattr(smoke.os, "name", "nt")

    def fake_run(command: list[str], **_: object) -> Mock:
        calls.append(command)
        return Mock()

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    process = Mock(pid=4321)
    process.poll.return_value = None
    process.wait.return_value = 1
    assert smoke._stop(process) == 1
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]
    process.terminate.assert_called_once()

    calls.clear()
    monkeypatch.setattr(smoke.os, "name", "posix")
    assert smoke._stop(process) == 1
    assert calls == []


# Real lines from the published 0.5.3 packages, before the afterPack fix.
DEB_0_5_3 = """\
drwxr-xr-x root/root         0 2026-11-11 00:39 ./opt/Raticode/
-rwxrwxrwx root/root 234458440 2026-11-11 00:39 ./opt/Raticode/gofer-flow
-rw-rw-rw- root/root    568310 2026-11-11 00:39 ./opt/Raticode/chrome_100_percent.pak
-rwxr-xr-x root/root  91232400 2026-11-11 00:39 ./opt/Raticode/resources/backend/Raticode
"""
RPM_0_5_3 = """\
-rwxrwxrwx  0 0      0       15216 Nov 11 00:39 ./opt/Raticode/chrome-sandbox
lrwxrwxrwx  0 0      0          28 Nov 11 00:43 ./usr/lib/.build-id/34/f6 -> ../../opt/gofer-flow
drwxrwxr-x  0 0      0           0 Nov 11 00:39 ./opt/Raticode/locales
-rw-r--r--  0 0      0      927734 Nov 11 00:39 ./opt/Raticode/resources.pak
"""


def recorded_scope(folder: Path) -> str:
    return str(json.loads(next(folder.glob("package-tests-*.json")).read_text())["scope"])


def test_package_listing_flags_group_and_world_writable_entries(smoke: ModuleType) -> None:
    deb = smoke.writable_entries(DEB_0_5_3)
    assert [line.rsplit(" ", 1)[1] for line in deb] == [
        "./opt/Raticode/gofer-flow",
        "./opt/Raticode/chrome_100_percent.pak",
    ]
    rpm = smoke.writable_entries(RPM_0_5_3)
    # Symlinks are always lrwxrwxrwx and never count; a group-writable folder does.
    assert [line.split()[-1] for line in rpm] == [
        "./opt/Raticode/chrome-sandbox",
        "./opt/Raticode/locales",
    ]
    assert smoke.writable_entries("dpkg-deb: warning: something\n\n") == []


def test_linux_packages_with_writable_files_fail_the_smoke_test(
    smoke: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: object, **_: object) -> str:
        calls.append(tuple(str(arg) for arg in args))
        return DEB_0_5_3 if args[0] == "dpkg-deb" else RPM_0_5_3

    monkeypatch.setattr(smoke, "run", fake_run)
    with pytest.raises(RuntimeError, match="installs 2 group or world writable entries"):
        smoke.check_package_modes(tmp_path / "Raticode-0.5.3-amd64.deb")
    with pytest.raises(RuntimeError, match="gofer-flow|chrome-sandbox"):
        smoke.check_package_modes(tmp_path / "Raticode-0.5.3-x86_64.rpm")
    assert calls[0][:2] == ("dpkg-deb", "--contents")
    assert calls[1][:2] == ("bsdtar", "-tvf")

    monkeypatch.setattr(smoke, "run", lambda *a, **k: "-rwxr-xr-x root/root 1 x ./opt/a\n")
    smoke.check_package_modes(tmp_path / "Raticode-0.5.3-amd64.deb")
    # Every Linux package is checked before it is extracted.
    source = (ROOT / "scripts/test-release-packages.py").read_text()
    assert "for package in [*debs, *rpms]:\n                check_package_modes(package)" in source
