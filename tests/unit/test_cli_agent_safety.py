"""Agent turns can't act with the user's authority through the CLI or a limited token.

Regression tests for the second CLI sync audit (2026-11-10):

- H1: the owner token doesn't reach Rem workers through RATICODE_TOKEN or
  RATICODE_TOKEN_FILE, and the client refuses any non-agent token inside an agent turn.
- H2: the legacy ``workflow approve`` and ``reject`` refuse inside an agent turn (exit 5)
  and ``--by`` can only name one of the gate's approvers.
- H3: agent tokens may only make the writes on an allow-list.
- M1: ``Raticode api`` refuses user-only requests inside an agent turn.

Everything runs on scratch data folders with no provider and no backend, except where a
test says so. No request leaves the process: the refusals happen before connecting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.client import (
    AGENT_SESSION_ENVS,
    AGENT_TOKEN_PREFIX,
    CliError,
    ExitCode,
    discover_endpoint,
    write_api_files,
)
from gofer.cli.commands.api import USER_ONLY_ORG_ACTIONS, agent_api_refusal
from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.ui import headless
from gofer.ui.headless import AGENT_SECOND_BRAIN_ACTIONS, agent_request_refusal
from gofer.utils.process import CREDENTIAL_ENV_VARS, build_subprocess_env

ROOT = Path(__file__).resolve().parents[2]
OWNER = "owner-token-0123456789abcdef0123456789"
AGENT = AGENT_TOKEN_PREFIX + "0123456789abcdef0123456789"
CLEARED_ENV = (
    "RATICODE_URL",
    "RATICODE_SOCKET",
    "RATICODE_TOKEN",
    "RATICODE_TOKEN_FILE",
    "RATICODE_DATA_DIR",
    "GOFER_DATA_DIR",
    "GOFER_UI_API_TOKEN",
    "RATICODE_NON_INTERACTIVE",
    *AGENT_SESSION_ENVS,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests run inside Rem sessions too, which set these; each test starts without them.
    for name in CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)


def _private(path: Path, text: str) -> Path:
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


# --- H1: the owner token never reaches a worker ---------------------------------------


def test_child_processes_drop_raticode_api_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", OWNER)
    monkeypatch.setenv("RATICODE_TOKEN_FILE", "/home/user/.config/raticode-token")
    env = build_subprocess_env()
    assert "RATICODE_TOKEN" not in env
    assert "RATICODE_TOKEN_FILE" not in env
    assert env["RATICODE_AGENT_SESSION"] == "1"
    # Not even an explicit override or a copied parent env passes them on.
    copied = build_subprocess_env(
        {"RATICODE_TOKEN": OWNER, "RATICODE_TOKEN_FILE": "/x"}, inherit_parent=False
    )
    assert not {"RATICODE_TOKEN", "RATICODE_TOKEN_FILE"} & set(copied)
    assert {"RATICODE_TOKEN", "RATICODE_TOKEN_FILE", "GOFER_UI_API_TOKEN"} <= set(
        CREDENTIAL_ENV_VARS
    )


@pytest.mark.parametrize("marker", AGENT_SESSION_ENVS)
def test_agent_turns_refuse_an_owner_token_from_any_source(tmp_path: Path, marker: str) -> None:
    owner_file = _private(tmp_path / "owner", OWNER)
    agent_file = _private(tmp_path / "agent", AGENT)
    session = {marker: "1"}
    for env, token_file in (
        ({**session, "RATICODE_TOKEN": OWNER}, None),
        ({**session, "RATICODE_TOKEN_FILE": str(owner_file)}, None),
        (session, owner_file),
    ):
        with pytest.raises(CliError) as refused:
            discover_endpoint(data_dir=tmp_path, env=env, token_file=token_file)
        assert refused.value.exit_code == ExitCode.AUTH
        assert refused.value.code == "owner_token_in_agent_session"
        assert OWNER not in str(refused.value)

    # Agent tokens still work from each source.
    for env, token_file in (
        ({**session, "RATICODE_TOKEN": AGENT}, None),
        ({**session, "RATICODE_TOKEN_FILE": str(agent_file)}, None),
        (session, agent_file),
    ):
        assert discover_endpoint(data_dir=tmp_path, env=env, token_file=token_file).token == AGENT


@pytest.mark.parametrize("marker", AGENT_SESSION_ENVS)
def test_agent_turns_never_discover_the_published_owner_token(tmp_path: Path, marker: str) -> None:
    write_api_files(tmp_path, url="http://127.0.0.1:9", token=OWNER)
    assert discover_endpoint(data_dir=tmp_path, env={}).token == OWNER
    found = discover_endpoint(data_dir=tmp_path, env={marker: "1"})
    assert found.token == "" and found.token_source == "none"


def test_the_user_can_still_export_a_token(tmp_path: Path) -> None:
    owner_file = _private(tmp_path / "owner", OWNER)
    assert discover_endpoint(data_dir=tmp_path, env={"RATICODE_TOKEN": OWNER}).token == OWNER
    found = discover_endpoint(data_dir=tmp_path, env={"RATICODE_TOKEN_FILE": str(owner_file)})
    assert found.token == OWNER


def test_client_and_backend_agree_on_the_agent_token_prefix() -> None:
    assert AGENT_TOKEN_PREFIX == headless.AGENT_TOKEN_PREFIX
    assert headless.AGENT_SESSION_ENV in AGENT_SESSION_ENVS


# --- H2: legacy workflow approve/reject ---------------------------------------------


def _gate(data_dir: Path, **fields: Any) -> ApprovalStore:
    store = ApprovalStore(data_dir)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="demo",
            run_id="run-1",
            node_id="gate",
            message="Ship it?",
            **fields,
        )
    )
    return store


def _decide(data_dir: Path, verb: str, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "workflow",
            verb,
            "run-1",
            "gate",
            "--workflow",
            "demo",
            *extra,
            "--data-dir",
            str(data_dir),
        ],
    )


@pytest.mark.parametrize("verb", ["approve", "reject"])
@pytest.mark.parametrize("marker", AGENT_SESSION_ENVS)
def test_legacy_decisions_are_refused_inside_agent_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str, marker: str
) -> None:
    store = _gate(tmp_path)
    monkeypatch.setenv(marker, "1")
    result = _decide(tmp_path, verb, "--by", "user")
    assert result.exit_code == 5, result.output
    assert "Only you can" in result.stderr
    request = store.get("demo", "run-1", "gate")
    assert request is not None
    assert request.status == "pending" and request.decision is None


def test_legacy_by_must_name_one_of_the_gates_approvers(tmp_path: Path) -> None:
    store = _gate(tmp_path)
    # A gate that lists no approvers takes no --by, so "user" can't be claimed.
    for claimed in ("user", "gofer", "raticode"):
        result = _decide(tmp_path, "approve", "--by", claimed)
        assert result.exit_code == 1, result.output
        assert "--by" in result.stderr
    request = store.get("demo", "run-1", "gate")
    assert request is not None and request.decision is None

    result = _decide(tmp_path, "approve")
    assert result.exit_code == 0, result.output
    request = store.get("demo", "run-1", "gate")
    assert request is not None and request.decision is not None
    assert request.decision.decided_by == "cli"


def test_legacy_by_picks_a_listed_approver(tmp_path: Path) -> None:
    store = _gate(tmp_path, approvers=["alice", "bob"])
    refused = _decide(tmp_path, "reject", "--by", "mallory")
    assert refused.exit_code == 1, refused.output
    assert "alice, bob" in refused.stderr
    accepted = _decide(tmp_path, "reject", "--by", "bob", "--notes", "Not yet")
    assert accepted.exit_code == 0, accepted.output
    request = store.get("demo", "run-1", "gate")
    assert request is not None and request.decision is not None
    assert (request.decision.decision, request.decision.decided_by) == ("rejected", "bob")


def test_legacy_approve_of_a_rattish_gate_does_not_resume_it_as_toml(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text("Rattish: 1\n", encoding="utf-8")
    store = _gate(tmp_path, workflow_path=str(source))
    result = _decide(tmp_path, "approve")
    assert result.exit_code == 0, result.output
    assert "resume failed" not in result.output
    assert "rattish resume demo run-1" in result.stderr
    request = store.get("demo", "run-1", "gate")
    assert request is not None and request.decision is not None
    assert request.decision.decision == "approved"


# --- H3: what a limited agent token may write ----------------------------------------

REFUSED_WRITES = [
    ("PUT", "/api/workflows/w"),
    ("DELETE", "/api/workflows/w"),
    ("POST", "/api/workflows"),
    ("POST", "/api/workflows/w/document/save"),
    ("POST", "/api/workflows/w/document/mutate"),
    ("POST", "/api/workflows/w/metadata"),
    ("POST", "/api/workflows/w/run"),
    ("POST", "/api/workflows/w/plan"),
    ("POST", "/api/workflows/w/queue"),
    ("POST", "/api/workflows/w/runs/r/resume"),
    ("POST", "/api/workflows/w/history/h/restore"),
    ("POST", "/api/workflows/w/validate/fix"),
    ("POST", "/api/workflows/import"),
    ("POST", "/api/rattish/workflows/import"),
    ("POST", "/api/queue/q/cancel"),
    ("POST", "/api/rattish/triggers"),
    ("POST", "/api/rattish/triggers/t1"),
    ("DELETE", "/api/rattish/triggers/t1"),
    ("POST", "/api/rattish/triggers/t1/rotate-token"),
    ("POST", "/api/rattish/triggers/login"),
    ("POST", "/api/swarms"),
    ("PUT", "/api/swarms/s1"),
    ("DELETE", "/api/swarms/s1"),
    ("POST", "/api/swarms/s1/start"),
    ("POST", "/api/swarms/s1/control"),
    ("POST", "/api/swarms/s1/messages"),
    ("POST", "/api/usage/refresh"),
    ("POST", "/api/retention"),
    ("POST", "/api/rem-resources"),
    ("POST", "/api/provider/settings"),
    ("POST", "/api/generation-jobs"),
    ("POST", "/api/rem/messages"),
    ("POST", "/api/rem/kanban/ticket"),
    ("POST", "/api/chat/stream"),
    ("POST", "/api/workflows/w/approvals/r/n/approve"),
    # A route nobody has classified yet stays closed.
    ("POST", "/api/some-new-route"),
    ("PATCH", "/api/organizations"),
    ("POST", "/api/organizations/x"),
    ("POST", "/api/workflows/a/b/validate"),
]

ALLOWED = [
    ("GET", "/api/workflows"),
    ("GET", "/api/swarms"),
    ("GET", "/api/rattish/triggers"),
    ("POST", "/api/rem/kanban/timeline"),
    ("POST", "/api/workflows/w/validate"),
    ("POST", "/api/workflows/w/document/analyze"),
    ("POST", "/api/workflows/import/preview"),
    ("POST", "/api/rattish/workflows/import/preview"),
    ("POST", "/api/rattish/triggers/preview"),
    ("POST", "/api/organizations"),
    ("POST", "/api/second-brains"),
]


@pytest.mark.parametrize(("method", "path"), REFUSED_WRITES)
def test_agent_tokens_cant_write_outside_the_allow_list(method: str, path: str) -> None:
    assert agent_request_refusal(method, path) is not None


@pytest.mark.parametrize(("method", "path"), ALLOWED)
def test_agent_tokens_can_read_and_make_listed_writes(method: str, path: str) -> None:
    assert agent_request_refusal(method, path) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/session",
        "/api/devices",
        "/api/desktop/trusted-roots",
        "/api/rem/context",
        "/api/rem-resources",
    ],
)
def test_agent_tokens_cant_even_read_owner_only_routes(path: str) -> None:
    assert agent_request_refusal("GET", path) is not None


def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(
        (ROOT / "docs" / "cli-parity.json").read_text(encoding="utf-8")
    )
    return manifest


def _concrete(route: str) -> tuple[str, str, str | None]:
    key, _, action = route.partition("#")
    method, path = key.split(" ", 1)
    return method, path.replace("*", "x"), action or None


def test_every_known_write_route_is_classified_for_agent_tokens() -> None:
    """A new backend write is refused to agent tokens unless someone allows it here."""
    allowed_paths = {path for _method, path in ALLOWED}
    for route in _manifest()["routes"]:
        method, path, _ = _concrete(route)
        if method in {"GET", "HEAD", "OPTIONS"}:
            continue
        refusal = agent_request_refusal(method, path)
        if path in allowed_paths or path in {
            "/api/workflows/x/validate",
            "/api/workflows/x/document/analyze",
        }:
            continue
        # Webhook ingress has its own per-trigger token and never reaches this check.
        if "/webhooks/" in path or path.startswith("/api/organization-webhooks/"):
            continue
        assert refusal is not None, route


# --- M1: `Raticode api` from inside an agent turn --------------------------------------

USER_ONLY_LABELS = {"org-run-control", "consent-gated", "runs-agents", "credentials"}


def test_api_guard_covers_every_user_only_org_action_in_the_manifest() -> None:
    routes = _manifest()["routes"]
    labelled = {
        route.partition("#")[2]
        for route, entry in routes.items()
        if route.startswith("POST /api/organizations#")
        and entry.get("excluded") in USER_ONLY_LABELS
    }
    assert labelled, "the manifest should still list organization exclusions"
    assert labelled <= USER_ONLY_ORG_ACTIONS
    # Rem-level reads and edits stay available to an agent token through `api`.
    for action in ("list", "read", "tasks", "task_create", "comment", "events"):
        assert agent_api_refusal("POST", "/organizations", {"action": action}, {}) is None


def test_api_guard_covers_every_excluded_user_only_route() -> None:
    for route, entry in _manifest()["routes"].items():
        if entry.get("excluded") not in USER_ONLY_LABELS | {"desktop-auth", "pairing"}:
            continue
        method, path, action = _concrete(route)
        body = {"action": action} if action else {}
        assert agent_api_refusal(method, path, body, {}) is not None, route


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (
            ["POST", "/organizations", "-f", "action=control", "-F", 'params.state="running"'],
            "Organizations tab",
        ),
        (["POST", "/organizations", "-f", "action=request_decide"], "Organizations tab"),
        (["POST", "/organizations", "-f", "action=workflow_launch"], "Organizations tab"),
        (["POST", "/api/organizations", "-d", '{"action": "resume_all"}'], "Organizations tab"),
        (["POST", "/second-brains", "-f", "action=mutate"], "Second Brain"),
        (["POST", "/swarms/s1/start", "-f", "projectRoot=/srv"], "swarms"),
        (["POST", "/workflows/w/run"], "workflows"),
        (["POST", "/workflows/w/approvals/r/n/approve"], "approve"),
        (["POST", "/rem/kanban/ticket", "-f", "action=approve"], "Rem"),
        (["GET", "/session"], "owner"),
    ],
)
def test_api_refuses_user_only_requests_inside_agent_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], needle: str
) -> None:
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    monkeypatch.setenv("RATICODE_TOKEN", AGENT)
    # Nothing listens here, so a request that got sent would exit 6, not 5.
    monkeypatch.setenv("RATICODE_URL", "http://127.0.0.1:9")
    result = runner.invoke(app, ["api", *args, "--data-dir", str(tmp_path)])
    assert result.exit_code == 5, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "user_only"
    assert needle in payload["error"]["message"]


def test_api_still_sends_agent_safe_requests_inside_agent_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    monkeypatch.setenv("RATICODE_TOKEN", AGENT)
    monkeypatch.setenv("RATICODE_URL", "http://127.0.0.1:9")
    for args in (
        ["GET", "/workflows"],
        ["POST", "/organizations", "-f", "action=list"],
        ["POST", "/second-brains", "-f", "action=search"],
    ):
        result = runner.invoke(app, ["api", *args, "--data-dir", str(tmp_path)])
        # Sent, and nothing answered.
        assert result.exit_code == 6, (args, result.output)


def test_api_is_unchanged_for_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", OWNER)
    monkeypatch.setenv("RATICODE_URL", "http://127.0.0.1:9")
    result = runner.invoke(
        app, ["api", "POST", "/organizations", "-f", "action=list", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 6, result.output


def test_second_brain_reads_for_agents_are_reads() -> None:
    assert not {"mutate", "task", "remove", "open", "update_brain", "cancel"} & set(
        AGENT_SECOND_BRAIN_ACTIONS
    )
