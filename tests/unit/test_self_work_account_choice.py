"""Self-work's own account choice: Settings > Rem > Self-work, the phone and the CLI.

A run self-work started reads the task's own account first, then self-work's choice for
its provider, then Rem's setting (Settings > Rem > Accounts). Chat, threads and tasks the
user created keep reading Rem's setting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts as accounts
from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.rem_provider_accounts import (
    pick_run_account,
    self_work_choices,
    self_work_config,
    validate_self_work_accounts,
)
from tests.unit.test_cli_rem_api import app, cli, runner
from tests.unit.test_cli_rem_api import backend as backend  # noqa: F401
from tests.unit.test_rem_kanban import board as board  # noqa: F401
from tests.unit.test_rem_kanban import set_options


def _accounts(data_dir: Path, *names: str) -> None:
    for name in names:
        accounts.create_account(name, provider="claude_code", data_dir=data_dir)


def test_the_choice_is_validated_like_rems_account_setting() -> None:
    assert validate_self_work_accounts(
        {"claude_code": "work", "codex": "auto", "grok": "@default", "antigravity": ""}
    ) == {"claude_code": "work", "codex": "auto", "grok": "@default"}
    for bad in (
        ["claude_code"],
        {"nope": "work"},
        {"claude_code": 3},
        {"claude_code": "x" * 129},
        {"claude_code": "../work"},
        {"claude_code": "@home"},
    ):
        with pytest.raises(ValueError):
            validate_self_work_accounts(bad)
    # A malformed saved value reads as no choice at all, never as an error.
    assert self_work_choices({"provider_accounts": {"nope": "x"}}) == {}


def test_a_self_started_run_reads_the_task_then_self_work_then_rem(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _accounts(data_dir, "work", "spare", "mine")
    rem = {"provider_accounts": {"claude_code": "work"}}

    def picked(config: dict[str, Any], choices: dict[str, str]) -> dict[str, Any]:
        return pick_run_account({}, self_work_config(config, choices), data_dir)

    # No self-work choice: Rem's setting.
    assert picked(rem, {})["name"] == "work"
    # Self-work's named account replaces Rem's setting for that provider only.
    assert picked(rem, {"claude_code": "spare"})["name"] == "spare"
    assert picked(rem, {"codex": "auto"})["name"] == "work"
    # Default sign-in, even though Rem's setting names an account.
    chosen = picked(rem, {"claude_code": "@default"})
    assert chosen["name"] == "" and chosen["source"] == "default"
    # The task's own account still wins over self-work's choice.
    task = picked({**rem, "task_account": "mine"}, {"claude_code": "spare"})
    assert task["name"] == "mine" and task["source"] == "task"
    # The old Claude-only setting still counts as Rem's setting for providers without a choice.
    legacy = self_work_config({"claude_account": "work"}, {"codex": "auto"})
    assert legacy["provider_accounts"] == {"claude_code": "work", "codex": "auto"}
    # Choose by usage under self-work picks from the accounts like Rem's "auto" does.
    auto = pick_run_account(
        {},
        self_work_config(rem, {"claude_code": "auto"}),
        data_dir,
        choose=lambda _d: {"name": "spare", "why": "Most room left."},
    )
    assert auto["name"] == "spare" and auto["source"] == "auto"


def test_settings_change_by_provider_and_only_self_started_runs_use_them(board) -> None:  # noqa: F811
    home, _, _ = board
    _accounts(home.data_dir, "work", "spare")
    home.configure(
        {
            "provider": "claude_code",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 8},
            "projects": [],
            "provider_accounts": {"claude_code": "work"},
        }
    )
    view = home.self_work.view()
    assert view["settings"]["provider_accounts"] == {}
    (row,) = [r for r in view["accounts"] if r["provider"] == "claude_code"]
    assert row["choice"] == "" and row["rem_setting"] == "work"
    assert row["accounts"] == ["spare", "work"]

    set_options(home, provider_accounts={"claude_code": "spare"})
    set_options(home, provider_accounts={"codex": "auto"})
    view = home.self_work.view()
    assert view["settings"]["provider_accounts"] == {"claude_code": "spare", "codex": "auto"}
    # "" puts one provider back on Rem's setting and leaves the others.
    set_options(home, provider_accounts={"codex": ""})
    assert home.self_work.view()["settings"]["provider_accounts"] == {"claude_code": "spare"}
    revision = home.self_work.view()["settings"]["revision"]
    for bad in ({"nope": "x"}, {"claude_code": "../x"}, "spare"):
        with pytest.raises(KanbanRefused):
            home.self_work.update_settings(
                {"provider_accounts": bad, "expected_revision": revision}
            )

    updates: list[dict[str, Any]] = []

    def update(identity: str, **patch: Any) -> dict[str, Any]:
        updates.append(patch)
        return {}

    home._update = update  # type: ignore[method-assign,assignment]
    with home.store.read() as db:
        config_now = home.store.setting(db, "config")
    self_started = {"id": "r1", "self_work": {"kind": "ticket"}}
    assert home._run_account(self_started, config_now, "claude_code") == "spare"
    assert updates[-1]["provider_account"]["source"] == "setting"
    # A ticket the user moved to In progress is the user's work: Rem's setting.
    users = {"id": "r2", "self_work": {"kind": "ticket", "origin": "user"}}
    assert home._run_account(users, config_now, "claude_code") == "work"
    # A task the user created: Rem's setting.
    assert home._run_account({"id": "r3"}, config_now, "claude_code") == "work"
    # The task's own account wins over self-work's choice.
    own = {**config_now, "task_account": "work"}
    assert home._run_account({"id": "r4", "self_work": {"kind": "ticket"}}, own, "claude_code") == (
        "work"
    )


def test_rem_settings_apply_replaces_them_and_the_windows_resend_keeps_them(board) -> None:  # noqa: F811
    home, _, project = board
    base = {
        "provider": "codex",
        "model": "cli-default",
        "command_slots": {"maxWorkers": 8},
        "projects": [{"root": str(project), "name": "project"}],
    }
    before = home.self_work.view()["settings"]["revision"]
    home.configure({**base, "self_work_accounts": {"claude_code": "work", "codex": ""}})
    settings = home.self_work.view()["settings"]
    assert settings["provider_accounts"] == {"claude_code": "work"}
    assert settings["revision"] == before + 1
    with home.store.read() as db:
        assert "self_work_accounts" not in home.store.setting(db, "config")
    # The window sends Rem's settings without them, which leaves them alone.
    home.configure(base)
    assert home.self_work.view()["settings"]["provider_accounts"] == {"claude_code": "work"}
    # The same file again changes nothing, so devices editing them see no conflict.
    home.configure({**base, "self_work_accounts": {"claude_code": "work"}})
    assert home.self_work.view()["settings"]["revision"] == before + 1
    with pytest.raises(ValueError):
        home.configure({**base, "self_work_accounts": {"claude_code": "a/b"}})
    with pytest.raises(ValueError):
        home.configure({**base, "self_work_accounts": {"nope": "work"}})


def test_cli_sets_shows_exports_and_applies_the_choice(backend, tmp_path) -> None:  # noqa: F811
    service, _, _ = backend
    _accounts(service.data_dir, "work")
    revision = cli("selfwork", "status")["data"]["settings"]["revision"]
    changed = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(revision),
        "--account",
        "claude_code=work",
        "--account",
        "codex=auto",
    )["data"]
    assert changed["settings"]["provider_accounts"] == {"claude_code": "work", "codex": "auto"}
    text = runner.invoke(app, ["selfwork", "status", "--output", "text"]).stdout
    assert "Self-work accounts: Claude Code: work" in " ".join(text.split())
    bad = cli("selfwork", "set", "--expected-revision", "0", "--account", "work", code=2)
    assert "PROVIDER=ACCOUNT" in bad["error"]["message"]

    # `selfwork apply` replaces the choices: a provider the file leaves out follows Rem again.
    exported = json.loads(runner.invoke(app, ["selfwork", "export"]).stdout)
    assert exported["provider_accounts"] == {"claude_code": "work", "codex": "auto"}
    exported["provider_accounts"] = {"claude_code": "@default"}
    target = tmp_path / "self-work.json"
    target.write_text(json.dumps(exported))
    applied = cli("selfwork", "apply", "--file", str(target))["data"]
    assert applied["settings"]["provider_accounts"] == {"claude_code": "@default"}

    # `rem settings` carries them as self_work_accounts.
    shown = cli("rem", "settings", "show")["data"]["config"]
    assert shown["self_work_accounts"] == {"claude_code": "@default"}
    rem_file = tmp_path / "rem.json"
    assert runner.invoke(app, ["rem", "settings", "export", "--file", str(rem_file)]).exit_code == 0
    config = json.loads(rem_file.read_text())
    assert config["self_work_accounts"] == {"claude_code": "@default"}
    config["self_work_accounts"] = {"codex": "work"}
    rem_file.write_text(json.dumps(config))
    cli("rem", "settings", "apply", "--file", str(rem_file))
    status = cli("selfwork", "status")["data"]["settings"]
    assert status["provider_accounts"] == {"codex": "work"}
    config["self_work_accounts"] = {"codex": "no such/folder"}
    rem_file.write_text(json.dumps(config))
    assert cli("rem", "settings", "apply", "--file", str(rem_file), code=2)
