"""Account usage reads are metadata-only, bounded and account/profile scoped."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_usage as usage
from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.subscriptions.acp_transport import AcpRpcError, AcpTransportError, open_acp_transport

CLAUDE_CODE_REPORT = usage._claude_cli_allowance


@pytest.fixture(autouse=True)
def isolated_usage_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(usage.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(usage, "get_data_dir", lambda: tmp_path / "data")
    reports: list[Any] = []

    async def report(executable, env, data_dir):
        reports.append((executable, env))

    # Never start a real Claude Code process from a unit test. By default Claude
    # Code produces no report, so the direct credential fallback is exercised.
    monkeypatch.setattr(usage, "_claude_cli_allowance", report)
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "CURSOR_API_KEY",
        "CURSOR_API_ENDPOINT",
        "OPENCODE_AUTH_CONTENT",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "RATICODE_OPENAI_ADMIN_API_KEY",
        "RATICODE_ANTHROPIC_ADMIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return reports


def test_codex_multiple_buckets_do_not_duplicate_legacy_or_convert_to_tokens():
    windows = usage.normalize_codex_limits(
        {
            "rateLimits": {"primary": {"usedPercent": 99}},
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {
                        "usedPercent": 25,
                        "windowDurationMins": 300,
                        "resetsAt": 1800000000,
                    },
                    "secondary": {"usedPercent": 40, "windowDurationMins": 10080},
                    "credits": {"balance": "12.50", "unlimited": False},
                },
                "review": {"primary": {"usedPercent": 10}},
            },
        }
    )
    assert [window["remaining"] for window in windows] == [75, 60, 12.5, 90]
    assert [window["unit"] for window in windows] == ["percent", "percent", "credits", "percent"]
    assert windows[0]["resets_at"] == "2027-01-15T08:00:00+00:00"
    assert windows[2]["limit"] is None


@pytest.mark.parametrize("used", [None, -1, 101, float("nan"), float("inf"), True, "20"])
def test_invalid_percentages_are_missing_not_misleading_bars(used):
    assert usage.normalize_codex_limits({"rateLimits": {"primary": {"usedPercent": used}}}) == []


def test_copilot_preserves_zero_unlimited_and_overage():
    windows = usage.normalize_copilot_quota(
        {
            "quotaSnapshots": {
                "premium_interactions": {
                    "entitlementRequests": 300,
                    "usedRequests": 315,
                    "remainingPercentage": 0,
                    "overage": 15,
                    "overageAllowedWithExhaustedQuota": True,
                    "resetDate": "2026-10-01T00:00:00Z",
                },
                "chat": {
                    "isUnlimitedEntitlement": True,
                    "entitlementRequests": -1,
                    "usedRequests": 23,
                },
                "missing": {},
            }
        }
    )
    assert len(windows) == 2
    assert windows[0]["remaining"] == 0
    assert windows[0]["remaining_percent"] == 0
    assert windows[0]["unit"] == "requests"
    assert windows[0]["overage"] == 15
    assert windows[0]["overage_allowed"] is True
    assert windows[1]["unlimited"] is True
    assert windows[1]["limit"] is None
    assert windows[1]["remaining_percent"] is None


class FakeRpc:
    def __init__(
        self,
        provider: str = "codex",
        error: Exception | None = None,
        account_type: str = "chatgpt",
    ) -> None:
        self.provider = provider
        self.error = error
        self.account_type = account_type
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method, params, *, timeout):
        self.calls.append((method, params))
        if self.error:
            raise self.error
        if method == "initialize":
            return {}
        if method == "account/read":
            return {"account": {"type": self.account_type}}
        if method == "account/rateLimits/read":
            return {"rateLimits": {"primary": {"usedPercent": 25}}}
        if method == "_x.ai/billing":
            return {"config": {"creditUsagePercent": 30, "currentPeriod": {"end": "2099-01-01"}}}
        if method == "account.getQuota":
            return {
                "quotaSnapshots": {
                    "premium_interactions": {
                        "entitlementRequests": 300,
                        "usedRequests": 50,
                        "remainingPercentage": 83.3,
                    }
                }
            }
        raise AssertionError(f"Unexpected request {method}")

    async def notify(self, method, params):
        self.calls.append((method, params))


def patch_rpc(monkeypatch: pytest.MonkeyPatch, rpc: FakeRpc) -> list[Any]:
    launches = []

    @asynccontextmanager
    async def transport(command, **kwargs):
        launches.append((command, kwargs))
        yield rpc

    monkeypatch.setattr(usage, "open_acp_transport", transport)
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: f"/bin/{provider}")
    return launches


@pytest.mark.parametrize("provider", ["codex", "copilot"])
async def test_refresh_only_requests_account_metadata(monkeypatch, tmp_path, provider):
    rpc = FakeRpc(provider)
    launches = patch_rpc(monkeypatch, rpc)
    row = await usage.refresh_provider_usage(provider, data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["observed_at"]
    assert row["windows"]
    assert [method for method, _ in rpc.calls] == (
        ["initialize", "initialized", "account/read", "account/rateLimits/read"]
        if provider == "codex"
        else ["account.getQuota"]
    )
    assert launches[0][1]["content_length_framing"] is (provider == "copilot")
    assert launches[0][1]["allow_missing_jsonrpc"] is (provider == "codex")
    assert not Path(launches[0][1]["cwd"]).exists()


async def test_codex_api_key_does_not_receive_subscription_quota(monkeypatch, tmp_path):
    rpc = FakeRpc(account_type="apiKey")
    patch_rpc(monkeypatch, rpc)
    row = await usage.refresh_provider_usage("codex", data_dir=tmp_path)
    assert row["status"] == "unavailable"
    assert not row["windows"]
    assert "account/rateLimits/read" not in [method for method, _ in rpc.calls]


async def test_profile_environment_and_account_selection_are_used(monkeypatch, tmp_path):
    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work",
                subscription="codex",
                env={"CODEX_HOME": "/work/codex"},
                secret_refs={"WORK_TOKEN": "WORK_SECRET"},
                extra_args=["--profile", "work"],
            )
        },
        tmp_path,
    )
    monkeypatch.setenv("GOFER_SECRET_WORK_SECRET", "private-token")
    launches = patch_rpc(monkeypatch, FakeRpc())
    row = await usage.refresh_provider_usage("codex", "work", data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["profile"] == "work"
    assert launches[0][0][-2:] == ["-c", 'profile="work"']
    assert launches[0][1]["env"] == {"CODEX_HOME": "/work/codex", "WORK_TOKEN": "private-token"}
    assert "private-token" not in json.dumps(row)


async def test_profile_missing_never_falls_back_to_default_account(monkeypatch, tmp_path):
    launches = patch_rpc(monkeypatch, FakeRpc())
    row = await usage.refresh_provider_usage("codex", "missing", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert not launches


@pytest.mark.parametrize(
    "error,status",
    [
        (AcpRpcError(-32601, "method unavailable, secret=abc"), "unsupported"),
        (AcpRpcError(-32000, "unauthorized secret=abc"), "auth_required"),
        (AcpRpcError(-32000, "unexpected secret=abc"), "error"),
        (AcpTransportError("ACP request timed out; secret=abc"), "error"),
    ],
)
async def test_rpc_errors_are_classified_without_leaking_details(
    monkeypatch, tmp_path, error, status
):
    patch_rpc(monkeypatch, FakeRpc(error=error))
    row = await usage.refresh_provider_usage("copilot", data_dir=tmp_path)
    assert row["status"] == status
    assert row["observed_at"] is None
    assert "abc" not in json.dumps(row)


async def test_refresh_deadline_is_bounded(monkeypatch, tmp_path):
    async def blocked(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(usage, "_rpc_allowance", blocked)
    monkeypatch.setattr(usage, "REFRESH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda _: "/bin/codex")
    row = await asyncio.wait_for(usage.refresh_provider_usage("codex", data_dir=tmp_path), 1)
    assert row["status"] == "error"


@pytest.mark.parametrize(
    "provider,status",
    [
        ("cursor", "auth_required"),
        ("opencode", "unavailable"),
        ("openai_api", "configuration_required"),
        ("anthropic_api", "configuration_required"),
    ],
)
async def test_missing_sign_in_is_explicit_and_never_runs_a_prompt(
    monkeypatch, tmp_path, provider, status
):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("RATICODE_CURSOR_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("RATICODE_CURSOR_USAGE_EMAIL", raising=False)
    launches = patch_rpc(monkeypatch, FakeRpc())
    row = await usage.refresh_provider_usage(provider, data_dir=tmp_path)
    assert row["status"] == status
    assert row["reason"] and row["dashboard_url"]
    assert row["windows"] == []
    assert not launches


def test_cursor_exact_account_match_pagination_and_actual_dollar_units(monkeypatch):
    bodies = [
        {"teamMemberSpend": [{"email": "other@example.com", "spendCents": 99999}], "totalPages": 2},
        {
            "teamMemberSpend": [
                {
                    "email": "person@example.com",
                    "spendCents": 1250,
                    "effectivePerUserLimitDollars": 50,
                }
            ],
            "totalPages": 2,
        },
    ]
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps(bodies.pop(0)).encode()

    def fetch(request, *, timeout):
        requests.append(request)
        return Response()

    class Opener:
        open = staticmethod(fetch)

    monkeypatch.setattr(usage.urllib.request, "build_opener", lambda *args: Opener())
    windows = usage._cursor_spending("secret", "person@example.com")
    assert len(requests) == 2
    assert json.loads(requests[1].data)["page"] == 2
    assert windows[0]["used"] == 12.5
    assert windows[0]["remaining"] == 37.5
    assert windows[0]["remaining_percent"] == 75
    assert windows[0]["unit"] == "USD"


def test_cursor_redirect_never_forwards_admin_credentials():
    request = usage.urllib.request.Request(
        "https://api.cursor.com/teams/spend", headers={"Authorization": "Basic private"}
    )
    with pytest.raises(usage.urllib.error.HTTPError, match="Redirect rejected"):
        usage._RejectRedirects().redirect_request(
            request, None, 302, "Redirect", {}, "https://other.example/usage"
        )


async def test_custom_api_endpoint_does_not_link_to_unrelated_billing(monkeypatch, tmp_path):
    save_provider_profiles(
        {
            "custom": ProviderProfile(
                name="custom",
                subscription="openai_api",
                api_base_url="https://custom.example/v1",
            )
        },
        tmp_path,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    row = await usage.refresh_provider_usage("openai_api", "custom", data_dir=tmp_path)
    assert row["status"] == "unavailable"
    assert row["dashboard_url"] is None
    assert "custom API endpoint" in row["reason"]


async def test_copilot_profile_arguments_do_not_fall_back_to_default_account(monkeypatch, tmp_path):
    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work", subscription="copilot", extra_args=["--account", "work"]
            )
        },
        tmp_path,
    )
    launches = patch_rpc(monkeypatch, FakeRpc("copilot"))
    row = await usage.refresh_provider_usage("copilot", "work", data_dir=tmp_path)
    assert row["status"] == "unavailable"
    assert not launches


@pytest.mark.parametrize("allow,works", [(False, False), (True, True)])
async def test_codex_jsonrpc_omission_requires_explicit_opt_in(tmp_path, allow, works):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, sys, time\n"
        "request=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'id':request['id'],'result':{'ok':True}}), flush=True)\n"
        "time.sleep(30)\n"
    )
    async with open_acp_transport(
        [sys.executable, str(script)],
        cwd=tmp_path,
        allow_missing_jsonrpc=allow,
    ) as rpc:
        if works:
            assert await rpc.request("account/read", {}) == {"ok": True}
        else:
            with pytest.raises(AcpTransportError, match="invalid JSON-RPC"):
                await rpc.request("account/read", {})


def test_claude_windows_preserve_scope_zero_reset_and_extra_usage_cents():
    windows = usage.normalize_claude_usage(
        {
            "five_hour": {"utilization": 0, "resets_at": "2026-09-24T12:00:00Z"},
            "seven_day": {"utilization": 100},
            "seven_day_sonnet": {"utilization": 20},
            "seven_day_opus": None,
            "extra_usage": {"is_enabled": True, "used_credits": 1250, "monthly_limit": 5000},
        }
    )
    assert [w["remaining"] for w in windows] == [100, 0, 80, 37.5]
    assert windows[0]["resets_at"] == "2026-09-24T12:00:00+00:00"
    assert windows[-1]["unit"] == "USD"
    assert windows[-1]["used"] == 12.5
    assert windows[-1]["limit"] == 50
    assert windows[-1]["remaining_percent"] == 75


@pytest.mark.parametrize("value", [None, True, -1, 101, float("nan"), "20"])
def test_claude_ignores_invalid_measurements_and_disabled_extra_usage(value):
    assert (
        usage.normalize_claude_usage(
            {
                "five_hour": {"utilization": value},
                "extra_usage": {"is_enabled": False, "monthly_limit": 1000, "used_credits": 100},
            }
        )
        == []
    )


def test_claude_new_model_scoped_limits_are_preserved_without_legacy_duplicates():
    windows = usage.normalize_claude_usage(
        {
            "seven_day_sonnet": {"utilization": 20},
            "limits": [
                {"kind": "weekly_scoped", "percent": 20, "scope": {"model": {"id": "sonnet"}}},
                {
                    "kind": "weekly_scoped",
                    "percent": 50,
                    "scope": {"model": {"id": "new-model", "display_name": "New model"}},
                },
                {"kind": "weekly", "percent": 99, "is_active": False},
            ],
        }
    )
    # is_active marks the binding limit only; Claude Code still shows the others.
    assert [w["id"] for w in windows] == ["seven_day_sonnet", "seven_day_new-model", "seven_day"]
    assert windows[1]["remaining"] == 50
    assert "New model" in windows[1]["label"]


def write_claude_credentials(path: Path, **changes: Any) -> None:
    path.mkdir(parents=True, exist_ok=True)
    oauth = {"accessToken": "native-secret", "scopes": ["user:profile"], **changes}
    (path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))


async def test_claude_native_credentials_fetch_metadata_without_cli(monkeypatch, tmp_path):
    write_claude_credentials(tmp_path / ".claude")
    seen = []

    def fetch(token):
        seen.append(token)
        return usage.normalize_claude_usage({"five_hour": {"utilization": 25}})

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    launches = patch_rpc(monkeypatch, FakeRpc())
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert seen == ["native-secret"]
    assert row["status"] == "available"
    assert row["windows"][0]["remaining"] == 75
    assert row["observed_at"]
    assert not launches
    assert "native-secret" not in json.dumps(row)


@pytest.mark.parametrize("changes", [{"expiresAt": 1}, {"scopes": ["user:inference"]}])
def test_claude_native_expired_and_inference_only_tokens_not_used(tmp_path, changes):
    write_claude_credentials(tmp_path / ".claude", **changes)
    assert usage._claude_native_token({}, None) is None


async def test_claude_named_profile_never_reads_default_credentials(monkeypatch, tmp_path):
    write_claude_credentials(tmp_path / ".claude")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "other-account-secret")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    save_provider_profiles(
        {"work": ProviderProfile(name="work", subscription="claude_code")}, tmp_path
    )
    row = await usage.refresh_provider_usage("claude_code", "work", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert not row["windows"]


def test_claude_named_profile_reads_its_own_config_dir(tmp_path):
    write_claude_credentials(tmp_path / "work", accessToken="work-secret")
    assert (
        usage._claude_native_token({"CLAUDE_CONFIG_DIR": str(tmp_path / "work")}, "work")
        == "work-secret"
    )


async def test_claude_saved_override_takes_precedence_and_never_uses_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-secret")
    monkeypatch.setattr(
        usage, "resolve_usage_credentials", lambda *args, **kwargs: {"oauth_token": "saved-secret"}
    )
    seen = []

    def fetch(token):
        seen.append(token)
        return []

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert seen == ["saved-secret"]


@pytest.mark.parametrize(
    "code,status,phrase",
    [
        (401, "auth_required", "user:profile"),
        (403, "auth_required", "user:profile"),
        (429, "error", "rate-limited"),
        (500, "error", "failed"),
    ],
)
async def test_claude_http_failure_is_actionable_and_redacted(
    monkeypatch, tmp_path, code, status, phrase
):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")

    def fetch(token):
        raise usage.urllib.error.HTTPError(
            "https://api.anthropic.com", code, "secret-token", {}, None
        )

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == status
    assert phrase in row["reason"]
    assert "secret-token" not in json.dumps(row)


def test_claude_request_headers_origin_and_body_limits(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == usage.MAX_OUTPUT_BYTES + 1
            return b'{"five_hour":{"utilization":40}}'

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            assert timeout == usage.REFRESH_TIMEOUT_SECONDS
            return Response()

    monkeypatch.setattr(usage.urllib.request, "build_opener", lambda *args: Opener())
    windows = usage._claude_allowance("private-token")
    assert windows[0]["remaining"] == 60
    assert calls[0].full_url == "https://api.anthropic.com/api/oauth/usage"
    assert calls[0].get_header("Authorization") == "Bearer private-token"
    assert calls[0].get_header("Anthropic-beta") == "oauth-2025-04-20"
    assert calls[0].get_method() == "GET"


async def test_corrupt_usage_config_is_sanitized(monkeypatch, tmp_path):
    def resolve(*args, **kwargs):
        raise ValueError("private configuration content")

    monkeypatch.setattr(usage, "resolve_usage_credentials", resolve)
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert "private configuration" not in json.dumps(row)


@pytest.mark.parametrize(
    "provider,key_env", [("openai_api", "OPENAI_API_KEY"), ("anthropic_api", "ANTHROPIC_API_KEY")]
)
async def test_admin_reporting_does_not_require_inference_keys(
    monkeypatch, tmp_path, provider, key_env
):
    from gofer.core import provider_usage_reports

    monkeypatch.delenv(key_env, raising=False)
    monkeypatch.setattr(
        usage,
        "resolve_usage_credentials",
        lambda *args, **kwargs: {"admin_api_key": "admin-secret"},
    )
    seen = []

    def fetch(provider, config):
        seen.append((provider, config))
        return [{"id": "tokens", "unit": "tokens", "used": 100}]

    monkeypatch.setattr(provider_usage_reports, "fetch_reporting_usage", fetch)
    row = await usage.refresh_provider_usage(provider, data_dir=tmp_path)
    assert row["status"] == "available"
    assert seen == [(provider, {"admin_api_key": "admin-secret"})]
    assert "admin-secret" not in json.dumps(row)


async def test_cursor_saved_key_and_member_are_used(monkeypatch, tmp_path):
    monkeypatch.setattr(
        usage,
        "resolve_usage_credentials",
        lambda *args, **kwargs: {"admin_api_key": "admin-secret", "email": "member@example.com"},
    )
    seen = []

    def fetch(key, email):
        seen.append((key, email))
        return []

    monkeypatch.setattr(usage, "_cursor_spending", fetch)
    await usage.refresh_provider_usage("cursor", data_dir=tmp_path)
    assert seen == [("admin-secret", "member@example.com")]


async def test_unavailable_secret_store_has_actionable_sanitized_error(monkeypatch, tmp_path):
    def resolve(*args, **kwargs):
        raise usage.UsageCredentialError("secret-store-sensitive-detail")

    monkeypatch.setattr(usage, "resolve_usage_credentials", resolve)
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert "Unlock the OS credential store" in row["reason"]
    assert "sensitive-detail" not in json.dumps(row)


@pytest.mark.parametrize(
    "name", ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"]
)
async def test_claude_alternate_auth_does_not_poll_unrelated_native_subscription(
    monkeypatch, tmp_path, name
):
    write_claude_credentials(tmp_path / ".claude")
    monkeypatch.setenv(name, "another-account")
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert not row["windows"]


async def test_profile_reporting_secret_does_not_require_unrelated_inference_secret(
    monkeypatch, tmp_path
):
    from gofer.core import provider_usage_reports

    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work",
                subscription="anthropic_api",
                secret_refs={
                    "RATICODE_ANTHROPIC_ADMIN_API_KEY": "ADMIN",
                    "ANTHROPIC_API_KEY": "MISSING",
                },
                api_key_secret="MISSING",
            )
        },
        tmp_path,
    )
    monkeypatch.setenv("GOFER_SECRET_ADMIN", "admin-secret")
    monkeypatch.delenv("GOFER_SECRET_MISSING", raising=False)
    monkeypatch.delenv("MISSING", raising=False)
    seen = []

    def fetch(provider, config):
        seen.append(config)
        return [{"id": "tokens", "unit": "tokens", "used": 100}]

    monkeypatch.setattr(provider_usage_reports, "fetch_reporting_usage", fetch)
    row = await usage.refresh_provider_usage("anthropic_api", "work", data_dir=tmp_path)
    assert row["status"] == "available"
    assert seen == [{"admin_api_key": "admin-secret"}]


async def test_saved_usage_key_overrides_missing_profile_reporting_secret(monkeypatch, tmp_path):
    save_provider_profiles(
        {
            "work": ProviderProfile(
                name="work",
                subscription="claude_code",
                secret_refs={"CLAUDE_CODE_OAUTH_TOKEN": "MISSING"},
            )
        },
        tmp_path,
    )
    monkeypatch.delenv("GOFER_SECRET_MISSING", raising=False)
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.setattr(
        usage, "resolve_usage_credentials", lambda *args, **kwargs: {"oauth_token": "saved-secret"}
    )
    seen = []

    def fetch(token):
        seen.append(token)
        return usage.normalize_claude_usage({"five_hour": {"utilization": 25}})

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    row = await usage.refresh_provider_usage("claude_code", "work", data_dir=tmp_path)
    assert row["status"] == "available"
    assert seen == ["saved-secret"]


def jwt(**claims: Any) -> str:
    import base64

    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_cursor_summary_preserves_percent_cents_and_unlimited():
    windows = usage.normalize_cursor_usage(
        {
            "billingCycleEnd": "2099-10-29T18:15:34.916Z",
            "individualUsage": {
                "plan": {"enabled": True, "used": 1500, "limit": 2000},
                "onDemand": {"enabled": True, "used": 250, "limit": 1000},
            },
        }
    )
    assert [w["id"] for w in windows] == ["plan", "on_demand"]
    assert windows[0]["used"] == 75
    assert windows[0]["unit"] == "percent"
    assert windows[0]["resets_at"].startswith("2099-10-29T18:15:34")
    assert (windows[1]["used"], windows[1]["limit"], windows[1]["unit"]) == (2.5, 10, "USD")
    reported = usage.normalize_cursor_usage(
        {"individualUsage": {"plan": {"totalPercentUsed": 12.5, "used": 0, "limit": 0}}}
    )
    assert reported[0]["remaining_percent"] == 87.5
    unlimited = usage.normalize_cursor_usage({"isUnlimited": True, "individualUsage": {}})
    assert unlimited[0]["unlimited"] is True
    assert usage.normalize_cursor_usage({"individualUsage": {"plan": {"limit": 0}}}) == []


def write_cursor_login(root: Path, token: str) -> None:
    (root / "cursor").mkdir(parents=True)
    (root / "cursor" / "auth.json").write_text(json.dumps({"accessToken": token}))


async def test_cursor_reads_cli_sign_in_without_configuration(monkeypatch, tmp_path):
    token = jwt(sub="auth0|user_123", exp=4_000_000_000)
    write_cursor_login(tmp_path / ".config", token)
    seen = []

    def fetch(session):
        seen.append(session)
        return usage.normalize_cursor_usage({"individualUsage": {"plan": {"totalPercentUsed": 5}}})

    monkeypatch.setattr(usage, "_cursor_allowance", fetch)
    row = await usage.refresh_provider_usage("cursor", data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["source"] == "Cursor account usage summary"
    assert seen == [token]
    assert token not in json.dumps(row)


@pytest.mark.parametrize(
    "token,env",
    [
        (jwt(sub="auth0|user_123", exp=1), {}),
        (jwt(exp=4_000_000_000), {}),
        ("not-a-jwt", {}),
        (jwt(sub="auth0|user_123", exp=4_000_000_000), {"CURSOR_API_KEY": "key"}),
    ],
)
def test_cursor_expired_unidentified_or_api_key_sessions_are_not_used(tmp_path, token, env):
    write_cursor_login(tmp_path / ".config", token)
    assert usage._cursor_native_token(env, None) is None


def test_cursor_profiles_only_read_their_own_sign_in(tmp_path):
    token = jwt(sub="auth0|work", exp=4_000_000_000)
    write_cursor_login(tmp_path / ".config", jwt(sub="auth0|default", exp=4_000_000_000))
    write_cursor_login(tmp_path / "work", token)
    assert usage._cursor_native_token({}, "work") is None
    assert usage._cursor_native_token({"XDG_CONFIG_HOME": str(tmp_path / "work")}, "work") == token


def test_cursor_app_database_is_a_read_only_fallback(tmp_path):
    import sqlite3

    token = jwt(sub="google-oauth2|user_9", exp=4_000_000_000)
    database = tmp_path / "config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
        db.execute("INSERT INTO ItemTable VALUES ('cursorAuth/accessToken', ?)", (token,))
    before = sorted(path.name for path in database.parent.iterdir())
    assert usage._cursor_native_token({"XDG_CONFIG_HOME": str(tmp_path / "config")}, "p") == token
    assert sorted(path.name for path in database.parent.iterdir()) == before


def test_cursor_request_uses_session_cookie_and_rejects_redirects(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b'{"individualUsage":{"plan":{"totalPercentUsed":40}}}'

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            return Response()

    handlers: list[Any] = []

    def build_opener(*args):
        handlers.extend(args)
        return Opener()

    monkeypatch.setattr(usage.urllib.request, "build_opener", build_opener)
    token = jwt(sub="auth0|user_123", exp=4_000_000_000)
    assert usage._cursor_allowance(token)[0]["remaining"] == 60
    assert calls[0].full_url == "https://cursor.com/api/usage-summary"
    assert calls[0].get_header("Cookie") == f"WorkosCursorSessionToken=user_123%3A%3A{token}"
    assert isinstance(handlers[0], usage._RejectRedirects)
    with pytest.raises(ValueError):
        usage._cursor_allowance(jwt(sub="auth0|bad/user"))


def test_antigravity_groups_keep_each_quota_and_reject_bad_fractions():
    windows = usage.normalize_antigravity_usage(
        {
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "id": "gemini-weekly",
                                    "window": "weekly",
                                    "remaining_fraction": 0.25,
                                    "reset_time": "2099-11-03T18:41:42Z",
                                },
                                {"id": "bad", "remaining_fraction": 1.5},
                                {"id": "missing"},
                            ],
                        },
                        "malformed",
                    ]
                },
            }
        }
    )
    assert len(windows) == 1
    assert windows[0]["id"] == "Gemini Models:gemini-weekly"
    assert windows[0]["label"] == "Gemini Models · weekly"
    assert windows[0]["used"] == 75
    assert windows[0]["resets_at"] == "2099-11-03T18:41:42+00:00"


@pytest.mark.parametrize(
    "code,payload,status",
    [
        (
            0,
            {
                "status": "SUCCESS",
                "command": {
                    "name": "usage",
                    "data": {
                        "groups": [{"name": "G", "buckets": [{"id": "w", "remaining_fraction": 1}]}]
                    },
                },
            },
            "available",
        ),
        (0, {"status": "SUCCESS", "num_turns": 0}, "unsupported"),
        (1, {"status": "ERROR"}, "auth_required"),
        (0, "not json", "auth_required"),
    ],
)
async def test_antigravity_reads_local_usage_command_without_a_prompt(
    monkeypatch, tmp_path, code, payload, status
):
    launches = []

    async def run(command, **kwargs):
        launches.append((command, kwargs))
        return code, json.dumps(payload) if isinstance(payload, dict) else payload, ""

    monkeypatch.setattr(usage, "run_subprocess", run)
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: "/bin/agy")
    row = await usage.refresh_provider_usage("antigravity", data_dir=tmp_path)
    assert row["status"] == status
    assert bool(row["windows"]) is (status == "available")
    command, options = launches[0]
    assert command == ["/bin/agy", "-p", "/usage", "--output-format", "json"]
    assert options["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert options["cwd"] == tmp_path / "usage-probe"
    if sys.platform != "win32":
        # Sign-in pages must never open from a background refresh.
        assert options["env"]["BROWSER"].endswith("xdg-open")
        assert not Path(options["env"]["BROWSER"]).exists()


def test_grok_billing_units_and_unknown_usage():
    windows = usage.normalize_grok_billing(
        {
            "config": {
                "creditUsagePercent": 42.5,
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "end": "2099-11-03T00:00:00+00:00",
                },
                "onDemandCap": {"val": 2000},
                "onDemandUsed": {"val": "500"},
                "prepaidBalance": {"val": 1250},
            }
        }
    )
    assert [(w["id"], w["unit"]) for w in windows] == [
        ("credits", "percent"),
        ("on_demand", "USD"),
        ("prepaid", "USD"),
    ]
    assert windows[0]["label"] == "Weekly credits"
    assert windows[0]["remaining"] == 57.5
    assert (windows[1]["used"], windows[1]["limit"], windows[1]["remaining"]) == (5, 20, 15)
    assert windows[2]["remaining"] == 12.5
    # A period without a published percentage is unknown usage, never zero.
    assert (
        usage.normalize_grok_billing(
            {"config": {"currentPeriod": {"end": "2099-01-01"}, "onDemandCap": {"val": 0}}}
        )
        == []
    )


async def test_grok_reads_billing_extension_without_a_session(monkeypatch, tmp_path):
    rpc = FakeRpc("grok")
    launches = patch_rpc(monkeypatch, rpc)
    row = await usage.refresh_provider_usage("grok", data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["windows"][0]["remaining"] == 70
    assert [method for method, _ in rpc.calls] == ["initialize", "_x.ai/billing"]
    assert launches[0][0] == ["/bin/grok", "agent", "--no-leader", "stdio"]


def write_opencode_auth(root: Path, accounts: dict[str, Any]) -> None:
    (root / "opencode").mkdir(parents=True)
    (root / "opencode" / "auth.json").write_text(json.dumps(accounts))


async def test_opencode_reuses_upstream_subscription_sign_ins(monkeypatch, tmp_path):
    write_opencode_auth(
        tmp_path / ".local" / "share",
        {
            "anthropic": {
                "type": "oauth",
                "access": "claude-secret",
                "refresh": "r",
                "expires": 9e15,
            },
            "openai": {
                "type": "oauth",
                "access": "chatgpt-secret",
                "refresh": "r",
                "expires": 9e15,
                "accountId": "acct",
            },
            "openrouter": {"type": "api", "key": "api-secret"},
        },
    )
    seen = []

    def claude(token):
        seen.append(("claude", token))
        return usage.normalize_claude_usage({"five_hour": {"utilization": 20}})

    def chatgpt(token, account):
        seen.append(("chatgpt", token, account))
        return usage.normalize_chatgpt_usage(
            {"rate_limit": {"primary_window": {"used_percent": 10, "limit_window_seconds": 18000}}}
        )

    monkeypatch.setattr(usage, "_claude_allowance", claude)
    monkeypatch.setattr(usage, "_chatgpt_allowance", chatgpt)
    row = await usage.refresh_provider_usage("opencode", data_dir=tmp_path)
    assert row["status"] == "available"
    assert seen == [("claude", "claude-secret"), ("chatgpt", "chatgpt-secret", "acct")]
    assert [w["label"] for w in row["windows"]] == [
        "Claude · Five-hour allowance",
        "ChatGPT · 5-hour allowance",
    ]
    assert "secret" not in json.dumps(row)


async def test_opencode_expired_sign_in_is_not_refreshed(monkeypatch, tmp_path):
    write_opencode_auth(
        tmp_path / ".local" / "share",
        {"anthropic": {"type": "oauth", "access": "old", "refresh": "r", "expires": 1}},
    )
    monkeypatch.setattr(usage, "_claude_allowance", pytest.fail)
    row = await usage.refresh_provider_usage("opencode", data_dir=tmp_path)
    assert row["status"] == "unavailable"
    assert "expired" in row["reason"]


def test_chatgpt_usage_request_headers(monkeypatch):
    seen = []

    def read(request):
        seen.append(request)
        return {}

    monkeypatch.setattr(usage, "_read_json", read)
    assert usage._chatgpt_allowance("token", "acct") == []
    assert seen[0].full_url == "https://chatgpt.com/backend-api/wham/usage"
    assert seen[0].get_header("Authorization") == "Bearer token"
    assert seen[0].get_header("Chatgpt-account-id") == "acct"


CLAUDE_REPORT = {
    "type": "assistant",
    "usage_report": {
        "rate_limits": {
            "limits": [
                {"kind": "session", "group": "session", "percent": 14, "is_active": True},
                {"kind": "weekly_all", "group": "weekly", "percent": 6, "is_active": False},
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 0,
                    "scope": {"model": {"display_name": "Fable"}},
                    "is_active": False,
                },
            ],
            "extra_usage": {"is_enabled": True, "monthly_limit": 2000, "used_credits": 0},
        }
    },
}


async def test_claude_code_reports_usage_without_raticode_reading_credentials(
    monkeypatch, tmp_path
):
    write_claude_credentials(tmp_path / ".claude")
    launches = []

    async def run(command, **kwargs):
        launches.append((command, kwargs))
        lines = [{"type": "system", "subtype": "init"}, CLAUDE_REPORT, {"type": "result"}]
        return 0, "not json\n" + "\n".join(json.dumps(line) for line in lines), ""

    monkeypatch.setattr(usage, "_claude_cli_allowance", CLAUDE_CODE_REPORT)
    monkeypatch.setattr(usage, "run_subprocess", run)
    monkeypatch.setattr(usage, "_claude_native_token", pytest.fail)
    monkeypatch.setattr(usage, "_claude_allowance", pytest.fail)
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: "/bin/claude")
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["source"] == "Claude Code /usage"
    assert [(w["id"], w["label"], w["used"]) for w in row["windows"]] == [
        ("five_hour", "Five-hour allowance", 14),
        ("seven_day", "Weekly allowance", 6),
        ("seven_day_fable", "Weekly Fable allowance", 0),
        ("extra_usage", "Monthly extra usage", 0),
    ]
    command, options = launches[0]
    assert command[:3] == ["/bin/claude", "-p", "/usage"]
    # The probe never persists a session or starts the user's hooks and MCP servers.
    assert {"--no-session-persistence", "--strict-mcp-config"} <= set(command)
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings == {"disableAllHooks": True, "remoteControlAtStartup": False}
    assert options["cwd"] == tmp_path / "usage-probe"


async def test_claude_code_without_a_report_falls_back_to_its_stored_sign_in(
    monkeypatch, tmp_path, isolated_usage_credentials
):
    write_claude_credentials(tmp_path / ".claude")
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: "/bin/claude")
    seen = []

    def fetch(token):
        seen.append(token)
        return usage.normalize_claude_usage({"five_hour": {"utilization": 5}})

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "available"
    assert row["source"] == "Claude OAuth /api/oauth/usage"
    assert len(isolated_usage_credentials) == 1
    assert seen == ["native-secret"]


async def test_claude_code_signed_out_backs_off(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: "/bin/claude")
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["status"] == "auth_required"
    assert row["retry_after_seconds"] == usage.CLAUDE_FAILURE_BACKOFF_SECONDS


async def test_claude_rate_limit_honors_retry_after(monkeypatch, tmp_path):
    from email.message import Message

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")
    headers = Message()
    headers["Retry-After"] = "3600"

    def fetch(token):
        raise usage.urllib.error.HTTPError("https://api.anthropic.com", 429, "busy", headers, None)

    monkeypatch.setattr(usage, "_claude_allowance", fetch)
    row = await usage.refresh_provider_usage("claude_code", data_dir=tmp_path)
    assert row["rate_limited"] is True
    assert row["retry_after_seconds"] == 3600


async def test_claude_profile_without_its_own_config_never_uses_claude_code(
    monkeypatch, tmp_path, isolated_usage_credentials
):
    save_provider_profiles(
        {"work": ProviderProfile(name="work", subscription="claude_code")}, tmp_path
    )
    monkeypatch.setattr(usage, "resolve_provider_executable", lambda provider: "/bin/claude")
    row = await usage.refresh_provider_usage("claude_code", "work", data_dir=tmp_path)
    assert row["status"] == "configuration_required"
    assert isolated_usage_credentials == []


def test_copilot_plan_without_an_allowance_is_omitted():
    windows = usage.normalize_copilot_quota(
        {
            "quotaSnapshots": {
                "chat": {"entitlementRequests": 200, "usedRequests": 25, "hasQuota": True},
                "premium_interactions": {
                    "entitlementRequests": 0,
                    "usedRequests": 0,
                    "remainingPercentage": 0,
                    "hasQuota": False,
                },
            }
        }
    )
    assert [w["id"] for w in windows] == ["chat"]


async def test_elapsed_provider_reset_dates_do_not_mark_fresh_readings_stale(monkeypatch, tmp_path):
    class Expired(FakeRpc):
        async def request(self, method, params, *, timeout):
            return {
                "quotaSnapshots": {
                    "chat": {
                        "entitlementRequests": 200,
                        "usedRequests": 25,
                        "resetDate": "2020-01-01T00:00:00Z",
                    },
                    "completions": {
                        "entitlementRequests": 200,
                        "usedRequests": 25,
                        "resetDate": "2099-01-01T00:00:00Z",
                    },
                }
            }

    patch_rpc(monkeypatch, Expired("copilot"))
    row = await usage.refresh_provider_usage("copilot", data_dir=tmp_path)
    assert [w["resets_at"] for w in row["windows"]] == [None, "2099-01-01T00:00:00+00:00"]


def test_codex_window_durations_are_readable():
    windows = usage.normalize_codex_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 1, "windowDurationMins": 300},
                "secondary": {"usedPercent": 1, "windowDurationMins": 10080},
            }
        }
    )
    assert [w["label"] for w in windows] == [
        "codex · primary · 5-hour",
        "codex · secondary · 7-day",
    ]
