"""Loaded Rattish node contracts and deterministic identities."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from gofer.rattish.contract_validator import validate_contract_files


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize an I-JSON value using RFC 8785 ordering and number spelling."""
    return _canonical_json(value).encode("utf-8")


def _canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("RFC 8785 integers must be exactly representable as IEEE-754.")
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Canonical JSON object keys must be strings.")
        keys = sorted(value, key=lambda key: key.encode("utf-16be", errors="surrogatepass"))
        return (
            "{"
            + ",".join(f"{_canonical_json(key)}:{_canonical_json(value[key])}" for key in keys)
            + "}"
        )
    raise TypeError(f"Value of type {type(value).__name__} is not JSON.")


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("RFC 8785 does not permit non-finite numbers.")
    if value == 0:
        return "0"
    decimal = Decimal(repr(value))
    absolute = abs(decimal)
    if Decimal("1e-6") <= absolute < Decimal("1e21"):
        fixed = format(decimal, "f")
        if "." in fixed:
            fixed = fixed.rstrip("0").rstrip(".")
        return fixed

    sign = "-" if decimal.is_signed() else ""
    digits = "".join(str(digit) for digit in decimal.copy_abs().as_tuple().digits)
    exponent = cast(int, decimal.as_tuple().exponent)
    digits = digits.rstrip("0") or "0"
    scientific_exponent = len(digits) + exponent - 1
    mantissa = digits[0]
    if len(digits) > 1:
        mantissa += "." + digits[1:]
    exponent_sign = "+" if scientific_exponent >= 0 else ""
    return f"{sign}{mantissa}e{exponent_sign}{scientific_exponent}"


def json_fingerprint(value: Any) -> str:
    """Return the Rattish SHA-256 fingerprint form."""
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


@dataclass(frozen=True, slots=True)
class LoadedNodeContract:
    node_type: str
    version: int
    document: dict[str, Any]
    fingerprint: str


class NodeContractRegistry:
    """Validated contracts keyed by canonical node type."""

    def __init__(self, contracts: dict[str, LoadedNodeContract]) -> None:
        self._contracts = contracts

    @classmethod
    def from_files(
        cls,
        meta_schema_path: Path,
        contract_paths: list[Path],
        *,
        fingerprint_overrides: dict[str, str] | None = None,
    ) -> NodeContractRegistry:
        documents = validate_contract_files(meta_schema_path, contract_paths)
        overrides = fingerprint_overrides or {}
        contracts = {
            document["node_type"]: LoadedNodeContract(
                node_type=document["node_type"],
                version=document["contract_version"],
                document=document,
                fingerprint=overrides.get(document["node_type"], json_fingerprint(document)),
            )
            for document in documents
        }
        return cls(contracts)

    def get(self, node_type: str) -> LoadedNodeContract | None:
        return self._contracts.get(node_type.lower())

    def require(self, node_type: str) -> LoadedNodeContract:
        contract = self.get(node_type)
        if contract is None:
            raise KeyError(node_type)
        return contract

    def __iter__(self) -> Iterator[LoadedNodeContract]:
        return iter(self._contracts.values())
