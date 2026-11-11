"""The shared CLI client core, `Raticode api`, `wait` and `completion`.

Backend tests start a real `GoferUiServer` on a free loopback port with an isolated
data folder. Rem's task runner is replaced by one that never starts, so nothing here
can reach a provider.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from gofer.cli import client_options as opts
from gofer.cli.client import (
    AGENT_SESSION_ENVS,
    ApiClient,
    CliError,
    Endpoint,
    ExitCode,
    Output,
    OutputFormat,
    api_files,
    api_path,
    confirm,
    dedupes_request_id,
    discover_endpoint,
    envelope,
    exit_code_for_status,
    field_classifier,
    is_non_interactive,
    parse_duration,
    remove_api_files,
    revision_field,
    safe_to_resend,
    wait_until,
    write_api_files,
)
from gofer.cli.commands.api import complete_api_path, complete_method
from gofer.cli.main import app

runner = CliRunner()

CLIENT_ENV = (
    "RATICODE_URL",
    "RATICODE_SOCKET",
    "RATICODE_TOKEN",
    "RATICODE_TOKEN_FILE",
    "RATICODE_DATA_DIR",
    "GOFER_DATA_DIR",
    "RATICODE_NON_INTERACTIVE",
    *AGENT_SESSION_ENVS,
)


@pytest.fixture(autouse=True)
def _clean_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CLIENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _sockets_available() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _invoke(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> Any:
    return runner.invoke(app, list(args), env=env, input=stdin)


def _envelope(result: Any) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, f"no stdout; stderr={result.stderr!r}"
    payload: dict[str, Any] = json.loads(lines[-1])
    assert set(payload) >= {"ok", "command", "data", "error", "warnings"}
    return payload


# --- Exit codes, envelope and paths --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "name"),
    [
        (200, ExitCode.OK, "ok"),
        (400, ExitCode.USAGE, "invalid_request"),
        (401, ExitCode.AUTH, "unauthorized"),
        (403, ExitCode.AUTH, "forbidden"),
        (404, ExitCode.NOT_FOUND, "not_found"),
        (409, ExitCode.CONFLICT, "conflict"),
        (412, ExitCode.CONFLICT, "conflict"),
        (408, ExitCode.UNKNOWN, "timeout"),
        (422, ExitCode.USAGE, "invalid_request"),
        (500, ExitCode.FAILED, "backend_error"),
        (503, ExitCode.FAILED, "busy"),
    ],
)
def test_http_statuses_map_to_stable_exit_codes(status: int, code: ExitCode, name: str) -> None:
    assert exit_code_for_status(status) == (code, name)


def test_exit_codes_are_the_documented_values() -> None:
    assert [int(code) for code in ExitCode] == list(range(9))


def test_envelope_shape_is_stable() -> None:
    ok = envelope("api", data={"x": 1}, warnings=["w"], request_id="r1")
    assert ok == {
        "ok": True,
        "command": "api",
        "data": {"x": 1},
        "error": None,
        "warnings": ["w"],
        "request_id": "r1",
    }
    failure = envelope(
        "api", error=CliError("gone", code="not_found", exit_code=ExitCode.NOT_FOUND)
    )
    assert failure["ok"] is False
    assert failure["error"] == {"code": "not_found", "message": "gone", "http": None}
    assert "request_id" not in failure


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rem/kanban", "/api/rem/kanban"),
        ("/rem/kanban", "/api/rem/kanban"),
        ("/api/rem/kanban", "/api/rem/kanban"),
        ("/health?x=1", "/api/health?x=1"),
    ],
)
def test_api_paths_normalize_to_the_api_prefix(raw: str, expected: str) -> None:
    assert api_path(raw) == expected


@pytest.mark.parametrize(
    "raw", ["http://evil.example/api/health", "//evil/api", "/api/../etc", "", "/a b"]
)
def test_api_paths_cannot_leave_the_local_api(raw: str) -> None:
    with pytest.raises(CliError) as caught:
        api_path(raw)
    assert caught.value.exit_code == ExitCode.USAGE


def test_revision_field_follows_each_api_convention() -> None:
    assert revision_field("/rem/kanban/ticket") == "expected_revision"
    assert revision_field("/organizations") == "expectedRevision"
    assert revision_field("/rattish/workflows/x/document") == "expectedRevision"


def test_request_ids_and_revisions_are_injected_where_the_api_reads_them() -> None:
    client = ApiClient(Endpoint(url="http://127.0.0.1:1"))
    _, target, encoded = client._prepare(
        "POST", "/rem/kanban/ticket", {"action": "move"}, None, "rid-1", 4
    )
    assert target == "/api/rem/kanban/ticket"
    assert json.loads(encoded or b"") == {
        "request_id": "rid-1",
        "expected_revision": 4,
        "action": "move",
    }
    # An explicit body value wins over the flag.
    _, _, encoded = client._prepare(
        "POST", "/rem/messages", {"request_id": "mine"}, None, "rid-2", None
    )
    assert json.loads(encoded or b"")["request_id"] == "mine"
    # Organization actions read expectedRevision inside params.
    _, _, encoded = client._prepare(
        "POST",
        "/organizations",
        {"action": "configure", "params": {"reason": "sync"}},
        None,
        None,
        7,
    )
    assert json.loads(encoded or b"")["params"] == {"expectedRevision": 7, "reason": "sync"}
    # A body-less DELETE carries the request ID as a query parameter.
    _, target, encoded = client._prepare("DELETE", "/rem/tasks/t1", None, None, "rid-3", None)
    assert target == "/api/rem/tasks/t1?request_id=rid-3" and encoded is None


def test_request_id_and_revision_option_values() -> None:
    generated = opts.request_id_value("auto")
    assert generated and len(generated) == 36
    assert opts.request_id_value(" keep-me ") == "keep-me"
    with pytest.raises(CliError):
        opts.request_id_value("  ")
    assert opts.expected_revision_value("12") == 12
    assert opts.expected_revision_value("sha256:abc") == "sha256:abc"


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("90", 90.0), ("90s", 90.0), ("5m", 300.0), ("1h30m", 5400.0), ("250ms", 0.25)],
)
def test_durations(raw: str, seconds: float) -> None:
    assert parse_duration(raw) == pytest.approx(seconds)


@pytest.mark.parametrize("raw", ["soon", "5x", "-3", "1h soon"])
def test_bad_durations_are_usage_errors(raw: str) -> None:
    with pytest.raises(CliError) as caught:
        parse_duration(raw)
    assert caught.value.exit_code == ExitCode.USAGE


# --- Discovery -----------------------------------------------------------------------


def test_discovery_reads_published_files_from_the_data_folder(tmp_path: Path) -> None:
    files = write_api_files(tmp_path, url="http://127.0.0.1:4567", token="secret-token", pid=42)
    assert files == api_files(tmp_path)
    if os.name == "posix":
        assert stat.S_IMODE(files.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(files.token_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(files.endpoint_file.stat().st_mode) == 0o600

    found = discover_endpoint(data_dir=tmp_path, env={})
    assert found.url == "http://127.0.0.1:4567"
    assert found.token == "secret-token"
    assert found.pid == 42
    assert "secret-token" not in repr(found)
    assert "secret-token" not in json.dumps(found.describe())


def test_published_record_matches_the_headless_layout(tmp_path: Path) -> None:
    files = write_api_files(tmp_path, url="http://127.0.0.1:4567", token="t" * 32, pid=9)
    assert files.endpoint_file == tmp_path / "headless" / "endpoint.json"
    assert files.token_file == tmp_path / "headless" / "owner-token"
    record = json.loads(files.endpoint_file.read_text())
    assert record["url"] == "http://127.0.0.1:4567/api"
    assert (record["host"], record["port"], record["pid"]) == ("127.0.0.1", 4567, 9)
    assert record["tokenFile"] == str(files.token_file)


def test_endpoint_record_token_file_is_used(tmp_path: Path) -> None:
    secret = tmp_path / "secret-token"
    secret.write_text("mounted-secret\n")
    secret.chmod(0o600)
    headless = tmp_path / "headless"
    headless.mkdir(mode=0o700)
    record = {"url": "http://127.0.0.1:5555/api", "pid": 1, "tokenFile": str(secret)}
    (headless / "endpoint.json").write_text(json.dumps(record))
    (headless / "endpoint.json").chmod(0o600)
    found = discover_endpoint(data_dir=tmp_path, env={})
    assert (found.url, found.token, found.token_source) == (
        "http://127.0.0.1:5555",
        "mounted-secret",
        str(secret),
    )


def test_agent_sessions_never_discover_the_owner_token(tmp_path: Path) -> None:
    write_api_files(tmp_path, url="http://127.0.0.1:4567", token="owner-token-value")
    found = discover_endpoint(data_dir=tmp_path, env={"RATICODE_AGENT_SESSION": "1"})
    assert found.url == "http://127.0.0.1:4567"
    assert found.token == ""
    explicit = discover_endpoint(
        data_dir=tmp_path, env={"RATICODE_AGENT_SESSION": "1", "RATICODE_TOKEN": "rtk_agent_x"}
    )
    assert explicit.token == "rtk_agent_x"


def test_discovery_honors_data_dir_environment(tmp_path: Path) -> None:
    write_api_files(tmp_path / "a", url="http://127.0.0.1:1111", token="a-token")
    write_api_files(tmp_path / "b", url="http://127.0.0.1:2222", token="b-token")
    assert discover_endpoint(env={"GOFER_DATA_DIR": str(tmp_path / "a")}).token == "a-token"
    both = {"RATICODE_DATA_DIR": str(tmp_path / "b"), "GOFER_DATA_DIR": str(tmp_path / "a")}
    assert discover_endpoint(env=both).url == "http://127.0.0.1:2222"


def test_discovery_order_flag_then_environment_then_files(tmp_path: Path) -> None:
    write_api_files(tmp_path, url="http://127.0.0.1:1111", token="file-token")
    token_file = tmp_path / "other-token"
    token_file.write_text("flag-token\n")
    token_file.chmod(0o600)
    env = {
        "RATICODE_URL": "http://localhost:2222",
        "RATICODE_TOKEN": "env-token",
    }
    found = discover_endpoint(
        endpoint="http://127.0.0.1:3333", token_file=token_file, data_dir=tmp_path, env=env
    )
    assert (found.url, found.token, found.source) == (
        "http://127.0.0.1:3333",
        "flag-token",
        "--endpoint",
    )
    found = discover_endpoint(data_dir=tmp_path, env=env)
    assert (found.url, found.token) == ("http://localhost:2222", "env-token")
    found = discover_endpoint(data_dir=tmp_path, env={"RATICODE_TOKEN_FILE": str(token_file)})
    assert (found.url, found.token) == ("http://127.0.0.1:1111", "flag-token")
    found = discover_endpoint(endpoint="unix:/run/raticode.sock", data_dir=tmp_path, env={})
    assert found.socket_path == "/run/raticode.sock"


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8765",
        "http://example.com:8765",
        "https://127.0.0.1:8765",
        "http://user:pw@127.0.0.1:8765",
        "http://0.0.0.0:8765",
    ],
)
def test_non_loopback_or_unsafe_endpoints_are_refused(tmp_path: Path, url: str) -> None:
    with pytest.raises(CliError) as caught:
        discover_endpoint(data_dir=tmp_path, env={"RATICODE_URL": url})
    assert caught.value.exit_code == ExitCode.USAGE


def test_loopback_endpoints_are_accepted(tmp_path: Path) -> None:
    for url, expected in [
        ("127.0.0.1:9", "http://127.0.0.1:9"),
        ("http://127.1.2.3:9/", "http://127.1.2.3:9"),
        ("http://[::1]:9", "http://[::1]:9"),
        ("http://localhost:9/api", "http://localhost:9"),
    ]:
        assert discover_endpoint(data_dir=tmp_path, env={"RATICODE_URL": url}).url == expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_token_files_readable_by_others_are_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("t")
    token_file.chmod(0o644)
    with pytest.raises(CliError) as caught:
        discover_endpoint(token_file=token_file, data_dir=tmp_path, env={})
    assert caught.value.exit_code == ExitCode.AUTH
    assert caught.value.code == "insecure_token_file"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_endpoint_file_may_be_readable_but_not_writable_by_others(tmp_path: Path) -> None:
    files = write_api_files(tmp_path, url="http://127.0.0.1:4567", token="t")
    files.endpoint_file.chmod(0o644)
    assert discover_endpoint(data_dir=tmp_path, env={}).url == "http://127.0.0.1:4567"
    files.endpoint_file.chmod(0o666)
    with pytest.raises(CliError) as caught:
        discover_endpoint(data_dir=tmp_path, env={})
    assert caught.value.code == "insecure_token_file"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_token_files_through_symlinks_are_checked_at_the_target(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    target.write_text("linked-token\n")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    found = discover_endpoint(data_dir=tmp_path, env={"RATICODE_TOKEN_FILE": str(link)})
    assert found.token == "linked-token"
    target.chmod(0o640)
    with pytest.raises(CliError):
        discover_endpoint(data_dir=tmp_path, env={"RATICODE_TOKEN_FILE": str(link)})


def test_missing_token_file_is_an_auth_error(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        discover_endpoint(token_file=tmp_path / "nope", data_dir=tmp_path, env={})
    assert caught.value.exit_code == ExitCode.AUTH


def test_remove_api_files_only_removes_its_own(tmp_path: Path) -> None:
    write_api_files(tmp_path, url="http://127.0.0.1:1", token="t", pid=111)
    remove_api_files(tmp_path, pid=222)
    assert api_files(tmp_path).token_file.exists()
    remove_api_files(tmp_path, pid=111)
    assert not api_files(tmp_path).token_file.exists()
    assert not api_files(tmp_path).endpoint_file.exists()


def _all_params(command: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every (command path, parameter) in the tree.

    Typer bundles its own click, so its groups are not ``click.Group`` instances; walk
    anything that has subcommands.
    """
    for param in command.params:
        yield prefix, param
    for name, child in sorted(getattr(command, "commands", {}).items()):
        yield from _all_params(child, f"{prefix} {name}".strip())


# Option names that would carry a credential. File, environment-variable, reference
# and field-name options are fine: they name where a secret is, not the secret.
SECRET_OPTION = re.compile(
    r"(token|password|passwd|secret|api-key|credential)(?!-file|-env|-ref|-field)", re.I
)

# Each exception says why its value is not a credential on the command line.
SECRET_OPTION_EXCEPTIONS = {
    # Deprecated and hidden; prints a warning. Use --token-file or RATICODE_WEBHOOK_TOKEN.
    ("workflow trigger", "--token"),
    # Takes a secret reference such as {{secret.SMTP_PASSWORD}}; warns on a literal value.
    ("workflow add-node", "--notification-smtp-password"),
    # The name of a stored secret, not its value.
    ("provider profile create", "--api-key-secret"),
    ("provider profile edit", "--api-key-secret"),
}


def test_the_option_walker_reaches_nested_commands() -> None:
    commands = {where for where, _ in _all_params(get_command(app))}
    assert {
        "api",
        "workflow trigger",
        "provider profile create",
        "rattish triggers add cron",
    } <= commands
    assert len(commands) > 50


def test_no_command_accepts_a_credential_on_the_command_line() -> None:
    found = {
        (where, opt)
        for where, param in _all_params(get_command(app))
        for opt in getattr(param, "opts", [])
        if opt.startswith("--") and SECRET_OPTION.search(opt)
    }
    assert found - SECRET_OPTION_EXCEPTIONS == set()
    # Every exception still exists, so the list can't silently go stale.
    assert SECRET_OPTION_EXCEPTIONS <= found
    trigger = get_command(app).commands["workflow"].commands["trigger"]  # type: ignore[attr-defined]
    deprecated = next(p for p in trigger.params if "--token" in p.opts)
    assert deprecated.hidden is True


# --- Waiting -------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_wait_until_reaches_success_failure_and_needs_user() -> None:
    classify = field_classifier(
        "run.state", success=["done"], failure=["failed"], needs_user=["needs_you"]
    )
    for final, expected in [
        ("done", ExitCode.OK),
        ("failed", ExitCode.FAILED),
        ("needs_you", ExitCode.NEEDS_USER),
    ]:
        states = iter(["queued", "running", final])
        clock = FakeClock()

        def fetch(states: Iterator[str] = states) -> dict[str, Any]:
            return {"run": {"state": next(states)}}

        result = wait_until(
            fetch,
            classify,
            timeout=60,
            interval=1,
            clock=clock,
            sleep=clock.sleep,
        )
        assert result.exit_code == expected
        assert result.attempts == 3


def test_wait_until_times_out_with_the_last_value_and_tolerates_restarts() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    def fetch() -> dict[str, str]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise CliError("down", code="unreachable", exit_code=ExitCode.UNREACHABLE)
        return {"status": "running"}

    with pytest.raises(CliError) as caught:
        wait_until(
            fetch,
            field_classifier("status", success=["done"]),
            timeout=10,
            interval=1,
            clock=clock,
            sleep=clock.sleep,
        )
    assert caught.value.exit_code == ExitCode.UNKNOWN
    assert caught.value.code == "timeout"
    assert caught.value.data == {"status": "running"}
    assert calls["n"] > 2


def test_wait_until_does_not_hide_other_errors() -> None:
    def fetch() -> None:
        raise CliError("gone", code="not_found", exit_code=ExitCode.NOT_FOUND)

    with pytest.raises(CliError) as caught:
        wait_until(fetch, lambda _: None, timeout=5, sleep=lambda _: None)
    assert caught.value.exit_code == ExitCode.NOT_FOUND


# --- Interaction and completion -------------------------------------------------------


def test_non_interactive_mode_requires_yes() -> None:
    assert is_non_interactive(env={"RATICODE_NON_INTERACTIVE": "1"})
    assert is_non_interactive(env={"CI": "true"})
    assert is_non_interactive(flag=True, env={})
    confirm("Delete it?", yes=True)
    with pytest.raises(CliError) as caught:
        confirm("Delete it?", non_interactive=True)
    assert caught.value.exit_code == ExitCode.USAGE
    assert caught.value.code == "confirmation_required"


def test_root_non_interactive_flag_sets_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATICODE_NON_INTERACTIVE", "0")
    result = _invoke("--non-interactive", "completion", "bash")
    assert result.exit_code == 0
    assert os.environ["RATICODE_NON_INTERACTIVE"] == "1"


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "powershell"])
def test_completion_scripts_target_the_raticode_command(shell: str) -> None:
    result = _invoke("completion", shell)
    assert result.exit_code == 0, result.stderr
    assert "_RATICODE_COMPLETE" in result.stdout


def test_completion_rejects_unknown_shells() -> None:
    result = _invoke("completion", "tcsh")
    assert result.exit_code == ExitCode.USAGE


def test_path_and_method_completion_are_offline() -> None:
    assert complete_method("p") == ["POST", "PUT", "PATCH"]
    assert "/rem/kanban" in complete_api_path("/rem/k")
    assert "/rem/kanban" in complete_api_path("rem/k")
    assert "/rem/tasks" in complete_api_path("/api/rem/t")


def test_shell_completion_offers_the_new_commands() -> None:
    result = runner.invoke(
        app,
        [],
        prog_name="Raticode",
        env={"_RATICODE_COMPLETE": "complete_bash", "COMP_WORDS": "Raticode ap", "COMP_CWORD": "1"},
    )
    assert "api" in result.stdout


def test_text_output_goes_to_stdout_and_errors_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = Output("api", OutputFormat.text)
    out.error(CliError("nope", exit_code=ExitCode.FAILED))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nope" in captured.err


# --- A real, isolated backend ---------------------------------------------------------


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    from gofer.ui.rem_home import RemHome
    from gofer.ui.server import create_server

    data_dir = tmp_path / "data"
    monkeypatch.delenv("GOFER_UI_API_TOKEN", raising=False)
    server = create_server(host="127.0.0.1", port=0, data_dir=data_dir, api_token="test-token-1")

    async def no_provider(**_kwargs: Any) -> Any:
        yield {"type": "final", "message": {"body": "No live provider in this test"}}

    # The server's own Rem runner would wait for desktop context anyway; this one
    # can't start tasks at all, so no provider can be reached.
    server.rem_home.close()
    service = RemHome(
        server.data_dir,
        source=no_provider,
        jobs=server.chat_jobs,
        steering=server.chat_steering,
        authorize=server._authorize_rem_home,
        start=False,
    )
    server.rem_home = service
    service._lease()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    write_api_files(data_dir, url=f"http://127.0.0.1:{port}", token="test-token-1")
    monkeypatch.setenv("RATICODE_DATA_DIR", str(data_dir))
    try:
        yield {"server": server, "data_dir": data_dir, "port": port}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server.chat_jobs.close()
        server.chat_steering.close()
        server.generation_jobs.close()


def test_api_get_returns_an_envelope(backend: dict[str, Any]) -> None:
    result = _invoke("api", "GET", "/health")
    assert result.exit_code == 0, result.stderr
    payload = _envelope(result)
    assert payload == {
        "ok": True,
        "command": "api",
        "data": {"ok": True},
        "error": None,
        "warnings": [],
    }
    raw = _invoke("api", "get", "health", "--raw")
    assert json.loads(raw.stdout) == {"ok": True}


def test_api_authenticates_with_the_discovered_token(backend: dict[str, Any]) -> None:
    result = _invoke("api", "GET", "/rem/kanban")
    assert result.exit_code == 0, result.stderr
    data = _envelope(result)["data"]
    assert "board" in data and "self_work" in data


def test_api_without_a_token_exits_with_auth_error(backend: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "elsewhere"
    port = backend["port"]
    result = _invoke(
        "api",
        "GET",
        "/rem/kanban",
        "--endpoint",
        f"http://127.0.0.1:{port}",
        "--data-dir",
        str(empty),
    )
    assert result.exit_code == ExitCode.AUTH
    payload = _envelope(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unauthorized"
    assert payload["error"]["http"] == 401
    assert "No token found" in result.stderr


def test_api_wrong_token_is_rejected(backend: dict[str, Any], tmp_path: Path) -> None:
    token_file = tmp_path / "wrong"
    token_file.write_text("not-the-token")
    token_file.chmod(0o600)
    result = _invoke("api", "GET", "/rem/kanban", "--token-file", str(token_file))
    assert result.exit_code == ExitCode.AUTH
    assert "rejected" in result.stderr


def test_api_not_found_and_invalid_input(backend: dict[str, Any]) -> None:
    missing = _invoke("api", "GET", "/rem/nothing-here")
    assert missing.exit_code == ExitCode.NOT_FOUND
    assert _envelope(missing)["error"]["http"] == 404
    bad = _invoke("api", "POST", "/rem/kanban/ticket", "-f", "action=create")
    assert bad.exit_code == ExitCode.USAGE
    assert _envelope(bad)["error"]["http"] == 400
    bad_json = _invoke("api", "POST", "/rem/kanban/ticket", "--data", "{nope")
    assert bad_json.exit_code == ExitCode.USAGE
    assert _envelope(bad_json)["error"]["code"] == "invalid_json"
    method = _invoke("api", "TRACE", "/health")
    assert method.exit_code == ExitCode.USAGE


def test_api_refuses_remote_endpoints_before_connecting(backend: dict[str, Any]) -> None:
    result = _invoke("api", "GET", "/health", "--endpoint", "http://192.0.2.10:8765")
    assert result.exit_code == ExitCode.USAGE
    assert _envelope(result)["error"]["code"] == "remote_refused"


def test_unreachable_backend_exits_6(tmp_path: Path) -> None:
    port = _free_port()
    write_api_files(tmp_path, url=f"http://127.0.0.1:{port}", token="t", pid=2**22 + 7)
    result = _invoke("api", "GET", "/health", "--data-dir", str(tmp_path))
    assert result.exit_code == ExitCode.UNREACHABLE
    assert _envelope(result)["error"]["code"] == "unreachable"


def test_request_id_retries_do_not_duplicate_tickets(backend: dict[str, Any]) -> None:
    args = (
        "api",
        "POST",
        "/rem/kanban/ticket",
        "-f",
        "action=create",
        "-f",
        "title=From the CLI",
        "--request-id",
        "cli-test-1",
    )
    first = _invoke(*args)
    assert first.exit_code == 0, first.stderr
    assert _envelope(first)["request_id"] == "cli-test-1"
    second = _invoke(*args)
    assert second.exit_code == 0, second.stderr
    tickets = _envelope(second)["data"]["board"]["tickets"]
    assert [t["title"] for t in tickets] == ["From the CLI"]


def test_auto_request_id_is_reported(backend: dict[str, Any]) -> None:
    result = _invoke(
        "api",
        "POST",
        "/rem/kanban/ticket",
        "--data",
        '{"action": "create", "title": "Auto"}',
        "--request-id",
        "auto",
    )
    assert result.exit_code == 0, result.stderr
    assert len(_envelope(result)["request_id"]) == 36


def test_stale_expected_revision_exits_4(backend: dict[str, Any]) -> None:
    created = _invoke("api", "POST", "/rem/kanban/ticket", "-f", "action=create", "-f", "title=Rev")
    ticket = _envelope(created)["data"]["board"]["tickets"][0]
    move = (
        "api",
        "POST",
        "/rem/kanban/ticket",
        "-f",
        "action=move",
        "-f",
        f"ticket_id={ticket['ticket_id']}",
        "-f",
        "column=backlog",
        "--expected-revision",
        str(ticket["revision"]),
    )
    assert _invoke(*move).exit_code == 0
    stale = _invoke(*move)
    assert stale.exit_code == ExitCode.CONFLICT
    assert _envelope(stale)["error"] == {
        "code": "conflict",
        "message": "This ticket changed. Check it and try again.",
        "http": 409,
    }


def test_stdin_body_and_typed_fields(backend: dict[str, Any]) -> None:
    result = _invoke(
        "api",
        "PUT",
        "/rem/kanban/settings",
        "--data",
        "@-",
        "-F",
        "max_threads=2",
        "--expected-revision",
        "0",
        stdin='{"max_threads": 9}',
    )
    assert result.exit_code == 0, result.stderr
    assert _envelope(result)["data"]["self_work"]["settings"]["max_threads"] == 2


def test_follow_polls_event_lists_and_stops_at_the_timeout(backend: dict[str, Any]) -> None:
    result = _invoke(
        "api", "GET", "/rem/events", "--follow", "--interval", "0.05", "--timeout", "300ms"
    )
    assert result.exit_code == ExitCode.UNKNOWN
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert lines[-1]["kind"] == "result"
    assert lines[-1]["error"]["code"] == "timeout"
    assert all(line["kind"] == "event" for line in lines[:-1])


def test_follow_refuses_endpoints_that_are_not_streams(backend: dict[str, Any]) -> None:
    result = _invoke("api", "GET", "/health", "--follow", "--timeout", "1s")
    assert result.exit_code == ExitCode.USAGE
    assert _envelope(result)["error"]["code"] == "not_followable"


def test_follow_missing_chat_turn_is_not_found(backend: dict[str, Any]) -> None:
    result = _invoke(
        "api",
        "GET",
        "/chat/events",
        "-f",
        "conversationId=nope",
        "-f",
        "turnId=nope",
        "--follow",
        "--timeout",
        "2s",
    )
    assert result.exit_code == ExitCode.NOT_FOUND


def test_wait_command_against_the_backend(backend: dict[str, Any]) -> None:
    done = _invoke("wait", "/health", "--field", "ok", "--success", "true", "--timeout", "5s")
    assert done.exit_code == 0, done.stderr
    assert _envelope(done)["data"] == {"ok": True}
    failed = _invoke("wait", "/health", "--field", "ok", "--success", "x", "--failure", "true")
    assert failed.exit_code == ExitCode.FAILED
    assert _envelope(failed)["data"] == {"ok": True}
    user = _invoke("wait", "/health", "--field", "ok", "--success", "x", "--needs-user", "true")
    assert user.exit_code == ExitCode.NEEDS_USER
    late = _invoke(
        "wait",
        "/health",
        "--field",
        "ok",
        "--success",
        "x",
        "--timeout",
        "200ms",
        "--interval",
        "0.05",
    )
    assert late.exit_code == ExitCode.UNKNOWN
    assert _envelope(late)["error"]["code"] == "timeout"
    missing = _invoke("wait", "/health", "--timeout", "1s")
    assert missing.exit_code == ExitCode.USAGE


def test_client_helpers_against_the_backend(backend: dict[str, Any]) -> None:
    client = opts.connect()
    assert client.get("/health") == {"ok": True}
    board = client.post(
        "/rem/kanban/ticket", {"action": "create", "title": "Helper"}, request_id="h-1"
    )
    assert board["board"]["tickets"][0]["title"] == "Helper"
    assert list(client.stream("GET", "/health")) == [{"ok": True}]


# --- NDJSON streaming against a stand-in backend ---------------------------------------


class _StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen.append(  # type: ignore[attr-defined]
            {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()
        for event in [{"type": "delta", "text": "hi"}, {"type": "final", "ok": True}]:
            self.wfile.write(json.dumps(event).encode() + b"\n")
            self.wfile.flush()
        if body.get("fail"):
            self.wfile.write(b'{"type": "error", "error": "provider failed"}\n')


@pytest.fixture
def ndjson_backend(tmp_path: Path) -> Iterator[tuple[ThreadingHTTPServer, Path]]:
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamHandler)
    server.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    write_api_files(tmp_path, url=f"http://127.0.0.1:{server.server_address[1]}", token="s-tok")
    try:
        yield server, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_follow_streams_ndjson_events(
    ndjson_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    server, data_dir = ndjson_backend
    result = _invoke(
        "api",
        "POST",
        "/chat/stream",
        "--data",
        '{"message": "x"}',
        "--request-id",
        "s-1",
        "--follow",
        "--data-dir",
        str(data_dir),
    )
    assert result.exit_code == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines == [
        {"kind": "event", "data": {"type": "delta", "text": "hi"}},
        {"kind": "event", "data": {"type": "final", "ok": True}},
        {
            "kind": "result",
            "ok": True,
            "command": "api",
            "data": {"events": 2},
            "error": None,
            "warnings": [],
            "request_id": "s-1",
        },
    ]
    seen = server.seen  # type: ignore[attr-defined]
    assert seen[0]["path"] == "/api/chat/stream"
    assert seen[0]["body"] == {"request_id": "s-1", "message": "x"}
    assert seen[0]["auth"] == "Bearer s-tok"


def test_follow_reports_a_stream_error_event(
    ndjson_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    _, data_dir = ndjson_backend
    result = _invoke(
        "api",
        "POST",
        "/chat/stream",
        "-F",
        "fail=true",
        "--follow",
        "--data-dir",
        str(data_dir),
    )
    assert result.exit_code == ExitCode.FAILED
    final = json.loads(result.stdout.splitlines()[-1])
    assert final["kind"] == "result" and final["error"]["code"] == "stream_error"
    assert final["error"]["message"] == "provider failed"


def test_unix_socket_transport(tmp_path: Path) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix sockets are unavailable")
    from socketserver import ThreadingUnixStreamServer

    class UnixHandler(_StreamHandler):
        def address_string(self) -> str:
            return "local"

    socket_path = tmp_path / "api.sock"
    server = ThreadingUnixStreamServer(str(socket_path), UnixHandler)
    server.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ApiClient(discover_endpoint(endpoint=f"unix:{socket_path}", env={}))
        events = list(client.stream("POST", "/chat/stream", body={}))
        assert events[-1] == {"type": "final", "ok": True}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_typer_exit_codes_pass_through_run_command() -> None:
    def body(_out: Output) -> None:
        raise CliError("x", code="needs_user", exit_code=ExitCode.NEEDS_USER)

    with pytest.raises(typer.Exit) as caught:
        opts.run_command("demo", OutputFormat.json, body)
    assert caught.value.exit_code == ExitCode.NEEDS_USER


def test_smtp_password_option_warns_on_a_literal_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gofer.cli.commands.workflow import _warn_literal_secret

    _warn_literal_secret("{{secret.SMTP_PASSWORD}}", "--notification-smtp-password")
    _warn_literal_secret("secret:SMTP_PASSWORD", "--notification-smtp-password")
    _warn_literal_secret(None, "--notification-smtp-password")
    assert capsys.readouterr().err == ""
    _warn_literal_secret("hunter2", "--notification-smtp-password")
    assert "looks like a literal secret" in capsys.readouterr().err


# --- Resending only what is safe to resend ---------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("POST", "/rem/messages", {"body": "hi"}, True),
        ("POST", "/rem/tasks", {}, True),
        ("PATCH", "/rem/tasks/t1", {}, True),
        ("DELETE", "/api/rem/tasks/t1", None, True),
        ("POST", "/rem/tasks/t1/run", {}, True),
        ("POST", "/rem/runs/r1/cancel", {}, True),
        ("POST", "/rem/threads/th1/messages", {}, True),
        ("POST", "/rem/kanban/ticket", {"action": "create"}, True),
        ("POST", "/rem/kanban/ticket", {"action": "comment"}, True),
        ("POST", "/rem/kanban/ticket", {"action": "move"}, False),
        ("PUT", "/rem/kanban/settings", {}, False),
        ("POST", "/rem/context", {}, False),
        ("POST", "/workflows/w1/run", {}, False),
        ("POST", "/chat/stream", {}, False),
        ("POST", "/organizations", {"action": "configure"}, False),
    ],
)
def test_only_endpoints_with_receipts_dedupe_request_ids(
    method: str, path: str, body: Any, expected: bool
) -> None:
    assert dedupes_request_id(method, path, body) is expected
    assert safe_to_resend(method, path, body, "rid") is expected
    assert safe_to_resend(method, path, body, None) is False
    assert safe_to_resend("GET", path) is True


class _SlowHandler(BaseHTTPRequestHandler):
    """Counts each request, then answers more slowly than the client waits."""

    def log_message(self, *_args: Any) -> None:
        return

    def _slow(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        self.server.release.wait(5)  # type: ignore[attr-defined]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
        except OSError:
            pass

    do_POST = _slow
    do_GET = _slow


@pytest.fixture
def slow_backend(tmp_path: Path) -> Iterator[tuple[ThreadingHTTPServer, Path]]:
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    server.hits = []  # type: ignore[attr-defined]
    server.release = threading.Event()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    write_api_files(tmp_path, url=f"http://127.0.0.1:{server.server_address[1]}", token="t")
    try:
        yield server, tmp_path
    finally:
        server.release.set()  # type: ignore[attr-defined]
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _slow_client(data_dir: Path) -> ApiClient:
    endpoint = discover_endpoint(data_dir=data_dir, env={})
    return ApiClient(endpoint, timeout=0.3, retries=2, retry_delay=0.01)


def test_slow_post_with_a_request_id_is_sent_once_when_the_endpoint_ignores_it(
    slow_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    server, data_dir = slow_backend
    with pytest.raises(CliError) as caught:
        _slow_client(data_dir).post("/workflows/w1/run", {}, request_id="r1")
    assert caught.value.exit_code == ExitCode.UNKNOWN
    assert caught.value.code == "outcome_unknown"
    assert "not resent" in caught.value.message
    assert server.hits == ["/api/workflows/w1/run"]  # type: ignore[attr-defined]


def test_api_command_sends_a_slow_non_deduplicating_post_once(
    slow_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    server, data_dir = slow_backend
    result = _invoke(
        "api",
        "POST",
        "/workflows/w1/run",
        "--request-id",
        "auto",
        "--timeout",
        "300ms",
        "--data-dir",
        str(data_dir),
    )
    assert result.exit_code == ExitCode.UNKNOWN
    payload = _envelope(result)
    assert payload["error"]["code"] == "outcome_unknown"
    assert len(payload["request_id"]) == 36
    assert server.hits == ["/api/workflows/w1/run"]  # type: ignore[attr-defined]


def test_slow_post_to_a_deduplicating_endpoint_is_resent_with_the_same_id(
    slow_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    server, data_dir = slow_backend
    with pytest.raises(CliError) as caught:
        _slow_client(data_dir).post("/rem/tasks", {"title": "x"}, request_id="r2")
    assert caught.value.exit_code == ExitCode.UNKNOWN
    assert "safe" in caught.value.message
    assert server.hits == ["/api/rem/tasks"] * 3  # type: ignore[attr-defined]


def test_slow_post_without_a_request_id_is_sent_once(
    slow_backend: tuple[ThreadingHTTPServer, Path],
) -> None:
    server, data_dir = slow_backend
    with pytest.raises(CliError):
        _slow_client(data_dir).post("/rem/tasks", {"title": "x"})
    assert server.hits == ["/api/rem/tasks"]  # type: ignore[attr-defined]


def test_slow_reads_are_retried(slow_backend: tuple[ThreadingHTTPServer, Path]) -> None:
    server, data_dir = slow_backend
    with pytest.raises(CliError) as caught:
        _slow_client(data_dir).get("/rem/kanban")
    assert caught.value.code == "timeout"
    assert server.hits == ["/api/rem/kanban"] * 3  # type: ignore[attr-defined]
