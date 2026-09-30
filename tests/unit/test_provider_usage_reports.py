"""Organization reporting uses admin credentials and preserves accounting scope."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from gofer.core import provider_usage_reports as reports


def page(*rows: dict[str, Any], cursor: str | None = None) -> dict[str, Any]:
    return {"data": [{"results": list(rows)}], "has_more": cursor is not None, "next_page": cursor}


def mock_responses(monkeypatch: Any, *payloads: Any) -> list[Any]:
    calls: list[Any] = []
    responses = iter(payloads)

    def get_json(url, headers, deadline):
        calls.append((urlsplit(url), parse_qs(urlsplit(url).query), dict(headers)))
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(reports, "_get_json", get_json)
    return calls


def test_openai_paginates_scopes_and_does_not_double_count_cached_input(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Clock:
            return cls(2026, 9, 24, 12, 30, tzinfo=UTC)

    monkeypatch.setattr(reports, "datetime", Clock)
    calls = mock_responses(
        monkeypatch,
        page(
            {"input_tokens": 100, "input_cached_tokens": 70, "output_tokens": 20},
            cursor="opaque/page+1",
        ),
        page({"input_tokens": 50, "output_tokens": 10}),
        page({"amount": {"value": 1.2, "currency": "usd"}}, cursor="cost-2"),
        page({"amount": {"value": 0.3, "currency": "usd"}}),
    )
    windows = reports.fetch_reporting_usage(
        "openai_api", {"admin_api_key": "test-admin", "project_id": "proj-A"}
    )
    assert [w["used"] for w in windows] == [150, 30, 1.5]
    assert all(w["limit"] is None and w["remaining"] is None for w in windows)
    assert all("project proj-A" in w["label"] for w in windows)
    assert all(call[0].netloc == "api.openai.com" for call in calls)
    assert all(call[1]["project_ids[]"] == ["proj-A"] for call in calls)
    assert all(call[2]["Authorization"] == "Bearer test-admin" for call in calls)
    assert calls[1][1]["page"] == ["opaque/page+1"]
    assert calls[3][1]["page"] == ["cost-2"]
    assert calls[0][1]["start_time"] == [str(int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()))]
    assert calls[0][1]["end_time"] == [str(int(Clock.now().timestamp()))]


def test_anthropic_includes_cache_tokens_converts_cents_and_filters_workspace_costs(monkeypatch):
    calls = mock_responses(
        monkeypatch,
        page(
            {
                "uncached_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 30,
                    "ephemeral_5m_input_tokens": 40,
                },
                "output_tokens": 50,
            }
        ),
        page(
            {"amount": "90000", "currency": "USD", "workspace_id": "other"},
            {"amount": "123.456", "currency": "USD", "workspace_id": "wrk-A"},
            cursor="next",
        ),
        page({"amount": "0.544", "currency": "USD", "workspace_id": "wrk-A"}),
    )
    windows = reports.fetch_reporting_usage(
        "anthropic_api", {"admin_api_key": "test-admin", "workspace_id": "wrk-A"}
    )
    assert [w["used"] for w in windows] == [370, 50, 1.24]
    assert all("workspace wrk-A" in w["label"] for w in windows)
    assert calls[0][1]["workspace_ids[]"] == ["wrk-A"]
    assert calls[1][1]["group_by[]"] == ["workspace_id"]
    assert "workspace_ids[]" not in calls[1][1]
    assert all(call[2]["x-api-key"] == "test-admin" for call in calls)
    assert all(call[2]["anthropic-version"] == "2023-06-01" for call in calls)


@pytest.mark.parametrize("provider", ["openai_api", "anthropic_api"])
def test_empty_reports_are_zero_org_usage_without_manufacturing_allowance(monkeypatch, provider):
    mock_responses(monkeypatch, page(), page())
    windows = reports.fetch_reporting_usage(provider, {"admin_api_key": "test-admin"})
    assert [w["used"] for w in windows] == [0, 0, 0]
    assert all("organization" in w["label"] and w["remaining"] is None for w in windows)


@pytest.mark.parametrize("code", [401, 403])
def test_regular_key_or_missing_admin_access_propagates_http_error(monkeypatch, code):
    mock_responses(
        monkeypatch, urllib.error.HTTPError("https://api.openai.com", code, "denied", {}, None)
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "ordinary-inference-key"})
    assert exc.value.code == code


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": [], "has_more": "false"},
        {"data": [{}], "has_more": False},
        {"data": [{"results": [None]}], "has_more": False},
        {"data": [], "has_more": True, "next_page": None},
    ],
)
def test_malformed_response_never_becomes_zero_usage(monkeypatch, payload):
    mock_responses(monkeypatch, payload)
    with pytest.raises(ValueError):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})


def test_failed_second_report_does_not_return_partial_tokens(monkeypatch):
    mock_responses(
        monkeypatch, page({"input_tokens": 100, "output_tokens": 20}), TimeoutError("timeout")
    )
    with pytest.raises(TimeoutError):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})


def test_repeated_cursor_and_page_limit_fail_instead_of_partial_totals(monkeypatch):
    mock_responses(monkeypatch, page(cursor="same"), page(cursor="same"))
    with pytest.raises(ValueError, match="pagination"):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})
    monkeypatch.setattr(reports, "MAX_PAGES", 1)
    mock_responses(monkeypatch, page(cursor="first"))
    with pytest.raises(ValueError, match="page limit"):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})


@pytest.mark.parametrize("tokens", [None, True, -1, "10", 2.5])
def test_invalid_token_counts_fail(monkeypatch, tokens):
    mock_responses(monkeypatch, page({"input_tokens": tokens, "output_tokens": 20}))
    with pytest.raises(ValueError, match="token"):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})


@pytest.mark.parametrize(
    "amount", [None, {"value": "NaN", "currency": "usd"}, {"value": 2, "currency": "eur"}]
)
def test_invalid_cost_or_currency_fails(monkeypatch, amount):
    mock_responses(monkeypatch, page(), page({"amount": amount}))
    with pytest.raises(ValueError):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": "test-admin"})


def test_anthropic_does_not_accept_missing_workspace_group(monkeypatch):
    mock_responses(monkeypatch, page(), page({"amount": "100", "currency": "USD"}))
    with pytest.raises(ValueError, match="workspace"):
        reports.fetch_reporting_usage(
            "anthropic_api", {"admin_api_key": "test-admin", "workspace_id": "wrk-A"}
        )


@pytest.mark.parametrize("key", ["", "bad\nheader", "too long" * 2000])
def test_bad_key_rejected_before_network(monkeypatch, key):
    calls = mock_responses(monkeypatch)
    with pytest.raises(ValueError):
        reports.fetch_reporting_usage("openai_api", {"admin_api_key": key})
    assert not calls


def test_http_reader_bounds_bytes_timeout_and_refuses_redirects(monkeypatch):
    handlers = []
    timeouts = []

    class Opener:
        def open(self, request, timeout):
            timeouts.append(timeout)
            assert request.full_url.startswith("https://api.openai.com/")
            return io.BytesIO(json.dumps({"data": []}).encode())

    def build_opener(handler):
        handlers.append(handler)
        return Opener()

    monkeypatch.setattr(reports.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(reports.time, "monotonic", lambda: 10.0)
    assert reports._get_json("https://api.openai.com/v1/test", {}, 12) == {"data": []}
    assert timeouts == [2]
    assert (
        handlers[0].redirect_request(None, None, 302, "", None, "https://attacker.invalid") is None
    )
    monkeypatch.setattr(reports, "MAX_RESPONSE_BYTES", 2)
    with pytest.raises(ValueError, match="size limit"):
        reports._get_json("https://api.openai.com/v1/test", {}, 12)
    with pytest.raises(TimeoutError):
        reports._get_json("https://api.openai.com/v1/test", {}, 9)
