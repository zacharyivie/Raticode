from __future__ import annotations

from pathlib import Path

import pytest

from gofer.core.agent import AgentConfig
from gofer.core.graph import GraphNode
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.operations import AgentOperation, OperationType
from gofer.core.planner import build_execution_plan
from gofer.core.provider_profiles import (
    MASKED_SECRET_VALUE,
    ProviderProfile,
    ResolvedProviderSettings,
    is_sensitive_env_name,
    load_provider_profiles,
    provider_profile_from_ui_payload,
    resolved_provider_env,
    save_provider_profiles,
    validate_provider_settings,
)
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription
from gofer.ui.api import (
    ProviderProfileError,
    delete_provider_profile_payload,
    provider_profiles_payload,
    upsert_provider_profile_payload,
)


def test_provider_profile_serialization_round_trips(tmp_path: Path) -> None:
    save_provider_profiles(
        {
            "fast": ProviderProfile.model_validate(
                {
                    "name": "fast",
                    "subscription": "codex",
                    "model": "gpt-5-mini",
                    "timeout": 45,
                    "reasoning": "low",
                    "approval_mode": "never",
                    "sandbox_mode": "read-only",
                    "extra_args": ["--flag"],
                    "env": {"A": "B"},
                }
            )
        },
        tmp_path,
    )

    loaded = load_provider_profiles(tmp_path)

    assert loaded["fast"].model == "gpt-5-mini"
    assert loaded["fast"].effort == "low"
    assert loaded["fast"].timeout == 45
    assert loaded["fast"].extra_args == ["--flag"]


def test_direct_provider_profile_serialization_round_trips(tmp_path: Path) -> None:
    save_provider_profiles(
        {
            "api": ProviderProfile(
                name="api",
                subscription="openai_api",
                model="gpt-5-mini",
                api_base_url="https://example.test/v1",
                api_key_secret="OPENAI_TEST",
                organization="org-1",
                provider_options={"api": "chat", "temperature": "0"},
            )
        },
        tmp_path,
    )

    loaded = load_provider_profiles(tmp_path)["api"]

    assert loaded.subscription == "openai_api"
    assert loaded.api_base_url == "https://example.test/v1"
    assert loaded.api_key_secret == "OPENAI_TEST"
    assert loaded.provider_options == {"api": "chat", "temperature": "0"}


def test_provider_profile_secret_refs_resolve_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_SECRET_CODEX_TOKEN", "secret-token")
    settings = ResolvedProviderSettings(
        profile_name="secure",
        subscription="codex",
        env={"PLAIN": "1"},
        secret_refs={"CODEX_API_KEY": "CODEX_TOKEN"},
    )

    assert resolved_provider_env(settings) == {
        "PLAIN": "1",
        "CODEX_API_KEY": "secret-token",
    }


@pytest.mark.parametrize("name", ["PRIVATE_KEY", "AWS_ACCESS_KEY_ID", "service-key"])
def test_provider_profile_classifies_service_key_env_names_as_sensitive(
    name: str,
) -> None:
    assert is_sensitive_env_name(name)


def test_ui_provider_profile_payload_masks_sensitive_env(tmp_path: Path) -> None:
    save_provider_profiles(
        {
            "legacy": ProviderProfile(
                name="legacy",
                subscription="codex",
                env={
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "CODEX_API_KEY": "secret-token",
                    "GOFER_TRACE": "1",
                    "PRIVATE_KEY": "private-key",
                },
            )
        },
        tmp_path,
    )

    payload = provider_profiles_payload(tmp_path)

    profile = payload["profiles"][0]
    assert profile["env"] == {
        "AWS_ACCESS_KEY_ID": MASKED_SECRET_VALUE,
        "CODEX_API_KEY": MASKED_SECRET_VALUE,
        "GOFER_TRACE": "1",
        "PRIVATE_KEY": MASKED_SECRET_VALUE,
    }
    assert profile["masked_env"] == ["AWS_ACCESS_KEY_ID", "CODEX_API_KEY", "PRIVATE_KEY"]
    assert "aws-access" not in str(payload)
    assert "private-key" not in str(payload)
    assert "secret-token" not in str(payload)


def test_ui_provider_profile_masked_round_trip_preserves_legacy_env(
    tmp_path: Path,
) -> None:
    save_provider_profiles(
        {
            "legacy": ProviderProfile(
                name="legacy",
                subscription="codex",
                env={"CODEX_API_KEY": "secret-token", "GOFER_TRACE": "1"},
            )
        },
        tmp_path,
    )

    result = upsert_provider_profile_payload(
        {
            "name": "legacy",
            "subscription": "codex",
            "env": {"CODEX_API_KEY": MASKED_SECRET_VALUE, "GOFER_TRACE": "0"},
        },
        tmp_path,
    )
    stored = load_provider_profiles(tmp_path)["legacy"]

    assert result["profile"]["env"]["CODEX_API_KEY"] == MASKED_SECRET_VALUE
    assert stored.env == {"CODEX_API_KEY": "secret-token", "GOFER_TRACE": "0"}


def test_ui_provider_profile_secret_ref_replaces_legacy_plaintext_env(
    tmp_path: Path,
) -> None:
    save_provider_profiles(
        {
            "legacy": ProviderProfile(
                name="legacy",
                subscription="codex",
                env={"CODEX_API_KEY": "secret-token"},
            )
        },
        tmp_path,
    )

    upsert_provider_profile_payload(
        {
            "name": "legacy",
            "subscription": "codex",
            "env": {"CODEX_API_KEY": MASKED_SECRET_VALUE},
            "secret_refs": {"CODEX_API_KEY": "CODEX_TOKEN"},
        },
        tmp_path,
    )
    stored = load_provider_profiles(tmp_path)["legacy"]

    assert stored.env == {}
    assert stored.secret_refs == {"CODEX_API_KEY": "CODEX_TOKEN"}


def test_ui_provider_profile_rejects_sensitive_plaintext_env(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must use secret_refs"):
        provider_profile_from_ui_payload(
            {
                "name": "bad",
                "subscription": "codex",
                "env": {"AWS_ACCESS_KEY_ID": "plaintext"},
            }
        )


def test_ui_provider_profile_accepts_explicit_unsafe_env(tmp_path: Path) -> None:
    profile = provider_profile_from_ui_payload(
        {
            "name": "unsafe",
            "subscription": "codex",
            "unsafe_env": {"API_TOKEN": "plaintext"},
        }
    )

    assert profile.env == {"API_TOKEN": "plaintext"}


def test_direct_provider_api_key_secret_resolves_to_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_SECRET_OPENAI_TEST", "api-key")
    settings = ResolvedProviderSettings(
        profile_name="api",
        subscription="openai_api",
        api_key_secret="OPENAI_TEST",
    )

    assert resolved_provider_env(settings)["GOFER_DIRECT_API_KEY"] == "api-key"


def test_provider_profile_missing_secret_is_reported_in_plan_and_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOFER_SECRET_CODEX_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_TOKEN", raising=False)
    save_provider_profiles(
        {
            "secure": ProviderProfile(
                name="secure",
                subscription="codex",
                secret_refs={"CODEX_API_KEY": "CODEX_TOKEN"},
            )
        },
        tmp_path,
    )
    workflow = AgenticWorkflow(WorkflowConfig(id="profiles", name="Profiles"))
    workflow.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="codex",
            working_dir=tmp_path,
            profile="secure",
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="review",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
            ),
        )
    )

    plan = build_execution_plan(workflow, workflow_path=tmp_path / "workflow.toml")

    assert plan["requiredSecrets"] == ["CODEX_TOKEN"]
    with pytest.raises(ValueError, match="missing secret reference"):
        workflow.validate(tmp_path / "workflow.toml")


def test_direct_provider_missing_api_secret_is_reported_in_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workflow = AgenticWorkflow(WorkflowConfig(id="direct", name="Direct"))
    workflow.register_agent(
        AgentConfig(
            agent_id="writer",
            subscription="openai_api",
            working_dir=tmp_path,
            model="gpt-5-mini",
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="write",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="writer",
                working_dir=tmp_path,
            ),
        )
    )

    plan = build_execution_plan(workflow, workflow_path=tmp_path / "workflow.toml")

    assert plan["requiredSecrets"] == ["OPENAI_API_KEY"]
    assert plan["providerRequirements"][0]["directApi"] is True


def test_codex_profile_command_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ResolvedProviderSettings(
        profile_name="quality",
        subscription="codex",
        model="gpt-5",
        sandbox_mode="read-only",
        extra_args=["--config", "x=y"],
    )

    cmd = CodexSubscription()._build_command(
        "prompt",
        [],
        [],
        [],
        settings,
    )

    assert ["--model", "gpt-5"] == cmd[cmd.index("--model") : cmd.index("--model") + 2]
    assert ["--sandbox", "read-only"] == cmd[cmd.index("--sandbox") : cmd.index("--sandbox") + 2]
    assert "--config" in cmd


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("approval_mode", "on-request", "do not support approval_mode")],
)
def test_codex_profile_rejects_unsupported_settings_before_launch(
    field: str,
    value: str,
    message: str,
) -> None:
    settings = ResolvedProviderSettings(
        profile_name="quality",
        subscription="codex",
        **{field: value},
    )

    with pytest.raises(ValueError, match=message):
        validate_provider_settings(settings)
    with pytest.raises(ValueError, match=message):
        CodexSubscription()._build_command("prompt", [], [], [], settings)


def test_codex_profile_passes_effort_to_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ResolvedProviderSettings(
        profile_name="quality",
        subscription="codex",
        model="gpt-5",
        effort="high",
    )

    validate_provider_settings(settings)
    command = CodexSubscription()._build_command("prompt", [], [], [], settings)

    assert ["-c", 'model_reasoning_effort="high"'] == command[
        command.index("-c") : command.index("-c") + 2
    ]


def test_claude_profile_command_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ResolvedProviderSettings(
        profile_name="review",
        subscription="claude_code",
        model="opus",
        approval_mode="manual",
        tools=["Read"],
        mcp_servers=["docs"],
    )

    cmd = ClaudeCodeSubscription()._build_command(
        "prompt",
        ["Bash"],
        [],
        [],
        settings,
    )

    assert ["--model", "opus"] == cmd[cmd.index("--model") : cmd.index("--model") + 2]
    assert ["--permission-mode", "manual"] == cmd[
        cmd.index("--permission-mode") : cmd.index("--permission-mode") + 2
    ]
    assert cmd.count("--allowedTools") == 2
    assert "--mcp-server" in cmd


def test_workflow_validation_rejects_missing_profile(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="profiles", name="Profiles"))
    workflow.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="codex",
            working_dir=tmp_path,
            profile="missing",
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="review",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
            ),
        )
    )

    with pytest.raises(ValueError, match="Provider profile 'missing' was not found"):
        workflow.validate(tmp_path / "workflow.toml")


def test_workflow_validation_rejects_unsupported_profile_settings(
    tmp_path: Path,
) -> None:
    save_provider_profiles(
        {
            "unsupported": ProviderProfile(
                name="unsupported",
                subscription="codex",
                tools=["Read"],
            )
        },
        tmp_path,
    )
    workflow = AgenticWorkflow(WorkflowConfig(id="profiles", name="Profiles"))
    workflow.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="codex",
            working_dir=tmp_path,
            profile="unsupported",
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="review",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
            ),
        )
    )

    plan = build_execution_plan(workflow, workflow_path=tmp_path / "workflow.toml")

    assert any("do not support default tools" in warning for warning in plan["warnings"])
    with pytest.raises(ValueError, match="do not support default tools"):
        workflow.validate(tmp_path / "workflow.toml")


def test_ui_provider_profile_api_round_trip(tmp_path: Path) -> None:
    created = upsert_provider_profile_payload(
        {"name": "fast", "subscription": "codex", "model": "gpt-5-mini"},
        tmp_path,
    )
    listed = provider_profiles_payload(tmp_path)
    deleted = delete_provider_profile_payload("fast", tmp_path)

    assert created["profile"]["name"] == "fast"
    assert listed["profiles"][0]["model"] == "gpt-5-mini"
    assert deleted == {"profile": "fast", "deleted": True}


def test_ui_provider_profile_api_rejects_invalid_payloads(tmp_path: Path) -> None:
    cases = [
        ({}, "Field required"),
        ({"name": "bad name", "subscription": "codex"}, "Profile name must match"),
        ({"name": "bad-sub", "subscription": "missing"}, "Input should be"),
        ({"name": "bad-env", "subscription": "codex", "env": []}, "env must be an object"),
        (
            {"name": "bad-list", "subscription": "codex", "extra_args": "--verbose"},
            "valid list",
        ),
        (
            {"name": "bad-setting", "subscription": "openai_api", "tools": ["Read"]},
            "Direct API provider profiles do not support tools",
        ),
    ]

    for payload, snippet in cases:
        with pytest.raises(ProviderProfileError, match=snippet):
            upsert_provider_profile_payload(payload, tmp_path)

    assert provider_profiles_payload(tmp_path) == {"profiles": []}


def test_ui_provider_profile_api_upsert_replaces_existing_profile(tmp_path: Path) -> None:
    upsert_provider_profile_payload(
        {
            "name": "fast",
            "subscription": "claude_code",
            "model": "old",
            "tools": ["Read", "Write"],
            "env": {"TRACE": "1"},
            "secret_refs": {"ANTHROPIC_API_KEY": "ANTHROPIC_TOKEN"},
        },
        tmp_path,
    )

    result = upsert_provider_profile_payload(
        {
            "name": "fast",
            "subscription": "claude_code",
            "model": "new",
            "tools": ["Read"],
            "secret_refs": {},
        },
        tmp_path,
    )

    stored = load_provider_profiles(tmp_path)["fast"]

    assert result["profile"]["model"] == "new"
    assert stored.model == "new"
    assert stored.tools == ["Read"]
    assert stored.env == {}
    assert stored.secret_refs == {}


def test_ui_provider_profile_api_persists_secret_ref_names_without_plaintext(
    tmp_path: Path,
) -> None:
    upsert_provider_profile_payload(
        {
            "name": "secure",
            "subscription": "codex",
            "env": {"CODEX_API_KEY": MASKED_SECRET_VALUE, "TRACE": "1"},
            "secret_refs": {"CODEX_API_KEY": "CODEX_TOKEN_SECRET"},
        },
        tmp_path,
    )

    payload = provider_profiles_payload(tmp_path)["profiles"][0]
    stored_text = (tmp_path / "provider-profiles.json").read_text(encoding="utf-8")

    assert payload["env"] == {"TRACE": "1"}
    assert payload["secret_refs"] == {"CODEX_API_KEY": "CODEX_TOKEN_SECRET"}
    assert "plaintext-token" not in stored_text
    assert MASKED_SECRET_VALUE not in stored_text
    assert "CODEX_TOKEN_SECRET" in stored_text


def test_ui_provider_profile_api_delete_rejects_invalid_and_missing_names(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProviderProfileError, match="Profile name must match"):
        delete_provider_profile_payload("bad name", tmp_path)

    with pytest.raises(ProviderProfileError, match="not found"):
        delete_provider_profile_payload("missing", tmp_path)


class FakeHttpClient:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.response


@pytest.mark.anyio
async def test_openai_direct_provider_uses_fake_client_and_exact_usage() -> None:
    client = FakeHttpClient(
        HttpResponse(
            status=200,
            headers={},
            body=b'{"output_text":"done","model":"gpt-5-mini","usage":{"input_tokens":7,"output_tokens":3}}',
        )
    )
    settings = ResolvedProviderSettings(
        profile_name="api",
        subscription="openai_api",
        model="gpt-5-mini",
        api_base_url="https://example.test/v1",
    )

    result = await OpenAiApiSubscription(client).execute(
        prompt="hello",
        working_dir=Path.cwd(),
        tools=[],
        mcp_servers=[],
        env={"GOFER_DIRECT_API_KEY": "key"},
        provider_settings=settings,
    )

    assert result.success is True
    assert result.output == "done"
    assert result.usage_metadata["input_tokens"] == 7
    assert result.usage_metadata["output_tokens"] == 3
    assert result.usage_metadata["total_tokens"] == 10
    assert client.requests[0].url == "https://example.test/v1/responses"


@pytest.mark.anyio
async def test_anthropic_direct_provider_normalizes_rate_limit_error() -> None:
    client = FakeHttpClient(
        HttpResponse(
            status=429,
            headers={},
            body=b'{"error":{"type":"rate_limit_error","message":"slow down"}}',
        )
    )
    settings = ResolvedProviderSettings(
        profile_name="api",
        subscription="anthropic_api",
        model="claude-test",
    )

    result = await AnthropicApiSubscription(client).execute(
        prompt="hello",
        working_dir=Path.cwd(),
        tools=[],
        mcp_servers=[],
        env={"GOFER_DIRECT_API_KEY": "key"},
        provider_settings=settings,
    )

    assert result.success is False
    assert "rate limit" in result.output.lower()


@pytest.mark.parametrize("provider", ["grok"])
def test_acp_provider_profile_round_trip_preserves_custom_model(provider, tmp_path):
    profile = ProviderProfile.model_validate(
        {"name": "custom", "subscription": provider, "model": "vendor/custom-model", "timeout": 45}
    )
    save_provider_profiles({"custom": profile}, tmp_path)
    restored = load_provider_profiles(tmp_path)["custom"]
    assert restored.subscription == provider
    assert restored.model == "vendor/custom-model"
    assert restored.timeout == 45
    validate_provider_settings(
        ResolvedProviderSettings(subscription=provider, model=restored.model, timeout=45)
    )


@pytest.mark.parametrize("provider", ["grok"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effort", "high"),
        ("extra_args", ["--unrestricted"]),
        ("tools", ["shell"]),
        ("mcp_servers", ["ambient"]),
        ("provider_options", {"unsafe": True}),
        ("approval_mode", "auto"),
        ("sandbox_mode", "danger-full-access"),
    ],
)
def test_acp_profiles_reject_unsupported_options(provider, field, value):
    settings = ResolvedProviderSettings.model_validate(
        {"subscription": provider, field: value}
    )
    if provider == "grok" and field == "effort":
        validate_provider_settings(settings)
    else:
        with pytest.raises(ValueError, match=field):
            validate_provider_settings(settings)
