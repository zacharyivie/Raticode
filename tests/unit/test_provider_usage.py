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


@pytest.fixture(autouse=True)
def isolated_usage_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(usage.Path, "home", lambda: tmp_path)
    for name in (
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
    "provider",
    ["cursor", "opencode", "antigravity", "grok", "openai_api", "anthropic_api"],
)
async def test_unsupported_polling_is_explicit_and_never_runs_a_prompt(
    monkeypatch, tmp_path, provider
):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("RATICODE_CURSOR_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("RATICODE_CURSOR_USAGE_EMAIL", raising=False)
    launches = patch_rpc(monkeypatch, FakeRpc())
    row = await usage.refresh_provider_usage(provider, data_dir=tmp_path)
    assert row["status"] == (
        "configuration_required"
        if provider in {"cursor", "openai_api", "anthropic_api"}
        else "unavailable"
    )
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
    assert len(windows) == 2
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
