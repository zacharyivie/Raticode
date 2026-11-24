"""Claude Code accounts: one official config folder each, never their credentials."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core import claude_accounts as accounts
from gofer.core.provider_profiles import (
    load_provider_profiles,
    resolve_provider_settings,
    save_provider_profiles,
)
from gofer.core.usage_ledger import profile_usage_identity, record_invocation

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake CLI")

# A stand-in for Claude Code's own CLI. It keeps a pretend sign-in in a file of
# the folder CLAUDE_CONFIG_DIR names, so each account folder is signed in on its own.
FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
folder = os.environ["CLAUDE_CONFIG_DIR"]
login = os.path.join(folder, "fake-login.json")
args = sys.argv[1:]
with open(os.path.join(folder, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\n")
if args[:2] == ["auth", "status"]:
    if os.path.exists(login):
        data = json.load(open(login))
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": data["email"],
                          "orgId": "org", "subscriptionType": "max", "planTier": "max_5x",
                          "loginExpiresAt": data.get("expires", "2999-01-01T00:00:00Z")}))
        sys.exit(0)
    print(json.dumps({"loggedIn": False, "authMethod": "none", "loginExpiresAt": None}))
    sys.exit(1)
if args[:2] == ["auth", "login"]:
    if sys.stdin.read() != "":
        sys.exit(3)
    browser = os.environ.get("BROWSER")
    if browser:
        subprocess.run([browser, "https://claude.com/cai/oauth/authorize?fake=1"], check=False,
                       env={**os.environ, "PATH": "/nonexistent"})
    email = open(os.path.join(folder, "who")).read().strip() if os.path.exists(
        os.path.join(folder, "who")) else "someone@example.com"
    json.dump({"email": email}, open(login, "w"))
    sys.exit(0)
if args[:2] == ["auth", "logout"]:
    if os.path.exists(login):
        os.remove(login)
    sys.exit(0)
if args[:2] == ["-p", "/usage"]:
    if not os.path.exists(login):
        sys.exit(1)
    usage = json.load(open(os.path.join(folder, "usage.json"))) if os.path.exists(
        os.path.join(folder, "usage.json")) else {"five_hour": {"utilization": 5}}
    print(json.dumps({"type": "result", "usage_report": {"rate_limits": usage}}))
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(FAKE_CLAUDE)
    script.chmod(0o755)
    monkeypatch.setattr(accounts, "_claude_executable", lambda: str(script))
    import gofer.core.provider_usage as provider_usage

    monkeypatch.setattr(provider_usage, "resolve_provider_executable", lambda _p: str(script))
    accounts._status_cache.clear()
    return script


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


def _sign_in_directly(data_dir: Path, name: str, email: str) -> Path:
    folder = accounts.account_config_dir(accounts.get_account(name, data_dir), data_dir)
    (folder / "fake-login.json").write_text(json.dumps({"email": email}))
    accounts._status_cache.clear()
    return folder


def test_each_account_gets_an_owner_only_folder_named_by_an_internal_id(data_dir: Path) -> None:
    work = accounts.create_account("work", data_dir=data_dir)
    home = accounts.create_account("home", data_dir=data_dir)

    assert work.account_id and home.account_id and work.account_id != home.account_id
    root = data_dir / "claude-accounts"
    for profile in (work, home):
        folder = root / str(profile.account_id)
        assert folder.is_dir()
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert folder.name == profile.account_id
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    saved = load_provider_profiles(data_dir)
    assert saved["work"].subscription == "claude_code"
    assert saved["work"].account_id == work.account_id

    # Renaming keeps the folder, so the sign-in that lives in it stays.
    renamed = accounts.rename_account("work", "office", data_dir=data_dir)
    assert renamed.account_id == work.account_id
    assert (root / str(work.account_id)).is_dir()


def test_runs_with_an_account_use_its_folder_and_keep_their_usage_history(data_dir: Path) -> None:
    profile = accounts.create_account("work", data_dir=data_dir)
    settings = resolve_provider_settings(
        agent_subscription="claude_code", profile_name="work", data_dir=data_dir
    )
    assert settings.env["CLAUDE_CONFIG_DIR"] == str(
        data_dir / "claude-accounts" / str(profile.account_id)
    )
    before = profile_usage_identity("work", data_dir)
    profiles = load_provider_profiles(data_dir)
    profiles["work"] = profiles["work"].model_copy(update={"model": "claude-opus-5-5"})
    save_provider_profiles(profiles, data_dir)
    assert profile_usage_identity("work", data_dir) == before


def test_shared_setup_is_copied_from_an_allowlist_and_never_a_sign_in(tmp_path: Path) -> None:
    source = tmp_path / "home-claude"
    (source / "skills" / "one").mkdir(parents=True)
    (source / "skills" / "one" / "SKILL.md").write_text("skill")
    (source / "CLAUDE.md").write_text("rules")
    (source / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "secret"}}')
    (source / "settings.json").write_text("{}")
    (source / "history.jsonl").write_text("{}")
    (source / "projects").mkdir()
    (source / "projects" / "chat.jsonl").write_text("{}")
    (source / "agents").symlink_to(source / "projects")
    target = tmp_path / "account"
    target.mkdir()

    copied = accounts.copy_setup(source, target)

    assert sorted(copied) == ["CLAUDE.md", "skills"]
    assert (target / "skills" / "one" / "SKILL.md").read_text() == "skill"
    for kept_out in (".credentials.json", "settings.json", "history.jsonl", "projects", "agents"):
        assert not (target / kept_out).exists()


def test_status_reads_only_claude_codes_own_status_output() -> None:
    signed_out = accounts.parse_status({"loggedIn": False, "authMethod": "none"})
    assert signed_out["state"] == accounts.NEEDS_SIGN_IN

    signed_in = accounts.parse_status(
        {"loggedIn": True, "email": "a@example.com", "orgId": "o", "planTier": "max_20x"}
    )
    assert signed_in["state"] == accounts.SIGNED_IN
    assert signed_in["plan"] == "max_20x"
    assert signed_in["identity"] and "a@example.com" not in signed_in["identity"]

    expired = accounts.parse_status(
        {"loggedIn": True, "email": "a@example.com", "loginExpiresAt": "2020-01-01T00:00:00Z"}
    )
    assert expired["state"] == accounts.NEEDS_SIGN_IN
    assert "expired" in expired["reason"]


def test_two_folders_signed_in_to_the_same_account_get_a_warning(
    data_dir: Path, fake_claude: Path
) -> None:
    for name in ("work", "home", "spare"):
        accounts.create_account(name, data_dir=data_dir)
    _sign_in_directly(data_dir, "work", "same@example.com")
    _sign_in_directly(data_dir, "home", "SAME@example.com")

    overview = accounts.accounts_overview(data_dir, refresh=True)

    views = {view["name"]: view for view in overview["accounts"]}
    assert views["spare"]["needs_sign_in"] is True
    assert views["work"]["state"] == accounts.SIGNED_IN
    assert [w["accounts"] for w in overview["warnings"]] == [["home", "work"]]
    assert views["work"]["duplicate_of"] == ["home"]
    assert "identity" not in views["work"]


def test_one_click_sign_in_runs_claudes_own_login_and_sees_it_finish(
    data_dir: Path, fake_claude: Path
) -> None:
    accounts.create_account("work", data_dir=data_dir)

    overview = accounts.account_action({"action": "sign_in", "name": "work"}, data_dir)
    assert overview["started"]["state"] == "waiting"
    deadline = time.monotonic() + 20
    state = None
    while time.monotonic() < deadline:
        state = accounts.sign_ins.state("work", data_dir=data_dir, check=accounts.check_status)
        if state and state["state"] != "waiting":
            break
        time.sleep(0.1)

    assert state is not None and state["state"] == "signed_in"
    view = accounts.accounts_overview(data_dir)["accounts"][0]
    assert view["state"] == accounts.SIGNED_IN and view["needs_sign_in"] is False
    folder = data_dir / "claude-accounts" / str(accounts.get_account("work", data_dir).account_id)
    calls = (folder / "calls.log").read_text().splitlines()
    assert "auth login --claudeai" in calls
    # The sign-in's private scratch folder is gone once it finished.
    assert not any((data_dir / "claude-accounts" / ".sign-in").iterdir())
    # Switching between accounts is only a different folder; nothing signs in again.
    accounts.create_account("home", data_dir=data_dir)
    _sign_in_directly(data_dir, "home", "home@example.com")
    for _ in range(3):
        for name in ("work", "home"):
            assert (
                accounts.check_status(
                    accounts.get_account(name, data_dir), data_dir=data_dir, refresh=True
                )["state"]
                == accounts.SIGNED_IN
            )
    assert (folder / "calls.log").read_text().count("auth login") == 1


def test_the_browser_shim_records_the_sign_in_page_for_a_fallback_link(
    data_dir: Path, fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accounts.create_account("work", data_dir=data_dir)
    held = fake_claude.parent / "hold"
    # Keep the login waiting so its sign-in page can be read while it runs.
    fake_claude.write_text(
        FAKE_CLAUDE.replace(
            "    email = open", "    import time\n    time.sleep(5)\n    email = open"
        )
    )
    accounts.sign_ins.start("work", data_dir=data_dir)
    try:
        url = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not url:
            url = (accounts.sign_ins.state("work") or {}).get("url")
            time.sleep(0.1)
        assert url == "https://claude.com/cai/oauth/authorize?fake=1"
    finally:
        accounts.sign_ins.cancel("work")
    assert accounts.sign_ins.state("work") is None
    assert not held.exists()


def test_removing_an_account_signs_it_out_first_then_deletes_its_folder(
    data_dir: Path, fake_claude: Path
) -> None:
    accounts.create_account("work", data_dir=data_dir)
    folder = _sign_in_directly(data_dir, "work", "w@example.com")
    log = data_dir.parent / "logout.log"
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append(command[1:])
        log.write_text(str(Path(str(kwargs["env"]["CLAUDE_CONFIG_DIR"])) == folder))  # type: ignore[index]
        return None

    accounts.remove_account("work", data_dir=data_dir, run=run)

    assert calls == [["auth", "logout"]]
    assert log.read_text() == "True"
    assert not folder.exists()
    assert "work" not in load_provider_profiles(data_dir)


def _reading(data_dir: Path, name: str, weekly: float | None, hourly: float | None) -> None:
    windows = []
    for window_id, used in (("seven_day", weekly), ("five_hour", hourly)):
        if used is not None:
            windows.append({"id": window_id, "label": window_id, "unit": "percent", "used": used})
    record_invocation(
        invocation_id=f"call-{name}",
        provider="claude_code",
        profile=name,
        model="claude-opus-5-5",
        metadata={"input_tokens": 10, "output_tokens": 5, "quota_windows": windows},
        data_dir=data_dir,
    )


def test_rem_picks_the_signed_in_account_with_the_most_room(
    data_dir: Path, fake_claude: Path
) -> None:
    for name in ("busy", "roomy", "signed-out", "unknown"):
        accounts.create_account(name, data_dir=data_dir)
    _sign_in_directly(data_dir, "busy", "busy@example.com")
    _sign_in_directly(data_dir, "roomy", "roomy@example.com")
    _sign_in_directly(data_dir, "unknown", "unknown@example.com")
    _reading(data_dir, "busy", 80, 10)
    _reading(data_dir, "roomy", 20, 50)
    _reading(data_dir, "signed-out", 0, 0)

    choice = accounts.choose_account(data_dir)
    assert choice is not None and choice["name"] == "roomy"
    assert {"name": "signed-out", "reason": "needs sign-in"} in choice["skipped"]
    assert "20%" in choice["why"]

    # A full 5-hour window takes an account out, however much weekly room it has.
    _reading(data_dir, "roomy", 20, 100)
    assert accounts.choose_account(data_dir)["name"] == "busy"  # type: ignore[index]
    _reading(data_dir, "busy", 100, 10)
    # Only the account without readings is left.
    assert accounts.choose_account(data_dir)["name"] == "unknown"  # type: ignore[index]


def test_the_ledger_counts_each_accounts_tokens_separately(data_dir: Path) -> None:
    from gofer.core.usage_ledger import summarize_calls, usage_db

    accounts.create_account("work", data_dir=data_dir)
    accounts.create_account("home", data_dir=data_dir)
    _reading(data_dir, "work", 10, 10)
    _reading(data_dir, "home", 10, 10)
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT provider, profile, tokens, estimated, partial FROM usage_invocations"
        ).fetchall()
    groups = {(g["provider"], g["profile"]) for g in summarize_calls(rows)["providers"]}
    assert groups == {("claude_code", "work"), ("claude_code", "home")}


def test_an_explicit_choice_beats_rems_own_pick(data_dir: Path, fake_claude: Path) -> None:
    from gofer.ui.rem_claude_accounts import pick_account, pick_run_account

    for name in ("mine", "other"):
        accounts.create_account(name, data_dir=data_dir)
    auto = {"claude_account": "auto"}

    def choose(_data_dir: Path) -> dict[str, Any]:
        return {"name": "other", "why": "room"}

    run: dict[str, Any] = {}
    picked = pick_run_account(
        run,
        {**auto, "claude_account": "mine", "claude_account_explicit": True},
        data_dir,
        choose=choose,
    )
    assert picked["name"] == "mine" and picked["source"] == "task"
    assert pick_run_account(run, auto, data_dir, choose=choose)["name"] == "other"
    # A run stays on the account it started on.
    kept = {"claude_account": {"name": "mine", "source": "auto"}}
    assert pick_run_account(kept, auto, data_dir, choose=choose)["name"] == "mine"
    # With no room anywhere, Rem says so and uses the default sign-in.
    fallback = pick_account(auto, data_dir, choose=lambda _d: None)
    assert fallback["name"] == "" and "default sign-in" in fallback["why"]
    assert pick_account({}, data_dir)["source"] == "default"


def test_account_usage_comes_only_from_claude_code_and_never_reads_a_token(
    data_dir: Path, fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.core.provider_usage as provider_usage
    from gofer.core.usage_credentials import UsageCredentialError, save_usage_credentials

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an account's credentials must never be read")

    monkeypatch.setattr(provider_usage, "_claude_native_token", forbidden)
    monkeypatch.setattr(provider_usage, "_claude_allowance", forbidden)
    accounts.create_account("work", data_dir=data_dir)
    accounts.create_account("home", data_dir=data_dir)
    folder = _sign_in_directly(data_dir, "work", "w@example.com")
    (folder / "usage.json").write_text(
        json.dumps({"seven_day": {"utilization": 42}, "five_hour": {"utilization": 7}})
    )

    work = asyncio.run(
        provider_usage.refresh_provider_usage("claude_code", "work", data_dir=data_dir)
    )
    home = asyncio.run(
        provider_usage.refresh_provider_usage("claude_code", "home", data_dir=data_dir)
    )

    assert work["source"] == "Claude Code /usage" and work["status"] == "available"
    assert {w["id"]: w["used"] for w in work["windows"]} == {"seven_day": 42, "five_hour": 7}
    assert home["status"] == "auth_required" and home["needs_sign_in"] is True
    with pytest.raises(UsageCredentialError):
        save_usage_credentials("claude_code", "work", {"oauth_token": "x"}, data_dir=data_dir)


def test_generic_profile_edits_cant_make_or_move_an_account(data_dir: Path) -> None:
    from gofer.ui.api import ProviderProfileError, upsert_provider_profile_payload

    with pytest.raises(ProviderProfileError):
        upsert_provider_profile_payload(
            {"name": "sneaky", "subscription": "claude_code", "account_id": "0" * 16}, data_dir
        )
    accounts.create_account("work", data_dir=data_dir)
    with pytest.raises(ProviderProfileError):
        upsert_provider_profile_payload(
            {"name": "work", "subscription": "claude_code", "account_id": "1" * 16}, data_dir
        )
    with pytest.raises(ValueError):
        upsert_provider_profile_payload(
            {
                "name": "work",
                "subscription": "claude_code",
                "account_id": accounts.get_account("work", data_dir).account_id,
                "env": {"CLAUDE_CONFIG_DIR": "/tmp/elsewhere"},
            },
            data_dir,
        )


def test_turns_name_their_account_and_no_tool_reads_any_account_folder(data_dir: Path) -> None:
    from gofer.ui import claude_sandbox
    from gofer.ui.chat import ChatProviderError, _claude_account_env, claude_account_deny_rules
    from gofer.ui.chat_sessions import session_key

    assert claude_account_deny_rules(data_dir) == []
    profile = accounts.create_account("work", data_dir=data_dir)
    env = _claude_account_env("claude_code", "work", data_dir)
    assert env["CLAUDE_CONFIG_DIR"] == str(data_dir / "claude-accounts" / str(profile.account_id))
    # An API key or token in Raticode's own environment never replaces the folder's sign-in.
    assert env["RATICODE_UNSET_ENV"].split() == list(accounts.OTHER_AUTH_ENV)
    assert set(env) == {"CLAUDE_CONFIG_DIR", "RATICODE_UNSET_ENV"}
    assert _claude_account_env("claude_code", None, data_dir) == {}
    with pytest.raises(ChatProviderError):
        _claude_account_env("codex", "work", data_dir)
    with pytest.raises(ChatProviderError):
        _claude_account_env("claude_code", "missing", data_dir)
    rules = claude_account_deny_rules(data_dir)
    root = str((data_dir / "claude-accounts").resolve())
    assert f"Read(/{root}/**)" in rules and f"Edit(/{root}/**)" in rules
    assert "claude-accounts" in claude_sandbox.DATA_SECRETS
    assert session_key("claude_code", "work") != session_key("claude_code", "home")
    assert session_key("claude_code", None) == "claude_code"


def test_cli_lists_accounts_and_marks_the_ones_that_need_sign_in(
    data_dir: Path, fake_claude: Path
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app
    from gofer.cli.commands.system import _usage_text

    accounts.create_account("work", data_dir=data_dir)
    accounts.create_account("home", data_dir=data_dir)
    _sign_in_directly(data_dir, "work", "w@example.com")
    result = CliRunner().invoke(
        app, ["account", "list", "--format", "json", "--data-dir", str(data_dir)]
    )
    assert result.exit_code == 0, result.output
    states = {a["name"]: a["state"] for a in json.loads(result.output)["accounts"]}
    assert states == {"work": "signed_in", "home": "needs_sign_in"}

    text = " ".join(
        _usage_text(
            {
                "accounts": [
                    {"provider": "claude_code", "profile": "work", "status": "available"},
                    {"provider": "claude_code", "profile": "home", "needs_sign_in": True},
                ]
            }
        ).split()
    )
    assert "ACCOUNT" in text and "home needs sign-in" in text and "work available" in text
    assert os.environ.get("CLAUDE_CONFIG_DIR") != str(data_dir)


def test_a_thread_on_auto_stays_on_its_account_until_it_needs_sign_in(
    data_dir: Path, fake_claude: Path
) -> None:
    from gofer.ui.chat_sessions import _digest
    from gofer.ui.rem_claude_accounts import thread_account

    for name in ("work", "home"):
        accounts.create_account(name, data_dir=data_dir)
        _sign_in_directly(data_dir, name, f"{name}@example.com")
    assert thread_account("t1", "work", data_dir) == "work"
    assert thread_account("t1", "", data_dir) is None
    session = data_dir / "chat-provider-sessions" / _digest("t1")
    session.mkdir(parents=True)
    (session / "session.json").write_text(json.dumps({"lastProvider": "claude_code@home"}))
    assert thread_account("t1", "auto", data_dir, choose=lambda _d: {"name": "work"}) == "home"
    (
        data_dir
        / "claude-accounts"
        / str(accounts.get_account("home", data_dir).account_id)
        / "fake-login.json"
    ).unlink()
    accounts._status_cache.clear()
    assert thread_account("t1", "auto", data_dir, choose=lambda _d: {"name": "work"}) == "work"
