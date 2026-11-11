"""Claude Code's model list, built from output captured from the real CLI (2.1.319)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_capabilities as capabilities
from gofer.core import provider_preferences as preferences
from gofer.core.provider_capabilities import (
    ClaudeCodeCapabilityProbe,
    ProviderCapability,
    ProviderCapabilityError,
    _claude_efforts_from_slash_command,
    _validate_capability_selection,
)
from gofer.ui import commit_message, server, voice_tidy

CAPTURE = (
    Path(__file__).parents[1] / "fixtures" / "claude_code" / "initialize_2_1_319.json"
).read_text()
MODEL_TEXT = (
    "Current model: `Opus 5.5 (default)`\n"
    "Usage: /model <name>. Available: sonnet, opus, haiku, fable, best, sonnet[1m], opus[1m], "
    "fable[1m], opusplan, default, or a full model ID."
)
SLASH_MODEL = "\n".join(
    [
        json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5-5"}),
        json.dumps({"type": "result", "result": MODEL_TEXT}),
    ]
)
SLASH_EFFORT = json.dumps(
    {
        "type": "result",
        "result": "Usage: /effort <low|medium|high|xhigh|max|auto|ultracode [on|off]>",
    }
)
FULL_IDS = [
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]
# The real ~/.local/state/taskurotta-dev/data/gofer/providers.json when the user reported this.
REPORTED_SETTINGS = {
    "deniedModels": ["default", "opusplan", "opus[1m]", "haiku", "opus", "best", "sonnet"],
    "defaultModel": "claude-opus-5-5",
    "defaultEffort": "high",
}


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preferences, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        capabilities, "resolve_provider_executable", lambda *_a, **_k: "/fake/claude"
    )
    monkeypatch.setattr(capabilities, "_service", None)


def fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    *,
    catalog: Any = (0, CAPTURE.replace("\n", " "), ""),
    model: Any = (0, SLASH_MODEL, ""),
    effort: Any = (0, SLASH_EFFORT, ""),
) -> list[tuple[list[str], bytes | None]]:
    calls: list[tuple[list[str], bytes | None]] = []

    async def run(command: list[str], *, stdin: bytes | None = None) -> tuple[int, str, str]:
        calls.append((command, stdin))
        last = command[-1]
        if last == "--version":
            return 0, "2.1.319 (Claude Code)\n", ""
        response = {"--no-session-persistence": catalog, "/model": model, "/effort": effort}[last]
        if isinstance(response, Exception):
            raise response
        result: tuple[int, str, str] = response
        return result

    monkeypatch.setattr(capabilities, "_run_probe", run)
    return calls


async def discover(monkeypatch: pytest.MonkeyPatch, **responses: Any) -> ProviderCapability:
    fake_cli(monkeypatch, **responses)
    return await ClaudeCodeCapabilityProbe().discover("/fake/claude")


def listed(capability: ProviderCapability) -> list[str]:
    return [model.id for model in capability.models if model.listed]


def entry(capability: ProviderCapability, model_id: str) -> capabilities.ModelCapability:
    return next(model for model in capability.models if model.id == model_id)


@pytest.mark.asyncio
async def test_every_model_claude_code_reports_is_listed_by_full_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)

    assert capability.discovery_status == "ready"
    assert capability.default_model == "claude-opus-5-5"
    assert listed(capability) == [
        *FULL_IDS,
        "best",
        "sonnet[1m]",
        "opus[1m]",
        "fable[1m]",
        "opusplan",
    ]
    assert entry(capability, "claude-sonnet-5-5").display_name == "Sonnet 5.5"
    assert entry(capability, "claude-haiku-5-5").display_name == "Haiku 5.5"
    assert entry(capability, "claude-opus-5-5").display_name == "Opus 5.5"
    assert entry(capability, "claude-haiku-5-5").description == "Fastest for quick answers"


@pytest.mark.asyncio
async def test_a_one_million_alias_gets_a_real_name_and_keeps_the_id_the_cli_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)

    model = entry(capability, "sonnet[1m]")
    assert model.display_name == "Sonnet 5.5 (1M context)"
    assert model.description == "claude-sonnet-5-5 with the 1M context window"
    assert all("[1M]" not in item.display_name for item in capability.models)


@pytest.mark.asyncio
async def test_the_catalog_probe_cannot_run_a_model_or_keep_account_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_cli(monkeypatch)
    capability = await ClaudeCodeCapabilityProbe().discover("/fake/claude")

    command, stdin = next(call for call in calls if call[1] is not None)
    assert stdin is not None
    messages = [json.loads(line) for line in stdin.decode().splitlines()]
    assert messages == [
        {
            "type": "control_request",
            "request_id": "raticode-model-catalog",
            "request": {"subtype": "initialize"},
        }
    ]
    assert not any("dangerously" in part or "permission" in part for part in command)
    assert "--no-session-persistence" in command
    assert "example.invalid" not in capability.model_dump_json()


@pytest.mark.asyncio
async def test_the_reported_blocked_settings_are_kept_and_the_missing_models_appear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferences.save_provider_preference("claude_code", dict(REPORTED_SETTINGS))
    payload = (await discover(monkeypatch)).to_ui_payload()

    # Before the fix this was exactly: claude-opus-5-5, fable, sonnet[1m], fable[1m].
    allowed = [model["id"] for model in payload["models"] if model["listed"]]
    assert allowed == [*FULL_IDS, "sonnet[1m]", "fable[1m]"]
    assert payload["deniedModels"] == REPORTED_SETTINGS["deniedModels"]
    assert not {model["id"] for model in payload["models"]} & set(REPORTED_SETTINGS["deniedModels"])
    assert payload["defaultModel"] == "claude-opus-5-5"
    assert payload["defaultModelOverride"] == "claude-opus-5-5"
    assert payload["defaultEffortOverride"] == "high"
    default = next(model for model in payload["models"] if model["id"] == "claude-opus-5-5")
    assert default["defaultEffort"] == "high"
    assert preferences.provider_preference("claude_code") == REPORTED_SETTINGS


@pytest.mark.asyncio
async def test_saved_aliases_keep_working_but_stay_out_of_menus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)

    for alias in ("haiku", "sonnet", "opus", "fable", "default"):
        assert entry(capability, alias).listed is False
    assert entry(capability, "haiku").alias_of == "claude-haiku-5-5"
    assert entry(capability, "haiku").display_name == "Haiku alias (Haiku 5.5)"
    for selection in ("haiku", "sonnet", "sonnet[1m]", "best", "claude-haiku-5-5"):
        _validate_capability_selection(capability, selection, "low")

    preferences.save_provider_preference("claude_code", {"defaultModel": "haiku"})
    payload = capability.to_ui_payload()
    assert payload["defaultModel"] == "haiku"
    assert preferences.provider_preference("claude_code")["defaultModel"] == "haiku"


@pytest.mark.asyncio
async def test_blocking_a_full_model_also_closes_its_hidden_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)
    preferences.save_provider_preference(
        "claude_code", {"deniedModels": ["claude-opus-5-5"], "defaultModel": "opus"}
    )

    payload = capability.to_ui_payload()
    ids = [model["id"] for model in payload["models"]]
    assert "claude-opus-5-5" not in ids and "opus" not in ids and "default" not in ids
    assert payload["defaultModel"] == "claude-fable-5-1"
    assert "claude-opus-5-5" in [model["id"] for model in payload["discoveredModels"]]


@pytest.mark.asyncio
async def test_each_model_offers_only_its_own_efforts(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = await discover(monkeypatch)

    assert [e.id for e in entry(capability, "claude-haiku-5-5").efforts] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "auto",
    ]
    assert "xhigh" not in [e.id for e in entry(capability, "claude-opus-4-6").efforts]
    assert entry(capability, "claude-haiku-4-5-20251001").efforts == []
    with pytest.raises(ProviderCapabilityError, match="not supported"):
        _validate_capability_selection(capability, "claude-haiku-4-5-20251001", "high")
    with pytest.raises(ProviderCapabilityError, match="not available"):
        _validate_capability_selection(capability, "made-up-model", None)


def test_effort_usage_text_keeps_only_real_efforts() -> None:
    assert [effort.id for effort in _claude_efforts_from_slash_command(SLASH_EFFORT)] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "auto",
    ]


@pytest.mark.parametrize(
    "catalog",
    [
        TimeoutError(),
        OSError("cannot start"),
        RuntimeError("output too large"),
        (1, "", "unknown option --input-format"),
        (0, "", ""),
        (0, "not json\n", ""),
        (
            0,
            json.dumps(
                {
                    "type": "control_response",
                    "response": {
                        "request_id": "raticode-model-catalog",
                        "subtype": "error",
                        "error": "nope",
                    },
                }
            ),
            "",
        ),
        (0, CAPTURE.replace("\n", " ").replace("raticode-model-catalog", "someone-else"), ""),
        (
            0,
            json.dumps(
                {
                    "type": "control_response",
                    "response": {
                        "request_id": "raticode-model-catalog",
                        "subtype": "success",
                        "response": {"models": []},
                    },
                }
            ),
            "",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_catalog_falls_back_to_the_alias_list(
    monkeypatch: pytest.MonkeyPatch, catalog: Any
) -> None:
    capability = await discover(monkeypatch, catalog=catalog)

    assert capability.discovery_status == "ready"
    assert capability.default_model == "claude-opus-5-5"
    assert [model.id for model in capability.models[:3]] == ["claude-opus-5-5", "sonnet", "opus"]
    assert all(model.listed for model in capability.models)
    assert entry(capability, "sonnet[1m]").display_name == "Sonnet (1M context)"
    # A full ID picked while the catalog worked must not be refused during an outage.
    _validate_capability_selection(capability, "claude-haiku-5-5", "low")


@pytest.mark.asyncio
async def test_the_catalog_survives_failures_of_the_slash_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch, model=TimeoutError(), effort=(1, "", "boom"))

    assert capability.discovery_status == "ready"
    assert capability.default_model == "claude-opus-5-5"
    assert "claude-haiku-5-5" in listed(capability)
    assert capability.supports_custom_model is False

    capability = await discover(monkeypatch, effort=(1, "", "boom"))
    assert "sonnet[1m]" in listed(capability)


@pytest.mark.asyncio
async def test_when_every_source_fails_the_status_is_honest_and_nothing_is_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch, catalog=OSError(), model=TimeoutError())
    assert capability.discovery_status == "timeout"
    assert capability.models == []

    capability = await discover(monkeypatch, catalog=(1, "", ""), model=(0, "{}", ""))
    assert capability.discovery_status == "invalid_response"
    assert capability.models == []
    # Selections are left to the CLI while the list is unknown.
    _validate_capability_selection(capability, "claude-haiku-5-5", "low")


@pytest.mark.asyncio
async def test_a_signed_out_cli_still_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = await discover(monkeypatch, model=(1, "", "Not logged in"))
    assert capability.discovery_status == "unauthenticated"
    assert capability.models == []


@pytest.mark.asyncio
async def test_unusable_ids_from_the_cli_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = [
        {"value": "--dangerously-skip-permissions", "displayName": "Flag"},
        {"value": "has space", "displayName": "Space"},
        {"value": 7},
        "nonsense",
        {"value": "good", "resolvedModel": "--bad", "displayName": "Good"},
        {"value": "claude-new-9", "displayName": "New 9", "supportedEffortLevels": ["low", "; rm"]},
    ]
    reply = {
        "type": "control_response",
        "response": {
            "request_id": "raticode-model-catalog",
            "subtype": "success",
            "response": {"models": raw},
        },
    }
    capability = await discover(monkeypatch, catalog=(0, json.dumps(reply), ""))

    ids = [model.id for model in capability.models]
    assert "good" in ids and "claude-new-9" in ids
    assert not any(model_id.startswith("-") or " " in model_id for model_id in ids)
    assert "; rm" not in [effort.id for effort in entry(capability, "claude-new-9").efforts]


@pytest.mark.asyncio
async def test_a_model_chosen_outside_the_catalog_still_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = SLASH_MODEL.replace('"model": "claude-opus-5-5"', '"model": "claude-sonnet-5-5[1m]"')
    capability = await discover(monkeypatch, model=(0, current, ""))

    assert capability.default_model == "claude-sonnet-5-5[1m]"
    assert capability.models[0].id == "claude-sonnet-5-5[1m]"
    assert capability.models[0].display_name == "Sonnet 5.5 (1M context)"


@pytest.mark.asyncio
async def test_the_phone_gets_full_ids_within_its_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = await discover(monkeypatch)
    preferences.save_provider_preference("claude_code", dict(REPORTED_SETTINGS))
    monkeypatch.setattr(
        server,
        "provider_capabilities_payload",
        lambda: {"providers": [capability.to_ui_payload()]},
    )

    provider = server._device_provider_catalog()[0]

    assert "claude-sonnet-5-5" in provider["models"]
    assert "claude-haiku-5-5" in provider["models"]
    assert "sonnet[1m]" in provider["models"]
    assert "fable" not in provider["models"] and "haiku" not in provider["models"]
    # A thread already on a visible alias keeps its effort choices.
    assert "low" in provider["efforts"]["fable"]
    assert len(provider["models"]) <= 100 and len(provider["efforts"]) <= 100
    assert all(1 <= len(model) <= 160 for model in provider["models"])


@pytest.mark.asyncio
async def test_voice_tidy_finds_haiku_in_the_discovered_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)
    preferences.save_provider_preference("claude_code", dict(REPORTED_SETTINGS))
    payload = {**capability.to_ui_payload(), "enabled": True}

    result = voice_tidy.selection({"enabled": True}, "claude_code", lambda _provider: payload)

    assert result["available"] is True
    assert result["model"] == "claude-haiku-5-5"
    assert result["effort"] == "low"


def test_a_blocked_full_model_refuses_its_alias_for_new_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cli(monkeypatch)
    preferences.save_provider_preference("claude_code", {"deniedModels": ["claude-sonnet-5-5"]})

    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_commit_message_preference({"provider": "claude_code", "model": "sonnet"})
    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_voice_tidy_preference({"provider": "claude_code", "model": "sonnet"})
    assert preferences.commit_message_preference().get("model", "") == ""
    assert preferences.voice_tidy_preference()["model"] == ""


@pytest.mark.asyncio
async def test_a_saved_alias_cannot_keep_running_a_blocked_full_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cli(monkeypatch)
    preferences.save_commit_message_preference({"provider": "claude_code", "model": "sonnet"})
    preferences.save_provider_preference("claude_code", {"deniedModels": ["claude-sonnet-5-5"]})

    with pytest.raises(ValueError, match="commit model is denied"):
        await commit_message.generate_commit_message(
            provider="claude_code", model="ignored", diff="+change"
        )


@pytest.mark.asyncio
async def test_voice_tidy_refuses_an_alias_of_a_blocked_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = await discover(monkeypatch)
    preferences.save_provider_preference("claude_code", {"deniedModels": ["claude-sonnet-5-5"]})
    payload = {**capability.to_ui_payload(), "enabled": True}

    result = voice_tidy.selection(
        {"enabled": True, "provider": "claude_code", "model": "sonnet"},
        "claude_code",
        lambda _provider: payload,
    )

    assert result["available"] is False
    assert "blocked" in result["reason"]


def test_blocks_stay_exact_in_both_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cli(monkeypatch)

    # The reported settings: the alias is denied and its full model is the default.
    preferences.save_provider_preference("claude_code", dict(REPORTED_SETTINGS))
    preferences.save_commit_message_preference(
        {"provider": "claude_code", "model": "claude-opus-5-5"}
    )
    assert preferences.commit_message_preference()["model"] == "claude-opus-5-5"
    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_commit_message_preference({"provider": "claude_code", "model": "opus"})

    # Blocking one model leaves another model's alias alone.
    preferences.save_provider_preference("claude_code", {"deniedModels": ["claude-opus-5-5"]})
    preferences.save_commit_message_preference({"provider": "claude_code", "model": "sonnet"})
    assert preferences.commit_message_preference()["model"] == "sonnet"


def test_an_alias_is_refused_when_the_catalog_cannot_name_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cli(monkeypatch, catalog=TimeoutError())
    preferences.save_provider_preference("claude_code", {"deniedModels": ["claude-sonnet-5-5"]})

    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_commit_message_preference({"provider": "claude_code", "model": "sonnet"})
    preferences.save_commit_message_preference({"provider": "claude_code", "model": "haiku"})
    assert preferences.commit_message_preference()["model"] == "haiku"


def test_providers_without_aliases_never_run_discovery_for_a_block_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_cli(monkeypatch)
    preferences.save_provider_preference("codex", {"deniedModels": ["gpt-blocked"]})

    preferences.save_commit_message_preference({"provider": "codex", "model": "gpt-allowed"})
    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_commit_message_preference({"provider": "codex", "model": "gpt-blocked"})
    assert calls == []
