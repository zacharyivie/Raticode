from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish import artifacts, ir_validation, runtime
from gofer.rattish.ir_validation import ValidatedRattishIR


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


BASH_WORKFLOW = (
    "Rattish: 1\n\nWorkflow:\n  name: Example\n\n"
    "Node step-1:\n  type: bash-command\n  command: echo hello\n"
)


class _LoadIrCalls:
    """Counts artifacts.load_ir calls and records the errors they raise."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        self.errors: list[Exception] = []
        original = artifacts.load_ir

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                self.errors.append(exc)
                raise

        monkeypatch.setattr(artifacts, "load_ir", counted)


def _artifact_path(data_dir: Path) -> Path:
    [path] = list(data_dir.rglob("*.json"))
    return path


def _fresh_memo() -> None:
    with artifacts._validated_artifacts_lock:
        artifacts._validated_artifacts.clear()


def test_cache_hit_reuses_validated_artifact_without_load_ir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_memo()
    loads = _LoadIrCalls(monkeypatch)
    source = tmp_path / "workflow.rattish"
    source.write_text(BASH_WORKFLOW, encoding="utf-8")
    data_dir = tmp_path / "data"

    compiled = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert not compiled.cache_hit
    assert loads.calls == 0

    first_hit = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert first_hit.cache_hit
    assert loads.calls == 1

    for _ in range(3):
        repeat = artifacts.compile_rattish_file(source, data_dir=data_dir)
        assert repeat.cache_hit
        assert repeat.ir == first_hit.ir
        assert repeat.source_path == first_hit.source_path
    assert loads.calls == 1


def test_one_changed_artifact_byte_runs_full_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_memo()
    loads = _LoadIrCalls(monkeypatch)
    source = tmp_path / "workflow.rattish"
    source.write_text(BASH_WORKFLOW, encoding="utf-8")
    data_dir = tmp_path / "data"
    artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
    assert loads.calls == 1

    # A harmless one-byte change (the trailing newline) is still validated in full.
    path = _artifact_path(data_dir)
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    path.write_bytes(raw[:-1] + b" ")
    accepted = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert accepted.cache_hit
    assert loads.calls == 2
    assert loads.errors == []


def test_tampered_artifact_is_rejected_after_a_remembered_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_memo()
    loads = _LoadIrCalls(monkeypatch)
    source = tmp_path / "workflow.rattish"
    source.write_text(BASH_WORKFLOW, encoding="utf-8")
    data_dir = tmp_path / "data"
    artifacts.compile_rattish_file(source, data_dir=data_dir)
    trusted = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert trusted.cache_hit
    assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
    assert loads.calls == 1

    path = _artifact_path(data_dir)
    raw = path.read_bytes()
    original = b'"runtime_handler":"raticode.bash_command"'
    assert raw.count(original) == 1
    tampered = raw.replace(original, b'"runtime_handler":"raticode.bash_commanD"')
    assert len(tampered) == len(raw)
    assert sum(a != b for a, b in zip(raw, tampered, strict=True)) == 1
    path.write_bytes(tampered)

    result = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert loads.calls == 2
    assert len(loads.errors) == 1
    assert isinstance(loads.errors[0], artifacts.InvalidRattishIrError)
    # The tampered file was not trusted: the source was compiled again.
    assert not result.cache_hit
    assert result.ir["nodes"][0]["runtime_handler"] == "raticode.bash_command"
    assert b"raticode.bash_commanD" not in path.read_bytes()


def test_changed_ir_schema_misses_remembered_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_memo()
    root = tmp_path / "assets"
    original = artifacts.rattish_asset_root()
    for directory in ("schemas", "contracts", "providers"):
        shutil.copytree(original / directory, root / directory)
    monkeypatch.setattr(artifacts, "rattish_asset_root", lambda: root)
    loads = _LoadIrCalls(monkeypatch)
    source = tmp_path / "workflow.rattish"
    source.write_text(BASH_WORKFLOW, encoding="utf-8")
    data_dir = tmp_path / "data"
    artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
    assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
    assert loads.calls == 1

    schema_path = root / "schemas" / "ir.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["description"] = "Changed IR schema"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
    assert loads.calls == 2


def test_cache_hits_return_copies_callers_cannot_share(tmp_path: Path) -> None:
    _fresh_memo()
    source = tmp_path / "workflow.rattish"
    source.write_text(
        "Rattish: 1\nWorkflow:\n  name: W\nNode x:\n  type: jev\n  question: Q?\n  state: s\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    artifacts.compile_rattish_file(source, data_dir=data_dir)
    first = artifacts.compile_rattish_file(source, data_dir=data_dir)
    second = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert first.cache_hit and second.cache_hit
    assert isinstance(first.ir, ValidatedRattishIR)
    assert first.ir is not second.ir
    assert first.ir["workflow"] is not second.ir["workflow"]
    assert [item["code"] for item in first.diagnostics] == ["RATTISH_JEV_CRITERIA_MISSING"]
    assert first.diagnostics[0] is not second.diagnostics[0]

    with pytest.raises(TypeError):
        first.ir["workflow"]["name"] = "Changed"  # type: ignore[index]
    # Even writes that bypass the frozen IR, or edit diagnostics, stay with that caller.
    dict.__setitem__(first.ir["workflow"], "name", "Changed")
    first.diagnostics[0]["message"] = "Changed"
    third = artifacts.compile_rattish_file(source, data_dir=data_dir)
    assert third.cache_hit
    assert third.ir["workflow"]["name"] == "W"
    assert third.diagnostics[0]["message"] != "Changed"


def test_memo_holds_at_most_the_configured_number_of_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_memo()
    monkeypatch.setattr(artifacts, "VALIDATED_ARTIFACT_LIMIT", 2)
    loads = _LoadIrCalls(monkeypatch)
    data_dir = tmp_path / "data"
    sources = []
    for index in range(3):
        source = tmp_path / f"w{index}" / "workflow.rattish"
        source.parent.mkdir()
        source.write_text(BASH_WORKFLOW, encoding="utf-8")
        artifacts.compile_rattish_file(source, data_dir=data_dir)
        assert artifacts.compile_rattish_file(source, data_dir=data_dir).cache_hit
        sources.append(source)
    assert len(artifacts._validated_artifacts) == 2
    assert loads.calls == 3
    # The oldest entry was evicted, so it is validated again; the newest is remembered.
    assert artifacts.compile_rattish_file(sources[0], data_dir=data_dir).cache_hit
    assert loads.calls == 4
    assert artifacts.compile_rattish_file(sources[2], data_dir=data_dir).cache_hit
    assert loads.calls == 4


def test_load_ir_reuses_validators_and_checks_each_output_schema_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(BASH_WORKFLOW, encoding="utf-8")
    compiled = artifacts.compile_rattish_file(source, data_dir=tmp_path / "data")
    compiler = artifacts._compiler(artifacts.rattish_asset_root())
    contracts = {contract.node_type: contract.document for contract in compiler.contracts}
    providers = artifacts._provider_contracts(artifacts.rattish_asset_root())
    schema = compiler.ir_validator.schema
    assert runtime._schema_validator(schema) is runtime._schema_validator(schema)

    built: list[object] = []
    original_validator = runtime.Draft202012Validator

    def counting_validator(*args: Any, **kwargs: Any) -> Any:
        built.append(args[0])
        return original_validator(*args, **kwargs)

    checks: list[object] = []
    original_check = ir_validation.validate_schema

    def counting_check(value: dict[str, Any]) -> None:
        checks.append(value)
        original_check(value)

    monkeypatch.setattr(runtime, "Draft202012Validator", counting_validator)
    monkeypatch.setattr(ir_validation, "validate_schema", counting_check)
    document = json.loads(json.dumps(compiled.ir))
    runtime.load_ir(document, schema, contracts, providers, {})
    built.clear()
    checks.clear()
    runtime.load_ir(document, schema, contracts, providers, {})
    assert built == []
    assert checks == []

    # A schema the cache has never accepted is still checked, and rejected every time.
    document["nodes"][0]["output"]["schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "https://example.com/remote.json",
    }
    for _ in range(2):
        with pytest.raises(runtime.InvalidRattishIrError, match="invalid JSON Schema"):
            runtime.load_ir(document, schema, contracts, providers, {})
    assert len(checks) == 2
