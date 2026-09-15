from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gofer.rattish import artifacts


def test_repeated_compilation_reuses_assets_but_reads_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = artifacts.rattish_asset_root()
    artifacts._cached_compiler.cache_clear()
    artifacts._cached_provider_contracts.cache_clear()
    artifacts._diagnostic_validator.cache_clear()
    reads: list[Path] = []
    original = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.is_relative_to(root):
            reads.append(path)
        return original(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", read_text)
    source = tmp_path / "workflow.rattish"
    source.write_text("Rattish: 1\n\nWorkflow:\n  name: Example\n", encoding="utf-8")
    first = artifacts.compile_rattish_file(source, data_dir=tmp_path / "data")
    assert not first.cache_hit
    assert reads
    artifacts.compile_rattish_file(source, data_dir=tmp_path / "data")
    reads.clear()
    assert artifacts.compile_rattish_file(source, data_dir=tmp_path / "data").cache_hit
    assert reads == []
    source.write_text("Rattish: 1\n\nWorkflow:\n  name: Changed\n", encoding="utf-8")
    changed = artifacts.compile_rattish_file(source, data_dir=tmp_path / "data")
    assert not changed.cache_hit
    assert changed.ir["workflow"]["name"] == "Changed"


def test_asset_caches_invalidate_edits_replacements_and_removal(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    original = artifacts.rattish_asset_root()
    for directory in ("schemas", "contracts", "providers"):
        shutil.copytree(original / directory, root / directory)
    compiler = artifacts._compiler(root)
    providers = artifacts._provider_contracts(root)
    assert artifacts._compiler(root) is compiler
    assert artifacts._provider_contracts(root) is providers

    contract = next((root / "contracts").glob("*.json"))
    contract.write_text(contract.read_text() + "\n")
    assert artifacts._compiler(root) is not compiler
    provider = next((root / "providers").glob("*.json"))
    replacement = provider.with_suffix(".tmp")
    replacement.write_text(provider.read_text() + "\n")
    replacement.replace(provider)
    assert artifacts._provider_contracts(root) is not providers
    provider.unlink()
    assert len(artifacts._provider_contracts(root)) == len(providers) - 1

    schema = root / "schemas" / "diagnostic.schema.json"
    validator = artifacts._diagnostic_validator(artifacts._asset_identity([schema]))
    document = json.loads(schema.read_text())
    document["description"] = "Changed diagnostic schema"
    schema.write_text(json.dumps(document))
    assert artifacts._diagnostic_validator(artifacts._asset_identity([schema])) is not validator
    schema.unlink()
    with pytest.raises(FileNotFoundError):
        artifacts._asset_identity([schema])
