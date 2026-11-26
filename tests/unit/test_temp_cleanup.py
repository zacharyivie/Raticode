from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gofer.ui import scratch_cleanup, temp_cleanup
from gofer.ui.temp_cleanup import STALE_SECONDS, clean_worker_temp

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Worker temp roots are cleared on Linux"
)

UID = os.getuid()
HOUR = 60 * 60
# Every check runs "later", so files made by the test are old without touching ctime.
LATER = STALE_SECONDS + HOUR


def _root(tmp_path: Path) -> Path:
    root = tmp_path / f"claude-{UID}"
    root.mkdir(mode=0o700)
    return root


def _profile(path: Path, size: int = 64 * 1024) -> Path:
    """A browser profile like the ones test runs leave behind."""
    path.mkdir(parents=True)
    (path / "places.sqlite").write_bytes(b"x" * size)
    (path / "cache2").mkdir()
    (path / "cache2" / "entry").write_bytes(b"y" * 1024)
    return path


def _fresh(path: Path, now: float) -> None:
    """Make one file inside ``path`` change at ``now``, as a running test would."""
    target = next(item for item in path.rglob("*") if item.is_file())
    os.utime(target, (now, now))


def _sweep(root: Path, **options: object) -> temp_cleanup.TempCleanupResult:
    options.setdefault("held", [])
    options.setdefault("used_percent", lambda _path: 10.0)
    now = float(options.pop("now", time.time() + LATER))  # type: ignore[arg-type]
    return clean_worker_temp([root], now=now, **options)  # type: ignore[arg-type]


def test_leftover_test_folders_are_removed_and_live_ones_kept(tmp_path: Path) -> None:
    root = _root(tmp_path)
    now = time.time() + LATER
    # What a sandboxed test run left in /tmp/claude-<uid> before test-tmp.cjs cleaned up.
    leftover = _profile(root / "firefox-port-test-AbC123")
    killed_run = _profile(
        root / f"raticode-tests-{UID}" / "n1ulakm0-r4242-AbCdEf" / "firefox-profile-x"
    )
    stray_file = root / "after.txt"
    stray_file.write_text("notes")
    # A profile a running browser has open, and one a test wrote to a moment ago.
    live = _profile(root / "firefox-rem-test-Live01")
    fresh = _profile(root / "firefox-stream-test-Fresh1")
    _fresh(fresh, now)
    # Claude Code's own per-project folder: each session in it is judged on its own.
    project = root / "-home-me-Projects-app"
    old_session = _profile(project / "old-session" / "tasks")
    new_session = _profile(project / "new-session" / "tasks")
    _fresh(new_session, now)

    result = _sweep(root, now=now, held=[f"{live}/places.sqlite"])

    assert not leftover.exists()
    assert not killed_run.parent.exists()
    assert not stray_file.exists()
    assert not old_session.parent.exists()
    assert live.exists() and fresh.exists() and new_session.exists()
    assert project.is_dir() and (root / f"raticode-tests-{UID}").is_dir()
    assert sorted(path.name for path in result.removed) == [
        "after.txt",
        "firefox-port-test-AbC123",
        "n1ulakm0-r4242-AbCdEf",
        "old-session",
    ]
    assert result.freed_bytes >= 3 * 64 * 1024


def test_links_are_removed_as_links_and_their_targets_stay(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = _profile(tmp_path / "outside")
    (root / "points-out").symlink_to(outside)
    holder = _profile(root / "profile-with-link")
    (holder / "linked").symlink_to(outside)

    _sweep(root)

    assert not os.path.lexists(root / "points-out")
    assert not holder.exists()
    assert (outside / "places.sqlite").stat().st_size == 64 * 1024
    assert (outside / "cache2" / "entry").exists()


def test_a_folder_a_process_uses_is_kept(tmp_path: Path) -> None:
    root = _root(tmp_path)
    by_cwd = _profile(root / "server-cwd-AAAAAA")
    by_env = _profile(root / f"raticode-tests-{UID}" / "n1-r1-BBBBBB")
    gone = _profile(root / "firefox-watchdog-test-CCCCCC")
    script = "import time; time.sleep(60)"
    env = {**os.environ, "TMPDIR": str(by_env)}
    processes = [
        subprocess.Popen([sys.executable, "-c", script], cwd=by_cwd),
        subprocess.Popen([sys.executable, "-c", script], env=env),
    ]
    try:
        # held=None reads every process's folders, files and environment from /proc.
        result = _sweep(root, held=None)
    finally:
        for process in processes:
            process.kill()
            process.wait()

    assert by_cwd.exists() and by_env.exists()
    assert not gone.exists()
    assert [path.name for path in result.removed] == ["firefox-watchdog-test-CCCCCC"]


def test_files_changed_since_a_running_turn_began_are_kept(tmp_path: Path) -> None:
    root = _root(tmp_path)
    now = time.time() + 6 * HOUR
    before = _profile(root / "before-turn")
    during = _profile(root / "during-turn")
    for item in [during, *during.rglob("*")]:
        os.utime(item, (now - 4 * HOUR, now - 4 * HOUR), follow_symlinks=False)

    # Both are older than the age limit, but a turn that began 5 hours "ago" may
    # still use what changed after it started.
    _sweep(root, now=now, running_since=now - 5 * HOUR)

    assert not before.exists()
    assert during.exists()

    _sweep(root, now=now)
    assert not during.exists()


def test_a_full_temp_folder_clears_files_idle_for_an_hour(tmp_path: Path) -> None:
    root = _root(tmp_path)
    profile = _profile(root / "firefox-real-everyday-AAAAAA")
    now = time.time() + 2 * HOUR

    _sweep(root, now=now, used_percent=lambda _path: 50.0)
    assert profile.exists()

    _sweep(root, now=now, used_percent=lambda _path: 95.0)
    assert not profile.exists()


def test_a_folder_with_a_pipe_waits_longer(tmp_path: Path) -> None:
    root = _root(tmp_path)
    server = _profile(root / "raticode-dbus-AAAAAA")
    os.mkfifo(server / "bus")

    _sweep(root)
    assert server.exists()

    _sweep(root, now=time.time() + temp_cleanup.SERVER_STALE_SECONDS + HOUR)
    assert not server.exists()


def test_roots_others_could_change_are_left_alone(tmp_path: Path) -> None:
    shared = _root(tmp_path)
    shared.chmod(0o777)
    kept = _profile(shared / "firefox-profile-test-AAAAAA")
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    target = _profile(real / "firefox-profile-test-BBBBBB")
    linked = tmp_path / "linked-root" / f"claude-{UID}"
    linked.parent.mkdir()
    linked.symlink_to(real)

    assert _sweep(shared).removed == []
    assert _sweep(linked).removed == []
    assert kept.exists() and target.exists()


def test_a_file_dated_in_the_future_doesnt_keep_its_folder(tmp_path: Path) -> None:
    root = _root(tmp_path)
    probe = _profile(root / "symprobe")
    year_3000 = 32503680000.0
    for item in [probe, *probe.rglob("*")]:
        os.utime(item, (year_3000, year_3000), follow_symlinks=False)

    _sweep(root)

    assert not probe.exists()


def test_a_dry_run_reports_without_removing(tmp_path: Path) -> None:
    root = _root(tmp_path)
    profile = _profile(root / "firefox-port-test-AAAAAA")

    result = _sweep(root, dry_run=True)

    assert profile.exists()
    assert [path.name for path in result.removed] == ["firefox-port-test-AAAAAA"]


def test_running_turns_hold_back_the_sweep_until_they_end() -> None:
    first = temp_cleanup.turn_started()
    started = time.time()
    second = temp_cleanup.turn_started()
    try:
        since = temp_cleanup.running_turns_since()
        assert since is not None and since <= started
        temp_cleanup.turn_finished(first)
        later = temp_cleanup.running_turns_since()
        assert later is not None and later >= started
    finally:
        temp_cleanup.turn_finished(first)
        temp_cleanup.turn_finished(second)
    # A turn stuck for half a day no longer holds anything back.
    assert temp_cleanup.running_turns_since(time.time() + 13 * HOUR) is None


def test_the_sweeper_runs_at_start_and_again_after_a_turn_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweeps: list[str] = []
    swept = threading.Semaphore(0)

    def worker_temp() -> temp_cleanup.TempCleanupResult:
        sweeps.append("temp")
        swept.release()
        return temp_cleanup.TempCleanupResult()

    monkeypatch.setattr(temp_cleanup, "clean_worker_temp", worker_temp)
    monkeypatch.setattr(temp_cleanup, "sweeps_worker_temp", lambda: True)
    monkeypatch.setattr(
        scratch_cleanup, "clean_stale_scratch", lambda data_dir: sweeps.append("scratch")
    )
    monkeypatch.setattr(temp_cleanup, "TURN_END_SWEEP_GAP_SECONDS", 0)

    stop = temp_cleanup.start_temp_cleanup(tmp_path, interval=3600)
    try:
        assert swept.acquire(timeout=10)
        assert sweeps == ["scratch", "temp"]
        temp_cleanup.turn_finished(temp_cleanup.turn_started())
        assert swept.acquire(timeout=10)
        assert sweeps == ["scratch", "temp", "scratch", "temp"]
    finally:
        stop()
    # With the sweeper stopped, a turn's end asks nothing of anyone.
    temp_cleanup.request_temp_cleanup()


def test_a_backend_inside_a_worker_never_sweeps_the_shared_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RATICODE_AGENT_SESSION", raising=False)
    monkeypatch.delenv("RATICODE_COMMAND_SANDBOX", raising=False)
    monkeypatch.setattr(temp_cleanup, "_inside_own_process_namespace", lambda: False)
    assert temp_cleanup.sweeps_worker_temp()
    # A sandbox with its own process numbers sees only some processes.
    monkeypatch.setattr(temp_cleanup, "_inside_own_process_namespace", lambda: True)
    assert not temp_cleanup.sweeps_worker_temp()
    monkeypatch.setattr(temp_cleanup, "_inside_own_process_namespace", lambda: False)
    monkeypatch.setenv("RATICODE_COMMAND_SANDBOX", "1")
    assert not temp_cleanup.sweeps_worker_temp()
    monkeypatch.delenv("RATICODE_COMMAND_SANDBOX")
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    assert not temp_cleanup.sweeps_worker_temp()


def test_the_default_roots_are_the_sandbox_temp_folder_and_test_run_folders() -> None:
    assert temp_cleanup.temp_roots(1000) == [
        Path("/tmp/claude-1000"),
        Path("/var/tmp/raticode-tests-1000"),
        Path("/tmp/raticode-tests-1000"),
    ]
