"""Local model server provider profiles: Ollama, llama.cpp, LM Studio and the like.

The run tests talk real HTTP to a small OpenAI-compatible server on 127.0.0.1, so the
network policy, the chat-completions request and the usage ledger are all the real ones.
``test_live_local_model_server`` runs the same checks against a real server when
RATICODE_LIVE_LOCAL_MODEL_URL and RATICODE_LIVE_LOCAL_MODEL are set.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core import local_models
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.local_models import check_local_server, model_matches
from gofer.core.provider_profiles import (
    ProviderProfile,
    ResolvedProviderSettings,
    is_loopback_base_url,
    load_provider_profiles,
    profile_store_path,
    provider_profile_from_ui_payload,
    provider_profile_ui_payload,
    resolve_provider_settings,
    resolved_provider_env,
    save_provider_profiles,
    unresolved_provider_secret_refs,
    validate_provider_profile,
)
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import run_rattish_file
from gofer.subscriptions.direct_api import OpenAiApiSubscription
from gofer.ui.api import ProviderProfileError, check_provider_profile_payload

runner = CliRunner()


class FakeModelServer:
    """An OpenAI-compatible server: GET /v1/models and POST /v1/chat/completions."""

    def __init__(self, models: list[str], *, report_usage: bool = True) -> None:
        self.models = models
        self.report_usage = report_usage
        self.requests: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append({"method": "GET", "path": self.path, **self._headers()})
                if self.path == "/v1/models":
                    self._reply(
                        200,
                        {
                            "object": "list",
                            "data": [{"id": m, "object": "model"} for m in server.models],
                        },
                    )
                else:
                    self._reply(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append(
                    {"method": "POST", "path": self.path, "body": body, **self._headers()}
                )
                if self.path != "/v1/chat/completions":
                    self._reply(404, {"error": {"message": "not found"}})
                    return
                if body.get("model") not in server.models:
                    self._reply(404, {"error": {"message": f"model {body.get('model')} not found"}})
                    return
                payload: dict[str, Any] = {
                    "object": "chat.completion",
                    "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ready"}}],
                }
                if server.report_usage:
                    payload["usage"] = {
                        "prompt_tokens": 21,
                        "completion_tokens": 4,
                        "total_tokens": 25,
                    }
                self._reply(200, payload)

            def _headers(self) -> dict[str, Any]:
                return {"authorization": self.headers.get("Authorization")}

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakeModelServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture(autouse=True)
def _fresh_model_cache() -> Iterator[None]:
    local_models._cache.clear()
    yield
    local_models._cache.clear()


def _save_local(data_dir: Path, base_url: str, name: str = "local", **fields: Any) -> None:
    profiles = load_provider_profiles(data_dir)
    profiles[name] = ProviderProfile(
        name=name, subscription="openai_api", local=True, api_base_url=base_url, **fields
    )
    save_provider_profiles(profiles, data_dir)


def _workflow(tmp_path: Path, model: str, fields: str = "", profile: str = "local") -> Path:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Local model\n"
        f"{fields}"
        "\nNode summarize:\n  type: agent\n  provider: openai-api\n"
        f"  profile: {profile}\n  model: {model}\n  prompt: Reply with the word ready.\n",
        encoding="utf-8",
    )
    return source


# Profiles and the CLI ---------------------------------------------------------------


def test_cli_creates_a_keyless_loopback_profile_with_the_ollama_default(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    created = runner.invoke(
        app, ["provider", "profile", "create", "ollama", "--local", "--data-dir", str(data_dir)]
    )
    assert created.exit_code == 0, created.output
    assert "provider: openai-api, profile: ollama" in " ".join(created.output.split())

    stored = json.loads(profile_store_path(data_dir).read_text())["profiles"]["ollama"]
    assert stored == {
        "name": "ollama",
        "subscription": "openai_api",
        "api_base_url": "http://127.0.0.1:11434/v1",
        "local": True,
        "extra_args": [],
        "tools": [],
        "mcp_servers": [],
        "env": {},
        "secret_refs": {},
        "provider_options": {},
    }
    listed = runner.invoke(
        app, ["provider", "profile", "list", "--format", "json", "--data-dir", str(data_dir)]
    )
    assert json.loads(listed.output)[0]["local"] is True


def test_profiles_from_before_local_servers_are_stored_unchanged(tmp_path: Path) -> None:
    save_provider_profiles(
        {"api": ProviderProfile(name="api", subscription="openai_api")}, tmp_path
    )
    assert "local" not in profile_store_path(tmp_path).read_text()
    assert load_provider_profiles(tmp_path)["api"].local is False


def test_a_non_loopback_server_needs_a_key_and_warns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    base = ["provider", "profile", "create", "gpu-box", "--local", "--data-dir", str(data_dir)]
    url = ["--api-base-url", "http://192.168.1.20:11434/v1"]

    refused = runner.invoke(app, [*base, *url])
    assert refused.exit_code == 1
    assert "needs an API key" in " ".join(refused.output.split())

    created = runner.invoke(app, [*base, *url, "--api-key-secret", "GPU_BOX_KEY"])
    assert created.exit_code == 0, created.output
    text = " ".join(created.output.split())
    assert "Warning: This server isn't on this computer" in text
    assert "192.168.1.20" in text

    profile = load_provider_profiles(data_dir)["gpu-box"]
    payload = provider_profile_ui_payload(profile)
    assert payload["loopback"] is False
    assert "isn't on this computer" in payload["warning"]


def test_local_needs_the_openai_compatible_subscription(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "provider",
            "profile",
            "create",
            "x",
            "--local",
            "--subscription",
            "codex",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    with pytest.raises(ValueError, match="OpenAI-compatible API"):
        validate_provider_profile(
            ProviderProfile(
                name="x",
                subscription="anthropic_api",
                local=True,
                api_base_url="http://127.0.0.1:1/v1",
            )
        )
    with pytest.raises(ValueError, match="http:// or https://"):
        validate_provider_profile(
            ProviderProfile(
                name="x", subscription="openai_api", local=True, api_base_url="file:///v1"
            )
        )
    with pytest.raises(ValueError, match="user name, password"):
        validate_provider_profile(
            ProviderProfile(
                name="x",
                subscription="openai_api",
                local=True,
                api_base_url="http://user:pw@127.0.0.1:1/v1",
            )
        )


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:11434/v1", True),
        ("http://127.8.9.10/v1", True),
        ("http://localhost:8080/v1", True),
        ("http://[::1]:1234/v1", True),
        ("http://192.168.1.20:11434/v1", False),
        ("https://models.example.com/v1", False),
        ("http://127.0.0.1.example.com/v1", False),
        ("not a url", False),
    ],
)
def test_loopback_addresses(url: str, loopback: bool) -> None:
    assert is_loopback_base_url(url) is loopback


def test_a_loopback_profile_never_sends_the_openai_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")
    _save_local(tmp_path, "http://127.0.0.1:11434/v1")
    settings = resolve_provider_settings(
        agent_subscription="openai_api", profile_name="local", data_dir=tmp_path
    )
    assert settings.local is True
    assert settings.api_key_env is None
    assert resolved_provider_env(settings) == {}
    assert unresolved_provider_secret_refs(settings) == []


def test_a_remote_profile_key_comes_from_a_saved_workflow_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_local(tmp_path, "https://models.example.com/v1", api_key_secret="BOX_KEY")
    settings = resolve_provider_settings(
        agent_subscription="openai_api", profile_name="local", data_dir=tmp_path
    )
    assert unresolved_provider_secret_refs(settings) == ["BOX_KEY"]
    monkeypatch.setenv("BOX_KEY", "box-secret")
    assert resolved_provider_env(settings) == {"GOFER_DIRECT_API_KEY": "box-secret"}


def test_ui_payload_round_trips_a_local_profile() -> None:
    profile = provider_profile_from_ui_payload(
        {
            "name": "lm",
            "local": True,
            "api_base_url": "http://127.0.0.1:1234/v1",
            "loopback": True,
            "warning": None,
        }
    )
    assert profile.subscription == "openai_api"
    assert profile.local is True
    payload = provider_profile_ui_payload(profile)
    assert payload["local"] is True and payload["loopback"] is True
    assert "warning" not in payload
    assert "local" not in provider_profile_ui_payload(
        ProviderProfile(name="api", subscription="openai_api")
    )


def test_ollama_names_match_with_and_without_latest() -> None:
    assert model_matches("qwen3:8b", ["qwen3:8b"])
    assert model_matches("llama3.2", ["llama3.2:latest"])
    assert model_matches("llama3.2:latest", ["llama3.2"])
    assert not model_matches("qwen3", ["qwen3:8b"])


# The request -----------------------------------------------------------------------


class RecordingClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(200, {}, json.dumps(self.payload).encode())


@pytest.mark.anyio
async def test_local_calls_use_chat_completions_without_a_key_and_cost_nothing(
    tmp_path: Path,
) -> None:
    client = RecordingClient(
        {
            "model": "qwen3:8b",
            "choices": [{"message": {"content": "ready"}}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25},
        }
    )
    settings = ResolvedProviderSettings(
        profile_name="local",
        subscription="openai_api",
        model="qwen3:8b",
        api_base_url="http://127.0.0.1:11434/v1/",
        local=True,
    )
    result = await OpenAiApiSubscription(client).execute(
        "hi", tmp_path, [], [], {}, provider_settings=settings
    )
    assert result.success, result.output
    assert result.output == "ready"
    request = client.requests[0]
    assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in request.headers
    assert request.network_allowlist == ["127.0.0.1"]
    assert json.loads(request.body or b"{}") == {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert result.usage_metadata["cost_usd"] == 0.0
    assert result.usage_metadata["input_tokens"] == 21
    assert result.usage_metadata["total_tokens"] == 25


@pytest.mark.anyio
async def test_a_remote_local_server_still_needs_its_key(tmp_path: Path) -> None:
    settings = ResolvedProviderSettings(
        profile_name="box",
        subscription="openai_api",
        model="m",
        api_base_url="https://models.example.com/v1",
        api_key_secret="BOX_KEY",
        local=True,
    )
    with pytest.raises(ValueError, match="missing an API key"):
        await OpenAiApiSubscription(RecordingClient({})).execute(
            "hi", tmp_path, [], [], {}, provider_settings=settings
        )


def test_plain_openai_profiles_keep_the_policy_and_responses_api() -> None:
    settings = ResolvedProviderSettings(
        subscription="openai_api", model="gpt-5-mini", api_base_url="https://api.openai.com/v1"
    )
    request = OpenAiApiSubscription()._request("hi", "sk", settings, None)
    assert request.url.endswith("/responses")
    assert request.network_allowlist == []
    assert request.headers["Authorization"] == "Bearer sk"


# Real HTTP: check, preflight and runs ----------------------------------------------


def test_check_lists_the_servers_models_and_finds_one(tmp_path: Path) -> None:
    with FakeModelServer(["qwen3:8b", "llama3.2:latest"]) as server:
        _save_local(tmp_path, server.base_url)
        found = check_local_server("local", model="llama3.2", data_dir=tmp_path)
        missing = check_local_server("local", model="mistral", data_dir=tmp_path)
        payload = check_provider_profile_payload("local", "qwen3:8b", tmp_path)
    assert found.reachable and found.models == ["llama3.2:latest", "qwen3:8b"]
    assert found.model_available is True
    assert missing.model_available is False
    assert payload["check"]["modelAvailable"] is True
    assert payload["check"]["loopback"] is True
    assert server.requests[0]["authorization"] is None


def test_check_reports_a_server_that_isnt_running(tmp_path: Path) -> None:
    with FakeModelServer([]) as server:
        url = server.base_url
    _save_local(tmp_path, url)
    result = check_local_server("local", data_dir=tmp_path)
    assert result.reachable is False
    assert "didn't answer" in (result.error or "")
    with pytest.raises(ProviderProfileError, match="isn't a local model server"):
        save_provider_profiles(
            {"api": ProviderProfile(name="api", subscription="openai_api")}, tmp_path
        )
        check_provider_profile_payload("api", None, tmp_path)


def test_cli_check_prints_models_and_fails_for_a_missing_one(tmp_path: Path) -> None:
    with FakeModelServer(["qwen3:8b"]) as server:
        _save_local(tmp_path, server.base_url)
        ok = runner.invoke(
            app, ["provider", "profile", "check", "local", "--data-dir", str(tmp_path)]
        )
        missing = runner.invoke(
            app,
            [
                "provider",
                "profile",
                "check",
                "local",
                "--model",
                "mistral",
                "--format",
                "json",
                "--data-dir",
                str(tmp_path),
            ],
        )
    assert ok.exit_code == 0, ok.output
    assert "qwen3:8b" in ok.output
    assert missing.exit_code == 1
    assert json.loads(missing.output)["modelAvailable"] is False


def test_preflight_accepts_a_listed_model_and_names_the_others(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with FakeModelServer(["qwen3:8b", "llama3.2:latest"]) as server:
        _save_local(data_dir, server.base_url)
        ready = run_preflight(
            compile_rattish_file(_workflow(tmp_path, "qwen3:8b"), data_dir=data_dir).ir,
            data_dir=data_dir,
        )
        local_models._cache.clear()
        missing = run_preflight(
            compile_rattish_file(_workflow(tmp_path, "mistral"), data_dir=data_dir).ir,
            data_dir=data_dir,
        )
    assert ready.ready, [d.message for d in ready.diagnostics]
    assert not missing.ready
    (problem,) = [d for d in missing.diagnostics if d.severity == "error"]
    assert problem.code == "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE"
    assert "doesn't have model 'mistral'" in problem.message
    assert "llama3.2:latest, qwen3:8b" in problem.message


def test_preflight_fails_when_the_server_is_down(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with FakeModelServer([]) as server:
        url = server.base_url
    _save_local(data_dir, url)
    result = run_preflight(
        compile_rattish_file(_workflow(tmp_path, "qwen3:8b"), data_dir=data_dir).ir,
        data_dir=data_dir,
    )
    (problem,) = [d for d in result.diagnostics if d.severity == "error"]
    assert problem.code == "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE"
    assert "didn't answer" in problem.message


@pytest.mark.anyio
async def test_a_run_counts_reported_tokens_at_zero_cost_under_both_limits(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    fields = "  max-cost-usd: 0.01\n  max-tokens: 1000\n"
    with FakeModelServer(["qwen3:8b"]) as server:
        _save_local(data_dir, server.base_url)
        result = await run_rattish_file(_workflow(tmp_path, "qwen3:8b", fields), data_dir=data_dir)
    document = result.document
    assert document["status"] == "passed", document.get("error")
    assert document["usage"]["total_tokens"] == 25
    assert document["usage"]["cost_usd"] == 0.0
    assert document["usage"]["cost_unknown_calls"] == 0
    (call,) = [r for r in server.requests if r["method"] == "POST"]
    assert call["body"]["model"] == "qwen3:8b"
    assert call["authorization"] is None


@pytest.mark.anyio
async def test_a_server_that_reports_no_tokens_ends_a_max_tokens_run(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with FakeModelServer(["m"], report_usage=False) as server:
        _save_local(data_dir, server.base_url)
        capped = await run_rattish_file(
            _workflow(tmp_path, "m", "  max-tokens: 1000\n"), data_dir=data_dir
        )
        cost_only = await run_rattish_file(
            _workflow(tmp_path, "m", "  max-cost-usd: 0.01\n"), data_dir=data_dir
        )
    assert capped.document["error"]["code"] == "RATTISH_TOKEN_LIMIT_UNCHECKABLE"
    assert capped.document["usage"]["total_tokens"] is None
    # Cost is still known, so a cost limit alone keeps working.
    assert cost_only.document["status"] == "passed", cost_only.document.get("error")
    assert cost_only.document["usage"]["cost_usd"] == 0.0


# A real local server, when one is configured ----------------------------------------

LIVE_URL = os.environ.get("RATICODE_LIVE_LOCAL_MODEL_URL")
LIVE_MODEL = os.environ.get("RATICODE_LIVE_LOCAL_MODEL")


@pytest.mark.skipif(
    not (LIVE_URL and LIVE_MODEL),
    reason="set RATICODE_LIVE_LOCAL_MODEL_URL and RATICODE_LIVE_LOCAL_MODEL to test a real server",
)
@pytest.mark.anyio
async def test_live_local_model_server(tmp_path: Path) -> None:
    assert LIVE_URL and LIVE_MODEL
    data_dir = tmp_path / "data"
    _save_local(data_dir, LIVE_URL)
    check = check_local_server("local", model=LIVE_MODEL, data_dir=data_dir)
    assert check.reachable, check.error
    assert check.model_available, f"{LIVE_MODEL} not in {check.models}"
    # The ID exactly as the server lists it, unquoted, the way a user writes it.
    source = _workflow(tmp_path, LIVE_MODEL, "  max-cost-usd: 0.01\n  max-tokens: 20000\n")
    preflight = run_preflight(compile_rattish_file(source, data_dir=data_dir).ir, data_dir=data_dir)
    assert preflight.ready, [d.message for d in preflight.diagnostics]
    result = await run_rattish_file(source, data_dir=data_dir)
    document = result.document
    print("LIVE usage:", json.dumps(document.get("usage")), "error:", document.get("error"))
    assert document["status"] == "passed", document.get("error")
    assert document["usage"]["cost_usd"] == 0.0
    assert document["usage"]["total_tokens"], "the server reported no token counts"
