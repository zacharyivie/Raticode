from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from gofer.core.agent import (
    Agent,
    AgentConfig,
    agent_external_access_warnings,
    configured_extra_paths,
)
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.subscriptions.acp_providers import AcpSubscription
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription
from gofer.utils.agent_helpers import resolve_prompt, unique_agent_id
from gofer.utils.paths import get_data_dir
from gofer.utils.registry import find_agent, find_workflow, list_all_agents

app = typer.Typer(help="Manage and run agents", no_args_is_help=True)
console = Console()

_SUBSCRIPTIONS = {
    "claude_code": ClaudeCodeSubscription(),
    "codex": CodexSubscription(),
    **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
    "antigravity": AntigravitySubscription(),
    "grok": AcpSubscription("grok"),
    "openai_api": OpenAiApiSubscription(),
    "anthropic_api": AnthropicApiSubscription(),
}
_SUBSCRIPTION_CHOICES = list(_SUBSCRIPTIONS)


@app.command("create")
def create(
    name: str | None = typer.Option(None, "--name", help="Human-readable agent name"),
    subscription: str | None = typer.Option(
        None, "--subscription", help=f"Subscription ({', '.join(_SUBSCRIPTION_CHOICES)})"
    ),
    profile: str | None = typer.Option(None, "--profile", help="Provider profile name"),
    model: str | None = typer.Option(None, "--model", help="Provider model override"),
    working_dir: Path | None = typer.Option(None, "--working-dir", help="Agent working directory"),
    prompt: str | None = typer.Option(
        None, "--prompt", help="Prompt text or path to a prompt file"
    ),
    tools: str | None = typer.Option(None, "--tools", help="Comma-separated tool names"),
    mcp_servers: str | None = typer.Option(
        None, "--mcp-servers", help="Comma-separated MCP server names"
    ),
    extra_path: list[Path] | None = typer.Option(
        None,
        "--extra-path",
        help="Additional path to grant the provider sandbox (repeatable)",
    ),
    env: list[str] | None = typer.Option(
        None, "--env", help="Environment variables as KEY=VALUE (repeatable)"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Create a new agent and save it to the data directory."""
    base = data_dir or get_data_dir()
    base.mkdir(parents=True, exist_ok=True)

    # ── Collect required fields, prompting for any that are missing ──────────

    if not name:
        name = typer.prompt("Agent name")
    name = name.strip()

    agent_id = unique_agent_id(name, base)
    dest_path = base / f"{agent_id}.toml"

    if not subscription:
        subscription = typer.prompt(
            f"Subscription ({', '.join(_SUBSCRIPTION_CHOICES)})",
            default="claude_code",
        )
    subscription = subscription.strip()
    if subscription not in _SUBSCRIPTION_CHOICES:
        console.print(
            f"[red]Invalid subscription '{subscription}'. "
            f"Choose from: {', '.join(_SUBSCRIPTION_CHOICES)}[/red]"
        )
        raise typer.Exit(1)

    if not working_dir:
        raw = typer.prompt("Working directory", default=str(Path.cwd()))
        working_dir = Path(raw).expanduser().resolve()

    if not prompt:
        prompt = typer.prompt(
            "Prompt (text or path to a .md file)",
            prompt_suffix="\n> ",
        )
    prompt = prompt.strip()

    prompt_path = resolve_prompt(prompt, base, agent_id)

    # ── Optional fields ──────────────────────────────────────────────────────

    tools_list = [t.strip() for t in tools.split(",")] if tools else []
    mcp_list = [s.strip() for s in mcp_servers.split(",")] if mcp_servers else []
    env_dict: dict[str, str] = {}
    for pair in env or []:
        if "=" not in pair:
            console.print(f"[red]Invalid --env value '{pair}': expected KEY=VALUE[/red]")
            raise typer.Exit(1)
        k, _, v = pair.partition("=")
        env_dict[k] = v

    # ── Write TOML ───────────────────────────────────────────────────────────

    config = AgentConfig(
        agent_id=agent_id,
        subscription=subscription,  # type: ignore[arg-type]
        working_dir=working_dir,
        profile=profile,
        model=model,
        prompt_path=prompt_path,
        tools=tools_list,
        mcp_servers=mcp_list,
        env=env_dict,
        extra_paths=_resolve_extra_paths(extra_path),
    )
    wf = AgenticWorkflow(WorkflowConfig(id=agent_id, name=name))
    wf.register_agent(config)
    wf.to_file(dest_path)

    console.print(f"[green]Created agent[/green] [bold]{agent_id}[/bold] → {dest_path}")


@app.command("edit")
def edit(
    agent_id: str = typer.Argument(..., help="Agent ID to edit"),
    subscription: str | None = typer.Option(
        None, "--subscription", help=f"Subscription ({', '.join(_SUBSCRIPTION_CHOICES)})"
    ),
    profile: str | None = typer.Option(None, "--profile", help="Provider profile name"),
    model: str | None = typer.Option(None, "--model", help="Provider model override"),
    working_dir: Path | None = typer.Option(None, "--working-dir", help="Agent working directory"),
    prompt: str | None = typer.Option(
        None, "--prompt", help="Prompt text or path to a prompt file"
    ),
    tools: str | None = typer.Option(None, "--tools", help="Comma-separated tool names"),
    mcp_servers: str | None = typer.Option(
        None, "--mcp-servers", help="Comma-separated MCP server names"
    ),
    extra_path: list[Path] | None = typer.Option(
        None,
        "--extra-path",
        help="Replace additional provider sandbox paths (repeatable)",
    ),
    env: list[str] | None = typer.Option(
        None, "--env", help="Environment variables as KEY=VALUE (repeatable)"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Edit an existing agent's configuration."""
    base = data_dir or get_data_dir()
    try:
        wf, cfg = find_agent(agent_id, base)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    # No flags → interactive TUI editor
    _any_flag = any(
        v is not None
        for v in [
            subscription,
            profile,
            model,
            working_dir,
            prompt,
            tools,
            mcp_servers,
            extra_path,
            env,
        ]
    )
    if not _any_flag:
        from gofer.cli.tui_editor import (
            FieldEditorApp,
            agent_to_sections,
            sections_to_agent,
        )

        sections = agent_to_sections(cfg)
        if FieldEditorApp(sections, title=f"Edit Agent: {agent_id}").run():
            cfg = sections_to_agent(sections, cfg)
            wf.agents[agent_id] = cfg
            dest_path = base / f"{wf.config.id}.toml"
            wf.to_file(dest_path)
            console.print(f"[green]Updated agent[/green] [bold]{agent_id}[/bold] → {dest_path}")
        else:
            console.print("[yellow]Edit cancelled.[/yellow]")
        return

    if subscription is not None:
        subscription = subscription.strip()
        if subscription not in _SUBSCRIPTION_CHOICES:
            console.print(
                f"[red]Invalid subscription '{subscription}'. "
                f"Choose from: {', '.join(_SUBSCRIPTION_CHOICES)}[/red]"
            )
            raise typer.Exit(1)
        cfg = cfg.model_copy(update={"subscription": subscription})

    if working_dir is not None:
        cfg = cfg.model_copy(update={"working_dir": working_dir.expanduser().resolve()})

    if profile is not None:
        cfg = cfg.model_copy(update={"profile": profile.strip() or None})

    if model is not None:
        cfg = cfg.model_copy(update={"model": model.strip() or None})

    if prompt is not None:
        prompt_path = resolve_prompt(prompt.strip(), base, agent_id)
        cfg = cfg.model_copy(update={"prompt_path": prompt_path})

    if tools is not None:
        cfg = cfg.model_copy(update={"tools": [t.strip() for t in tools.split(",")]})

    if mcp_servers is not None:
        cfg = cfg.model_copy(update={"mcp_servers": [s.strip() for s in mcp_servers.split(",")]})

    if extra_path is not None:
        cfg = cfg.model_copy(update={"extra_paths": _resolve_extra_paths(extra_path)})

    if env is not None:
        env_dict: dict[str, str] = {}
        for pair in env:
            if "=" not in pair:
                console.print(f"[red]Invalid --env value '{pair}': expected KEY=VALUE[/red]")
                raise typer.Exit(1)
            k, _, v = pair.partition("=")
            env_dict[k] = v
        cfg = cfg.model_copy(update={"env": env_dict})

    wf.agents[agent_id] = cfg
    dest_path = base / f"{wf.config.id}.toml"
    wf.to_file(dest_path)
    console.print(f"[green]Updated agent[/green] [bold]{agent_id}[/bold] → {dest_path}")


def _resolve_extra_paths(paths: list[Path] | None) -> list[Path]:
    resolved_paths: list[Path] = []
    for path in paths or []:
        resolved_paths.append(path.expanduser().resolve())
    return resolved_paths


def _print_agent_access_summary(config: AgentConfig) -> None:
    warnings = agent_external_access_warnings(config)
    if not warnings:
        return
    console.print("[yellow]Agent filesystem access outside working_dir:[/yellow]")
    for warning in warnings:
        console.print(f"[yellow]- {warning}[/yellow]")


def _validate_agent_extra_paths(config: AgentConfig) -> None:
    try:
        configured_extra_paths(config)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("rm")
def rm(
    agent_id: str = typer.Argument(..., help="Agent ID to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Remove an agent from its workflow."""
    base = data_dir or get_data_dir()
    try:
        wf, cfg = find_agent(agent_id, base)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if not yes:
        typer.confirm(f"Remove agent '{agent_id}' from workflow '{wf.config.id}'?", abort=True)

    prompt_path = cfg.prompt_path
    del wf.agents[agent_id]
    dest_path = base / f"{wf.config.id}.toml"
    if not wf.agents and not list(wf.graph._graph.nodes()):
        dest_path.unlink()
        console.print(
            f"[green]Removed agent[/green] [bold]{agent_id}[/bold] and deleted {dest_path}"
        )
    else:
        wf.to_file(dest_path)
        console.print(f"[green]Removed agent[/green] [bold]{agent_id}[/bold] from {dest_path}")

    managed_prompts_dir = base / "prompts"
    try:
        if (
            prompt_path is not None
            and prompt_path.is_relative_to(managed_prompts_dir)
            and prompt_path.exists()
        ):
            prompt_path.unlink()
    except ValueError:
        pass


@app.command("run")
def run(
    agent_id: str = typer.Argument(..., help="Agent ID (e.g. TradeAgent)"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Run a named agent."""
    try:
        _, config = find_agent(agent_id, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    sub = _SUBSCRIPTIONS.get(config.subscription)
    if sub is None:
        console.print(f"[red]Unknown subscription '{config.subscription}'[/red]")
        raise typer.Exit(1)

    _validate_agent_extra_paths(config)
    _print_agent_access_summary(config)

    result = asyncio.run(Agent(config, sub).run())
    if result.success:
        console.print(result.output)
    else:
        console.print(f"[red]Agent failed (exit {result.exit_code}):[/red]\n{result.output}")
        raise typer.Exit(1)


@app.command("list")
def list_agents(
    workflow: str | None = typer.Option(None, "--workflow", help="Filter by workflow ID"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List agents. Without --workflow, lists all agents in the data directory."""
    base = data_dir or get_data_dir()

    if workflow:
        try:
            wf = find_workflow(workflow, base)
            pairs = [(wf, cfg) for cfg in wf.agents.values()]
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    else:
        pairs = list_all_agents(base)

    if not pairs:
        console.print(f"No agents found in [bold]{base}[/bold].")
        return

    table = Table()
    table.add_column("Agent ID", no_wrap=True)
    table.add_column("Workflow", no_wrap=True)
    table.add_column("Subscription", no_wrap=True)
    table.add_column("Profile", overflow="fold")
    table.add_column("Model", overflow="fold")
    table.add_column("Working Dir", overflow="fold")
    table.add_column("Prompt", overflow="fold")
    for wf, cfg in pairs:
        table.add_row(
            cfg.agent_id,
            wf.config.id,
            cfg.subscription,
            cfg.profile or "",
            cfg.model or "",
            str(cfg.working_dir),
            str(cfg.prompt_path),
        )
    console.print(table)
