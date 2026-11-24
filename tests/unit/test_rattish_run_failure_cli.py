"""`rattish runs failure` against a real backend: what Ask Rem about this failure attaches."""

from __future__ import annotations

from tests.unit.test_cli_api_commands import (  # noqa: F401 - fixtures
    Backend,
    backend,
    clean_environment,
    ok,
    run,
)

FAILING = """Rattish: 1
Workflow:
  name: Breaker
Node check:
  type: bash-command
  command: echo checking; echo broken >&2; exit 3
"""


def test_runs_failure_prints_the_failing_node_for_rem(backend: Backend) -> None:  # noqa: F811
    workflow_id = backend.workflow(FAILING, name="Breaker")
    code, started, _ = run("rattish", "start", workflow_id, "--wait", "--timeout", "60")
    assert code != 0 and started["data"]["status"] == "error", started
    run_id = str(started["data"]["id"])

    failure = ok("rattish", "runs", "failure", run_id)
    assert failure["runId"] == run_id and failure["workflowId"] == workflow_id
    assert failure["nodeId"] == "check"
    assert failure["name"] == f"run-failure-{run_id}-check.txt"
    assert "Node: check (type: bash-command), failed" in failure["text"]
    assert "Exit code: 3" in failure["text"]

    code, text, _ = run(
        "rattish",
        "runs",
        "failure",
        run_id,
        "--node",
        "check",
        "--output",
        "text",
        json_output=False,
    )
    assert code == 0
    assert text.startswith("Raticode workflow run, attached with Ask Rem about this failure.")
    assert "checking\nstderr:\nbroken" in text
    assert "----- END UNTRUSTED RUN OUTPUT" in text

    code, _, _ = run("rattish", "runs", "failure", run_id, "--node", "nope", json_output=False)
    assert code != 0
