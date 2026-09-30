from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from gofer.core.agent import AgentConfig
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode
from gofer.core.operations import (
    AgentOperation,
    BashCommandOperation,
    DirectoryFanSource,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    OperationType,
    PassOperation,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.workflow import (
    AgenticWorkflow,
    ScheduleConfig,
    WatchConfig,
    WorkflowConfig,
    WorkflowParameterConfig,
)


class TemplateAsset(BaseModel):
    path: str
    kind: str = "prompt"


class WorkflowTemplatePreview(BaseModel):
    name: str
    title: str
    purpose: str
    version: int = 1
    required_inputs: list[dict[str, Any]] = Field(default_factory=list)
    generated_nodes: list[dict[str, str]] = Field(default_factory=list)
    provider_assumptions: list[dict[str, str]] = Field(default_factory=list)
    assets: list[TemplateAsset] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


@dataclass(frozen=True)
class WorkflowTemplateCreateResult:
    workflow: AgenticWorkflow
    path: Path
    created_paths: list[Path]
    template: WorkflowTemplatePreview


@dataclass(frozen=True)
class _TemplateDefinition:
    name: str
    title: str
    purpose: str
    builder: Callable[[str, str], tuple[AgenticWorkflow, dict[str, str]]]


def list_workflow_templates() -> list[WorkflowTemplatePreview]:
    return [preview_workflow_template(name) for name in sorted(_TEMPLATES)]


def preview_workflow_template(name: str) -> WorkflowTemplatePreview:
    definition = _template_definition(name)
    workflow, assets = definition.builder("template-preview", definition.title)
    return WorkflowTemplatePreview(
        name=definition.name,
        title=definition.title,
        purpose=definition.purpose,
        required_inputs=[
            {"name": param_name, **param.model_dump(mode="json", exclude_none=True)}
            for param_name, param in workflow.config.parameters.items()
            if param.required
        ],
        generated_nodes=[
            {
                "id": node.node_id,
                "type": str(node.operation.type),
                "label": node.label or _title(node.node_id),
            }
            for node in workflow.graph.nodes_in_order()
        ],
        provider_assumptions=[
            {
                "agentId": agent.agent_id,
                "subscription": agent.subscription,
                **({"profile": agent.profile} if agent.profile else {}),
                **({"model": agent.model} if agent.model else {}),
            }
            for agent in workflow.agents.values()
        ],
        assets=[TemplateAsset(path=path, kind="prompt") for path in assets],
    )


def create_workflow_from_template(
    name: str,
    data_dir: Path,
    *,
    workflow_name: str | None = None,
) -> WorkflowTemplateCreateResult:
    definition = _template_definition(name)
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    requested_name = (
        workflow_name.strip() if workflow_name and workflow_name.strip() else definition.title
    )
    workflow_id = _unique_workflow_id(_slugify(requested_name), data_dir)
    if workflow_id != _slugify(requested_name):
        requested_name = f"{requested_name} {workflow_id.rsplit('-', 1)[-1]}"
    workflow, prompt_assets = definition.builder(workflow_id, requested_name)
    workflow_path = data_dir / f"{workflow_id}.toml"

    created_paths: list[Path] = []
    for relative_path, content in prompt_assets.items():
        target = _safe_data_path(data_dir, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created_paths.append(target)

    workflow.validate(workflow_path, data_dir)
    workflow.to_file(workflow_path)
    created_paths.insert(0, workflow_path)
    return WorkflowTemplateCreateResult(
        workflow=workflow,
        path=workflow_path,
        created_paths=created_paths,
        template=preview_workflow_template(name),
    )


def _template_definition(name: str) -> _TemplateDefinition:
    key = _normalize_template_name(name)
    try:
        return _TEMPLATES[key]
    except KeyError as exc:
        choices = ", ".join(sorted(_TEMPLATES))
        raise ValueError(
            f"Unknown workflow template '{name}'. Available templates: {choices}"
        ) from exc


def _normalize_template_name(name: str) -> str:
    return _slugify(name).replace("_", "-")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "workflow"


def _unique_workflow_id(base_id: str, data_dir: Path) -> str:
    candidate = base_id
    suffix = 2
    while (data_dir / f"{candidate}.toml").exists():
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def _safe_data_path(data_dir: Path, relative_path: str) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError("Template asset paths must be relative")
    target = (data_dir / relative_path).resolve()
    if target != data_dir and not target.is_relative_to(data_dir):
        raise ValueError("Template asset path escapes data directory")
    return target


def _title(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").title()


def _agent(agent_id: str, prompt_path: str) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        subscription="codex",
        working_dir=Path("."),
        prompt_path=Path(prompt_path),
        tools=[],
        mcp_servers=[],
        env={},
    )


def _add_agent_node(
    workflow: AgenticWorkflow,
    node_id: str,
    agent_id: str,
    prompt_path: str,
    *,
    label: str,
    inputs: dict[str, str] | None = None,
) -> None:
    workflow.add_operation(
        GraphNode(
            node_id=node_id,
            label=label,
            inputs=inputs or {},
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id=agent_id,
                prompt_path=Path(prompt_path),
                working_dir=Path("."),
            ),
        )
    )


def _workflow_config(
    workflow_id: str,
    workflow_name: str,
    *,
    parameters: dict[str, WorkflowParameterConfig] | None = None,
    schedule: ScheduleConfig | None = None,
    watch: WatchConfig | None = None,
) -> WorkflowConfig:
    return WorkflowConfig(
        id=workflow_id,
        name=workflow_name,
        parameters=parameters or {},
        schedule=schedule,
        watch=watch,
    )


def _code_review_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    prompt_path = f"prompts/{workflow_id}/code-review.md"
    # Values expanded from the environment stay data, including shell syntax.
    # bash-command uses PowerShell on Windows and Bash on other platforms.
    diff_ref = "$env:GOFER_DIFF_REF" if sys.platform == "win32" else "$GOFER_DIFF_REF"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            parameters={
                "diff_ref": WorkflowParameterConfig(
                    type="string",
                    label="Diff reference",
                    description="Git revision or range to compare against HEAD.",
                    required=False,
                    default="HEAD",
                )
            },
        )
    )
    wf.register_agent(_agent("reviewer", prompt_path))
    wf.add_operation(
        GraphNode(
            node_id="collect-diff",
            label="Collect git diff",
            pipe_output=True,
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command=(
                    f'git diff --no-ext-diff --no-textconv --end-of-options "{diff_ref}" --'
                ),
                env={"GOFER_DIFF_REF": "{{params.diff_ref}}"},
                working_dir=Path("."),
            ),
        )
    )
    _add_agent_node(wf, "review-diff", "reviewer", prompt_path, label="Review diff")
    wf.then("collect-diff", "review-diff")
    return wf, {
        prompt_path: """Review this git diff for correctness, maintainability, tests, \
and risky behavior changes.

Diff:

{{collect-diff.output}}

Return findings first, ordered by severity. Include file paths and concrete remediation steps.
""",
    }


def _markdown_summary_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    prompt_path = f"prompts/{workflow_id}/summarize-markdown.md"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            parameters={
                "folder": WorkflowParameterConfig(
                    type="folder",
                    label="Markdown folder",
                    required=True,
                    default="docs",
                )
            },
        )
    )
    wf.register_agent(_agent("summarizer", prompt_path))
    wf.add_operation(
        GraphNode(
            node_id="markdown-files",
            label="Markdown files",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(
                    type="directory",
                    path=Path("{{params.folder}}"),
                    glob="*.md",
                    include_content=True,
                ),
            ),
        )
    )
    _add_agent_node(wf, "summarize-file", "summarizer", prompt_path, label="Summarize file")
    wf.then("markdown-files", "summarize-file")
    return wf, {
        prompt_path: """Summarize this Markdown file for a project knowledge base.

Path: {{path}}
Name: {{name}}

Content:

{{file_content}}

Return a concise summary, key decisions, and follow-up questions.
""",
    }


def _daily_report_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    prompt_path = f"prompts/{workflow_id}/daily-report.md"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            schedule=ScheduleConfig(
                cron_expression="0 9 * * 1-5",
                timezone="UTC",
            ),
            parameters={
                "report_date": WorkflowParameterConfig(
                    type="date",
                    label="Report date",
                    required=False,
                ),
                "topic": WorkflowParameterConfig(
                    type="string",
                    label="Report topic",
                    required=False,
                    default="project status",
                ),
            },
        )
    )
    wf.register_agent(_agent("reporter", prompt_path))
    _add_agent_node(wf, "draft-report", "reporter", prompt_path, label="Draft report")
    wf.add_operation(
        GraphNode(
            node_id="save-report",
            label="Save report",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=Path("reports/{{params.report_date}}.md"),
                content="{{draft-report.output}}",
            ),
        )
    )
    wf.then("draft-report", "save-report")
    return wf, {
        prompt_path: """Draft a daily report.

Date: {{params.report_date}}
Topic: {{params.topic}}

Use concise sections for summary, notable changes, risks, and next actions.
""",
    }


def _file_watcher_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    prompt_path = f"prompts/{workflow_id}/process-new-file.md"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            watch=WatchConfig(
                path=Path("inputs/watch"),
                glob="*",
                recursive=False,
                debounce_seconds=1.0,
                mode="fanout",
                max_concurrency=2,
            ),
        )
    )
    wf.register_agent(_agent("file_processor", prompt_path))
    wf.add_operation(
        GraphNode(
            node_id="changed-files",
            label="Changed files",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TriggerEventsFanSource(
                    type="trigger_events",
                    include_content=True,
                    max_concurrency=2,
                ),
            ),
        )
    )
    _add_agent_node(wf, "process-file", "file_processor", prompt_path, label="Process file")
    wf.then("changed-files", "process-file")
    return wf, {
        prompt_path: """Process this changed file event.

Event kind: {{kind}}
Path: {{path}}
Name: {{name}}

Content:

{{file_content}}

Return the important facts, suggested routing, and any action needed.
""",
    }


def _retry_review_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    draft_prompt = f"prompts/{workflow_id}/draft.md"
    review_prompt = f"prompts/{workflow_id}/review.md"
    revise_prompt = f"prompts/{workflow_id}/revise.md"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            parameters={
                "request": WorkflowParameterConfig(
                    type="multiline",
                    label="Request",
                    required=True,
                    default="Write a short project update.",
                )
            },
        )
    )
    wf.register_agent(_agent("writer", draft_prompt))
    wf.register_agent(_agent("reviewer", review_prompt))
    wf.register_agent(_agent("reviser", revise_prompt))
    _add_agent_node(wf, "draft", "writer", draft_prompt, label="Draft")
    _add_agent_node(
        wf,
        "review",
        "reviewer",
        review_prompt,
        label="Review",
        inputs={"draft": "draft.output"},
    )
    _add_agent_node(
        wf,
        "revise",
        "reviser",
        revise_prompt,
        label="Revise",
        inputs={"draft": "draft.output", "review": "review.output"},
    )
    wf.add_operation(
        GraphNode(
            node_id="approved",
            label="Approved",
            operation=PassOperation(type=OperationType.PASS, message="Review accepted"),
        )
    )
    wf.then("draft", "review")
    wf.then(
        "review",
        "approved",
        EdgeConfig(
            from_node="review",
            to_node="approved",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern=r"(?i)approved|ship",
        ),
    )
    wf.then(
        "review",
        "revise",
        EdgeConfig(
            from_node="review",
            to_node="revise",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern=r"(?i)revise|changes requested",
        ),
    )
    wf.then("revise", "review")
    return wf, {
        draft_prompt: "Draft a response for this request:\n\n{{params.request}}\n",
        review_prompt: """Review the draft below.

Draft:
{{draft}}

Reply with APPROVED if it is ready, or CHANGES REQUESTED with concrete revision notes.
""",
        revise_prompt: """Revise the draft using the review feedback.

Original draft:
{{draft}}

Review:
{{review}}
""",
    }


def _local_search_template(
    workflow_id: str,
    workflow_name: str,
) -> tuple[AgenticWorkflow, dict[str, str]]:
    prompt_path = f"prompts/{workflow_id}/answer-from-search.md"
    index_path = f"indexes/{workflow_id}/local.json"
    wf = AgenticWorkflow(
        _workflow_config(
            workflow_id,
            workflow_name,
            parameters={
                "source_folder": WorkflowParameterConfig(
                    type="folder",
                    label="Source folder",
                    required=True,
                    default="docs",
                ),
                "query": WorkflowParameterConfig(
                    type="string",
                    label="Search query",
                    required=True,
                    default="What are the main topics?",
                ),
            },
        )
    )
    wf.register_agent(_agent("answerer", prompt_path))
    wf.add_operation(
        GraphNode(
            node_id="vectorize",
            label="Vectorize local files",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=Path("{{params.source_folder}}"),
                index_path=Path(index_path),
                glob="**/*.md",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            label="Search index",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=Path(index_path),
                query="{{params.query}}",
                top_k=5,
            ),
        )
    )
    _add_agent_node(
        wf,
        "answer",
        "answerer",
        prompt_path,
        label="Answer from search",
        inputs={"search_results": "search.output"},
    )
    wf.then("vectorize", "search")
    wf.then("search", "answer")
    return wf, {
        prompt_path: """Answer the user's question from the local search results.

Question: {{params.query}}

Search results:
{{search_results}}

Include source paths when useful.
""",
    }


_TEMPLATES: dict[str, _TemplateDefinition] = {
    "code-review": _TemplateDefinition(
        name="code-review",
        title="Code Review From Git Diff",
        purpose="Collect a git diff and ask an agent to review it with findings-first output.",
        builder=_code_review_template,
    ),
    "markdown-folder-summary": _TemplateDefinition(
        name="markdown-folder-summary",
        title="Summarize Markdown Folder",
        purpose="Fan out over Markdown files in a folder and summarize each file.",
        builder=_markdown_summary_template,
    ),
    "daily-report": _TemplateDefinition(
        name="daily-report",
        title="Scheduled Daily Report",
        purpose="Run on a weekday schedule and save a generated report.",
        builder=_daily_report_template,
    ),
    "file-watcher": _TemplateDefinition(
        name="file-watcher",
        title="File Watcher Processor",
        purpose="Watch a folder and fan changed file events into an agent.",
        builder=_file_watcher_template,
    ),
    "retry-review-loop": _TemplateDefinition(
        name="retry-review-loop",
        title="Retry Review Loop",
        purpose="Draft, review, and revise through conditional output matching.",
        builder=_retry_review_template,
    ),
    "local-vector-search": _TemplateDefinition(
        name="local-vector-search",
        title="Local Vectorize And Search",
        purpose="Build a local vector index, search it, and answer from retrieved snippets.",
        builder=_local_search_template,
    ),
}
