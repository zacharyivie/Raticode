from pathlib import Path

import pytest

from gofer.core import provider_capabilities as capabilities
from gofer.core import provider_preferences as preferences
from gofer.subscriptions.codex import CodexSubscription
from gofer.ui import chat


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preferences, "get_data_dir", lambda: tmp_path)


def test_override_is_persisted_and_used_by_discovery_and_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "chosen codex"
    binary.write_text("test executable")
    binary.chmod(0o755)
    monkeypatch.setattr(capabilities.shutil, "which", lambda _: "/wrong/codex")
    preferences.save_provider_preference("codex", {"executable": str(binary), "enabled": False})
    assert capabilities.resolve_provider_executable("codex") == str(binary)
    assert CodexSubscription()._build_command("hello", [], [])[0] == str(binary)
    assert preferences.provider_preference("codex")["enabled"] is False
    preferences.save_provider_preference("copilot", {"enabled": True})
    assert preferences.provider_preference("codex")["executable"] == str(binary)
    binary.unlink()
    assert capabilities.resolve_provider_executable("codex") is None
    preferences.save_provider_preference("codex", {"executable": ""})
    assert capabilities.resolve_provider_executable("codex") == "/wrong/codex"


@pytest.mark.parametrize(
    "changes", [{"enabled": "false"}, {"executable": "/missing"}, {"other": True}]
)
def test_invalid_preferences_do_not_overwrite_saved_settings(changes: dict[str, object]) -> None:
    preferences.save_provider_preference("codex", {"enabled": False})
    with pytest.raises(ValueError):
        preferences.save_provider_preference("codex", changes)
    assert preferences.provider_preference("codex") == {"enabled": False}


def test_defaults_follow_executable_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = capabilities.ProviderCapability(
        id="codex", display_name="Codex", available=True, discovery_status="unauthenticated"
    )
    monkeypatch.setattr(capabilities, "resolve_provider_executable", lambda _: "/bin/codex")
    assert capability.to_ui_payload()["enabled"] is True
    monkeypatch.setattr(capabilities, "resolve_provider_executable", lambda _: None)
    assert capability.to_ui_payload()["enabled"] is False
    preferences.save_provider_preference("codex", {"enabled": True})
    assert capability.to_ui_payload()["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", capabilities.CLI_PROVIDERS)
async def test_disabled_provider_cannot_start_rem_turn(tmp_path: Path, provider: str) -> None:
    preferences.save_provider_preference(provider, {"enabled": False})
    with pytest.raises(chat.ChatProviderError, match="disabled"):
        await chat.run_workflow_chat(
            provider=provider,
            model="cli-default",
            messages=[{"role": "user", "body": "hello"}],
            workflow=None,
            working_dir=tmp_path,
            data_dir=tmp_path,
        )


@pytest.mark.parametrize("provider", capabilities.CLI_PROVIDERS)
def test_model_and_effort_defaults_override_discovery_without_mutating_cache(provider) -> None:
    capability = capabilities.ProviderCapability(
        id=provider,
        display_name=provider,
        available=True,
        discovery_status="ready",
        default_model="sol",
        models=[
            capabilities.ModelCapability(id="sol", display_name="Sol", is_default=True),
            capabilities.ModelCapability(
                id="astra",
                display_name="Astra",
                default_effort="medium",
                efforts=[
                    capabilities.EffortCapability(id=level, display_name=level)
                    for level in ("medium", "high")
                ],
            ),
        ],
    )
    preferences.save_provider_preference(
        provider, {"defaultModel": "astra", "defaultEffort": "high"}
    )
    payload = capability.to_ui_payload()
    assert payload["defaultModel"] == "astra"
    assert payload["providerDefaultModel"] == "sol"
    assert payload["models"][1]["defaultEffort"] == "high"
    assert payload["models"][1]["providerDefaultEffort"] == "medium"
    assert [model["id"] for model in payload["models"] if model["isDefault"]] == ["astra"]
    assert capability.default_model == "sol"
    assert capability.models[1].default_effort == "medium"
    preferences.save_provider_preference(provider, {"defaultEffort": "unavailable"})
    assert capability.to_ui_payload()["models"][1]["defaultEffort"] == "medium"
    preferences.save_provider_preference(provider, {"defaultModel": "removed-model"})
    assert capability.to_ui_payload()["defaultModel"] == "sol"
    assert preferences.provider_preference(provider)["defaultModel"] == "removed-model"
    preferences.save_provider_preference(provider, {"defaultModel": "", "defaultEffort": ""})
    assert capability.to_ui_payload()["defaultModel"] == "sol"
    assert capability.to_ui_payload()["models"][1]["defaultEffort"] == "medium"


@pytest.mark.parametrize(
    "changes", [{"defaultModel": None}, {"defaultEffort": 3}, {"defaultModel": "a" * 257}]
)
def test_invalid_default_preferences_are_rejected(changes) -> None:
    with pytest.raises(ValueError):
        preferences.save_provider_preference("codex", changes)
    assert preferences.provider_preference("codex") == {}
