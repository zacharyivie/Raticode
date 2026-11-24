"""Retired and retiring cloud models are caught before a run, in preflight and Triggers."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core import model_lifecycle
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.model_lifecycle import (
    PINNED_MODELS,
    check_model,
    list_models,
    model_listed,
    pinned_problem,
    replacement_for,
    retired_models,
)
from gofer.core.provider_profiles import (
    ProviderProfile,
    ResolvedProviderSettings,
    save_provider_profiles,
)
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"
TODAY = date(2026, 11, 17)


def _contracts() -> dict[str, Any]:
    return load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )


def _settings(subscription: str, model: str, **fields: Any) -> ResolvedProviderSettings:
    base = {
        "anthropic_api": "https://api.anthropic.com/v1",
        "openai_api": "https://api.openai.com/v1",
    }[subscription]
    key_env = {"anthropic_api": "ANTHROPIC_API_KEY", "openai_api": "OPENAI_API_KEY"}[subscription]
    return ResolvedProviderSettings(
        subscription=subscription,  # type: ignore[arg-type]
        model=model,
        api_base_url=fields.pop("api_base_url", base),
        api_key_env=key_env,
        **fields,
    )


class FakeApi:
    """Answers GET /models like the provider, and records each request."""

    def __init__(self, pages: list[list[str]] | None = None, status: int = 200) -> None:
        self.pages = pages or [[]]
        self.status = status
        self.requests: list[HttpRequest] = []
        self.offline = False

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.offline:
            raise OSError("Network is unreachable")
        index = min(len(self.requests) - 1, len(self.pages) - 1)
        page = self.pages[index]
        more = index < len(self.pages) - 1
        body = {
            "data": [{"id": item, "type": "model"} for item in page],
            "has_more": more,
            "last_id": page[-1] if page else None,
        }
        return HttpResponse(self.status, {}, json.dumps(body).encode())


@pytest.fixture
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> date:
    monkeypatch.setattr(model_lifecycle, "today_utc", lambda: TODAY)
    return TODAY


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(model_lifecycle, "_send", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return fake


# The pinned list --------------------------------------------------------------------


def test_no_provider_contract_defaults_to_a_retired_model() -> None:
    """Fails once a provider retires a contract's default: pick its replacement."""
    contracts = _contracts()
    checked = 0
    for contract in contracts.values():
        retired = retired_models(contract.runtime_subscription)
        assert contract.default_model not in retired, (
            f"{contract.provider_id} defaults to retired model {contract.default_model!r}; "
            f"use {replacement_for(contract.runtime_subscription, contract.default_model)!r}"
        )
        checked += contract.runtime_subscription in PINNED_MODELS
    assert checked == 2


def test_anthropic_api_defaults_to_a_current_model_in_both_places() -> None:
    contracts = _contracts()
    assert contracts["anthropic-api"].default_model == "claude-sonnet-5-5"
    assert AnthropicApiSubscription.default_model == contracts["anthropic-api"].default_model
    assert OpenAiApiSubscription.default_model == contracts["openai-api"].default_model
    assert "claude-3-5-sonnet-latest" in retired_models("anthropic_api", today=TODAY)


def test_a_retired_model_fails_and_names_its_replacement(fixed_today: date) -> None:
    problem = pinned_problem(_settings("anthropic_api", "claude-3-5-sonnet-latest"))
    assert problem is not None
    assert problem.severity == "error"
    assert problem.code == "RATTISH_PREFLIGHT_MODEL_RETIRED"
    assert problem.replacement == "claude-sonnet-5-5"
    assert "retired on October 28, 2025" in problem.message
    assert "Use 'claude-sonnet-5-5' instead." in problem.message
    assert problem.suggestion.startswith("Set model: claude-sonnet-5-5")


def test_a_deprecated_model_warns_with_its_retirement_date(fixed_today: date) -> None:
    problem = pinned_problem(_settings("anthropic_api", "claude-sonnet-4-5-20250929"))
    assert problem is not None
    assert problem.severity == "warning"
    assert problem.code == "RATTISH_PREFLIGHT_MODEL_DEPRECATED"
    assert problem.retires == date(2026, 11, 30)
    assert "retires on November 30, 2026" in problem.message
    # The same model fails once the date has passed.
    after = pinned_problem(
        _settings("anthropic_api", "claude-sonnet-4-5-20250929"), today=date(2026, 11, 30)
    )
    assert after is not None and after.severity == "error"


def test_replacements_follow_the_chain_to_a_current_model() -> None:
    assert replacement_for("openai_api", "chatgpt-4o-latest") == "gpt-5.6-sol"
    assert replacement_for("openai_api", "o1-mini") == "gpt-5.6-terra"
    assert replacement_for("anthropic_api", "claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_other_hosts_and_current_models_are_left_alone(fixed_today: date) -> None:
    proxy = _settings("openai_api", "gpt-4-turbo", api_base_url="https://openrouter.example/api/v1")
    assert pinned_problem(proxy) is None
    assert pinned_problem(_settings("anthropic_api", "claude-sonnet-5-5")) is None


# The provider's model list ----------------------------------------------------------


def test_anthropic_aliases_match_their_dated_snapshots() -> None:
    listed = ["claude-haiku-4-5-20251001", "claude-opus-4-6"]
    assert model_listed("anthropic_api", "claude-haiku-4-5", listed)
    assert model_listed("anthropic_api", "claude-opus-4-6", listed)
    assert not model_listed("anthropic_api", "claude-opus-4", listed)
    assert not model_listed("openai_api", "claude-haiku-4-5", listed)


def test_the_list_is_fetched_once_a_day_across_pages(
    api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.pages = [["claude-opus-5-5"], ["claude-sonnet-5-5"]]
    settings = _settings("anthropic_api", "claude-sonnet-5-5")
    assert list_models(settings, tmp_path) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert len(api.requests) == 2
    first, second = api.requests
    assert first.url == "https://api.anthropic.com/v1/models?limit=1000"
    assert second.url.endswith("&after_id=claude-opus-5-5")
    assert first.headers["x-api-key"] == "sk-ant-test"
    # A second preflight, and a new backend process, use the saved list.
    assert list_models(settings, tmp_path) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    model_lifecycle.clear_cache()
    assert list_models(settings, tmp_path) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert len(api.requests) == 2
    saved = (tmp_path / "provider-model-lists.json").read_text(encoding="utf-8")
    assert "sk-ant-test" not in saved
    # A day later it asks again.
    real_now = model_lifecycle._now
    monkeypatch.setattr(model_lifecycle, "_now", lambda: real_now() + 86_401)
    model_lifecycle.clear_cache()
    api.pages = [["claude-sonnet-5-5"]]
    api.requests.clear()
    assert list_models(settings, tmp_path) == ["claude-sonnet-5-5"]
    assert len(api.requests) == 1


def test_offline_skips_the_list_and_retries_later(
    api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed_today: date
) -> None:
    api.offline = True
    settings = _settings("openai_api", "gpt-made-up")
    assert list_models(settings, tmp_path) is None
    assert check_model(settings, tmp_path) is None
    assert len(api.requests) == 1  # remembered: Studio doesn't wait on every edit
    real_now = model_lifecycle._now
    monkeypatch.setattr(model_lifecycle, "_now", lambda: real_now() + 301)
    api.offline = False
    api.pages = [["gpt-5.6-sol"]]
    problem = check_model(settings, tmp_path)
    assert problem is not None and problem.code == "RATTISH_PREFLIGHT_MODEL_UNAVAILABLE"
    assert api.requests[-1].headers["Authorization"] == "Bearer sk-test"


def test_errors_and_missing_keys_skip_the_list(
    api: FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.status = 401
    assert list_models(_settings("openai_api", "gpt-5.6-sol"), tmp_path) is None
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    api.requests.clear()
    assert list_models(_settings("anthropic_api", "claude-sonnet-5-5"), tmp_path) is None
    assert api.requests == []


def test_an_unlisted_model_fails_with_the_closest_listed_one(
    api: FakeApi, tmp_path: Path, fixed_today: date
) -> None:
    api.pages = [["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]]
    problem = check_model(_settings("anthropic_api", "claude-sonet-5-5"), tmp_path)
    assert problem is not None
    assert problem.severity == "error"
    assert problem.state == "unlisted"
    assert problem.replacement == "claude-sonnet-5-5"
    assert "doesn't list model 'claude-sonet-5-5'" in problem.message
    assert check_model(_settings("anthropic_api", "claude-sonnet-5-5"), tmp_path) is None


def test_a_deprecated_model_the_list_still_has_only_warns(
    api: FakeApi, tmp_path: Path, fixed_today: date
) -> None:
    api.pages = [["gpt-5-mini", "gpt-6.1-terra"]]
    problem = check_model(_settings("openai_api", "gpt-5-mini"), tmp_path)
    assert problem is not None
    assert problem.severity == "warning"
    assert "retires on December 11, 2026" in problem.message
    assert problem.replacement == "gpt-6.1-terra"


# Preflight and the Triggers section -------------------------------------------------


def _workflow(tmp_path: Path, model: str, fallback: str = "") -> Path:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Cloud model\n"
        "\nNode summarize:\n  type: agent\n  provider: anthropic-api\n"
        f"  model: {model}\n  prompt: Reply with the word ready.\n{fallback}",
        encoding="utf-8",
    )
    return source


def _preflight(source: Path, data_dir: Path) -> Any:
    return run_preflight(compile_rattish_file(source, data_dir=data_dir).ir, data_dir=data_dir)


def test_preflight_fails_a_retired_model_and_warns_about_a_retiring_one(
    api: FakeApi, tmp_path: Path, fixed_today: date
) -> None:
    data_dir = tmp_path / "data"
    api.pages = [["claude-sonnet-4-5-20250929", "claude-sonnet-5-5"]]

    retired = _preflight(_workflow(tmp_path, "claude-3-5-sonnet-latest"), data_dir)
    assert not retired.ready
    (error,) = [item for item in retired.diagnostics if item.severity == "error"]
    assert error.code == "RATTISH_PREFLIGHT_MODEL_RETIRED"
    assert "claude-sonnet-5-5" in error.message
    assert error.details["replacement"] == "claude-sonnet-5-5"
    assert error.suggestions[0].startswith("Set model: claude-sonnet-5-5")

    retiring = _preflight(_workflow(tmp_path, "claude-sonnet-4-5"), data_dir)
    assert retiring.ready, [item.message for item in retiring.diagnostics]
    (warning,) = [item for item in retiring.diagnostics if item.severity == "warning"]
    assert warning.code == "RATTISH_PREFLIGHT_MODEL_DEPRECATED"
    assert "November 30, 2026" in warning.message
    assert warning.details["retires"] == "2026-11-30"

    current = _preflight(_workflow(tmp_path, "claude-sonnet-5-5"), data_dir)
    assert current.ready
    assert not [item for item in current.diagnostics if "MODEL" in item.code]


def test_a_fallback_entrys_error_wins_over_a_primary_warning(
    api: FakeApi, tmp_path: Path, fixed_today: date
) -> None:
    data_dir = tmp_path / "data"
    api.pages = [["claude-sonnet-4-5-20250929", "claude-sonnet-5-5"]]
    source = _workflow(
        tmp_path,
        "claude-sonnet-4-5",
        "  fallback:\n    -\n      provider: anthropic-api\n"
        "      model: claude-3-7-sonnet-latest\n",
    )
    result = _preflight(source, data_dir)
    (error,) = [item for item in result.diagnostics if item.severity == "error"]
    assert error.code == "RATTISH_PREFLIGHT_MODEL_RETIRED"
    assert error.message.startswith("Fallback entry 1 (anthropic-api):")


def test_preflight_skips_the_list_offline_and_with_a_proxy_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed_today: date
) -> None:
    # conftest makes every request fail, like a computer with no network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data_dir = tmp_path / "data"
    assert _preflight(_workflow(tmp_path, "claude-sonnet-5-5"), data_dir).ready
    save_provider_profiles(
        {
            "proxy": ProviderProfile(
                name="proxy",
                subscription="anthropic_api",
                api_base_url="https://llm-proxy.example/v1",
            )
        },
        data_dir,
    )
    source = _workflow(tmp_path, "claude-3-5-sonnet-latest")
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "provider: anthropic-api\n", "provider: anthropic-api\n  profile: proxy\n"
        ),
        encoding="utf-8",
    )
    assert _preflight(source, data_dir).ready


def test_the_triggers_section_and_cli_show_a_retiring_model(
    tmp_path: Path, fixed_today: date
) -> None:
    from gofer.rattish.workspaces import discover_registered_workflows
    from gofer.ui.rattish_triggers import triggers_payload

    data = tmp_path / "data"
    data.mkdir()
    source = _workflow(tmp_path, "claude-sonnet-4-5-20250929")
    (registered,) = discover_registered_workflows(source.parent, registry_dir=data)
    (notice,) = triggers_payload(data, registered.workflow_id)["models"]["notices"]
    assert notice["node"] == "summarize"
    assert notice["provider"] == "anthropic-api"
    assert notice["state"] == "deprecated"
    assert notice["retires"] == "2026-11-30"
    assert notice["replacement"] == "claude-sonnet-5-5"
    assert "retires on November 30, 2026" in notice["message"]

    result = CliRunner().invoke(
        app,
        [
            "rattish", "triggers", "add", "cron", str(source), "--cron", "0 9 * * *",
            "--timezone", "UTC", "--data-dir", str(data),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Step 'summarize' on anthropic-api: Model 'claude-sonnet-4-5-20250929' is" in (
        " ".join(result.output.split())
    )
