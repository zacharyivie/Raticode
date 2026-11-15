"""The jev workflow node: compile rules, runtime, routes, retries and preflight.

Every run uses a fake Jev client; no test calls TypeSafe or reads a real key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.classifiers import jev_settings
from gofer.classifiers.jev import JevClient, JevLimiter
from gofer.core.http import HttpRequest, HttpResponse
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.rattish.workflow_runtime import execute_workflow

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"


class FakeJev:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def noul(score: float) -> HttpResponse:
    body = {
        "model": "jev-1.14.0",
        "answers": {"question": {"type": "noul", "noul": score}},
        "usage": {"input_tokens": 40, "output_tokens": 0},
    }
    return HttpResponse(200, {}, json.dumps(body).encode())


def choice(label: str, confidence: float) -> HttpResponse:
    body = {
        "model": "jev-1.14.0",
        "answers": {
            "question": {
                "type": "choice",
                "choice": label,
                "confidence": confidence,
                "probabilities": {"billing": 1 - confidence, "bugs": confidence}
                if label == "bugs"
                else {"billing": confidence, "bugs": 1 - confidence},
            }
        },
        "usage": {"input_tokens": 40, "output_tokens": 0},
    }
    return HttpResponse(200, {}, json.dumps(body).encode())


def fake_client(fake: FakeJev, data_dir: Path) -> JevClient:
    return JevClient(key=lambda: "fake-key", http=fake, data_dir=data_dir, limiter=JevLimiter())


def compile_source(source: str, project_root: Path) -> Any:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    return compiler.compile(source, CompileContext("jev", project_root))


TRIAGE = """Rattish: 1
Workflow:
  name: Refund triage
  inputs:
    message:
      schema: {"type": "string"}
      required: true
Node triage:
  type: jev
  question: Does the customer ask for their money back?
  criteria:
    yes: The writer asks for a refund.
    no: Anything that doesn't ask for money back.
  threshold: 0.6
  retry-count: RETRIES
  retry-delay: 1ms
  with:
    state: input.message
  to:
    - refund when node.triage.output.label == "yes"
    - general otherwise
Node refund:
  type: bash-command
  needs: triage
  command: echo refund
Node general:
  type: bash-command
  needs: triage
  command: echo general
"""


def triage(project: Path, retries: int = 0) -> Any:
    return compile_source(TRIAGE.replace("RETRIES", str(retries)), project).ir


@pytest.fixture
def use_fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(fake: FakeJev) -> None:
        monkeypatch.setattr(
            jev_settings, "default_jev_client", lambda data_dir=None: fake_client(fake, data_dir)
        )

    return install


async def test_yes_and_otherwise_routes_follow_the_label(tmp_path: Path, use_fake: Any) -> None:
    for score, ran, skipped in ((0.9, "refund", "general"), (0.2, "general", "refund")):
        fake = FakeJev(noul(score))
        use_fake(fake)
        result = await execute_workflow(
            triage(tmp_path),
            workflow_inputs={"message": "Please refund my order"},
            data_dir=tmp_path,
        )
        assert result.outcome == "pass", result.error
        started = {record.node_id for record in result.runs}
        assert ran in started and skipped not in started
        output = result.latest_node_outputs["triage"]
        assert output["label"] == ("yes" if score >= 0.6 else "no")
        assert output["score"] == score
        assert output["threshold"] == 0.6
        assert output["question_type"] == "yes-no"
        assert output["requested_model"] == output["model"] == "jev-1.14.0"
        assert output["input_tokens"] == 40
        sent = json.loads(fake.requests[0].body or b"")
        assert sent["state"] == "Please refund my order"
        assert sent["questions"]["question"]["criteria"] == {
            "true": "The writer asks for a refund.",
            "false": "Anything that doesn't ask for money back.",
        }


async def test_choice_node_routes_on_option_and_score(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Route a message
Node topic:
  type: jev
  question-type: CHOICE
  question: Which team should handle this?
  options:
    billing: Payments and invoices
    bugs: Something is broken
  state: The app crashes on start.
  to:
    - bugs when node.topic.output.label == "bugs" and node.topic.output.score >= 0.7
Node bugs:
  type: bash-command
  needs: topic
  command: echo bugs
"""
    ir = compile_source(source, tmp_path).ir
    fake = FakeJev(choice("bugs", 0.85))

    result = await execute_node(
        ir, "topic", data_dir=tmp_path, jev_client=fake_client(fake, tmp_path)
    )

    assert result.outcome == "success", result.error
    assert result.output["label"] == "bugs"
    assert result.output["score"] == 0.85
    assert result.output["threshold"] is None
    assert result.output["question_type"] == "choice"
    assert json.loads(fake.requests[0].body or b"")["questions"]["question"]["criteria"] == {
        "billing": "Payments and invoices",
        "bugs": "Something is broken",
    }


async def test_non_retryable_errors_skip_retry_count(tmp_path: Path, use_fake: Any) -> None:
    fake = FakeJev(HttpResponse(401, {}, b"no"))
    use_fake(fake)

    result = await execute_workflow(
        triage(tmp_path, retries=2), workflow_inputs={"message": "x"}, data_dir=tmp_path
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert len(fake.requests) == 1
    record = next(r for r in result.runs if r.node_id == "triage")
    assert record.result.error is not None
    assert record.result.error.code == "RATTISH_JEV_AUTH"
    assert record.result.error.details["retryable"] is False


async def test_retryable_errors_use_retry_count(tmp_path: Path, use_fake: Any) -> None:
    fake = FakeJev(HttpResponse(503, {}, b""), HttpResponse(500, {}, b""), noul(0.9))
    use_fake(fake)

    result = await execute_workflow(
        triage(tmp_path, retries=2), workflow_inputs={"message": "x"}, data_dir=tmp_path
    )

    assert result.outcome == "pass", result.error
    assert len(fake.requests) == 3


async def test_rate_limit_error_carries_the_wait(tmp_path: Path) -> None:
    ir = triage(tmp_path)
    fake = FakeJev(HttpResponse(429, {"Retry-After": "90"}, b""))

    result = await execute_node(
        ir,
        "triage",
        workflow_inputs={"message": "x"},
        data_dir=tmp_path,
        jev_client=fake_client(fake, tmp_path),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert (result.error.kind, result.error.code) == ("provider", "RATTISH_JEV_RATE_LIMITED")
    assert result.error.details == {"retryable": True, "status": 429, "retry_after_ms": 90_000}


async def test_empty_state_is_a_configuration_error_and_makes_no_call(tmp_path: Path) -> None:
    ir = triage(tmp_path)
    fake = FakeJev(noul(0.5))

    result = await execute_node(
        ir,
        "triage",
        workflow_inputs={"message": "   "},
        data_dir=tmp_path,
        jev_client=fake_client(fake, tmp_path),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.kind == "configuration"
    assert fake.requests == []


async def test_allow_fail_routes_a_failed_classification(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Fallback
Node check:
  type: jev
  question: Is this spam?
  criteria:
    yes: Unwanted bulk advertising.
    no: Anything else.
  state: Buy now!!!
  allow-fail: true
  to:
    - by-hand when failed
Node by-hand:
  type: bash-command
  needs: check
  command: echo review
"""
    ir = compile_source(source, tmp_path).ir
    result = await execute_node(
        ir,
        "check",
        data_dir=tmp_path,
        jev_client=fake_client(FakeJev(HttpResponse(500, {}, b"")), tmp_path),
    )
    assert result.outcome == "allowed_failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_JEV_UNAVAILABLE"


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ("  question-type: choice\n", "RATTISH_MISSING_FIELD"),
        ("  question-type: choice\n  options:\n    only: One\n", "RATTISH_INVALID_FIELD_VALUE"),
        (
            "  question-type: choice\n  threshold: 0.7\n  options:\n    a: A\n    b: B\n",
            "RATTISH_INVALID_FIELD_VALUE",
        ),
        ("  options:\n    a: A\n    b: B\n", "RATTISH_INVALID_FIELD_VALUE"),
        ("  threshold: 1.5\n", "RATTISH_INVALID_FIELD_VALUE"),
        ("  request-timeout: 2m\n", "RATTISH_INVALID_FIELD_VALUE"),
        ("  url: https://elsewhere.example\n", "RATTISH_UNKNOWN_FIELD"),
    ],
)
def test_compile_rules(tmp_path: Path, fields: str, code: str) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Rules\nNode x:\n  type: jev\n"
        "  question: Is it?\n  state: hi\n" + fields
    )
    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, tmp_path)
    assert code in {item.code for item in caught.value.diagnostics}


def test_missing_criteria_is_a_warning_and_the_model_is_pinned(tmp_path: Path) -> None:
    result = compile_source(
        "Rattish: 1\nWorkflow:\n  name: W\nNode x:\n  type: jev\n  question: Q?\n  state: s\n",
        tmp_path,
    )
    assert [item.code for item in result.diagnostics] == ["RATTISH_JEV_CRITERIA_MISSING"]
    node = result.ir["nodes"][0]
    assert node["configuration"]["model"] == "jev-1.14.0"
    assert node["configuration"]["threshold"] == 0.5
    assert node["configuration"]["request_timeout"] == "15s"
    assert node["resolutions"]["provider"] is None
    assert set(node["effects"]) == {"network", "provider"}


def test_preflight_requires_a_saved_turned_on_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: dict[str, str] = {}

    class Store:
        def get(self, account: str) -> str | None:
            return store.get(account)

        def put(self, account: str, value: str) -> None:
            store[account] = value

        def delete(self, account: str) -> None:
            store.pop(account, None)

    monkeypatch.setattr(jev_settings, "_secret_store", Store)
    ir = triage(tmp_path)

    def codes() -> list[str]:
        return [item.code for item in run_preflight(ir, data_dir=tmp_path).diagnostics]

    assert codes() == ["RATTISH_PREFLIGHT_JEV_NOT_CONFIGURED"]
    jev_settings.save_jev_key("fake-key", tmp_path)
    assert codes() == []
    jev_settings.save_jev_settings({"enabled": False}, tmp_path)
    assert codes() == ["RATTISH_PREFLIGHT_JEV_DISABLED"]


def test_editor_starter_fields_compile(tmp_path: Path) -> None:
    from gofer.rattish.editor import _STARTER_FIELDS, _render_field, _render_json_value

    lines = "".join(
        "\n" + _render_field("  ", field, _render_json_value(value))
        for field, value in _STARTER_FIELDS["jev"].items()
    )
    result = compile_source(
        f"Rattish: 1\nWorkflow:\n  name: Starter\nNode x:\n  type: jev{lines}\n", tmp_path
    )
    assert result.diagnostics == ()
