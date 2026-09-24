"""Commit drafting must not give providers the normal coding permissions."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gofer.ui import commit_message


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
            '{"type":"agent_message","text":"fix: show resolved files"}}',
            "",
        )
    )
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider="codex", model="cli-default", diff="+resolved"
    )
    assert result == {"message": "fix: show resolved files"}
    directory = run.call_args.kwargs["cwd"]
    assert not directory.exists()
    assert run.call_args.kwargs["timeout"] == 150
    assert run.call_args.kwargs["stdin"].decode().endswith('{"staged_diff": "+resolved"}')
    run.return_value = (
        0,
        '{"type":"item.completed","item":{"type":"agent_message","text":"Here is a message"}}',
        "",
    )
    with pytest.raises(commit_message.ChatProviderError, match="Conventional Commit"):
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
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
            ]
            assert kwargs["cwd"] == tmp_path
            return 0, diff, ""
        assert (kwargs["cwd"] / "staged-diff.txt").read_text() == diff
        return 0, "", ""

    run = AsyncMock(side_effect=execute)
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message, "_provider_final_message", lambda *_: "feat: update project"
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
    assert prompt.startswith("Read staged-diff.txt in the current directory.")
    assert "diff --git" not in prompt
    assert "Do not use tools" not in prompt
    directory = run.call_args.kwargs["cwd"]
    assert directory != tmp_path
    assert not directory.exists()
    assert run.await_count == 2
    if provider == "codex":
        assert "features.shell_tool=true" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--cd") + 1] == str(directory)
    else:
        assert command[command.index("--tools") + 1] == "Read"
        assert command[command.index("--allowedTools") + 1] == "Read"
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
        attempts.append((kwargs["cwd"] / "staged-diff.txt").read_text())
        return 0, "invalid" if len(attempts) == 1 else "fix: use original changes", ""

    run = AsyncMock(side_effect=execute)
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message,
        "_provider_final_message",
        lambda *_: "invalid" if len(attempts) == 1 else "fix: use original changes",
    )
    assert await commit_message.generate_commit_message(
        provider=provider,
        model="cli-default",
        diff="",
        project_root=tmp_path,
        inspect_staged=True,
    ) == {"message": "fix: use original changes"}
    assert attempts == ["+original staged change", "+original staged change"]
    assert sum(call.args[0][0] == "git" for call in run.call_args_list) == 1


@pytest.mark.asyncio
async def test_explicit_inspection_omits_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/codex")
    run = AsyncMock(return_value=(0, "+staged change", ""))
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(
        commit_message, "_provider_final_message", lambda *_: "fix: handle large commits"
    )
    result = await commit_message.generate_commit_message(
        provider="codex", model="cli-default", diff="", project_root=tmp_path, inspect_staged=True
    )
    assert result == {"message": "fix: handle large commits"}
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
        assert (directory / "staged-diff.txt").read_text() == diff
        assert max(len(arg) for arg in command) < 2000
        assert command[command.index("--model") + 1] == "selected-model"
        if provider == "cursor":
            assert command[command.index("--mode") + 1] == "ask"
            allowed = command[command.index("--allowed-tools") + 1]
            assert "edit_tool_call" not in allowed and "shell_tool_call" not in allowed
            config = json.loads((directory / "cursor-config/cli-config.json").read_text())
            assert "Write(*)" in config["permissions"]["deny"]
            return (
                0,
                '{"type":"result","subtype":"success","result":"fix: handle staged files"}',
                "",
            )
        if provider == "opencode":
            env = kwargs["env"]
            assert isinstance(env, dict)
            permissions = json.loads(env["OPENCODE_PERMISSION"])
            assert permissions["*"] == "deny" and permissions["read"] == "allow"
            assert "edit" not in permissions and "bash" not in permissions
            return 0, '{"type":"text","part":{"text":"fix: handle staged files"}}', ""
        assert command[command.index("--available-tools") + 1] == "view"
        assert "--disable-builtin-mcps" in command
        assert "--allow-all-tools" not in command
        return 0, "fix: handle staged files\n", ""

    monkeypatch.setattr(commit_message, "run_subprocess", run)
    result = await commit_message.generate_commit_message(
        provider=provider,
        model="selected-model",
        diff="" if inspect_staged else diff,
        project_root=tmp_path,
        inspect_staged=inspect_staged,
    )
    assert result == {"message": "fix: handle staged files"}
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
        "```text\nfix(ui): align indicators\n\nPreserve keyboard focus.\n```",
        "Here is the commit message:\n\n```gitcommit\nfix(ui): align indicators\n\n"
        "Preserve keyboard focus.\n```\n\nThis covers the change.",
        "\x1b[32mfix(ui): align indicators\r\n\r\nPreserve keyboard focus.\x1b[0m",
        '"fix(ui): align indicators\n\nPreserve keyboard focus."',
    ],
)
def test_commit_normalization_preserves_body(wrapped: str) -> None:
    assert commit_message._validated_message(wrapped) == {
        "message": "fix(ui): align indicators\n\nPreserve keyboard focus."
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
            "I reviewed the diff." if len(prompts) == 1 else "```\nfix: handle staged changes\n```"
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
    ) == {"message": "fix: handle staged changes"}
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
        assert args[0] == "grok" if provider == "grok" else "staged-diff.txt" in args[0]
        assert kwargs["model"] == "selected-model"
        assert kwargs["effort"] == "high"
        assert kwargs["permission_mode"] == "cli-managed"
        directory = kwargs["cwd"]
        directories.append(directory)
        assert directory != tmp_path
        assert (directory / "staged-diff.txt").read_text() == "+change"
        yield {"type": "thought", "text": "Inspecting"}
        yield {"type": "final", "message": {"body": "fix: handle changes"}, "exitCode": 0}

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
    assert result == {"message": "fix: handle changes"}
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
    cli = AsyncMock(return_value="fix: use commit model")
    monkeypatch.setattr(commit_message, "validate_provider_selection_async", validate)
    monkeypatch.setattr(commit_message, "resolve_provider_executable", lambda _: "/bin/provider")
    monkeypatch.setattr(commit_message, "run_subprocess", run)
    monkeypatch.setattr(commit_message, "_generate_cli_commit", cli)
    monkeypatch.setattr(
        commit_message, "_provider_final_message", lambda *_: "fix: use commit model"
    )
    result = await commit_message.generate_commit_message(
        provider="cursor",
        model="chat-model",
        effort="high",
        permission_mode="default",
        diff="+change",
    )
    assert result == {"message": "fix: use commit model"}
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
