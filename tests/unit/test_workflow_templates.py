from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.executor import WorkflowExecutor
from gofer.core.operations import BashCommandOperation
from gofer.core.templates import (
    create_workflow_from_template,
    list_workflow_templates,
    preview_workflow_template,
)
from gofer.core.workflow import AgenticWorkflow
from gofer.ui.api import create_workflow_payload, list_workflow_templates_payload
from tests.conftest import FakeSubscription


@pytest.fixture
def review_repository(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, timeout=10
        )

    git("init")
    git("config", "user.name", "Template test")
    git("config", "user.email", "template@example.test")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "example.txt").write_text("before\n")
    git("add", "example.txt")
    git("commit", "-m", "Initial")
    (tmp_path / "example.txt").write_text("after\n")
    git("commit", "-am", "Change")
    return tmp_path


@pytest.mark.parametrize(
    "reference",
    [
        "HEAD; echo compromised > injected.txt",
        "HEAD\necho compromised > injected.txt",
        "$(echo compromised > injected.txt)",
        "`echo compromised > injected.txt`",
        "--output=injected.txt",
    ],
)
async def test_code_review_template_rejects_shell_and_git_option_injection(
    review_repository: Path, reference: str
) -> None:
    template = create_workflow_from_template("code-review", review_repository)
    provider = FakeSubscription()
    result = await WorkflowExecutor(
        template.workflow, {"codex": provider}, workflow_path=template.path
    ).with_parameters({"diff_ref": reference}).run()

    assert not result.success
    assert not (review_repository / "injected.txt").exists()
    assert not provider.calls


async def test_code_review_template_preserves_revision_ranges(
    review_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "raticode-unavailable-external-diff")
    (review_repository / ".gitattributes").write_text("example.txt diff=review\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(review_repository),
            "config",
            "diff.review.textconv",
            "raticode-unavailable-textconv",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    template = create_workflow_from_template("code-review", review_repository)
    provider = FakeSubscription()
    result = await WorkflowExecutor(
        template.workflow, {"codex": provider}, workflow_path=template.path
    ).with_parameters({"diff_ref": "HEAD~1..HEAD"}).run()

    assert result.success
    assert "-before" in result.node_outputs["collect-diff"].output
    assert "+after" in result.node_outputs["collect-diff"].output
    assert len(provider.calls) == 1


def test_code_review_template_uses_powershell_environment_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gofer.core.templates.sys.platform", "win32")
    template = create_workflow_from_template("code-review", tmp_path)
    operation = template.workflow.graph.nodes_in_order()[0].operation
    assert isinstance(operation, BashCommandOperation)
    assert '"$env:GOFER_DIFF_REF"' in operation.command
    assert operation.env == {"GOFER_DIFF_REF": "{{params.diff_ref}}"}


def test_all_workflow_templates_generate_valid_workflows(tmp_path: Path) -> None:
    templates = list_workflow_templates()

    assert {item.name for item in templates} == {
        "code-review",
        "daily-report",
        "file-watcher",
        "local-vector-search",
        "markdown-folder-summary",
        "retry-review-loop",
    }

    for template in templates:
        result = create_workflow_from_template(template.name, tmp_path)
        loaded = AgenticWorkflow.from_file(result.path)

        loaded.validate(result.path, tmp_path)
        assert loaded.config.id == result.workflow.config.id
        assert result.path.exists()
        assert len(loaded.graph.nodes_in_order()) >= 1
        assert result.created_paths[0] == result.path
        assert all(path.exists() for path in result.created_paths)


def test_template_creation_uses_unique_workflow_ids_and_prompt_paths(tmp_path: Path) -> None:
    first = create_workflow_from_template("code-review", tmp_path, workflow_name="Review")
    second = create_workflow_from_template("code-review", tmp_path, workflow_name="Review")

    assert first.workflow.config.id == "review"
    assert second.workflow.config.id == "review-2"
    assert first.path.name == "review.toml"
    assert second.path.name == "review-2.toml"
    assert (tmp_path / "prompts" / "review" / "code-review.md").exists()
    assert (tmp_path / "prompts" / "review-2" / "code-review.md").exists()


def test_template_preview_reports_inputs_nodes_and_provider_assumptions() -> None:
    preview = preview_workflow_template("markdown-folder-summary")

    assert preview.name == "markdown-folder-summary"
    assert preview.required_inputs[0]["name"] == "folder"
    assert any(node["type"] == "loop" for node in preview.generated_nodes)
    assert preview.provider_assumptions == [{"agentId": "summarizer", "subscription": "codex"}]


def test_ui_api_lists_and_creates_template_workflow(tmp_path: Path) -> None:
    listed = list_workflow_templates_payload()

    assert any(item["name"] == "file-watcher" for item in listed["templates"])

    payload = create_workflow_payload(
        "Incoming Files",
        tmp_path,
        template="file-watcher",
    )

    assert payload["id"] == "incoming-files"
    assert payload["watch"]["path"] == "inputs/watch"
    assert [node["id"] for node in payload["nodes"]] == ["changed-files", "process-file"]


def test_cli_lists_and_creates_template_workflow(tmp_path: Path) -> None:
    runner = CliRunner()

    listed = runner.invoke(app, ["workflow", "create", "--list-templates"])
    created = runner.invoke(
        app,
        [
            "workflow",
            "create",
            "--template",
            "local-vector-search",
            "--name",
            "Search Docs",
            "--output",
            str(tmp_path),
        ],
    )

    assert listed.exit_code == 0
    assert "local-vector-search" in listed.output
    assert created.exit_code == 0
    workflow = AgenticWorkflow.from_file(tmp_path / "search-docs.toml")
    assert workflow.config.id == "search-docs"
    assert (tmp_path / "prompts" / "search-docs" / "answer-from-search.md").exists()
