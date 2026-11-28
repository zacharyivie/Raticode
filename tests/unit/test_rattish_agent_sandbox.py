"""Agent steps run in the same operating-system sandbox as Rem's unattended turns."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish import agent_sandbox
from gofer.rattish.agent_sandbox import (
    SandboxError,
    claude_step_settings,
    network_need,
    prepare_step,
    sandbox_problem,
    unsandboxed_reason,
)
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import run_rattish_file
from gofer.subscriptions import claude_code, codex
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.ui import claude_sandbox


@pytest.fixture(autouse=True)
def sandbox_can_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # The checks below don't depend on bubblewrap being installed here.
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda platform=None: None)


def _claude(**changes: Any) -> ResolvedProviderSettings:
    return ResolvedProviderSettings(subscription="claude_code", **changes)


def _codex(**changes: Any) -> ResolvedProviderSettings:
    return ResolvedProviderSettings(subscription="codex", **changes)


def _settings_arg(settings: ResolvedProviderSettings) -> dict[str, Any]:
    args = settings.extra_args
    return json.loads(args[args.index("--settings") + 1])  # type: ignore[no-any-return]


# --- Claude Code settings -------------------------------------------------------------


def test_claude_step_writes_only_to_its_folders_and_hides_credentials(tmp_path: Path) -> None:
    work, extra, data = tmp_path / "work", tmp_path / "extra", tmp_path / "data"
    for folder in (work, extra, data):
        folder.mkdir()
    scratch = data / "run-scratch" / "run-1"

    settings = claude_step_settings(
        working_dir=work, data_dir=data, scratch=scratch, extra_paths=[extra], network=[]
    )

    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    # The step's own tool permissions still decide whether Bash may run.
    assert sandbox["autoAllowBashIfSandboxed"] is False
    assert sandbox["filesystem"]["allowWrite"] == [str(scratch.resolve()), str(extra.resolve())]
    assert "~/.ssh" in sandbox["filesystem"]["denyRead"]
    assert str(data.resolve()) in sandbox["filesystem"]["denyRead"]
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True}
    deny = settings["permissions"]["deny"]
    assert "Read(~/.ssh)" in deny and "Edit(~/.ssh/**)" in deny
    assert "allow" not in settings["permissions"]


def test_claude_step_reaches_listed_hosts_or_any_host(tmp_path: Path) -> None:
    listed = claude_step_settings(
        working_dir=tmp_path,
        data_dir=tmp_path,
        scratch=tmp_path / "s",
        extra_paths=[],
        network=["api.github.com", "*.pypi.org"],
    )
    assert listed["sandbox"]["network"] == {
        "allowedDomains": ["api.github.com", "*.pypi.org"],
        "strictAllowlist": True,
    }

    anywhere = claude_step_settings(
        working_dir=tmp_path,
        data_dir=tmp_path,
        scratch=tmp_path / "s",
        extra_paths=[],
        network="any",
    )
    # allowedDomains takes no wildcard; a bare * WebFetch domain rule opens every host.
    assert anywhere["sandbox"]["network"]["allowedDomains"] == []
    assert anywhere["permissions"]["allow"] == ["WebFetch(domain:*)"]


def test_prepare_step_passes_settings_scratch_and_cleans_mount_points(tmp_path: Path) -> None:
    work, data = tmp_path / "work", tmp_path / "data"
    work.mkdir()
    prepared = prepare_step(
        "claude_code",
        {"network": ["example.com"], "sandbox": "on"},
        _claude(extra_args=["--verbose"]),
        working_dir=work,
        extra_paths=[],
        data_dir=data,
        run_id="run-7",
        triggered=False,
        platform="linux",
    )
    scratch = data.resolve() / "run-scratch" / "run-7"
    assert prepared.sandboxed is True
    assert prepared.settings.extra_args[0] == "--verbose"
    assert _settings_arg(prepared.settings)["sandbox"]["network"]["allowedDomains"] == [
        "example.com"
    ]
    assert scratch.is_dir()
    assert prepared.env["CLAUDE_CODE_TMPDIR"] == str(scratch)
    assert prepared.stub_root == work
    assert prepared.admin_stub_root is None


def test_mount_points_left_in_a_run_worktree_admin_folder_are_removed(tmp_path: Path) -> None:
    # config.worktree is protected but usually missing, so the sandbox may leave an empty
    # read-only file there; what git wrote itself stays.
    work, admin = tmp_path / "work", tmp_path / "admin"
    work.mkdir()
    admin.mkdir()
    (admin / "gitdir").write_text("x\n")
    prepared = agent_sandbox.PreparedStep(_claude(), stub_root=work, admin_stub_root=admin)
    with agent_sandbox.mount_stub_cleanup(prepared):
        stub = admin / "config.worktree"
        stub.touch()
        stub.chmod(0o444)
        (admin / "ORIG_HEAD").write_text("abc\n")
    assert not stub.exists()
    assert (admin / "ORIG_HEAD").exists() and (admin / "gitdir").exists()


@pytest.mark.parametrize(
    ("configuration", "platform"),
    [({"sandbox": "off"}, "linux"), ({}, "win32")],
    ids=["sandbox-off", "native-windows"],
)
def test_unsandboxed_claude_steps_run_as_before(
    tmp_path: Path, configuration: dict[str, Any], platform: str
) -> None:
    settings = _claude()
    prepared = prepare_step(
        "claude_code",
        configuration,
        settings,
        working_dir=tmp_path,
        extra_paths=[],
        data_dir=tmp_path,
        run_id="r",
        triggered=True,
        platform=platform,
    )
    assert prepared.sandboxed is False
    assert prepared.settings is settings
    assert prepared.env == {}


def test_a_sandbox_that_cant_start_stops_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_sandbox, "unavailable_reason", lambda platform=None: "Install bubblewrap."
    )
    with pytest.raises(SandboxError) as caught:
        prepare_step(
            "claude_code",
            {},
            _claude(),
            working_dir=tmp_path,
            extra_paths=[],
            data_dir=tmp_path,
            run_id="r",
            triggered=False,
            platform="linux",
        )
    assert caught.value.code == "RATTISH_SANDBOX_UNAVAILABLE"
    assert "sandbox: off" in (caught.value.suggestion or "")


def test_a_profile_with_its_own_settings_cant_replace_the_sandbox() -> None:
    problem = sandbox_problem(
        "claude_code",
        {},
        _claude(profile_name="mine", extra_args=["--settings", "{}"]),
        triggered=False,
        platform="linux",
    )
    assert problem is not None and "--settings" in problem.message


@pytest.mark.parametrize(
    "args", [["--setting-sources", "user,project"], ["--setting-sources=project"]]
)
def test_a_profile_cant_bring_back_the_projects_own_settings(args: list[str]) -> None:
    """The step loads only the user's settings; a profile that widens that is refused."""
    problem = sandbox_problem(
        "claude_code",
        {},
        _claude(profile_name="mine", extra_args=args),
        triggered=False,
        platform="linux",
    )
    assert problem is not None and "--setting-sources" in problem.message


# --- Codex -----------------------------------------------------------------------------


def test_codex_keeps_workspace_write_and_gets_network_only_for_any(tmp_path: Path) -> None:
    kwargs: dict[str, Any] = {
        "working_dir": tmp_path,
        "extra_paths": [],
        "data_dir": tmp_path,
        "run_id": "r",
        "triggered": True,
        "platform": "linux",
    }
    plain = prepare_step("codex", {}, _codex(), **kwargs)
    assert plain.sandboxed is True and plain.settings.extra_args == []
    anywhere = prepare_step("codex", {"network": "any"}, _codex(), **kwargs)
    assert anywhere.settings.extra_args == ["-c", "sandbox_workspace_write.network_access=true"]
    with pytest.raises(SandboxError) as caught:
        prepare_step("codex", {"network": ["pypi.org"]}, _codex(), **kwargs)
    assert caught.value.code == "RATTISH_NETWORK_UNSUPPORTED"


@pytest.mark.parametrize(
    "settings",
    [
        _codex(sandbox_mode="danger-full-access"),
        _codex(extra_args=["--dangerously-bypass-approvals-and-sandbox"]),
        _codex(extra_args=["-s", "danger-full-access"]),
    ],
    ids=["profile-mode", "bypass-flag", "sandbox-flag"],
)
def test_codex_full_access_needs_sandbox_off_in_triggered_runs(
    settings: ResolvedProviderSettings,
) -> None:
    refused = sandbox_problem("codex", {}, settings, triggered=True, platform="linux")
    assert refused is not None and refused.code == "RATTISH_PREFLIGHT_SANDBOX_REQUIRED"
    assert sandbox_problem("codex", {}, settings, triggered=False, platform="linux") is None
    assert sandbox_problem("codex", {"sandbox": "off"}, settings, triggered=True) is None
    assert unsandboxed_reason("codex", {}, settings, platform="linux") == (
        "its profile gives Codex full access"
    )


def test_providers_without_a_sandbox_and_native_windows_run_unsandboxed() -> None:
    assert unsandboxed_reason("cursor", {}, platform="linux") == (
        "Cursor has no sandbox Raticode can turn on"
    )
    assert "native Windows" in (unsandboxed_reason("claude_code", {}, platform="win32") or "")
    assert "native Windows" in (unsandboxed_reason("codex", {}, platform="win32") or "")
    assert unsandboxed_reason("claude_code", {}, platform="linux") is None
    # A plain API call has no shell to sandbox.
    assert unsandboxed_reason("openai_api", {}, platform="win32") is None


# --- Network needs -----------------------------------------------------------------------


def test_network_needs_come_from_tools_and_prompt_addresses() -> None:
    need = network_need(
        {"tools": ["Bash(curl:*)", "Bash(git push:*)", "Bash(ls:*)", "Read"]},
        "Read https://api.github.com/repos/a/b and http://localhost:8000/x.",
    )
    assert need is not None
    assert need.commands == ("curl", "git push")
    assert need.hosts == ("api.github.com", "localhost")


def test_declared_hosts_any_and_webfetch_cover_network_needs() -> None:
    prompt = "Fetch https://files.pypi.org/simple and https://api.github.com/."
    assert network_need({"network": "any", "tools": ["Bash(curl:*)"]}, prompt) is None
    partial = network_need({"network": ["*.pypi.org"]}, prompt)
    assert partial is not None and partial.hosts == ("api.github.com",)
    assert network_need({"network": ["*.pypi.org", "api.github.com"]}, prompt) is None
    # WebFetch runs outside the sandbox, so an address alone isn't a shell need.
    assert network_need({"tools": ["WebFetch"]}, prompt) is None
    assert network_need({"tools": ["Bash"]}, "No addresses here.") is None


# --- Compiler and preflight --------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Rattish: 1\n\nWorkflow:\n  name: Sandbox\n\n" + body, encoding="utf-8")
    return source


def _preflight(source: Path, tmp_path: Path, *, triggered: bool = False) -> dict[str, list[Any]]:
    compiled = compile_rattish_file(source, data_dir=tmp_path / "data")
    result = run_preflight(
        compiled.ir,
        data_dir=tmp_path / "data",
        subscriptions={"claude_code": ClaudeCodeSubscription(), "codex": CodexSubscription()},
        triggered=triggered,
    )
    found: dict[str, list[Any]] = {}
    for item in result.diagnostics:
        found.setdefault(item.code, []).append(item)
    return found


def test_sandbox_off_is_a_studio_warning(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Node fix:\n  type: agent\n  provider: claude-code\n  prompt: Fix it.\n  sandbox: off\n",
    )
    compiled = compile_rattish_file(source, data_dir=tmp_path / "data")
    warning = next(
        item for item in compiled.diagnostics if item["code"] == "RATTISH_AGENT_SANDBOX_OFF"
    )
    assert warning["severity"] == "warning"
    assert warning["details"] == {"node": "fix", "field": "sandbox"}
    assert compiled.ir["nodes"][0]["configuration"]["sandbox"] == "off"


def test_network_defaults_to_none_and_rejects_addresses(tmp_path: Path) -> None:
    source = _write(tmp_path, "Node a:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n")
    configuration = compile_rattish_file(source, data_dir=tmp_path / "data").ir["nodes"][0][
        "configuration"
    ]
    assert configuration["network"] == [] and configuration["sandbox"] == "on"
    bad = _write(
        tmp_path,
        "Node a:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n"
        "  network:\n    - https://example.com\n",
    )
    with pytest.raises(RattishError):
        compile_rattish_file(bad, data_dir=tmp_path / "data")


def test_preflight_fails_with_a_fix_when_an_agent_needs_the_network(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Node fetch:\n  type: agent\n  provider: claude-code\n"
        "  prompt: Summarize https://api.github.com/repos/a/b.\n",
    )
    found = _preflight(source, tmp_path)
    [error] = found["RATTISH_PREFLIGHT_NETWORK_UNDECLARED"]
    assert error.severity == "error"
    assert "can reach no hosts" in error.message
    assert "network:\n    - api.github.com" in error.suggestions[0]

    fixed = _write(
        tmp_path,
        "Node fetch:\n  type: agent\n  provider: claude-code\n"
        "  prompt: Summarize https://api.github.com/repos/a/b.\n"
        "  network:\n    - api.github.com\n",
    )
    assert "RATTISH_PREFLIGHT_NETWORK_UNDECLARED" not in _preflight(fixed, tmp_path)


def test_preflight_reads_prompt_files_for_addresses(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Node fetch:\n  type: agent\n  provider: codex\n  prompt-path: task.md\n",
    )
    (source.parent / "task.md").write_text("Clone https://github.com/a/b.\n", encoding="utf-8")
    [error] = _preflight(source, tmp_path)["RATTISH_PREFLIGHT_NETWORK_UNDECLARED"]
    # Codex can only switch the network on or off.
    assert "`network: any`" in error.suggestions[0]


def test_preflight_refuses_codex_full_access_only_for_triggered_runs(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "provider-profiles.json").write_text(
        json.dumps(
            {"profiles": {"wide": {"subscription": "codex", "sandbox_mode": "danger-full-access"}}}
        ),
        encoding="utf-8",
    )
    source = _write(
        tmp_path, "Node fix:\n  type: agent\n  provider: codex\n  profile: wide\n  prompt: Fix.\n"
    )
    manual = _preflight(source, tmp_path)
    assert "RATTISH_PREFLIGHT_SANDBOX_REQUIRED" not in manual
    [warning] = manual["RATTISH_PREFLIGHT_RUNS_UNSANDBOXED"]
    assert warning.severity == "warning"
    assert warning.message.startswith(
        "Runs unsandboxed: codex (its profile gives Codex full access)"
    )

    [error] = _preflight(source, tmp_path, triggered=True)["RATTISH_PREFLIGHT_SANDBOX_REQUIRED"]
    assert error.severity == "error"
    assert "sandbox: off" in error.suggestions[0]

    opted_out = _write(
        tmp_path,
        "Node fix:\n  type: agent\n  provider: codex\n  profile: wide\n  prompt: Fix.\n"
        "  sandbox: off\n",
    )
    assert "RATTISH_PREFLIGHT_SANDBOX_REQUIRED" not in _preflight(
        opted_out, tmp_path, triggered=True
    )


def test_preflight_warns_about_unsandboxed_fallback_providers(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Node fix:\n  type: agent\n  provider: claude-code\n  prompt: Fix.\n"
        "  fallback:\n    -\n      provider: cursor\n",
    )
    [warning] = _preflight(source, tmp_path)["RATTISH_PREFLIGHT_RUNS_UNSANDBOXED"]
    assert warning.details["unsandboxed"] == [
        {"provider": "cursor", "reason": "Cursor has no sandbox Raticode can turn on"}
    ]


def test_preflight_names_a_sandbox_that_cant_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_sandbox, "unavailable_reason", lambda platform=None: "Install bubblewrap (bwrap)."
    )
    source = _write(tmp_path, "Node a:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n")
    [error] = _preflight(source, tmp_path)["RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE"]
    assert error.message == "Install bubblewrap (bwrap)."


# --- Running a step ------------------------------------------------------------------------

_FAKE_CLI = """#!{python}
import json, os, sys
sys.stdin.read()
with open({record!r}, "w") as handle:
    json.dump({{"argv": sys.argv[1:], "tmpdir": os.environ.get("CLAUDE_CODE_TMPDIR")}}, handle)
print(json.dumps({{"type": "result", "subtype": "success", "result": "done"}}), flush=True)
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    record = tmp_path / "claude-call.json"
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLI.format(python=sys.executable, record=str(record)))
    script.chmod(0o755)
    monkeypatch.setattr(claude_code, "resolve_provider_executable", lambda _id: str(script))
    return record


@pytest.mark.anyio
async def test_a_claude_step_runs_inside_its_sandbox_and_its_scratch_is_removed(
    tmp_path: Path, fake_claude: Path
) -> None:
    source = _write(
        tmp_path,
        "Node work:\n  type: agent\n  provider: claude-code\n  prompt: Do it.\n"
        "  extra-paths:\n    - ../shared\n  network:\n    - registry.npmjs.org\n",
    )
    (tmp_path / "shared").mkdir()
    data = tmp_path / "data"
    result = await run_rattish_file(
        source, data_dir=data, subscriptions={"claude_code": ClaudeCodeSubscription()}
    )
    assert result.document["status"] == "passed", result.document
    call = json.loads(fake_claude.read_text())
    settings = json.loads(call["argv"][call["argv"].index("--settings") + 1])
    filesystem = settings["sandbox"]["filesystem"]
    run_id = result.document["run_id"]
    scratch = data.resolve() / "run-scratch" / run_id
    assert filesystem["allowWrite"][:2] == [str(scratch), str((tmp_path / "shared").resolve())]
    assert settings["sandbox"]["network"]["allowedDomains"] == ["registry.npmjs.org"]
    assert call["tmpdir"] == str(scratch)
    assert not scratch.exists()


@pytest.mark.anyio
async def test_a_triggered_run_refuses_codex_full_access_before_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(codex, "resolve_provider_executable", lambda _id: sys.executable)
    monkeypatch.setattr(CodexSubscription, "is_available", lambda self: True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "provider-profiles.json").write_text(
        json.dumps(
            {"profiles": {"wide": {"subscription": "codex", "sandbox_mode": "danger-full-access"}}}
        ),
        encoding="utf-8",
    )
    source = _write(
        tmp_path, "Node fix:\n  type: agent\n  provider: codex\n  profile: wide\n  prompt: Fix.\n"
    )
    original = CodexSubscription._build_command

    def spy(self: CodexSubscription, *args: Any, **kwargs: Any) -> list[str]:
        command = original(self, *args, **kwargs)
        calls.append(command)
        return command

    monkeypatch.setattr(CodexSubscription, "_build_command", spy)
    result = await run_rattish_file(
        source,
        data_dir=data,
        subscriptions={"codex": CodexSubscription()},
        started_by="trigger",
    )
    assert result.document["status"] == "preflight_failed"
    assert calls == []
    assert agent_sandbox.in_triggered_run() is False


# --- Triggers ------------------------------------------------------------------------------


def test_triggers_section_and_cli_name_unsandboxed_steps(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app
    from gofer.rattish.workspaces import discover_project_workflows
    from gofer.ui.rattish_triggers import create_trigger_payload, triggers_payload

    data = tmp_path / "data"
    data.mkdir()
    source = _write(
        tmp_path,
        "Node safe:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n  to: loose\n\n"
        "Node loose:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n  sandbox: off\n"
        "  needs: safe\n",
    )
    (registered,) = discover_project_workflows(source.parent, registry_dir=data).workflows
    expected = [
        {"node": "loose", "provider": "claude-code", "reason": "the step sets sandbox: off"}
    ]
    assert triggers_payload(data, registered.workflow_id)["sandbox"] == {
        "unsandboxed": expected,
        "widened": [],
    }
    created = create_trigger_payload(
        data,
        {
            "kind": "cron",
            "workflowId": registered.workflow_id,
            "config": {"expression": "0 9 * * *", "timezone": "UTC"},
        },
    )
    assert created["unsandboxed"] == expected

    result = CliRunner().invoke(
        app,
        [
            "rattish", "triggers", "add", "cron", str(source), "--cron", "0 9 * * *",
            "--timezone", "UTC", "--data-dir", str(data),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert (
        "Runs unsandboxed: step 'loose' on claude-code (the step sets sandbox: off)."
        in " ".join(result.output.split())
    )


# --- A workflow file widening its own sandbox --------------------------------------------


def test_extra_paths_outside_the_project_and_any_host_are_warned_about(tmp_path: Path) -> None:
    outside = tmp_path / "notes"
    outside.mkdir()
    source = _write(
        tmp_path,
        "Node wide:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n"
        f"  extra-paths:\n    - {outside}\n    - build\n  network: any\n",
    )
    [warning] = _preflight(source, tmp_path)["RATTISH_PREFLIGHT_SANDBOX_WIDENED"]
    assert warning.severity == "warning"
    reason = f"its shell can write {outside}, outside the project"
    assert warning.details["widened"] == [
        {"kind": "path", "path": str(outside), "reason": reason},
        {"kind": "network", "reason": "its shell can reach any host"},
    ]
    # Paths inside the project and listed hosts widen nothing.
    narrow = _write(
        tmp_path,
        "Node narrow:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n"
        "  extra-paths:\n    - build\n  network:\n    - example.com\n",
    )
    assert "RATTISH_PREFLIGHT_SANDBOX_WIDENED" not in _preflight(narrow, tmp_path)


@pytest.mark.parametrize("authored", ["~/.ssh", "{data}/claude-accounts", "~"])
def test_preflight_refuses_a_credential_folder_as_an_extra_path(
    tmp_path: Path, authored: str
) -> None:
    data = tmp_path / "data"
    (data / "claude-accounts").mkdir(parents=True)
    source = _write(
        tmp_path,
        "Node keys:\n  type: agent\n  provider: codex\n  prompt: Hi.\n"
        f"  extra-paths:\n    - {authored.format(data=data)}\n",
    )
    [failure] = _preflight(source, tmp_path)["RATTISH_PREFLIGHT_CREDENTIAL_PATH"]
    assert failure.severity == "error"
    assert "credential" in failure.message


def test_triggers_section_and_cli_name_steps_that_widen_their_sandbox(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app
    from gofer.rattish.workspaces import discover_project_workflows
    from gofer.ui.rattish_triggers import create_trigger_payload, triggers_payload

    data = tmp_path / "data"
    data.mkdir()
    source = _write(
        tmp_path,
        "Node fetch:\n  type: agent\n  provider: claude-code\n  prompt: Hi.\n  network: any\n",
    )
    (registered,) = discover_project_workflows(source.parent, registry_dir=data).workflows
    expected = [{"node": "fetch", "kind": "network", "reason": "its shell can reach any host"}]
    assert triggers_payload(data, registered.workflow_id)["sandbox"]["widened"] == expected
    created = create_trigger_payload(
        data,
        {
            "kind": "cron",
            "workflowId": registered.workflow_id,
            "config": {"expression": "0 9 * * *", "timezone": "UTC"},
        },
    )
    assert created["widened"] == expected
    result = CliRunner().invoke(
        app,
        [
            "rattish", "triggers", "add", "cron", str(source), "--cron", "0 9 * * *",
            "--timezone", "UTC", "--data-dir", str(data),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Widens its sandbox: step 'fetch' (its shell can reach any host)." in " ".join(
        result.output.split()
    )


def test_a_step_in_a_worktree_project_cant_change_the_repositorys_hooks_or_config(
    tmp_path: Path,
) -> None:
    import subprocess

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    repo, work, data = tmp_path / "repo", tmp_path / "work", tmp_path / "data"
    repo.mkdir()
    data.mkdir()
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init", "--allow-empty"], check=True)
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(work)], check=True)
    common = (repo / ".git").resolve()

    settings = claude_step_settings(
        working_dir=work, data_dir=data, scratch=data / "s", extra_paths=[], network=[]
    )

    files = settings["sandbox"]["filesystem"]
    assert str(common) in files["allowWrite"]
    for path in (common / "hooks", common / "config", common / "worktrees/work/commondir"):
        assert str(path) in files["denyWrite"]
        assert f"Edit(/{path}/**)" in settings["permissions"]["deny"]
