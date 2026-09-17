from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

from gofer.core.cursor_models import cursor_model_id
from gofer.utils.paths import get_data_dir

ProfileSubscription = Literal[
    "claude_code",
    "codex",
    "openai_api",
    "anthropic_api",
    "cursor",
    "copilot",
    "opencode",
    "antigravity",
    "grok",
]
DIRECT_API_SUBSCRIPTIONS = {"openai_api", "anthropic_api"}
DEFAULT_DIRECT_API_BASE_URLS = {
    "openai_api": "https://api.openai.com/v1",
    "anthropic_api": "https://api.anthropic.com/v1",
}
DEFAULT_DIRECT_API_KEY_ENVS = {
    "openai_api": "OPENAI_API_KEY",
    "anthropic_api": "ANTHROPIC_API_KEY",
}
ApprovalMode = Literal[
    "cli-managed",
    "default",
    "auto",
    "manual",
    "never",
    "on-request",
    "on-failure",
    "acceptEdits",
    "dontAsk",
    "plan",
    "bypassPermissions",
]
SandboxMode = Literal["default", "read-only", "workspace-write", "danger-full-access"]

PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
MASKED_SECRET_VALUE = "********"
SENSITIVE_ENV_NAME_PATTERN = re.compile(
    r"(^|_)(API_?KEY|AUTHORIZATION|AUTH|BEARER|CREDENTIALS?|KEY|PASSWORD|PASS|SECRET|TOKEN)(_|$)",
    re.IGNORECASE,
)


class ProviderProfile(BaseModel):
    name: str
    subscription: ProfileSubscription
    model: str | None = None
    timeout: float | None = None
    effort: str | None = Field(
        default=None,
        validation_alias=AliasChoices("effort", "reasoning"),
    )
    approval_mode: ApprovalMode | None = None
    sandbox_mode: SandboxMode | None = None
    extra_args: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    api_base_url: str | None = None
    api_key_env: str | None = None
    api_key_secret: str | None = None
    organization: str | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        validate_provider_profile_name(value)
        return value

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("Profile timeout must be greater than 0")
        return value


class ResolvedProviderSettings(BaseModel):
    profile_name: str | None = None
    subscription: ProfileSubscription
    model: str | None = None
    timeout: float | None = None
    effort: str | None = Field(
        default=None,
        validation_alias=AliasChoices("effort", "reasoning"),
    )
    approval_mode: ApprovalMode | None = None
    sandbox_mode: SandboxMode | None = None
    extra_args: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    api_base_url: str | None = None
    api_key_env: str | None = None
    api_key_secret: str | None = None
    organization: str | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)


def profile_store_path(data_dir: Path | None = None) -> Path:
    return (data_dir or get_data_dir()) / "provider-profiles.json"


def load_provider_profiles(data_dir: Path | None = None) -> dict[str, ProviderProfile]:
    path = profile_store_path(data_dir)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_profiles = data.get("profiles", data)
    if not isinstance(raw_profiles, dict):
        raise ValueError("Provider profile store must contain an object")
    return {
        str(name): ProviderProfile(
            name=str(name),
            **{k: v for k, v in value.items() if k != "name"},
        )
        for name, value in raw_profiles.items()
        if isinstance(value, dict)
    }


def save_provider_profiles(
    profiles: dict[str, ProviderProfile],
    data_dir: Path | None = None,
) -> None:
    path = profile_store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profiles": {
            name: profile.model_dump(exclude_none=True)
            for name, profile in sorted(profiles.items())
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_sensitive_env_name(name: str) -> bool:
    normalized = name.strip().replace("-", "_")
    return bool(SENSITIVE_ENV_NAME_PATTERN.search(normalized))


def validate_provider_profile_name(name: str) -> None:
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError("Profile name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def sensitive_plaintext_env(profile: ProviderProfile) -> dict[str, str]:
    return {
        name: value
        for name, value in profile.env.items()
        if is_sensitive_env_name(name) and name not in profile.secret_refs
    }


def validate_safe_provider_env(
    env: dict[str, str],
    *,
    unsafe_env: dict[str, str] | None = None,
) -> None:
    unsafe_names = set((unsafe_env or {}).keys())
    sensitive_names = sorted(
        name for name in env if is_sensitive_env_name(name) and name not in unsafe_names
    )
    if sensitive_names:
        joined = ", ".join(sensitive_names)
        raise ValueError(
            f"Sensitive provider env value(s) must use secret_refs: {joined}. "
            "Use --secret-ref KEY=SECRET_NAME, or --unsafe-env KEY=VALUE for an "
            "explicit local-only plaintext opt-in."
        )


def provider_profile_ui_payload(profile: ProviderProfile) -> dict[str, Any]:
    payload = profile.model_dump(mode="json", exclude_none=True)
    if profile.env:
        payload["env"] = {
            name: MASKED_SECRET_VALUE if is_sensitive_env_name(name) else value
            for name, value in profile.env.items()
        }
    legacy_sensitive = sorted(sensitive_plaintext_env(profile))
    if legacy_sensitive:
        payload["masked_env"] = legacy_sensitive
    return payload


def provider_profile_from_ui_payload(
    payload: dict[str, Any],
    existing: ProviderProfile | None = None,
) -> ProviderProfile:
    raw_payload = dict(payload)
    unsafe_env = _string_dict(raw_payload.pop("unsafe_env", {}), "unsafe_env")
    raw_env = _string_dict(raw_payload.get("env", {}), "env")
    secret_refs = _string_dict(raw_payload.get("secret_refs", {}), "secret_refs")
    preserved_env: dict[str, str] = {}
    if existing is not None:
        for name, value in raw_env.items():
            if (
                is_sensitive_env_name(name)
                and value == MASKED_SECRET_VALUE
                and name in existing.env
                and name not in secret_refs
            ):
                preserved_env[name] = existing.env[name]
    env = {
        name: value
        for name, value in raw_env.items()
        if not (
            is_sensitive_env_name(name) and value == MASKED_SECRET_VALUE and name in secret_refs
        )
    }
    env.update(preserved_env)
    env.update(unsafe_env)
    validate_safe_provider_env(
        env,
        unsafe_env={**preserved_env, **unsafe_env},
    )
    raw_payload["env"] = env
    raw_payload["secret_refs"] = secret_refs
    profile = ProviderProfile(**raw_payload)
    validate_provider_profile(profile)
    return profile


def _string_dict(value: Any, field_name: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def resolve_provider_settings(
    *,
    agent_subscription: ProfileSubscription,
    profile_name: str | None,
    agent_model: str | None = None,
    agent_effort: str | None = None,
    operation_model: str | None = None,
    operation_effort: str | None = None,
    operation_profile: str | None = None,
    operation_timeout: float | None = None,
    data_dir: Path | None = None,
) -> ResolvedProviderSettings:
    selected_profile_name = operation_profile or profile_name
    profiles = load_provider_profiles(data_dir)
    profile = profiles.get(selected_profile_name) if selected_profile_name else None
    if selected_profile_name and profile is None:
        raise ValueError(f"Provider profile '{selected_profile_name}' was not found")
    subscription = profile.subscription if profile else agent_subscription
    if profile and profile.subscription != agent_subscription:
        raise ValueError(
            f"Provider profile '{profile.name}' uses subscription '{profile.subscription}', "
            f"but agent is configured for '{agent_subscription}'"
        )
    return ResolvedProviderSettings(
        profile_name=profile.name if profile else selected_profile_name,
        subscription=subscription,
        model=operation_model or agent_model or (profile.model if profile else None),
        timeout=(
            operation_timeout
            if operation_timeout is not None
            else (profile.timeout if profile else None)
        ),
        effort=operation_effort or agent_effort or (profile.effort if profile else None),
        approval_mode=profile.approval_mode if profile else None,
        sandbox_mode=profile.sandbox_mode if profile else None,
        extra_args=list(profile.extra_args) if profile else [],
        tools=list(profile.tools) if profile else [],
        mcp_servers=list(profile.mcp_servers) if profile else [],
        env=dict(profile.env) if profile else {},
        secret_refs=dict(profile.secret_refs) if profile else {},
        api_base_url=(
            profile.api_base_url
            if profile and profile.api_base_url
            else DEFAULT_DIRECT_API_BASE_URLS.get(subscription)
        ),
        api_key_env=_resolved_direct_api_key_env(profile, subscription),
        api_key_secret=profile.api_key_secret if profile else None,
        organization=profile.organization if profile else None,
        provider_options=dict(profile.provider_options) if profile else {},
    )


def validate_provider_settings(settings: ResolvedProviderSettings) -> None:
    if settings.subscription == "antigravity" and settings.effort not in (
        None,
        "cli-default",
        "low",
        "medium",
        "high",
    ):
        raise ValueError(f"Unsupported Antigravity effort {settings.effort!r}")
    if settings.subscription == "cursor":
        cursor_model_id(settings.model, settings.effort)
    if settings.subscription in {"cursor", "copilot", "opencode", "antigravity", "grok"}:
        unsupported = [
            name
            for name in ("effort", "extra_args", "tools", "mcp_servers", "provider_options")
            if getattr(settings, name)
            and getattr(settings, name) != "cli-default"
            and not (
                settings.subscription in {"cursor", "copilot", "antigravity", "grok"}
                and name == "effort"
            )
        ]
        unsupported += [
            name
            for name in ("approval_mode", "sandbox_mode")
            if getattr(settings, name)
            not in (
                (None, "default", "cli-managed")
                if name == "approval_mode"
                and settings.subscription in {"antigravity", "grok"}
                else (None, "default")
            )
        ]
        if unsupported:
            raise ValueError(
                f"{settings.subscription} profiles do not support {', '.join(unsupported)}"
            )
        return
    if settings.subscription == "codex":
        if settings.tools:
            raise ValueError(
                "Codex provider profiles do not support default tools; "
                "configure tools on a Claude Code profile or on the agent"
            )
        if settings.mcp_servers:
            raise ValueError(
                "Codex provider profiles do not support MCP server flags; "
                "configure MCP servers on a Claude Code profile or on the agent"
            )
        if settings.approval_mode not in (None, "default"):
            raise ValueError(
                "Codex provider profiles do not support approval_mode; "
                "remove approval_mode from the profile"
            )
        return
    if settings.subscription == "claude_code":
        if settings.sandbox_mode not in (None, "default"):
            raise ValueError(
                "Claude Code provider profiles do not support sandbox_mode; "
                "remove sandbox_mode from the profile or use a Codex profile"
            )
        return
    if settings.subscription in DIRECT_API_SUBSCRIPTIONS:
        unsupported = []
        if settings.tools:
            unsupported.append("tools")
        if settings.mcp_servers:
            unsupported.append("mcp_servers")
        if settings.extra_args:
            unsupported.append("extra_args")
        if settings.approval_mode not in (None, "default"):
            unsupported.append("approval_mode")
        if settings.sandbox_mode not in (None, "default"):
            unsupported.append("sandbox_mode")
        if unsupported:
            raise ValueError(
                f"Direct API provider profiles do not support {', '.join(unsupported)}"
            )
        if settings.api_key_secret and settings.api_key_env:
            raise ValueError(
                "Direct API provider profiles must use api_key_secret or api_key_env, not both"
            )


def validate_provider_profile(profile: ProviderProfile) -> None:
    validate_provider_settings(
        ResolvedProviderSettings(
            profile_name=profile.name,
            subscription=profile.subscription,
            model=profile.model,
            timeout=profile.timeout,
            effort=profile.effort,
            approval_mode=profile.approval_mode,
            sandbox_mode=profile.sandbox_mode,
            extra_args=list(profile.extra_args),
            tools=list(profile.tools),
            mcp_servers=list(profile.mcp_servers),
            env=dict(profile.env),
            secret_refs=dict(profile.secret_refs),
            api_base_url=profile.api_base_url,
            api_key_env=profile.api_key_env,
            api_key_secret=profile.api_key_secret,
            organization=profile.organization,
            provider_options=dict(profile.provider_options),
        )
    )


def unresolved_provider_secret_refs(settings: ResolvedProviderSettings) -> list[str]:
    missing = {
        secret_name
        for secret_name in settings.secret_refs.values()
        if not _secret_value(secret_name)
    }
    if settings.subscription in DIRECT_API_SUBSCRIPTIONS:
        if settings.api_key_secret:
            if not _secret_value(settings.api_key_secret):
                missing.add(settings.api_key_secret)
        elif settings.api_key_env and not os.environ.get(settings.api_key_env):
            missing.add(settings.api_key_env)
    return sorted(missing)


def resolved_provider_env(settings: ResolvedProviderSettings) -> dict[str, str]:
    env = dict(settings.env)
    for env_name, secret_name in settings.secret_refs.items():
        secret = _secret_value(secret_name)
        if secret is None:
            profile = f" '{settings.profile_name}'" if settings.profile_name else ""
            raise ValueError(
                f"Provider profile{profile} requires secret '{secret_name}' for "
                f"environment variable '{env_name}'. Set GOFER_SECRET_{secret_name} "
                f"or {secret_name} before running."
            )
        env[env_name] = secret
    if settings.subscription in DIRECT_API_SUBSCRIPTIONS:
        if settings.api_key_secret:
            secret = _secret_value(settings.api_key_secret)
            if secret is None:
                raise ValueError(
                    f"Provider profile '{settings.profile_name}' requires API key "
                    f"secret '{settings.api_key_secret}'. Set "
                    f"GOFER_SECRET_{settings.api_key_secret} or {settings.api_key_secret}."
                )
            env["GOFER_DIRECT_API_KEY"] = secret
        elif settings.api_key_env:
            secret = os.environ.get(settings.api_key_env)
            if secret is None:
                raise ValueError(
                    f"Direct API provider '{settings.subscription}' requires "
                    f"environment variable '{settings.api_key_env}'."
                )
            env["GOFER_DIRECT_API_KEY"] = secret
    return env


def _secret_value(name: str) -> str | None:
    for env_name in (f"GOFER_SECRET_{name}", name):
        if env_name in os.environ:
            return os.environ[env_name]
    return None


def _resolved_direct_api_key_env(
    profile: ProviderProfile | None,
    subscription: ProfileSubscription,
) -> str | None:
    if profile and profile.api_key_secret:
        return profile.api_key_env
    if profile and profile.api_key_env:
        return profile.api_key_env
    return DEFAULT_DIRECT_API_KEY_ENVS.get(subscription)
