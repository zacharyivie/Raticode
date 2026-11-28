"""Every node contract field carries the help line the workflow editor shows under it."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from gofer.rattish.contracts import contract_fingerprint

PROJECT_ROOT = Path(__file__).parents[2]
CONTRACTS = sorted((PROJECT_ROOT / "rattish" / "contracts").glob("*.json"))


def _fields(schema: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """Each property of a configuration schema, nested ones included."""
    for name, field in (schema.get("properties") or {}).items():
        path = f"{prefix}{name}"
        yield path, field
        if isinstance(field, Mapping):
            yield from _fields(field, f"{path}.")
            items = field.get("items")
            if isinstance(items, Mapping):
                yield from _fields(items, f"{path}[].")


def _load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def test_every_contract_field_has_a_description() -> None:
    assert CONTRACTS
    missing: list[str] = []
    for path in CONTRACTS:
        for name, field in _fields(_load(path)["configuration_schema"]):
            description = field.get("description") if isinstance(field, Mapping) else None
            if not isinstance(description, str) or not description.strip():
                missing.append(f"{path.name}: {name}")
    assert not missing, "Add a short description to: " + ", ".join(missing)


def test_field_descriptions_are_short_single_sentence_case_lines() -> None:
    for path in CONTRACTS:
        for name, field in _fields(_load(path)["configuration_schema"]):
            description = field["description"]
            where = f"{path.name}: {name}"
            assert "\n" not in description and "`" not in description, where
            assert "](" not in description, where
            assert len(description) <= 120, where
            assert description[0].isupper() or description[0].isdigit(), where
            assert description.endswith("."), where


def test_http_request_headers_help_matches_the_website() -> None:
    document = _load(PROJECT_ROOT / "rattish/contracts/http-request.json")
    headers = document["configuration_schema"]["properties"]["headers"]

    assert headers["description"] == "String HTTP headers."
    page = (PROJECT_ROOT / "website/docs/nodes/http-request.md").read_text(encoding="utf-8")
    assert "| `headers` | object | `{}` | String HTTP headers. |" in page


def test_field_help_does_not_change_the_contract_fingerprint() -> None:
    # Saved workflows pin each contract's fingerprint, so rewording help can't break them.
    for path in CONTRACTS:
        document = _load(path)
        reworded = copy.deepcopy(document)
        bare = copy.deepcopy(document)
        for (_, field), (_, other) in zip(
            _fields(reworded["configuration_schema"]),
            _fields(bare["configuration_schema"]),
            strict=True,
        ):
            field["description"] = "Something else."
            other.pop("description", None)

        assert contract_fingerprint(reworded) == contract_fingerprint(document), path.name
        assert contract_fingerprint(bare) == contract_fingerprint(document), path.name


def test_a_field_named_description_keeps_its_schema_in_the_fingerprint() -> None:
    def contract(field_schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_type": "example",
            "configuration_schema": {
                "type": "object",
                "properties": {"description": field_schema},
            },
        }

    assert contract_fingerprint(contract({"type": "string"})) != contract_fingerprint(
        contract({"type": "integer"})
    )
