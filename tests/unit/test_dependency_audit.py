from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    script = Path(__file__).resolve().parents[2] / "scripts/audit-dependencies.py"
    spec = importlib.util.spec_from_file_location("dependency_audit", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {}})
    )
    (tmp_path / "uv.lock").write_text(
        "\n".join(
            f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
            f'source = {{ {source} = "https://example.invalid" }}'
            for name, version, source in [
                ("vosk", "0.3.44", "registry"),
                ("vosk", "0.3.45", "registry"),
                ("example", "1.0", "registry"),
                ("example", "1.0", "registry"),
                ("local-project", "1.0", "editable"),
            ]
        )
    )
    return module


@pytest.mark.parametrize("vulnerable_batch", [None, 0, 1])
def test_audit_checks_every_locked_version_and_preserves_advisories(
    audit: Any, monkeypatch: pytest.MonkeyPatch, vulnerable_batch: int | None
) -> None:
    batches: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--no-deps" in command and "--disable-pip" in command
        requirements = Path(command[command.index("--requirement") + 1]).read_text().splitlines()
        assert len({item.split("==")[0] for item in requirements}) == len(requirements)
        vulnerable = len(batches) == vulnerable_batch
        batches.append(requirements)
        dependencies: list[dict[str, Any]] = [
            {"name": name, "version": version, "vulns": []}
            for name, version in (item.split("==") for item in requirements)
        ]
        if vulnerable:
            dependencies[0]["vulns"] = [{"id": "TEST-ADVISORY", "fix_versions": []}]
        Path(command[command.index("--output") + 1]).write_text(
            json.dumps({"dependencies": dependencies, "fixes": []})
        )
        return subprocess.CompletedProcess(command, int(vulnerable))

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.main() == int(vulnerable_batch is not None)
    expected = {"example==1.0", "vosk==0.3.44", "vosk==0.3.45"}
    assert len(batches) == 2
    assert {item for batch in batches for item in batch} == expected
    evidence = audit.ROOT / "audit-evidence"
    assert set((evidence / "python-lock-inventory.txt").read_text().splitlines()) == expected
    report = json.loads((evidence / "python-audit.json").read_text())
    assert {f"{item['name']}=={item['version']}" for item in report["dependencies"]} == expected
    assert sum(bool(item["vulns"]) for item in report["dependencies"]) == int(
        vulnerable_batch is not None
    )
    assert (evidence / "npm-lock-inventory.json").exists()


@pytest.mark.parametrize("failure", ["missing", "invalid", "incomplete", "skipped", "exit"])
def test_audit_failures_cannot_reuse_clean_evidence(
    audit: Any, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    evidence = audit.ROOT / "audit-evidence"
    evidence.mkdir()
    output = evidence / "python-audit.json"
    output.write_text('{"dependencies": [], "fixes": []}')

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        report = Path(command[command.index("--output") + 1])
        if failure == "invalid":
            report.write_text("not JSON")
        elif failure != "missing":
            dependencies: list[dict[str, Any]] = [
                {"name": name, "version": version, "vulns": []}
                for name, version in (
                    item.split("==")
                    for item in Path(command[command.index("--requirement") + 1])
                    .read_text()
                    .splitlines()
                )
            ]
            if failure == "incomplete":
                dependencies.pop()
            elif failure == "skipped":
                dependencies[0]["skip_reason"] = "Unavailable"
            report.write_text(json.dumps({"dependencies": dependencies, "fixes": []}))
        return subprocess.CompletedProcess(command, 1 if failure in {"missing", "exit"} else 0)

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.main() == 1
    if failure == "exit":
        assert len(json.loads(output.read_text())["dependencies"]) == 3
    else:
        assert not output.exists()
