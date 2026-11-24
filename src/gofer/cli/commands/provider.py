from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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
    if profiles[name].account_id:
        err_console.print(
            f"'{name}' is an account. Remove it with `provider account rm {name}`, "
            "which signs it out first."
        )
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


account_app = typer.Typer(
    help="Provider accounts: one CLI sign-in per account folder (Claude Code, Codex, Grok)",
    no_args_is_help=True,
)
app.add_typer(account_app, name="account")


def _account_provider_option(default: str | None) -> Any:
    return typer.Option(
        default,
        "--provider",
        help="Provider ID: claude_code, codex or grok. "
        + ("Defaults to claude_code." if default else "Defaults to every provider with accounts."),
    )


def _device_auth_option() -> Any:
    return typer.Option(
        False,
        "--device-auth",
        help="Sign in with a code entered on another device, such as a phone, instead of "
        "opening a browser here (`codex login --device-auth`, `grok login --device-auth`).",
    )


def _account_error(exc: Exception) -> typer.Exit:
    err_console.print(f"[red]Error:[/red] {exc}")
    return typer.Exit(1)


def _account_rows(data_dir: Path | None, refresh: bool, provider: str | None) -> dict[str, Any]:
    from gofer.core.provider_accounts import accounts_overview

    return accounts_overview(data_dir or get_data_dir(), refresh=refresh, provider=provider)


def _print_accounts(overview: dict[str, Any]) -> None:
    accounts = overview.get("accounts") or []
    if not accounts:
        console.print(
            "No accounts. Add one with `provider account add NAME [--provider PROVIDER]`."
        )
        return
    table = Table("Account", "Provider", "Status", "Plan", "Signed in as")
    for account in accounts:
        status = {
            "signed_in": "signed in",
            "needs_sign_in": "needs sign-in",
        }.get(str(account.get("state")), str(account.get("state") or "unknown"))
        table.add_row(
            str(account["name"]),
            str(account.get("provider") or ""),
            status,
            str(account.get("plan") or ""),
            str(account.get("email") or ""),
        )
    console.print(table)
    for warning in overview.get("warnings") or []:
        err_console.print(f"[yellow]Warning:[/yellow] {warning['message']}")


@account_app.command("list")
def list_accounts_command(
    refresh: bool = typer.Option(False, "--refresh", help="Ask each CLI again now."),
    provider: str | None = _account_provider_option(None),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List accounts and whether each is signed in (from its CLI's own status command)."""
    from gofer.core.provider_accounts import AccountError

    try:
        overview = _account_rows(data_dir, refresh, provider)
    except AccountError as exc:
        raise _account_error(exc) from exc
    if wants_json(output_format):
        emit_json(overview)
        return
    _print_accounts(overview)


@account_app.command("status")
def account_status_command(
    name: str = typer.Argument(..., help="Account name"),
    provider: str | None = _account_provider_option(None),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Check one account's sign-in with its CLI now."""
    from gofer.core.provider_accounts import AccountError, account_action

    try:
        overview = account_action(
            {"action": "check", "name": name, "provider": provider}, data_dir or get_data_dir()
        )
    except AccountError as exc:
        raise _account_error(exc) from exc
    overview["accounts"] = [a for a in overview["accounts"] if a["name"] == name]
    if wants_json(output_format):
        emit_json(overview)
        return
    _print_accounts(overview)


@account_app.command("add")
def add_account_command(
    name: str = typer.Argument(..., help="A short name, such as work or personal"),
    provider: str = _account_provider_option("claude_code"),
    copy_setup: bool = typer.Option(
        False,
        "--copy-setup",
        help="Copy shared setup (for Claude Code: CLAUDE.md, skills, commands, agents and "
        "output styles from ~/.claude; for Codex: AGENTS.md, skills and config.toml without "
        "its sign-in settings from ~/.codex). Grok accounts start empty. Never a sign-in.",
    ),
    copy_setup_from: Path | None = typer.Option(
        None,
        "--copy-setup-from",
        help="Copy shared setup from this folder instead of the provider's own (implies "
        "--copy-setup).",
    ),
    sign_in: bool = typer.Option(
        True, "--sign-in/--no-sign-in", help="Run the CLI's own sign-in for it right away."
    ),
    device_auth: bool = _device_auth_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add an account with its own config folder, then sign it in once."""
    from gofer.core.provider_accounts import AccountError, create_account, require_adapter

    try:
        adapter = require_adapter(provider)
        create_account(
            name,
            provider=provider,
            data_dir=data_dir or get_data_dir(),
            copy_shared_setup=copy_setup or copy_setup_from is not None,
            setup_source=copy_setup_from.expanduser().absolute() if copy_setup_from else None,
        )
    except AccountError as exc:
        raise _account_error(exc) from exc
    console.print(f"Added {adapter.label} account '{name}'.")
    if sign_in:
        login_account_command(
            name=name, provider=provider, device_auth=device_auth, data_dir=data_dir
        )


@account_app.command("login")
def login_account_command(
    name: str = typer.Argument(..., help="Account name"),
    provider: str | None = _account_provider_option(None),
    device_auth: bool = _device_auth_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Sign the account in with its CLI's own sign-in, such as `codex login` or `grok login`.

    The CLI opens your browser; you approve there. If it asks for a code, paste it
    into the CLI here. With --device-auth (Codex, Grok), the CLI prints a page and a
    code to enter on any device instead; a paired phone does the same for a Codex
    or Grok account with Sign in. Raticode never sees the sign-in. If the provider refuses it,
    the CLI's own message says why.
    """
    import subprocess

    from gofer.core.provider_accounts import (
        SIGNED_IN,
        AccountError,
        account_config_dir,
        account_status,
        get_account,
        require_adapter,
    )
    from gofer.utils.process import without_unset_env

    root = data_dir or get_data_dir()
    try:
        account = get_account(name, root, provider)
        adapter = require_adapter(account.subscription)
        folder = account_config_dir(account, root)
    except AccountError as exc:
        raise _account_error(exc) from exc
    if device_auth and adapter.device_sign_in_args is None:
        err_console.print(
            f"[red]Error:[/red] {adapter.label} has no device-code sign-in. "
            "Leave out --device-auth."
        )
        raise typer.Exit(1)
    executable = adapter.find_executable()
    if executable is None:
        err_console.print(f"[red]Error:[/red] {adapter.missing_cli()}")
        raise typer.Exit(1)
    sign_in_args = (
        adapter.device_sign_in_args
        if device_auth and adapter.device_sign_in_args
        else adapter.sign_in_args
    )
    completed = subprocess.run(
        [executable, *sign_in_args],
        env=without_unset_env(adapter.environment(folder)),
        cwd=str(folder),
        check=False,
    )
    status = account_status(account, data_dir=root, refresh=True)
    if status.get("state") == SIGNED_IN:
        console.print(
            f"'{name}' is signed in. Raticode won't ask again until {adapter.label} does."
        )
        return
    err_console.print(f"'{name}' still needs sign-in.")
    if device_auth and completed.returncode and adapter.device_sign_in_help:
        # Codex prints its own reason above; this says where to turn it on.
        err_console.print(f"If {adapter.label} said device code sign-in is off: ")
        err_console.print(adapter.device_sign_in_help)
    raise typer.Exit(1)


@account_app.command("rename")
def rename_account_command(
    name: str = typer.Argument(..., help="Account name"),
    new_name: str = typer.Argument(..., help="New name"),
    provider: str | None = _account_provider_option(None),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Rename an account. Its folder and sign-in stay as they are."""
    from gofer.core.provider_accounts import AccountError, get_account, rename_account

    root = data_dir or get_data_dir()
    try:
        get_account(name, root, provider)
        rename_account(name, new_name, data_dir=root)
    except AccountError as exc:
        raise _account_error(exc) from exc
    console.print(f"Renamed '{name}' to '{new_name}'.")


@account_app.command("import-setup")
def import_setup_account_command(
    name: str = typer.Argument(..., help="Account name"),
    setup_from: Path | None = typer.Option(
        None,
        "--from",
        help="Folder to copy from. Defaults to the provider's own (~/.claude or ~/.codex).",
    ),
    provider: str | None = _account_provider_option(None),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Copy shared setup (skills and the like) into an account. Never a sign-in.

    Items the account already has are kept as they are.
    """
    from gofer.core.provider_accounts import AccountError, get_account, import_setup

    root = data_dir or get_data_dir()
    try:
        get_account(name, root, provider)
        copied = import_setup(
            name,
            data_dir=root,
            setup_source=setup_from.expanduser().absolute() if setup_from else None,
        )
    except AccountError as exc:
        raise _account_error(exc) from exc
    if wants_json(output_format):
        emit_json({"account": name, "copied": copied})
        return
    if copied:
        console.print(f"Copied {', '.join(copied)} into '{name}'.")
    else:
        console.print(f"Nothing new to copy into '{name}'.")


@account_app.command("copy-setup")
def copy_setup_account_command(
    name: str = typer.Argument(..., help="Account name"),
    setup_from: Path | None = typer.Option(
        None,
        "--from",
        help="Folder to compare with. Defaults to the provider's own (~/.claude or ~/.codex).",
    ),
    check: bool = typer.Option(
        False, "--check", help="Only list the items that differ; copy nothing."
    ),
    provider: str | None = _account_provider_option(None),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Copy your setup again: re-copy only the setup items that changed since the copy.

    Compares the account's copied setup (Claude Code: CLAUDE.md, skills, commands,
    agents, output-styles; Codex: AGENTS.md, skills, and config.toml without its
    sign-in settings) with the source folder. Files and settings only the account
    has are kept. Sign-ins, sessions and history are never touched.
    """
    from gofer.core.provider_accounts import (
        AccountError,
        get_account,
        recopy_setup,
        require_adapter,
        setup_changes,
        setup_folder,
    )

    root = data_dir or get_data_dir()
    source = setup_from.expanduser().absolute() if setup_from else None
    try:
        profile = get_account(name, root, provider)
        if check:
            if not require_adapter(profile.subscription).setup_allowlist:
                raise AccountError("This account's provider has no shared setup to copy")
            changed = setup_changes(
                profile, root, setup_folder(source) if source is not None else None
            )
        else:
            changed = recopy_setup(name, data_dir=root, setup_source=source)
    except AccountError as exc:
        raise _account_error(exc) from exc
    if wants_json(output_format):
        emit_json({"account": name, "changed" if check else "copied": changed})
        return
    if check:
        if changed:
            console.print(f"'{name}' differs from your setup in {', '.join(changed)}.")
        else:
            console.print(f"'{name}' matches your setup.")
    elif changed:
        console.print(f"Copied {', '.join(changed)} into '{name}' again.")
    else:
        console.print(f"'{name}' already matches your setup; nothing to copy.")


@account_app.command("rm")
def rm_account_command(
    name: str = typer.Argument(..., help="Account name"),
    provider: str | None = _account_provider_option(None),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask first."),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Sign the account out with its CLI, then delete its folder."""
    from gofer.core.provider_accounts import (
        AccountError,
        get_account,
        remove_account,
        require_adapter,
    )

    root = data_dir or get_data_dir()
    try:
        label = require_adapter(get_account(name, root, provider).subscription).label
    except AccountError as exc:
        raise _account_error(exc) from exc
    if not yes and not typer.confirm(
        f"Sign out '{name}' and delete its {label} folder, including its history?"
    ):
        raise typer.Exit(1)
    try:
        remove_account(name, data_dir=root)
    except AccountError as exc:
        raise _account_error(exc) from exc
    console.print(f"Removed '{name}'.")
