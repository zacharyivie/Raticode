"""Several Grok accounts, each in its own GROK_HOME, with a stand-in ``grok`` CLI.

The stand-in keeps a pretend sign-in (auth.json) in the GROK_HOME it runs with, answers
the ACP agent's ``_x.ai/billing`` only when that folder is signed in (as Grok 1.0.89
does, with error -32000 "Authentication required" otherwise), runs a turn, and logs
every call with the folder it ran in and whether XAI_API_KEY or another sign-in
variable reached it. Nothing here runs the real grok or the network.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core import grok_accounts
from gofer.core import provider_accounts as accounts
from gofer.core.provider_profiles import load_provider_profiles, resolve_provider_settings

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX stand-in CLI")

STAND_IN = r"""#!/usr/bin/env python3
import json, os, subprocess, sys, time
args = sys.argv[1:]
home = os.environ.get("GROK_HOME", "")
log = os.environ.get("STAND_IN_GROK_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps({
            "args": args, "home": home,
            "xai_key": "XAI_API_KEY" in os.environ,
            "old_key": "GROK_CODE_XAI_API_KEY" in os.environ,
            "auth_path": "GROK_AUTH_PATH" in os.environ,
            "marker": "RATICODE_UNSET_ENV" in os.environ,
        }) + "\n")
auth = os.path.join(home, "auth.json") if home else ""

def read(name, default):
    path = os.path.join(home, name)
    return json.load(open(path)) if home and os.path.exists(path) else default

if args[:2] == ["login", "--device-auth"]:
    # grok 1.0.89's own prompt and errors (xai-grok-login/src/device_code.rs), on stderr.
    def fail(message):
        sys.stderr.write("Error: " + message + "\n")
        sys.exit(1)
    if os.path.exists(os.path.join(home, "device-off")):
        fail("Device-code login is not available for this deployment. Try `grok login` "
             "or set XAI_API_KEY instead.")
    code = "ABCD-1234"
    link = "https://accounts.x.ai/device"
    if os.path.exists(os.path.join(home, "complete-link")):
        link += "?user_code=" + code
    sys.stderr.write("\nTo sign in, open this URL in your browser:\n  " + link + "\n")
    # Grok opens the link itself (the webbrowser crate, $BROWSER first).
    browser = os.environ.get("BROWSER")
    opened = subprocess.run([browser, link]).returncode if browser else None
    with open(os.path.join(home, "browser.json"), "w") as handle:
        json.dump({"browser": browser, "exit": opened}, handle)
    if opened != 0:
        sys.stderr.write("  (Could not open browser automatically — open the URL above "
                         "manually.)\n")
    sys.stderr.write("\n" + ("Confirm this code in your browser:" if "?" in link
                             else "Then enter this code:") + "\n  " + code + "\n\n")
    sys.stderr.write("\x1b[90mOnly continue with a code you requested. Don't share it with "
                     "anyone.\x1b[0m\n\nWaiting for authorization...\n")
    sys.stderr.flush()
    if os.path.exists(os.path.join(home, "device-wait")):
        deadline = time.time() + 30
        while not os.path.exists(os.path.join(home, "approved")):
            if os.path.exists(os.path.join(home, "expire")) or time.time() > deadline:
                fail("Device code expired. Run `grok login --device-auth` again.")
            time.sleep(0.05)
    json.dump({"stand_in": True}, open(auth, "w"))
    sys.stderr.write("\u2713 Signed in\n")
    sys.exit(0)
if args[:1] == ["login"]:
    sys.stderr.write("Opening your browser to sign in...\n")
    sys.stderr.write("https://auth.x.ai/oauth2/authorize?client_id=stand-in&state=abc\n")
    sys.stderr.flush()
    if os.path.exists(os.path.join(home, "hold")):
        time.sleep(30)
    refuse = os.path.join(home, "refuse")
    if os.path.exists(refuse):
        sys.stderr.write("\x1b[31mLogin failed: " + open(refuse).read().strip() + "\x1b[0m\n")
        sys.exit(1)
    json.dump({"stand_in": True}, open(auth, "w"))
    sys.exit(0)
if args[:1] == ["logout"]:
    if auth and os.path.exists(auth):
        os.remove(auth)
    sys.exit(0)
if args[:1] == ["agent"] and args[-1:] == ["stdio"]:
    os.makedirs(os.path.join(home or ".", "sessions"), exist_ok=True)
    if home and not os.path.exists(os.path.join(home, "config.toml")):
        open(os.path.join(home, "config.toml"), "w").write("# written by grok\n")
    def reply(ident, **body):
        print(json.dumps({"jsonrpc": "2.0", "id": ident, **body}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        if ident is None or method is None:
            continue
        if method == "initialize":
            reply(ident, result={"protocolVersion": 1, "agentCapabilities": {},
                                 "authMethods": [{"id": "grok.com", "name": "Grok"}]})
        elif method == "_x.ai/billing":
            if os.path.exists(os.path.join(home, "broken")):
                reply(ident, error={"code": -32603, "message": "upstream unavailable"})
            elif auth and os.path.exists(auth):
                reply(ident, result=read("billing.json", {"config": {
                    "creditUsagePercent": 25,
                    "currentPeriod": {"type": "USAGE_PERIOD_TYPE_MONTHLY",
                                      "end": "2026-12-01T00:00:00Z"}}}))
            else:
                reply(ident, error={"code": -32000, "message": "Authentication required",
                                    "data": "Authentication required to fetch billing data"})
        elif method == "session/new":
            reply(ident, result={"sessionId": "s1"})
        elif method == "session/prompt":
            seen = {"home": home, "xai_key": "XAI_API_KEY" in os.environ}
            print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": json.dumps(seen)}}}}), flush=True)
            reply(ident, result={"stopReason": "end_turn"})
        else:
            reply(ident, error={"code": -32601, "message": "no " + method})
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture
def grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The stand-in grok CLI, with a parent environment that holds other sign-ins."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "grok"
    script.write_text(STAND_IN)
    script.chmod(0o755)
    monkeypatch.setattr(grok_accounts, "_grok_executable", lambda: str(script))
    monkeypatch.setenv("STAND_IN_GROK_LOG", str(tmp_path / "calls.log"))
    # Raticode's own environment may carry these; an account must never pass them on.
    monkeypatch.setenv("XAI_API_KEY", "xai-parent-key")
    monkeypatch.setenv("GROK_CODE_XAI_API_KEY", "xai-old-name")
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "shared-auth.json"))
    monkeypatch.delenv("GROK_HOME", raising=False)
    # The stand-in opens $BROWSER as Grok does; never the real one from here.
    monkeypatch.delenv("BROWSER", raising=False)
    accounts._status_cache.clear()
    try:
        yield script
    finally:
        accounts.sign_ins.cancel_all()
        accounts._status_cache.clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


def _calls(tmp_path: Path) -> list[dict[str, Any]]:
    log = tmp_path / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _no_other_sign_in(call: dict[str, Any]) -> bool:
    return not (call["xai_key"] or call["old_key"] or call["auth_path"] or call["marker"])


def _wait_sign_in(name: str, data_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state(name, data_dir=data_dir, check=accounts.account_status)
        assert state is not None
        if state["state"] != "waiting":
            return state
        time.sleep(0.05)
    raise AssertionError("sign-in never finished")


def _sign_in(data_dir: Path, name: str) -> Path:
    accounts.sign_ins.start(name, data_dir=data_dir)
    assert _wait_sign_in(name, data_dir)["state"] == "signed_in"
    accounts._status_cache.clear()
    return grok_accounts.account_home(name, data_dir)


def test_grok_is_a_built_in_adapter_with_owner_only_grok_home_folders(
    data_dir: Path, grok: Path
) -> None:
    adapter = accounts.account_adapter("grok")
    assert adapter is grok_accounts.ADAPTER
    assert adapter.folder == "grok-accounts" and adapter.folder_env == "GROK_HOME"
    assert adapter.sign_in_args == ("login",) and adapter.sign_out_args == ("logout",)
    assert adapter.device_sign_in_args == ("login", "--device-auth")
    assert "XAI_API_KEY" in adapter.other_auth_env
    assert [p["id"] for p in accounts.providers_view()] == ["claude_code", "codex", "grok"]
    # A new Grok account starts empty: there's no setup to copy.
    assert not accounts.providers_view()[2]["can_copy_setup"]

    work = accounts.create_account("work", provider="grok", data_dir=data_dir)
    folder = data_dir / "grok-accounts" / str(work.account_id)
    assert folder.is_dir() and stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(folder.parent.stat().st_mode) == 0o700
    assert list(folder.iterdir()) == []

    # Workflow agents and Rem's replies resolve the profile: GROK_HOME is the folder,
    # and the other sign-ins are named for removal.
    settings = resolve_provider_settings(
        agent_subscription="grok", profile_name="work", data_dir=data_dir
    )
    assert settings.env["GROK_HOME"] == str(folder)
    assert set(settings.env["RATICODE_UNSET_ENV"].split()) >= {
        "XAI_API_KEY",
        "GROK_CODE_XAI_API_KEY",
        "GROK_AUTH_PATH",
    }


def test_the_child_environment_never_carries_the_parents_xai_key(
    data_dir: Path, grok: Path
) -> None:
    from gofer.utils.process import build_subprocess_env

    work = accounts.create_account("work", provider="grok", data_dir=data_dir)
    folder = accounts.account_config_dir(work, data_dir)
    child = build_subprocess_env(grok_accounts.ADAPTER.folder_overrides(folder))
    assert child["GROK_HOME"] == str(folder)
    for name in ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY", "GROK_AUTH_PATH", "RATICODE_UNSET_ENV"):
        assert name not in child
    # Without an account nothing changes: the default sign-in keeps its API key fallback.
    assert build_subprocess_env({})["XAI_API_KEY"] == "xai-parent-key"


def test_status_is_whether_billing_answers_and_sign_in_runs_grok_login_in_the_folder(
    data_dir: Path, grok: Path, tmp_path: Path
) -> None:
    accounts.create_account("work", provider="grok", data_dir=data_dir)
    accounts.create_account("home", provider="grok", data_dir=data_dir)
    before = accounts.accounts_overview(data_dir, provider="grok")
    assert {a["name"]: a["state"] for a in before["accounts"]} == {
        "work": "needs_sign_in",
        "home": "needs_sign_in",
    }

    work = _sign_in(data_dir, "work")
    overview = accounts.accounts_overview(data_dir, provider="grok", refresh=True)
    rows = {a["name"]: a for a in overview["accounts"]}
    assert rows["work"]["state"] == "signed_in"
    assert rows["home"]["state"] == "needs_sign_in"
    # Grok reports no identity, so no same-account warning can be made.
    assert overview["warnings"] == []

    calls = _calls(tmp_path)
    assert [c["home"] for c in calls if c["args"] == ["login"]] == [str(work)]
    checks = [c for c in calls if c["args"] == ["agent", "--no-leader", "stdio"]]
    home = grok_accounts.account_home("home", data_dir)
    assert {c["home"] for c in checks} == {str(work), str(home)}
    assert all(_no_other_sign_in(c) for c in calls)

    # Grok picks up a changed auth.json by itself; the next check just asks again.
    (work / "auth.json").unlink()
    again = accounts.account_status(
        accounts.get_account("work", data_dir), data_dir=data_dir, refresh=True
    )
    assert again["state"] == "needs_sign_in"

    # Another failure doesn't read as signed out.
    (work / "broken").write_text("")
    broken = accounts.account_status(
        accounts.get_account("work", data_dir), data_dir=data_dir, refresh=True
    )
    assert broken["state"] == "unknown" and "upstream unavailable" in broken["reason"]
    assert accounts.choose_account(data_dir, provider="grok") is None


def test_groks_authentication_required_error_over_the_shared_transport_reads_not_signed_in_yet(
    data_dir: Path, grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core import provider_usage

    opened: list[tuple[list[str], dict[str, Any]]] = []
    real = provider_usage.open_acp_transport

    def spy(command: list[str], **options: Any) -> Any:
        opened.append((command, options))
        return real(command, **options)

    monkeypatch.setattr(provider_usage, "open_acp_transport", spy)
    work = accounts.create_account("work", provider="grok", data_dir=data_dir)
    folder = accounts.account_config_dir(work, data_dir)

    # The stand-in answers -32000 "Authentication required", as Grok 1.0.89 does.
    status = accounts.account_status(work, data_dir=data_dir, refresh=True)
    assert status == {
        "state": "needs_sign_in",
        "plan": None,
        "email": None,
        "identity": None,
        "reason": "Not signed in yet.",
        "checked_at": status["checked_at"],
    }
    command, options = opened[-1]
    assert command[-4:] == [str(grok), *grok_accounts.ACP_ARGS]
    assert options["cwd"] == folder
    assert options["timeout"] == accounts.STATUS_TIMEOUT_SECONDS
    assert options["max_output_bytes"] == 4 * 1024 * 1024
    assert options["env"]["GROK_HOME"] == str(folder)

    # Another refusal shows Grok's own words, without the transport's prefix.
    (folder / "broken").write_text("")
    broken = accounts.account_status(work, data_dir=data_dir, refresh=True)
    assert broken["state"] == "unknown"
    assert broken["reason"] == "Grok couldn't report this account: upstream unavailable."
    # The transport starts from Raticode's own environment; the parent's keys stay out.
    checks = [c for c in _calls(tmp_path) if c["args"] == list(grok_accounts.ACP_ARGS)]
    assert len(checks) == 2 and all(c["home"] == str(folder) for c in checks)
    assert all(_no_other_sign_in(c) for c in checks)


def test_remove_signs_out_with_grok_logout_first_then_deletes_the_folder(
    data_dir: Path, grok: Path, tmp_path: Path
) -> None:
    accounts.create_account("work", provider="grok", data_dir=data_dir)
    work = _sign_in(data_dir, "work")
    seen: list[bool] = []
    real_run = accounts.subprocess.run

    def run(command: list[str], **kwargs: Any) -> Any:
        # The folder and its sign-in still exist when grok logout runs.
        seen.append((work / "auth.json").exists())
        return real_run(command, **kwargs)

    accounts.remove_account("work", data_dir=data_dir, run=run)
    assert seen == [True]
    assert not work.exists()
    logouts = [c for c in _calls(tmp_path) if c["args"] == ["logout"]]
    assert [c["home"] for c in logouts] == [str(work)] and _no_other_sign_in(logouts[0])
    assert "work" not in load_provider_profiles(data_dir)


def test_a_refused_sign_in_shows_groks_own_message_and_a_waiting_one_links_its_page(
    data_dir: Path, grok: Path
) -> None:
    accounts.create_account("second", provider="grok", data_dir=data_dir)
    folder = grok_accounts.account_home("second", data_dir)
    (folder / "refuse").write_text("This team doesn't allow Grok Build.")
    accounts.sign_ins.start("second", data_dir=data_dir)
    state = _wait_sign_in("second", data_dir)
    assert state["state"] == "failed"
    assert state["reason"] == "Login failed: This team doesn't allow Grok Build."

    accounts.create_account("slow", provider="grok", data_dir=data_dir)
    (grok_accounts.account_home("slow", data_dir) / "hold").write_text("")
    accounts.sign_ins.start("slow", data_dir=data_dir)
    deadline = time.monotonic() + 10
    url = None
    while time.monotonic() < deadline and url is None:
        waiting = accounts.sign_ins.state("slow", data_dir=data_dir)
        assert waiting is not None and waiting["state"] == "waiting"
        url = waiting["url"]
        time.sleep(0.05)
    assert url == "https://auth.x.ai/oauth2/authorize?client_id=stand-in&state=abc"
    accounts.sign_ins.cancel("slow")


def _device_code(name: str, data_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state(name, data_dir=data_dir, check=accounts.account_status)
        assert state is not None and state["state"] == "waiting", state
        if state["code"]:
            return state
        time.sleep(0.05)
    raise AssertionError("Grok never printed a code")


def test_a_phone_device_sign_in_gives_groks_link_and_code_and_keeps_the_browser_shut(
    data_dir: Path, grok: Path, tmp_path: Path
) -> None:
    assert accounts.providers_view()[2]["phone_sign_in"] is True
    accounts.create_account("phone", provider="grok", data_dir=data_dir)
    folder = grok_accounts.account_home("phone", data_dir)
    (folder / "device-wait").write_text("")
    started = accounts.account_action(
        {"action": "sign_in", "name": "phone", "device": True}, data_dir
    )["started"]
    assert started["mode"] == "device" and started["state"] == "waiting"
    # Grok waits at least 10 minutes for its code; the countdown starts there.
    assert 590 < started["seconds_left"] <= 600

    state = _device_code("phone", data_dir)
    assert state["url"] == "https://accounts.x.ai/device"
    assert state["code"] == "ABCD-1234"
    assert "Settings › Providers › Grok" in state["help"] and state["refused"] is False
    # grok login --device-auth ran in the account's GROK_HOME, without other sign-ins.
    devices = [c for c in _calls(tmp_path) if c["args"] == ["login", "--device-auth"]]
    assert [c["home"] for c in devices] == [str(folder)] and _no_other_sign_in(devices[0])
    # Grok's own attempt to open the link reached only Raticode's no-op $BROWSER.
    opened = json.loads((folder / "browser.json").read_text())
    assert opened["exit"] == 0
    assert Path(opened["browser"]).parent.parent == data_dir / "grok-accounts" / ".sign-in"
    row = accounts.accounts_overview(data_dir, provider="grok")["accounts"][0]
    assert row["phone_sign_in"] is True and row["sign_in"]["code"] == "ABCD-1234"

    (folder / "approved").write_text("")
    done = _wait_sign_in("phone", data_dir)
    assert done["state"] == "signed_in" and done["code"] is None and done["url"] is None
    row = accounts.accounts_overview(data_dir, provider="grok", refresh=True)["accounts"][0]
    assert row["state"] == "signed_in"


def test_a_grok_link_that_holds_the_code_still_gives_the_code(data_dir: Path, grok: Path) -> None:
    accounts.create_account("filled", provider="grok", data_dir=data_dir)
    folder = grok_accounts.account_home("filled", data_dir)
    (folder / "device-wait").write_text("")
    (folder / "complete-link").write_text("")
    accounts.sign_ins.start("filled", data_dir=data_dir, device=True)
    state = _device_code("filled", data_dir)
    assert state["url"] == "https://accounts.x.ai/device?user_code=ABCD-1234"
    assert state["code"] == "ABCD-1234"
    accounts.sign_ins.cancel("filled")


def test_xai_refusing_device_sign_in_shows_groks_words_and_the_computer_sign_in(
    data_dir: Path, grok: Path
) -> None:
    accounts.create_account("off", provider="grok", data_dir=data_dir)
    (grok_accounts.account_home("off", data_dir) / "device-off").write_text("")
    accounts.sign_ins.start("off", data_dir=data_dir, device=True)
    state = _wait_sign_in("off", data_dir)
    assert state["state"] == "failed" and state["refused"] is True
    assert state["reason"] == (
        "Device-code login is not available for this deployment. Try `grok login` or set "
        "XAI_API_KEY instead."
    )
    assert state["help"] == grok_accounts.DEVICE_SIGN_IN_HELP


def test_an_expired_grok_code_reads_as_not_finished_in_time(data_dir: Path, grok: Path) -> None:
    accounts.create_account("late", provider="grok", data_dir=data_dir)
    folder = grok_accounts.account_home("late", data_dir)
    (folder / "device-wait").write_text("")
    accounts.sign_ins.start("late", data_dir=data_dir, device=True)
    _device_code("late", data_dir)
    (folder / "expire").write_text("")
    state = _wait_sign_in("late", data_dir)
    assert state["state"] == "timed_out" and state["refused"] is False
    assert state["reason"] == "The sign-in wasn't finished in time."


def test_device_code_parsing_reads_groks_prompt() -> None:
    # grok 1.0.89's stderr, as Raticode reads it (indents and colors stripped).
    lines = [
        "To sign in, open this URL in your browser:",
        "https://accounts.x.ai/device",
        "Then enter this code:",
        "ABCD-1234",
        "Only continue with a code you requested. Don't share it with anyone.",
        "Waiting for authorization...",
    ]
    assert accounts.device_code(lines) == "ABCD-1234"
    assert accounts.sign_in_page(lines) == "https://accounts.x.ai/device"
    # Grok takes any letters, digits and hyphens from xAI, not only Codex's shape.
    assert accounts.device_code(["Confirm this code in your browser:", "WDJB-MJHT-9X"]) == (
        "WDJB-MJHT-9X"
    )
    assert accounts.device_code(["Then enter this code:", "Waiting for authorization..."]) is None
    assert accounts.device_sign_in_refused(
        "Device-code login is not available for this deployment."
    )
    assert accounts.device_sign_in_expired(
        "Device code expired. Run `grok login --device-auth` again."
    )
    assert not accounts.device_sign_in_expired("Authorization denied. The user rejected it.")
    assert accounts.sign_in_message(["Error: Authorization denied."]) == "Authorization denied."


def _refresh(data_dir: Path, grok: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import gofer.core.provider_usage as provider_usage
    from gofer.core.usage_service import refresh_usage

    # The default sign-in's row uses the stand-in too, without a GROK_HOME.
    monkeypatch.setattr(provider_usage, "resolve_provider_executable", lambda _p: str(grok))
    return asyncio.run(refresh_usage(data_dir, providers={"grok"}, min_age=0, explicit=True))


def test_usage_comes_from_each_folders_billing(
    data_dir: Path, grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core.usage_accounts import with_account_glance
    from gofer.ui.rem_provider_accounts import usage_next_account

    for name in ("busy", "roomy", "signed-out"):
        accounts.create_account(name, provider="grok", data_dir=data_dir)
        if name != "signed-out":
            _sign_in(data_dir, name)
    (grok_accounts.account_home("busy", data_dir) / "billing.json").write_text(
        json.dumps({"config": {"creditUsagePercent": 90}})
    )

    overview = _refresh(data_dir, grok, monkeypatch)
    rows = {a.get("profile"): a for a in overview["accounts"] if a["provider"] == "grok"}
    assert rows["busy"]["source"] == "grok _x.ai/billing"
    assert {w["label"]: w["used"] for w in rows["busy"]["windows"]} == {"Credits": 90}
    assert {w["label"]: w["used"] for w in rows["roomy"]["windows"]} == {"Monthly credits": 25}
    assert rows["signed-out"]["status"] == "auth_required" and rows["signed-out"]["needs_sign_in"]
    homes = {c["home"] for c in _calls(tmp_path) if c["args"] == ["agent", "--no-leader", "stdio"]}
    assert str(grok_accounts.account_home("busy", data_dir)) in homes
    # Only the default sign-in's own row may see Raticode's environment as it is.
    assert all(_no_other_sign_in(c) for c in _calls(tmp_path) if c["home"])
    assert any(c["xai_key"] for c in _calls(tmp_path) if not c["home"])

    # The Usage cards carry each account's own sign-in state and Rem's next pick.
    pick = usage_next_account({"provider_accounts": {"grok": "auto"}}, data_dir, provider="grok")
    assert pick is not None and pick["name"] in {"busy", "roomy"}
    glance = with_account_glance(overview, data_dir, rem_picks={"grok": pick})
    cards = {a.get("profile"): a for a in glance["accounts"] if a["provider"] == "grok"}
    assert cards["signed-out"]["sign_in_state"] == "needs_sign_in"
    assert cards[pick["name"]]["rem_next"]["why"]


def _store_credits(data_dir: Path, name: str, used: float, label: str = "Monthly credits") -> None:
    """A Grok account's stored credits window, the way a usage refresh saves it."""
    from datetime import UTC, datetime, timedelta

    from gofer.core.usage_ledger import profile_usage_identity, usage_db

    now = datetime.now(UTC)
    window = {
        "id": "credits",
        "label": label,
        "unit": "percent",
        "used": used,
        "limit": 100.0,
        "remaining_percent": 100.0 - used,
        "resets_at": (now + timedelta(days=12)).isoformat(),
    }
    observed = (now - timedelta(minutes=2)).isoformat()
    with usage_db(data_dir) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_snapshots VALUES (?, ?, ?)",
            (
                profile_usage_identity(name, data_dir),
                json.dumps({"observed_at": observed, "windows": [window]}),
                observed,
            ),
        )


def test_choose_by_usage_ranks_grok_accounts_by_their_credits(data_dir: Path, grok: Path) -> None:
    for name in ("alpha", "bravo", "charlie", "delta", "echo", "unread"):
        accounts.create_account(name, provider="grok", data_dir=data_dir)
    _store_credits(data_dir, "alpha", 70)
    _store_credits(data_dir, "bravo", 40)
    _store_credits(data_dir, "charlie", 100)
    _store_credits(data_dir, "delta", 40)
    _store_credits(data_dir, "echo", 10)

    def check(profile: Any, **_: Any) -> dict[str, Any]:
        signed_out = profile.name == "echo"
        return {"state": "needs_sign_in" if signed_out else accounts.SIGNED_IN}

    room = accounts.account_headroom(accounts.get_account("bravo", data_dir), data_dir)
    assert room == {
        "seven_day": None,
        "five_hour": None,
        "credits": 40.0,
        "credits_label": "Monthly credits",
    }
    choice = accounts.choose_account(data_dir, provider="grok", check=check)
    # bravo and delta tie at 40%; the name decides. echo has the most room but is signed out.
    assert choice is not None and choice["name"] == "bravo"
    assert choice["credits_percent"] == 40.0
    assert choice["why"] == "Most room left: 40% of its monthly credits used."
    skipped = {item["name"]: item["reason"] for item in choice["skipped"]}
    assert skipped == {"charlie": "credits used up", "echo": "needs sign-in"}
    # An account without a reading comes after every account with one.
    later = accounts.choose_account(
        data_dir, provider="grok", names=["unread", "alpha"], check=check
    )
    assert later is not None and later["name"] == "alpha"
    assert later["why"] == "Most room left: 70% of its monthly credits used."
    # A period without a type is plain "credits"; only full accounts left means no choice.
    _store_credits(data_dir, "alpha", 5, label="Credits")
    plain = accounts.choose_account(data_dir, provider="grok", names=["alpha"], check=check)
    assert plain is not None and plain["why"] == "Most room left: 5% of its credits used."
    full = accounts.choose_account(data_dir, provider="grok", names=["charlie"], check=check)
    assert full is None


@pytest.mark.asyncio
async def test_a_grok_thread_runs_in_its_accounts_folder_without_the_parents_key(
    data_dir: Path, grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.subscriptions import acp_providers
    from gofer.ui import chat

    async def validate(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def identity(*_args: Any) -> str:
        return "stand-in"

    monkeypatch.setattr(chat, "validate_provider_selection_async", validate)
    monkeypatch.setattr(chat, "provider_preference", lambda _: {})
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _p: str(grok))
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _: None)
    monkeypatch.setattr(chat, "with_second_brain", lambda w, *a: w)
    monkeypatch.setattr(chat, "with_report_outputs", lambda w, *a: w)
    monkeypatch.setattr(acp_providers, "grok_build_version", identity)
    account = accounts.create_account("work", provider="grok", data_dir=data_dir)
    folder = accounts.account_config_dir(account, data_dir)
    (tmp_path / "project").mkdir()

    async def turn(profile: str | None) -> dict[str, Any]:
        events = [
            event
            async for event in chat.stream_workflow_chat(
                "grok",
                "cli-default",
                [{"role": "user", "body": "hello"}],
                {"projectRoot": str(tmp_path / "project")},
                data_dir=data_dir,
                provider_profile=profile,
                permission_mode="cli-managed",
            )
        ]
        assert events[-1]["type"] == "final", events[-1]
        seen: dict[str, Any] = json.loads(events[-1]["message"]["body"])
        return seen

    assert await turn("work") == {"home": str(folder), "xai_key": False}
    # The default sign-in keeps Raticode's own environment, as before.
    assert await turn(None) == {"home": "", "xai_key": True}
    agents = [c for c in _calls(tmp_path) if c["args"][:1] == ["agent"]]
    assert agents[0]["home"] == str(folder) and _no_other_sign_in(agents[0])


@pytest.mark.asyncio
async def test_the_acp_mask_gives_back_only_the_turns_own_account(
    data_dir: Path, grok: Path, monkeypatch: pytest.MonkeyPatch, credential_guards: None
) -> None:
    from gofer.core import credential_mask
    from gofer.subscriptions import acp_transport

    monkeypatch.setattr(credential_mask, "unavailable_reason", lambda *_a: None)
    monkeypatch.setattr(credential_mask.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("GOFER_DATA_DIR", str(data_dir))
    work = accounts.account_config_dir(
        accounts.create_account("work", provider="grok", data_dir=data_dir), data_dir
    )
    other = accounts.account_config_dir(
        accounts.create_account("other", provider="grok", data_dir=data_dir), data_dir
    )
    for folder in (work, other):
        (folder / "auth.json").write_text("{}")
        (folder / "hooks").mkdir()
        (folder / "trusted_folders.toml").write_text("")
    # No config.toml yet: the mask creates an empty one first, so the turn can't.
    assert not (work / "config.toml").exists()
    seen: list[tuple[str, ...]] = []

    async def spawn(*args: str, **_kwargs: Any) -> Any:
        seen.append(args)
        raise OSError("stop here")

    monkeypatch.setattr(acp_transport.asyncio, "create_subprocess_exec", spawn)
    env = grok_accounts.ADAPTER.folder_overrides(work)
    with pytest.raises(OSError):
        async with acp_transport.open_acp_transport(
            [str(grok), "agent", "--no-leader", "stdio"], cwd=data_dir, env=env
        ):
            pass
    command = list(seen[0])
    assert command[0] == "/usr/bin/bwrap"
    mounts = command[: command.index("--")]
    root = str(data_dir / "grok-accounts")
    # The whole grok-accounts folder is covered, and only this account is given back.
    assert ["--tmpfs", root] == mounts[mounts.index(root) - 1 : mounts.index(root) + 1]
    assert ["--bind", str(work), str(work)] == mounts[
        mounts.index("--bind") : mounts.index("--bind") + 3
    ]
    assert str(other) not in mounts
    # The files in it that start programs or trust a folder stay read-only.
    for name in ("config.toml", "hooks", "trusted_folders.toml"):
        assert ["--ro-bind", str(work / name), str(work / name)] == mounts[
            mounts.index(str(work / name)) - 1 : mounts.index(str(work / name)) + 2
        ]
    assert (work / "config.toml").read_text() == ""
    assert str(work / "auth.json") not in mounts
    assert command[command.index("--") + 1 :] == [str(grok), "agent", "--no-leader", "stdio"]


def test_cli_adds_signs_in_with_device_auth_lists_and_removes_grok_accounts(
    data_dir: Path, grok: Path, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    runner = CliRunner()
    base = ["--data-dir", str(data_dir)]
    added = runner.invoke(
        app, ["account", "add", "work", "--provider", "grok", "--device-auth", *base]
    )
    assert added.exit_code == 0, added.output
    assert "Added Grok account 'work'" in added.output and "is signed in" in added.output
    logins = [c for c in _calls(tmp_path) if c["args"][:1] == ["login"]]
    assert logins[-1]["args"] == ["login", "--device-auth"]
    listed = runner.invoke(
        app, ["account", "list", "--provider", "grok", "--format", "json", *base]
    )
    rows = json.loads(listed.output)["accounts"]
    assert [(r["name"], r["provider"], r["state"]) for r in rows] == [("work", "grok", "signed_in")]
    # Providers without a device-code sign-in say so instead of running something else.
    accounts.create_account("cc", provider="claude_code", data_dir=data_dir)
    refused = runner.invoke(app, ["account", "login", "cc", "--device-auth", *base])
    assert refused.exit_code == 1 and "no device-code sign-in" in refused.output
    removed = runner.invoke(app, ["account", "rm", "work", "--provider", "grok", "-y", *base])
    assert removed.exit_code == 0, removed.output
    assert [c["args"] for c in _calls(tmp_path)][-1] == ["logout"]
