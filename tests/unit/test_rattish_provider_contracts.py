from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.rattish.provider_contracts import ProviderContractError, load_provider_contracts

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def test_bundled_provider_contracts_validate_and_have_portable_defaults() -> None:
    contracts = load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )

    assert set(contracts) == {"anthropic-api", "claude-code", "codex", "openai-api"}
    assert contracts["codex"].default_model == "gpt-5.6-sol"
    assert contracts["codex"].default_effort == "high"
    assert contracts["codex"].runtime_subscription == "codex"
    assert all(item.fingerprint.startswith("sha256:") for item in contracts.values())


def test_invalid_provider_contract_is_rejected_before_compilation(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            {
                "$schema": "../schemas/provider-contract.schema.json",
                "contract_version": 1,
                "provider_id": "Not Portable",
                "runtime_subscription": "codex",
                "default_model": "model",
                "default_effort": "high",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProviderContractError, match="provider_id"):
        load_provider_contracts(
            RATTISH_ROOT / "schemas" / "provider-contract.schema.json", [invalid]
        )
