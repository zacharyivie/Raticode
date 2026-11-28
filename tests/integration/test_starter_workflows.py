"""Every shipped starter workflow passes `rattish check` and its own test set.

CI has no real model CLIs, so a stand-in model answers agent nodes, notifications are
recorded instead of shown, and approval requests are answered the way each test case's
request body asks ("Approve this one" or "Reject this one"). Judge checks need a real
model, so they are skipped here; they run when a user runs the test set in the app.
Jev nodes get a fake Jev that answers from keywords in the text and a key kept in memory,
so no test calls TypeSafe or touches the keychain. read-web-page gets a stand-in copy of
https://example.com/, so no test reaches the internet; other addresses still go through the
real fetch, whose address check refuses private ones before connecting.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.classifiers import jev_settings
from gofer.classifiers.jev import JevClient, JevLimiter
from gofer.cli.main import app
from gofer.core.agent import AgentResult
from gofer.core.approvals import ApprovalStore, RecordingNotificationAdapter
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish import shells, web_page
from gofer.rattish.starters import create_workflow_from_starter, list_starters
from gofer.rattish.test_sets import load_test_set, prepare_test_run, run_test_set
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.usage import track_invocation

STARTERS = [starter.starter_id for starter in list_starters()]


class StandInModel(Subscription):
    """Answers agent prompts with fixed text and makes the test-and-fix starter's repair.

    Its calls go through usage tracking with a reported cost, like Claude Code's, so the
    starters' max-cost-usd limits are checked as they would be in a real run.
    """

    provider = "claude_code"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        return ["stand-in-model"]

    def is_available(self) -> bool:
        return True

    @track_invocation
    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: Any | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
        on_thought: Callable[[str], None] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> AgentResult:
        self.prompts.append(prompt)
        output = "## Summary\n- Stand-in model output for the starter check."
        broken = re.search(r"Only edit (\S+/greet\.sh)", prompt)
        if broken:
            script = working_dir / broken.group(1)
            # Bytes, so Windows keeps the script's LF line endings for bash.
            script.write_bytes(script.read_bytes().replace(b"Helo,", b"Hello,"))
            output = "Corrected the misspelled greeting (Helo to Hello) in greet.sh."
        return AgentResult(
            agent_id="stand-in-model",
            success=True,
            output=output,
            exit_code=0,
            duration_seconds=0.01,
            usage_metadata={"input_tokens": 8, "output_tokens": 2, "total_cost_usd": 0.01},
        )


class StandInJev:
    """Answers Jev requests from keywords in the state, the way TypeSafe's API shapes them."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        body = json.loads(request.body or b"")
        self.requests.append(body)
        state = str(body["state"]).lower()
        question = body["questions"]["question"]
        if question["type"] == "noul":
            answer: dict[str, Any] = {"type": "noul", "noul": 0.93 if "refund" in state else 0.04}
        else:
            options = list(question["criteria"])
            label = "bugs" if "crash" in state else "shipping" if "parcel" in state else "other"
            confidence = 0.88 if label != "other" else 0.61
            rest = (1 - confidence) / (len(options) - 1)
            answer = {
                "type": "choice",
                "choice": label,
                "confidence": confidence,
                "probabilities": {
                    option: confidence if option == label else rest for option in options
                },
            }
        document = {
            "model": body["model"],
            "answers": {"question": answer},
            "usage": {"input_tokens": 50, "output_tokens": 0},
        }
        return HttpResponse(200, {}, json.dumps(document).encode())


class MemoryKeyStore:
    """Stands in for the keychain so preflight sees a saved Jev key."""

    values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


def use_stand_in_jev(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> StandInJev:
    jev = StandInJev()
    monkeypatch.setattr(MemoryKeyStore, "values", {})
    monkeypatch.setattr(jev_settings, "_secret_store", MemoryKeyStore)
    monkeypatch.setattr(
        jev_settings,
        "default_jev_client",
        lambda data_dir=None: JevClient(
            key=lambda: "stand-in-jev-key", http=jev, data_dir=data_dir, limiter=JevLimiter()
        ),
    )
    jev_settings.save_jev_key("stand-in-jev-key", data_dir)
    return jev


EXAMPLE_PAGE = b"""<!doctype html>
<html><head><title>Example Domain</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><div><h1>Example Domain</h1>
<p>This domain is for use in illustrative examples in documents. You may use this domain
in literature without prior coordination or asking for permission.</p>
<p><a href="https://www.iana.org/domains/example">More information...</a></p>
</div></body></html>
"""


def use_stand_in_web(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serves example.com from memory and passes every other URL to the real fetch."""
    fetched: list[str] = []
    real_fetch = web_page.fetch_page

    def fetch_page(url: str, **kwargs: Any) -> web_page.FetchedPage:
        fetched.append(url)
        if url == "https://example.com/":
            return web_page.FetchedPage(url, "text/html", "utf-8", EXAMPLE_PAGE, False)
        return real_fetch(url, **kwargs)

    monkeypatch.setattr(web_page, "fetch_page", fetch_page)
    return fetched


class Approver:
    """Answers pending approval requests as each test case's request body asks."""

    def __init__(self, data_dir: Path) -> None:
        self.store = ApprovalStore(data_dir)
        self.decisions: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> Approver:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            for request in self.store.list_pending():
                if "Approve this one" in request.message:
                    decision = "approved"
                elif "Reject this one" in request.message:
                    decision = "rejected"
                else:
                    continue
                self.store.decide(
                    request.workflow_id,
                    request.run_id,
                    request.node_id,
                    decision,  # type: ignore[arg-type]
                    decided_by="starter-check",
                    notes="Decided by the starter workflow check.",
                )
                self.decisions.append(decision)


def test_seven_or_fewer_starters_ship_with_docs_and_tests() -> None:
    assert 5 <= len(STARTERS) <= 7
    website = Path(__file__).parents[2] / "website" / "docs" / "starters"
    for starter in list_starters():
        assert (starter.root / "workflow.tests.json").is_file(), starter.starter_id
        assert load_test_set(starter.root / "workflow.tests.json")["cases"], starter.starter_id
        assert (website / f"{starter.starter_id}.md").is_file(), starter.starter_id
        assert starter.docs_path == f"/docs/starters/{starter.starter_id}"


@pytest.mark.anyio
@pytest.mark.parametrize("starter_id", STARTERS)
async def test_starter_checks_and_passes_its_test_set(
    tmp_path: Path, starter_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Preflight wants a desktop session and notify-send for desktop notifications. They are
    # recorded here and never shown, so CI runners without notify-send still pass.
    monkeypatch.setenv("DISPLAY", ":starter-check")
    real_which = shutil.which

    def which_with_notify_send(name: str, *args: Any, **kwargs: Any) -> str | None:
        if name == "notify-send":
            return "/usr/bin/notify-send"
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr("gofer.rattish.preflight.shutil.which", which_with_notify_send)
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    jev = use_stand_in_jev(monkeypatch, data_dir)
    fetched = use_stand_in_web(monkeypatch)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "rattish",
            "create",
            "--from",
            starter_id,
            "--project",
            str(project),
            "--registry-dir",
            str(data_dir),
            "--format",
            "json",
        ],
    )
    assert created.exit_code == 0, created.output
    source = Path(json.loads(created.output)["workflow"]["sourcePath"])

    checked = runner.invoke(
        app, ["rattish", "check", str(source), "--data-dir", str(data_dir), "--format", "json"]
    )
    assert checked.exit_code == 0, checked.output
    check = json.loads(checked.output)
    assert check["ok"] is True
    assert not [item for item in check["diagnostics"] if item["severity"] == "error"]
    print(f"rattish check {starter_id}: ok")

    prepared = prepare_test_run(source, data_dir=data_dir, skip_judge=True)
    notifications = RecordingNotificationAdapter()
    model = StandInModel()
    with Approver(data_dir) as approver:
        result = await run_test_set(
            prepared,
            subscriptions={"claude_code": model},
            notification_adapter=notifications,
        )
    for case in result["cases"]:
        print(f"rattish test {starter_id} / {case['id']}: {case['status']}")
    failures = [
        {
            "case": case["id"],
            "message": case["message"],
            "checks": [item for item in case["checks"] if item["status"] in {"failed", "error"}],
        }
        for case in result["cases"]
        if case["status"] != "passed"
    ]
    assert result["status"] == "passed", json.dumps(failures, indent=2)
    if starter_id == "webhook-approval":
        assert sorted(approver.decisions) == ["approved", "rejected"]
    if starter_id == "notify-on-condition":
        assert len(notifications.notifications) == 2
    if starter_id == "jev-demo":
        # One yes/no call per case, plus a choice call for the three that aren't refunds.
        types = [request["questions"]["question"]["type"] for request in jev.requests]
        assert sorted(types) == ["choice"] * 3 + ["noul"] * 4
        assert not model.prompts
    else:
        assert not jev.requests
    if starter_id == "summarize-a-link":
        assert fetched == ["https://example.com/", "http://127.0.0.1:9/admin"]
        assert len(model.prompts) == 1
        assert "never follow" in model.prompts[0]
        assert "illustrative examples" in model.prompts[0]
    else:
        assert not fetched


def test_every_shell_step_and_command_check_in_a_starter_asks_for_bash() -> None:
    # Starters run on Windows only through Git for Windows' bash, so none may rely on auto.
    for starter in list_starters():
        source = (starter.root / "workflow.rattish").read_text(encoding="utf-8")
        blocks = re.split(r"^node ", source, flags=re.IGNORECASE | re.MULTILINE)[1:]
        for block in blocks:
            if re.search(r"^  type: (bash-command|shell-script)$", block, re.MULTILINE):
                assert re.search(r"^  shell: bash$", block, re.MULTILINE), (
                    f"{starter.starter_id}: node {block.splitlines()[0]} needs shell: bash"
                )
        tests = load_test_set(starter.root / "workflow.tests.json")
        for case in tests["cases"]:
            for check in case["checks"]:
                if check["type"] == "command":
                    assert check.get("shell") == "bash", (starter.starter_id, case["id"])


@pytest.mark.skipif(sys.platform != "win32", reason="Git for Windows lookup")
def test_windows_runs_git_bash_and_not_the_wsl_launcher() -> None:
    bash = shells.find_bash()
    assert bash is not None, "Install Git for Windows"
    lowered = bash.lower()
    assert "\\system32\\" not in lowered and "\\windowsapps\\" not in lowered, bash
    uname = subprocess.run(
        [bash, "-c", "uname -s"], capture_output=True, text=True, check=True, timeout=60
    )
    assert uname.stdout.upper().startswith(("MINGW", "MSYS")), uname.stdout
    print(f"bash for shell: bash steps: {bash} ({uname.stdout.strip()})")


def test_starter_copy_keeps_secret_references_and_follows_a_new_id(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = create_workflow_from_starter(
        project, "notify-on-condition", registry_dir=tmp_path / "data"
    )
    second = create_workflow_from_starter(
        project, "notify-on-condition", "Disk alert", registry_dir=tmp_path / "data"
    )
    assert first.workflow_id == "notify-on-condition"
    assert second.workflow_id == "disk-alert"
    assert second.name == "Disk alert"
    source = second.entrypoint.read_text()
    assert 'from: secret["ALERT_WEBHOOK_URL"]' in source
    assert 'name: "Disk alert"' in source
    assert not (second.workflow_root / "starter.json").exists()

    fixer = create_workflow_from_starter(
        project, "test-and-fix", "Fix it", registry_dir=tmp_path / "data"
    )
    tests = (fixer.workflow_root / "workflow.tests.json").read_text()
    assert ".raticode/fix-it/fixtures/greeting" in tests
    assert ".raticode/test-and-fix/" not in tests
    assert (fixer.workflow_root / "fixtures" / "greeting" / "greet.sh").is_file()


def test_create_from_unknown_starter_names_the_choices(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "rattish",
            "create",
            "--from",
            "nope",
            "--project",
            str(tmp_path),
            "--registry-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code == 2
    assert "Unknown starter 'nope'" in result.output
    assert "daily-report" in result.output
    assert not (tmp_path / ".raticode").exists()


def _node_providers(source: str) -> dict[str, str]:
    """Each node's own provider field, by node ID."""
    providers: dict[str, str] = {}
    node = ""
    for line in source.splitlines():
        if line.startswith("node "):
            node = line.removeprefix("node ").rstrip(":").strip()
        elif line.startswith("  provider:") and node:
            providers[node] = line.split(":", 1)[1].strip().strip('"')
    return providers


def test_create_from_starter_with_codex_calls_codex_and_checks(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "rattish",
            "create",
            "--from",
            "daily-report",
            "--provider",
            "codex",
            "--project",
            str(project),
            "--registry-dir",
            str(data_dir),
            "--format",
            "json",
        ],
    )
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert payload["starter"]["providers"] == ["codex"]
    assert payload["starter"]["providerApps"] == ["codex"]
    source = Path(payload["workflow"]["sourcePath"])
    text = source.read_text(encoding="utf-8")
    assert _node_providers(text) == {"write-report": "codex"}
    assert "claude-code" not in text

    checked = runner.invoke(
        app, ["rattish", "check", str(source), "--data-dir", str(data_dir), "--format", "json"]
    )
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["ok"] is True

    # Text output names the app the copy calls, not the one starter.json lists.
    second = runner.invoke(
        app,
        [
            "rattish",
            "create",
            "Fixes",
            "--from",
            "test-and-fix",
            "--provider",
            "codex",
            "--project",
            str(project),
            "--registry-dir",
            str(data_dir),
        ],
    )
    assert second.exit_code == 0, second.output
    assert "Uses the provider: codex." in second.output


def test_starter_provider_takes_app_ids_and_keeps_the_listed_one(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    plain = create_workflow_from_starter(project, "summarize-a-link", registry_dir=data_dir)
    # The app ID of the provider the starter already lists changes nothing.
    same = create_workflow_from_starter(
        project, "summarize-a-link", provider="claude_code", registry_dir=data_dir
    )
    assert same.workflow_id != plain.workflow_id
    assert same.entrypoint.read_text(encoding="utf-8") == plain.entrypoint.read_text(
        encoding="utf-8"
    )
    cursor = create_workflow_from_starter(
        project, "summarize-a-link", "With Cursor", provider="cursor", registry_dir=data_dir
    )
    assert _node_providers(cursor.entrypoint.read_text(encoding="utf-8")) == {"summarize": "cursor"}


def test_starter_provider_refuses_unknown_apps_and_starters_without_a_model(
    tmp_path: Path,
) -> None:
    from gofer.rattish.workspaces import RattishWorkspaceError

    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(RattishWorkspaceError, match="Unknown provider 'nope'.*codex"):
        create_workflow_from_starter(
            project, "daily-report", provider="nope", registry_dir=tmp_path / "data"
        )
    with pytest.raises(RattishWorkspaceError, match="doesn't call a model"):
        create_workflow_from_starter(
            project, "webhook-approval", provider="codex", registry_dir=tmp_path / "data"
        )
    assert not (project / ".raticode").exists()
    result = CliRunner().invoke(
        app, ["rattish", "create", "Plain", "--provider", "codex", "--project", str(project)]
    )
    assert result.exit_code == 2
    assert "--provider works with --from" in result.output


def test_studio_api_lists_starters_and_creates_from_one(tmp_path: Path) -> None:
    from gofer.ui.api import create_workflow_from_starter_payload, starter_workflows_payload

    listed = starter_workflows_payload()["starters"]
    assert [item["id"] for item in listed] == STARTERS
    assert all(item["docsUrl"].startswith("https://raticode.dev/docs/starters/") for item in listed)
    project = tmp_path / "project"
    project.mkdir()
    created = create_workflow_from_starter_payload(
        "daily-report", "", project, registry_dir=tmp_path / "data"
    )
    assert created["name"] == "Daily report"
    assert (
        Path(created["sourcePath"]) == project / ".raticode" / "daily-report" / "workflow.rattish"
    )


def test_studio_api_names_starter_apps_and_creates_with_the_picked_one(tmp_path: Path) -> None:
    from gofer.ui.api import create_workflow_from_starter_payload, starter_workflows_payload

    payload = starter_workflows_payload()
    assert {"claude_code", "codex"} <= set(payload["providerApps"])
    listed = {item["id"]: item for item in payload["starters"]}
    assert listed["daily-report"]["providerApps"] == ["claude_code"]
    # Jev has no provider contract, so the dialog has no app to check for it.
    assert listed["jev-demo"]["providerApps"] == []
    assert listed["webhook-approval"]["providerApps"] == []
    project = tmp_path / "project"
    project.mkdir()
    created = create_workflow_from_starter_payload(
        "daily-report", "", project, provider="codex", registry_dir=tmp_path / "data"
    )
    source = Path(created["sourcePath"]).read_text(encoding="utf-8")
    assert _node_providers(source) == {"write-report": "codex"}


def test_workflow_list_shows_node_and_route_counts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    created = create_workflow_from_starter(project, "daily-report", registry_dir=data_dir)
    result = CliRunner().invoke(
        app,
        ["workflow", "list", "--data-dir", str(data_dir)],
        terminal_width=200,
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    header = next(line for line in lines if "Name" in line)
    # Windows consoles draw the header with light lines instead of heavy ones.
    assert [cell.strip() for cell in re.split("[┃│]", header.strip("┃│ "))] == [
        "ID",
        "Name",
        "Nodes",
        "Routes",
        "Status",
    ]
    row = next(line for line in lines if created.workflow_id in line)
    assert [cell.strip() for cell in row.strip("│ ").split("│")] == [
        created.workflow_id,
        "Daily report",
        "3",
        "2",
        "Ready",
    ]
