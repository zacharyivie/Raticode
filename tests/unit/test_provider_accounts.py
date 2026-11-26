"""Provider accounts work for any CLI provider with an adapter.

A stand-in adapter in Codex's place, with a stand-in CLI, shows that nothing
in the registry, the profile validator, usage, Rem's account choice, chat turns,
the deny rules or the CLI is Claude-only. Claude Code's own behavior is covered
unchanged by test_claude_accounts.py, and the real Codex adapter by
test_codex_accounts.py.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts as accounts
from gofer.core.provider_profiles import (
    ProviderProfile,
    load_provider_profiles,
    resolve_provider_settings,
    save_provider_profiles,
    validate_provider_profile,
)
from gofer.core.usage_ledger import profile_usage_identity, record_invocation
from gofer.rattish import preflight

# The real sign-in check; tests/conftest.py replaces it with "unknown" for every test.
REAL_ACCOUNT_SIGN_IN_STATE = preflight.account_sign_in_state

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake CLI")

# A stand-in provider CLI. It keeps a pretend sign-in in the folder STAND_IN_HOME
# names, prints a one-line status and a JSON usage report.
FAKE_CLI = r"""#!/usr/bin/env python3
import json, os, sys
folder = os.environ["STAND_IN_HOME"]
login = os.path.join(folder, "login.json")
args = sys.argv[1:]
with open(os.path.join(folder, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\n")
if args == ["login", "status"]:
    if os.path.exists(login):
        data = json.load(open(login))
        print(f"Logged in as {data['email']} plan={data.get('plan', 'pro')}")
    else:
        print("Not logged in")
    sys.exit(0)
if args == ["login"]:
    who = os.path.join(folder, "who")
    email = open(who).read().strip() if os.path.exists(who) else "someone@example.com"
    json.dump({"email": email}, open(login, "w"))
    sys.exit(0)
if args == ["logout"]:
    if os.path.exists(login):
        os.remove(login)
    sys.exit(0)
if args == ["usage", "--json"]:
    usage = os.path.join(folder, "usage.json")
    print(open(usage).read() if os.path.exists(usage) else json.dumps({"weekly": 5}))
    sys.exit(0)
sys.exit(2)
"""


def _read_status(output: str) -> dict[str, Any]:
    line = output.strip()
    if not line.startswith("Logged in as "):
        return {"state": accounts.NEEDS_SIGN_IN, "reason": "Not signed in yet.", "plan": None}
    email, _, plan = line.removeprefix("Logged in as ").partition(" plan=")
    return {
        "state": accounts.SIGNED_IN,
        "plan": plan or None,
        "email": email,
        "identity": f"id-{email.lower()}",
    }


@pytest.fixture
def stand_in(tmp_path: Path) -> Iterator[Path]:
    """Register a stand-in Codex adapter for one test, then put the real one back."""
    real = accounts.account_adapter("codex")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(FAKE_CLI)
    script.chmod(0o755)

    async def read_usage(
        executable: str, env: dict[str, str], _data_dir: Path | None
    ) -> list[dict[str, Any]] | None:
        process = await asyncio.create_subprocess_exec(
            executable, "usage", "--json", env=env, stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        weekly = json.loads(stdout or b"null").get("weekly")
        return [{"id": "seven_day", "label": "Weekly", "unit": "percent", "used": weekly}]

    accounts.register_adapter(
        accounts.AccountAdapter(
            provider="codex",
            label="Stand-in Codex",
            cli_name="codex",
            folder_env="STAND_IN_HOME",
            sign_in_args=("login",),
            sign_out_args=("logout",),
            status_args=("login", "status"),
            read_status=_read_status,
            read_usage=read_usage,
            usage_source="Stand-in usage",
            other_auth_env=("OPENAI_API_KEY",),
            executable=lambda: str(script),
            browser_shim=False,
        )
    )
    accounts._status_cache.clear()
    try:
        yield script
    finally:
        accounts.sign_ins.cancel_all()
        accounts.unregister_adapter("codex")
        if real is not None:
            accounts.register_adapter(real)
        accounts._status_cache.clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


def _sign_in(data_dir: Path, name: str, email: str) -> Path:
    folder = accounts.account_config_dir(accounts.get_account(name, data_dir), data_dir)
    (folder / "login.json").write_text(json.dumps({"email": email}))
    accounts._status_cache.clear()
    return folder


def test_claude_code_is_the_first_adapter_and_keeps_its_folder() -> None:
    claude = accounts.account_adapter("claude_code")
    assert claude is not None and claude.folder == "claude-accounts"
    assert claude.folder_env == "CLAUDE_CONFIG_DIR"
    assert claude.sign_in_args == ("auth", "login", "--claudeai")
    assert claude.status_args == ("auth", "status", "--json")
    assert accounts.account_adapter("cursor") is None
    assert [p["id"] for p in accounts.providers_view()] == ["claude_code", "codex", "grok"]


def test_a_second_adapter_gets_its_own_folders_and_profiles(data_dir: Path, stand_in: Path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=data_dir)
    claude = accounts.create_account("mine", provider="claude_code", data_dir=data_dir)

    folder = data_dir / "codex-accounts" / str(work.account_id)
    assert folder.is_dir() and stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert (data_dir / "claude-accounts" / str(claude.account_id)).is_dir()
    assert load_provider_profiles(data_dir)["work"].subscription == "codex"
    assert set(accounts.list_accounts(data_dir, "codex")) == {"work"}
    assert set(accounts.list_accounts(data_dir)) == {"work", "mine"}

    settings = resolve_provider_settings(
        agent_subscription="codex", profile_name="work", data_dir=data_dir
    )
    assert settings.env["STAND_IN_HOME"] == str(folder)
    assert "CLAUDE_CONFIG_DIR" not in settings.env
    # Another sign-in in the profile would label someone else's use as this account.
    profile = load_provider_profiles(data_dir)["work"]
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_provider_profile(profile.model_copy(update={"env": {"OPENAI_API_KEY": "x"}}))
    with pytest.raises(ValueError, match="STAND_IN_HOME"):
        validate_provider_profile(profile.model_copy(update={"env": {"STAND_IN_HOME": "/x"}}))
    # The usage history follows the folder, not the profile's other settings.
    before = profile_usage_identity("work", data_dir)
    profiles = load_provider_profiles(data_dir)
    profiles["work"] = profiles["work"].model_copy(update={"model": "gpt-6"})
    save_provider_profiles(profiles, data_dir)
    assert profile_usage_identity("work", data_dir) == before


def test_the_validator_accepts_account_id_only_for_providers_with_an_adapter(
    stand_in: Path,
) -> None:
    codex = ProviderProfile(name="a", subscription="codex", account_id="0" * 16)
    validate_provider_profile(codex)
    with pytest.raises(ValueError, match="can't be accounts"):
        validate_provider_profile(
            ProviderProfile(name="b", subscription="cursor", account_id="0" * 16)
        )
    with pytest.raises(ValueError, match="account ID"):
        validate_provider_profile(codex.model_copy(update={"account_id": "not-an-id"}))
    accounts.unregister_adapter("codex")
    with pytest.raises(ValueError, match="can't be accounts"):
        validate_provider_profile(codex)


def test_sign_in_status_and_sign_out_use_the_adapters_own_commands(
    data_dir: Path, stand_in: Path
) -> None:
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("home", provider="codex", data_dir=data_dir)

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

    folder = accounts.account_config_dir(accounts.get_account("work", data_dir), data_dir)
    assert (folder / "calls.log").read_text().splitlines()[0] == "login"
    views = {
        v["name"]: v for v in accounts.accounts_overview(data_dir, provider="codex")["accounts"]
    }
    assert views["work"]["state"] == accounts.SIGNED_IN and views["work"]["plan"] == "pro"
    assert views["work"]["label"] == "Stand-in Codex · work"
    assert views["home"]["needs_sign_in"] is True
    assert "identity" not in views["work"]

    calls: list[tuple[list[str], str]] = []

    def run(command: list[str], **kwargs: Any) -> None:
        calls.append((command[1:], kwargs["env"]["STAND_IN_HOME"]))

    accounts.remove_account("work", data_dir=data_dir, run=run)
    assert calls == [(["logout"], str(folder))]
    assert not folder.exists()
    assert "work" not in load_provider_profiles(data_dir)


def test_shared_sign_in_warnings_stay_within_one_provider(
    data_dir: Path, stand_in: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, provider in (("a", "codex"), ("b", "codex"), ("c", "claude_code")):
        accounts.create_account(name, provider=provider, data_dir=data_dir)

    def check(profile: ProviderProfile, **_: object) -> dict[str, Any]:
        return {"state": accounts.SIGNED_IN, "identity": "same"}

    overview = accounts.accounts_overview(data_dir, check=check)
    assert [(w["provider"], w["accounts"]) for w in overview["warnings"]] == [("codex", ["a", "b"])]
    assert "same Stand-in Codex account" in overview["warnings"][0]["message"]
    assert {p["id"] for p in overview["providers"]} == {"claude_code", "codex", "grok"}


def test_usage_comes_from_the_adapter_and_marks_accounts_that_need_sign_in(
    data_dir: Path, stand_in: Path
) -> None:
    import gofer.core.provider_usage as provider_usage

    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("home", provider="codex", data_dir=data_dir)
    folder = _sign_in(data_dir, "work", "w@example.com")
    (folder / "usage.json").write_text(json.dumps({"weekly": 42}))

    work = asyncio.run(provider_usage.refresh_provider_usage("codex", "work", data_dir=data_dir))
    home = asyncio.run(provider_usage.refresh_provider_usage("codex", "home", data_dir=data_dir))

    assert work["source"] == "Stand-in usage" and work["status"] == "available"
    assert {w["id"]: w["used"] for w in work["windows"]} == {"seven_day": 42}
    assert home["status"] == "auth_required" and home["needs_sign_in"] is True
    assert "Stand-in Codex accounts" in home["reason"]


def _reading(data_dir: Path, name: str, provider: str, weekly: float) -> None:
    record_invocation(
        invocation_id=f"call-{name}",
        provider=provider,
        profile=name,
        model="m",
        metadata={
            "input_tokens": 1,
            "output_tokens": 1,
            "quota_windows": [{"id": "seven_day", "label": "w", "unit": "percent", "used": weekly}],
        },
        data_dir=data_dir,
    )


def test_rem_chooses_per_provider_and_still_reads_the_claude_only_setting(
    data_dir: Path, stand_in: Path
) -> None:
    from gofer.ui.rem_provider_accounts import (
        configured_account,
        pick_account,
        pick_run_account,
        reply_account,
    )

    for name in ("busy", "roomy"):
        accounts.create_account(name, provider="codex", data_dir=data_dir)
        _sign_in(data_dir, name, f"{name}@example.com")
    accounts.create_account("claude-work", provider="claude_code", data_dir=data_dir)
    _reading(data_dir, "busy", "codex", 90)
    _reading(data_dir, "roomy", "codex", 10)

    config = {"provider_accounts": {"codex": "auto"}, "claude_account": "claude-work"}
    picked = pick_account(config, data_dir, provider="codex")
    assert picked["name"] == "roomy" and picked["source"] == "auto"
    assert reply_account("codex", config, data_dir) == "roomy"
    # Settings > Usage's "Rem uses next" badge reads the same per-provider setting.
    from gofer.ui.rem_provider_accounts import usage_next_account

    assert (usage_next_account(config, data_dir, provider="codex") or {}).get("name") == "roomy"
    assert usage_next_account(config, data_dir) is None
    assert usage_next_account({"claude_account": "auto"}, data_dir, choose=lambda _d: None)
    # The old claude_account key is read for Claude Code only, and only when
    # provider_accounts has no Claude Code value.
    assert configured_account(config, "claude_code") == "claude-work"
    assert (
        configured_account({**config, "provider_accounts": {"claude_code": ""}}, "claude_code")
        == ""
    )
    assert configured_account({"claude_account": "x"}, "codex") == ""
    assert (
        pick_account({"provider_accounts": {"codex": "busy"}}, data_dir, provider="codex")["source"]
        == "setting"
    )
    assert pick_account({}, data_dir, provider="codex")["source"] == "default"
    assert (
        pick_account({"provider_accounts": {"cursor": "auto"}}, data_dir, provider="cursor")["name"]
        == ""
    )

    # A task's own account wins, and a run stays on the account it started on.
    task = pick_run_account({}, {**config, "task_account": "busy"}, data_dir, provider="codex")
    assert task["name"] == "busy" and task["source"] == "task"
    kept = {"provider_account": {"provider": "codex", "name": "busy", "source": "auto"}}
    assert pick_run_account(kept, config, data_dir, provider="codex")["name"] == "busy"
    # A Claude Code run doesn't take a Codex run's account.
    assert pick_run_account(kept, {}, data_dir, provider="claude_code")["name"] == ""


def test_rem_validates_its_per_provider_setting() -> None:
    from gofer.ui.rem_provider_accounts import validate_provider_accounts

    validate_provider_accounts({"provider_accounts": {"codex": "auto", "claude_code": ""}})
    validate_provider_accounts({"claude_account": "work"})
    for bad in (
        {"provider_accounts": {"nope": "x"}},
        {"provider_accounts": {"codex": 3}},
        {"provider_accounts": ["codex"]},
        {"provider_accounts": {"codex": "x" * 129}},
        {"claude_account": 5},
    ):
        with pytest.raises(ValueError):
            validate_provider_accounts(bad)


def test_worker_config_names_the_tasks_own_account() -> None:
    from gofer.ui.rem_home_models import worker_config

    selected = worker_config({"provider": "codex"}, {"provider_profile": "work"})
    assert selected["task_account"] == "work"
    assert "claude_account" not in selected


def test_a_thread_on_auto_stays_on_its_providers_account(data_dir: Path, stand_in: Path) -> None:
    from gofer.ui.chat_sessions import _digest
    from gofer.ui.rem_provider_accounts import thread_account

    for name in ("work", "home"):
        accounts.create_account(name, provider="codex", data_dir=data_dir)
        _sign_in(data_dir, name, f"{name}@example.com")
    session = data_dir / "chat-provider-sessions" / _digest("t1")
    session.mkdir(parents=True)
    (session / "session.json").write_text(json.dumps({"lastProvider": "codex@home"}))
    choose = lambda _d: {"name": "work"}  # noqa: E731
    assert thread_account("t1", "auto", data_dir, provider="codex", choose=choose) == "home"
    # A Claude Code thread never stays on a Codex account.
    assert thread_account("t1", "auto", data_dir, provider="claude_code", choose=choose) == "work"


def test_turns_get_their_providers_folder_and_every_account_folder_is_hidden(
    data_dir: Path, stand_in: Path
) -> None:
    from gofer.ui import claude_sandbox
    from gofer.ui.chat import ChatProviderError, _account_env, account_deny_rules
    from gofer.utils.data_folders import is_managed_data_folder

    assert account_deny_rules(data_dir) == []
    work = accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("mine", provider="claude_code", data_dir=data_dir)
    # The turn also drops the provider's other sign-in variables from its child.
    assert _account_env("codex", "work", data_dir) == {
        "STAND_IN_HOME": str(data_dir / "codex-accounts" / str(work.account_id)),
        "RATICODE_UNSET_ENV": "OPENAI_API_KEY",
    }
    with pytest.raises(ChatProviderError):
        _account_env("claude_code", "work", data_dir)
    with pytest.raises(ChatProviderError):
        _account_env("codex", "mine", data_dir)

    # A folder of a provider whose adapter isn't loaded is hidden as well.
    (data_dir / "cursor-accounts").mkdir()
    rules = account_deny_rules(data_dir)
    for folder in ("codex-accounts", "claude-accounts", "cursor-accounts"):
        root = str((data_dir / folder).resolve())
        assert f"Read(/{root}/**)" in rules and f"Edit(/{root}/**)" in rules
        assert folder in claude_sandbox.data_secrets(data_dir)
        assert is_managed_data_folder(folder)
    sandbox_rules = claude_sandbox.permission_deny_rules(data_dir)
    assert f"Read(/{data_dir / 'codex-accounts'}/**)" in sandbox_rules
    assert "claude-accounts" in claude_sandbox.data_secrets(data_dir)


def test_generic_account_actions_name_their_provider(data_dir: Path, stand_in: Path) -> None:
    created = accounts.account_action(
        {"action": "create", "provider": "codex", "name": "work"}, data_dir
    )
    assert [(a["name"], a["provider"]) for a in created["accounts"]] == [("work", "codex")]
    # Without a provider, a new account is Claude Code's, as in the first version.
    accounts.account_action({"action": "create", "name": "mine"}, data_dir)
    assert load_provider_profiles(data_dir)["mine"].subscription == "claude_code"
    # The claude-accounts API path lists Claude Code only.
    only_claude = accounts.account_action(
        {"action": "rename", "name": "mine", "newName": "ours"}, data_dir, provider="claude_code"
    )
    assert [a["name"] for a in only_claude["accounts"]] == ["ours"]
    with pytest.raises(accounts.AccountError):
        accounts.account_action(
            {"action": "check", "name": "work"}, data_dir, provider="claude_code"
        )
    with pytest.raises(accounts.AccountError):
        accounts.account_action({"action": "create", "provider": "cursor", "name": "x"}, data_dir)


def test_cli_manages_any_providers_accounts(data_dir: Path, stand_in: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    runner = CliRunner()
    base = ["--data-dir", str(data_dir)]
    added = runner.invoke(app, ["account", "add", "work", "--provider", "codex", *base])
    assert added.exit_code == 0, added.output
    assert "Stand-in Codex account 'work'" in added.output
    assert "is signed in" in added.output
    listed = runner.invoke(
        app, ["account", "list", "--provider", "codex", "--format", "json", *base]
    )
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)["accounts"]
    assert [(r["name"], r["provider"], r["state"]) for r in rows] == [
        ("work", "codex", "signed_in")
    ]
    wrong = runner.invoke(app, ["account", "status", "work", "--provider", "claude_code", *base])
    assert wrong.exit_code == 1
    unknown = runner.invoke(app, ["account", "add", "x", "--provider", "cursor", *base])
    assert unknown.exit_code == 1 and "aren't available" in unknown.output
    removed = runner.invoke(app, ["account", "rm", "work", "--provider", "codex", "-y", *base])
    assert removed.exit_code == 0, removed.output
    assert "work" not in load_provider_profiles(data_dir)


# --- Add account options: a folder to copy setup from, and Import setup ---------------


def _shared_setup(folder: Path) -> Path:
    """A setup folder with Claude Code's allowlisted items and things never copied."""
    (folder / "skills" / "review").mkdir(parents=True)
    (folder / "skills" / "review" / "SKILL.md").write_text("review")
    (folder / "CLAUDE.md").write_text("shared rules")
    (folder / ".credentials.json").write_text('{"secret": "never copied"}')
    (folder / "settings.json").write_text("{}")
    (folder / "projects").mkdir()
    return folder


def _create(data_dir: Path, **body: Any) -> dict[str, Any]:
    return accounts.account_action(
        {"action": "create", "provider": "claude_code", "name": "work", **body}, data_dir
    )


def _folder(data_dir: Path, name: str = "work") -> Path:
    return accounts.account_config_dir(accounts.get_account(name, data_dir), data_dir)


def test_a_new_account_copies_only_the_allowlist_from_a_chosen_folder(
    data_dir: Path, tmp_path: Path
) -> None:
    chosen = _shared_setup(tmp_path / "team-setup")
    overview = _create(data_dir, copySetup=True, setupFrom=str(chosen))
    assert [a["name"] for a in overview["accounts"]] == ["work"]
    folder = _folder(data_dir)
    assert (folder / "skills" / "review" / "SKILL.md").read_text() == "review"
    assert (folder / "CLAUDE.md").read_text() == "shared rules"
    # No sign-in, settings or history comes along.
    for never in (".credentials.json", "settings.json", "projects"):
        assert not (folder / never).exists(), never
    # The providers list names the folder used when none is chosen.
    claude = next(p for p in overview["providers"] if p["id"] == "claude_code")
    assert claude["setup_folder"].endswith(".claude")


def test_a_chosen_folder_is_ignored_without_copy_setup(data_dir: Path, tmp_path: Path) -> None:
    chosen = _shared_setup(tmp_path / "team-setup")
    _create(data_dir, setupFrom=str(chosen))
    assert not (_folder(data_dir) / "CLAUDE.md").exists()


@pytest.mark.parametrize("bad", ["relative/folder", "missing", "   ", "file"])
def test_a_bad_setup_folder_is_refused_and_adds_no_account(
    data_dir: Path, tmp_path: Path, bad: str
) -> None:
    (tmp_path / "file").write_text("not a folder")
    value = {"missing": str(tmp_path / "missing"), "file": str(tmp_path / "file")}.get(bad, bad)
    with pytest.raises(accounts.AccountError):
        _create(data_dir, copySetup=True, setupFrom=value)
    assert "work" not in load_provider_profiles(data_dir)
    assert not (data_dir / "claude-accounts").exists() or not any(
        p for p in (data_dir / "claude-accounts").iterdir() if p.name != ".sign-in"
    )


def test_an_empty_setup_folder_means_the_providers_own_folder(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The form's empty "Copy from" field uses ~/.claude (CLAUDE_CONFIG_DIR if set).
    own = _shared_setup(tmp_path / "own-claude")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(own))
    _create(data_dir, copySetup=True, setupFrom="")
    assert (_folder(data_dir) / "CLAUDE.md").read_text() == "shared rules"


def test_the_accounts_api_answers_a_bad_setup_folder_with_400(
    data_dir: Path, tmp_path: Path
) -> None:
    # The route maps AccountError to a 400 with the message (server.py).
    with pytest.raises(accounts.AccountError, match="full path"):
        _create(data_dir, copySetup=True, setupFrom="relative")
    with pytest.raises(accounts.AccountError, match="no folder"):
        _create(data_dir, copySetup=True, setupFrom=str(tmp_path / "gone"))


def test_import_setup_keeps_what_the_account_has_and_returns_what_it_copied(
    data_dir: Path, tmp_path: Path
) -> None:
    _create(data_dir)
    folder = _folder(data_dir)
    (folder / "CLAUDE.md").write_text("the account's own rules")
    chosen = _shared_setup(tmp_path / "team-setup")
    result = accounts.account_action(
        {"action": "import_setup", "name": "work", "setupFrom": str(chosen)}, data_dir
    )
    assert result["copied"] == ["skills"]
    assert [a["name"] for a in result["accounts"]] == ["work"]
    assert (folder / "CLAUDE.md").read_text() == "the account's own rules"
    assert (folder / "skills" / "review" / "SKILL.md").exists()
    assert not (folder / ".credentials.json").exists()
    # A second import has nothing new to copy.
    again = accounts.account_action(
        {"action": "import_setup", "name": "work", "setupFrom": str(chosen)}, data_dir
    )
    assert again["copied"] == []
    with pytest.raises(accounts.AccountError):
        accounts.account_action(
            {"action": "import_setup", "name": "work", "setupFrom": "relative"}, data_dir
        )
    with pytest.raises(accounts.AccountError):
        accounts.account_action({"action": "import_setup", "name": "nobody"}, data_dir)


def test_import_setup_is_refused_for_a_provider_with_no_shared_setup(
    data_dir: Path, stand_in: Path, tmp_path: Path
) -> None:
    # The stand-in adapter has no setup_allowlist.
    accounts.account_action({"action": "create", "provider": "codex", "name": "spare"}, data_dir)
    chosen = _shared_setup(tmp_path / "team-setup")
    with pytest.raises(accounts.AccountError, match="no shared setup"):
        accounts.account_action(
            {"action": "import_setup", "name": "spare", "setupFrom": str(chosen)}, data_dir
        )
    assert accounts.providers_view()[1]["can_copy_setup"] is False


def test_cli_copies_setup_from_a_folder_and_imports_it_later(
    data_dir: Path, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    runner = CliRunner()
    base = ["--data-dir", str(data_dir)]
    chosen = _shared_setup(tmp_path / "team-setup")
    added = runner.invoke(
        app, ["account", "add", "work", "--no-sign-in", "--copy-setup-from", str(chosen), *base]
    )
    assert added.exit_code == 0, added.output
    folder = _folder(data_dir)
    assert (folder / "CLAUDE.md").read_text() == "shared rules"
    assert not (folder / ".credentials.json").exists()
    (chosen / "commands").mkdir()
    (chosen / "commands" / "ship.md").write_text("ship")
    imported = runner.invoke(
        app,
        ["account", "import-setup", "work", "--from", str(chosen), "--format", "json", *base],
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output) == {"account": "work", "copied": ["commands"]}
    again = runner.invoke(app, ["account", "import-setup", "work", "--from", str(chosen), *base])
    assert again.exit_code == 0 and "Nothing new to copy" in again.output
    missing = runner.invoke(
        app, ["account", "import-setup", "work", "--from", str(tmp_path / "nope"), *base]
    )
    assert missing.exit_code == 1 and "no folder" in missing.output
    gone = str(tmp_path / "x")
    bad_add = runner.invoke(
        app, ["account", "add", "other", "--no-sign-in", "--copy-setup-from", gone, *base]
    )
    assert bad_add.exit_code == 1
    assert "other" not in load_provider_profiles(data_dir)


# --- Copy my setup again: compare the copied setup and re-copy only what changed -------


def test_copy_setup_again_lists_changed_items_and_recopies_only_those(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = _shared_setup(tmp_path / "own-claude")
    (own / "commands").mkdir()
    (own / "commands" / "ship.md").write_text("ship")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(own))
    _create(data_dir, copySetup=True)
    folder = _folder(data_dir)
    (folder / ".credentials.json").write_text("the account's own sign-in")
    (folder / "history.jsonl").write_text("history")
    view = accounts.accounts_overview(data_dir)["accounts"][0]
    assert view["setup_changes"] == []

    # The source changes: a skill is edited and one added; CLAUDE.md and commands stay.
    (own / "skills" / "review" / "SKILL.md").write_text("review, sharper")
    (own / "skills" / "draft").mkdir()
    (own / "skills" / "draft" / "SKILL.md").write_text("draft")
    (own / ".credentials.json").write_text("the source's sign-in")
    # The account has a skill of its own, and an item it was never given (agents).
    (folder / "skills" / "mine").mkdir()
    (folder / "skills" / "mine" / "SKILL.md").write_text("mine")
    (own / "agents").mkdir()
    (own / "agents" / "helper.md").write_text("helper")
    (folder / "CLAUDE.md").write_text("edited in the account")
    view = accounts.accounts_overview(data_dir)["accounts"][0]
    assert view["setup_changes"] == ["CLAUDE.md", "skills"]

    commands_before = (folder / "commands" / "ship.md").stat().st_mtime_ns
    result = accounts.account_action({"action": "copy_setup", "name": "work"}, data_dir)
    assert result["copied"] == ["CLAUDE.md", "skills"]
    assert result["accounts"][0]["setup_changes"] == []
    assert (folder / "CLAUDE.md").read_text() == "shared rules"
    assert (folder / "skills" / "review" / "SKILL.md").read_text() == "review, sharper"
    assert (folder / "skills" / "draft" / "SKILL.md").read_text() == "draft"
    # The account's own files stay; unchanged items aren't rewritten.
    assert (folder / "skills" / "mine" / "SKILL.md").read_text() == "mine"
    assert (folder / "commands" / "ship.md").stat().st_mtime_ns == commands_before
    # Items the account never had are Import setup's job; sign-ins and history untouched.
    assert not (folder / "agents").exists()
    assert (folder / ".credentials.json").read_text() == "the account's own sign-in"
    assert (folder / "history.jsonl").read_text() == "history"
    again = accounts.account_action({"action": "copy_setup", "name": "work"}, data_dir)
    assert again["copied"] == []


def test_copy_setup_again_never_writes_through_a_link_in_the_account(
    data_dir: Path, tmp_path: Path
) -> None:
    source = _shared_setup(tmp_path / "team-setup")
    _create(data_dir, copySetup=True, setupFrom=str(source))
    folder = _folder(data_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("outside")
    (folder / "skills" / "review" / "SKILL.md").unlink()
    (folder / "skills" / "review").rmdir()
    (folder / "skills" / "review").symlink_to(outside)
    (source / "skills" / "review" / "SKILL.md").write_text("changed")
    (source / "CLAUDE.md").write_text("new rules")
    profile = accounts.get_account("work", data_dir)
    assert accounts.setup_changes(profile, data_dir, source) == ["CLAUDE.md"]
    copied = accounts.recopy_setup("work", data_dir=data_dir, setup_source=str(source))
    assert copied == ["CLAUDE.md"]
    assert (outside / "SKILL.md").read_text() == "outside"
    assert (folder / "skills" / "review").is_symlink()


def test_copy_setup_again_is_refused_for_a_provider_with_no_shared_setup(
    data_dir: Path, stand_in: Path
) -> None:
    overview = accounts.account_action(
        {"action": "create", "provider": "codex", "name": "spare"}, data_dir
    )
    assert "setup_changes" not in overview["accounts"][0]
    with pytest.raises(accounts.AccountError, match="no shared setup"):
        accounts.account_action({"action": "copy_setup", "name": "spare"}, data_dir)


def test_cli_copy_setup_checks_then_copies_again(data_dir: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands.provider import app

    runner = CliRunner()
    base = ["--data-dir", str(data_dir)]
    source = _shared_setup(tmp_path / "team-setup")
    _create(data_dir, copySetup=True, setupFrom=str(source))
    folder = _folder(data_dir)
    same = runner.invoke(app, ["account", "copy-setup", "work", "--from", str(source), *base])
    assert same.exit_code == 0 and "already matches" in same.output
    (source / "CLAUDE.md").write_text("new rules")
    check = runner.invoke(
        app,
        ["account", "copy-setup", "work", "--from", str(source), "--check", "--format", "json"]
        + base,
    )
    assert check.exit_code == 0, check.output
    assert json.loads(check.output) == {"account": "work", "changed": ["CLAUDE.md"]}
    assert (folder / "CLAUDE.md").read_text() == "shared rules"
    copied = runner.invoke(
        app, ["account", "copy-setup", "work", "--from", str(source), "--format", "json", *base]
    )
    assert copied.exit_code == 0, copied.output
    assert json.loads(copied.output) == {"account": "work", "copied": ["CLAUDE.md"]}
    assert (folder / "CLAUDE.md").read_text() == "new rules"
    missing = runner.invoke(app, ["account", "copy-setup", "nobody", *base])
    assert missing.exit_code == 1


def test_closing_the_backend_stops_account_sign_ins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sign-in runs in its own session; it must not outlive the backend."""
    from gofer.ui.server import GoferUiServer

    stopped: list[str] = []
    monkeypatch.setattr(accounts.sign_ins, "cancel_all", lambda: stopped.append("all"))
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token="sign-in-close")
    server.server_close()
    assert stopped == ["all"]


def test_a_second_sign_in_for_one_account_stops_the_first(
    data_dir: Path, stand_in: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    stopped: list[Any] = []
    real_stop = accounts.sign_ins._stop

    def stop(sign_in: Any) -> None:
        stopped.append(sign_in.process)
        real_stop(sign_in)

    monkeypatch.setattr(accounts.sign_ins, "_stop", stop)
    # The phone's start runs while the window's start is already past its own cancel.
    real_cancel = accounts.sign_ins.cancel
    monkeypatch.setattr(accounts.sign_ins, "cancel", lambda name: None)
    try:
        accounts.account_action({"action": "sign_in", "name": "work"}, data_dir)
        first = accounts.sign_ins._active["work"].process
        accounts.account_action({"action": "sign_in", "name": "work"}, data_dir)
        assert first in stopped
        assert accounts.sign_ins._active["work"].process is not first
    finally:
        monkeypatch.setattr(accounts.sign_ins, "cancel", real_cancel)
        accounts.sign_ins.cancel_all()


def test_preflight_checks_an_account_steps_sign_in_with_the_adapter(
    data_dir: Path, stand_in: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish.artifacts import compile_rattish_source
    from gofer.subscriptions.base import Subscription

    monkeypatch.setattr(preflight, "account_sign_in_state", REAL_ACCOUNT_SIGN_IN_STATE)
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("home", provider="codex", data_dir=data_dir)
    home = _sign_in(data_dir, "home", "home@example.com")

    class Installed(Subscription):
        def _build_command(self, *args: Any, **kwargs: Any) -> list[str]:
            return ["codex"]

        def is_available(self) -> bool:
            return True

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("preflight never runs the step")

    def diagnostics(profile: str) -> list[tuple[str, str]]:
        source = tmp_path / profile / "workflow.rattish"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "Rattish: 1\n\nWorkflow:\n  name: Account\n\nNode ask:\n  type: agent\n"
            f"  provider: codex\n  profile: {profile}\n  prompt: Say hi.\n",
            encoding="utf-8",
        )
        compiled = compile_rattish_source(
            source.read_text(encoding="utf-8"), source, project_root=source.parent
        )
        result = preflight.run_preflight(
            compiled.ir, data_dir=data_dir, subscriptions={"codex": Installed()}
        )
        return [(item.code, item.severity) for item in result.diagnostics]

    assert diagnostics("work") == [("RATTISH_PREFLIGHT_ACCOUNT_NEEDS_SIGN_IN", "error")]
    assert diagnostics("home") == []
    # The second preflight reused the recent check instead of running the CLI again.
    status_calls = (home / "calls.log").read_text().splitlines()
    assert status_calls == ["login status"]
    diagnostics("home")
    assert (home / "calls.log").read_text().splitlines() == ["login status"]


def test_quick_status_stops_waiting_and_shares_one_slow_check(
    data_dir: Path, stand_in: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = accounts.create_account("work", provider="codex", data_dir=data_dir)
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(profile: ProviderProfile, **options: Any) -> dict[str, Any]:
        calls.append(profile.name)
        started.set()
        release.wait(10)
        return {"state": accounts.NEEDS_SIGN_IN}

    monkeypatch.setattr(accounts, "account_status", slow)

    assert accounts.quick_status(profile, data_dir=data_dir, timeout=0.05) is None
    assert started.wait(5)
    assert accounts.quick_status(profile, data_dir=data_dir, timeout=0.05) is None
    assert calls == ["work"]
    release.set()
    status = accounts.quick_status(profile, data_dir=data_dir, timeout=5)
    assert status is not None and status["state"] == accounts.NEEDS_SIGN_IN


def test_provider_environments_never_carry_raticode_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `provider account login` runs the provider's sign-in with this environment; a
    # RATICODE_TOKEN or Jev key exported in the user's shell used to reach it.
    from gofer.utils.process import CREDENTIAL_ENV_VARS

    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "secret")
    for provider in ("claude_code", "codex"):
        env = accounts.require_adapter(provider).environment(tmp_path)
        assert not set(CREDENTIAL_ENV_VARS) & set(env), provider
