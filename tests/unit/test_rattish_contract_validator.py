from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.rattish.contract_validator import ContractValidationError, validate_contract_files

PROJECT_ROOT = Path(__file__).parents[2]
META_SCHEMA = PROJECT_ROOT / "rattish" / "schemas" / "node-contract.schema.json"
CONTRACT_DIR = PROJECT_ROOT / "rattish" / "contracts"


def contract_paths() -> list[Path]:
    return sorted(CONTRACT_DIR.glob("*.json"))


def test_representative_contracts_validate() -> None:
    contracts = validate_contract_files(META_SCHEMA, contract_paths())

    assert {contract["node_type"] for contract in contracts} == {
        "agent",
        "approval-gate",
        "bash-command",
        "break",
        "common-llm-task",
        "copy-file",
        "delete-file",
        "file",
        "folder",
        "http-request",
        "local-search",
        "local-vectorize",
        "loop",
        "move-file",
        "notification",
        "open-resource",
        "prompt-file",
        "read-file",
        "python-script",
        "shell-script",
        "write-file",
        "workflow",
    }


def test_unknown_default_field_is_reported(tmp_path: Path) -> None:
    source = json.loads((CONTRACT_DIR / "break.json").read_text(encoding="utf-8"))
    source["defaults"]["not_a_field"] = "bad"
    contract_path = tmp_path / "invalid.json"
    contract_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ContractValidationError) as exc_info:
        validate_contract_files(META_SCHEMA, [contract_path])

    assert [issue.code for issue in exc_info.value.issues] == [
        "RATTISH_CONTRACT_UNKNOWN_DEFAULT_FIELD"
    ]
    assert exc_info.value.issues[0].path == ("defaults", "not_a_field")


def test_invalid_literal_default_is_reported(tmp_path: Path) -> None:
    source = json.loads((CONTRACT_DIR / "break.json").read_text(encoding="utf-8"))
    source["defaults"]["message"] = 42
    contract_path = tmp_path / "invalid.json"
    contract_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ContractValidationError) as exc_info:
        validate_contract_files(META_SCHEMA, [contract_path])

    assert [issue.code for issue in exc_info.value.issues] == ["RATTISH_CONTRACT_DEFAULT_INVALID"]
    assert exc_info.value.issues[0].path == ("defaults", "message")


def test_optional_runtime_field_requires_default_or_explicit_absence(tmp_path: Path) -> None:
    source = json.loads((CONTRACT_DIR / "break.json").read_text(encoding="utf-8"))
    source["defaults"].pop("message")
    contract_path = tmp_path / "invalid.json"
    contract_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ContractValidationError) as exc_info:
        validate_contract_files(META_SCHEMA, [contract_path])

    assert "RATTISH_CONTRACT_OPTIONAL_FIELD_UNSPECIFIED" in {
        issue.code for issue in exc_info.value.issues
    }


def test_duplicate_contract_identity_is_reported() -> None:
    contract = CONTRACT_DIR / "agent.json"

    with pytest.raises(ContractValidationError) as exc_info:
        validate_contract_files(META_SCHEMA, [contract, contract])

    assert exc_info.value.issues[-1].code == "RATTISH_CONTRACT_DUPLICATE_IDENTITY"


def test_unsupported_data_schema_keyword_is_reported(tmp_path: Path) -> None:
    source = json.loads((CONTRACT_DIR / "break.json").read_text(encoding="utf-8"))
    source["success_output"]["schema"]["oneOf"] = [{"type": "string"}]
    contract_path = tmp_path / "invalid.json"
    contract_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ContractValidationError) as exc_info:
        validate_contract_files(META_SCHEMA, [contract_path])

    assert [issue.code for issue in exc_info.value.issues] == [
        "RATTISH_CONTRACT_SCHEMA_PROFILE_UNSUPPORTED"
    ]
    assert exc_info.value.issues[0].path == ("success_output", "schema", "oneOf")
