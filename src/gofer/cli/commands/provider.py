from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from rich.console import Console
from rich.table import Table

from gofer.cli.output import emit_json, format_option, wants_json
from gofer.utils.paths import get_data_dir

if TYPE_CHECKING:
    from gofer.core.provider_profiles import ProviderProfile

app = typer.Typer(help="Manage provider settings", no_args_is_help=True)
profile_app = typer.Typer(help="Manage named provider profiles", no_args_is_help=True)
app.add_typer(profile_app, name="profile")
console = Console()
err_console = Console(stderr=True)

_SUBSCRIPTIONS = [
    "codex",
    "claude_code",
    "openai_api",
    "anthropic_api",
    "cursor",
    "copilot",
    "opencode",
]


@profile_app.command("list")
def list_profiles(
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List provider profiles. JSON names secrets and env variables, never their values."""
    from gofer.core.provider_profiles import load_provider_profiles

    as_json = wants_json(output_format)
    profiles = load_provider_profiles(data_dir or get_data_dir())
    if as_json:
        emit_json(
            [
                {
                    "name": profile.name,
                    "subscription": profile.subscription,
                    "model": profile.model,
                    "effort": profile.effort,
                    "timeout": profile.timeout,
                    "apiKeySecret": profile.api_key_secret,
                    "apiKeyEnv": profile.api_key_env,
                    "apiBaseUrl": profile.api_base_url,
                    "local": profile.local,
                    "envNames": sorted(profile.env),
                }
                for profile in profiles.values()
            ]
        )
        return
    if not profiles:
        console.print("No provider profiles found.")
        return
    table = Table("Name", "Subscription", "Model", "Timeout", "API Key", "Base URL")
    for profile in profiles.values():
        table.add_row(
            profile.name,
            "local model server" if profile.local else profile.subscription,
            profile.model or "",
            str(profile.timeout or ""),
            profile.api_key_secret or profile.api_key_env or "",
            profile.api_base_url or "",
        )
    console.print(table)


@profile_app.command(
    "create",
    help="Create a provider profile in the data folder.",
)
def create_profile(
    name: str = typer.Argument(..., help="Profile name"),
    subscription: str | None = typer.Option(
        None,
        "--subscription",
        help=(
            "CLI or API subscription, such as codex, antigravity, grok, or openai_api "
            "(default: codex, or openai_api with --local)"
        ),
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help=(
            "A local model server you run, such as Ollama, llama.cpp or LM Studio, through "
            "its OpenAI-compatible API. Needs no API key on this computer's own address. "
            "--api-base-url defaults to http://127.0.0.1:11434/v1 (Ollama)."
        ),
    ),
    model: str | None = typer.Option(None, "--model", help="Provider model"),
    timeout: float | None = typer.Option(None, "--timeout", help="Default timeout in seconds"),
    effort: str | None = typer.Option(
        None,
        "--effort",
        "--reasoning",
        help="Model effort setting (--reasoning is deprecated)",
    ),
    approval_mode: str | None = typer.Option(
        None,
        "--approval-mode",
        help="Provider approval mode",
    ),
    sandbox_mode: str | None = typer.Option(None, "--sandbox-mode", help="Provider sandbox mode"),
    extra_arg: list[str] | None = typer.Option(
        None,
        "--extra-arg",
        help="Additional provider CLI argument (repeatable)",
    ),
    tool: list[str] | None = typer.Option(None, "--tool", help="Default tool (repeatable)"),
    mcp_server: list[str] | None = typer.Option(
        None,
        "--mcp-server",
        help="Default MCP server (repeatable)",
    ),
    env: list[str] | None = typer.Option(None, "--env", help="KEY=VALUE env var (repeatable)"),
    unsafe_env: list[str] | None = typer.Option(
        None,
        "--unsafe-env",
        help="Explicit local-only plaintext sensitive env var (repeatable)",
    ),
    secret_ref: list[str] | None = typer.Option(
        None,
        "--secret-ref",
        help="KEY=SECRET_NAME secret env reference (repeatable)",
    ),
    api_base_url: str | None = typer.Option(None, "--api-base-url", help="Direct API base URL"),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Direct API key env var"),
    api_key_secret: str | None = typer.Option(
        None,
        "--api-key-secret",
        help="Direct API key secret name",
    ),
    organization: str | None = typer.Option(None, "--organization", help="Provider organization"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    from gofer.core.provider_profiles import (
        DEFAULT_LOCAL_MODEL_BASE_URL,
        LOCAL_MODEL_SERVER_SUBSCRIPTION,
        load_provider_profiles,
        save_provider_profiles,
    )

    if local:
        if subscription not in (None, LOCAL_MODEL_SERVER_SUBSCRIPTION):
            err_console.print(
                "[red]--local uses the OpenAI-compatible API; leave out --subscription "
                f"or use --subscription {LOCAL_MODEL_SERVER_SUBSCRIPTION}[/red]"
            )
            raise typer.Exit(1)
        subscription = LOCAL_MODEL_SERVER_SUBSCRIPTION
        api_base_url = api_base_url or DEFAULT_LOCAL_MODEL_BASE_URL
    subscription = subscription or "codex"
    if subscription not in _SUBSCRIPTIONS:
        err_console.print(f"[red]Invalid subscription '{subscription}'[/red]")
        raise typer.Exit(1)
    profiles = load_provider_profiles(data_dir or get_data_dir())
    if name in profiles:
        err_console.print(f"[red]Provider profile '{name}' already exists[/red]")
        raise typer.Exit(1)
    profile = _profile_from_options(
        name=name,
        subscription=subscription,
        model=model,
        timeout=timeout,
        effort=effort,
        approval_mode=approval_mode,
        sandbox_mode=sandbox_mode,
        extra_arg=extra_arg,
        tool=tool,
        mcp_server=mcp_server,
        env=env,
        unsafe_env=unsafe_env,
        secret_ref=secret_ref,
        api_base_url=api_base_url,
        api_key_env=api_key_env,
        api_key_secret=api_key_secret,
        organization=organization,
        local=local,
    )
    profiles[name] = profile
    save_provider_profiles(profiles, data_dir or get_data_dir())
    console.print(f"[green]Created provider profile[/green] [bold]{name}[/bold]")
    _print_local_warning(profile)
    if profile.local:
        console.print(
            f"Check it with: Raticode provider profile check {name}. In a workflow, use "
            f"provider: openai-api, profile: {name} and a model the server lists."
        )


@profile_app.command(
    "edit",
    help="Change a provider profile. Options you give replace the stored values.",
)
def edit_profile(
    name: str = typer.Argument(..., help="Profile name"),
    model: str | None = typer.Option(None, "--model", help="Provider model"),
    timeout: float | None = typer.Option(None, "--timeout", help="Default timeout in seconds"),
    effort: str | None = typer.Option(
        None,
        "--effort",
        "--reasoning",
        help="Model effort setting (--reasoning is deprecated)",
    ),
    approval_mode: str | None = typer.Option(
        None,
        "--approval-mode",
        help="Provider approval mode",
    ),
    sandbox_mode: str | None = typer.Option(None, "--sandbox-mode", help="Provider sandbox mode"),
    extra_arg: list[str] | None = typer.Option(None, "--extra-arg", help="Replace extra args"),
    tool: list[str] | None = typer.Option(None, "--tool", help="Replace default tools"),
    mcp_server: list[str] | None = typer.Option(None, "--mcp-server", help="Replace MCP servers"),
    env: list[str] | None = typer.Option(None, "--env", help="Replace env vars"),
    unsafe_env: list[str] | None = typer.Option(
        None,
        "--unsafe-env",
        help="Replace or add explicit local-only plaintext sensitive env vars",
    ),
    secret_ref: list[str] | None = typer.Option(
        None,
        "--secret-ref",
        help="Replace secret env references",
    ),
    api_base_url: str | None = typer.Option(None, "--api-base-url", help="Direct API base URL"),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Direct API key env var"),
    api_key_secret: str | None = typer.Option(
        None,
        "--api-key-secret",
        help="Direct API key secret name",
    ),
    organization: str | None = typer.Option(None, "--organization", help="Provider organization"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    from gofer.core.provider_profiles import (
        load_provider_profiles,
        save_provider_profiles,
        validate_provider_profile,
        validate_safe_provider_env,
    )

    profiles = load_provider_profiles(data_dir or get_data_dir())
    profile = profiles.get(name)
    if profile is None:
        err_console.print(f"[red]Provider profile '{name}' not found[/red]")
        raise typer.Exit(1)
    updates: dict[str, object] = {
        key: value
        for key, value in {
            "model": model,
            "timeout": timeout,
            "effort": effort,
            "approval_mode": approval_mode,
            "sandbox_mode": sandbox_mode,
            "api_base_url": api_base_url,
            "api_key_env": api_key_env,
            "api_key_secret": api_key_secret,
            "organization": organization,
        }.items()
        if value is not None
    }
    if extra_arg is not None:
        updates["extra_args"] = list(extra_arg)
    if tool is not None:
        updates["tools"] = list(tool)
    if mcp_server is not None:
        updates["mcp_servers"] = list(mcp_server)
    try:
        if env is not None:
            parsed_env = _parse_key_value_options(env, "--env", "KEY=VALUE")
            validate_safe_provider_env(parsed_env)
            updates["env"] = parsed_env
        if unsafe_env is not None:
            parsed_unsafe_env = _parse_key_value_options(
                unsafe_env,
                "--unsafe-env",
                "KEY=VALUE",
            )
            base_env = cast(dict[str, str], updates.get("env", profile.env))
            updates["env"] = {**base_env, **parsed_unsafe_env}
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if secret_ref is not None:
        parsed_secret_refs = _parse_key_value_options(
            secret_ref,
            "--secret-ref",
            "KEY=SECRET_NAME",
        )
        updates["secret_refs"] = parsed_secret_refs
        if parsed_secret_refs:
            base_env = cast(dict[str, str], updates.get("env", profile.env))
            updates["env"] = {
                name: value for name, value in base_env.items() if name not in parsed_secret_refs
            }
    updated_profile = profile.model_copy(update=updates)
    try:
        validate_provider_profile(updated_profile)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    profiles[name] = updated_profile
    save_provider_profiles(profiles, data_dir or get_data_dir())
    console.print(f"[green]Updated provider profile[/green] [bold]{name}[/bold]")
    _print_local_warning(updated_profile)


@profile_app.command(
    "check",
    help=(
        "Check a local model server profile: whether its server answers and which models "
        "it lists. With --model, also whether it has that model. Exits 1 if not."
    ),
)
def check_profile(
    name: str = typer.Argument(..., help="Local model server profile name"),
    model: str | None = typer.Option(
        None, "--model", help="Model ID to look for (default: the profile's model)"
    ),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    from gofer.core.local_models import check_local_server

    try:
        result = check_local_server(name, model=model, data_dir=data_dir or get_data_dir())
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    ok = result.reachable and result.model_available is not False
    if wants_json(output_format):
        emit_json(result.to_json())
        if not ok:
            raise typer.Exit(1)
        return
    if result.warning:
        err_console.print(f"[yellow]Warning: {result.warning}[/yellow]")
    if not result.reachable:
        err_console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(1)
    console.print(f"The server at {result.base_url} answered.")
    if result.models:
        console.print("Models it lists:")
        for item in result.models:
            console.print(f"  {item}", markup=False, highlight=False)
    else:
        console.print("It lists no models. Load or pull one in the server first.")
    if result.model_available is True:
        console.print(f"[green]Model {result.model} is available.[/green]")
    elif result.model_available is False:
        err_console.print(f"[red]The server doesn't list model {result.model}.[/red]")
        raise typer.Exit(1)


def _print_local_warning(profile: ProviderProfile) -> None:
    from gofer.core.provider_profiles import local_server_warning

    if warning := local_server_warning(profile):
        err_console.print(f"[yellow]Warning: {warning}[/yellow]")


@profile_app.command(
    "rm",
    help="Remove a provider profile. Asks first unless --yes is given.",
)
def rm_profile(
    name: str = typer.Argument(..., help="Profile name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    from gofer.core.provider_profiles import load_provider_profiles, save_provider_profiles

    profiles = load_provider_profiles(data_dir or get_data_dir())
    if name not in profiles:
        err_console.print(f"[red]Provider profile '{name}' not found[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Remove provider profile '{name}'?", abort=True)
    del profiles[name]
    save_provider_profiles(profiles, data_dir or get_data_dir())
    console.print(f"[green]Removed provider profile[/green] [bold]{name}[/bold]")


def _profile_from_options(
    *,
    name: str,
    subscription: str,
    model: str | None,
    timeout: float | None,
    effort: str | None,
    approval_mode: str | None,
    sandbox_mode: str | None,
    extra_arg: list[str] | None,
    tool: list[str] | None,
    mcp_server: list[str] | None,
    env: list[str] | None,
    unsafe_env: list[str] | None,
    secret_ref: list[str] | None,
    api_base_url: str | None,
    api_key_env: str | None,
    api_key_secret: str | None,
    organization: str | None,
    local: bool = False,
) -> ProviderProfile:
    from gofer.core.provider_profiles import (
        ProviderProfile,
        validate_provider_profile,
        validate_safe_provider_env,
    )

    try:
        parsed_env = _parse_key_value_options(env or [], "--env", "KEY=VALUE")
        parsed_unsafe_env = _parse_key_value_options(
            unsafe_env or [],
            "--unsafe-env",
            "KEY=VALUE",
        )
        validate_safe_provider_env(parsed_env)
        profile = ProviderProfile(
            name=name,
            subscription=subscription,  # type: ignore[arg-type]
            model=model,
            timeout=timeout,
            effort=effort,
            approval_mode=approval_mode,  # type: ignore[arg-type]
            sandbox_mode=sandbox_mode,  # type: ignore[arg-type]
            extra_args=list(extra_arg or []),
            tools=list(tool or []),
            mcp_servers=list(mcp_server or []),
            env={**parsed_env, **parsed_unsafe_env},
            secret_refs=_parse_key_value_options(
                secret_ref or [],
                "--secret-ref",
                "KEY=SECRET_NAME",
            ),
            api_base_url=api_base_url,
            api_key_env=api_key_env,
            api_key_secret=api_key_secret,
            organization=organization,
            local=local,
        )
        validate_provider_profile(profile)
        return profile
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _parse_key_value_options(
    values: list[str],
    option_name: str,
    expected: str,
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in values:
        if pair == "":
            continue
        if "=" not in pair:
            err_console.print(
                f"[red]Invalid {option_name} value '{pair}': expected {expected}[/red]"
            )
            raise typer.Exit(1)
        key, _, value = pair.partition("=")
        if not key:
            err_console.print(
                f"[red]Invalid {option_name} value '{pair}': expected {expected}[/red]"
            )
            raise typer.Exit(1)
        parsed[key] = value
    return parsed
