"""Providers, usage, projects, paired devices, computers and live threads through the backend.

Providers' commands join the existing ``provider`` group next to ``provider profile``;
the rest are small groups of their own. All are reads, except provider settings and
steering or stopping a live thread turn.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ExitCode, Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, failure, finish, session, table

usage_app = typer.Typer(
    help="Provider usage and limits the backend has recorded.", no_args_is_help=True
)
projects_app = typer.Typer(
    help="Projects the backend knows about (read only).", no_args_is_help=True
)
devices_app = typer.Typer(
    help="Paired devices (read only; pairing stays in the app).", no_args_is_help=True
)
computers_app = typer.Typer(
    help=(
        "The computers Raticode can run work on: this one and each paired desktop, with "
        "its worker limit, running workers and load."
    ),
    no_args_is_help=True,
)
threads_app = typer.Typer(
    help=(
        "Follow, steer or stop a live thread turn by its IDs. The thread list itself "
        "lives in the desktop window, so listing and starting threads isn't available."
    ),
    no_args_is_help=True,
)

_PROVIDER_FIELDS = {"enabled", "executable", "defaultModel", "defaultEffort", "deniedModels"}


# Providers and models --------------------------------------------------------------


def list_providers(
    refresh: bool = typer.Option(False, "--refresh", help="Look for provider CLIs again."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List providers: installed, enabled, version and default model."""
    current = session("provider list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(p.get("id")),
                str(p.get("displayName") or ""),
                "yes" if p.get("available") else "no",
                "yes" if p.get("enabled") else "no",
                str(p.get("version") or ""),
                str(p.get("defaultModel") or ""),
            ]
            for p in data
        ]
        return table(rows, ["ID", "NAME", "INSTALLED", "ENABLED", "VERSION", "DEFAULT MODEL"])

    finish(
        current,
        lambda b, _o: b.request(
            "GET", "/provider/capabilities", query={"refresh": "1" if refresh else None}
        ).get("providers", []),
        text,
    )


def list_models(
    provider: str = typer.Argument(..., help="Provider ID from `provider list`."),
    refresh: bool = typer.Option(False, "--refresh", help="Ask the provider CLI again."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a provider's models and effort levels, and which models are blocked."""
    current = session("provider models", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        payload = backend.request(
            "GET",
            "/provider/capabilities",
            query={"provider": provider, "refresh": "1" if refresh else None},
        )
        providers = payload.get("providers") or []
        if not providers:
            raise failure(f"Unknown provider '{provider}'", ExitCode.NOT_FOUND)
        return dict(providers[0])

    def text(data: dict[str, Any]) -> str:
        denied = set(data.get("deniedModels") or [])
        rows = []
        for model in data.get("models") or []:
            model_id = str(model.get("id") if isinstance(model, dict) else model)
            label = str(model.get("label") or "") if isinstance(model, dict) else ""
            marks = []
            if model_id == data.get("defaultModel"):
                marks.append("default")
            if model_id in denied:
                marks.append("blocked")
            rows.append([model_id, label, ", ".join(marks)])
        return table(rows, ["MODEL", "LABEL", "NOTE"]) or "No models reported."

    finish(current, fetch, text)


def provider_status(
    provider: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show whether a provider is signed in. Signing in happens in the app."""
    current = session("provider status", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: b.request("GET", "/provider/auth", query={"provider": provider}))


def provider_set(
    provider: str = typer.Argument(...),
    values: list[str] = typer.Argument(
        ...,
        help=(
            "name=value pairs: enabled, executable, defaultModel, defaultEffort, "
            "deniedModels (a JSON list)."
        ),
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change a provider's settings, such as its default model."""
    current = session("provider set", output, endpoint, token_file, data_dir)
    changes = res.parse_assignments(current, values)
    unknown = set(changes) - _PROVIDER_FIELDS
    if unknown:
        res.invalid(current, f"Unknown provider setting: {', '.join(sorted(unknown))}")

    def save(backend: Backend, out: Output) -> Any:
        require_user("change provider settings")
        return backend.request("POST", "/provider/settings", {"provider": provider, **changes})

    finish(current, save, lambda d: f"Saved {provider} settings.")


def register_provider_commands(app: typer.Typer) -> None:
    app.command("list")(list_providers)
    app.command("models")(list_models)
    app.command("status")(provider_status)
    app.command("set")(provider_set)


# Usage -----------------------------------------------------------------------------


USAGE_RANGE_HELP = "Period: 24h, 7d, 30d, 3mo, 6mo, 1y, 5y or all. Default 30d."
USAGE_BY_HELP = (
    "Split tokens per time bucket by provider or by account. Default provider. "
    "JSON output always has both: series.providers and series.accounts."
)
USAGE_BY = ("provider", "account")


def _usage_by(value: str) -> str:
    choice = value.strip().lower()
    if choice not in USAGE_BY:
        raise typer.BadParameter("Use --by provider or --by account.")
    return choice


def _usage_query(
    period: str | None, days: int | None, start: str | None, end: str | None
) -> dict[str, Any]:
    """The usage endpoints' period options. The backend checks the values."""
    if start or end:
        if period not in (None, "custom"):
            raise typer.BadParameter("Use --range or --from/--to, not both.")
        if not (start and end):
            raise typer.BadParameter("A custom range needs both --from and --to.")
        if days is not None:
            raise typer.BadParameter("Use --days or --from/--to, not both.")
        return {"range": "custom", "start": start, "end": end}
    if period == "custom":
        raise typer.BadParameter("A custom range needs both --from and --to.")
    if period and days is not None:
        raise typer.BadParameter("Use --range or --days, not both.")
    if period:
        return {"range": period}
    return {"days": days} if days is not None else {"range": "30d"}


@usage_app.command("show")
def show_usage(
    period: str | None = typer.Option(None, "--range", help=USAGE_RANGE_HELP),
    start: str | None = typer.Option(
        None, "--from", help="Custom range start date, YYYY-MM-DD. Needs --to."
    ),
    end: str | None = typer.Option(
        None, "--to", help="Custom range end date, YYYY-MM-DD, included. Needs --from."
    ),
    days: int | None = typer.Option(
        None, "--days", help="Older form: 1, 7, 30, 90 (3mo), 180, 365 or 1825 days."
    ),
    by: str = typer.Option("provider", "--by", help=USAGE_BY_HELP),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show recorded usage, limits and tokens per time bucket. Asks no provider."""
    query = _usage_query(period, days, start, end)
    split = _usage_by(by)
    current = session("usage show", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("GET", "/usage/overview", query=query),
        lambda data: _usage_text(data, split),
    )


@usage_app.command("refresh")
def refresh_usage(
    period: str | None = typer.Option(None, "--range", help=USAGE_RANGE_HELP),
    start: str | None = typer.Option(
        None, "--from", help="Custom range start date, YYYY-MM-DD. Needs --to."
    ),
    end: str | None = typer.Option(
        None, "--to", help="Custom range end date, YYYY-MM-DD, included. Needs --from."
    ),
    days: int | None = typer.Option(
        None, "--days", help="Older form: 1, 7, 30, 90 (3mo), 180, 365 or 1825 days."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Ask each signed-in provider for current usage, then show it."""
    query = _usage_query(period, days, start, end)
    current = session("usage refresh", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("POST", "/usage/refresh", {}, query=query, timeout=180),
        _usage_text,
    )


@usage_app.command("alerts")
def usage_alerts(
    on: bool | None = typer.Option(
        None, "--on/--off", help="Turn limit alerts on or off. Omit to keep the setting."
    ),
    first: int | None = typer.Option(
        None, "--first", help="First alert level in percent, 1-100. Default 80."
    ),
    second: int | None = typer.Option(
        None, "--second", help="Second alert level in percent, higher than --first. Default 95."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show or change the alerts sent when an account nears its 5-hour or weekly limit."""
    current = session("usage alerts", output, endpoint, token_file, data_dir)

    def run(b: Backend, _o: Output) -> Any:
        if on is None and first is None and second is None:
            return b.request("GET", "/usage/alerts")
        require_user("change usage alerts")
        body: dict[str, Any] = {}
        if on is not None:
            body["enabled"] = on
        if first is not None or second is not None:
            saved = b.request("GET", "/usage/alerts").get("thresholds") or [80, 95]
            body["thresholds"] = [
                first if first is not None else saved[0],
                second if second is not None else saved[1],
            ]
        return b.request("POST", "/usage/alerts", body)

    def text(data: dict[str, Any]) -> str:
        levels = " and ".join(f"{level}%" for level in data.get("thresholds") or [])
        state = "on" if data.get("enabled") else "off"
        return f"Limit alerts are {state}, at {levels} of each 5-hour and weekly window."

    finish(current, run, text)


@usage_app.command("verified")
def usage_verified(
    period: str | None = typer.Option(None, "--range", help=USAGE_RANGE_HELP),
    start: str | None = typer.Option(
        None, "--from", help="Custom range start date, YYYY-MM-DD. Needs --to."
    ),
    end: str | None = typer.Option(
        None, "--to", help="Custom range end date, YYYY-MM-DD, included. Needs --from."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show how often Rem's tickets passed review on their own, as Settings > Usage does."""
    query = _usage_query(period, None, start, end)
    current = session("usage verified", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("GET", "/usage/verified-work", query=query),
        _verified_text,
    )


def _verified_text(data: dict[str, Any]) -> str:
    """Usage > Verified work as lines of text."""
    label = (data.get("range") or {}).get("label") or "this period"
    lines = [f"Verified work, {label}: {data.get('line') or ''}"]
    accepted = int(data.get("accepted") or 0)
    if accepted:
        lines.append(
            f"Repairs per ticket: {data.get('repairs_per_ticket')} "
            f"({int(data.get('repairs') or 0)} over {accepted} accepted)"
        )
    finished = int(data.get("finished") or 0)
    if finished:
        causes = ", ".join(
            f"{c.get('label')} {int(c.get('tickets') or 0)}"
            for c in data.get("blocked_causes") or []
        )
        lines.append(
            f"Blocked: {int(data.get('blocked') or 0)} of {finished} finished tickets"
            + (f" ({causes})" if causes else "")
        )
    check = data.get("project_check") or {}
    if check.get("checked"):
        lines.append(
            f"Project check failed on the first try: {int(check.get('failed_first') or 0)} "
            f"of {int(check['checked'])} checked tickets"
        )
        if "flaky" in check:
            lines.append(
                f"Flaky check runs (failed, then passed on a rerun of the same commit): "
                f"{int(check.get('flaky') or 0)}"
            )
    tokens = data.get("tokens") or {}
    if tokens.get("per_accepted") is not None:
        lines.append(
            f"Tokens per accepted ticket: {int(tokens['per_accepted']):,}"
            + (" (some calls didn't report usage)" if tokens.get("partial") else "")
        )
    steps = data.get("step_ins") or {}
    lines.append(
        f"You stepped in {int(steps.get('total') or 0)} times: "
        f"{int(steps.get('request_changes') or 0)} change requests, "
        f"{int(steps.get('manual_moves') or 0)} moves by hand"
    )
    if data.get("history_since"):
        lines.append(f"History starts {str(data['history_since'])[:10]}.")
    return "\n".join(lines)


def _usd(value: Any) -> str:
    if not isinstance(value, int | float):
        return "unknown"
    amount = float(value)
    if 0 < abs(amount) < 0.01:
        return "<$0.01" if amount > 0 else "-<$0.01"
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"


def _cost_text(cost: dict[str, Any], label: str) -> str:
    """Estimated API cost, as in Settings > Usage > Tokens in Raticode."""
    total = cost.get("total") or {}
    if not total.get("calls"):
        return ""
    prefix = "at least " if total.get("lower_bound") else ""
    lines = [f"Estimated API cost ({label}): {prefix}{_usd(total.get('cost_usd'))}"]
    rows = []
    for account in cost.get("providers") or []:
        name = str(account.get("provider") or "")
        if account.get("profile"):
            name += f" ({account['profile']})"
        known = account.get("cost_usd")
        unpriced = ", ".join(account.get("unpriced_models") or [])
        rows.append(
            [
                name,
                ("at least " if account.get("lower_bound") else "") + _usd(known),
                f"{int(account.get('unsplit_calls') or 0)}",
                unpriced,
            ]
        )
    lines.append(table(rows, ["PROVIDER", "API COST", "NO CACHE SPLIT", "NO PRICE"]))
    lines.append(f"{cost.get('method') or ''} Prices checked {cost.get('prices_checked') or '?'}.")
    return "\n".join(lines)


def _usage_text(data: dict[str, Any], by: str = "provider") -> str:
    rows = []
    for account in data.get("accounts") or []:
        windows = ", ".join(
            f"{w.get('label') or w.get('name')}: "
            f"{w.get('used_percent', w.get('usedPercent', '?'))}%"
            for w in account.get("windows") or []
        )
        status = str(account.get("status") or "")
        if account.get("needs_sign_in"):
            status = "needs sign-in"
        rows.append(
            [
                str(account.get("provider") or account.get("id")),
                str(account.get("profile") or "default"),
                status,
                windows,
                str(account.get("observed_at") or ""),
            ]
        )
    text = (
        table(rows, ["PROVIDER", "ACCOUNT", "STATUS", "LIMITS", "OBSERVED"]) or "No usage recorded."
    )
    parts = [text, _usage_pace_text(data), _usage_tokens_text(data, by)]
    if isinstance(data.get("api_cost"), dict):
        label = (data.get("range") or {}).get("label") or "this period"
        parts.append(_cost_text(data["api_cost"], label))
    if isinstance(data.get("standing_parts"), dict):
        parts.append(_standing_parts_text(data["standing_parts"]))
    return "\n\n".join(part for part in parts if part)


def _standing_parts_text(standing: dict[str, Any]) -> str:
    """Rem's biggest standing prompt parts, as on Settings > Usage. Sizes only."""
    if not standing.get("parts"):
        return ""
    rows = [
        [
            str(part.get("label") or part.get("key")),
            f"{int(part.get('sent_turns') or 0)} of {int(part.get('turns') or 0)} "
            + ("calls" if part.get("call") else "turns"),
            f"{int(part.get('average_chars') or 0):,}",
            f"~{int(part.get('sent_tokens_estimate') or 0):,}",
        ]
        for part in standing["parts"]
    ]
    turns = int(standing.get("turns") or 0)
    calls = int(standing.get("calls") or 0)
    counted = f"{turns} {'turn' if turns == 1 else 'turns'}"
    if calls:
        counted += f", {calls} Rem {'call' if calls == 1 else 'calls'}"
    lines = [
        f"Biggest standing parts, last {standing.get('days') or 7} days ({counted}):",
        table(rows, ["PART", "SENT IN", "AVERAGE CHARS", "TOKENS SENT"]),
    ]
    kinds = [kind for kind in standing.get("rem_calls") or [] if isinstance(kind, dict)]
    if kinds:
        lines.append("Rem's own calls:")
        lines.append(
            table(
                [
                    [
                        str(kind.get("label") or kind.get("call")),
                        f"{int(kind.get('calls') or 0):,}",
                        f"{int(kind.get('reasked') or 0):,}",
                        f"{int(kind.get('failed') or 0):,}",
                        f"~{int(kind.get('average_tokens_estimate') or 0):,}",
                        f"~{int(kind.get('max_tokens_estimate') or 0):,}",
                        f"~{int(kind.get('sent_tokens_estimate') or 0):,}",
                        *(_reported_tokens(kind.get(field)) for field in _CALL_TOKEN_COLUMNS),
                    ]
                    for kind in kinds
                ],
                [
                    "CALL",
                    "CALLS",
                    "RE-ASKED",
                    "FAILED",
                    "AVERAGE TOKENS",
                    "LARGEST",
                    "TOKENS SENT",
                    "INPUT",
                    "CACHE READ",
                    "CACHE WRITE",
                ],
            )
        )
        lines.append(
            "INPUT, CACHE READ and CACHE WRITE are the tokens providers reported for these "
            "calls (input includes the cache); - means none was reported."
        )
    lines.append(str(standing.get("estimate") or ""))
    return "\n".join(lines)


# The reported token sums standing_parts gives each kind of Rem's own call.
_CALL_TOKEN_COLUMNS = ("input_tokens", "cache_read_tokens", "cache_write_tokens")


def _reported_tokens(value: Any) -> str:
    """A reported token sum, or - when no call reported it."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return "-"


def _usage_pace_text(data: dict[str, Any]) -> str:
    """Each account's weekly window: its readings this week and whether it lasts to reset."""
    rows = []
    for account in data.get("accounts") or []:
        chart = account.get("weekly_burndown")
        if not isinstance(chart, dict):
            continue
        readings = chart.get("readings") or []
        used = readings[-1].get("used_percent") if readings else None
        trail = " → ".join(f"{round(r.get('used_percent') or 0)}%" for r in readings[-6:])
        if len(readings) > 6:
            trail = "… → " + trail
        rows.append(
            [
                str(account.get("provider") or account.get("id")),
                str(account.get("profile") or "default"),
                "?" if used is None else f"{round(used)}%",
                f"{int(chart.get('reading_count') or 0)}: {trail}" if trail else "0",
                str(chart.get("resets_at") or "")[:16].replace("T", " "),
                str((chart.get("pace") or {}).get("text") or ""),
            ]
        )
    if not rows:
        return ""
    return "Weekly allowance pace, from each provider's own readings\n" + table(
        rows, ["PROVIDER", "ACCOUNT", "USED", "READINGS THIS WEEK", "RESETS (UTC)", "PACE"]
    )


def _usage_tokens_text(data: dict[str, Any], by: str = "provider") -> str:
    """Tokens in Raticode per time bucket and provider or account, as in Settings > Usage."""
    series = data.get("series") or {}
    buckets = [b for b in series.get("buckets") or [] if b.get("calls")]
    period = (data.get("range") or {}).get("label") or ""
    total = (data.get("activity") or {}).get("total_tokens")
    if not buckets:
        return f"Tokens in Raticode ({period}): no calls recorded." if period else ""
    if by == "account":
        # Accounts by name, as the chart's legend shows them: "codex (work)".
        names = {
            str(a.get("key")): f"{a.get('provider')} ({a.get('name') or a.get('profile')})"
            for a in series.get("accounts") or []
        }
        field, column = "accounts", "BY ACCOUNT"
    else:
        names = {
            str(p.get("provider")): str(p.get("provider")) for p in series.get("providers") or []
        }
        field, column = "providers", "BY PROVIDER"
    rows = []
    for bucket in buckets:
        amounts = bucket.get(field) or {}
        whole = int(bucket.get("total_tokens") or 0)
        parts = ", ".join(
            f"{label} {amounts[key]:,} ({round(100 * amounts[key] / whole)}%)"
            for key, label in names.items()
            if amounts.get(key)
        )
        unknown = int(bucket.get("unknown_calls") or 0)
        rows.append(
            [
                str(bucket.get("start") or "")[:16].replace("T00:00", ""),
                f"{whole:,}",
                parts or "-",
                str(unknown) if unknown else "",
            ]
        )
    heading = f"Tokens in Raticode, {period}, per {series.get('bucket') or 'day'}"
    heading += f": {total:,} reported" if isinstance(total, int) else ": none reported"
    return heading + "\n" + table(rows, ["FROM", "TOKENS", column, "NO TOKEN DATA"])


# Projects --------------------------------------------------------------------------


@projects_app.command("setup")
def project_setup(
    project: Path | None = typer.Argument(
        None,
        help="Project folder. Leave it out to list setups waiting for your approval.",
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show a project's worktree setup and whether you approved it.

    The setup lives in .raticode/worktree-setup.json. Approve it in the app, where
    its commands are shown in full.
    """
    current = session("projects setup", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> Any:
        if project is None:
            return backend.request("GET", "/worktree-setup/pending").get("pending", [])
        return backend.request(
            "GET", "/worktree-setup", query={"project": str(project.expanduser().resolve())}
        )

    def text(data: Any) -> str:
        if isinstance(data, list):
            rows = [
                [str(p.get("projectPath") or ""), str(p.get("seenAt") or "")[:19]] for p in data
            ]
            return table(rows, ["PROJECT", "SEEN"]) or "No setups are waiting for approval."
        states = {
            "none": "No setup file.",
            "approved": "Approved. New worktrees run it.",
            "changed": "Changed since you approved it; new worktrees run the approved version.",
            "unapproved": "Not approved yet; new worktrees skip it.",
            "invalid": "The setup file is invalid: "
            + str((data.get("file") or {}).get("error") or ""),
        }
        lines = [f"{data['project']}: {states.get(data['state'], data['state'])}"]
        effective = (data.get("file") or {}).get("effective") or {}
        for label, key in (("Runs", "setup"), ("Links", "link"), ("Copies", "copy")):
            for item in effective.get(key) or []:
                lines.append(f"  {label}: {item}")
        return "\n".join(lines)

    finish(current, fetch, text)


@projects_app.command("list")
def list_projects(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List project folders that hold registered workflows.

    The window's list of open projects is window state; this lists the projects the
    backend's workflow registry knows.
    """
    current = session("projects list", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> list[dict[str, Any]]:
        projects: dict[str, dict[str, Any]] = {}
        for workflow in backend.request("GET", "/workflows").get("workflows", []):
            root = workflow.get("projectRoot")
            if not root:
                continue
            entry = projects.setdefault(
                root,
                {
                    "root": root,
                    "name": workflow.get("projectName") or Path(root).name,
                    "workflows": [],
                },
            )
            entry["workflows"].append(workflow.get("id"))
        return sorted(projects.values(), key=lambda item: str(item["root"]))

    def text(data: list[dict[str, Any]]) -> str:
        rows = [[str(p["name"]), str(len(p["workflows"])), str(p["root"])] for p in data]
        return table(rows, ["NAME", "WORKFLOWS", "FOLDER"]) or "No projects with workflows."

    finish(current, fetch, text)


# Devices ---------------------------------------------------------------------------


@devices_app.command("list")
def list_devices(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List paired phones and computers and when each was last seen."""
    current = session("devices list", output, endpoint, token_file, data_dir)

    def text(data: dict[str, Any]) -> str:
        if data.get("error"):
            return str(data["error"])
        rows = [
            [
                str(p.get("name") or p.get("label") or p.get("id")),
                str(p.get("kind") or p.get("platform") or ""),
                str(p.get("state") or p.get("status") or ""),
                str(p.get("last_seen") or p.get("lastSeen") or ""),
            ]
            for p in data.get("peers") or []
        ]
        header = f"Device pairing {'on' if data.get('enabled') else 'off'}"
        return header + ("\n" + table(rows, ["NAME", "KIND", "STATE", "LAST SEEN"]) if rows else "")

    finish(current, lambda b, _o: b.request("GET", "/devices"), text)


# Computers -------------------------------------------------------------------------


def _pressure_text(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0f}%"


def _computers_text(data: dict[str, Any]) -> str:
    rows = []
    for computer in data.get("computers") or []:
        limit = computer.get("max_threads")
        setting = computer.get("max_threads_setting")
        rows.append(
            [
                str(computer.get("name") or computer.get("id")),
                str(computer.get("state") or ""),
                str(computer.get("running_threads") or 0),
                (
                    "unknown"
                    if limit is None
                    else f"{limit}{' (automatic)' if setting == 0 else ''}"
                ),
                "unknown" if computer.get("capacity") is None else str(computer["capacity"]),
                _pressure_text(computer.get("cpu_pressure")),
                _pressure_text(computer.get("memory_pressure")),
                "this computer" if computer.get("this_computer") else str(computer.get("id")),
            ]
        )
    listed = table(rows, ["NAME", "STATE", "RUNNING", "LIMIT", "ROOM", "CPU", "MEMORY", "ID"])
    if not listed:
        return "No computers."
    running = _running_lines(data.get("computers") or [])
    return listed + ("\n\nRunning workers\n" + "\n".join(running) if running else "")


def _running_lines(computers: list[dict[str, Any]]) -> list[str]:
    """Each computer's running workers by kind, and what this computer's are."""
    from gofer.core.worker_claims import KIND_WORDS, breakdown

    lines = []
    for computer in computers:
        kinds = computer.get("running_by_kind")
        if computer.get("state") != "online" or not isinstance(kinds, dict):
            continue
        # Counts that don't add up to the total aren't shown, as in Settings › Computers.
        words = breakdown(kinds) if sum(kinds.values()) == computer.get("running_threads") else ""
        if not words:
            continue
        lines.append(f"  {computer.get('name') or computer.get('id')}: {words}")
        for work in computer.get("running_work") or []:
            kind = KIND_WORDS.get(str(work.get("kind")), ("worker",))[0]
            lines.append(f"    - {kind}: {work.get('label')}")
    return lines


@computers_app.command("list")
def list_computers(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List each computer with its state, worker limit, running workers and load.

    ROOM is how many new workers a computer can take now: its limit, lowered while
    its CPU or memory is under pressure, minus the workers running. An offline
    computer has no room, and one that just came back has room for one at first.
    Below the table, each computer's running workers by kind, and what each of this
    computer's workers is. A paired desktop shares its counts only, and an older one
    only its total.
    """
    current = session("computers list", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: b.request("GET", "/computers"), _computers_text)


@computers_app.command("set")
def set_computer(
    max_threads: int = typer.Argument(
        ..., help="Workers that may run at once, 0 to 16. 0 picks one from cores and memory."
    ),
    computer: str = typer.Option(
        "",
        "--computer",
        help="The computer's ID from `computers list`. Default: this computer.",
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change this computer's worker limit (Settings > Computers > Worker limit).

    A paired desktop's limit is changed in Raticode on that desktop.
    """
    current = session("computers set", output, endpoint, token_file, data_dir)

    def save(backend: Backend, out: Output) -> Any:
        require_user("change a computer's worker limit")
        body: dict[str, Any] = {"max_threads": max_threads}
        if computer:
            body["computer_id"] = computer
        return backend.request("POST", "/computers", body)

    finish(current, save, _computers_text)


def _placement_text(data: dict[str, Any]) -> str:
    names = {c.get("id"): str(c.get("name") or c.get("id")) for c in data.get("computers") or []}
    rows = []
    for consumer in data.get("consumers") or []:
        shared = set(consumer.get("shared") or [])
        missing = set(consumer.get("missing_computers") or [])
        computers = [
            names.get(c, c)
            + (" (shared)" if c in shared else "")
            + (" (missing)" if c in missing else "")
            for c in consumer.get("computers") or []
        ]
        rows.append(
            [
                str(consumer.get("name") or consumer.get("consumer")),
                str(consumer.get("strategy") or "")
                + ("" if consumer.get("saved") else " (default)"),
                ", ".join(computers) or "-",
                str(consumer.get("consumer")),
            ]
        )
    order = [names.get(c, c) for c in data.get("order") or []]
    listed = table(rows, ["FOR", "STRATEGY", "COMPUTERS", "ID"]) or "No placements."
    # A placement whose work can't start anywhere says so under the table.
    notices = [
        f"{consumer.get('name') or consumer.get('consumer')}: {consumer['notice']}"
        for consumer in data.get("consumers") or []
        if isinstance(consumer.get("notice"), str) and consumer["notice"]
    ]
    if notices:
        listed = f"{listed}\n\n" + "\n".join(notices)
    return f"Order: {', '.join(order)}\n\n{listed}" if order else listed


@computers_app.command("placement")
def show_placement(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show where Rem and each organization run work: strategy and assigned computers.

    overflow fills one computer to its worker limit, then the next. balanced spreads
    work evenly across computers with room. manual uses only the assigned computers.
    """
    current = session("computers placement", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: b.request("GET", "/computers/placement"), _placement_text)


@computers_app.command("place")
def set_placement(
    consumer: str = typer.Argument(
        ..., help="rem, or org:<organization ID> from `computers placement`."
    ),
    strategy: str = typer.Argument(..., help="overflow, balanced or manual."),
    computer: list[str] = typer.Option(
        [],
        "--computer",
        help="A computer ID from `computers list`, for manual. Repeat for several.",
    ),
    shared: list[str] = typer.Option(
        [],
        "--shared",
        help=(
            "One of the --computer IDs that others may also be assigned. A computer in "
            "two manual placements must be shared in both. Repeat for several."
        ),
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Choose where Rem or an organization runs work.

    Manual needs at least one --computer. A computer already assigned to someone else
    can be added only when both mark it --shared.
    """
    current = session("computers place", output, endpoint, token_file, data_dir)

    def save(backend: Backend, out: Output) -> Any:
        require_user("change where work runs")
        body: dict[str, Any] = {"consumer": consumer, "strategy": strategy}
        if computer:
            body["computers"] = computer
        if shared:
            body["shared"] = shared
        return backend.request("POST", "/computers/placement", body)

    finish(current, save, _placement_text)


@computers_app.command("order")
def set_order(
    computer: list[str] = typer.Argument(
        ...,
        help=(
            "Computer IDs from `computers list`, first to fill first. Computers left out "
            "come after them."
        ),
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Set your order of computers: overflow fills the first one, then the next.

    Settings > Computers changes the same order with its move up and move down
    buttons. An ID that isn't one of your computers is refused.
    """
    current = session("computers order", output, endpoint, token_file, data_dir)

    def save(backend: Backend, out: Output) -> Any:
        require_user("change the order of computers")
        return backend.request("POST", "/computers/placement", {"order": computer})

    finish(current, save, _placement_text)


# Threads ---------------------------------------------------------------------------


@threads_app.command("follow")
def follow_turn(
    conversation_id: str = typer.Argument(..., help="The thread's conversation ID."),
    turn_id: str = typer.Argument(..., help="The turn to follow."),
    after: int = typer.Option(0, "--after", help="Skip events up to this sequence number."),
    output: opts.Output = opts.OutputFormat.ndjson,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stream a turn's events until it ends: one NDJSON event per line, then the result.

    Exit 3 if the turn is gone.
    """
    current = session("threads follow", output, endpoint, token_file, data_dir)

    def follow(backend: Backend, out: Output) -> dict[str, Any]:
        out.begin_stream()
        count = 0
        for event in backend.stream(
            "GET",
            "/chat/events",
            query={"conversationId": conversation_id, "turnId": turn_id, "after": after},
        ):
            out.event(event)
            count += 1
        return {"conversationId": conversation_id, "turnId": turn_id, "events": count}

    finish(current, follow, lambda d: None)


@threads_app.command("steering")
def steering_receipts(
    conversation_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List the steering messages sent to a thread and whether each was delivered."""
    current = session("threads steering", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("GET", "/chat/steering", query={"conversationId": conversation_id}),
    )


@threads_app.command("steer")
def steer_turn(
    conversation_id: str = typer.Argument(...),
    turn_id: str = typer.Argument(...),
    text_value: str = typer.Option(..., "--text", help="What to tell the running turn."),
    request_id: str | None = typer.Option(
        None, "--request-id", help="Retrying with the same ID never sends it twice."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Send a message to a turn that is still running. Rem's own tasks use `rem` commands."""
    current = session("threads steer", output, endpoint, token_file, data_dir)
    body = {
        "conversationId": conversation_id,
        "turnId": turn_id,
        "requestId": request_id or str(uuid.uuid4()),
        "text": text_value,
    }
    finish(current, lambda b, _o: b.request("POST", "/chat/steer", body).get("receipt"))


@threads_app.command("stop")
def stop_turn(
    conversation_id: str = typer.Argument(...),
    turn_id: str = typer.Argument(..., help="The running turn."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stop a thread's running turn."""
    current = session("threads stop", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request(
            "POST", "/chat/stop", {"conversationId": conversation_id, "turnId": turn_id}
        ),
        lambda d: "Stopped.",
    )
