"""Official organization API reports, separate from subscription allowances.

OpenAI: developers.openai.com/api/reference/resources/admin/subresources/organization/
Anthropic: platform.claude.com/docs/en/manage-claude/usage-cost-api
Reporting credentials are never forwarded to inference or custom API endpoints.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from gofer.core.http import read_response_bytes

MAX_RESPONSE_BYTES = 2_000_000
MAX_PAGES = 20
REPORT_TIMEOUT_SECONDS = 10.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _get_json(url: str, headers: dict[str, str], deadline: float) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Reporting request exceeded its time limit")
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=min(5.0, remaining)) as response:
        raw = read_response_bytes(response, MAX_RESPONSE_BYTES)
    if time.monotonic() > deadline:
        raise TimeoutError("Reporting request exceeded its time limit")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Reporting response exceeds size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Invalid reporting response")
    return payload


def _results(
    base_url: str, params: dict[str, str], headers: dict[str, str], deadline: float
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        payload = _get_json(f"{base_url}?{urllib.parse.urlencode(params)}", headers, deadline)
        data = payload.get("data")
        if not isinstance(data, list) or not isinstance(payload.get("has_more"), bool):
            raise ValueError("Invalid reporting page")
        for bucket in data:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                raise ValueError("Invalid reporting bucket")
            for result in bucket["results"]:
                if not isinstance(result, dict):
                    raise ValueError("Invalid reporting result")
                results.append(result)
        if not payload["has_more"]:
            return results
        cursor = payload.get("next_page")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
            raise ValueError("Invalid reporting pagination")
        cursors.add(cursor)
        params = {**params, "page": cursor}
    raise ValueError("Reporting pagination exceeds page limit")


def _tokens(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Invalid reported token count")
    return value


def _cost(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid reported cost")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid reported cost") from None
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError("Invalid reported cost")
    return result


def _window(identifier: str, label: str, unit: str, used: int | float) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "unit": unit,
        "used": used,
        "limit": None,
        "remaining": None,
        "remaining_percent": None,
        "resets_at": None,
        "unlimited": False,
    }


def fetch_reporting_usage(provider: str, config: dict[str, str]) -> list[dict[str, Any]]:
    """Read this UTC month's API tokens and costs using a separate reporting key.

    All pages and both reports must succeed. A failed or truncated report must not
    look like a lower usage total. These reports provide no remaining allowance.
    """
    if provider not in {"openai_api", "anthropic_api"}:
        raise ValueError("Unsupported reporting provider")
    key = config.get("admin_api_key", "").strip()
    if not key or len(key) > 8192 or any(char.isspace() for char in key):
        raise ValueError("An organization reporting API key is required")
    now = datetime.now(UTC).replace(microsecond=0)
    start = now.replace(day=1, hour=0, minute=0, second=0)
    deadline = time.monotonic() + REPORT_TIMEOUT_SECONDS
    headers = {"Accept": "application/json", "User-Agent": "Raticode-Usage/1.0"}
    cost = Decimal(0)
    input_tokens = output_tokens = 0
    if provider == "openai_api":
        headers["Authorization"] = f"Bearer {key}"
        params = {
            "start_time": str(int(start.timestamp())),
            "end_time": str(int(now.timestamp())),
            "bucket_width": "1d",
            "limit": "31",
        }
        project = config.get("project_id", "").strip()
        if project:
            params["project_ids[]"] = project
        scope = f"project {project}" if project else "organization"
        for row in _results(
            "https://api.openai.com/v1/organization/usage/completions", params, headers, deadline
        ):
            # input_tokens includes cached input, cache writes, and audio/image input.
            input_tokens += _tokens(row.get("input_tokens"))
            output_tokens += _tokens(row.get("output_tokens"))
        for row in _results(
            "https://api.openai.com/v1/organization/costs", params, headers, deadline
        ):
            amount = row.get("amount")
            if not isinstance(amount, dict) or str(amount.get("currency", "")).lower() != "usd":
                raise ValueError("Unsupported reported currency")
            cost += _cost(amount.get("value"))
        token_kind = "completion"
    else:
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        params = {
            "starting_at": start.isoformat().replace("+00:00", "Z"),
            "ending_at": now.isoformat().replace("+00:00", "Z"),
            "bucket_width": "1d",
            "limit": "31",
        }
        workspace = config.get("workspace_id", "").strip()
        usage_params = {**params, **({"workspace_ids[]": workspace} if workspace else {})}
        scope = f"workspace {workspace}" if workspace else "organization"
        for row in _results(
            "https://api.anthropic.com/v1/organizations/usage_report/messages",
            usage_params,
            headers,
            deadline,
        ):
            cache = row.get("cache_creation")
            if not isinstance(cache, dict):
                raise ValueError("Missing reported cache creation tokens")
            input_tokens += (
                _tokens(row.get("uncached_input_tokens"))
                + _tokens(row.get("cache_read_input_tokens"))
                + _tokens(cache.get("ephemeral_1h_input_tokens"))
                + _tokens(cache.get("ephemeral_5m_input_tokens"))
            )
            output_tokens += _tokens(row.get("output_tokens"))
        # Cost reports support grouping, not workspace filtering. Filter locally
        # after retrieving every page, so workspace reports never show org spend.
        cost_params = {**params, **({"group_by[]": "workspace_id"} if workspace else {})}
        for row in _results(
            "https://api.anthropic.com/v1/organizations/cost_report", cost_params, headers, deadline
        ):
            if workspace:
                if "workspace_id" not in row:
                    raise ValueError("Missing cost report workspace")
                if row["workspace_id"] != workspace:
                    continue
            if str(row.get("currency", "")).upper() != "USD":
                raise ValueError("Unsupported reported currency")
            # Anthropic reports decimal cents; OpenAI reports dollars.
            cost += _cost(row.get("amount")) / 100
        token_kind = "message"
    if not math.isfinite(float(cost)):
        raise ValueError("Invalid total reported cost")
    label = f"Month to date · {scope}"
    return [
        _window("api-month-input", f"{label} · {token_kind} input", "tokens", input_tokens),
        _window("api-month-output", f"{label} · {token_kind} output", "tokens", output_tokens),
        _window("api-month-cost", f"{label} · API spend", "USD", float(cost)),
    ]
