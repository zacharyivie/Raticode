"""Commit drafting must not give providers the normal coding permissions."""

import shlex
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gofer.ui import commit_message


async def test_staged_diff_stays_inside_selected_subfolder(tmp_path: Path) -> None:
    from gofer.ui.generation_jobs import git

    git(tmp_path, "init", "-b", "main")
    project = tmp_path / "selected-project"
    project.mkdir()
    (project / "change.txt").write_text("selected change\n")
    (tmp_path / "private.txt").write_text("private sibling change\n")
    git(tmp_path, "add", ".")

    diff = await commit_message._read_staged_diff(project)
    assert "selected change" in diff
    assert "private sibling change" not in diff
    assert "private.txt" not in diff


async def test_commit_snapshot_and_diff_ignore_executable_filesystem_monitor(
    tmp_path: Path,
) -> None:
    from gofer.ui.generation_jobs import commit_snapshot, git

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / "change.txt").write_text("staged change\n")
    git(root, "add", ".")
    marker = tmp_path / "monitor-ran"
    monitor = tmp_path / "monitor.py"
    monitor.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "print('token\\0', end='')\n"
    )
    command = " ".join(
        shlex.quote(value.replace("\\", "/")) for value in (sys.executable, str(monitor))
    )
    git(root, "config", "core.fsmonitor", command)
    snapshot = commit_snapshot(root)
    assert not marker.exists(), "commit snapshot executed a repository monitor"
    assert snapshot["tree"] == git(root, "write-tree")
    diff = await commit_message._read_staged_diff(root)
    assert not marker.exists(), "commit diff executed a repository monitor"
    assert "+staged change" in diff


def test_commit_commands_restrict_provider_permissions(tmp_path: Path) -> None:
    codex = commit_message.commit_message_command(
        "codex", "cli-default", None, "draft", "/bin/codex", tmp_path
    )
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in codex
    assert "--add-dir" not in codex
    claude = commit_message.commit_message_command(
        "claude_code", "cli-default", None, "draft", "/bin/claude", tmp_path
    )
    assert claude[claude.index("--tools") + 1] == ""
    assert "--allowedTools" not in claude
    assert "--strict-mcp-config" in claude
    assert "--add-dir" not in claude


@pytest.mark.asyncio
async def test_generation_uses_temporary_directory_and_validates_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/codex")
    run = AsyncMock(
        return_value=(
            0,
            '{"type":"item.completed","item":'
            '{"type":"agent_message","text":"fix: show resolved files'
            '\\n\\n - Show resolved files"}}',
            "",
        )
    )
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider="codex", model="cli-default", diff="+resolved"
    )
    assert result == {"message": "fix: show resolved files\n\n - Show resolved files"}
    directory = run.call_args.kwargs["cwd"]
    assert not directory.exists()
    assert run.call_args.kwargs["timeout"] == 150
    assert run.call_args.kwargs["stdin"].decode().endswith('{"staged_diff": "+resolved"}')
    assert (
        "one line high level executive summary of changes. "
        "Only absolutely necessary technical terms; no jargon."
    ) in run.call_args.kwargs["stdin"].decode()
    run.return_value = (
        0,
        '{"type":"item.completed","item":{"type":"agent_message","text":"Here is a message"}}',
        "",
    )
    with pytest.raises(
        commit_message.ChatProviderError, match="commit message matching the template"
    ):
        await commit_message.generate_commit_message(
            provider="codex", model="cli-default", diff="+resolved"
        )


@pytest.mark.asyncio
async def test_generation_rejects_empty_diff() -> None:
    with pytest.raises(ValueError, match="staged changes"):
        await commit_message.generate_commit_message(provider="codex", model="cli-default", diff="")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code"])
async def test_large_commit_uses_captured_diff_outside_project(
    monkeypatch: pytest.MonkeyPatch, provider: str, tmp_path: Path
) -> None:
    diff = "".join(
        f"diff --git a/file-{i}.py b/file-{i}.py\n" + "+change\n" * 1000 for i in range(92)
    )
    validate = AsyncMock()
    monkeypatch.setattr(commit_message, "validate_provider_selection_async", validate)
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")

    async def execute(command, **kwargs):
        if command[0] == "git":
            assert command == [
                "git",
                "-c",
                "core.fsmonitor=false",
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--",
                ".",
            ]
            assert kwargs["cwd"] == tmp_path
            return 0, diff, ""
        assert not (kwargs["cwd"] / "staged-diff.txt").exists()
        return 0, "", ""

    run = AsyncMock(side_effect=execute)
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: "feat: update project\n\n - Update project",
    )
    await commit_message.generate_commit_message(
        provider=provider, model="selected-model", effort="high", diff=diff, project_root=tmp_path
    )
    validate.assert_awaited_once_with(provider, "selected-model", "high")
    command = run.call_args.args[0]
    assert command[command.index("--model") + 1] == "selected-model"
    assert 'model_reasoning_effort="high"' in command if provider == "codex" else "high" in command
    prompt = run.call_args.kwargs["stdin"].decode()
    assert prompt not in command
    assert prompt.startswith("Write only a commit message")
    assert "diff --git" not in prompt
    assert "Do not use tools" in prompt
    directory = run.call_args.kwargs["cwd"]
    assert directory != tmp_path
    assert not directory.exists()
    assert run.await_count > 2
    if provider == "codex":
        assert "features.shell_tool=false" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--cd") + 1] == str(directory)
    else:
        assert command[command.index("--tools") + 1] == ""
        assert "--allowedTools" not in command
        assert not any("Bash" in arg for arg in command)
        assert "Edit" not in command


@pytest.mark.asyncio
async def test_inspection_requires_project() -> None:
    with pytest.raises(ValueError, match="project folder"):
        await commit_message.generate_commit_message(
            provider="codex", model="cli-default", diff="", inspect_staged=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code", "copilot"])
@pytest.mark.parametrize(
    "code,diff,error", [(0, "", "staged changes"), (1, "", "Could not read staged changes")]
)
async def test_index_failure_stops_before_provider_launch(
    monkeypatch, tmp_path, provider, code, diff, error
):
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    run = AsyncMock(return_value=(code, diff, ""))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    with pytest.raises((ValueError, commit_message.ChatProviderError), match=error):
        await commit_message.generate_commit_message(
            provider=provider,
            model="cli-default",
            diff="",
            project_root=tmp_path,
            inspect_staged=True,
        )
    assert run.await_count == 1
    assert run.call_args.args[0][0] == "git"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code", "copilot"])
async def test_format_retry_uses_same_index_snapshot(monkeypatch, tmp_path, provider):
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    attempts = []

    async def execute(command, **kwargs):
        if command[0] == "git":
            return 0, "+original staged change", ""
        attempts.append(kwargs.get("stdin", b"").decode() or command[command.index("-p") + 1])
        return (
            0,
            "invalid"
            if len(attempts) == 1
            else "fix: use original changes\n\n - Use original changes",
            "",
        )

    run = AsyncMock(side_effect=execute)
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: (
            "invalid"
            if len(attempts) == 1
            else "fix: use original changes\n\n - Use original changes"
        ),
    )
    assert await commit_message.generate_commit_message(
        provider=provider,
        model="cli-default",
        diff="",
        project_root=tmp_path,
        inspect_staged=True,
    ) == {"message": "fix: use original changes\n\n - Use original changes"}
    assert "+original staged change" in attempts[0]
    assert "+original staged change" not in attempts[1]
    assert '"previous_response": "invalid"' in attempts[1]
    assert "validation_error" in attempts[1]
    assert sum(call.args[0][0] == "git" for call in run.call_args_list) == 1


@pytest.mark.asyncio
async def test_explicit_inspection_omits_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/codex")
    run = AsyncMock(return_value=(0, "+staged change", ""))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: "fix: handle large commits\n\n - Handle large commits",
    )
    result = await commit_message.generate_commit_message(
        provider="codex", model="cli-default", diff="", project_root=tmp_path, inspect_staged=True
    )
    assert result == {"message": "fix: handle large commits\n\n - Handle large commits"}
    assert run.call_args_list[0].kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["cwd"] != tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
@pytest.mark.parametrize("inspect_staged", [False, True])
@pytest.mark.parametrize("diff_repeats", [1, 25000])
async def test_cli_commit_drafting_reads_only_supplied_diff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    inspect_staged: bool,
    diff_repeats: int,
) -> None:
    import json

    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    validate = AsyncMock()
    monkeypatch.setattr(commit_message, "validate_provider_selection_async", validate)
    diff = "+change\n" * diff_repeats
    calls = []

    async def run(command: list[str], **kwargs: object) -> tuple[int, str, str]:
        calls.append(command)
        if command[0] == "git":
            assert kwargs["cwd"] == tmp_path
            assert "--no-ext-diff" in command and "--no-textconv" in command
            return 0, diff, ""
        directory = kwargs["cwd"]
        assert isinstance(directory, Path) and directory != tmp_path
        assert not (directory / "staged-diff.txt").exists()
        assert max(len(arg.encode("utf-8")) for arg in command) < 32000
        assert command[command.index("--model") + 1] == "selected-model"
        if provider == "cursor":
            assert command[command.index("--mode") + 1] == "ask"
            allowed = command[command.index("--allowed-tools") + 1]
            assert "edit_tool_call" not in allowed and "shell_tool_call" not in allowed
            config = json.loads((directory / "cursor-config/cli-config.json").read_text())
            assert "Write(*)" in config["permissions"]["deny"]
            return (
                0,
                '{"type":"result","subtype":"success","result":"fix: handle staged files'
                '\\n\\n - Handle staged files"}',
                "",
            )
        if provider == "opencode":
            env = kwargs["env"]
            assert isinstance(env, dict)
            permissions = json.loads(env["OPENCODE_PERMISSION"])
            assert permissions["*"] == "deny" and permissions["read"] == "allow"
            assert "edit" not in permissions and "bash" not in permissions
            return (
                0,
                '{"type":"text","part":{"text":"fix: handle staged files'
                '\\n\\n - Handle staged files"}}',
                "",
            )
        assert command[command.index("--available-tools") + 1] == "view"
        assert "--disable-builtin-mcps" in command
        assert "--allow-all-tools" not in command
        return 0, "fix: handle staged files\n\n - Handle staged files\n", ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider=provider,
        model="selected-model",
        diff="" if inspect_staged else diff,
        project_root=tmp_path,
        inspect_staged=inspect_staged,
    )
    assert result == {"message": "fix: handle staged files\n\n - Handle staged files"}
    validate.assert_awaited_once_with(provider, "selected-model", None)
    # Oversized supplied diffs also read the current index, never truncate argv.
    assert len(calls) == (2 if inspect_staged or diff_repeats == 25000 else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "copilot"])
@pytest.mark.parametrize("exit_code,stdout", [(1, ""), (0, "Not a commit message")])
async def test_cli_commit_rejects_provider_failures_and_invalid_messages(
    monkeypatch: pytest.MonkeyPatch, provider: str, exit_code: int, stdout: str
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    monkeypatch.setattr(
        commit_message, "run_subprocess", AsyncMock(return_value=(exit_code, stdout, "failed"))
    )
    with pytest.raises(commit_message.ChatProviderError):
        await commit_message.generate_commit_message(
            provider=provider, model="cli-default", diff="+ok"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code", "copilot"])
async def test_cli_commit_rejects_truncated_index_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    run = AsyncMock(
        return_value=(
            0,
            "+partial\n"
            f"[subprocess output truncated at {commit_message.STAGED_DIFF_FILE_LIMIT} bytes]",
            "",
        )
    )
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    with pytest.raises(commit_message.ChatProviderError, match="exceed 32 MB"):
        await commit_message.generate_commit_message(
            provider=provider,
            model="cli-default",
            diff="",
            project_root=tmp_path,
            inspect_staged=True,
        )
    assert run.await_count == 1


@pytest.mark.parametrize(
    "wrapped",
    [
        "```text\nfix: align indicators\n\n - Preserve keyboard focus.\n```",
        "Here is the commit message:\n\n```gitcommit\nfix: align indicators\n\n"
        " - Preserve keyboard focus.\n```\n\nThis covers the change.",
        "\x1b[32mfix: align indicators\r\n\r\n - Preserve keyboard focus.\x1b[0m",
        '"fix: align indicators\n\n - Preserve keyboard focus."',
    ],
)
def test_commit_normalization_preserves_body(wrapped: str) -> None:
    assert commit_message._validated_message(wrapped) == {
        "message": "fix: align indicators\n\n - Preserve keyboard focus."
    }


@pytest.mark.parametrize(
    "response",
    [
        "No changes to commit.",
        "I could not inspect the diff. Try fix: update files",
        "```\nfix: first option\n```\n```\nfeat: second option\n```",
        "fix: first option\n\nfeat: second option",
        "```\nfix:   \n```",
    ],
)
def test_commit_normalization_rejects_nonanswers_and_ambiguous_options(response: str) -> None:
    with pytest.raises(commit_message.ChatProviderError):
        commit_message._validated_message(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "claude_code", "copilot", "opencode"])
async def test_commit_retries_format_once_and_uses_final_response(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    import json

    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    prompts = []

    async def run(command, **kwargs):
        prompts.append(
            kwargs.get("stdin", b"").decode() or command[command.index("-p") + 1]
            if provider != "opencode"
            else command[-1]
        )
        answer = (
            "I reviewed the diff."
            if len(prompts) == 1
            else "```\nfix: handle staged changes\n\n - Handle staged changes\n```"
        )
        if provider == "copilot":
            return 0, answer, ""
        if provider == "opencode":
            return 0, json.dumps({"type": "text", "part": {"text": answer}}), ""
        records = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Reading files."}]},
            },
            {"type": "result", "subtype": "success", "result": answer},
        ]
        return 0, "\n".join(json.dumps(record) for record in records), ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    assert await commit_message.generate_commit_message(
        provider=provider,
        model="cli-default",
        diff="+change",
    ) == {"message": "fix: handle staged changes\n\n - Handle staged changes"}
    assert len(prompts) == 2
    assert "previous response" in prompts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "claude_code", "copilot", "opencode"])
async def test_commit_transport_errors_are_not_retried(monkeypatch, provider):
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    run = AsyncMock(return_value=(1, "", "Authentication required"))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    with pytest.raises(commit_message.ChatProviderError, match="Authentication required"):
        await commit_message.generate_commit_message(
            provider=provider, model="cli-default", diff="+ok"
        )
    assert run.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["grok", "antigravity"])
@pytest.mark.parametrize("inspect_staged", [False, True])
async def test_native_commit_uses_selected_model_and_permissions(
    monkeypatch,
    tmp_path,
    provider,
    inspect_staged,
):
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    validate = AsyncMock()
    monkeypatch.setattr(commit_message, "validate_provider_selection_async", validate)
    run = AsyncMock(return_value=(0, "+change", ""))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    directories = []

    async def stream(*args, **kwargs):
        assert args[0] == "grok" if provider == "grok" else "staged_diff" in args[0]
        assert kwargs["model"] == "selected-model"
        assert kwargs["effort"] == "high"
        assert kwargs["permission_mode"] == "cli-managed"
        directory = kwargs["cwd"]
        directories.append(directory)
        assert directory != tmp_path
        assert not (directory / "staged-diff.txt").exists()
        yield {"type": "thought", "text": "Inspecting"}
        yield {
            "type": "final",
            "message": {"body": "fix: handle changes\n\n - Handle changes"},
            "exitCode": 0,
        }

    monkeypatch.setattr(
        commit_message, "stream_acp" if provider == "grok" else "stream_antigravity", stream
    )
    result = await commit_message.generate_commit_message(
        provider=provider,
        model="selected-model",
        effort="high",
        permission_mode="cli-managed",
        diff="" if inspect_staged else "+change",
        inspect_staged=inspect_staged,
        project_root=tmp_path,
    )
    assert result == {"message": "fix: handle changes\n\n - Handle changes"}
    validate.assert_awaited_once_with(provider, "selected-model", "high")
    assert all(not directory.exists() for directory in directories)
    assert run.await_count == int(inspect_staged)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["grok", "antigravity"])
async def test_native_commit_preserves_permission_opt_in(provider):
    with pytest.raises(ValueError, match="CLI-managed"):
        await commit_message.generate_commit_message(
            provider=provider, model="cli-default", diff="+ok"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["grok", "antigravity"])
async def test_native_commit_error_is_not_mistaken_for_a_message(monkeypatch, provider):
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    calls = []

    async def stream(*args, **kwargs):
        calls.append(args)
        yield {
            "type": "error",
            "error": "Login required",
            "message": {"body": "fix: should not use"},
        }

    monkeypatch.setattr(
        commit_message, "stream_acp" if provider == "grok" else "stream_antigravity", stream
    )
    with pytest.raises(commit_message.ChatProviderError, match="Login required"):
        await commit_message.generate_commit_message(
            provider=provider,
            model="cli-default",
            diff="+ok",
            permission_mode="cli-managed",
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "grok", "antigravity"]
)
async def test_dedicated_commit_selection_overrides_active_rem_and_resets_effort(
    monkeypatch,
    provider,
) -> None:
    from gofer.core.provider_preferences import save_commit_message_preference

    save_commit_message_preference({"provider": provider, "model": "commit-model"})
    validate = AsyncMock()
    run = AsyncMock(return_value=(0, "", ""))
    cli = AsyncMock(return_value="fix: use commit model\n\n - Use commit model")
    monkeypatch.setattr(commit_message, "validate_provider_selection_async", validate)
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(commit_message, "_generate_cli_commit", cli)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: "fix: use commit model\n\n - Use commit model",
    )
    result = await commit_message.generate_commit_message(
        provider="cursor",
        model="chat-model",
        effort="high",
        permission_mode="default",
        diff="+change",
    )
    assert result == {"message": "fix: use commit model\n\n - Use commit model"}
    validate.assert_awaited_once_with(provider, "commit-model", None)
    if provider in {"codex", "claude_code"}:
        command = run.call_args.args[0]
        assert command[command.index("--model") + 1] == "commit-model"
        assert "--effort" not in command
        assert not any("model_reasoning_effort" in part for part in command)
    else:
        assert cli.call_args.args[:3] == (provider, "commit-model", None)
        if provider in {"grok", "antigravity"}:
            assert cli.call_args.kwargs["permission_mode"] == "cli-managed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, error", [({"deniedModels": ["sol"]}, "denied"), ({"enabled": False}, "disabled")]
)
async def test_dedicated_commit_selection_reports_unusable_settings(changes, error) -> None:
    from gofer.core.provider_preferences import (
        save_commit_message_preference,
        save_provider_preference,
    )

    save_commit_message_preference({"provider": "codex", "model": "sol"})
    save_provider_preference("codex", changes)
    with pytest.raises(ValueError, match=error):
        await commit_message.generate_commit_message(
            provider="cursor", model="chat", diff="+change"
        )


@pytest.mark.parametrize("count", [1, 7, 8, 9])
def test_default_commit_template_enforces_change_limit(count: int) -> None:
    message = "feat: add preferences\n\n" + "\n".join(
        f" - Explain change {i}" for i in range(1, count + 1)
    )
    if count <= 8:
        assert commit_message._validated_message(message) == {"message": message}
    else:
        with pytest.raises(commit_message.ChatProviderError, match="1 to 8"):
            commit_message._validated_message(message)


@pytest.mark.parametrize(
    "message",
    [
        "fix: summary only",
        "docs: update docs\n\n - Explain the setting",
        "fix: missing bullet\n\nDescribe a change without a bullet",
        "fix: wrap a change\n\n - A change\nwith a continuation",
    ],
)
def test_default_commit_template_rejects_wrong_structure(message: str) -> None:
    with pytest.raises(commit_message.ChatProviderError):
        commit_message._validated_message(message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "grok", "antigravity"]
)
async def test_custom_template_reaches_every_commit_provider(monkeypatch, provider) -> None:
    import json

    from gofer.core.provider_preferences import save_commit_message_preference

    template = "Summary: one line\n\n* One change per line"
    save_commit_message_preference({"template": template})
    answer = "Summary: handle settings\n\n* Keep the user's format"
    prompts = []
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")

    async def run(command, **kwargs):
        prompt = kwargs.get("stdin", b"").decode()
        if not prompt:
            prompt = command[-1] if provider == "opencode" else command[command.index("-p") + 1]
        prompts.append(prompt)
        if provider == "copilot":
            return 0, answer, ""
        if provider == "opencode":
            return 0, json.dumps({"type": "text", "part": {"text": answer}}), ""
        if provider == "cursor":
            return 0, json.dumps({"type": "result", "subtype": "success", "result": answer}), ""
        return 0, "", ""

    async def stream(*args, **kwargs):
        prompts.append(args[1] if provider == "grok" else args[0])
        yield {"type": "final", "message": {"body": answer}, "exitCode": 0}

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(commit_message, "_provider_final_message", lambda *_: answer)
    monkeypatch.setattr(commit_message, "stream_acp", stream)
    monkeypatch.setattr(commit_message, "stream_antigravity", stream)
    result = await commit_message.generate_commit_message(
        provider=provider, model="cli-default", diff="+change", permission_mode="cli-managed"
    )
    assert result == {"message": answer}
    assert len(prompts) == 1
    assert template in prompts[0]
    assert "never more than 8" in prompts[0]
    assert "body only when useful" not in prompts[0]
    too_many = "Summary: changes\n\n" + "\n".join(f"* Change {i}" for i in range(9))
    with pytest.raises(commit_message.ChatProviderError, match="1 to 8"):
        commit_message._validated_message(too_many, template=template)


@pytest.mark.asyncio
async def test_large_background_diff_never_rereads_live_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    diff = "+captured snapshot\n" * 25000

    async def run(command: list[str], **kwargs: object) -> tuple[int, str, str]:
        assert command[0] != "git"
        directory = kwargs["cwd"]
        assert isinstance(directory, Path)
        assert not (directory / "staged-diff.txt").exists()
        return 0, "", ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: "fix: capture staged changes\n\n - Preserve the original branch snapshot",
    )
    result = await commit_message.generate_commit_message(
        provider="codex",
        model="cli-default",
        diff=diff,
        captured_diff=True,
    )
    assert result["message"].startswith("fix: capture staged changes")


@pytest.mark.parametrize(
    "bullet", ["- Change", "  - Change  ", "\t- Change", "* Change", "• Change"]
)
def test_default_bullet_whitespace_is_normalized(bullet: str) -> None:
    assert commit_message._validated_message("fix: preserve focus\n\n" + bullet) == {
        "message": "fix: preserve focus\n\n - Change"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code", "cursor", "opencode"])
async def test_final_answer_survives_megabytes_of_tool_output(monkeypatch, provider) -> None:
    import json

    from gofer.utils.process import run_subprocess

    answer = "fix: keep the final answer\n\n - Preserve the café label"
    if provider == "codex":
        final = {"type": "item.completed", "item": {"type": "agent_message", "text": answer}}
    elif provider in {"claude_code", "cursor"}:
        final = {"type": "result", "subtype": "success", "result": answer}
    else:
        final = {"type": "text", "part": {"text": answer}}
    script = (
        "import sys\n"
        'sys.stdout.write(\'{"type":"tool_use","output":"\' + \'x\'*2100000 + \'"}\\n\')\n'
        f"sys.stdout.write({json.dumps(final, ensure_ascii=False)!r})\n"
    )
    diagnostics: list[dict[str, Any]] = []
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")

    async def run(command, **kwargs):
        return await run_subprocess([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider=provider, model="cli-default", diff="+café", on_diagnostic=diagnostics.append
    )
    assert result == {"message": answer}
    assert diagnostics[-1]["logsTruncated"] is True
    assert diagnostics[-1]["draft"] == answer
    assert diagnostics[-1]["exitCode"] == 0


@pytest.mark.asyncio
async def test_provider_error_after_truncated_logs_is_not_accepted(monkeypatch) -> None:
    import json

    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    run = AsyncMock()

    async def execute(command, **kwargs):
        feed = kwargs["on_stdout"]
        feed(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "fix: answer\n\n - Change"},
                }
            )
            + "\n"
        )
        feed('{"type":"turn.failed","error":{"message":"Login expired"}}\n')
        return 0, "[subprocess output truncated at 1048576 bytes]", ""

    run.side_effect = execute
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    with pytest.raises(commit_message.ChatProviderError, match="Login expired"):
        await commit_message.generate_commit_message(
            provider="codex", model="cli-default", diff="+x"
        )
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_missing_final_answer_reports_collection_failure_without_retry(monkeypatch) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    run = AsyncMock(return_value=(0, '{"type":"turn.completed"}', ""))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    with pytest.raises(commit_message.ChatProviderError, match="output collection failed"):
        await commit_message.generate_commit_message(
            provider="codex", model="cli-default", diff="+x"
        )
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_278_file_review_covers_binary_deleted_renamed_and_generated_files(
    monkeypatch,
) -> None:
    import json

    patches = []
    for index in range(278):
        metadata = (
            "deleted file mode 100644\n"
            if index == 0
            else "similarity index 100%\nrename from old.txt\nrename to new.txt\n"
            if index == 1
            else "Binary files a/picture.png and b/picture.png differ\n"
            if index == 2
            else "new file mode 100644\n"
        )
        patches.append(
            f"diff --git a/file-{index} b/file-{index}\n"
            + metadata
            + ("@@ -0,0 +1 @@\n+generated data\n" * 500 if index > 2 else "")
        )
    diff = "".join(patches)
    reviewed = []
    prompts = []
    progress: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")

    async def run(command, **kwargs):
        assert command[0] != "git"
        prompt = kwargs["stdin"].decode()
        prompts.append(prompt)
        assert len(prompt.encode("utf-8")) < 2 * commit_message.COMMIT_INPUT_LIMIT + 8000
        data = json.loads(prompt[prompt.rindex("\n{") + 1 :])
        if "part" in data:
            reviewed.append(data["staged_diff"])
            answer = f"Part {data['part']}: update generated data and file metadata."
        else:
            assert "change_summaries" in data
            answer = "chore: update project files\n\n- Refresh generated data and file metadata"
        return 0, json.dumps({"type": "result", "result": answer}), ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider="codex",
        model="cli-default",
        diff=diff,
        captured_diff=True,
        on_progress=progress.append,
        on_diagnostic=diagnostics.append,
    )
    review = "".join(reviewed)
    for index in range(278):
        assert f"diff --git a/file-{index} b/file-{index}\n" in review
    assert "deleted file mode" in review
    assert "rename from old.txt" in review
    assert "Binary files" in review
    assert "content between excerpts is omitted" in review
    assert result["message"].startswith("chore:")
    assert diagnostics[0]["files"] == 278
    assert diagnostics[0]["batches"] == len(reviewed)
    assert progress[-1] == "Drafting commit message"


@pytest.mark.parametrize("text", ["😀" * 20000, "+minified" * 10000, "line\n" * 10000])
def test_diff_batches_preserve_every_character_with_bounded_utf8(text: str) -> None:
    batches = commit_message._diff_batches(text)
    assert "".join(batches) == text
    assert all(len(batch.encode("utf-8")) <= commit_message.COMMIT_INPUT_LIMIT for batch in batches)


@pytest.mark.parametrize("provider", ["codex", "claude_code", "cursor", "opencode"])
def test_answer_capture_ignores_commentary_and_tool_results(provider: str) -> None:
    import json

    answer = "fix: retain the answer\n\n - Keep the message"
    capture = commit_message._AnswerCapture(provider)
    capture.feed('{"type":"tool_use","result":"Tool output is not the answer"}\n')
    if provider == "opencode":
        events = [
            {
                "type": "text",
                "part": {"id": "a", "messageID": "commentary", "text": "Reading changes."},
            },
            {"type": "text", "part": {"id": "b", "messageID": "answer", "text": answer}},
            {"type": "text", "part": {"id": "b", "messageID": "answer", "text": answer}},
        ]
    elif provider == "codex":
        events = [
            {"type": "item.started", "item": {"type": "agent_message", "text": "Working"}},
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {"role": "assistant", "content": [{"type": "output_text", "text": answer}]}
                    ]
                },
            },
        ]
    else:
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working"}]}},
            {"type": "result", "subtype": "success", "result": answer},
        ]
    for event in events:
        line = json.dumps(event)
        for index in range(0, len(line), 7):
            capture.feed(line[index : index + 7])
        capture.feed("\n")
    capture.finish()
    assert capture.message == answer
    assert capture.error == ""


@pytest.mark.asyncio
async def test_summary_reduction_and_cancellation_are_bounded(monkeypatch) -> None:
    import json
    import threading

    monkeypatch.setattr(commit_message, "COMMIT_INPUT_LIMIT", 1000)
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    stages = []
    cancel = threading.Event()

    async def run(command, **kwargs):
        prompt = kwargs["stdin"].decode()
        data = json.loads(prompt[prompt.rindex("\n{") + 1 :])
        if "part" in data:
            stages.append("summary")
            answer = f"Batch {data['part']}: " + "Update metadata. " * 15
        elif "summaries" in data:
            stages.append("reduction")
            answer = "Combine metadata changes."
        else:
            stages.append("draft")
            answer = "chore: update metadata\n\n - Preserve all captured changes"
        return 0, json.dumps({"type": "result", "result": answer}), ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    diff = "".join(f"diff --git a/{i} b/{i}\n+change\n" for i in range(200))
    result = await commit_message.generate_commit_message(
        provider="codex", model="cli-default", diff=diff, cancel_event=cancel
    )
    assert "reduction" in stages
    assert result["message"].startswith("chore:")
    assert stages[-1] == "draft"
    stages.clear()

    def stop_after_second_batch(text):
        if text.startswith("Reading changes 2/"):
            cancel.set()

    with pytest.raises(commit_message.ChatProviderError, match="stopped"):
        await commit_message.generate_commit_message(
            provider="codex",
            model="cli-default",
            diff=diff,
            cancel_event=cancel,
            on_progress=stop_after_second_batch,
        )
    assert stages == ["summary"]
