"""Exercise CLIs from final distributions on a disposable native CI runner.

This covers package extraction/installation and backend startup, not desktop UI
or upgrades from a previous release. No workflows are executed.

The standalone CLI is also started as a headless backend on a scratch data folder,
and the same binary then talks to it as a client (``probe_headless``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "frontend/release"
VERSION = json.loads((ROOT / "frontend/package.json").read_text())["version"]
# How the macOS build was signed: "developer-id" for releases, "unsigned" otherwise.
MAC_SIGNING_MODES = {"developer-id": "signed", "unsigned": "unsigned"}
# Every probed process gets a fresh PyInstaller environment and plain output.
CHILD_ENV = {"PYINSTALLER_RESET_ENVIRONMENT": "1", "NO_COLOR": "1"}
HEADLESS_START_SECONDS = 120


def run(
    *args: str | Path,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    expect: Sequence[int] = (0,),
) -> str:
    """Run a command and return its output; ``expect`` lists the accepted exit codes."""
    command = [str(arg) for arg in args]
    print(f"Running: {command!r}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            env={**(os.environ if env is None else env), **CHILD_ENV},
        )
        if result.returncode not in expect:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {command!r} exited with status {exc.returncode}:\n{exc.stdout}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command {command!r} timed out after {exc.timeout}s:\n{exc.stdout!r}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start {command!r}: {exc}") from exc
    return result.stdout


def probe(binary: Path) -> None:
    if not binary.is_file():
        raise RuntimeError(f"Packaged backend missing: {binary}")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise RuntimeError(f"Packaged backend is not executable: {binary}")
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(binary))
    entries = {name.replace("\\", "/"): name for name in archive.toc}
    required = "third-party-licenses/inventory.json"
    if required not in entries:
        raise RuntimeError(f"Packaged backend has no third-party notices: {binary}")
    inventory = json.loads(archive.extract(entries[required]))
    if not inventory:
        raise RuntimeError(f"Packaged backend has an empty license inventory: {binary}")
    speech_library = {"darwin": "libvosk.dyld", "win32": "libvosk.dll"}.get(
        sys.platform, "libvosk.so"
    )
    if f"vosk/{speech_library}" not in entries:
        raise RuntimeError(f"Packaged backend missing Vosk speech library: {binary}")
    for required in (
        "gofer/devices/protocol/v2/event.schema.json",
        "third-party-licenses/PYTHON-LICENSE.txt",
        "third-party-licenses/native-inventory.json",
    ):
        if required not in entries:
            raise RuntimeError(f"Packaged backend missing {required}: {binary}")
    # Probe away from the source checkout to catch accidental source-file dependencies.
    with tempfile.TemporaryDirectory(prefix="raticode-probe-") as temporary:
        output = run(binary.resolve(), "--version", cwd=Path(temporary))
        run(binary.resolve(), "--help", cwd=Path(temporary))
        run(binary.resolve(), "ui", "serve", "--help", cwd=Path(temporary))
    if output.strip() != f"Raticode {VERSION}":
        raise RuntimeError(f"Wrong packaged version for {binary}: {output}")
    print(f"Verified {binary.name}: {output.strip()}")


def isolated_env(root: Path) -> dict[str, str]:
    """An environment whose home, data and config folders are all under ``root``.

    No provider CLI, keyring or Raticode setting of the machine running the probe is
    visible, and inherited Raticode variables (tokens, endpoints, agent markers) are
    dropped so the client finds only the backend this probe started.
    """
    keep = {"PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "LANG"}
    keep |= {"TEMP", "TMP", "TMPDIR"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    home = root / "home"
    for folder in ("home", "config", "cache", "state", "share", "appdata", "localappdata"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_DATA_HOME": str(root / "share"),
            "APPDATA": str(root / "appdata"),
            "LOCALAPPDATA": str(root / "localappdata"),
            "GOFER_DATA_DIR": str(root / "data"),
            "CI": "true",
        }
    )
    return env


def _envelope(output: str, command: str) -> dict[str, Any]:
    """The JSON envelope a backend command printed, ignoring any log lines around it."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "ok" in value:
                return value
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{command} printed no JSON envelope:\n{output}") from exc
    if not isinstance(value, dict) or "ok" not in value:
        raise RuntimeError(f"{command} printed no JSON envelope:\n{output}")
    return value


def _stop(process: subprocess.Popen[bytes]) -> int | None:
    if process.poll() is None:
        if os.name == "nt":
            # The Windows CLI is a one-file PyInstaller program: its launcher starts the
            # real backend as a child. Ending only the launcher leaves that backend
            # serving and holding its log open, so end the whole process tree.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        process.terminate()
        try:
            return process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
            return None
    return process.returncode


def probe_headless(binary: Path, root: Path | None = None) -> dict[str, Any]:
    """Run the binary as a headless backend, then as a client of that backend.

    Checks that it starts on a scratch data folder, publishes its endpoint and owner
    token, answers ``api GET /health`` and a backend command with the owner token,
    honours a limited agent token, and removes its endpoint file when it stops.
    Returns what was checked, for the evidence file.
    """
    with tempfile.TemporaryDirectory(prefix="raticode-headless-probe-") as temporary:
        base = root or Path(temporary)
        env = isolated_env(base)
        data = base / "data"
        endpoint = data / "headless" / "endpoint.json"
        log_path = base / "backend.log"
        cli = binary.resolve()
        command = [str(cli), "serve", "--headless", "--port", "0", "--data-dir", str(data)]
        print(f"Starting: {command!r}", flush=True)
        try:
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=base,
                    env={**env, **CHILD_ENV},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        except OSError as exc:
            raise RuntimeError(f"Could not start {command!r}: {exc}") from exc
        try:
            deadline = time.monotonic() + HEADLESS_START_SECONDS
            while not endpoint.exists():
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Headless backend {cli} exited with status {process.returncode}:\n"
                        + log_path.read_text(encoding="utf-8", errors="replace")
                    )
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Headless backend {cli} published no endpoint in "
                        f"{HEADLESS_START_SECONDS}s:\n"
                        + log_path.read_text(encoding="utf-8", errors="replace")
                    )
                time.sleep(0.2)
            published = json.loads(endpoint.read_text(encoding="utf-8"))
            if not (data / "headless" / "owner-token").is_file():
                raise RuntimeError(f"Headless backend {cli} wrote no owner token")
            flags = ["--data-dir", str(data)]

            health = _envelope(
                run(cli, "api", "GET", "/health", *flags, cwd=base, env=env), "api GET /health"
            )
            if health.get("ok") is not True:
                raise RuntimeError(f"api GET /health failed against the packaged backend: {health}")
            listed = _envelope(
                run(cli, "rem", "tasks", "list", *flags, cwd=base, env=env), "rem tasks list"
            )
            if listed.get("ok") is not True:
                raise RuntimeError(f"rem tasks list failed against the packaged backend: {listed}")

            agent_token = base / "agent.token"
            create = ["tokens", "create", "smoke", "--output", str(agent_token)]
            run(cli, *create, *flags, cwd=base, env=env)
            tokens = run(cli, "tokens", "list", "--format", "json", *flags, cwd=base, env=env)
            if "smoke" not in tokens:
                raise RuntimeError(f"tokens list doesn't show the new agent token:\n{tokens}")
            agent = ["--token-file", str(agent_token), *flags]
            allowed = _envelope(
                run(cli, "api", "GET", "/workflows", *agent, cwd=base, env=env), "agent GET"
            )
            if allowed.get("ok") is not True:
                raise RuntimeError(f"An agent token couldn't read workflows: {allowed}")
            refused = _envelope(
                run(cli, "api", "GET", "/session", *agent, cwd=base, env=env, expect=(5,)),
                "agent GET /session",
            )
            if refused.get("ok") is not False:
                raise RuntimeError(f"An agent token read the owner session: {refused}")
        finally:
            status = _stop(process)
        if os.name != "nt":
            # Windows has no SIGTERM, so the backend can't clean up there.
            deadline = time.monotonic() + 10
            while endpoint.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if endpoint.exists():
                raise RuntimeError(f"Headless backend {cli} left its endpoint file after stopping")
        unreachable = run(cli, "api", "GET", "/health", *flags, cwd=base, env=env, expect=(6,))
        _envelope(unreachable, "api GET /health after stop")
        result = {
            "binary": str(binary),
            "endpoint": {key: published.get(key) for key in ("host", "port") if key in published},
            "checks": [
                "serve --headless publishes endpoint and owner token",
                "api GET /health with the owner token",
                "rem tasks list (a backend command)",
                "tokens create/list",
                "agent token reads /workflows and is refused /session (exit 5)",
                "endpoint removed on stop" if os.name != "nt" else "stopped",
                "client exits 6 once the backend is gone",
            ],
            "exit_status": status,
        }
        print(f"Verified headless and client mode of {binary.name}", flush=True)
        return result


def probe_tree(root: Path) -> None:
    # The desktop executable is also named Raticode on macOS and Windows; probe only
    # the CLI copies in the packaged backend folder or a system bin directory.
    binaries = [
        p
        for p in root.rglob("Raticode*")
        if p.name in {"Raticode", "Raticode.exe"}
        and p.parent.name in {"backend", "bin"}
        and p.is_file()
    ]
    if not binaries:
        raise RuntimeError(f"No backend found in extracted distribution: {root}")
    for binary in binaries:
        probe(binary)


def probe_speech_helper(root: Path) -> None:
    """Raticode.app's dictation helper is bundled, runs, and answers its status check."""
    helpers = sorted(root.rglob("Raticode.app/Contents/Resources/speech/raticode-speech"))
    if len(helpers) != 1 or not helpers[0].is_file():
        raise RuntimeError(f"Packaged app is missing its macOS speech helper: {root}")
    helper = helpers[0]
    if not os.access(helper, os.X_OK):
        raise RuntimeError(f"Packaged macOS speech helper is not executable: {helper}")
    # `check` reads the permission state without asking for it or recording.
    with tempfile.TemporaryDirectory(prefix="raticode-speech-probe-") as temporary:
        output = run(helper, "check", cwd=Path(temporary))
    statuses = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "status":
            statuses.append(event)
    if not statuses:
        raise RuntimeError(f"Packaged macOS speech helper gave no status: {output!r}")
    print(f"Verified {helper.name}: available={statuses[-1].get('available')}")


def read_asar_file(archive: Path, name: str) -> bytes:
    """One top-level file from an Electron asar archive, without unpacking it.

    The archive starts with two 32-bit sizes, then a Chromium pickle holding the JSON
    index; file offsets count from the end of that header.
    """
    with archive.open("rb") as stream:
        _, header_size = struct.unpack("<II", stream.read(8))
        header = stream.read(header_size)
        (json_size,) = struct.unpack("<I", header[4:8])
        index = json.loads(header[8 : 8 + json_size])
        entry = index["files"].get(name)
        if not isinstance(entry, dict) or "offset" not in entry:
            raise RuntimeError(f"{archive} has no packed {name}")
        stream.seek(8 + header_size + int(entry["offset"]))
        return stream.read(int(entry["size"]))


def mac_signing() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "macos_release_signing", ROOT / "scripts/macos-release-signing.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up while the module is being executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mac_signing_mode() -> tuple[str, str]:
    mode = os.environ.get("RATICODE_MACOS_SIGNING", "")
    if mode not in MAC_SIGNING_MODES:
        raise RuntimeError("RATICODE_MACOS_SIGNING must be developer-id or unsigned")
    team_id = os.environ.get("RATICODE_MACOS_TEAM_ID", "")
    if mode == "developer-id" and not team_id:
        raise RuntimeError("A Developer ID build needs RATICODE_MACOS_TEAM_ID")
    return mode, team_id


def check_mac_app_signing(app: Path, mode: str, team_id: str, signing: Any) -> None:
    """The app's signature and its updater label agree with how the build was signed."""
    metadata = json.loads(read_asar_file(app / "Contents/Resources/app.asar", "package.json"))
    label = metadata.get("raticodeReleaseSigning")
    if label != MAC_SIGNING_MODES[mode]:
        # The updater installs Mac updates itself only when this says "signed".
        raise RuntimeError(f"{app} is labelled {label!r} for updates, expected {mode} signing")
    if mode == "developer-id":
        signing.check_app(app, team_id)
        return
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
    if not signing.signature(app).adhoc:
        raise RuntimeError(f"{app} should carry only the unsigned build's ad-hoc signature")
    print(f"Verified {app}: unsigned build with an ad-hoc signature")


def check_mac_dmg_signing(dmg: Path, mode: str, team_id: str, signing: Any) -> None:
    if mode != "developer-id":
        return
    signing.require_developer_id(dmg, team_id, executable=False)
    run("xcrun", "stapler", "validate", dmg)
    run(
        "/usr/sbin/spctl",
        "--assess",
        "--type",
        "open",
        "--context",
        "context:primary-signature",
        "--verbose=2",
        dmg,
    )


def linux_packages() -> tuple[list[Path], list[Path]]:
    # Require both desktop and standalone CLI packages, not merely any matching file.
    packages = []
    for pattern in (
        f"Raticode-{VERSION}-amd64.deb",
        f"raticode-cli_{VERSION}_amd64.deb",
        f"Raticode-{VERSION}-x86_64.rpm",
        f"raticode-cli-{VERSION}-1*.x86_64.rpm",
    ):
        matches = sorted(RELEASE.glob(pattern))
        if len(matches) != 1 or not matches[0].is_file():
            raise RuntimeError(
                f"Expected exactly one release package matching {pattern}: {matches}"
            )
        packages.extend(matches)
    return packages[:2], packages[2:]


def main() -> None:
    platform = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    evidence = RELEASE / f"package-tests-{platform}.json"
    evidence.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="raticode-package-test-") as temporary:
        root = Path(temporary)
        if sys.platform == "win32":
            probe(RELEASE / "raticode-cli-windows-x64.exe")
            headless = probe_headless(RELEASE / "raticode-cli-windows-x64.exe")
            installer = RELEASE / f"Raticode-Setup-{VERSION}.exe"
            target = root / "installed"
            # NSIS requires /D to be the final argument. No shell quoting involved.
            run(installer, "/S", f"/D={target}")
            probe_tree(target)
        elif sys.platform == "darwin":
            mode, team_id = mac_signing_mode()
            signing = mac_signing()
            cli = RELEASE / "raticode-cli-macos-arm64"
            probe(cli)
            headless = probe_headless(cli)
            if mode == "developer-id":
                signing.require_developer_id(cli, team_id)
            archive = RELEASE / f"Raticode-{VERSION}-arm64.zip"
            # The updater installs this zip, so its app must pass the same checks.
            run("ditto", "-x", "-k", archive, root / "zip")
            probe_tree(root / "zip")
            probe_speech_helper(root / "zip")
            check_mac_app_signing(root / "zip/Raticode.app", mode, team_id, signing)
            dmg = RELEASE / f"Raticode-{VERSION}-arm64.dmg"
            check_mac_dmg_signing(dmg, mode, team_id, signing)
            mount = root / "dmg"
            run("hdiutil", "attach", dmg, "-readonly", "-nobrowse", "-mountpoint", mount)
            try:
                # Copy out before probing so the readonly mount is never modified.
                run("ditto", mount / "Raticode.app", root / "dmg-app/Raticode.app")
                probe_tree(root / "dmg-app")
                probe_speech_helper(root / "dmg-app")
                check_mac_app_signing(root / "dmg-app/Raticode.app", mode, team_id, signing)
            finally:
                run("hdiutil", "detach", mount)
        else:
            debs, rpms = linux_packages()
            probe(RELEASE / "raticode-cli-linux-x64")
            headless = probe_headless(RELEASE / "raticode-cli-linux-x64")
            appimage = RELEASE / f"Raticode-{VERSION}-x86_64.AppImage"
            appimage.chmod(appimage.stat().st_mode | 0o111)
            run(appimage, "--appimage-extract", cwd=root)
            probe_tree(root / "squashfs-root")
            for package in debs:
                target = root / package.name
                run("dpkg-deb", "-x", package, target)
                probe_tree(target)
            for package in rpms:
                target = root / package.name
                target.mkdir()
                # bsdtar reads RPM payloads without invoking a shell pipeline.
                run("bsdtar", "-xf", package, "-C", target)
                probe_tree(target)
    result: dict[str, Any] = {
        "status": "passed",
        "version": VERSION,
        "platform": platform,
        "scope": "Final distribution extraction/installation, backend version/help, and the "
        "standalone CLI's headless backend and client mode"
        + (
            ", macOS speech helper status check, and signing, notarization and updater label"
            if sys.platform == "darwin"
            else ""
        ),
    }
    result["headless"] = headless
    if sys.platform == "darwin":
        result["signing"], team_id = mac_signing_mode()
        if team_id:
            result["team_id"] = team_id
    evidence.write_text(
        json.dumps(
            result,
            indent=2,
        )
        + "\n"
    )
    print(f"Final distribution backend smoke tests passed for {sys.platform}.")


if __name__ == "__main__":
    if os.environ.get("CI") != "true":
        raise SystemExit("Run only on disposable CI runners; Windows installs a package.")
    main()
