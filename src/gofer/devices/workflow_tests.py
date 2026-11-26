"""Try another model on a workflow's test set from a paired phone.

The phone gets the same action as the desktop's Tests panel and ``Raticode rattish test
--model``, through the same functions (``gofer.ui.workflow_tests``):

- ``workflows.tests`` reads one workflow's test set: how many cases, the steps that call a
  model with what each uses now, the desktop's enabled providers and their models, and the
  newest result. For a try that result has the side-by-side comparison: each case's
  pass/fail, cost and time for the current model against the candidate, with totals.
  The phone polls it while a try runs.
- ``workflows.tests.estimate`` reads the expected cost of both models before anything runs.
- ``workflows.tests.try`` starts the try. The phone sends it only after showing both
  estimates, with ``confirmed``; the desktop refuses it without, and a resent request
  starts nothing new.
- ``workflows.tests.use_model`` writes ``provider:`` and ``model:`` into the step through
  the editor service (``model_overrides.use_model``), so the graph stays valid and every
  trigger asks for approval again. The phone names the revision the try tested; a
  workflow that changed since is refused as a conflict.

A try changes nothing on the computer except the saved result and the runs it makes. Like
every phone request these pass the phone guard first. Results carry no source, inputs or
outputs; case names and messages are the test set's own words.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import Refused

READS = frozenset({"workflows.tests", "workflows.tests.estimate"})
ACTIONS = frozenset({"workflows.tests.try", "workflows.tests.use_model"})
CONFIRM_TRY = "Check the expected cost on your phone before trying the model."
STALE = "The workflow changed since this try. Try the model again."
STILL_RUNNING = "Wait for the try to finish."
NOT_A_TRY = "The newest result didn't try another model."
MAX_MODELS = 20
MAX_CASES = 200


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def models_param(value: Any) -> list[Any]:
    """The phone's ``models`` as checked overrides, or a refusal that says what's wrong."""
    from gofer.rattish.model_overrides import ModelOverrideError, model_overrides_from_json

    if not isinstance(value, list) or not value or len(value) > MAX_MODELS:
        raise Refused("invalid", "Choose a step and the model to try on it.")
    try:
        return model_overrides_from_json(value)
    except ModelOverrideError as exc:
        raise Refused("invalid", _text(exc, 600)) from None


def gather(op: str, params: dict[str, Any], registered: Any, base: Path) -> dict[str, Any]:
    """Compile and read for one phone view. Runs outside the device lock."""
    from gofer.rattish.test_sets import WorkflowTestError
    from gofer.ui.workflow_tests import estimate_workflow_tests_payload, workflow_tests_payload

    if op == "workflows.tests":
        return {
            "registered": registered,
            "tests": workflow_tests_payload(registered.workflow_id, base),
        }
    revision = params.get("expected_revision")
    if not isinstance(revision, str) or not revision:
        raise Refused("invalid", "Refresh the tests, then check the cost again.")
    models = models_param(params.get("models"))
    try:
        estimate = estimate_workflow_tests_payload(
            registered.workflow_id,
            base,
            {"models": [item.to_json() for item in models], "expectedRevision": revision},
        )
    except WorkflowTestError as exc:
        code = "conflict" if "changed since" in str(exc) else "invalid"
        raise Refused(code, _text(exc, 600)) from None
    return {"registered": registered, "estimate": estimate}


def usd(value: Any, complete: bool = True) -> str:
    from gofer.rattish.test_sets import format_usd

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "Unknown"
    text = format_usd(float(value))
    return text if complete else f"At least {text}"


def seconds(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{value / 1000:.1f}s"


def estimate_view(workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from gofer.rattish.test_sets import variant_label

    estimate = payload["estimate"]
    totals = estimate.get("totals") or {}
    current = estimate.get("current") or {}
    candidate = estimate.get("candidate") or {}
    return {
        "workflow_id": workflow_id,
        "revision": _text(payload.get("revision"), 80),
        "current": {
            "summary": _text(current.get("summary"), 600),
            "reused": bool(current.get("reused")),
        },
        "candidate": {
            "label": _text(variant_label(candidate.get("variant")), 400),
            "summary": _text(candidate.get("summary"), 600),
        },
        "total": usd(totals.get("costUsd"), bool(totals.get("costComplete"))),
    }


def tests_view(
    registered: Any, payload: dict[str, Any], providers: list[dict[str, Any]], clean: Any
) -> dict[str, Any]:
    """One workflow's tests for the phone. ``clean`` hides the project and data folders."""
    from gofer.rattish.test_sets import variant_label

    test_set = payload.get("testSet") or {}
    latest = payload.get("latest")
    view: dict[str, Any] = {
        "workflow_id": registered.workflow_id,
        "revision": _text(payload.get("revision"), 80),
        "exists": bool(payload.get("exists")),
        "cases": len(test_set.get("cases") or []),
        "error": clean(payload.get("error"), 600),
        "model_nodes": [
            {
                "node": _text(item.get("node"), 128),
                "provider": _text(item.get("provider"), 64),
                "model": _text(item.get("model"), 200),
            }
            for item in payload.get("modelNodes") or []
        ][:MAX_MODELS],
        "providers": [
            {
                "id": _text(item.get("id"), 64).replace("_", "-"),
                "models": [_text(model, 200) for model in item.get("models") or []][:100],
            }
            for item in providers
            if item.get("id")
        ][:32],
        "running": bool(payload.get("activeResultId")),
        "result": None,
    }
    if isinstance(latest, dict):
        totals = latest.get("totals") or {}
        variant = latest.get("variant")
        running = latest.get("status") == "running"
        stale = bool(view["revision"]) and latest.get("revision") != payload.get("revision")
        result: dict[str, Any] = {
            "result_id": _text(latest.get("id"), 128),
            "status": _text(latest.get("status"), 20),
            "revision": _text(latest.get("revision"), 80),
            "stale": stale,
            "tried": _text(variant_label(variant), 400),
            "models": [
                {key: _text(item.get(key), 200) for key in ("node", "provider", "model")}
                for item in (variant or {}).get("models") or []
            ][:MAX_MODELS],
            "passed": int(totals.get("passed") or 0),
            "total": int(totals.get("cases") or len(latest.get("cases") or [])),
            "comparison": None,
        }
        comparison = latest.get("modelComparison")
        if isinstance(comparison, dict):
            result["comparison"] = _comparison_view(comparison, running)
        if not variant:
            reason = NOT_A_TRY
        elif running:
            reason = STILL_RUNNING
        elif stale:
            reason = STALE
        else:
            reason = ""
        result["use"] = {"allowed": not reason, "reason": reason}
        view["result"] = result
    return view


def _side(item: dict[str, Any]) -> dict[str, str]:
    return {
        "status": _text(item.get("status"), 20),
        "cost": usd(item.get("costUsd")) if item.get("status") not in {None, "pending"} else "",
        "time": seconds(item.get("durationMs")),
    }


def _total(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": int(item.get("passed") or 0),
        "cases": int(item.get("cases") or 0),
        "cost": usd(item.get("costUsd"), bool(item.get("costComplete"))),
        "time": seconds(item.get("durationMs")),
    }


def _comparison_view(comparison: dict[str, Any], running: bool) -> dict[str, Any]:
    totals = comparison.get("totals") or {}
    return {
        "sentence": "" if running else _text(comparison.get("sentence"), 600),
        "baseline_reused": bool(comparison.get("baselineReused")),
        "rows": [
            {
                "case_id": _text(row.get("id"), 64),
                "name": _text(row.get("name") or row.get("id"), 200),
                "change": _text(row.get("change"), 20),
                "current": _side(row.get("current") or {}),
                "candidate": _side(row.get("candidate") or {}),
            }
            for row in comparison.get("cases") or []
        ][:MAX_CASES],
        "totals": {
            "current": _total(totals.get("current") or {}),
            "candidate": _total(totals.get("candidate") or {}),
        },
    }


def start_try(registered: Any, base: Path, params: dict[str, Any]) -> str:
    """Start a try the phone confirmed; returns its result ID."""
    from gofer.rattish.test_sets import WorkflowTestError
    from gofer.ui.workflow_tests import start_workflow_tests_payload

    if params.get("confirmed") is not True:
        raise Refused("invalid", CONFIRM_TRY)
    revision = params.get("expected_revision")
    if not isinstance(revision, str) or not revision:
        raise Refused("invalid", "Refresh the tests, then try again.")
    models = models_param(params.get("models"))
    try:
        started = start_workflow_tests_payload(
            registered.workflow_id,
            base,
            {"models": [item.to_json() for item in models], "expectedRevision": revision},
        )
    except WorkflowTestError as exc:
        code = "conflict" if "changed since" in str(exc) else "invalid"
        raise Refused(code, _text(exc, 600)) from None
    return _text(started["result"]["id"], 128)


def use_tried_model(registered: Any, base: Path, params: dict[str, Any]) -> str:
    """Write the tried models into the workflow through the editor; returns the message."""
    from gofer.rattish.artifacts import RattishArtifactError
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.model_overrides import ModelOverrideError, use_model

    revision = params.get("expected_revision")
    if not isinstance(revision, str) or not revision:
        raise Refused("invalid", "Refresh the tests, then try again.")
    models = models_param(params.get("models"))
    try:
        result = use_model(registered.entrypoint, models, data_dir=base, expected_revision=revision)
    except ModelOverrideError as exc:
        code = "conflict" if "changed since" in str(exc) else "invalid"
        raise Refused(code, _text(exc, 600)) from None
    except (RattishError, RattishArtifactError, OSError) as exc:
        raise Refused("invalid", _text(exc, 600)) from None
    changed = "; ".join(
        f"{item['node']} now uses {item['provider']} {item['model']}" for item in result["models"]
    )
    return f"{changed}. Triggers on this workflow ask for approval again."
