"""Several Codex accounts, each in its own CODEX_HOME, with a stand-in ``codex`` CLI.

The stand-in keeps a pretend sign-in (auth.json) and sessions in the CODEX_HOME it
runs with, answers the app-server's account/read and account/rateLimits/read, and
logs every call with the folder it ran in and whether OPENAI_API_KEY or
CODEX_ACCESS_TOKEN reached it. Nothing here runs the real codex or the network.
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

from gofer.core import codex_accounts
from gofer.core import provider_accounts as accounts
from gofer.core.provider_profiles import load_provider_profiles, resolve_provider_settings

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX stand-in CLI")

STAND_IN = r"""#!/usr/bin/env python3
import json, os, shutil, sys, time
args = sys.argv[1:]
home = os.environ.get("CODEX_HOME", "")
log = os.environ.get("STAND_IN_CODEX_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps({
            "args": args, "home": home,
            "openai_key": "OPENAI_API_KEY" in os.environ,
            "access_token": "CODEX_ACCESS_TOKEN" in os.environ,
            "marker": "RATICODE_UNSET_ENV" in os.environ,
        }) + "\n")
auth = os.path.join(home, "auth.json") if home else ""

def read(name, default):
    path = os.path.join(home, name)
    return json.load(open(path)) if home and os.path.exists(path) else default

if args[:2] == ["login", "--device-auth"]:
    # Codex 0.156.1's own prompt (codex-rs/login/src/device_code_auth.rs), on stdout.
    sys.stderr.write("WARNING: proceeding, even though we could not create PATH aliases\n")
    if os.path.exists(os.path.join(home, "device-off")):
        sys.stderr.write("Error logging in with device code: device code login is not "
                         "enabled for this Codex server. Use the browser login or verify "
                         "the server URL.\n")
        sys.exit(1)
    blue, gray, reset = "\x1b[94m", "\x1b[90m", "\x1b[0m"
    sys.stdout.write(
        f"\nWelcome to Codex [v{gray}0.156.1{reset}]\n"
        f"{gray}OpenAI's command-line coding agent{reset}\n\n"
        "Follow these steps to sign in with ChatGPT using device code authorization:\n\n"
        "1. Open this link in your browser and sign in to your account\n"
        f"   {blue}https://auth.openai.com/codex/device{reset}\n\n"
        f"2. Enter this one-time code {gray}(expires in 15 minutes){reset}\n"
        f"   {blue}WXYZ-12345{reset}\n\n"
        f"{gray}Continue only if you started this login in Codex. If a website or another "
        f"person gave you this code, cancel.{reset}\n\n"
    )
    sys.stdout.flush()
    deadline = time.time() + 30
    while not os.path.exists(os.path.join(home, "approved")):
        if time.time() > deadline:
            sys.stderr.write("Error logging in with device code: device auth timed out "
                             "after 15 minutes\n")
            sys.exit(1)
        time.sleep(0.05)
    who = read("who.json", {"email": "phone@example.com", "plan": "plus"})
    json.dump({"tokens": {"stand_in": True}, **who}, open(auth, "w"))
    sys.exit(0)
if args[:1] == ["login"]:
    url = "https://auth.openai.com/oauth/authorize?client_id=stand-in&state=abc"
    sys.stderr.write("Starting local login server on http://localhost:1455.\n")
    sys.stderr.write("If your browser did not open, navigate to this URL to authenticate:\n\n")
    sys.stderr.write(url + "\n")
    sys.stderr.flush()
    if os.path.exists(os.path.join(home, "hold")):
        time.sleep(30)
    refuse = os.path.join(home, "refuse")
    if os.path.exists(refuse):
        sys.stderr.write("\x1b[31mError logging in: " + open(refuse).read().strip() + "\x1b[0m\n")
        sys.exit(1)
    if os.path.exists(os.path.join(home, "no-save")):
        sys.stderr.write("Successfully logged in\n")
        sys.exit(0)
    who = read("who.json", {"email": "someone@example.com", "plan": "plus"})
    json.dump({"tokens": {"stand_in": True}, **who}, open(auth, "w"))
    sys.exit(0)
if args[:1] == ["logout"]:
    if auth and os.path.exists(auth):
        os.remove(auth)
    sys.exit(0)
if args[:1] == ["app-server"]:
    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        if ident is None:
            continue
        if method == "initialize":
            result = {"userAgent": "stand-in"}
        elif method == "account/read":
            if auth and os.path.exists(auth):
                data = json.load(open(auth))
                account = {"type": "chatgpt", "email": data["email"], "planType": data["plan"]}
            else:
                account = None
            result = {"account": account, "requiresOpenaiAuth": True}
        elif method == "account/rateLimits/read":
            result = read("limits.json", {"rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 10, "windowDurationMins": 300},
                "secondary": {"usedPercent": 20, "windowDurationMins": 10080},
            }})
        else:
            print(json.dumps({"id": ident, "error": {"code": -32601, "message": "no"}}), flush=True)
            continue
        print(json.dumps({"id": ident, "result": result}), flush=True)
    sys.exit(0)
if args[:1] == ["exec"]:
    sessions = os.path.join(home or os.path.expanduser("~/.stand-in-codex"), "sessions")
    os.makedirs(sessions, exist_ok=True)
    prompt = sys.stdin.read()
    if "resume" in args:
        thread = args[args.index("resume") + 1]
        if not os.path.exists(os.path.join(sessions, thread)):
            print(json.dumps({"type": "error", "message": "no session " + thread}))
            sys.exit(1)
    elif "fork" in args:
        parent = args[args.index("fork") + 1]
        if not os.path.exists(os.path.join(sessions, parent)):
            print(json.dumps({"type": "error", "message": "no session " + parent}))
            sys.exit(1)
        thread = parent + "-fork"
        shutil.copy(os.path.join(sessions, parent), os.path.join(sessions, thread))
    else:
        thread = "t%d" % len(os.listdir(sessions))
    open(os.path.join(sessions, thread), "a").write(prompt[-200:])
    seen = {"home": home, "openai_key": "OPENAI_API_KEY" in os.environ}
    print(json.dumps({"type": "thread.started", "thread_id": thread}))
    print(json.dumps({"type": "item.completed",
                      "item": {"type": "agent_message", "text": json.dumps(seen)}}))
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 1, "output_tokens": 1}}))
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture
def codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The stand-in codex CLI, with a parent environment that holds other sign-ins."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(STAND_IN)
    script.chmod(0o755)
    monkeypatch.setattr(codex_accounts, "_codex_executable", lambda: str(script))
    monkeypatch.setenv("STAND_IN_CODEX_LOG", str(tmp_path / "calls.log"))
    # Raticode's own environment may carry these; an account must never pass them on.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-key")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "parent-token")
    monkeypatch.delenv("CODEX_HOME", raising=False)
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


def _sign_in(data_dir: Path, name: str, email: str, plan: str = "plus") -> Path:
    folder = codex_accounts.account_home(name, data_dir)
    (folder / "who.json").write_text(json.dumps({"email": email, "plan": plan}))
    accounts.sign_ins.start(name, data_dir=data_dir)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state(name, data_dir=data_dir, check=accounts.account_status)
        if state and state["state"] != "waiting":
            assert state["state"] == "signed_in", state
            break
        time.sleep(0.05)
    accounts._status_cache.clear()
    return folder


def _wait_sign_in(name: str, data_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state(name, data_dir=data_dir, check=accounts.account_status)
        assert state is not None
        if state["state"] != "waiting":
            return state
        time.sleep(0.05)
    raise AssertionError("sign-in never finished")


def test_codex_is_a_built_in_adapter_with_owner_only_codex_home_folders(
    data_dir: Path, codex: Path
) -> None:
    adapter = accounts.account_adapter("codex")
    assert adapter is codex_accounts.ADAPTER
    assert adapter.folder == "codex-accounts" and adapter.folder_env == "CODEX_HOME"
    assert adapter.sign_in_args == ("login",) and adapter.sign_out_args == ("logout",)
    assert {"OPENAI_API_KEY", "CODEX_ACCESS_TOKEN"} <= set(adapter.other_auth_env)
    assert [p["id"] for p in accounts.providers_view()][:2] == ["claude_code", "codex"]

    work = accounts.create_account("work", provider="codex", data_dir=data_dir)
    folder = data_dir / "codex-accounts" / str(work.account_id)
    assert folder.is_dir() and stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(folder.parent.stat().st_mode) == 0o700

    # Workflow agents and Rem's replies resolve the profile: CODEX_HOME is the folder,
    # and the other sign-ins are named for removal.
    settings = resolve_provider_settings(
        agent_subscription="codex", profile_name="work", data_dir=data_dir
    )
    assert settings.env["CODEX_HOME"] == str(folder)
    assert set(settings.env["RATICODE_UNSET_ENV"].split()) >= {
        "OPENAI_API_KEY",
        "CODEX_ACCESS_TOKEN",
    }


def test_the_child_environment_never_carries_the_parents_openai_key(
    data_dir: Path, codex: Path
) -> None:
    from gofer.utils.process import build_subprocess_env

    work = accounts.create_account("work", provider="codex", data_dir=data_dir)
    folder = accounts.account_config_dir(work, data_dir)
    child = build_subprocess_env(codex_accounts.ADAPTER.folder_overrides(folder))
    assert child["CODEX_HOME"] == str(folder)
    assert "OPENAI_API_KEY" not in child and "CODEX_ACCESS_TOKEN" not in child
    assert "RATICODE_UNSET_ENV" not in child
    # The full environment an account's own commands run with is the same.
    assert "OPENAI_API_KEY" not in build_subprocess_env(codex_accounts.ADAPTER.environment(folder))
    # Without an account nothing changes.
    assert build_subprocess_env({})["OPENAI_API_KEY"] == "sk-parent-key"


def test_sign_in_runs_codex_login_in_the_folder_and_status_comes_from_account_read(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("home", provider="codex", data_dir=data_dir)
    before = accounts.accounts_overview(data_dir, provider="codex")
    assert {a["name"]: a["state"] for a in before["accounts"]} == {
        "work": "needs_sign_in",
        "home": "needs_sign_in",
    }

    work = _sign_in(data_dir, "work", "Work@Example.com", "pro")
    overview = accounts.accounts_overview(data_dir, provider="codex", refresh=True)
    rows = {a["name"]: a for a in overview["accounts"]}
    assert rows["work"]["state"] == "signed_in"
    assert rows["work"]["email"] == "Work@Example.com" and rows["work"]["plan"] == "pro"
    assert "identity" not in rows["work"]
    assert rows["home"]["state"] == "needs_sign_in"

    logins = [c for c in _calls(tmp_path) if c["args"] == ["login"]]
    assert [c["home"] for c in logins] == [str(work)]
    statuses = [c for c in _calls(tmp_path) if c["args"] == ["app-server"]]
    assert statuses and all(not c["openai_key"] and not c["access_token"] for c in _calls(tmp_path))
    assert all(not c["marker"] for c in _calls(tmp_path))

    # The same ChatGPT account in two folders gets a warning.
    _sign_in(data_dir, "home", "work@example.com")
    overview = accounts.accounts_overview(data_dir, provider="codex", refresh=True)
    assert [w["accounts"] for w in overview["warnings"]] == [["home", "work"]]
    assert "same ChatGPT account" in overview["warnings"][0]["message"]

    # Removing signs out with codex logout first, then deletes the folder.
    accounts.remove_account("work", data_dir=data_dir)
    assert not work.exists()
    assert [c["home"] for c in _calls(tmp_path) if c["args"] == ["logout"]] == [str(work)]


def test_a_refused_sign_in_shows_codexs_own_message(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    accounts.create_account("second", provider="codex", data_dir=data_dir)
    folder = codex_accounts.account_home("second", data_dir)
    message = (
        "This device already has the maximum number of paid ChatGPT accounts signed in to Codex."
    )
    (folder / "refuse").write_text(message)
    accounts.sign_ins.start("second", data_dir=data_dir)
    state = _wait_sign_in("second", data_dir)
    assert state["state"] == "failed"
    assert state["reason"] == f"Error logging in: {message}"
    # Shown on the account's row too.
    overview = accounts.accounts_overview(data_dir, provider="codex")
    assert overview["accounts"][0]["sign_in"]["reason"] == f"Error logging in: {message}"


def test_a_sign_in_that_exits_cleanly_but_isnt_signed_in_says_so_plainly(
    data_dir: Path, codex: Path
) -> None:
    accounts.create_account("odd", provider="codex", data_dir=data_dir)
    (codex_accounts.account_home("odd", data_dir) / "no-save").write_text("")
    accounts.sign_ins.start("odd", data_dir=data_dir)
    state = _wait_sign_in("odd", data_dir)
    # Codex's "Successfully logged in" would contradict account/read, so it isn't shown.
    assert state["state"] == "failed"
    assert state["reason"] == "Codex didn't finish the sign-in. Try again."


def test_a_waiting_sign_in_links_the_page_codex_printed(data_dir: Path, codex: Path) -> None:
    # The stand-in opens no browser, so the page comes from Codex's own output.
    accounts.create_account("slow", provider="codex", data_dir=data_dir)
    (codex_accounts.account_home("slow", data_dir) / "hold").write_text("")
    accounts.sign_ins.start("slow", data_dir=data_dir)
    deadline = time.monotonic() + 10
    url = None
    while time.monotonic() < deadline and url is None:
        state = accounts.sign_ins.state("slow", data_dir=data_dir)
        assert state is not None and state["state"] == "waiting"
        url = state["url"]
        time.sleep(0.05)
    assert url == "https://auth.openai.com/oauth/authorize?client_id=stand-in&state=abc"
    accounts.sign_ins.cancel("slow")


def _device_code(name: str, data_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state(name, data_dir=data_dir, check=accounts.account_status)
        assert state is not None and state["state"] == "waiting", state
        if state["code"]:
            return state
        time.sleep(0.05)
    raise AssertionError("Codex never printed a code")


def test_a_device_sign_in_gives_codexs_link_and_code_and_signs_in_when_codex_exits_0(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    accounts.create_account("phone", provider="codex", data_dir=data_dir)
    folder = codex_accounts.account_home("phone", data_dir)
    started = accounts.account_action(
        {"action": "sign_in", "name": "phone", "device": True}, data_dir
    )["started"]
    assert started["mode"] == "device" and started["state"] == "waiting"
    assert started["seconds_left"] > 600  # Codex's code lasts 15 minutes.

    state = _device_code("phone", data_dir)
    assert state["url"] == "https://auth.openai.com/codex/device"
    assert state["code"] == "WXYZ-12345"
    assert "Settings › Security" in state["help"] and state["refused"] is False
    # codex login --device-auth ran in the account's CODEX_HOME, without other sign-ins.
    devices = [c for c in _calls(tmp_path) if c["args"] == ["login", "--device-auth"]]
    assert [c["home"] for c in devices] == [str(folder)]
    assert not devices[0]["openai_key"] and not devices[0]["access_token"]
    # No browser opened on the computer: no shim recorded a page.
    assert not list((data_dir / "codex-accounts" / ".sign-in").glob("*/sign-in-url"))
    # The row shows the same sign-in, and that a phone can sign it in.
    row = accounts.accounts_overview(data_dir, provider="codex")["accounts"][0]
    assert row["phone_sign_in"] is True and row["sign_in"]["code"] == "WXYZ-12345"

    (folder / "approved").write_text("")
    done = _wait_sign_in("phone", data_dir)
    assert done["state"] == "signed_in" and done["code"] is None and done["url"] is None
    row = accounts.accounts_overview(data_dir, provider="codex", refresh=True)["accounts"][0]
    assert row["state"] == "signed_in" and row["email"] == "phone@example.com"


def test_a_refused_device_sign_in_shows_codexs_message_and_where_to_turn_it_on(
    data_dir: Path, codex: Path
) -> None:
    accounts.create_account("off", provider="codex", data_dir=data_dir)
    (codex_accounts.account_home("off", data_dir) / "device-off").write_text("")
    accounts.sign_ins.start("off", data_dir=data_dir, device=True)
    state = _wait_sign_in("off", data_dir)
    assert state["state"] == "failed" and state["refused"] is True
    assert state["reason"] == (
        "Error logging in with device code: device code login is not enabled for this "
        "Codex server. Use the browser login or verify the server URL."
    )
    assert "chatgpt.com/settings/security" in state["help"]


def test_device_code_parsing_reads_codexs_prompt_only() -> None:
    lines = [
        "WARNING: proceeding, even though we could not create PATH aliases",
        "Welcome to Codex [v0.156.1]",
        "OpenAI's command-line coding agent",
        "Follow these steps to sign in with ChatGPT using device code authorization:",
        "1. Open this link in your browser and sign in to your account",
        "https://auth.openai.com/codex/device",
        "2. Enter this one-time code (expires in 15 minutes)",
        "ABCD-EF123",
    ]
    assert accounts.device_code(lines) == "ABCD-EF123"
    assert accounts.sign_in_page(lines) == "https://auth.openai.com/codex/device"
    # Nothing that looks like a code before Codex asks for one counts.
    assert accounts.device_code(["ABCD-EF123", "Requesting a one-time code..."]) is None
    assert accounts.device_sign_in_refused("device code login is not enabled for this server")
    assert not accounts.device_sign_in_refused("device auth timed out after 15 minutes")
    assert not accounts.device_sign_in_refused(None)


def test_claude_code_accounts_have_no_device_sign_in(data_dir: Path, codex: Path) -> None:
    accounts.create_account("laptop", provider="claude_code", data_dir=data_dir)
    with pytest.raises(accounts.AccountError, match="only be signed in on your computer"):
        accounts.sign_ins.start("laptop", data_dir=data_dir, device=True)
    views = {p["id"]: p["phone_sign_in"] for p in accounts.providers_view()}
    # The phone signs in Codex and Grok accounts; Claude Code has no device sign-in.
    assert views == {"claude_code": False, "codex": True, "grok": True}


def test_cli_login_device_auth_runs_codex_device_sign_in(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    accounts.create_account("cli", provider="codex", data_dir=data_dir)
    folder = codex_accounts.account_home("cli", data_dir)
    (folder / "approved").write_text("")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["account", "login", "cli", "--device-auth", "--data-dir", str(data_dir)],
    )
    assert result.exit_code == 0, result.output
    assert "'cli' is signed in" in result.output
    assert [c["args"] for c in _calls(tmp_path) if c["args"][:1] == ["login"]] == [
        ["login", "--device-auth"]
    ]

    (folder / "auth.json").unlink()
    (folder / "device-off").write_text("")
    accounts._status_cache.clear()
    refused = runner.invoke(
        app,
        ["account", "login", "cli", "--device-auth", "--data-dir", str(data_dir)],
    )
    assert refused.exit_code == 1
    assert "Settings › Security" in refused.output


def _limits(folder: Path, primary: Any, secondary: Any, **extra: Any) -> None:
    (folder / "limits.json").write_text(
        json.dumps(
            {
                "rateLimits": {"limitId": "codex", "primary": primary, "secondary": secondary},
                **extra,
            }
        )
    )


def _refresh(data_dir: Path, codex: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import gofer.core.provider_usage as provider_usage
    from gofer.core.usage_service import refresh_usage

    # The default sign-in's row uses the stand-in too, without a CODEX_HOME.
    monkeypatch.setattr(provider_usage, "resolve_provider_executable", lambda _p: str(codex))
    return asyncio.run(refresh_usage(data_dir, providers={"codex"}, min_age=0, explicit=True))


def test_usage_comes_from_each_folders_rate_limits_and_null_windows_stay_unknown(
    data_dir: Path, codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("busy", "roomy", "nulls", "blocked", "signed-out"):
        accounts.create_account(name, provider="codex", data_dir=data_dir)
        if name != "signed-out":
            _sign_in(data_dir, name, f"{name}@example.com")
    _limits(
        codex_accounts.account_home("busy", data_dir),
        {"usedPercent": 30, "windowDurationMins": 300},
        {"usedPercent": 80, "windowDurationMins": 10080},
    )
    _limits(
        codex_accounts.account_home("roomy", data_dir),
        {"usedPercent": 60, "windowDurationMins": 300},
        {"usedPercent": 25, "windowDurationMins": 10080},
    )
    # Codex may report a window, or the whole reading, as null: that's unknown, not 0%.
    _limits(codex_accounts.account_home("nulls", data_dir), None, None)
    _limits(
        codex_accounts.account_home("blocked", data_dir),
        {"usedPercent": 5, "windowDurationMins": 300},
        {"usedPercent": 5, "windowDurationMins": 10080},
        ordinaryUsageAllowed=False,
    )

    overview = _refresh(data_dir, codex, monkeypatch)
    rows = {a.get("profile"): a for a in overview["accounts"] if a["provider"] == "codex"}
    assert rows["busy"]["source"] == "Codex account/rateLimits/read"
    assert {w["label"]: w["used"] for w in rows["busy"]["windows"]} == {
        "codex · primary · 5-hour": 30,
        "codex · secondary · 7-day": 80,
    }
    assert rows["signed-out"]["status"] == "auth_required"
    assert rows["nulls"]["windows"] == []
    homes = {c["home"] for c in _calls(tmp_path) if c["args"] == ["app-server"]}
    assert str(codex_accounts.account_home("busy", data_dir)) in homes
    # Only the default sign-in's own row may see Raticode's environment as it is.
    assert not any(c["openai_key"] or c["access_token"] for c in _calls(tmp_path) if c["home"])

    room = {
        name: accounts.account_headroom(accounts.get_account(name, data_dir), data_dir)
        for name in ("busy", "roomy", "nulls", "blocked")
    }
    # Codex reports no credits window as a percent, so only Grok's credits are read.
    nothing = {"credits": None, "credits_label": ""}
    assert room["busy"] == {"seven_day": 80.0, "five_hour": 30.0, **nothing}
    assert room["nulls"] == {"seven_day": None, "five_hour": None, **nothing}
    assert room["blocked"] == {"seven_day": 100.0, "five_hour": 100.0, **nothing}

    choice = accounts.choose_account(data_dir, provider="codex")
    assert choice is not None and choice["name"] == "roomy"
    skipped = {item["name"]: item["reason"] for item in choice["skipped"]}
    assert skipped == {"blocked": "usage limit reached", "signed-out": "needs sign-in"}
    # With only unknown readings left, the unknown account is still a choice, ranked last.
    only = accounts.choose_account(data_dir, provider="codex", names=["nulls", "busy"])
    assert only is not None and only["name"] == "busy"
    unknown = accounts.choose_account(data_dir, provider="codex", names=["nulls"])
    assert unknown is not None and unknown["weekly_percent"] is None
    assert "No recent usage reading" in unknown["why"]


def test_usage_cards_show_codex_sign_in_and_rems_next_pick(
    data_dir: Path, codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core.usage_accounts import with_account_glance
    from gofer.ui.rem_provider_accounts import usage_next_account

    for name in ("a", "b"):
        accounts.create_account(name, provider="codex", data_dir=data_dir)
    _sign_in(data_dir, "a", "a@example.com")
    overview = _refresh(data_dir, codex, monkeypatch)
    config = {"provider_accounts": {"codex": "auto"}}
    pick = usage_next_account(config, data_dir, provider="codex")
    glance = with_account_glance(overview, data_dir, rem_picks={"codex": pick})
    rows = {a.get("profile"): a for a in glance["accounts"] if a["provider"] == "codex"}
    assert rows["a"]["sign_in_state"] == "signed_in" and rows["a"]["rem_next"]["why"]
    assert rows["b"]["sign_in_state"] == "needs_sign_in" and "rem_next" not in rows["b"]


def test_new_accounts_copy_only_the_allowlisted_setup_and_never_a_sign_in(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    shared = tmp_path / "shared-codex"
    (shared / "skills" / "review").mkdir(parents=True)
    (shared / "skills" / "review" / "SKILL.md").write_text("---\nname: review\n---\n")
    (shared / "AGENTS.md").write_text("Be brief.")
    (shared / "auth.json").write_text('{"tokens": "secret"}')
    (shared / "history.jsonl").write_text("{}")
    (shared / "sessions").mkdir()
    (shared / "config.toml").write_text(
        'model = "gpt-6"\n'
        'preferred_auth_method = "apikey"\n'
        'forced_login_method = "api"\n'
        'cli_auth_credentials_store = "keyring"\n'
        'model_provider = "proxy"\n'
        "[model_providers.proxy]\n"
        'base_url = "https://proxy.example"\n'
        'env_key = "PROXY_KEY"\n'
        "[mcp_servers.docs]\n"
        'command = "docs-mcp"\n'
        'bearer_token = "abc"\n'
        'env = { DOCS_TOKEN = "xyz" }\n'
        "[profiles.fast]\n"
        'model = "gpt-6-mini"\n'
        'model_provider = "proxy"\n'
    )
    account = accounts.create_account(
        "work", provider="codex", data_dir=data_dir, copy_shared_setup=True, setup_source=shared
    )
    folder = accounts.account_config_dir(account, data_dir)
    assert sorted(p.name for p in folder.iterdir()) == ["AGENTS.md", "config.toml", "skills"]
    assert (folder / "skills" / "review" / "SKILL.md").is_file()
    config = (folder / "config.toml").read_text()
    for gone in (
        "preferred_auth_method",
        "forced_login_method",
        "cli_auth_credentials_store",
        "model_provider",
        "proxy",
        "PROXY_KEY",
        "bearer_token",
        "abc",
        "xyz",
    ):
        assert gone not in config, gone
    import tomllib

    kept = tomllib.loads(config)
    assert kept["model"] == "gpt-6"
    assert kept["mcp_servers"]["docs"] == {"command": "docs-mcp"}
    assert kept["profiles"]["fast"] == {"model": "gpt-6-mini"}
    # A symlinked config is never followed.
    (shared / "config.toml").unlink()
    (shared / "config.toml").symlink_to(shared / "auth.json")
    other = accounts.create_account(
        "other", provider="codex", data_dir=data_dir, copy_shared_setup=True, setup_source=shared
    )
    assert not (accounts.account_config_dir(other, data_dir) / "config.toml").exists()


def test_turns_mcp_names_and_skills_read_the_accounts_codex_home(
    data_dir: Path, codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core import rem_library
    from gofer.core.prompt_envelope import AgentResources, resource_cli_args

    own = tmp_path / "own-codex"
    (own / "skills" / "mine").mkdir(parents=True)
    (own / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
    (own / "config.toml").write_text('[mcp_servers.own_server]\ncommand = "x"\n')
    monkeypatch.setenv("CODEX_HOME", str(own))
    account = accounts.create_account("work", provider="codex", data_dir=data_dir)
    folder = accounts.account_config_dir(account, data_dir)
    (folder / "skills" / "theirs").mkdir(parents=True)
    (folder / "skills" / "theirs" / "SKILL.md").write_text("---\nname: theirs\n---\n")
    (folder / "config.toml").write_text('[mcp_servers.account_server]\ncommand = "y"\n')
    project = tmp_path / "project"
    project.mkdir()

    args = resource_cli_args("codex", AgentResources.none(), project, codex_home=folder)
    assert "mcp_servers.account_server.enabled=false" in args
    assert "mcp_servers.own_server.enabled=false" not in args
    default = resource_cli_args("codex", AgentResources.none(), project)
    assert "mcp_servers.own_server.enabled=false" in default

    def codex_skills(**kwargs: Any) -> set[str]:
        return {
            s["name"]
            for s in rem_library.discover_skills(data_dir, **kwargs)
            if s["origin"] == "codex"
        }

    assert codex_skills() == {"mine"}
    assert codex_skills(codex_home=folder) == {"theirs"}
    # Settings > Rem naming a Codex account makes the library read that account's folder.
    monkeypatch.setattr(
        codex_accounts, "_rem_config", lambda _d: {"provider_accounts": {"codex": "work"}}
    )
    assert codex_skills() == {"theirs"}
    monkeypatch.setattr(
        codex_accounts, "_rem_config", lambda _d: {"provider_accounts": {"codex": "auto"}}
    )
    assert codex_skills() == {"mine"}


@pytest.mark.asyncio
async def test_codex_exec_resume_and_fork_run_in_the_accounts_folder(
    data_dir: Path, codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.ui import chat

    async def validate(*_args: Any) -> None:
        return None

    monkeypatch.setattr(chat, "validate_provider_selection_async", validate)
    monkeypatch.setattr(chat, "provider_preference", lambda _: {})
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _p: str(codex))
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _: None)
    monkeypatch.setattr(chat, "with_second_brain", lambda w, *a: w)
    monkeypatch.setattr(chat, "with_report_outputs", lambda w, *a: w)
    account = accounts.create_account("work", provider="codex", data_dir=data_dir)
    folder = accounts.account_config_dir(account, data_dir)
    (tmp_path / "project").mkdir()
    workflow = {"projectRoot": str(tmp_path / "project")}

    async def turn(messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        events = [
            event
            async for event in chat.stream_workflow_chat(
                "codex",
                "cli-default",
                messages,
                workflow,
                data_dir=data_dir,
                provider_profile="work",
                **kwargs,
            )
        ]
        assert events[-1]["type"] == "final", events[-1]
        return events[-1]

    first = [{"role": "user", "body": "hello"}]
    answer = await turn(first, conversation_id="thread-a")
    assert json.loads(answer["message"]["body"]) == {"home": str(folder), "openai_key": False}
    second = [*first, {"role": "assistant", "body": "hi"}, {"role": "user", "body": "again"}]
    resumed = await turn(second, conversation_id="thread-a")
    assert resumed.get("sessionResumed") is True
    # A fork of the whole thread copies its session, in the same account's folder.
    forked = await turn(
        [*second, {"role": "assistant", "body": "ok"}, {"role": "user", "body": "fork"}],
        conversation_id="thread-b",
        fork_from="thread-a",
    )
    assert json.loads(forked["message"]["body"])["home"] == str(folder)
    execs = [c for c in _calls(tmp_path) if c["args"][:1] == ["exec"]]
    assert len(execs) == 3
    assert "resume" in execs[1]["args"] and "fork" in execs[2]["args"]
    assert all(c["home"] == str(folder) and not c["openai_key"] for c in execs)
    assert all(not c["access_token"] and not c["marker"] for c in execs)
    # The sessions live in the account's own folder.
    assert sorted(p.name for p in (folder / "sessions").iterdir()) == ["t0", "t0-fork"]


@pytest.mark.asyncio
async def test_the_steering_app_server_gets_the_accounts_folder(
    data_dir: Path, codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.ui import codex_steering
    from gofer.ui.chat import _account_env

    account = accounts.create_account("work", provider="codex", data_dir=data_dir)
    folder = accounts.account_config_dir(account, data_dir)
    seen: list[dict[str, str]] = []
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["env"])
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(codex_steering.asyncio, "create_subprocess_exec", spawn)
    env = _account_env("codex", "work", data_dir)
    events = [
        event
        async for event in codex_steering.stream_codex_turn(
            [str(codex), "exec", "--sandbox", "workspace-write", "--json", "hi"],
            control=codex_steering.CodexTurnControl(),
            cwd=data_dir,
            env=env,
            cancel_event=None,
            max_output_bytes=None,
        )
    ]
    assert seen and seen[0]["CODEX_HOME"] == str(folder)
    assert "OPENAI_API_KEY" not in seen[0] and "CODEX_ACCESS_TOKEN" not in seen[0]
    # The stand-in app-server can't start a thread, so before turn/start the turn
    # falls back to codex exec, in the same folder and still without the parent's key.
    assert events[-1]["type"] == "exit" and events[-1]["returncode"] == 0
    calls = _calls(tmp_path)
    assert [c["args"][0] for c in calls] == ["app-server", "exec"]
    assert all(c["home"] == str(folder) and not c["openai_key"] for c in calls)


def test_cli_adds_signs_in_and_lists_codex_accounts(data_dir: Path, codex: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    runner = CliRunner()
    base = ["--data-dir", str(data_dir)]
    added = runner.invoke(app, ["account", "add", "work", "--provider", "codex", *base])
    assert added.exit_code == 0, added.output
    assert "Added Codex account 'work'" in added.output and "is signed in" in added.output
    listed = runner.invoke(
        app, ["account", "list", "--provider", "codex", "--format", "json", *base]
    )
    rows = json.loads(listed.output)["accounts"]
    assert [(r["name"], r["state"], r["email"], r["plan"]) for r in rows] == [
        ("work", "signed_in", "someone@example.com", "plus")
    ]
    assert load_provider_profiles(data_dir)["work"].subscription == "codex"
    removed = runner.invoke(app, ["account", "rm", "work", "--provider", "codex", "-y", *base])
    assert removed.exit_code == 0, removed.output


def test_copy_setup_again_merges_the_cleaned_config_and_never_touches_sign_ins(
    data_dir: Path, codex: Path, tmp_path: Path
) -> None:
    import tomllib

    shared = tmp_path / "shared-codex"
    (shared / "skills" / "review").mkdir(parents=True)
    (shared / "skills" / "review" / "SKILL.md").write_text("review")
    (shared / "AGENTS.md").write_text("Be brief.")
    (shared / "auth.json").write_text('{"tokens": "source secret"}')
    (shared / "config.toml").write_text('model = "gpt-6"\n[mcp_servers.docs]\ncommand = "docs"\n')
    account = accounts.create_account(
        "work", provider="codex", data_dir=data_dir, copy_shared_setup=True, setup_source=shared
    )
    folder = accounts.account_config_dir(account, data_dir)
    (folder / "auth.json").write_text('{"tokens": "the account\'s own"}')
    (folder / "sessions").mkdir()
    (folder / "sessions" / "one.jsonl").write_text("{}")
    # Codex adds settings of its own to the account's config; they don't count as a change.
    (folder / "config.toml").write_text(
        'model = "gpt-6"\ncli_auth_credentials_store = "file"\n'
        '[mcp_servers.docs]\ncommand = "docs"\n[projects."/work"]\ntrust_level = "trusted"\n'
    )
    assert accounts.setup_changes(account, data_dir, shared) == []

    # Sign-in settings in the source never count, and never arrive.
    text = (shared / "config.toml").read_text()
    (shared / "config.toml").write_text('preferred_auth_method = "apikey"\n' + text)
    assert accounts.setup_changes(account, data_dir, shared) == []
    (shared / "config.toml").write_text(
        'model = "gpt-7"\npreferred_auth_method = "apikey"\n'
        '[mcp_servers.docs]\ncommand = "docs"\nbearer_token = "abc"\n'
    )
    (shared / "skills" / "review" / "SKILL.md").write_text("review, sharper")
    assert accounts.setup_changes(account, data_dir, shared) == ["skills", "config.toml"]

    copied = accounts.recopy_setup("work", data_dir=data_dir, setup_source=str(shared))
    assert copied == ["skills", "config.toml"]
    config = tomllib.loads((folder / "config.toml").read_text())
    assert config["model"] == "gpt-7"
    assert config["mcp_servers"]["docs"] == {"command": "docs"}
    # The account's own settings, including its sign-in setting, stay.
    assert config["cli_auth_credentials_store"] == "file"
    assert config["projects"]["/work"] == {"trust_level": "trusted"}
    assert (
        "preferred_auth_method" not in config and "abc" not in (folder / "config.toml").read_text()
    )
    assert (folder / "skills" / "review" / "SKILL.md").read_text() == "review, sharper"
    assert (folder / "AGENTS.md").read_text() == "Be brief."
    assert json.loads((folder / "auth.json").read_text()) == {"tokens": "the account's own"}
    assert (folder / "sessions" / "one.jsonl").read_text() == "{}"
    assert accounts.setup_changes(account, data_dir, shared) == []
