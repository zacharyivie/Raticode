"""Versioned provider defaults used by portable Rattish compilation."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.rattish.contracts import json_fingerprint


class ProviderContractError(ValueError):
    """Raised when a bundled or plugin provider contract is invalid."""


@dataclass(frozen=True, slots=True)
class ProviderContract:
    provider_id: str
    version: int
    default_model: str
    default_effort: str
    fingerprint: str
    runtime_subscription: str | None = None


def load_provider_contracts(
    schema_path: Path, contract_paths: Iterable[Path]
) -> dict[str, ProviderContract]:
    """Load, validate, fingerprint, and index provider contracts by canonical ID."""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderContractError(f"Could not load provider contract schema: {exc}") from exc
    validator = Draft202012Validator(schema)
    loaded: dict[str, ProviderContract] = {}
    for path in contract_paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderContractError(f"Could not load provider contract {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ProviderContractError(f"Provider contract {path} must be a JSON object.")
        try:
            validator.validate(document)
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            raise ProviderContractError(
                f"Provider contract {path} is invalid at {location}: {exc.message}"
            ) from exc
        owned = cast(dict[str, Any], document)
        provider_id = cast(str, owned["provider_id"])
        if provider_id in loaded:
            raise ProviderContractError(f"Provider contract {provider_id!r} is duplicated.")
        loaded[provider_id] = ProviderContract(
            provider_id=provider_id,
            version=cast(int, owned["contract_version"]),
            default_model=cast(str, owned["default_model"]),
            default_effort=cast(str, owned["default_effort"]),
            fingerprint=json_fingerprint(owned),
            runtime_subscription=cast(str, owned["runtime_subscription"]),
        )
    return loaded
