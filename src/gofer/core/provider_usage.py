"""Account allowance adapters. Refreshes never create a session or send a prompt.

Provider quotas have different units and scopes. Missing measurements stay null;
local token estimates must never be used to manufacture an account allowance.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from gofer.core.http import read_response_bytes
from gofer.core.provider_capabilities import ProviderId, resolve_provider_executable
from gofer.core.provider_profiles import (
    DEFAULT_DIRECT_API_BASE_URLS,
    ProfileSubscription,
    ResolvedProviderSettings,
    resolve_provider_settings,
    resolved_provider_env,
)
from gofer.core.usage_credentials import UsageCredentialError, resolve_usage_credentials
from gofer.subscriptions.acp_transport import (
    AcpRpcError,
    AcpTransportError,
    open_acp_transport,
)
from gofer.utils.paths import get_data_dir
from gofer.utils.process import env_with_executable_on_path, run_subprocess

REFRESH_TIMEOUT_SECONDS = 12
# Antigravity and Claude Code report through their own CLI, which needs time to start.
CLI_REFRESH_TIMEOUT_SECONDS = 40
# Claude's usage endpoint rate-limits aggressively and is shared with Claude Code's
# own status display. Back off after a failed read instead of retrying every refresh.
CLAUDE_FAILURE_BACKOFF_SECONDS = 900
MAX_OUTPUT_BYTES = 2_000_000
PROVIDER_LABELS = {
    "codex": "Codex",
    "claude_code": "Claude Code",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot",
    "opencode": "OpenCode",
    "antigravity": "Antigravity",
    "grok": "Grok",
    "openai_api": "OpenAI API",
    "anthropic_api": "Anthropic API",
}
DASHBOARD_URLS = {
    "codex": "https://chatgpt.com/codex/settings/usage",
    "claude_code": "https://claude.ai/settings/usage",
    "cursor": "https://cursor.com/dashboard",
    "copilot": "https://github.com/settings/copilot",
    "opencode": "https://opencode.ai/docs/providers/",
    "antigravity": "https://antigravity.google/docs/cli/commands/usage",
    "grok": "https://grok.com/",
    "openai_api": "https://platform.openai.com/usage",
    "anthropic_api": "https://console.anthropic.com/settings/usage",
}
_UNAVAILABLE = {
    "openai_api": (
        "OpenAI API usage is metered billing, not a subscription token allowance. "
        "Organization usage reports require separate admin access and do not report tokens left."
    ),
    "anthropic_api": (
        "Anthropic API usage is metered billing, separate from Claude subscription limits. "
        "Organization reports require eligible admin access and do not report tokens left."
    ),
}


def provider_usage_placeholder(provider: str, profile: str | None = None) -> dict[str, Any]:
    """Construct a serializable empty row without doing provider discovery."""
    return {
        "provider": provider,
        "profile": profile,
        "label": PROVIDER_LABELS.get(provider, provider),
        "status": "unavailable" if provider in _UNAVAILABLE else "not_checked",
        "reason": _UNAVAILABLE.get(provider, "Refresh to check account allowances."),
        "source": None,
        "observed_at": None,
        "windows": [],
        "dashboard_url": DASHBOARD_URLS.get(provider),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _percentage(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number <= 100 else None


def _reset(value: Any) -> str | None:
    try:
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # A date-only value is a provider reset date, not a local timezone.
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        elif (number := _number(value)) is not None:
            stamp = datetime.fromtimestamp(number, UTC)
        else:
            return None
        return stamp.astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _window(identifier: str, label: str, unit: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "unit": unit,
        "used": None,
        "limit": None,
        "remaining": None,
        "remaining_percent": None,
        "resets_at": None,
        "unlimited": False,
    }


def normalize_codex_limits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer the multi-bucket view, preserving percentages as percentages."""
    buckets = payload.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = payload.get("rateLimits")
        buckets = {"codex": legacy} if isinstance(legacy, dict) else {}
    windows: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        label = str(bucket.get("limitName") or key)
        for name in ("primary", "secondary"):
            raw = bucket.get(name)
            if not isinstance(raw, dict):
                continue
            used = _percentage(raw.get("usedPercent"))
            if used is None:
                continue
            minutes = _number(raw.get("windowDurationMins"))
            duration = (
                ""
                if not minutes
                else f" · {minutes / 1440:g}-day"
                if minutes % 1440 == 0
                else f" · {minutes / 60:g}-hour"
                if minutes % 60 == 0
                else f" · {minutes:g} min"
            )
            window = _window(f"{key}:{name}", f"{label} · {name}{duration}", "percent")
            window.update(
                used=used,
                limit=100.0,
                remaining=100.0 - used,
                remaining_percent=100.0 - used,
                resets_at=_reset(raw.get("resetsAt")),
            )
            windows.append(window)
        credits = bucket.get("credits")
        if isinstance(credits, dict):
            # Codex's balance is a decimal string. It is credits, never USD.
            balance = credits.get("balance")
            try:
                remaining = (
                    _number(float(balance)) if isinstance(balance, str) else _number(balance)
                )
            except ValueError:
                remaining = None
            if remaining is not None or credits.get("unlimited") is True:
                window = _window(f"{key}:credits", f"{label} · credits", "credits")
                window.update(remaining=remaining, unlimited=credits.get("unlimited") is True)
                windows.append(window)
    return windows


def normalize_copilot_quota(payload: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = payload.get("quotaSnapshots")
    if not isinstance(snapshots, dict):
        return []
    windows = []
    for name, raw in snapshots.items():
        if not isinstance(raw, dict):
            continue
        unlimited = (
            raw.get("isUnlimitedEntitlement") is True or raw.get("entitlementRequests") == -1
        )
        if raw.get("hasQuota") is False and not unlimited:
            # The plan has no such allowance, e.g. premium requests on Copilot Free.
            continue
        limit = _number(raw.get("entitlementRequests"))
        used = _number(raw.get("usedRequests"))
        percent = _percentage(raw.get("remainingPercentage"))
        if used is None and limit is None and percent is None and not unlimited:
            continue
        window = _window(str(name), str(name).replace("_", " "), "requests")
        window.update(
            used=used,
            limit=None if unlimited else limit,
            remaining=max(0.0, limit - used)
            if limit is not None and used is not None and not unlimited
            else None,
            remaining_percent=None if unlimited else percent,
            resets_at=_reset(raw.get("resetDate")),
            unlimited=unlimited,
            overage=_number(raw.get("overage")),
            overage_allowed=raw.get("overageAllowedWithExhaustedQuota") is True,
            usage_allowed_after_exhaustion=raw.get("usageAllowedWithExhaustedQuota") is True,
        )
        windows.append(window)
    return windows


def normalize_claude_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve subscription percentages and convert extra-usage cents to currency."""
    labels = {
        "five_hour": "Five-hour allowance",
        "seven_day": "Weekly allowance",
        "seven_day_opus": "Weekly Opus allowance",
        "seven_day_sonnet": "Weekly Sonnet allowance",
        "seven_day_oauth_apps": "Weekly OAuth apps allowance",
    }
    windows = []
    for name, raw in payload.items():
        if name == "extra_usage" or not isinstance(raw, dict):
            continue
        used = _percentage(raw.get("utilization"))
        if used is None:
            continue
        window = _window(name, labels.get(name, name.replace("_", " ")), "percent")
        window.update(
            used=used,
            limit=100.0,
            remaining=100.0 - used,
            remaining_percent=100.0 - used,
            resets_at=_reset(raw.get("resets_at")),
        )
        windows.append(window)
    # Newer responses, including Claude Code's /usage report, use a limits array.
    # is_active marks the currently binding limit; Claude Code still shows the others.
    limits = payload.get("limits")
    if isinstance(limits, list):
        for index, raw in enumerate(limits):
            if not isinstance(raw, dict):
                continue
            used = _percentage(raw.get("percent"))
            if used is None:
                continue
            scope = raw.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            model_name = str(model.get("display_name") or "") if isinstance(model, dict) else ""
            model_id = str(model.get("id") or "") if isinstance(model, dict) else ""
            model_key = model_id or "_".join(model_name.lower().split())
            kind = str(raw.get("kind") or raw.get("group") or "Allowance")
            legacy_id = (
                f"seven_day_{model_key}"
                if model_key
                else {"weekly": "seven_day", "weekly_all": "seven_day", "session": "five_hour"}.get(
                    kind
                )
            )
            if legacy_id and any(w["id"] == legacy_id for w in windows):
                continue
            label = labels.get(legacy_id or "", "") or kind.replace("_", " ")
            if model_key and legacy_id not in labels:
                label = f"Weekly {model_name or model_id} allowance"
            window = _window(legacy_id or f"limit:{index}", label, "percent")
            window.update(
                used=used,
                limit=100.0,
                remaining=100.0 - used,
                remaining_percent=100.0 - used,
                resets_at=_reset(raw.get("resets_at")),
            )
            windows.append(window)
    extra = payload.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled") is True:
        used_cents = _number(extra.get("used_credits"))
        limit_cents = _number(extra.get("monthly_limit"))
        if used_cents is not None or limit_cents is not None:
            currency = str(extra.get("currency") or "USD").upper()
            # The OAuth endpoint expresses its monetary fields in hundredths.
            used = used_cents / 100 if used_cents is not None else None
            limit = limit_cents / 100 if limit_cents is not None else None
            window = _window("extra_usage", "Monthly extra usage", currency)
            window.update(
                used=used,
                limit=limit,
                remaining=max(0.0, limit - used)
                if used is not None and limit is not None
                else None,
                remaining_percent=max(0.0, 100 * (1 - used / limit))
                if used is not None and limit
                else None,
            )
            windows.append(window)
    return windows


def _percent_window(identifier: str, label: str, used: float, resets_at: Any) -> dict[str, Any]:
    window = _window(identifier, label, "percent")
    window.update(
        used=used,
        limit=100.0,
        remaining=100.0 - used,
        remaining_percent=100.0 - used,
        resets_at=_reset(resets_at),
    )
    return window


def normalize_cursor_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Cursor's own usage summary: plan percentages and on-demand spend in cents."""
    reset = payload.get("billingCycleEnd")
    individual = payload.get("individualUsage")
    individual = individual if isinstance(individual, dict) else {}
    windows = []
    if payload.get("isUnlimited") is True:
        window = _window("plan", "Included usage · billing cycle", "percent")
        window.update(unlimited=True, resets_at=_reset(reset))
        windows.append(window)
    plan = individual.get("plan")
    if isinstance(plan, dict) and plan.get("enabled") is not False and not windows:
        used = _percentage(plan.get("totalPercentUsed"))
        cents, limit = _number(plan.get("used")), _number(plan.get("limit"))
        if used is None and cents is not None and limit:
            used = min(100.0, 100 * cents / limit)
        if used is not None:
            windows.append(_percent_window("plan", "Included usage · billing cycle", used, reset))
    demand = individual.get("onDemand")
    if isinstance(demand, dict) and demand.get("enabled") is True:
        cents, limit = _number(demand.get("used")), _number(demand.get("limit"))
        if cents is not None:
            used_usd, limit_usd = cents / 100, limit / 100 if limit is not None else None
            window = _window("on_demand", "On-demand spend · billing cycle", "USD")
            window.update(
                used=used_usd,
                limit=limit_usd,
                remaining=max(0.0, limit_usd - used_usd) if limit_usd is not None else None,
                remaining_percent=max(0.0, 100 * (1 - used_usd / limit_usd)) if limit_usd else None,
                resets_at=_reset(reset),
            )
            windows.append(window)
    return windows


def normalize_antigravity_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Read `agy -p /usage --output-format json` quota groups, never its text table."""
    command = payload.get("command")
    data = command.get("data") if isinstance(command, dict) else None
    groups = data.get("groups") if isinstance(data, dict) else None
    windows = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            continue
        name = str(group.get("name") or "Models")
        for bucket in group["buckets"]:
            if not isinstance(bucket, dict):
                continue
            fraction = _number(bucket.get("remaining_fraction"))
            if fraction is None or fraction > 1:
                continue
            period = str(bucket.get("window") or bucket.get("name") or "limit")
            windows.append(
                _percent_window(
                    f"{name}:{bucket.get('id') or period}",
                    f"{name} · {period}",
                    100 * (1 - fraction),
                    bucket.get("reset_time"),
                )
            )
    return windows


def normalize_grok_billing(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Grok Build credits. Amounts are hundredths of a dollar; the percent is unitless."""
    config = payload.get("config")
    if not isinstance(config, dict):
        return []
    period = config.get("currentPeriod")
    period = period if isinstance(period, dict) else {}
    kind = str(period.get("type") or "").removeprefix("USAGE_PERIOD_TYPE_").lower()
    reset = period.get("end") or config.get("billingPeriodEnd")

    def amount(name: str) -> float | None:
        value = config.get(name)
        value = value.get("val") if isinstance(value, dict) else None
        try:
            return _number(float(value)) if isinstance(value, str) else _number(value)
        except ValueError:
            return None

    windows = []
    used = _percentage(config.get("creditUsagePercent"))
    if used is not None:
        label = f"{kind.title()} credits" if kind else "Credits"
        windows.append(_percent_window("credits", label, used, reset))
    cap, spent = amount("onDemandCap"), amount("onDemandUsed")
    if cap and spent is not None:
        window = _window("on_demand", "On-demand spend", "USD")
        window.update(
            used=spent / 100,
            limit=cap / 100,
            remaining=max(0.0, cap - spent) / 100,
            remaining_percent=max(0.0, 100 * (1 - spent / cap)),
            resets_at=_reset(reset),
        )
        windows.append(window)
    balance = amount("prepaidBalance")
    if balance:
        window = _window("prepaid", "Prepaid balance", "USD")
        window.update(remaining=balance / 100)
        windows.append(window)
    return windows


def normalize_chatgpt_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The ChatGPT subscription windows Codex reads, from an external client's sign-in."""
    limits = payload.get("rate_limit")
    windows = []
    for name, label in (("primary_window", "Session"), ("secondary_window", "Weekly")):
        raw = limits.get(name) if isinstance(limits, dict) else None
        if not isinstance(raw, dict) or (used := _percentage(raw.get("used_percent"))) is None:
            continue
        seconds = _number(raw.get("limit_window_seconds"))
        if seconds:
            label = f"{seconds / 3600:g}-hour" if seconds < 86400 else f"{seconds / 86400:g}-day"
        windows.append(_percent_window(name, f"{label} allowance", used, raw.get("reset_at")))
    return windows


def _drop_past_resets(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Some providers echo an elapsed reset date. It is not a pending reset and must
    # not make a fresh reading look stale.
    now = datetime.now(UTC)
    for window in windows:
        try:
            if window.get("resets_at") and datetime.fromisoformat(window["resets_at"]) <= now:
                window["resets_at"] = None
        except (TypeError, ValueError):
            window["resets_at"] = None
    return windows


def _read_limited(path: Path) -> bytes | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_OUTPUT_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValueError("Oversized credential file")
    return data


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _user_directory(
    env: dict[str, str], profile: str | None, variable: str, default: str
) -> Path | None:
    """A profile only uses a sign-in store that its own environment points at."""
    if env.get(variable):
        return Path(env[variable]).expanduser()
    if env.get("HOME"):
        return Path(env["HOME"]).expanduser() / default
    if profile:
        return None
    return Path(os.environ.get(variable) or Path.home() / default).expanduser()


def _cursor_native_token(env: dict[str, str], profile: str | None) -> str | None:
    """Reuse the cursor-agent or Cursor app sign-in. Never refresh or write it."""
    if _account_env(env, "CURSOR_API_KEY", profile):
        # An API key can belong to a different account from the stored sign-in.
        return None
    config = _user_directory(env, profile, "XDG_CONFIG_HOME", ".config")
    if config is None:
        return None
    candidates: list[str] = []
    if (data := _read_limited(config / "cursor" / "auth.json")) is not None:
        payload = json.loads(data)
        if isinstance(payload, dict) and isinstance(payload.get("accessToken"), str):
            candidates.append(payload["accessToken"])
    app = (
        Path.home() / "Library" / "Application Support"
        if sys.platform == "darwin" and not profile
        else Path(os.environ["APPDATA"])
        if sys.platform == "win32" and not profile and os.environ.get("APPDATA")
        else config
    )
    database = app / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if database.is_file():
        try:
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2) as db:
                row = db.execute(
                    "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'"
                ).fetchone()
            if row and isinstance(row[0], str):
                candidates.append(row[0])
        except sqlite3.Error:
            pass
    for token in candidates:
        claims = _jwt_claims(token.strip())
        expiry, subject = _number(claims.get("exp")), claims.get("sub")
        if expiry and expiry > time.time() + 60 and isinstance(subject, str) and subject:
            return token.strip()
    return None


def _cursor_allowance(token: str) -> list[dict[str, Any]]:
    # cursor.com's dashboard API accepts the CLI's WorkOS session as a cookie.
    user = str(_jwt_claims(token).get("sub", "")).split("|")[-1]
    if not user or not all(c.isalnum() or c in "._-" for c in user):
        raise ValueError("Invalid Cursor session")
    request = urllib.request.Request(
        "https://cursor.com/api/usage-summary",
        headers={
            "Cookie": f"WorkosCursorSessionToken={user}%3A%3A{token}",
            "Accept": "application/json",
        },
    )
    return normalize_cursor_usage(_read_json(request))


def _read_json(request: urllib.request.Request) -> dict[str, Any]:
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=REFRESH_TIMEOUT_SECONDS) as response:
        data = read_response_bytes(response, MAX_OUTPUT_BYTES)
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValueError("Oversized usage response")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Invalid usage response")
    return payload


def _chatgpt_allowance(token: str, account: str | None) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if account:
        headers["ChatGPT-Account-Id"] = account
    request = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
    return normalize_chatgpt_usage(_read_json(request))


def _opencode_accounts(env: dict[str, str], profile: str | None) -> dict[str, Any] | None:
    """OpenCode's stored upstream sign-ins, which own the allowances it draws from."""
    if content := _account_env(env, "OPENCODE_AUTH_CONTENT", profile):
        payload = json.loads(content)
    else:
        root = _user_directory(env, profile, "XDG_DATA_HOME", ".local/share")
        if root is None:
            return None
        data = _read_limited(root / "opencode" / "auth.json")
        payload = json.loads(data) if data is not None else {}
    return payload if isinstance(payload, dict) else {}


async def _opencode_allowance(accounts: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return windows from supported subscription sign-ins and whether any had expired."""
    windows: list[dict[str, Any]] = []
    expired = False
    for name, label, fetch in (
        ("anthropic", "Claude", lambda auth: _claude_allowance(auth["access"])),
        (
            "openai",
            "ChatGPT",
            lambda auth: _chatgpt_allowance(auth["access"], auth.get("accountId")),
        ),
    ):
        auth = accounts.get(name)
        if not isinstance(auth, dict) or auth.get("type") != "oauth":
            continue
        if not isinstance(auth.get("access"), str) or not auth["access"]:
            continue
        if (_number(auth.get("expires")) or 0) <= time.time() * 1000 + 60_000:
            # OpenCode renews this sign-in when it next runs; reading it must not.
            expired = True
            continue
        for window in await asyncio.to_thread(fetch, auth):
            windows.append(
                {**window, "id": f"{name}:{window['id']}", "label": f"{label} · {window['label']}"}
            )
    return windows, expired


def _probe_directory(data_dir: Path | None) -> Path:
    # A stable directory keeps CLIs from recording a new project on every refresh.
    path = (data_dir or get_data_dir()) / "usage-probe"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _run_usage_command(
    command: list[str], env: dict[str, str], data_dir: Path | None
) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="raticode-usage-") as launchers:
        child = env_with_executable_on_path(command[0], env)
        if os.name == "posix":
            # A background refresh must never open a browser sign-in page.
            for name in ("xdg-open", "open"):
                launcher = Path(launchers) / name
                launcher.write_text("#!/bin/sh\nexit 1\n")
                launcher.chmod(0o700)
            child["BROWSER"] = str(Path(launchers) / "xdg-open")
            child["PATH"] = os.pathsep.join(
                [launchers, child.get("PATH", os.environ.get("PATH", ""))]
            )
        code, stdout, _ = await run_subprocess(
            command,
            cwd=_probe_directory(data_dir),
            env=child,
            timeout=CLI_REFRESH_TIMEOUT_SECONDS - 5,
            max_output_bytes=MAX_OUTPUT_BYTES,
        )
    return code, stdout


async def _antigravity_allowance(
    executable: str, env: dict[str, str], data_dir: Path | None
) -> tuple[str, str | None, list[dict[str, Any]]]:
    # /usage is a local command: no conversation turn or model call is made.
    code, stdout = await _run_usage_command(
        [executable, "-p", "/usage", "--output-format", "json"],
        {**env, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"},
        data_dir,
    )
    try:
        payload = json.loads(stdout)
    except ValueError:
        payload = None
    if code != 0 or not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        return "auth_required", "Run agy and sign in, then refresh usage.", []
    command = payload.get("command")
    if not isinstance(command, dict) or command.get("name") != "usage":
        return "unsupported", "Update the Antigravity CLI to read model quotas.", []
    windows = normalize_antigravity_usage(payload)
    if not windows:
        return "unavailable", "Antigravity returned no model quota measurements.", []
    return "available", None, windows


CLAUDE_USAGE_COMMAND = [
    "-p",
    "/usage",
    "--output-format",
    "stream-json",
    "--verbose",
    "--no-session-persistence",
    # The probe must not start the user's MCP servers, hooks or remote control.
    "--strict-mcp-config",
    "--settings",
    json.dumps({"disableAllHooks": True, "remoteControlAtStartup": False}),
]


async def _claude_cli_allowance(
    executable: str, env: dict[str, str], data_dir: Path | None
) -> list[dict[str, Any]] | None:
    """Ask Claude Code for its own /usage report; it reads and renews its own sign-in.

    /usage is a local command: no conversation turn or model call is made, and
    Raticode never handles the Claude credential.
    """
    _, stdout = await _run_usage_command([executable, *CLAUDE_USAGE_COMMAND], env, data_dir)
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        report = payload.get("usage_report") if isinstance(payload, dict) else None
        limits = report.get("rate_limits") if isinstance(report, dict) else None
        if isinstance(limits, dict):
            return normalize_claude_usage(limits)
    return None


def _claude_native_token(env: dict[str, str], profile: str | None) -> str | None:
    """Read only the selected Claude account; Claude owns refreshing its tokens."""
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if profile and not config_dir:
        return None
    if not profile:
        config_dir = config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    data = _read_limited(path / ".credentials.json")
    if not data:
        return None
    payload = json.loads(data)
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        return None
    scopes = oauth.get("scopes")
    if isinstance(scopes, list) and "user:profile" not in scopes:
        return None
    expiry = _number(oauth.get("expiresAt"))
    if expiry is not None and expiry <= time.time() * 1000:
        return None
    token = oauth.get("accessToken")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _claude_allowance(token: str) -> list[dict[str, Any]]:
    # This Claude account endpoint is also used by CodexBar. It is not the
    # Anthropic organization Admin API, and may change independently of it.
    request = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    return normalize_claude_usage(_read_json(request))


def _reporting_env(settings: ResolvedProviderSettings, config: dict[str, str]) -> dict[str, str]:
    """Resolve reporting-specific secrets without requiring inference credentials."""
    allowed = {
        "claude_code": {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"},
        "cursor": {"RATICODE_CURSOR_ADMIN_API_KEY", "RATICODE_CURSOR_USAGE_EMAIL"},
        "openai_api": {"RATICODE_OPENAI_ADMIN_API_KEY", "RATICODE_OPENAI_PROJECT_ID"},
        "anthropic_api": {"RATICODE_ANTHROPIC_ADMIN_API_KEY", "RATICODE_ANTHROPIC_WORKSPACE_ID"},
    }.get(settings.subscription, set())
    saved_fields = {
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth_token",
        "RATICODE_CURSOR_ADMIN_API_KEY": "admin_api_key",
        "RATICODE_CURSOR_USAGE_EMAIL": "email",
        "RATICODE_OPENAI_ADMIN_API_KEY": "admin_api_key",
        "RATICODE_OPENAI_PROJECT_ID": "project_id",
        "RATICODE_ANTHROPIC_ADMIN_API_KEY": "admin_api_key",
        "RATICODE_ANTHROPIC_WORKSPACE_ID": "workspace_id",
    }
    allowed -= {name for name, field in saved_fields.items() if config.get(field)}
    reporting = settings.model_copy(
        update={
            "api_key_env": None,
            "api_key_secret": None,
            "secret_refs": {k: v for k, v in settings.secret_refs.items() if k in allowed},
        }
    )
    return resolved_provider_env(reporting)


def _account_env(env: dict[str, str], name: str, profile: str | None) -> str:
    # A process-wide key must not silently label another account as this profile.
    return env.get(name, "" if profile else os.environ.get(name, ""))


def _codex_account_args(settings: ResolvedProviderSettings) -> list[str] | None:
    """Only forward profile/config selection flags, never an arbitrary command."""
    result: list[str] = []
    args = iter(settings.extra_args)
    for value in args:
        if value in {"-c", "--config", "-p", "--profile"}:
            argument = next(args, None)
            if argument is None:
                return None
            if value in {"-p", "--profile"}:
                result.extend(["-c", f"profile={json.dumps(argument)}"])
            else:
                result.extend([value, argument])
        elif value.startswith("--profile="):
            result.extend(["-c", f"profile={json.dumps(value.split('=', 1)[1])}"])
        elif value.startswith("--config="):
            result.append(value)
        else:
            # Unknown flags could select a different account. Don't silently
            # fall back to the default account and label it as this profile.
            return None
    return result


async def _rpc_allowance(
    provider: str, executable: str, settings: ResolvedProviderSettings, env: dict[str, str]
) -> tuple[str, str | None, str, list[dict[str, Any]]]:
    source = "codex account/rateLimits/read" if provider == "codex" else "copilot account.getQuota"
    with tempfile.TemporaryDirectory(prefix="raticode-usage-") as directory:
        if provider == "codex":
            args = _codex_account_args(settings)
            if args is None:
                return (
                    "unavailable",
                    "This profile's CLI arguments cannot be used safely for account lookup.",
                    source,
                    [],
                )
            command = [executable, "app-server", *args]
        else:
            if settings.extra_args:
                return (
                    "unavailable",
                    "This profile's CLI arguments cannot be used safely for account lookup.",
                    source,
                    [],
                )
            command = [
                executable,
                "--headless",
                "--stdio",
                "--no-auto-update",
                "--log-level",
                "none",
                "--add-dir",
                directory,
            ]
        async with open_acp_transport(
            command,
            cwd=Path(directory),
            env=env,
            timeout=REFRESH_TIMEOUT_SECONDS,
            max_output_bytes=MAX_OUTPUT_BYTES,
            content_length_framing=provider == "copilot",
            allow_missing_jsonrpc=provider == "codex",
        ) as rpc:
            if provider == "codex":
                await rpc.request(
                    "initialize",
                    {"clientInfo": {"name": "raticode_usage", "version": "1"}},
                    timeout=REFRESH_TIMEOUT_SECONDS,
                )
                await rpc.notify("initialized", {})
                account = (
                    await rpc.request(
                        "account/read", {"refreshToken": False}, timeout=REFRESH_TIMEOUT_SECONDS
                    )
                ).get("account")
                if not isinstance(account, dict):
                    return (
                        "auth_required",
                        "Sign in to Codex to read account allowances.",
                        source,
                        [],
                    )
                if account.get("type") != "chatgpt":
                    return (
                        "unavailable",
                        "This Codex connection does not use a ChatGPT subscription allowance.",
                        source,
                        [],
                    )
                payload = await rpc.request(
                    "account/rateLimits/read", {}, timeout=REFRESH_TIMEOUT_SECONDS
                )
                windows = normalize_codex_limits(payload)
            else:
                payload = await rpc.request("account.getQuota", {}, timeout=REFRESH_TIMEOUT_SECONDS)
                windows = normalize_copilot_quota(payload)
    return (
        ("available", None, source, windows)
        if windows
        else (
            "unavailable",
            "The provider returned no supported account allowance measurements.",
            source,
            [],
        )
    )


async def _grok_allowance(
    executable: str, env: dict[str, str]
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Grok's ACP billing extension reads the signed-in account without a session."""
    with tempfile.TemporaryDirectory(prefix="raticode-usage-") as directory:
        async with open_acp_transport(
            [executable, "agent", "--no-leader", "stdio"],
            cwd=Path(directory),
            env=env,
            timeout=REFRESH_TIMEOUT_SECONDS,
            max_output_bytes=MAX_OUTPUT_BYTES,
        ) as rpc:
            await rpc.request(
                "initialize",
                {"protocolVersion": 1, "clientCapabilities": {}},
                timeout=REFRESH_TIMEOUT_SECONDS,
            )
            payload = await rpc.request("_x.ai/billing", {}, timeout=REFRESH_TIMEOUT_SECONDS)
    windows = normalize_grok_billing(payload)
    if windows:
        return "available", None, windows
    return (
        "unavailable",
        "Grok reported the current billing period without a usage measurement.",
        [],
    )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # A reporting credential belongs only to the documented API origin.
        raise urllib.error.HTTPError(req.full_url, code, "Redirect rejected", headers, fp)


def _cursor_spending(api_key: str, email: str) -> list[dict[str, Any]]:
    """Read a single exact team member, following the documented spend pagination."""
    auth = base64.b64encode(f"{api_key}:".encode()).decode()
    opener = urllib.request.build_opener(_RejectRedirects())
    deadline = time.monotonic() + REFRESH_TIMEOUT_SECONDS
    for page in range(1, 21):
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise TimeoutError("Usage deadline exceeded")
        request = urllib.request.Request(
            "https://api.cursor.com/teams/spend",
            data=json.dumps({"searchTerm": email, "page": page, "pageSize": 100}).encode(),
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=min(5, remaining_time)) as response:
            data = read_response_bytes(response, MAX_OUTPUT_BYTES)
        if len(data) > MAX_OUTPUT_BYTES:
            raise ValueError("Oversized usage response")
        payload = json.loads(data)
        if not isinstance(payload, dict) or not isinstance(payload.get("teamMemberSpend"), list):
            raise ValueError("Invalid spend response")
        for member in payload["teamMemberSpend"]:
            if (
                not isinstance(member, dict)
                or str(member.get("email", "")).casefold() != email.casefold()
            ):
                continue
            cents = _number(member.get("spendCents"))
            limit = _number(member.get("effectivePerUserLimitDollars"))
            if cents is None:
                return []
            used = cents / 100
            window = _window("team-member-spend", "Current billing cycle · on-demand spend", "USD")
            window.update(
                used=used,
                limit=limit,
                remaining=max(0.0, limit - used) if limit is not None else None,
                remaining_percent=max(0.0, 100 * (1 - used / limit)) if limit else None,
            )
            return [window]
        total_pages = _number(payload.get("totalPages"))
        if total_pages is None or page >= total_pages:
            break
    return []


async def refresh_provider_usage(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> dict[str, Any]:
    """Refresh one account independently; errors never expose credentials or CLI output."""
    row = provider_usage_placeholder(provider, profile)
    if provider not in PROVIDER_LABELS:
        return {**row, "status": "unavailable", "reason": "Unknown provider."}
    try:
        settings = resolve_provider_settings(
            agent_subscription=cast(ProfileSubscription, provider),
            profile_name=profile,
            data_dir=data_dir,
        )
        config = resolve_usage_credentials(provider, profile, data_dir=data_dir)
        env = (
            _reporting_env(settings, config)
            if provider in {"claude_code", "cursor", "openai_api", "anthropic_api"}
            else resolved_provider_env(settings)
        )
    except UsageCredentialError:
        return {
            **row,
            "status": "configuration_required",
            "reason": "Unlock the OS credential store to read saved reporting credentials.",
        }
    except (ValueError, OSError):
        return {
            **row,
            "status": "configuration_required",
            "reason": "The provider profile or its required credentials are unavailable.",
        }
    standard_url = DEFAULT_DIRECT_API_BASE_URLS.get(provider)
    if standard_url and (settings.api_base_url or "").rstrip("/") != standard_url:
        return {
            **row,
            "dashboard_url": None,
            "reason": (
                "This profile uses a custom API endpoint. Billing and limits belong to that "
                "service; the standard provider's account allowance does not apply."
            ),
        }
    command_line = provider in {"claude_code", "antigravity"}
    try:
        async with asyncio.timeout(
            CLI_REFRESH_TIMEOUT_SECONDS if command_line else REFRESH_TIMEOUT_SECONDS
        ):
            if provider == "cursor":
                # Profile values override the environment, including explicit empty values.
                key = config.get("admin_api_key") or _account_env(
                    env, "RATICODE_CURSOR_ADMIN_API_KEY", profile
                )
                email = config.get("email") or _account_env(
                    env, "RATICODE_CURSOR_USAGE_EMAIL", profile
                )
                if key and email:
                    # Team reporting was configured explicitly, so it takes precedence.
                    windows = await asyncio.to_thread(_cursor_spending, key, email)
                    row.update(
                        source="Cursor Admin API /teams/spend",
                        windows=windows,
                        status="available" if windows else "unavailable",
                        reason=None
                        if windows
                        else "No spending record matched the configured team member.",
                    )
                else:
                    session = (
                        None
                        if settings.extra_args or _account_env(env, "CURSOR_API_ENDPOINT", profile)
                        else await asyncio.to_thread(_cursor_native_token, env, profile)
                    )
                    if not session:
                        return {
                            **row,
                            "status": "auth_required",
                            "reason": "Run cursor-agent login to sign in, then refresh usage. "
                            "API-key and custom-endpoint connections cannot read plan usage.",
                        }
                    windows = await asyncio.to_thread(_cursor_allowance, session)
                    row.update(
                        source="Cursor account usage summary",
                        windows=windows,
                        status="available" if windows else "unavailable",
                        reason=None if windows else "Cursor returned no plan usage measurements.",
                    )
            elif provider == "claude_code":
                token: str | None = config.get("oauth_token") or _account_env(
                    env, "CLAUDE_CODE_OAUTH_TOKEN", profile
                )
                different_auth = any(
                    _account_env(env, name, profile)
                    for name in (
                        "ANTHROPIC_API_KEY",
                        "ANTHROPIC_AUTH_TOKEN",
                        "ANTHROPIC_BASE_URL",
                        "CLAUDE_CODE_USE_BEDROCK",
                        "CLAUDE_CODE_USE_VERTEX",
                        "CLAUDE_CODE_USE_FOUNDRY",
                    )
                )
                native = not token and not settings.extra_args and not different_auth
                executable = (
                    resolve_provider_executable("claude_code")
                    if native and (not profile or env.get("CLAUDE_CONFIG_DIR"))
                    else None
                )
                windows = None
                if executable:
                    # Preferred: Claude Code reads, renews and protects its own sign-in.
                    windows = await _claude_cli_allowance(executable, env, data_dir)
                    if windows is not None:
                        row["source"] = "Claude Code /usage"
                if windows is None:
                    if native:
                        token = await asyncio.to_thread(_claude_native_token, env, profile)
                    if not token:
                        return {
                            **row,
                            "status": "auth_required" if executable else "configuration_required",
                            "reason": (
                                "Claude Code could not report usage. Run claude and sign in, "
                                "then refresh."
                                if executable
                                else "Sign in with Claude Code, or connect a Claude OAuth access "
                                "token with user:profile scope in Usage settings. Anthropic API "
                                "keys and inference-only setup tokens cannot read subscription "
                                "limits."
                            ),
                            "retry_after_seconds": CLAUDE_FAILURE_BACKOFF_SECONDS,
                        }
                    windows = await asyncio.to_thread(_claude_allowance, token)
                    row["source"] = "Claude OAuth /api/oauth/usage"
                row.update(
                    windows=windows,
                    status="available" if windows else "unavailable",
                    reason=None if windows else "Claude returned no subscription usage windows.",
                )
            elif provider == "opencode":
                accounts = await asyncio.to_thread(_opencode_accounts, env, profile)
                if accounts is None:
                    return {
                        **row,
                        "status": "unavailable",
                        "reason": "This profile does not point at its own OpenCode sign-in.",
                    }
                windows, expired = await _opencode_allowance(accounts)
                row.update(
                    source="OpenCode subscription sign-ins",
                    windows=windows,
                    status="available" if windows else "unavailable",
                    reason=None
                    if windows
                    else "OpenCode's subscription sign-in has expired. It renews the next time "
                    "OpenCode runs."
                    if expired
                    else "OpenCode is not signed in to a Claude or ChatGPT subscription. Free "
                    "models and API keys have no allowance to report.",
                )
            elif provider in {"antigravity", "grok"}:
                executable = resolve_provider_executable(cast(ProviderId, provider))
                if not executable:
                    return {
                        **row,
                        "status": "not_installed",
                        "reason": "Install this provider CLI to read account allowances.",
                    }
                if settings.extra_args:
                    return {
                        **row,
                        "status": "unavailable",
                        "reason": "This profile's CLI arguments cannot be used safely for "
                        "account lookup.",
                    }
                if provider == "antigravity":
                    status, reason, windows = await _antigravity_allowance(
                        executable, env, data_dir
                    )
                    source = "agy /usage"
                else:
                    status, reason, windows = await _grok_allowance(executable, env)
                    source = "grok _x.ai/billing"
                row.update(status=status, reason=reason, source=source, windows=windows)
            elif provider in {"openai_api", "anthropic_api"}:
                from gofer.core.provider_usage_reports import fetch_reporting_usage

                prefix = "OPENAI" if provider == "openai_api" else "ANTHROPIC"
                key = config.get("admin_api_key") or _account_env(
                    env, f"RATICODE_{prefix}_ADMIN_API_KEY", profile
                )
                if not key:
                    return {
                        **row,
                        "status": "configuration_required",
                        "reason": "Open Settings → Usage → Configure reporting to add an "
                        "organization Admin API key. Inference API keys cannot read usage reports.",
                    }
                report_config = {**config, "admin_api_key": key}
                scope = "project_id" if provider == "openai_api" else "workspace_id"
                if scope not in report_config:
                    value = _account_env(env, f"RATICODE_{prefix}_{scope.upper()}", profile)
                    if value:
                        report_config[scope] = value
                windows = await asyncio.to_thread(fetch_reporting_usage, provider, report_config)
                row.update(
                    source=f"{PROVIDER_LABELS[provider]} organization usage report",
                    windows=windows,
                    status="available" if windows else "unavailable",
                    reason="Organization usage is metered billing; no token allowance is reported."
                    if windows
                    else "No usage records were returned for the reporting period.",
                )
            elif provider in {"codex", "copilot"}:
                executable = resolve_provider_executable(cast(ProviderId, provider))
                if not executable:
                    return {
                        **row,
                        "status": "not_installed",
                        "reason": "Install this provider CLI to read account allowances.",
                    }
                status, reason, source, windows = await _rpc_allowance(
                    provider, executable, settings, env
                )
                row.update(status=status, reason=reason, source=source, windows=windows)
            else:
                return row
        if row["status"] == "available":
            row["observed_at"] = datetime.now(UTC).isoformat()
            _drop_past_resets(row["windows"])
    except AcpRpcError as exc:
        message = str(exc).lower()
        if exc.code == -32601:
            row.update(
                status="unsupported",
                reason="This CLI version does not support account usage lookup. Update the CLI.",
            )
        elif any(
            term in message
            for term in (
                "unauthorized",
                "unauthenticated",
                "not authenticated",
                "not logged in",
                "not signed in",
                "authentication",
                "sign in",
            )
        ):
            row.update(
                status="auth_required",
                reason="Sign in to this provider again to read account allowances.",
            )
        else:
            row.update(status="error", reason="The provider rejected the account usage request.")
    except urllib.error.HTTPError as exc:
        row.update(
            status="auth_required" if exc.code in {401, 403} else "error",
            reason=(
                "Claude usage access was denied. Reconnect an OAuth token with user:profile "
                "scope or sign in again with Claude Code. Anthropic API keys do not apply."
                if provider == "claude_code" and exc.code in {401, 403}
                else "Sign in to this provider again to read account allowances."
                if provider in {"cursor", "opencode"} and exc.code in {401, 403}
                else "The provider rate-limited usage requests. Try refreshing later."
                if exc.code == 429
                else "Reporting API request failed. Check credentials and account access."
            ),
        )
        if exc.code == 429 or provider == "claude_code":
            retry = exc.headers.get("Retry-After") if exc.headers else None
            row["rate_limited"] = exc.code == 429
            row["retry_after_seconds"] = max(
                CLAUDE_FAILURE_BACKOFF_SECONDS,
                int(retry) if isinstance(retry, str) and retry.isdigit() else 0,
            )
    except (TimeoutError, AcpTransportError) as exc:
        timed_out = isinstance(exc, TimeoutError) or any(
            term in str(exc).lower() for term in ("timed out", "time limit")
        )
        row.update(
            status="error",
            reason="The usage request timed out. Try refreshing again."
            if timed_out
            else "The provider closed its usage connection or returned an unsupported response.",
        )
    except (OSError, ValueError, RuntimeError):
        row.update(
            status="error",
            reason="Could not read provider usage. Check the provider connection and retry.",
        )
    return row
