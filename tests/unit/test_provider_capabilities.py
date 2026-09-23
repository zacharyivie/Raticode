from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import cast

import pytest

from gofer.core import provider_capabilities
from gofer.core.provider_capabilities import (
    ClaudeCodeCapabilityProbe,
    CodexCapabilityProbe,
    ProviderCapability,
    ProviderCapabilityError,
    ProviderCapabilityService,
    provider_capabilities_payload_async,
    resolve_provider_executable,
    validate_provider_selection,
)


@pytest.mark.asyncio
async def test_codex_probe_uses_cli_catalog_and_per_model_efforts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = {
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6-Sol",
                "visibility": "list",
                "priority": 1,
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Fast"},
                    {"effort": "medium", "description": "Balanced"},
                    {"effort": "ultra", "description": "Delegated"},
                ],
            },
            {
                "slug": "hidden-model",
                "visibility": "hidden",
                "priority": 0,
                "supported_reasoning_levels": [{"effort": "high"}],
            },
        ]
    }

    async def fake_run(command: list[str], **_kwargs: object) -> tuple[int, str, str]:
        if command[-1] == "--version":
            return 0, "codex-cli 0.145.0\n", ""
        return 0, json.dumps(catalog), ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", fake_run)

    capability = await CodexCapabilityProbe().discover("/tmp/codex")

    assert capability.discovery_status == "ready"
    assert capability.default_model == "gpt-5.6-sol"
    assert [model.id for model in capability.models] == ["gpt-5.6-sol"]
    assert [effort.id for effort in capability.models[0].efforts] == ["low", "medium", "ultra"]
    assert capability.models[0].default_effort == "medium"


@pytest.mark.asyncio
async def test_claude_probe_uses_slash_catalog_and_supported_efforts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    model_response = "\n".join(
        [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "model": "claude-sonnet-5",
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "result": (
                        "Current model: Sonnet 5 (default) (effort: high)\n"
                        "Usage: /model <name>. Available: sonnet, opus, haiku, or a full model ID."
                    ),
                }
            ),
        ]
    )
    effort_response = json.dumps(
        {"type": "result", "result": "Usage: /effort <low|medium|high|xhigh|max|auto>"}
    )

    async def fake_run(command: list[str], **_kwargs: object) -> tuple[int, str, str]:
        commands.append(command)
        if command[-1] == "--version":
            return 0, "2.1.0\n", ""
        if command[-1] == "/model":
            return 0, model_response, ""
        return 0, effort_response, ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", fake_run)

    capability = await ClaudeCodeCapabilityProbe().discover("/tmp/claude")

    assert [command[-1] for command in commands] == ["--version", "/model", "/effort"]
    assert capability.default_model == "claude-sonnet-5"
    assert [model.id for model in capability.models] == [
        "claude-sonnet-5",
        "sonnet",
        "opus",
        "haiku",
    ]
    assert [effort.id for effort in capability.models[0].efforts] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "auto",
    ]


def test_capability_service_caches_probe_result(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProviderCapabilityService()
    calls = 0

    async def discover(_executable: str | None = None) -> ProviderCapability:
        nonlocal calls
        calls += 1
        return ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
        )

    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: "/tmp/codex")
    monkeypatch.setattr(service._probes["codex"], "discover", discover)

    assert service.provider("codex").discovery_status == "ready"
    assert service.provider("codex").discovery_status == "ready"
    assert calls == 1


@pytest.mark.asyncio
async def test_capability_service_discovers_inside_running_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderCapabilityService()

    async def discover(_executable: str | None = None) -> ProviderCapability:
        return ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
        )

    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: "/tmp/codex")
    monkeypatch.setattr(service._probes["codex"], "discover", discover)

    capability = await service.provider_async("codex")

    assert capability.discovery_status == "ready"


@pytest.mark.asyncio
async def test_concurrent_async_discovery_shares_one_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderCapabilityService()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def discover(_executable: str | None = None) -> ProviderCapability:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
        )

    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: "/tmp/codex")
    monkeypatch.setattr(service._probes["codex"], "discover", discover)

    tasks = [asyncio.create_task(service.provider_async("codex")) for _ in range(8)]
    await started.wait()
    await asyncio.sleep(0)
    release.set()
    capabilities = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(capability is capabilities[0] for capability in capabilities)


@pytest.mark.asyncio
async def test_sync_and_async_discovery_share_one_inflight_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderCapabilityService()
    started = threading.Event()
    release = threading.Event()
    calls = 0
    sync_results: list[ProviderCapability] = []

    async def discover(_executable: str | None = None) -> ProviderCapability:
        nonlocal calls
        calls += 1
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
        )

    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: "/tmp/codex")
    monkeypatch.setattr(service._probes["codex"], "discover", discover)

    sync_thread = threading.Thread(
        target=lambda: sync_results.append(service.provider("codex")),
    )
    sync_thread.start()
    while not started.is_set():
        await asyncio.sleep(0.01)
    async_task = asyncio.create_task(service.provider_async("codex"))
    await asyncio.sleep(0)
    release.set()
    async_capability = await async_task
    sync_thread.join(timeout=2)

    assert calls == 1
    assert not sync_thread.is_alive()
    assert sync_results == [async_capability]


@pytest.mark.asyncio
async def test_sync_capability_apis_fail_fast_inside_running_loop() -> None:
    service = ProviderCapabilityService()

    with pytest.raises(RuntimeError, match=r"await provider_async\(\) instead"):
        service.provider("codex")
    with pytest.raises(RuntimeError, match=r"await payload_async\(\) instead"):
        service.payload()
    with pytest.raises(
        RuntimeError,
        match=r"await validate_provider_selection_async\(\) instead",
    ):
        validate_provider_selection("codex", None, None, service=service)


@pytest.mark.asyncio
async def test_async_provider_payload_uses_async_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AsyncService:
        async def payload_async(
            self,
            *,
            refresh: bool = False,
        ) -> dict[str, list[dict[str, object]]]:
            return {"providers": [{"id": "codex", "refresh": refresh}]}

    service = cast(ProviderCapabilityService, AsyncService())
    monkeypatch.setattr(provider_capabilities, "provider_capability_service", lambda: service)

    assert await provider_capabilities_payload_async(refresh=True) == {
        "providers": [{"id": "codex", "refresh": True}]
    }


def test_resolve_claude_executable_uses_nvm_default_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    executable = tmp_path / "versions" / "node" / "v20.20.0" / "bin" / "claude"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    default = tmp_path / "alias" / "default"
    default.parent.mkdir()
    default.write_text("v20.20.0\n", encoding="utf-8")
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: None)

    assert resolve_provider_executable("claude_code") == str(executable)


def test_resolve_codex_executable_uses_nvm_default_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    executable = tmp_path / "versions" / "node" / "v20.20.0" / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    default = tmp_path / "alias" / "default"
    default.parent.mkdir()
    default.write_text("v20.20.0\n", encoding="utf-8")
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    monkeypatch.setattr(provider_capabilities.shutil, "which", lambda _name: None)

    assert resolve_provider_executable("codex") == str(executable)


@pytest.mark.asyncio
async def test_probe_puts_nvm_executable_directory_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The npm-global CLI shebang needs the ``node`` sitting beside it."""
    executable = "/home/user/.nvm/versions/node/v20.20.0/bin/codex"
    seen_env: dict[str, str] = {}

    async def fake_run_subprocess(
        command: list[str],
        **kwargs: object,
    ) -> tuple[int, str, str]:
        seen_env.update(cast(dict[str, str], kwargs["env"]))
        return 0, "", ""

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(provider_capabilities, "run_subprocess", fake_run_subprocess)

    await provider_capabilities._run_probe([executable, "debug", "models"])

    assert seen_env["PATH"].split(os.pathsep)[0] == ("/home/user/.nvm/versions/node/v20.20.0/bin")


def test_selection_validation_rejects_model_effort_not_in_host_catalog() -> None:
    capability = ProviderCapability.model_validate(
        {
            "id": "codex",
            "display_name": "Codex",
            "available": True,
            "discovery_status": "ready",
            "default_model": "gpt-5.6-sol",
            "models": [
                {
                    "id": "gpt-5.6-sol",
                    "display_name": "GPT-5.6-Sol",
                    "efforts": [{"id": "high", "display_name": "High"}],
                }
            ],
        }
    )

    class StaticService:
        def provider(self, _provider_id: str) -> ProviderCapability:
            return capability

    service = cast(ProviderCapabilityService, StaticService())
    validate_provider_selection("codex", "gpt-5.6-sol", "high", service=service)
    with pytest.raises(ProviderCapabilityError, match="Effort 'low'"):
        validate_provider_selection("codex", "gpt-5.6-sol", "low", service=service)
    with pytest.raises(ProviderCapabilityError, match="Model 'unknown'"):
        validate_provider_selection("codex", "unknown", None, service=service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "label", "commands"),
    [
        ("grok", "xAI Grok", [["/fake/grok", "version"], ["/fake/grok", "models"]]),
    ],
)
async def test_acp_provider_catalog_preserves_custom_models_without_guessing(
    monkeypatch, provider, label, commands
):
    calls = []

    async def run(command):
        calls.append(command)
        return 0, "Human readable output without a stable model schema", ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", run)
    result = await provider_capabilities.AdditionalCliCapabilityProbe(provider).discover(
        f"/fake/{provider}"
    )
    assert calls == commands
    assert result.available
    assert result.discovery_status == "invalid_response"
    assert result.models == []
    payload = result.to_ui_payload()
    assert payload["id"] == provider
    assert payload["displayName"] == label
    assert payload["supportsCustomModel"]
    assert payload["permissionModes"] == [
        {"id": "default", "displayName": "Strict resources, unavailable"},
        {"id": "cli-managed", "displayName": "CLI-managed permissions"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["grok"])
async def test_acp_provider_missing_binary_retains_custom_model_ui(monkeypatch, provider):
    monkeypatch.setattr(provider_capabilities, "resolve_provider_executable", lambda _: None)
    result = await provider_capabilities.AdditionalCliCapabilityProbe(provider).discover()
    assert not result.available
    assert result.discovery_status == "missing"
    assert result.to_ui_payload()["supportsCustomModel"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "stderr", "status"),
    [
        (1, "Authentication required: sign in", "unauthenticated"),
        (1, "unknown command models", "unsupported_cli_version"),
        (1, "unexpected catalog failure", "invalid_response"),
    ],
)
async def test_grok_catalog_failure_does_not_claim_ready(monkeypatch, code, stderr, status):
    async def run(command):
        if command[-1] == "version":
            return 0, "Grok Build test version", ""
        return code, "", stderr

    monkeypatch.setattr(provider_capabilities, "_run_probe", run)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("grok").discover("/fake/grok")
    assert result.discovery_status == status
    assert result.models == []
    assert result.to_ui_payload()["supportsCustomModel"]


@pytest.mark.asyncio
async def test_cursor_catalog_groups_variants_and_publishes_dropdown_payload(monkeypatch):
    listing = """Available models

\x1b[32mauto - Auto (current, default)\x1b[0m
cursor-grok-4.5-low - Cursor Grok 4.5 Low
cursor-grok-4.5-low-fast - Cursor Grok 4.5 Low Fast
cursor-grok-4.5-medium - Cursor Grok 4.5 Medium
cursor-grok-4.5-medium-fast - Cursor Grok 4.5 Medium Fast
cursor-grok-4.5-low - Cursor Grok 4.5 Low

gpt-5.3-codex-low - Codex 5.3 Low
gpt-5.3-codex - Codex 5.3
gpt-5.3-codex-fast - Codex 5.3 Fast
Tip: use --model <id> to switch.
not a model - prose
broken -
"""
    commands = []

    async def fake_run(command, **kwargs):
        commands.append(command)
        return (0, "2026.09.15", "") if command[-1] == "--version" else (0, listing, "")

    monkeypatch.setattr(provider_capabilities, "_run_probe", fake_run)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("cursor").discover(
        "/fake/cursor-agent"
    )
    assert commands[-1] == ["/fake/cursor-agent", "models"]
    assert result.discovery_status == "ready"
    assert result.default_model == "auto"
    assert result.error is None
    assert [model.id for model in result.models] == ["auto", "cursor-grok-4.5", "gpt-5.3-codex"]
    auto, grok, codex = result.models
    assert auto.display_name == "Auto" and auto.is_default and not auto.efforts
    provider_capabilities._validate_capability_selection(result, "cli-default", "cli-default")
    provider_capabilities._validate_capability_selection(result, "auto", "cli-default")
    assert grok.display_name == "Cursor Grok 4.5"
    assert grok.default_effort == "low"
    assert [effort.id for effort in grok.efforts] == ["low", "low-fast", "medium", "medium-fast"]
    assert codex.default_effort == "cli-default"
    assert [effort.id for effort in codex.efforts] == ["low", "cli-default", "fast"]
    payload = result.to_ui_payload()["models"][1]
    assert payload["efforts"][1]["displayName"] == "Low Fast"
    provider_capabilities._validate_capability_selection(result, grok.id, "medium-fast")
    with pytest.raises(ProviderCapabilityError, match="not supported"):
        provider_capabilities._validate_capability_selection(result, grok.id, "max")


@pytest.mark.parametrize(
    ("native_id", "label", "base", "effort", "display"),
    [
        (
            "gpt-5.5-extra-high-fast",
            "GPT-5.5 Extra High Fast",
            "gpt-5.5",
            "extra-high-fast",
            "GPT-5.5",
        ),
        ("gpt-5.5-xhigh", "GPT-5.5 Extra High", "gpt-5.5", "xhigh", "GPT-5.5"),
        (
            "claude-opus-thinking-high",
            "Claude Opus Thinking",
            "claude-opus-thinking",
            "high",
            "Claude Opus Thinking",
        ),
        (
            "claude-opus-high-thinking",
            "Claude Opus Thinking",
            "claude-opus",
            "high-thinking",
            "Claude Opus",
        ),
        ("muse-minimal", "Muse Minimal", "muse", "minimal", "Muse"),
        ("gpt-none", "GPT None", "gpt", "none", "GPT"),
        ("model-lowlatency", "Lowlatency", "model-lowlatency", None, "Lowlatency"),
        ("model-high-quality", "High quality", "model-high-quality", None, "High quality"),
    ],
)
def test_cursor_catalog_suffixes_round_trip(native_id, label, base, effort, display):
    from gofer.core.cursor_models import cursor_model_id

    (model,) = provider_capabilities._cursor_models_from_listing(f"{native_id} - {label}")
    assert model.id == base
    assert model.display_name == display
    assert model.default_effort == effort
    assert cursor_model_id(model.id, model.default_effort) == native_id


def test_cursor_default_markers_preserve_descriptions_and_override_first_variant():
    models = provider_capabilities._cursor_models_from_listing(
        "model-low - Model Low (NO ZDR)\n"
        "model-high-fast - Model High Fast (NO ZDR) (default)\n"
        "model - Model (NO ZDR) (current)\n"
    )
    assert len(models) == 1
    assert models[0].is_default
    assert models[0].default_effort == "high-fast"
    assert models[0].display_name == "Model (NO ZDR)"
    assert models[0].efforts[0].description == "Model Low (NO ZDR)"


@pytest.mark.asyncio
async def test_cursor_empty_catalog_keeps_custom_model_fallback(monkeypatch):
    async def fake_run(command, **kwargs):
        return 0, "Available models\nTip: use --model <id> to switch.", ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", fake_run)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("cursor").discover(
        "/fake/cursor"
    )
    assert result.discovery_status == "unsupported_cli_version"
    assert not result.models
    assert result.supports_custom_model


@pytest.mark.asyncio
async def test_copilot_catalog_populates_picker_and_efforts(monkeypatch):
    async def version(command):
        return 0, "1.0.85", ""

    async def catalog(executable):
        assert executable == "/fake/copilot"
        return {
            "models": [
                {
                    "id": "gpt-test",
                    "name": "GPT Test",
                    "supportedReasoningEfforts": ["low", "high", "high", None],
                    "defaultReasoningEffort": "high",
                },
                {"id": "gpt-test", "name": "Duplicate"},
                {"id": "claude-test", "name": "Claude Test"},
                {"id": "blocked", "policy": {"state": "disabled"}},
                {"name": "No ID"},
                None,
            ]
        }

    monkeypatch.setattr(provider_capabilities, "_run_probe", version)
    monkeypatch.setattr(provider_capabilities, "_copilot_model_catalog", catalog)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("copilot").discover(
        "/fake/copilot"
    )
    assert result.discovery_status == "ready"
    assert result.error is None
    payload = result.to_ui_payload()
    assert [m["id"] for m in payload["models"]] == ["gpt-test", "claude-test"]
    assert payload["models"][0]["displayName"] == "GPT Test"
    assert payload["models"][0]["defaultEffort"] == "high"
    assert [e["id"] for e in payload["models"][0]["efforts"]] == ["low", "high"]
    assert payload["models"][1]["efforts"] == []
    assert payload["defaultModel"] is None  # Don't invent an account default.


@pytest.mark.parametrize("payload", [{}, {"models": []}, {"models": "bad"}, {"models": [None]}])
def test_copilot_invalid_catalog(payload):
    with pytest.raises(provider_capabilities._InvalidCatalogError):
        provider_capabilities._copilot_models_from_catalog(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message", "status"),
    [
        (-32603, "unauthorized: not authorized to use this Copilot feature", "access_denied"),
        (-32603, "Authentication required", "unauthenticated"),
        (-32603, "No authentication information found", "unauthenticated"),
        (-32601, "Method not found", "unsupported_cli_version"),
        (-32603, "server unavailable", "invalid_response"),
    ],
)
async def test_copilot_rpc_errors(monkeypatch, code, message, status):
    from contextlib import asynccontextmanager

    from gofer.subscriptions import acp_transport

    class Rpc:
        async def request(self, method, params, **kwargs):
            assert method == "models.list"
            assert params == {}
            raise acp_transport.AcpRpcError(code, message)

    @asynccontextmanager
    async def transport(command, **kwargs):
        assert "--headless" in command and "--stdio" in command
        assert command[command.index("--add-dir") + 1] == str(kwargs["cwd"])
        assert "--allow-all-tools" not in command
        assert kwargs["content_length_framing"]
        yield Rpc()

    async def version(command):
        return 0, "1.0.85", ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", version)
    monkeypatch.setattr(acp_transport, "open_acp_transport", transport)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("copilot").discover(
        "/fake/copilot"
    )
    assert result.discovery_status == status
    assert result.models == []
    assert result.to_ui_payload()["supportsBrowserLogin"]
    if "not authorized to use" in message:
        assert "GitHub denied access" in (result.error or "")
        assert "complete its directory trust and account setup" in (result.error or "")
        assert result.to_ui_payload()["discoveryStatus"] == "access_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "stderr", "status"),
    [
        ("ACP models.list timed out; delivery is uncertain", "", "timeout"),
        ("ACP turn exceeded its time limit", "", "timeout"),
        ("ACP process closed", "unknown option --headless", "unsupported_cli_version"),
        ("ACP emitted malformed JSON", "", "invalid_response"),
    ],
)
async def test_copilot_transport_failure_status(monkeypatch, message, stderr, status):
    from contextlib import asynccontextmanager

    from gofer.subscriptions import acp_transport

    class Rpc:
        async def request(self, *args, **kwargs):
            raise acp_transport.AcpTransportError(message)

    rpc = Rpc()
    rpc.stderr = stderr

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield rpc

    async def version(command):
        return 0, "1.0.85", ""

    monkeypatch.setattr(provider_capabilities, "_run_probe", version)
    monkeypatch.setattr(acp_transport, "open_acp_transport", transport)
    result = await provider_capabilities.AdditionalCliCapabilityProbe("copilot").discover(
        "/fake/copilot"
    )
    assert result.discovery_status == status


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"models": []},
        {"models": {"availableModels": []}},
        {"models": {"availableModels": [None, {}, {"modelId": " "}]}},
    ],
)
def test_acp_rejects_empty_or_malformed_catalog(payload):
    with pytest.raises(provider_capabilities._InvalidCatalogError):
        provider_capabilities._acp_models_from_catalog(payload)


def test_acp_catalog_preserves_native_ids_and_current_selection():
    models = provider_capabilities._acp_models_from_catalog(
        {
            "models": {
                "currentModelId": "auto",
                "availableModels": [
                    {"modelId": "auto", "name": "Auto"},
                    {"modelId": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro"},
                    {"modelId": "gemini-3.1-pro-preview", "name": "Duplicate"},
                    None,
                    {"modelId": "new-model"},
                ],
            }
        }
    )
    assert [m.id for m in models] == ["auto", "gemini-3.1-pro-preview", "new-model"]
    assert [m.display_name for m in models] == ["Auto", "Gemini 3.1 Pro", "new-model"]
    assert [m.is_default for m in models] == [True, False, False]
    assert all(not m.efforts for m in models)


@pytest.mark.parametrize("sync", [False, True])
async def test_catalog_starts_all_probes_before_waiting(monkeypatch, sync):
    service = ProviderCapabilityService()
    providers = list(service._probes)
    started = set()
    calls = []

    def discovery(provider: provider_capabilities.ProviderId):
        async def discover(_executable=None):
            started.add(provider)
            calls.append(provider)

            # Sequential discovery cannot pass this barrier.
            async def wait_for_every_provider() -> None:
                while len(started) < len(providers):
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_every_provider(), 2)
            return ProviderCapability(
                id=provider, display_name=provider, available=True, discovery_status="ready"
            )

        return discover

    monkeypatch.setattr(provider_capabilities, "resolve_provider_executable", lambda _: None)
    for provider in providers:
        monkeypatch.setattr(service._probes[provider], "discover", discovery(provider))
    payload = await asyncio.to_thread(service.payload) if sync else await service.payload_async()
    assert [item["id"] for item in payload["providers"]] == providers
    assert len(calls) == len(providers)
    assert (await service.payload_async())["providers"] == payload["providers"]
    assert len(calls) == len(providers)


def test_grok_ui_defaults_to_cli_managed_permissions() -> None:
    capability = ProviderCapability(
        id="grok", display_name="xAI Grok", available=True, discovery_status="ready"
    )
    assert capability.to_ui_payload()["defaultPermissionMode"] == "cli-managed"


async def test_settings_snapshot_returns_preferences_and_stale_catalog_without_probing(monkeypatch):
    service = ProviderCapabilityService()
    preferences = {"enabled": False, "executable": "/chosen/codex"}
    executable = "/chosen/codex"
    monkeypatch.setattr(provider_capabilities, "provider_preference", lambda _: preferences)
    monkeypatch.setattr(
        provider_capabilities, "resolve_provider_executable", lambda _, **kwargs: executable
    )
    monkeypatch.setattr(provider_capabilities, "_executable_mtime_ns", lambda _: 1)
    calls = []

    async def discover(_executable):
        calls.append(_executable)
        return ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
            models=[provider_capabilities.ModelCapability(id="model", display_name="Model")],
        )

    monkeypatch.setattr(service._probes["codex"], "discover", discover)
    cold = service.snapshot_payload()["providers"]
    assert len(cold) == 7
    assert cold[0]["enabled"] is False
    assert cold[0]["executableOverride"] == "/chosen/codex"
    assert cold[0]["discoveryStatus"] == "pending"
    assert cold[0]["discoveredAt"] is None
    assert calls == []

    await service.provider_async("codex")
    for entry in service._cache.values():
        entry.expires_at = 0
    preferences["enabled"] = True
    warm = service.snapshot_payload()["providers"][0]
    assert warm["enabled"] is True
    assert warm["models"][0]["id"] == "model"
    assert warm["discoveryStatus"] == "ready"
    assert calls == ["/chosen/codex"]

    # Catalogs from another executable must not be offered as current defaults.
    executable = "/new/codex"
    changed = service.snapshot_payload()["providers"][0]
    assert changed["models"] == []
    assert changed["discoveryStatus"] == "pending"


async def test_settings_snapshot_exposes_finished_provider_while_another_is_pending(monkeypatch):
    service = ProviderCapabilityService()
    monkeypatch.setattr(
        provider_capabilities, "resolve_provider_executable", lambda _, **kwargs: None
    )
    monkeypatch.setattr(provider_capabilities, "provider_preference", lambda _: {})
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow(_executable):
        started.set()
        await finish.wait()
        return ProviderCapability(
            id="claude_code", display_name="Claude Code", available=True, discovery_status="ready"
        )

    monkeypatch.setattr(service._probes["claude_code"], "discover", slow)
    task = asyncio.create_task(service.provider_async("claude_code"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await service.provider_async("codex")
        snapshot = service.snapshot_payload()["providers"]
        assert snapshot[0]["discoveryStatus"] == "missing"
        assert snapshot[1]["discoveryStatus"] == "pending"
        assert not task.done()
    finally:
        finish.set()
        await task


async def test_settings_snapshot_never_probes_cursor_alias_and_reuses_verified_alias(
    monkeypatch, tmp_path
):
    service = ProviderCapabilityService()
    alias = str(tmp_path / "agent")
    monkeypatch.setattr(provider_capabilities, "provider_preference", lambda _: {})
    monkeypatch.setattr(
        provider_capabilities.shutil, "which", lambda name: alias if name == "agent" else None
    )
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "nvm"))
    checks = []

    def check_alias(executable):
        checks.append(executable)
        return True

    async def discover(executable):
        assert executable == alias
        return ProviderCapability(
            id="cursor", display_name="Cursor", available=True, discovery_status="ready"
        )

    monkeypatch.setattr(provider_capabilities, "_is_cursor_agent", check_alias)
    monkeypatch.setattr(service._probes["cursor"], "discover", discover)
    assert service.snapshot_payload()["providers"][2]["discoveryStatus"] == "pending"
    assert checks == []
    await service.provider_async("cursor")
    assert checks == [alias]
    cached = service.snapshot_payload()["providers"][2]
    assert cached["discoveryStatus"] == "ready"
    assert cached["executable"] == alias
    assert checks == [alias]
