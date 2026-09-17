from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, cast

import networkx as nx

try:
    import questionary
except ImportError as exc:
    raise ImportError(
        "questionary is required for the interactive builder: pip install questionary"
    ) from exc

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from gofer.core.agent import AgentConfig
from gofer.core.graph import CycleError, EdgeConditionType, EdgeConfig, GraphNode
from gofer.core.operations import (
    AgentOperation,
    BashCommandOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    FanSource,
    InfiniteFanSource,
    LoopOperation,
    MoveFileOperation,
    OpenResourceOperation,
    Operation,
    OperationType,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.workflow import AgenticWorkflow, ScheduleConfig, WatchConfig, WorkflowConfig
from gofer.utils.agent_helpers import resolve_prompt, unique_agent_id
from gofer.utils.paths import get_data_dir
from gofer.utils.registry import list_all_agents

console = Console()


class WorkflowBuilder:
    def __init__(self) -> None:
        self._workflow: AgenticWorkflow | None = None

    def run(self) -> AgenticWorkflow | None:
        console.print(Panel("[bold]Workflow Builder[/bold]", expand=False))
        config = self._ask_metadata()
        if config is None:
            return None
        self._workflow = AgenticWorkflow(config)
        self._ask_output_schemas()
        self._ask_nodes()
        if len(self._workflow.graph) >= 2:
            self._ask_edges()
        self._preview_dag()
        if not questionary.confirm("Save this workflow?", default=True).ask():
            return None
        return self._workflow

    # ── phases ───────────────────────────────────────────────────────────────

    def _ask_metadata(self) -> WorkflowConfig | None:
        name = questionary.text("Workflow name:").ask()
        if not name:
            return None
        wf_id = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        wf_id = questionary.text("Workflow ID:", default=wf_id).ask()
        if not wf_id:
            return None

        schedule = None
        if questionary.confirm("Add a cron schedule?", default=False).ask():
            cron = questionary.text("Cron expression (e.g. 0 9 * * 1-5):").ask()
            tz = questionary.text("Timezone:", default="UTC").ask()
            if cron:
                schedule = ScheduleConfig(cron_expression=cron, timezone=tz or "UTC")

        watch = None
        if questionary.confirm("Add a file/folder watcher?", default=False).ask():
            path_str = questionary.text("Path to watch:").ask()
            if path_str:
                glob = questionary.text("File pattern:", default="*").ask() or "*"
                recursive = questionary.confirm("Watch folders recursively?", default=False).ask()
                mode = cast(
                    Literal["batch", "queue", "fanout"],
                    questionary.select(
                        "Watcher mode:",
                        choices=["batch", "queue", "fanout"],
                        default="batch",
                    ).ask()
                    or "batch",
                )
                max_concurrency_str = questionary.text(
                    "Max watcher run concurrency:", default="1"
                ).ask()
                debounce_str = questionary.text("Debounce seconds:", default="1.0").ask()
                try:
                    debounce_seconds = float(debounce_str or "1.0")
                except ValueError:
                    debounce_seconds = 1.0
                try:
                    max_concurrency = int(max_concurrency_str or "1")
                except ValueError:
                    max_concurrency = 1
                watch = WatchConfig(
                    path=Path(path_str),
                    glob=glob,
                    recursive=recursive,
                    debounce_seconds=debounce_seconds,
                    mode=mode,
                    max_concurrency=max_concurrency,
                )

        return WorkflowConfig(id=wf_id, name=name, schedule=schedule, watch=watch)

    def _ask_output_schemas(self) -> None:
        assert self._workflow is not None
        while questionary.confirm("Define a named output schema?", default=False).ask():
            name = questionary.text("Schema name:").ask()
            if not name:
                return
            raw_schema = questionary.text("JSON Schema (JSON object):").ask()
            try:
                schema = json.loads(raw_schema or "")
            except json.JSONDecodeError:
                console.print("  [red]✗[/red] Output schema must be valid JSON")
                continue
            if not isinstance(schema, dict):
                console.print("  [red]✗[/red] Output schema must be a JSON object")
                continue
            self._workflow.config.output_schemas[name.strip()] = schema
            console.print(f"  [green]✓[/green] Added output schema '{name.strip()}'")

    def _ask_structured_output(self) -> tuple[str | dict[str, object] | None, int]:
        assert self._workflow is not None
        if not questionary.confirm("Require structured JSON output?", default=False).ask():
            return None, 0

        schema_source = questionary.select(
            "Output schema source:", choices=["named schema", "inline JSON Schema"]
        ).ask()
        if schema_source == "named schema":
            names = sorted(self._workflow.config.output_schemas)
            if not names:
                console.print(
                    "  [yellow]No named schemas are defined; using an inline schema.[/yellow]"
                )
                schema_source = "inline JSON Schema"
            else:
                output_schema = questionary.select("Output schema:", choices=names).ask()
                if output_schema is None:
                    return None, 0
        if schema_source == "inline JSON Schema":
            raw_schema = questionary.text("JSON Schema (JSON object):").ask()
            try:
                output_schema = json.loads(raw_schema or "")
            except json.JSONDecodeError:
                console.print("  [red]✗[/red] Output schema must be valid JSON")
                return None, 0
            if not isinstance(output_schema, dict):
                console.print("  [red]✗[/red] Output schema must be a JSON object")
                return None, 0

        raw_attempts = questionary.text(
            "Structured output repair attempts (0-3):", default="0"
        ).ask()
        try:
            repair_attempts = int(raw_attempts or "0")
        except ValueError:
            repair_attempts = 0
        repair_attempts = min(3, max(0, repair_attempts))
        return output_schema, repair_attempts

    def _ask_nodes(self) -> None:
        console.print("\n[bold]Add nodes[/bold]")
        while True:
            if not questionary.confirm("Add a node?", default=True).ask():
                break
            self._ask_one_node()
        if len(self._workflow.graph) == 0:  # type: ignore[union-attr]
            console.print("[yellow]No nodes added.[/yellow]")

    def _ask_one_node(self) -> None:
        assert self._workflow is not None
        node_id = questionary.text("Node ID:").ask()
        if not node_id:
            return

        node_type = questionary.select(
            "Node type:",
            choices=[
                "bash_command",
                "python_script",
                "shell_script",
                "read_file",
                "write_file",
                "copy_file",
                "move_file",
                "delete_file",
                "open_resource",
                "loop",
                "agent",
            ],
        ).ask()

        node: GraphNode | None = None
        op: Operation

        if node_type == "bash_command":
            command = questionary.text("Command:").ask()
            if not command:
                return
            working_dir_str = questionary.text("Working directory (optional):").ask()
            pipe_output = questionary.confirm("Pipe output to next node?", default=False).ask()
            op = BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command=command,
                working_dir=Path(working_dir_str) if working_dir_str else None,
            )
            node = GraphNode(node_id=node_id, operation=op, pipe_output=pipe_output)

        elif node_type in ("python_script", "shell_script"):
            script_path_str = questionary.text("Script path:").ask()
            if not script_path_str:
                return
            args_str = questionary.text("Arguments (space-separated, optional):").ask()
            args = args_str.split() if args_str else []
            pipe_output = questionary.confirm("Pipe output to next node?", default=False).ask()
            if node_type == "python_script":
                op = PythonScriptOperation(
                    type=OperationType.PYTHON_SCRIPT, script_path=Path(script_path_str), args=args
                )
            else:
                op = ShellScriptOperation(
                    type=OperationType.SHELL_SCRIPT, script_path=Path(script_path_str), args=args
                )
            node = GraphNode(node_id=node_id, operation=op, pipe_output=pipe_output)

        elif node_type == "read_file":
            path_str = questionary.text("File path:").ask()
            if not path_str:
                return
            op = ReadFileOperation(type=OperationType.READ_FILE, path=Path(path_str))
            node = GraphNode(node_id=node_id, operation=op, pipe_output=True)

        elif node_type == "write_file":
            path_str = questionary.text("File path:").ask()
            if not path_str:
                return
            content = questionary.text(
                "Content (leave empty to use piped input):", default=""
            ).ask()
            create_dirs = questionary.confirm("Create parent folders?", default=True).ask()
            overwrite = questionary.confirm("Overwrite existing file?", default=True).ask()
            append = questionary.confirm("Append instead of replace?", default=False).ask()
            op = WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=Path(path_str),
                content=content or "",
                create_dirs=create_dirs,
                overwrite=overwrite,
                append=append,
            )
            node = GraphNode(node_id=node_id, operation=op)

        elif node_type in ("copy_file", "move_file"):
            source_str = questionary.text("Source path:").ask()
            destination_str = questionary.text("Destination path:").ask()
            if not source_str or not destination_str:
                return
            create_dirs = questionary.confirm("Create parent folders?", default=True).ask()
            overwrite = questionary.confirm("Overwrite destination?", default=False).ask()
            if node_type == "copy_file":
                op = CopyFileOperation(
                    type=OperationType.COPY_FILE,
                    source_path=Path(source_str),
                    destination_path=Path(destination_str),
                    create_dirs=create_dirs,
                    overwrite=overwrite,
                )
            else:
                op = MoveFileOperation(
                    type=OperationType.MOVE_FILE,
                    source_path=Path(source_str),
                    destination_path=Path(destination_str),
                    create_dirs=create_dirs,
                    overwrite=overwrite,
                )
            node = GraphNode(node_id=node_id, operation=op)

        elif node_type == "delete_file":
            path_str = questionary.text("Path to delete:").ask()
            if not path_str:
                return
            use_trash = questionary.confirm("Move to Raticode trash?", default=True).ask()
            recursive = questionary.confirm("Allow recursive folder delete?", default=False).ask()
            missing_ok = questionary.confirm("Succeed if missing?", default=False).ask()
            op = DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=Path(path_str),
                use_trash=use_trash,
                recursive=recursive,
                missing_ok=missing_ok,
            )
            node = GraphNode(node_id=node_id, operation=op)

        elif node_type == "open_resource":
            target = questionary.text("File, folder, URL, or app to open:").ask()
            if not target:
                return
            resource_type = questionary.select(
                "Resource type:", choices=["auto", "file", "folder", "url", "app"]
            ).ask()
            args_str = questionary.text("App arguments (space-separated, optional):").ask()
            op = OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target=target,
                resource_type=resource_type or "auto",
                args=args_str.split() if args_str else [],
            )
            node = GraphNode(node_id=node_id, operation=op)

        elif node_type == "agent":
            agent_source = questionary.select(
                "Use a new or existing agent?", choices=["new", "existing"]
            ).ask()
            if agent_source is None:
                return

            if agent_source == "existing":
                all_agents = list_all_agents()
                if not all_agents:
                    console.print("[yellow]No existing agents found. Creating a new one.[/yellow]")
                    agent_source = "new"
                else:
                    choices = [f"{cfg.agent_id} ({wf.config.id})" for wf, cfg in all_agents]
                    chosen = questionary.select("Select an agent:", choices=choices).ask()
                    if chosen is None:
                        return
                    chosen_idx = choices.index(chosen)
                    _, agent_config = all_agents[chosen_idx]
                    self._workflow.register_agent(agent_config)
                    op = AgentOperation(
                        type=OperationType.AGENT,
                        agent_id=agent_config.agent_id,
                        prompt_path=agent_config.prompt_path,
                        working_dir=agent_config.working_dir,
                    )
                    op.output_schema, op.repair_attempts = self._ask_structured_output()
                    pipe_output = questionary.confirm(
                        "Pipe output to next node?", default=False
                    ).ask()
                    node = GraphNode(node_id=node_id, operation=op, pipe_output=pipe_output)

            if agent_source == "new":
                data_dir = get_data_dir()

                name = questionary.text("Agent name:").ask()
                if not name:
                    return
                name = name.strip()
                agent_id = unique_agent_id(name, data_dir)

                subscription = questionary.select(
                    "Subscription:",
                    choices=[
                        "claude_code",
                        "codex",
                        "openai_api",
                        "anthropic_api",
                        "cursor",
                        "copilot",
                        "opencode",
                    ],
                ).ask()
                if subscription is None:
                    return

                working_dir_str = questionary.text(
                    "Working directory:", default=str(Path.cwd())
                ).ask()
                if not working_dir_str:
                    return
                working_dir = Path(working_dir_str).expanduser().resolve()

                prompt_text = questionary.text("Prompt (text or path to a .md file):").ask()
                if not prompt_text:
                    return
                prompt_path = resolve_prompt(prompt_text.strip(), data_dir, agent_id)

                tools_str = questionary.text("Tools (comma-separated, optional):").ask()
                tools_list = [t.strip() for t in tools_str.split(",")] if tools_str else []

                mcp_str = questionary.text("MCP servers (comma-separated, optional):").ask()
                mcp_list = [s.strip() for s in mcp_str.split(",")] if mcp_str else []

                agent_config = AgentConfig(
                    agent_id=agent_id,
                    subscription=subscription,
                    prompt_path=prompt_path,
                    working_dir=working_dir,
                    tools=tools_list,
                    mcp_servers=mcp_list,
                )
                self._workflow.register_agent(agent_config)

                op = AgentOperation(
                    type=OperationType.AGENT,
                    agent_id=agent_id,
                    prompt_path=prompt_path,
                    working_dir=working_dir,
                )
                op.output_schema, op.repair_attempts = self._ask_structured_output()
                pipe_output = questionary.confirm("Pipe output to next node?", default=False).ask()
                node = GraphNode(node_id=node_id, operation=op, pipe_output=pipe_output)

        elif node_type == "loop":
            source = self._ask_fan_source(required=True)
            if source is None:
                return
            node = GraphNode(
                node_id=node_id,
                operation=LoopOperation(type=OperationType.LOOP, source=source),
            )

        if node is not None:
            self._workflow.add_operation(node)
            console.print(f"  [green]✓[/green] Added node '{node_id}'")

    def _ask_fan_source(self, required: bool = False) -> FanSource | None:
        if (
            not required
            and not questionary.confirm(
                "Loop once for each item in a collection?", default=False
            ).ask()
        ):
            return None

        source_type = questionary.select(
            "Run once for each…",
            choices=[
                "Fixed number of times",
                "Row in a JSONL/CSV file",
                "File in a directory",
                "File watcher trigger event",
                "Indefinitely until BREAK",
            ],
        ).ask()
        if source_type is None:
            return None

        if source_type == "Fixed number of times":
            count_str = questionary.text(
                "Number of iterations (integer or {{node.output}} reference):", default="1"
            ).ask()
            count: int | str
            try:
                count = int(count_str)
            except (ValueError, TypeError):
                count = count_str or 1
            return CountFanSource(type="count", count=count)

        if source_type == "Row in a JSONL/CSV file":
            path_str = questionary.text("Path to JSONL/CSV file:").ask()
            if not path_str:
                return None
            return TabularFanSource(
                type="tabular",
                path=Path(path_str),
            )

        if source_type == "File in a directory":
            path_str = questionary.text("Directory path:").ask()
            if not path_str:
                return None
            glob = questionary.text("File pattern (glob):", default="*").ask() or "*"
            include_content = questionary.confirm(
                "Pass file contents to the agent prompt?", default=False
            ).ask()
            return DirectoryFanSource(
                type="directory",
                path=Path(path_str),
                glob=glob,
                include_content=include_content,
            )

        if source_type == "File watcher trigger event":
            include_content = questionary.confirm(
                "Pass file contents to the agent prompt?", default=False
            ).ask()
            return TriggerEventsFanSource(
                type="trigger_events",
                include_content=include_content,
            )

        if source_type == "Indefinitely until BREAK":
            return InfiniteFanSource(type="infinite")

        return None

    def _ask_edges(self) -> None:
        assert self._workflow is not None
        console.print("\n[bold]Define edges[/bold]")
        node_ids = list(self._workflow.graph._nodes.keys())

        while True:
            if not questionary.confirm("Add an edge?", default=True).ask():
                break

            from_id = questionary.select("From node:", choices=node_ids).ask()
            to_choices = [n for n in node_ids if n != from_id]
            to_id = questionary.select("To node:", choices=to_choices).ask()
            if from_id == to_id:
                console.print(f"  [red]✗[/red] Edge would create a self-loop: {from_id}")
                continue
            if nx.has_path(self._workflow.graph._graph, to_id, from_id):
                console.print(f"  [red]✗[/red] Edge would create a cycle: {from_id} → {to_id}")
                continue

            condition_str = questionary.select(
                "Edge condition:",
                choices=[
                    "always",
                    "on_success",
                    "on_failure",
                    "output_matches",
                    "output_field",
                    "after_loop",
                ],
            ).ask()

            output_pattern = None
            field = None
            operator = None
            value = None
            if condition_str == "output_matches":
                output_pattern = questionary.text("Regex pattern:").ask()
            elif condition_str == "output_field":
                field = questionary.text("Structured result field:").ask()
                operator = questionary.select(
                    "Operator:",
                    choices=[
                        "equals",
                        "not_equals",
                        "in",
                        "not_in",
                        "greater_than",
                        "greater_than_or_equal",
                        "less_than",
                        "less_than_or_equal",
                        "exists",
                        "matches",
                    ],
                ).ask()
                if operator != "exists":
                    raw_value = questionary.text("Comparison value (JSON):").ask()
                    value = json.loads(raw_value)

            edge_config = EdgeConfig(
                from_node=from_id,
                to_node=to_id,
                condition=EdgeConditionType(condition_str),
                output_pattern=output_pattern,
                field=field,
                operator=operator,
                value=value,
            )

            try:
                self._workflow.then(from_id, to_id, edge_config)
                label = f"[{condition_str}]" if condition_str != "always" else ""
                console.print(f"  [green]✓[/green] {from_id} → {to_id} {label}")
            except CycleError as exc:
                console.print(f"  [red]✗[/red] {exc}")

    def _preview_dag(self) -> None:
        assert self._workflow is not None
        console.print()
        generations = self._workflow.graph.topological_generations()
        lines: list[str] = []
        for i, gen in enumerate(generations):
            ids = "   ".join(n.node_id for n in gen)
            lines.append(f"[gen {i}]  {ids}")

        # Annotate conditional edges
        edge_annotations: list[str] = []
        for (u, v), cfg in self._workflow.graph._edges.items():
            if cfg.condition != EdgeConditionType.ALWAYS:
                pat = f" pattern={cfg.output_pattern!r}" if cfg.output_pattern else ""
                edge_annotations.append(f"  {u} → {v}  [{cfg.condition.value}{pat}]")

        content = Text("\n".join(lines))
        if edge_annotations:
            content.append("\n\nConditional edges:\n" + "\n".join(edge_annotations))

        console.print(
            Panel(content, title=f"[bold]{self._workflow.config.id}[/bold]", expand=False)
        )
