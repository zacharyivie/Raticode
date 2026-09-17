"""Grok's text listing and ACP effort metadata, without a real CLI."""

from contextlib import asynccontextmanager

import pytest

from gofer.core import provider_capabilities as caps
from gofer.core.provider_profiles import ResolvedProviderSettings, validate_provider_settings

LISTING = """You are logged in with grok.com.

Default model: grok-4.6

Available models:
  * grok-4.6 (default)
  - custom/fast-model
  - grok-4.6
"""
CATALOG = {
    "result": {
        "currentModelId": "grok-4.6",
        "availableModels": [
            {
                "modelId": "grok-4.6",
                "name": "Grok 4.6",
                "_meta": {
                    "supportsReasoningEffort": True,
                    "reasoningEffort": "high",
                    "reasoningEfforts": [
                        {"id": "deep", "value": "xhigh", "label": "Extra High Effort"},
                        {"value": "high", "label": "High Effort", "default": True},
                        "medium",
                        {"value": "low"},
                        {"value": "low"},
                        None,
                    ],
                },
            },
            {"modelId": "custom/fast-model", "name": "Fast"},
        ],
    }
}


def test_text_catalog_ignores_banners_preserves_ids_and_default():
    models = caps._grok_models_from_listing("\x1b[32m" + LISTING + "\x1b[0m")
    assert [m.id for m in models] == ["grok-4.6", "custom/fast-model"]
    assert models[0].is_default
    assert not models[1].is_default
    assert not models[0].efforts
    assert caps._grok_models_from_listing("not a catalog") == []


def test_efforts_use_canonical_values_not_presentation_aliases():
    models = caps._grok_models_from_catalog(CATALOG)
    assert models[0].display_name == "Grok 4.6"
    assert models[0].default_effort == "high"
    assert [e.id for e in models[0].efforts] == ["xhigh", "high", "medium", "low"]
    assert models[0].efforts[0].display_name == "Extra High Effort"
    assert not models[1].efforts
    provider = caps.ProviderCapability(
        id="grok", display_name="Grok", available=True, discovery_status="ready", models=models
    )
    caps._validate_capability_selection(provider, "grok-4.6", "xhigh")
    with pytest.raises(caps.ProviderCapabilityError):
        caps._validate_capability_selection(provider, "grok-4.6", "deep")
    validate_provider_settings(
        ResolvedProviderSettings(
            subscription="grok", model="grok-4.6", effort="xhigh", approval_mode="cli-managed"
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_available", [True, False])
async def test_discovery_uses_listing_and_optional_structured_efforts(
    monkeypatch, metadata_available
):
    calls = []

    async def run(command):
        calls.append(command)
        return 0, LISTING if command[-1] == "models" else "1.0.34", ""

    async def catalog(executable):
        if not metadata_available:
            raise caps._InvalidCatalogError
        return CATALOG

    monkeypatch.setattr(caps, "_run_probe", run)
    monkeypatch.setattr(caps, "_grok_model_catalog", catalog)
    result = await caps.AdditionalCliCapabilityProbe("grok").discover("/fake/grok")
    assert calls == [["/fake/grok", "version"], ["/fake/grok", "models"]]
    assert result.discovery_status == "ready"
    assert result.default_model == "grok-4.6"
    assert bool(result.models[0].efforts) == metadata_available


@pytest.mark.asyncio
async def test_metadata_probe_never_creates_session_or_sends_prompt(monkeypatch):
    calls = []

    class Rpc:
        async def request(self, method, params, **kwargs):
            calls.append((method, params))
            return {"protocolVersion": 1} if method == "initialize" else CATALOG

    @asynccontextmanager
    async def transport(command, **kwargs):
        assert command == ["/fake/grok", "agent", "--no-leader", "stdio"]
        yield Rpc()

    monkeypatch.setattr("gofer.subscriptions.acp_transport.open_acp_transport", transport)
    assert await caps._grok_model_catalog("/fake/grok") == CATALOG
    assert [method for method, _ in calls] == ["initialize", "_x.ai/models/list"]


def test_malformed_metadata_rows_do_not_discard_valid_models():
    payload = {
        "result": {
            "availableModels": [
                {"modelId": []},
                {"modelId": "grok-4.6", "name": "Grok 4.6", "_meta": None},
            ]
        }
    }
    assert [m.id for m in caps._grok_models_from_catalog(payload)] == ["grok-4.6"]
