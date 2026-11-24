"""Each organization worker can run as one of its provider's accounts."""

from __future__ import annotations

import time
from typing import Any

import pytest

from gofer.core import provider_accounts as accounts
from gofer.ui.organization_accounts import check_accounts, pick_worker_account
from gofer.ui.organizations import OrganizationManager


def signed_in(_profile: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"state": accounts.SIGNED_IN}


@pytest.fixture
def manager(tmp_path, monkeypatch) -> Any:
    # No provider CLI runs to check a sign-in.
    monkeypatch.setattr(accounts, "account_status", signed_in)
    captured: list[dict[str, Any]] = []

    async def stream(**options: Any) -> Any:
        captured.append(options)
        yield {"type": "final", "message": {"body": "Done."}}

    result = OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)
    result.captured = captured  # type: ignore[attr-defined]
    yield result
    result.close()


def create(manager: Any, tmp_path: Any, **employee: Any) -> Any:
    return manager.call(
        str(tmp_path),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "employees": [
                        {"id": "builder", "name": "Mira", "provider": "codex", **employee}
                    ],
                }
            },
        },
    )


def configure(manager: Any, org: dict[str, Any], **changes: Any) -> Any:
    current = manager.call(
        org["projectRoot"], "user", {"organizationId": org["id"], "action": "read", "params": {}}
    )
    config = current["config"]
    config["employees"] = [{**config["employees"][0], **changes}]
    return manager.call(
        org["projectRoot"],
        "user",
        {
            "organizationId": org["id"],
            "action": "configure",
            "params": {
                "config": config,
                "expectedRevision": current["revision"],
                "reason": "Account",
            },
        },
    )


def employee(manager: Any, org: dict[str, Any]) -> Any:
    current = manager.call(
        org["projectRoot"], "user", {"organizationId": org["id"], "action": "read", "params": {}}
    )
    return current["config"]["employees"][0]


def run_once(manager: Any, org: dict[str, Any]) -> dict[str, Any]:
    root = org["projectRoot"]
    manager.call(
        root,
        "user",
        {
            "organizationId": org["id"],
            "action": "task_create",
            "params": {"title": "Build", "assignee": "builder"},
        },
    )
    manager.call(
        root,
        "user",
        {"organizationId": org["id"], "action": "control", "params": {"state": "running"}},
    )
    manager.dispatch()
    deadline = time.monotonic() + 5
    while manager._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager._active
    current = manager.call(
        root, "user", {"organizationId": org["id"], "action": "read", "params": {}}
    )
    return next(iter(current["runtime"]["attempts"].values()))


def test_a_worker_saves_an_account_id_of_its_provider(manager, tmp_path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    claude = accounts.create_account("personal", provider="claude_code", data_dir=manager.data_dir)
    org = create(manager, tmp_path)
    assert employee(manager, org).get("account") is None

    configure(manager, org, account=work.account_id)
    assert employee(manager, org)["account"] == work.account_id
    # Another provider's account, a name or an unknown ID is refused.
    with pytest.raises(ValueError, match="Codex accounts"):
        configure(manager, org, account=claude.account_id)
    with pytest.raises(ValueError):
        configure(manager, org, account="work")
    with pytest.raises(ValueError, match="Settings > Providers"):
        configure(manager, org, account="0" * 16)
    # Renaming the account keeps the choice: the ID is stored.
    accounts.rename_account("work", "office", data_dir=manager.data_dir)
    assert employee(manager, org)["account"] == work.account_id
    configure(manager, org, account=None)
    assert employee(manager, org)["account"] is None


def test_switching_provider_clears_the_old_providers_account(manager, tmp_path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    org = create(manager, tmp_path, account=work.account_id)
    configure(manager, org, provider="claude_code", permissionMode="dontAsk")
    saved = employee(manager, org)
    assert saved["provider"] == "claude_code" and saved["account"] is None


def test_an_unchanged_choice_survives_its_account_being_removed(manager, tmp_path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    org = create(manager, tmp_path, account=work.account_id)
    accounts.remove_account("work", data_dir=manager.data_dir, run=lambda *a, **k: None)
    # Editing something else still saves; the turn falls back instead.
    configure(manager, org, title="Builder")
    assert employee(manager, org)["account"] == work.account_id


def test_a_turn_runs_as_the_workers_account_and_counts_against_it(manager, tmp_path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    org = create(manager, tmp_path, account=work.account_id)
    attempt = run_once(manager, org)
    options = manager.captured[0]
    assert options["provider_profile"] == "work"
    # The account lives in Raticode's data folder, not the worker's runtime folder.
    assert options["account_data_dir"] == manager.data_dir
    assert options["data_dir"] != manager.data_dir
    assert attempt["account"] == {
        "accountId": work.account_id,
        "name": "work",
        "source": "worker",
        "why": "Codex account 'work', chosen for this worker.",
    }
    from gofer.core.usage_ledger import usage_db

    with usage_db(manager.data_dir) as db:
        row = db.execute("SELECT provider, profile FROM usage_invocations").fetchone()
    assert (row["provider"], row["profile"]) == ("codex", "work")


def test_a_worker_without_an_account_uses_the_default_sign_in(manager, tmp_path) -> None:
    org = create(manager, tmp_path)
    attempt = run_once(manager, org)
    assert "provider_profile" not in manager.captured[0]
    assert "account_data_dir" not in manager.captured[0]
    assert attempt["account"]["source"] == "default" and attempt["account"]["name"] == ""
    from gofer.core.usage_ledger import usage_db

    with usage_db(manager.data_dir) as db:
        assert db.execute("SELECT profile FROM usage_invocations").fetchone()["profile"] is None


def test_a_removed_account_falls_back_and_says_why(manager, tmp_path) -> None:
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    org = create(manager, tmp_path, account=work.account_id)
    accounts.remove_account("work", data_dir=manager.data_dir, run=lambda *a, **k: None)
    attempt = run_once(manager, org)
    assert "provider_profile" not in manager.captured[0]
    assert attempt["account"]["source"] == "default"
    assert attempt["account"]["accountId"] == work.account_id
    assert "was removed; using the default sign-in" in attempt["account"]["why"]


def test_a_signed_out_account_falls_back_and_says_why(tmp_path) -> None:
    data_dir = tmp_path / "data"
    work = accounts.create_account("work", provider="codex", data_dir=data_dir)
    worker = {"id": "builder", "provider": "codex", "account": work.account_id}

    def signed_out(_profile: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"state": accounts.NEEDS_SIGN_IN}

    def unknown(_profile: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"state": accounts.UNAVAILABLE}

    out = pick_worker_account(worker, data_dir, status=signed_out)
    assert out["source"] == "default" and out["name"] == ""
    assert "'work' is signed out; using the default sign-in" in out["why"]
    # A sign-in nobody could check isn't a sign-out: the turn tries the account.
    assert pick_worker_account(worker, data_dir, status=unknown)["name"] == "work"


def test_check_accounts_leaves_configurations_without_accounts_alone(tmp_path) -> None:
    config = {"employees": [{"id": "a", "provider": "codex"}]}
    assert check_accounts({}, config, tmp_path) is config


def test_providers_lists_each_providers_accounts_by_id(manager, tmp_path, monkeypatch) -> None:
    import gofer.core.provider_capabilities as capabilities

    monkeypatch.setattr(
        capabilities, "provider_capabilities_payload", lambda refresh=False: {"providers": []}
    )
    work = accounts.create_account("work", provider="codex", data_dir=manager.data_dir)
    result = manager.call("", "user", {"action": "providers", "params": {}})
    assert {"provider": "codex", "id": work.account_id, "name": "work"} in result["accounts"]
    assert result["providers"] == []
