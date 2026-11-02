from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 2)
ADVISORY = "GHSA-g3hv-75wg-m249"


@pytest.fixture
def gate() -> Any:
    spec = importlib.util.spec_from_file_location(
        "check_npm_audit", ROOT / "scripts/check-npm-audit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(advisory: str = ADVISORY, node: str = "node_modules/minimatch") -> dict[str, Any]:
    return {
        "auditReportVersion": 2,
        "metadata": {"vulnerabilities": {"moderate": 2}},
        "vulnerabilities": {
            "minimatch": {
                "via": [
                    {
                        "source": 1,
                        "name": "minimatch",
                        "url": f"https://github.com/advisories/{advisory}",
                        "range": "<11.0.1",
                    }
                ],
                "nodes": [node],
            },
            "eslint": {"via": ["minimatch"], "nodes": ["node_modules/eslint"]},
        },
    }


def _lock(*shipped: str) -> dict[str, Any]:
    paths = ["node_modules/minimatch", "node_modules/eslint", *shipped]
    return {"packages": {path: {"dev": path not in shipped} for path in paths}}


def _exceptions(expires: str = "2026-12-15") -> list[dict[str, str]]:
    return [{"advisory": ADVISORY, "package": "minimatch", "expires": expires, "reason": "r"}]


def test_reviewed_build_tool_advisories_pass(gate: Any) -> None:
    assert gate.check(_report(), _lock(), _exceptions(), TODAY) == ([], [])


def test_new_advisories_fail(gate: Any) -> None:
    failures, _ = gate.check(_report("GHSA-2222-3333-4444"), _lock(), _exceptions(), TODAY)

    assert failures == ["GHSA-2222-3333-4444 in minimatch has no reviewed exception."]


def test_exceptions_never_cover_packages_shipped_with_the_app(gate: Any) -> None:
    shipped = "node_modules/app/node_modules/minimatch"
    report = _report(node=shipped)

    failures, _ = gate.check(report, _lock(shipped), _exceptions(), TODAY)

    assert failures == [f"minimatch is shipped with the app: {shipped}."]


def test_dependents_of_an_advisory_must_also_stay_out_of_the_app(gate: Any) -> None:
    failures, _ = gate.check(_report(), _lock("node_modules/eslint"), _exceptions(), TODAY)

    assert failures == ["eslint is shipped with the app: node_modules/eslint."]


def test_expired_exceptions_fail(gate: Any) -> None:
    failures, _ = gate.check(_report(), _lock(), _exceptions("2026-11-01"), TODAY)

    assert failures == [f"The exception for {ADVISORY} in minimatch expired on 2026-11-01."]


def test_unused_exceptions_are_reported_for_removal(gate: Any) -> None:
    clean = {"auditReportVersion": 2, "metadata": {}, "vulnerabilities": {}}

    failures, notes = gate.check(clean, _lock(), _exceptions(), TODAY)

    assert failures == []
    assert notes == [f"The exception for {ADVISORY} in minimatch is no longer needed; remove it."]


def test_an_incomplete_audit_report_fails(gate: Any) -> None:
    network_error = {"error": {"code": "ENOTFOUND", "summary": "registry unreachable"}}

    failures, _ = gate.check(network_error, _lock(), _exceptions(), TODAY)

    assert failures == ["npm audit did not produce a complete report."]


def test_reviewed_exceptions_are_explained_and_short_lived() -> None:
    document = json.loads((ROOT / "frontend/npm-audit-exceptions.json").read_text(encoding="utf8"))

    for item in document["exceptions"]:
        assert item["advisory"].startswith("GHSA-")
        assert item["package"] and len(item["reason"]) > 40
        assert date.fromisoformat(item["expires"]) <= date(2027, 2, 1)
