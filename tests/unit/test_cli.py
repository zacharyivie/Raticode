from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.provider_profiles import (
    ProviderProfile,
    load_provider_profiles,
    save_provider_profiles,
)

runner = CliRunner()


def test_provider_profile_cli_create_list_and_remove(tmp_path: Path) -> None:
    create_result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "create",
            "fast",
            "--subscription",
            "codex",
            "--model",
            "gpt-5-mini",
            "--data-dir",
            str(tmp_path),
        ],
    )
    list_result = runner.invoke(
        app,
        ["provider", "profile", "list", "--data-dir", str(tmp_path)],
    )
    rm_result = runner.invoke(
        app,
        ["provider", "profile", "rm", "fast", "--yes", "--data-dir", str(tmp_path)],
    )

    assert create_result.exit_code == 0, create_result.output
    assert list_result.exit_code == 0, list_result.output
    assert "fast" in list_result.output
    assert "gpt-5-mini" in list_result.output
    assert rm_result.exit_code == 0, rm_result.output


def test_provider_profile_cli_list_no_profiles(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["provider", "profile", "list", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "No provider profiles found." in result.output


def test_provider_profile_cli_create_rejects_invalid_inputs(tmp_path: Path) -> None:
    duplicate = ProviderProfile(name="fast", subscription="codex")
    save_provider_profiles({"fast": duplicate}, tmp_path)

    cases = [
        (
            ["provider", "profile", "create", "fast", "--data-dir", str(tmp_path)],
            "already exists",
        ),
        (
            [
                "provider",
                "profile",
                "create",
                "bad-sub",
                "--subscription",
                "missing",
                "--data-dir",
                str(tmp_path),
            ],
            "Invalid subscription",
        ),
        (
            [
                "provider",
                "profile",
                "create",
                "bad-env",
                "--env",
                "NO_EQUALS",
                "--data-dir",
                str(tmp_path),
            ],
            "Invalid --env value",
        ),
        (
            [
                "provider",
                "profile",
                "create",
                "bad-secret",
                "--secret-ref",
                "NO_EQUALS",
                "--data-dir",
                str(tmp_path),
            ],
            "Invalid --secret-ref value",
        ),
        (
            [
                "provider",
                "profile",
                "create",
                "bad-setting",
                "--subscription",
                "openai_api",
                "--tool",
                "Read",
                "--data-dir",
                str(tmp_path),
            ],
            "Direct API provider profiles do not support tools",
        ),
    ]

    for args, snippet in cases:
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert snippet in result.output

    assert set(load_provider_profiles(tmp_path)) == {"fast"}


def test_provider_profile_cli_create_and_edit_secret_refs(tmp_path: Path) -> None:
    create_result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "create",
            "secure",
            "--subscription",
            "claude_code",
            "--env",
            "PLAIN=value",
            "--secret-ref",
            "ANTHROPIC_API_KEY=ANTHROPIC_TOKEN",
            "--data-dir",
            str(tmp_path),
        ],
    )
    edit_result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "edit",
            "secure",
            "--secret-ref",
            "ANTHROPIC_API_KEY=NEW_TOKEN",
            "--data-dir",
            str(tmp_path),
        ],
    )

    profiles = load_provider_profiles(tmp_path)

    assert create_result.exit_code == 0, create_result.output
    assert edit_result.exit_code == 0, edit_result.output
    assert profiles["secure"].env == {"PLAIN": "value"}
    assert profiles["secure"].secret_refs == {"ANTHROPIC_API_KEY": "NEW_TOKEN"}


def test_provider_profile_cli_edit_updates_and_replaces_collections(tmp_path: Path) -> None:
    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work",
                subscription="claude_code",
                model="old",
                timeout=10,
                extra_args=["--old"],
                tools=["Read", "Write"],
                mcp_servers=["legacy"],
                env={"KEEP": "yes", "ANTHROPIC_API_KEY": "plaintext"},
                secret_refs={"OLD_KEY": "OLD_SECRET"},
            )
        },
        tmp_path,
    )

    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "edit",
            "work",
            "--model",
            "claude-sonnet",
            "--timeout",
            "30",
            "--extra-arg",
            "--verbose",
            "--tool",
            "Read",
            "--mcp-server",
            "main",
            "--env",
            "NEXT=1",
            "--secret-ref",
            "ANTHROPIC_API_KEY=ANTHROPIC_TOKEN",
            "--data-dir",
            str(tmp_path),
        ],
    )

    profile = load_provider_profiles(tmp_path)["work"]
    persisted = json.loads((tmp_path / "provider-profiles.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0, result.output
    assert profile.model == "claude-sonnet"
    assert profile.timeout == 30
    assert profile.extra_args == ["--verbose"]
    assert profile.tools == ["Read"]
    assert profile.mcp_servers == ["main"]
    assert profile.env == {"NEXT": "1"}
    assert profile.secret_refs == {"ANTHROPIC_API_KEY": "ANTHROPIC_TOKEN"}
    assert "plaintext" not in json.dumps(persisted)
    assert "ANTHROPIC_TOKEN" in json.dumps(persisted)


def test_provider_profile_cli_edit_clears_env_and_secret_refs(tmp_path: Path) -> None:
    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work",
                subscription="codex",
                env={"TRACE": "1"},
                secret_refs={"CODEX_API_KEY": "CODEX_TOKEN"},
            )
        },
        tmp_path,
    )

    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "edit",
            "work",
            "--env",
            "",
            "--secret-ref",
            "",
            "--data-dir",
            str(tmp_path),
        ],
    )

    profile = load_provider_profiles(tmp_path)["work"]

    assert result.exit_code == 0, result.output
    assert profile.env == {}
    assert profile.secret_refs == {}


def test_provider_profile_cli_edit_rejects_missing_and_malformed_options(
    tmp_path: Path,
) -> None:
    save_provider_profiles({"work": ProviderProfile(name="work", subscription="codex")}, tmp_path)

    cases = [
        (
            ["provider", "profile", "edit", "missing", "--data-dir", str(tmp_path)],
            "not found",
        ),
        (
            [
                "provider",
                "profile",
                "edit",
                "work",
                "--env",
                "NO_EQUALS",
                "--data-dir",
                str(tmp_path),
            ],
            "Invalid --env value",
        ),
        (
            [
                "provider",
                "profile",
                "edit",
                "work",
                "--secret-ref",
                "NO_EQUALS",
                "--data-dir",
                str(tmp_path),
            ],
            "Invalid --secret-ref value",
        ),
    ]

    for args, snippet in cases:
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert snippet in result.output


def test_provider_profile_cli_rm_missing_and_confirmation_abort(tmp_path: Path) -> None:
    save_provider_profiles({"work": ProviderProfile(name="work", subscription="codex")}, tmp_path)

    missing = runner.invoke(
        app,
        ["provider", "profile", "rm", "missing", "--data-dir", str(tmp_path)],
    )
    aborted = runner.invoke(
        app,
        ["provider", "profile", "rm", "work", "--data-dir", str(tmp_path)],
        input="n\n",
    )

    assert missing.exit_code == 1, missing.output
    assert "not found" in missing.output
    assert aborted.exit_code == 1, aborted.output
    assert "Aborted" in aborted.output
    assert set(load_provider_profiles(tmp_path)) == {"work"}


def test_provider_profile_cli_rejects_sensitive_plaintext_env(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "create",
            "bad",
            "--subscription",
            "codex",
            "--env",
            "AWS_ACCESS_KEY_ID=plaintext",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "must use secret_refs" in result.output
    assert load_provider_profiles(tmp_path) == {}


def test_provider_profile_cli_allows_explicit_unsafe_env(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "create",
            "unsafe",
            "--subscription",
            "codex",
            "--unsafe-env",
            "CODEX_API_KEY=plaintext",
            "--data-dir",
            str(tmp_path),
        ],
    )

    profiles = load_provider_profiles(tmp_path)

    assert result.exit_code == 0, result.output
    assert profiles["unsafe"].env == {"CODEX_API_KEY": "plaintext"}


def test_provider_profile_cli_secret_ref_replaces_legacy_plaintext_env(
    tmp_path: Path,
) -> None:
    save_provider_profiles(
        {
            "legacy": ProviderProfile(
                name="legacy",
                subscription="codex",
                env={"CODEX_API_KEY": "plaintext", "GOFER_TRACE": "1"},
            )
        },
        tmp_path,
    )

    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "edit",
            "legacy",
            "--secret-ref",
            "CODEX_API_KEY=CODEX_TOKEN",
            "--data-dir",
            str(tmp_path),
        ],
    )
    profiles = load_provider_profiles(tmp_path)

    assert result.exit_code == 0, result.output
    assert profiles["legacy"].env == {"GOFER_TRACE": "1"}
    assert profiles["legacy"].secret_refs == {"CODEX_API_KEY": "CODEX_TOKEN"}


def test_workflow_approval_cli_lists_and_decides(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
            approvers=["alice"],
        )
    )

    result = runner.invoke(app, ["workflow", "approvals", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "approval-flow" in result.output
    assert "Approve deploy?" in result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            "run.log",
            "approve",
            "--workflow",
            "approval-flow",
            "--by",
            "alice",
            "--notes",
            "ok",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    decided = store.get("approval-flow", "run.log", "approve")
    assert decided is not None
    assert decided.decision is not None
    assert decided.decision.decision == "approved"
    assert decided.decision.decided_by == "alice"
    assert decided.decision.notes == "ok"

    result = runner.invoke(
        app,
        [
            "workflow",
            "approvals",
            "--all",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "approved" in result.output
    assert "alice" in result.output


def test_workflow_approval_cli_rejects_already_decided_request(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
        )
    )
    store.decide(
        "approval-flow",
        "run.log",
        "approve",
        "timeout",
        decided_by="gofer",
        notes="Timed out after 30 seconds",
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            "run.log",
            "approve",
            "--workflow",
            "approval-flow",
            "--by",
            "alice",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Approval already decided as timeout" in result.output
    decided = store.get("approval-flow", "run.log", "approve")
    assert decided is not None
    assert decided.decision is not None
    assert decided.decision.decision == "timeout"
    assert decided.decision.decided_by == "gofer"


@pytest.mark.parametrize("workflow_args", [["--workflow", "approval-flow"], []])
def test_workflow_reject_cli_explains_a_cancelled_gate(
    tmp_path: Path, workflow_args: list[str]
) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
        )
    )
    store.cancel("approval-flow", "run.log", "approve", "The workflow run ended (failed).")

    result = runner.invoke(
        app,
        ["workflow", "reject", "run.log", "approve", *workflow_args, "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Approval was cancelled: The workflow run ended (failed)." in result.output
    assert "pass --workflow" not in result.output


def test_workflow_approval_cli_enforces_configured_approvers(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
            approvers=["alice"],
        )
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            "run.log",
            "approve",
            "--workflow",
            "approval-flow",
            "--by",
            "bob",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "not allowed" in result.output
    request = store.get("approval-flow", "run.log", "approve")
    assert request is not None
    assert request.decision is None


def test_workflow_reject_cli_records_decision(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
            approvers=["alice", "bob"],
        )
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "reject",
            "run.log",
            "approve",
            "--workflow",
            "approval-flow",
            "--by",
            "bob",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    decided = store.get("approval-flow", "run.log", "approve")
    assert decided is not None
    assert decided.decision is not None
    assert decided.decision.decision == "rejected"
    assert decided.decision.decided_by == "bob"


def test_plural_commands_are_invalid() -> None:
    for command in ("workflows", "agents", "prompts"):
        result = runner.invoke(app, [command])
        assert result.exit_code != 0
        assert "No such command" in result.output


def test_prompt_command_is_invalid() -> None:
    result = runner.invoke(app, ["prompt"])
    assert result.exit_code != 0
    assert "No such command" in result.output
