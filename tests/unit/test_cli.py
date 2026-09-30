from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.graph import GraphNode
from gofer.core.operations import (
    AgentOperation,
    CommonLlmTaskOperation,
    DirectoryFanSource,
    LoopOperation,
    NotificationOperation,
    OperationType,
)
from gofer.core.provider_profiles import (
    ProviderProfile,
    load_provider_profiles,
    save_provider_profiles,
)
from gofer.core.resources import ResourceLimits
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.ui.chat import workflow_chat_prompt_path
from gofer.utils.run_state import workflow_stop_path
from tests.conftest import FakeSubscription

runner = CliRunner()

_SIMPLE_TOML = """
[workflow]
id = "simple"
name = "Simple"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
"""


def test_workflow_validate_valid(tmp_path: Path) -> None:
    f = tmp_path / "wf.toml"
    f.write_text(_SIMPLE_TOML)
    result = runner.invoke(app, ["workflow", "validate", str(f)])
    assert result.exit_code == 0
    assert "valid" in result.output


def test_workflow_validate_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "validate", str(tmp_path / "missing.toml")])
    assert result.exit_code != 0


def test_workflow_run_verbose_surfaces_local_vector_index_stats(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha gofer workflow")
    index = tmp_path / "index.json"
    workflow = tmp_path / "vector.toml"
    workflow.write_text(
        f"""
[workflow]
id = "vector"
name = "Vector"

[[nodes]]
id = "index"
type = "local_vectorize"
source_path = "{docs}"
index_path = "{index}"
glob = "*.txt"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["workflow", "run", str(workflow), "--verbose"])

    assert result.exit_code == 0
    assert "Index stats:" in result.output
    assert "last update" in result.output
    assert "strategy hash_token_v1" in result.output
    assert "search cosine_v1" in result.output


def test_workflow_create(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "My Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0
    created = list(tmp_path.glob("*.toml"))
    assert len(created) == 1


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


def test_runner_cli_register_queue_status_cancel(tmp_path: Path) -> None:
    workflow = tmp_path / "remote.toml"
    workflow.write_text(
        """
[workflow]
id = "remote"
name = "Remote"

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""",
        encoding="utf-8",
    )

    register_result = runner.invoke(
        app,
        [
            "runner",
            "register",
            "--id",
            "runner-1",
            "--name",
            "CI",
            "--label",
            "ci",
            "--provider-cli",
            "codex",
            "--data-dir",
            str(tmp_path),
        ],
    )
    queue_result = runner.invoke(
        app,
        [
            "runner",
            "queue",
            str(workflow),
            "--label",
            "ci",
            "--priority",
            "5",
            "--data-dir",
            str(tmp_path),
        ],
    )
    status_result = runner.invoke(
        app,
        ["runner", "status", "--data-dir", str(tmp_path)],
    )
    run_id = queue_result.output.split()[1]
    cancel_result = runner.invoke(
        app,
        ["runner", "cancel", run_id, "--data-dir", str(tmp_path)],
    )

    assert register_result.exit_code == 0, register_result.output
    assert queue_result.exit_code == 0, queue_result.output
    assert status_result.exit_code == 0, status_result.output
    assert "remote" in status_result.output
    assert "queued" in status_result.output
    assert cancel_result.exit_code == 0, cancel_result.output
    assert "canceled" in cancel_result.output


def test_runner_cli_start_once_executes_queued_workflow(tmp_path: Path) -> None:
    workflow = tmp_path / "remote.toml"
    workflow.write_text(
        """
[workflow]
id = "remote"
name = "Remote"

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""",
        encoding="utf-8",
    )

    queue_result = runner.invoke(
        app,
        ["runner", "queue", str(workflow), "--data-dir", str(tmp_path)],
    )
    start_result = runner.invoke(
        app,
        [
            "runner",
            "start",
            "--id",
            "runner-1",
            "--once",
            "--data-dir",
            str(tmp_path),
        ],
    )
    status_result = runner.invoke(
        app,
        ["runner", "status", "--data-dir", str(tmp_path)],
    )

    assert queue_result.exit_code == 0, queue_result.output
    assert start_result.exit_code == 0, start_result.output
    assert "completed" in start_result.output
    assert status_result.exit_code == 0, status_result.output
    assert "completed" in status_result.output


def test_runner_cli_preserves_parameters_through_execution(tmp_path: Path) -> None:
    from gofer.core.runner import RunnerQueueStore

    workflow = tmp_path / "parameterized.toml"
    workflow.write_text("""
[workflow]
id = "parameterized"
name = "Parameterized"

[workflow.inputs.customer]
type = "string"
required = true

[[nodes]]
id = "start"
type = "pass"
message = "{{inputs.customer}}"
""")
    queue_result = runner.invoke(
        app,
        [
            "runner",
            "queue",
            str(workflow),
            "--param",
            "customer=release-test",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert queue_result.exit_code == 0, queue_result.output

    start_result = runner.invoke(
        app,
        [
            "runner",
            "start",
            "--id",
            "worker",
            "--once",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert start_result.exit_code == 0, start_result.output
    queued = RunnerQueueStore(tmp_path).list_runs()[0]
    assert queued.status == "completed", queued.message
    assert "release-test" in Path(queued.run_log_path).read_text()


def test_runner_cli_rejects_invalid_parameters_before_enqueue(tmp_path: Path) -> None:
    from gofer.core.runner import RunnerQueueStore

    workflow = tmp_path / "parameterized.toml"
    workflow.write_text("""
[workflow]
id = "parameterized"
name = "Parameterized"

[workflow.inputs.count]
type = "number"
required = true

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""")
    result = runner.invoke(
        app,
        [
            "runner",
            "queue",
            str(workflow),
            "--param",
            "count=invalid",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "count" in result.output
    assert RunnerQueueStore(tmp_path).list_runs() == []


def test_workflow_validate_uses_data_dir_provider_profiles(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workflow_dir = tmp_path / "exported"
    workflow_dir.mkdir()
    save_provider_profiles(
        {
            "fast": ProviderProfile(
                name="fast",
                subscription="codex",
                model="gpt-5-mini",
            )
        },
        data_dir,
    )
    workflow = workflow_dir / "portable.toml"
    workflow.write_text(
        f"""
[workflow]
id = "portable"
name = "Portable"

[agents.reviewer]
subscription = "codex"
profile = "fast"
working_dir = "{workflow_dir}"

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "{workflow_dir}"
prompt = "Review"
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["workflow", "validate", str(workflow), "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output


def test_workflow_add_file_and_folder_nodes(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Path Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    commands = [
        [
            "workflow",
            "add-node",
            "path-flow",
            "--id",
            "source-file",
            "--type",
            "file",
            "--path",
            "data/input.txt",
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-node",
            "path-flow",
            "--id",
            "source-folder",
            "--type",
            "folder",
            "--path",
            "data",
            "--data-dir",
            str(tmp_path),
        ],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "path-flow.toml")
    assert wf.graph._nodes["source-file"].operation.type == "file"
    assert wf.graph._nodes["source-folder"].operation.type == "folder"


def test_workflow_add_node_persists_common_inputs(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Input Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "input-flow",
            "--id",
            "print",
            "--type",
            "bash_command",
            "--command",
            "cat",
            "--input",
            "stdin=previous.text",
            "--input",
            "env.FILE_PATH=loop.current.file_path",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "input-flow.toml")
    assert wf.graph._nodes["print"].inputs == {
        "stdin": "previous.text",
        "env.FILE_PATH": "loop.current.file_path",
    }


def test_workflow_commands_author_structured_output_workflow(tmp_path: Path) -> None:
    prompt_path = tmp_path / "review.md"
    prompt_path.write_text("Review the result", encoding="utf-8")
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Structured Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    commands = [
        [
            "workflow",
            "add-agent",
            "structured-flow",
            "--id",
            "reviewer",
            "--subscription",
            "codex",
            "--working-dir",
            str(tmp_path),
            "--prompt-path",
            str(prompt_path),
        ],
        [
            "workflow",
            "add-node",
            "structured-flow",
            "--id",
            "review",
            "--type",
            "agent",
            "--agent-id",
            "reviewer",
            "--working-dir",
            str(tmp_path),
            "--prompt-path",
            str(prompt_path),
            "--output-schema",
            '{"type":"object","properties":{"score":{"type":"number"}},"required":["score"]}',
            "--repair-attempts",
            "2",
        ],
        [
            "workflow",
            "add-node",
            "structured-flow",
            "--id",
            "publish",
            "--type",
            "bash_command",
            "--command",
            "echo published",
        ],
        [
            "workflow",
            "add-edge",
            "structured-flow",
            "--from",
            "review",
            "--to",
            "publish",
            "--condition",
            "output_field",
            "--field",
            "score",
            "--operator",
            "greater_than",
            "--value",
            "7",
        ],
    ]
    for command in commands:
        result = runner.invoke(app, [*command, "--data-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output

    workflow = AgenticWorkflow.from_file(tmp_path / "structured-flow.toml")
    workflow.validate(tmp_path / "structured-flow.toml")
    operation = workflow.graph._nodes["review"].operation
    assert isinstance(operation, AgentOperation)
    assert operation.output_schema == {
        "type": "object",
        "properties": {"score": {"type": "number"}},
        "required": ["score"],
    }
    assert operation.repair_attempts == 2
    assert workflow.graph._edges[("review", "publish")].value == 7


def test_workflow_add_notification_node_persists_network_channel(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Notify Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "notify-flow",
            "--id",
            "slack",
            "--type",
            "notification",
            "--channel",
            "slack",
            "--title",
            "Deploy",
            "--notification-body",
            "Done",
            "--notification-webhook-url",
            "{{secret.SLACK_WEBHOOK_URL}}",
            "--notification-header",
            "Authorization=Bearer {{secret.API_TOKEN}}",
            "--notification-payload-json",
            '{"text":"Done"}',
            "--notification-retry-attempts",
            "2",
            "--notification-expected-status",
            "202",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "notify-flow.toml")
    op = wf.graph._nodes["slack"].operation
    assert isinstance(op, NotificationOperation)
    assert op.channel == "slack"
    assert op.webhook_url == "{{secret.SLACK_WEBHOOK_URL}}"
    assert op.headers == {"Authorization": "Bearer {{secret.API_TOKEN}}"}
    assert op.payload == {"text": "Done"}
    assert op.retry.attempts == 2
    assert op.expected_statuses == [202]


def test_workflow_add_control_nodes(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Control Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    commands = [
        [
            "workflow",
            "add-node",
            "control-flow",
            "--id",
            "start",
            "--type",
            "start",
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-node",
            "control-flow",
            "--id",
            "pass",
            "--type",
            "pass",
            "--message",
            "done",
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-node",
            "control-flow",
            "--id",
            "fail",
            "--type",
            "fail",
            "--message",
            "bad",
            "--data-dir",
            str(tmp_path),
        ],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "control-flow.toml")
    assert wf.graph._nodes["start"].operation.type == "start"
    assert wf.graph._nodes["pass"].operation.message == "done"
    assert wf.graph._nodes["fail"].operation.message == "bad"


def test_workflow_add_node_rejects_duplicate_special_node(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Duplicate Start", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    first = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "duplicate-start",
            "--id",
            "start-a",
            "--type",
            "start",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "duplicate-start",
            "--id",
            "start-b",
            "--type",
            "start",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert second.exit_code != 0
    assert "one START node" in second.output


def test_workflow_add_node_allows_failure(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Allowed Failure", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "allowed-failure",
            "--id",
            "may-fail",
            "--type",
            "bash_command",
            "--command",
            "exit 1",
            "--allow-failure",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "allowed-failure.toml")
    assert wf.graph._nodes["may-fail"].allow_failure is True


def test_workflow_add_node_can_disable_await_all_inputs(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Loop Entry", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "loop-entry",
            "--id",
            "entry",
            "--type",
            "bash_command",
            "--command",
            "echo loop",
            "--no-await-all-inputs",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "loop-entry.toml")
    assert wf.graph._nodes["entry"].await_all_inputs is False
    assert "await_all_inputs = false" in (tmp_path / "loop-entry.toml").read_text()


def test_workflow_add_agent_node_supports_memory_option(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Memory Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-agent",
            "memory-flow",
            "--id",
            "agent-1",
            "--subscription",
            "codex",
            "--working-dir",
            ".",
            "--prompt-path",
            "prompts/agent-1.md",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "memory-flow",
            "--id",
            "remember",
            "--type",
            "agent",
            "--agent-id",
            "agent-1",
            "--prompt-path",
            "prompts/agent-1.md",
            "--working-dir",
            ".",
            "--memory",
            "all",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "memory-flow.toml")
    assert wf.graph._nodes["remember"].operation.memory == "all"


def test_workflow_add_agent_nodes_persist_provider_overrides(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Provider Flow", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "provider-flow",
            "--id",
            "agent-task",
            "--type",
            "agent",
            "--agent-id",
            "agent-1",
            "--prompt-path",
            "prompts/agent-1.md",
            "--working-dir",
            ".",
            "--profile",
            "fast",
            "--model",
            "gpt-5-mini",
            "--provider-timeout",
            "45",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "provider-flow",
            "--id",
            "common-task",
            "--type",
            "common_llm_task",
            "--agent-id",
            "agent-1",
            "--working-dir",
            ".",
            "--profile",
            "quality",
            "--model",
            "claude-sonnet-4-5",
            "--provider-timeout",
            "90",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "provider-flow.toml")
    agent_op = wf.graph._nodes["agent-task"].operation
    assert isinstance(agent_op, AgentOperation)
    assert agent_op.profile == "fast"
    assert agent_op.model == "gpt-5-mini"
    assert agent_op.timeout == 45

    common_op = wf.graph._nodes["common-task"].operation
    assert isinstance(common_op, CommonLlmTaskOperation)
    assert common_op.profile == "quality"
    assert common_op.model == "claude-sonnet-4-5"
    assert common_op.timeout == 90


def test_workflow_rename_and_duplicate_commands(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Original", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "rename",
            "original",
            "--name",
            "Renamed",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "original.toml").exists()
    wf = AgenticWorkflow.from_file(tmp_path / "original.toml")
    assert wf.config.id == "original"
    assert wf.config.name == "Renamed"

    result = runner.invoke(
        app,
        [
            "workflow",
            "duplicate",
            "original",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "renamed-2.toml").exists()


def test_workflow_set_info_configures_run_continuously(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Continuous", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "workflow",
            "set-info",
            "continuous",
            "--run-continuously",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    wf = AgenticWorkflow.from_file(tmp_path / "continuous.toml")
    assert wf.config.run_continuously is True

    result = runner.invoke(
        app,
        [
            "workflow",
            "set-info",
            "continuous",
            "--no-run-continuously",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    wf = AgenticWorkflow.from_file(tmp_path / "continuous.toml")
    assert wf.config.run_continuously is False


def test_workflow_run_rejects_active_continuous_workflow(tmp_path: Path) -> None:
    toml = tmp_path / "continuous.toml"
    toml.write_text(
        _SIMPLE_TOML.replace('id = "simple"', 'id = "continuous"').replace(
            'name = "Simple"',
            'name = "Continuous"\nrun_continuously = true',
        ),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs" / "continuous"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-06-18T10-00-00-0400.log").write_text(
        "2026-06-18T10:00:00-04:00 - continuous started successfully\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["workflow", "run", "continuous", "--data-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "already has an" in result.output
    assert "active run" in result.output


def test_workflow_run_dry_run(tmp_path: Path) -> None:
    f = tmp_path / "wf.toml"
    f.write_text(_SIMPLE_TOML)
    result = runner.invoke(
        app, ["workflow", "run", str(f), "--dry-run", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Execution plan" in result.output
    assert "shell command: echo hello" in result.output
    assert not (tmp_path / "logs").exists()
    assert not workflow_stop_path("simple", tmp_path).exists()


def test_workflow_secrets_json_reports_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GOFER_SECRET_API_TOKEN", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    f = tmp_path / "secret.toml"
    f.write_text(
        """
[workflow]
id = "secret"
name = "Secret"

[[nodes]]
id = "notify"
type = "notification"
body = "Token {{secret.API_TOKEN}}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["workflow", "secrets", str(f), "--json", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["requiredSecrets"] == ["API_TOKEN"]
    assert payload["secretReadiness"][0]["status"] == "missing"
    assert payload["secretReadiness"][0]["sources"] == ["node:notify"]


def test_workflow_approval_cli_lists_and_decides(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
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
    assert "Pending approval not found" in result.output
    decided = store.get("approval-flow", "run.log", "approve")
    assert decided is not None
    assert decided.decision is not None
    assert decided.decision.decision == "timeout"
    assert decided.decision.decided_by == "gofer"


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


def test_workflow_approval_cli_resume_writes_node_outputs_sidecar(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def fake_send(_adapter, _notification) -> None:
        return None

    monkeypatch.setattr("gofer.core.approvals.DesktopNotificationAdapter.send", fake_send)
    workflow_path = tmp_path / "approval-flow.toml"
    workflow_path.write_text(
        """
[workflow]
id = "approval-flow"
name = "Approval Flow"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Approve deploy?"

[[nodes]]
id = "notify"
type = "notification"
title = "Done"
body = "Decision: {{approve.data.decision}}"

[[edges]]
from = "approve"
to = "notify"
""".lstrip(),
        encoding="utf-8",
    )
    run_id = "run.log"
    log_path = tmp_path / "logs" / "approval-flow" / run_id
    log_path.parent.mkdir(parents=True)
    checkpoint_path = tmp_path / "approvals" / "approval-flow" / run_id / "approve.checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "workflowId": "approval-flow",
                "nodeId": "approve",
                "trigger": {},
                "nodeOutputs": {},
            }
        ),
        encoding="utf-8",
    )
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id=run_id,
            node_id="approve",
            message="Approve deploy?",
            workflow_path=str(workflow_path),
            log_path=str(log_path),
            checkpoint_path=str(checkpoint_path),
            waiter_seen_at="2099-01-01T00:00:00+00:00",
        )
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            run_id,
            "approve",
            "--workflow",
            "approval-flow",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    outputs_path = log_path.with_suffix(".outputs.json")
    assert outputs_path.exists()
    payload = json.loads(outputs_path.read_text(encoding="utf-8"))
    assert payload["nodeOutputs"]["approve"]["data"]["decision"] == "approved"
    assert payload["nodeOutputs"]["notify"]["data"]["body"] == "Decision: approved"


def test_workflow_usage_cli_reads_recent_run_sidecars(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Say hi", encoding="utf-8")
    workflow_path = tmp_path / "usage-flow.toml"
    workflow_path.write_text(
        f"""
[workflow]
id = "usage-flow"
name = "Usage Flow"

[[nodes]]
id = "ask"
type = "agent"
agent_id = "assistant"
working_dir = "."
prompt_path = "{prompt_path}"

[agents.assistant]
subscription = "codex"
working_dir = "."
""".lstrip(),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs" / "usage-flow"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "run-1.log"
    log_path.write_text("2026-01-01T00:00:00Z - INFO - completed\n", encoding="utf-8")
    log_path.with_suffix(".outputs.json").write_text(
        json.dumps(
            {
                "workflowId": "usage-flow",
                "runId": "run-1.log",
                "nodeOutputs": {},
                "usageSummary": {
                    "totals": {
                        "agent_calls": 1,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "estimated_cost": 0.0125,
                        "agent_time_seconds": 2.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "usage",
            str(workflow_path),
            "--json",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflowId"] == "usage-flow"
    assert payload["totals"]["agent_calls"] == 1
    assert payload["totals"]["total_tokens"] == 15
    assert payload["runs"][0]["summary"]["totals"]["estimated_cost"] == 0.0125


def test_workflow_run_persists_usage_sidecar_for_usage_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from gofer.cli.commands import workflow as workflow_module

    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Say hi", encoding="utf-8")
    workflow_path = tmp_path / "usage-run.toml"
    workflow_path.write_text(
        f"""
[workflow]
id = "usage-run"
name = "Usage Run"

[[nodes]]
id = "ask"
type = "agent"
agent_id = "assistant"
working_dir = "."
prompt_path = "{prompt_path}"

[agents.assistant]
subscription = "codex"
working_dir = "."
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow_module,
        "_SUBSCRIPTIONS",
        {"codex": FakeSubscription(output="hello")},
    )

    run_result = runner.invoke(
        app,
        ["workflow", "run", str(workflow_path), "--data-dir", str(tmp_path)],
    )
    assert run_result.exit_code == 0, run_result.output
    assert list((tmp_path / "logs" / "usage-run").glob("*.outputs.json"))

    usage_result = runner.invoke(
        app,
        [
            "workflow",
            "usage",
            str(workflow_path),
            "--json",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert usage_result.exit_code == 0, usage_result.output
    payload = json.loads(usage_result.output)
    assert payload["totals"]["agent_calls"] == 1
    assert payload["totals"]["total_tokens"] > 0


def test_workflow_approval_cli_list_resumes_expired_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def fake_send(_adapter, _notification) -> None:
        return None

    monkeypatch.setattr("gofer.core.approvals.DesktopNotificationAdapter.send", fake_send)
    workflow_path = tmp_path / "approval-flow.toml"
    workflow_path.write_text(
        """
[workflow]
id = "approval-flow"
name = "Approval Flow"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Approve deploy?"
timeout_seconds = 1

[[nodes]]
id = "notify"
type = "notification"
title = "Timed out"
body = "Decision: {{approve.data.decision}}"

[[edges]]
from = "approve"
to = "notify"
condition = "on_failure"
""".lstrip(),
        encoding="utf-8",
    )
    run_id = "run.log"
    log_path = tmp_path / "logs" / "approval-flow" / run_id
    checkpoint_path = tmp_path / "approvals" / "approval-flow" / run_id / "approve.checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "workflowId": "approval-flow",
                "nodeId": "approve",
                "trigger": {},
                "nodeOutputs": {},
            }
        ),
        encoding="utf-8",
    )
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id=run_id,
            node_id="approve",
            message="Approve deploy?",
            timeout_seconds=1,
            workflow_path=str(workflow_path),
            log_path=str(log_path),
            checkpoint_path=str(checkpoint_path),
            requested_at=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="seconds"),
        )
    )

    result = runner.invoke(
        app,
        ["workflow", "approvals", "--workflow", "approval-flow", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Resumed" in result.output
    outputs_path = log_path.with_suffix(".outputs.json")
    assert outputs_path.exists()
    payload = json.loads(outputs_path.read_text(encoding="utf-8"))
    assert payload["nodeOutputs"]["approve"]["data"]["decision"] == "timeout"
    assert payload["nodeOutputs"]["notify"]["data"]["body"] == "Decision: timeout"


def test_workflow_approval_cli_skips_restart_resume_for_live_waiter(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "approval-flow.toml"
    workflow_path.write_text(
        """
[workflow]
id = "approval-flow"
name = "Approval Flow"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Approve deploy?"

[[nodes]]
id = "notify"
type = "notification"
title = "Done"
body = "Decision: {{approve.data.decision}}"

[[edges]]
from = "approve"
to = "notify"
""".lstrip(),
        encoding="utf-8",
    )
    run_id = "run.log"
    log_path = tmp_path / "logs" / "approval-flow" / run_id
    checkpoint_path = tmp_path / "approvals" / "approval-flow" / run_id / "approve.checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "workflowId": "approval-flow",
                "nodeId": "approve",
                "trigger": {},
                "nodeOutputs": {},
            }
        ),
        encoding="utf-8",
    )
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id=run_id,
            node_id="approve",
            message="Approve deploy?",
            workflow_path=str(workflow_path),
            log_path=str(log_path),
            checkpoint_path=str(checkpoint_path),
            waiter_pid=os.getpid(),
        )
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            run_id,
            "approve",
            "--workflow",
            "approval-flow",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not log_path.with_suffix(".outputs.json").exists()


def test_workflow_plan_json(tmp_path: Path) -> None:
    f = tmp_path / "wf.toml"
    f.write_text(_SIMPLE_TOML)

    result = runner.invoke(app, ["workflow", "plan", str(f), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflowId"] == "simple"
    assert payload["generations"][0]["nodes"][0]["sideEffects"] == ["shell command: echo hello"]
    assert payload["destructiveActions"] == ["unknown shell command effects: echo hello"]
    assert payload["generations"][0]["nodes"][0]["sideEffectDetails"] == [
        {
            "kind": "command",
            "action": "execute",
            "command": "echo hello",
            "destructive": True,
            "effectsInferred": False,
        }
    ]
    assert payload["destructiveActionDetails"] == [
        {
            "kind": "command",
            "action": "unknown_effects",
            "command": "echo hello",
            "destructive": True,
            "effectsInferred": False,
        }
    ]


def test_workflow_plan_validates_relative_agent_extra_paths_from_workflow_path(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / "stored"
    workflow_dir.mkdir()
    (workflow_dir / "shared").mkdir()
    workflow_path = workflow_dir / "wf.toml"
    workflow_path.write_text(
        """
[workflow]
id = "relative-agent-paths"
name = "Relative Agent Paths"

[agents.reviewer]
subscription = "codex"
working_dir = "."
extra_paths = ["shared"]

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
""".strip()
    )

    result = runner.invoke(app, ["workflow", "plan", str(workflow_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["providerRequirements"][0]["extraPaths"] == [
        str((workflow_dir / "shared").resolve())
    ]


def test_workflow_plan_json_does_not_apply_rich_markup(tmp_path: Path) -> None:
    f = tmp_path / "wf.toml"
    f.write_text(
        """
[workflow]
id = "markup"
name = "Markup"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo [red]"
""".strip()
    )

    result = runner.invoke(app, ["workflow", "plan", str(f), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["generations"][0]["nodes"][0]["detail"] == "echo [red]"
    assert "[red]" in result.output


def test_workflow_plan_prints_fan_out_samples(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "alpha.txt").write_text("alpha")
    (files / "beta.txt").write_text("beta")
    workflow = AgenticWorkflow(WorkflowConfig(id="sample-plan", name="Sample Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="fan",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )
    path = tmp_path / "sample-plan.toml"
    workflow.to_file(path)

    result = runner.invoke(app, ["workflow", "plan", str(path)])

    assert result.exit_code == 0, result.output
    assert "samples=" in result.output
    assert "alpha.txt" in result.output


def test_workflow_plan_prints_partial_fan_out_counts_as_lower_bounds(
    tmp_path: Path,
) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "alpha.txt").write_text("alpha")
    (files / "beta.txt").write_text("beta")
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="partial-fanout-plan",
            name="Partial Fanout Plan",
            resource_limits=ResourceLimits(max_fanout_items=1),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="fan",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )
    path = tmp_path / "partial-fanout-plan.toml"
    workflow.to_file(path)

    result = runner.invoke(app, ["workflow", "plan", str(path)])

    assert result.exit_code == 0, result.output
    assert "count=at least 2" in result.output


def test_workflow_plan_json_marks_partial_fan_out_counts(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "alpha.txt").write_text("alpha")
    (files / "beta.txt").write_text("beta")
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="partial-fanout-json",
            name="Partial Fanout JSON",
            resource_limits=ResourceLimits(max_fanout_items=1),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="fan",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )
    path = tmp_path / "partial-fanout-json.toml"
    workflow.to_file(path)

    result = runner.invoke(app, ["workflow", "plan", str(path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    fan_out = payload["generations"][0]["nodes"][0]["fanOut"]
    assert fan_out["count"] == 2
    assert fan_out["countExact"] is False
    assert fan_out["countLowerBound"] == 2


def test_workflow_plan_prints_node_working_directories(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    workflow_path = tmp_path / "wf.toml"
    workflow_path.write_text(
        """
[workflow]
id = "working-dir-plan"
name = "Working Dir Plan"

[[nodes]]
id = "hello"
type = "bash_command"
command = "pwd"
working_dir = "work"
""".strip()
    )

    result = runner.invoke(app, ["workflow", "plan", str(workflow_path)])

    assert result.exit_code == 0, result.output
    assert "Working dir" in result.output
    assert str(workdir) in result.output


def test_workflow_plan_prints_preview_metadata(tmp_path: Path) -> None:
    workflow_path = tmp_path / "wf.toml"
    workflow_path.write_text(
        """
[workflow]
id = "metadata-plan"
name = "Metadata Plan"
max_total_node_runs = 12

[workflow.resource_limits]
max_fanout_items = 4
max_fanout_concurrency = 2
max_files_scanned = 9

[[nodes]]
id = "first"
type = "bash_command"
command = "echo first"
retry_count = 2
retry_delay_seconds = 3
timeout_seconds = 30
allow_failure = true

[[nodes]]
id = "second"
type = "bash_command"
command = "echo second"

[[edges]]
from = "first"
to = "second"
condition = "on_success"
""".strip()
    )

    result = runner.invoke(app, ["workflow", "plan", str(workflow_path)])

    assert result.exit_code == 0, result.output
    assert "Start nodes:" in result.output
    assert "first" in result.output
    assert "Resource limits:" in result.output
    assert "fanout_items=4" in result.output
    assert "Conditional branches:" in result.output
    assert "first -> second when on_success" in result.output
    assert "retries=2 delay=3.0s" in result.output
    assert "timeout=30.0s" in result.output
    assert "allow_failure=true" in result.output


def test_workflow_import_command(tmp_path: Path) -> None:
    source = tmp_path / "source.toml"
    source.write_text(
        """
[workflow]
id = "import-me"
name = "Import Me"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    data_dir = tmp_path / "data"

    result = runner.invoke(app, ["workflow", "import", str(source), "--data-dir", str(data_dir)])

    assert result.exit_code == 0, result.output
    assert (data_dir / "import-me.toml").exists()


@pytest.mark.parametrize("linked_state", [False, True])
def test_workflow_rm_cleans_state(tmp_path: Path, linked_state: bool) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Clean Me", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    log_dir = tmp_path / "logs" / "clean-me"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-06-13T10-00-00-0400.log").write_text("old run\n")
    memory_dir = tmp_path / "agent-memory" / "clean-me"
    memory_dir.mkdir(parents=True)
    (memory_dir / "agent-step.json").write_text("[]\n")
    chat_path = workflow_chat_prompt_path(tmp_path, "clean-me")
    chat_path.parent.mkdir(parents=True)
    chat_path.write_text("old chat\n")
    stop_path = workflow_stop_path("clean-me", tmp_path)
    if linked_state:
        outside = tmp_path / "outside"
        outside.mkdir()
        stop_path.parent.symlink_to(outside, target_is_directory=True)
    else:
        stop_path.parent.mkdir(parents=True)
    stop_path.write_text("stop\n")

    result = runner.invoke(
        app, ["workflow", "rm", "clean-me", "--yes", "--data-dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "clean-me.toml").exists()
    assert not log_dir.exists()
    assert not memory_dir.exists()
    assert not chat_path.exists()
    if linked_state:
        assert stop_path.read_text() == "stop\n"
        assert stop_path.parent.is_symlink()
    else:
        assert not stop_path.exists()


def test_workflow_logs_commands(tmp_path: Path) -> None:
    toml = tmp_path / "history.toml"
    toml.write_text(_SIMPLE_TOML.replace('id = "simple"', 'id = "history"'))
    log_dir = tmp_path / "logs" / "history"
    log_dir.mkdir(parents=True)
    log = log_dir / "2026-06-13T10-00-00-0400.log"
    log.write_text(
        "2026-06-13T10:00:00-04:00 - history started successfully\n"
        "hello from log\n"
        "2026-06-13T10:00:01-04:00 - INFO - history completed successfully\n"
    )

    list_result = runner.invoke(
        app, ["workflow", "logs", "list", "history", "--data-dir", str(tmp_path)]
    )
    latest_result = runner.invoke(
        app, ["workflow", "logs", "latest", "history", "--data-dir", str(tmp_path)]
    )
    show_result = runner.invoke(
        app,
        ["workflow", "logs", "show", "history", log.name, "--data-dir", str(tmp_path)],
    )

    assert list_result.exit_code == 0, list_result.output
    assert log.name in list_result.output
    assert latest_result.exit_code == 0, latest_result.output
    assert "hello from log" in latest_result.output
    assert show_result.exit_code == 0, show_result.output
    assert "history completed successfully" in show_result.output


def test_workflow_stop_command_writes_stop_marker(tmp_path: Path) -> None:
    toml = tmp_path / "stop-me.toml"
    toml.write_text(_SIMPLE_TOML.replace('id = "simple"', 'id = "stop-me"'))

    result = runner.invoke(app, ["workflow", "stop", "stop-me", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert workflow_stop_path("stop-me", tmp_path).exists()


def test_schedule_add_and_list(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    toml = tmp_path / "wf.toml"
    toml.write_text(_SIMPLE_TOML + '\n[workflow.schedule]\ncron_expression = "0 9 * * *"\n')
    result = runner.invoke(app, ["schedule", "add", str(toml), "--db", str(db)])
    assert result.exit_code == 0
    result2 = runner.invoke(app, ["schedule", "list", "--db", str(db)])
    assert "simple" in result2.output


def test_watch_list_shows_watched_workflows(tmp_path: Path) -> None:
    toml = tmp_path / "watched.toml"
    toml.write_text(
        _SIMPLE_TOML + '\n[workflow.watch]\npath = "inputs"\nglob = "*.txt"\nrecursive = true\n'
    )

    result = runner.invoke(app, ["watch", "list", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "simple" in result.output
    assert "*.txt" in result.output


def test_agent_create_with_inline_prompt(tmp_path: Path) -> None:
    extra_dir = tmp_path / "shared"
    extra_dir.mkdir()
    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "Test Agent",
            "--subscription",
            "claude_code",
            "--working-dir",
            str(tmp_path),
            "--prompt",
            "You are a helpful assistant.",
            "--extra-path",
            str(extra_dir),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    toml_file = tmp_path / "test-agent.toml"
    assert toml_file.exists()
    prompt_file = tmp_path / "prompts" / "test-agent.md"
    assert prompt_file.exists()
    assert prompt_file.read_text() == "You are a helpful assistant."
    wf = AgenticWorkflow.from_file(toml_file)
    assert wf.agents["test-agent"].extra_paths == [extra_dir.resolve()]


def test_agent_create_with_prompt_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "my_prompt.md"
    prompt_file.write_text("# My Prompt\nDo things.")
    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "File Agent",
            "--subscription",
            "codex",
            "--working-dir",
            str(tmp_path),
            "--prompt",
            str(prompt_file),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    toml_file = tmp_path / "file-agent.toml"
    assert toml_file.exists()
    # prompt_file should be referenced directly, not copied
    assert not (tmp_path / "prompts" / "file-agent.md").exists()


def test_agent_create_collision_gets_suffix(tmp_path: Path) -> None:
    base_args = [
        "agent",
        "create",
        "--name",
        "Dup Agent",
        "--subscription",
        "claude_code",
        "--working-dir",
        str(tmp_path),
        "--prompt",
        "hi",
        "--data-dir",
        str(tmp_path),
    ]
    runner.invoke(app, base_args)
    result = runner.invoke(app, base_args)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "dup-agent.toml").exists()
    assert (tmp_path / "dup-agent-2.toml").exists()


def test_agent_create_invalid_subscription(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "Bad Agent",
            "--subscription",
            "nonexistent",
            "--working-dir",
            str(tmp_path),
            "--prompt",
            "hi",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "Invalid subscription" in result.output


def test_agent_list_all(tmp_path: Path) -> None:
    runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "List Agent",
            "--subscription",
            "claude_code",
            "--working-dir",
            str(tmp_path),
            "--prompt",
            "hi",
            "--data-dir",
            str(tmp_path),
        ],
    )
    result = runner.invoke(app, ["agent", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "list-agent" in result.output


def test_plural_commands_are_invalid() -> None:
    for command in ("workflows", "agents", "prompts"):
        result = runner.invoke(app, [command])
        assert result.exit_code != 0
        assert "No such command" in result.output


def test_prompt_command_is_invalid() -> None:
    result = runner.invoke(app, ["prompt"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_workflow_mutation_commands_configure_loop_for_trigger_events(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["workflow", "create", "--name", "Watch Summaries", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    prompt = tmp_path / "prompts" / "summarizer.md"
    prompt.parent.mkdir()
    prompt.write_text("Summarize {{path}}")

    commands = [
        [
            "workflow",
            "set-watch",
            "watch-summaries",
            "--path",
            str(tmp_path / "incoming"),
            "--glob",
            "*.md",
            "--mode",
            "fanout",
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-agent",
            "watch-summaries",
            "--id",
            "summarizer",
            "--subscription",
            "codex",
            "--working-dir",
            str(tmp_path),
            "--prompt-path",
            str(prompt),
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-node",
            "watch-summaries",
            "--id",
            "changed-files",
            "--type",
            "loop",
            "--fan-source",
            "trigger-events",
            "--fan-include-content",
            "--fan-max-concurrency",
            "3",
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-node",
            "watch-summaries",
            "--id",
            "summarize-added-files",
            "--type",
            "agent",
            "--agent-id",
            "summarizer",
            "--prompt-path",
            str(prompt),
            "--working-dir",
            str(tmp_path),
            "--data-dir",
            str(tmp_path),
        ],
        [
            "workflow",
            "add-edge",
            "watch-summaries",
            "--from",
            "changed-files",
            "--to",
            "summarize-added-files",
            "--data-dir",
            str(tmp_path),
        ],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output

    wf = AgenticWorkflow.from_file(tmp_path / "watch-summaries.toml")
    assert wf.config.watch is not None
    assert wf.config.watch.mode == "fanout"
    assert wf.config.watch.glob == "*.md"
    assert "summarizer" in wf.agents
    loop = wf.graph._nodes["changed-files"]
    assert loop.operation.type == "loop"
    assert loop.operation.source.type == "trigger_events"
    assert loop.operation.source.include_content is True
    assert loop.operation.source.max_concurrency == 3
    node = wf.graph._nodes["summarize-added-files"]
    assert node.operation.type == "agent"


def test_workflow_recipe_watch_folder_summarize(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "workflow",
            "recipe",
            "watch-folder-summarize",
            "--name",
            "Summarize New Files",
            "--watch-path",
            str(tmp_path / "incoming"),
            "--glob",
            "*.txt",
            "--provider",
            "codex",
            "--working-dir",
            str(tmp_path),
            "--max-concurrency",
            "2",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    workflow_path = tmp_path / "summarize-new-files.toml"
    prompt_path = tmp_path / "prompts" / "summarize-new-files-summarizer.md"
    assert workflow_path.exists()
    assert prompt_path.exists()

    wf = AgenticWorkflow.from_file(workflow_path)
    assert wf.config.watch is not None
    assert wf.config.watch.path == tmp_path / "incoming"
    assert wf.config.watch.glob == "*.txt"
    assert wf.config.watch.mode == "fanout"
    assert wf.agents["summarizer"].subscription == "codex"
    loop = wf.graph._nodes["changed-files"]
    assert loop.operation.type == "loop"
    assert loop.operation.source.type == "trigger_events"
    assert loop.operation.source.include_content is True
    node = wf.graph._nodes["summarize-added-files"]
    assert node.operation.type == "agent"
