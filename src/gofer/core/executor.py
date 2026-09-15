from __future__ import annotations

import base64
import copy
import csv
import hashlib
import hmac
import importlib.metadata
import json
import os
import re
import secrets as stdlib_secrets
import shlex
import shutil
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import anyio

from gofer.core.agent import (
    Agent,
    AgentConfig,
    AgentResult,
    _accepts_execute_kwarg,
    configured_extra_paths,
    format_agent_memory,
)
from gofer.core.approvals import (
    ApprovalDecisionValue,
    ApprovalRequest,
    ApprovalStore,
    MultiChannelNotificationAdapter,
    Notification,
    NotificationAdapter,
    wait_for_decision,
)
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode, WorkflowGraph
from gofer.core.http import HttpClient, HttpRequest, UrllibHttpClient, append_query_params
from gofer.core.llm_prompts import common_llm_task_prompt
from gofer.core.network_policy import NetworkPolicyViolation, validate_http_request_url
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    BreakOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    FailOperation,
    FanSource,
    FileOperation,
    FolderOperation,
    HttpRequestOperation,
    HttpRetryPolicy,
    InfiniteFanSource,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    OperationType,
    PassOperation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    StartOperation,
    SubflowOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WorkflowCallOperation,
    WriteFileOperation,
)
from gofer.core.provider_profiles import (
    ResolvedProviderSettings,
    resolve_provider_settings,
    resolved_provider_env,
    validate_provider_settings,
)
from gofer.core.references import (
    ReferenceNamespace,
    parse_exact_reference,
    require_operation_reference_capability,
    require_reference_capability,
)
from gofer.core.resources import (
    DEFAULT_RESOURCE_LIMITS,
    ResourceLimitError,
    ResourceLimits,
    byte_len,
    read_text_limited,
    require_limit,
    truncate_text_bytes,
)
from gofer.core.secrets import missing_workflow_secrets
from gofer.core.structured_output import (
    StructuredOutputError,
    parse_and_validate_output,
    resolve_output_schema,
    structured_output_instruction,
    structured_workflow_errors,
)
from gofer.core.thoughts import summarize_thought
from gofer.core.usage import (
    summarize_node_outputs,
    usage_from_metadata,
)
from gofer.core.workflow import (
    AgenticWorkflow,
    FilesystemAccessEntry,
    WorkflowConfig,
    coerce_workflow_variable,
    masked_workflow_parameters,
    masked_workflow_variables,
    resolve_workflow_parameters,
)
from gofer.prompts.manager import PromptManager
from gofer.subscriptions.base import Subscription
from gofer.utils.logging import get_logger
from gofer.utils.paths import get_data_dir
from gofer.utils.process import run_subprocess
from gofer.utils.run_state import clear_workflow_stop, workflow_run_stop_path

log = get_logger(__name__)
AGENT_MEMORY_COMPACT_CHAR_LIMIT = 32_000
AGENT_MEMORY_RECENT_TURNS = 8
SECRET_REF_PATTERN = re.compile(
    r"^\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$"
    r"|^secret:([A-Za-z_][A-Za-z0-9_]*)$"
)
SECRET_INTERPOLATION_PATTERN = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
TEMPLATE_INTERPOLATION_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")
SENSITIVE_FIELD_NAMES = {
    "access-key",
    "access_key",
    "access_token",
    "apikey",
    "auth",
    "auth_token",
    "authorization",
    "api_key",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "api-key",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
    "x-api-key",
    "x_api_key",
}


def command_shell_args(command: str) -> list[str]:
    if sys.platform == "win32":
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return ["bash", "-c", command]


def open_resource_args(
    target: str,
    resource_type: str = "auto",
    args: list[str] | None = None,
) -> list[str]:
    if resource_type == "app":
        return [target, *(args or [])]
    if sys.platform == "win32":
        return ["cmd", "/c", "start", "", target]
    if sys.platform == "darwin":
        return ["open", target]
    return ["xdg-open", target]


def _secret_name(value: str) -> str | None:
    match = SECRET_REF_PATTERN.match(value.strip())
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _read_secret(name: str) -> str:
    env_names = [f"GOFER_SECRET_{name}", name]
    for env_name in env_names:
        if env_name in os.environ:
            return os.environ[env_name]
    raise ValueError(f"Missing secret: {name}")


def _resolve_exact_input_path(obj: object, parts: list[str], reference: str) -> object:
    for part in parts:
        if isinstance(obj, list):
            obj = obj[int(part)]
            continue
        if not isinstance(obj, dict) or part not in obj:
            raise ValueError(f"Cannot resolve exact input reference: {reference!r}")
        obj = obj[part]
    return obj


def _replace_secret_tokens(value: str) -> str:
    return SECRET_INTERPOLATION_PATTERN.sub(
        lambda match: _read_secret(match.group(1)),
        value,
    )


def _is_sensitive_field(path: str, configured: set[str]) -> bool:
    normalized = path.lower()
    if normalized in configured:
        return True
    name = normalized.rsplit(".", maxsplit=1)[-1]
    if name in configured:
        return True
    return name in SENSITIVE_FIELD_NAMES or any(
        token in name for token in ("token", "secret", "credential", "password")
    )


def _secret_reference_names(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    match = SECRET_REF_PATTERN.match(value.strip())
    names = {match.group(1) or match.group(2)} if match is not None else set()
    names.update(match.group(1) for match in SECRET_INTERPOLATION_PATTERN.finditer(value))
    return names


def _collect_secret_values(value: object) -> set[str]:
    values: set[str] = set()
    if isinstance(value, str):
        for name in _secret_reference_names(value):
            values.add(_read_secret(name))
    elif isinstance(value, dict):
        for item in value.values():
            values.update(_collect_secret_values(item))
    elif isinstance(value, list):
        for item in value:
            values.update(_collect_secret_values(item))
    return {value for value in values if value}


def _collect_leaf_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, dict):
        values: set[str] = set()
        for item in value.values():
            values.update(_collect_leaf_strings(item))
        return values
    if isinstance(value, list):
        list_values: set[str] = set()
        for item in value:
            list_values.update(_collect_leaf_strings(item))
        return list_values
    if value is None:
        return set()
    text = str(value)
    return {text} if text else set()


def _resolve_template_reference(
    template_context: dict[str, object],
    reference: str,
) -> object | None:
    value: object = template_context
    for part in reference.strip().split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _collect_sensitive_template_values(
    value: object,
    configured: set[str],
    template_context: dict[str, object],
    path: str = "",
    active: bool = False,
) -> set[str]:
    path_is_sensitive = active or (bool(path) and _is_sensitive_field(path, configured))
    if isinstance(value, str):
        if not path_is_sensitive:
            return set()
        values: set[str] = set()
        for match in TEMPLATE_INTERPOLATION_PATTERN.finditer(value):
            resolved = _resolve_template_reference(template_context, match.group(1))
            values.update(_collect_leaf_strings(resolved))
        return values
    if isinstance(value, dict):
        dict_values: set[str] = set()
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            dict_values.update(
                _collect_sensitive_template_values(
                    item,
                    configured,
                    template_context,
                    child_path,
                    path_is_sensitive,
                )
            )
        return dict_values
    if isinstance(value, list):
        list_values: set[str] = set()
        for item in value:
            list_values.update(
                _collect_sensitive_template_values(
                    item,
                    configured,
                    template_context,
                    path,
                    path_is_sensitive,
                )
            )
        return list_values
    return set()


def _collect_configured_secret_values(
    value: object,
    configured: set[str],
    path: str = "",
) -> set[str]:
    if isinstance(value, dict):
        values: set[str] = set()
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _is_sensitive_field(child_path, configured):
                values.update(_collect_leaf_strings(item))
            else:
                values.update(_collect_configured_secret_values(item, configured, child_path))
        return values
    if isinstance(value, list):
        list_values: set[str] = set()
        for item in value:
            list_values.update(_collect_configured_secret_values(item, configured, path))
        return list_values
    if path and _is_sensitive_field(path, configured):
        return _collect_leaf_strings(value)
    return set()


def _collect_configured_secret_text_values(
    value: str,
    configured: set[str],
) -> set[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        values: set[str] = set()

        def collect_key_value(match: re.Match[str]) -> str:
            key = match.group("key")
            if _is_sensitive_field(key, configured):
                item = match.group("value").strip("\"'")
                if item:
                    values.add(item)
            return match.group(0)

        re.sub(
            r"(?:^|[&\s;,])(?P<key>[A-Za-z0-9_.-]+)\s*=\s*"
            r"(?P<value>[^&\s;,]+)",
            collect_key_value,
            value,
        )
        re.sub(
            r"(?:^|[\s{,])['\"]?(?P<key>[A-Za-z0-9_.-]+)['\"]?\s*:\s*"
            r"(?P<quote>['\"]?)(?P<value>[^,'\"}\s]+)(?P=quote)",
            collect_key_value,
            value,
        )
        return values
    return _collect_configured_secret_values(parsed, configured)


def _replace_known_secrets(value: str, secret_values: set[str]) -> str:
    masked = value
    for secret_value in sorted(secret_values, key=len, reverse=True):
        masked = masked.replace(secret_value, "***")
    return masked


def _mask_http_value(
    value: object,
    configured: set[str],
    path: str = "",
    secret_values: set[str] | None = None,
) -> object:
    if isinstance(value, dict):
        masked: dict[str, object] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            masked[str(key)] = (
                "***"
                if _is_sensitive_field(child_path, configured)
                else _mask_http_value(item, configured, child_path, secret_values)
            )
        return masked
    if isinstance(value, list):
        return [_mask_http_value(item, configured, path, secret_values) for item in value]
    if isinstance(value, str) and secret_values:
        return _replace_known_secrets(value, secret_values)
    return value


def _mask_http_text(
    value: str,
    configured: set[str],
    *,
    secret_values: set[str],
) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        masked = _replace_known_secrets(value, secret_values)
    else:
        return json.dumps(
            _mask_http_value(parsed, configured, secret_values=secret_values),
            default=str,
        )

    def mask_key_value(match: re.Match[str]) -> str:
        key = match.group("key")
        if not _is_sensitive_field(key, configured):
            return match.group(0)
        quote = match.groupdict().get("quote") or ""
        return f"{match.group('prefix')}{quote}***{quote}"

    masked = re.sub(
        r"(?P<prefix>(?:^|[&\s;,])(?P<key>[A-Za-z0-9_.-]+)\s*=\s*)"
        r"(?P<value>[^&\s;,]+)",
        mask_key_value,
        masked,
    )
    return re.sub(
        r"(?P<prefix>(?:^|[\s{,])['\"]?(?P<key>[A-Za-z0-9_.-]+)['\"]?\s*:\s*)"
        r"(?P<quote>['\"]?)(?P<value>[^,'\"}\s]+)(?P=quote)",
        mask_key_value,
        masked,
    )


def _masked_trigger_context_for_log(trigger_context: dict[str, Any]) -> dict[str, Any]:
    masked = cast(
        dict[str, Any],
        _mask_http_value(trigger_context, _trigger_sensitive_field_config(trigger_context)),
    )
    if isinstance(masked.get("events"), list):
        masked["events_json"] = json.dumps(masked["events"], default=str)
    if "sanitizedPayload" in masked:
        masked.pop("sanitizedPayload", None)
    return masked


def _trigger_sensitive_field_config(trigger_context: dict[str, Any]) -> set[str]:
    configured: set[str] = set()
    for field_name in trigger_context.get("sensitivePayloadFields", []):
        value = str(field_name).strip().lower()
        if not value:
            continue
        configured.add(value)
        if value.startswith("payload."):
            configured.add(value.removeprefix("payload."))
        else:
            configured.add(f"payload.{value}")
    return configured


def _collect_sensitive_field_values(
    value: object,
    configured: set[str],
    path: str = "",
) -> set[str]:
    if isinstance(value, dict):
        values: set[str] = set()
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _is_sensitive_field(child_path, configured):
                values.update(_collect_leaf_strings(item))
            else:
                values.update(_collect_sensitive_field_values(item, configured, child_path))
        return values
    if isinstance(value, list):
        list_values: set[str] = set()
        for item in value:
            list_values.update(_collect_sensitive_field_values(item, configured, path))
        return list_values
    return set()


def _trigger_secret_values_for_logs(trigger_context: dict[str, Any]) -> set[str]:
    configured = _trigger_sensitive_field_config(trigger_context)
    payload = trigger_context.get("payload")
    values = _collect_sensitive_field_values(payload, configured)
    return {value for value in values if value}


def _mask_known_secret_values(value: object, secret_values: set[str]) -> object:
    if not secret_values:
        return value
    if isinstance(value, str):
        return _replace_known_secrets(value, secret_values)
    if isinstance(value, dict):
        return {
            str(key): _mask_known_secret_values(item, secret_values) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_known_secret_values(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_known_secret_values(item, secret_values) for item in value)
    return value


def _mask_node_output(output: NodeOutput, secret_values: set[str]) -> NodeOutput:
    if not secret_values:
        return output
    return NodeOutput(
        node_id=output.node_id,
        success=output.success,
        output=str(_mask_known_secret_values(output.output, secret_values)),
        exit_code=output.exit_code,
        duration_seconds=output.duration_seconds,
        skipped=output.skipped,
        fan_outputs=cast(
            list[tuple[str, str]],
            _mask_known_secret_values(output.fan_outputs, secret_values),
        ),
        terminal_status=output.terminal_status,
        loop_items=cast(
            list[dict[str, object]] | None,
            _mask_known_secret_values(output.loop_items, secret_values),
        ),
        loop_infinite=output.loop_infinite,
        loop_max_concurrency=output.loop_max_concurrency,
        loop_fail_fast=output.loop_fail_fast,
        type=output.type,
        text=cast(str | None, _mask_known_secret_values(output.text, secret_values)),
        value=_mask_known_secret_values(output.value, secret_values),
        data=cast(dict[str, object], _mask_known_secret_values(output.data, secret_values)),
        items=cast(list[object], _mask_known_secret_values(output.items, secret_values)),
        error=cast(str | None, _mask_known_secret_values(output.error, secret_values)),
    )


def _mask_http_url(
    url: str,
    *,
    configured: set[str],
    secret_values: set[str],
    url_sensitive: bool,
    sensitive_query_keys: set[str],
) -> str:
    if url_sensitive:
        return "***"
    parsed = urllib.parse.urlsplit(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    masked_pairs = [
        (
            key,
            "***"
            if key.lower() in sensitive_query_keys or _is_sensitive_field(key, configured)
            else _replace_known_secrets(value, secret_values),
        )
        for key, value in query_pairs
    ]
    masked_url = urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(masked_pairs))
    )
    return _replace_known_secrets(masked_url, secret_values)


def _extract_dotted_path(data: object, path: str) -> object:
    current = data
    for part in path.strip("{}").split("."):
        if part in {"response", ""}:
            continue
        if isinstance(current, list):
            current = current[int(part)]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _remove_path(path: Path, recursive: bool = False) -> None:
    if path.is_dir():
        if not recursive:
            raise IsADirectoryError(f"{path} is a directory; enable recursive delete")
        shutil.rmtree(path)
        return
    path.unlink()


def _prepare_destination(path: Path, create_dirs: bool, overwrite: bool) -> None:
    if create_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists")


def _copy_path(source: Path, destination: Path, create_dirs: bool, overwrite: bool) -> None:
    _prepare_destination(destination, create_dirs, overwrite)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
        return
    shutil.copy2(source, destination)


def _move_path(source: Path, destination: Path, create_dirs: bool, overwrite: bool) -> None:
    _prepare_destination(destination, create_dirs, overwrite)
    if destination.exists():
        _remove_path(destination, recursive=True)
    shutil.move(str(source), str(destination))


def _trash_path(path: Path) -> Path:
    trash_root = get_data_dir() / "trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S%f%z")
    destination = trash_root / f"{timestamp}-{path.name}"
    shutil.move(str(path), str(destination))
    return destination


def _safe_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe or "item"


def _file_path_data(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "file_path": str(path),
        "file_name": path.name,
        "file_stem": path.stem,
        "file_extension": path.suffix,
        "parent_path": str(path.parent),
        "directory": str(path.parent),
    }


def _resolve_workflow_path(path: Path, path_base: Path | None) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute() or path_base is None:
        return expanded
    return path_base / expanded


def _resolved_for_access(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _folder_path_data(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "folder_path": str(path),
        "folder_name": path.name,
        "parent_path": str(path.parent),
        "directory": str(path),
    }


def _turns_size(turns: list[dict[str, str]]) -> int:
    return sum(len(str(turn.get("body", ""))) for turn in turns)


def _turns_transcript(turns: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"{turn.get('role', 'message').upper()}:\n{turn.get('body', '')}"
        for turn in turns
        if turn.get("body")
    )


def _agent_memory_compaction_prompt(turns: list[dict[str, str]]) -> str:
    transcript = _turns_transcript(turns)
    return (
        "Compact this Raticode agent-node conversation memory for future node runs.\n"
        "Preserve durable goals, decisions, file paths, commands, inputs, outputs, "
        "errors, unresolved tasks, and details required to continue the workflow. "
        "Omit chatter and redundant text.\n\n"
        f"{transcript}"
    )


async def _summarize_agent_turns(
    turns: list[dict[str, str]],
    agent_config: AgentConfig,
    subscription: Subscription,
    cancel_event: threading.Event | None,
) -> str:
    prompt = _agent_memory_compaction_prompt(turns)
    try:
        result = await subscription.execute(
            prompt=prompt,
            working_dir=agent_config.working_dir,
            tools=agent_config.tools,
            mcp_servers=agent_config.mcp_servers,
            env=agent_config.env,
            timeout=180,
            cancel_event=cancel_event,
            extra_paths=configured_extra_paths(agent_config),
        )
    except Exception:  # noqa: BLE001
        return _fallback_turn_summary(turns)
    if not result.success:
        return _fallback_turn_summary(turns)
    summary = (result.message or result.output).strip()
    return summary or _fallback_turn_summary(turns)


def _fallback_turn_summary(turns: list[dict[str, str]]) -> str:
    transcript = _turns_transcript(turns)
    if len(transcript) <= 12_000:
        return transcript
    return (
        f"{transcript[:6_000]}\n\n[...middle omitted during compaction...]\n\n{transcript[-6_000:]}"
    )


def _load_tabular(
    path: Path,
    max_items: int | None = None,
    max_file_read_bytes: int | None = None,
    max_aggregate_read_bytes: int | None = None,
) -> list[dict[str, object]]:
    suffix = path.suffix.lower()
    if max_file_read_bytes is not None:
        require_limit(path.stat().st_size, max_file_read_bytes, f"{path} size")
    aggregate_bytes = 0

    def _with_row(row: dict[str, object]) -> dict[str, object]:
        return {**row, "_row": json.dumps(row)}

    def _append_row(
        rows: list[dict[str, object]],
        row: dict[str, object],
    ) -> None:
        nonlocal aggregate_bytes
        if max_items is not None and len(rows) >= max_items:
            raise ResourceLimitError(f"tabular fan-out exceeded limit {max_items} items")
        item = _with_row(row)
        row_bytes = byte_len(json.dumps(item, default=str))
        if max_file_read_bytes is not None:
            require_limit(row_bytes, max_file_read_bytes, "tabular fan-out row")
        aggregate_bytes += row_bytes
        if max_aggregate_read_bytes is not None and aggregate_bytes > max_aggregate_read_bytes:
            raise ResourceLimitError(
                "tabular fan-out content exceeded aggregate limit "
                f"{max_aggregate_read_bytes} bytes (got {aggregate_bytes} bytes)"
            )
        rows.append(item)

    if suffix == ".jsonl":
        jsonl_rows: list[dict[str, object]] = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    if max_items is not None and len(jsonl_rows) >= max_items:
                        raise ResourceLimitError(
                            f"tabular fan-out exceeded limit {max_items} items"
                        )
                    _append_row(jsonl_rows, json.loads(line))
        return jsonl_rows
    if suffix == ".csv":
        csv_rows: list[dict[str, object]] = []
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                _append_row(csv_rows, dict(row))
        return csv_rows
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError(
                "openpyxl is required for .xlsx support: pip install 'gofer-flow[xlsx]'"
            ) from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h) for h in next(rows_iter)]
            xlsx_rows: list[dict[str, object]] = []
            for row in rows_iter:
                _append_row(xlsx_rows, dict(zip(headers, row)))
            return xlsx_rows
        finally:
            wb.close()
    raise ValueError(f"Unsupported tabular format: {suffix!r}. Use .jsonl, .csv, or .xlsx")


def _token_vector(text: str) -> dict[str, float]:
    tokens = re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
    vector: dict[str, float] = {}
    for token in tokens:
        key = hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest()
        vector[key] = vector.get(key, 0.0) + 1.0
    norm = sum(value * value for value in vector.values()) ** 0.5
    if norm:
        vector = {key: value / norm for key, value in vector.items()}
    return vector


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


class LocalVectorStrategy(Protocol):
    embedding_strategy: str
    search_strategy: str

    def embed(self, text: str) -> dict[str, float]: ...

    def score(self, query_vector: dict[str, float], entry_vector: object) -> float: ...


@dataclass(frozen=True)
class HashTokenCosineStrategy:
    embedding_strategy: str = "hash_token_v1"
    search_strategy: str = "cosine_v1"

    def embed(self, text: str) -> dict[str, float]:
        return _token_vector(text)

    def score(self, query_vector: dict[str, float], entry_vector: object) -> float:
        if not isinstance(entry_vector, dict):
            return 0.0
        return _cosine_similarity(query_vector, cast(dict[str, float], entry_vector))


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    overlap = max(0, min(chunk_overlap, chunk_size - 1))
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += chunk_size - overlap
    return chunks or [""]


VECTOR_INDEX_VERSION = 2
VECTOR_EMBEDDING_STRATEGY = "hash_token_v1"
VECTOR_SEARCH_STRATEGY = "cosine_v1"
LOCAL_VECTOR_STRATEGIES: dict[tuple[str, str], LocalVectorStrategy] = {
    (VECTOR_EMBEDDING_STRATEGY, VECTOR_SEARCH_STRATEGY): cast(
        LocalVectorStrategy,
        HashTokenCosineStrategy(),
    ),
}


def _local_vector_strategy(
    embedding_strategy: str,
    search_strategy: str,
) -> LocalVectorStrategy:
    strategy = LOCAL_VECTOR_STRATEGIES.get((embedding_strategy, search_strategy))
    if strategy is None:
        raise ValueError(
            "Unsupported local vector strategy: "
            f"embedding={embedding_strategy!r}, search={search_strategy!r}"
        )
    return strategy


def _gofer_version() -> str:
    try:
        return importlib.metadata.version("gofer-flow")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _hash_file(path: Path, max_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            total += len(block)
            if total > max_bytes:
                raise ResourceLimitError(
                    f"{path} size exceeded limit {max_bytes} bytes (got {total} bytes)"
                )
            digest.update(block)
    return digest.hexdigest()


def _vector_file_id(path: Path) -> str:
    return str(path.resolve(strict=False))


def _default_vector_entries_path(index_path: Path) -> Path:
    return index_path.with_name(f"{index_path.name}.entries.jsonl")


def _vector_entries_path(index_path: Path, index: dict[str, Any] | None = None) -> Path:
    if index:
        entries_file = index.get("entries_file")
        if isinstance(entries_file, str) and entries_file:
            path = Path(entries_file)
            return path if path.is_absolute() else index_path.parent / path
    return _default_vector_entries_path(index_path)


def _iter_vector_sidecar_entries(
    entries_path: Path,
    max_bytes: int,
) -> Iterator[dict[str, Any]]:
    consumed = 0
    try:
        with entries_path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                consumed += byte_len(line)
                if consumed > max_bytes:
                    raise ResourceLimitError(
                        f"local vector index entries exceeded limit {max_bytes} bytes"
                    )
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid vector index entries JSONL: {entries_path}:{line_number}"
                    ) from exc
                if not isinstance(entry, dict):
                    raise ValueError(f"Invalid vector index entry: {entries_path}:{line_number}")
                yield entry
    except FileNotFoundError as exc:
        raise ValueError(f"Missing vector index entries file: {entries_path}") from exc


def _iter_vector_index_entries(
    index_path: Path,
    index: dict[str, Any] | None,
    max_bytes: int,
) -> Iterator[dict[str, Any]]:
    if not index:
        return
    entries = index.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                yield entry
        return
    if isinstance(index.get("entries_file"), str):
        yield from _iter_vector_sidecar_entries(
            _vector_entries_path(index_path, index),
            max_bytes,
        )


def _vector_index_disk_size(index_path: Path, index: dict[str, Any] | None) -> int:
    size = index_path.stat().st_size if index_path.exists() else 0
    entries_file = index.get("entries_file") if isinstance(index, dict) else None
    if isinstance(entries_file, str):
        entries_path = _vector_entries_path(index_path, index)
        if entries_path.exists():
            size += entries_path.stat().st_size
    return size


def _write_vector_entry(file: Any, entry: dict[str, object]) -> int:
    line = json.dumps(entry, default=str) + "\n"
    file.write(line)
    return byte_len(line)


def _load_vector_index(
    index_path: Path,
    max_bytes: int,
    *,
    include_entries: bool = True,
) -> dict[str, Any] | None:
    if not index_path.exists():
        return None
    try:
        index_text = read_text_limited(index_path, max_bytes=max_bytes)
        index = json.loads(index_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid vector index JSON: {index_path}") from exc
    if not isinstance(index, dict):
        raise ValueError(f"Invalid vector index format: {index_path}")
    entries = index.get("entries")
    if entries is not None and not isinstance(entries, list):
        raise ValueError(f"Invalid vector index entries: {index_path}")
    if include_entries and entries is None and isinstance(index.get("entries_file"), str):
        index["entries"] = list(
            _iter_vector_sidecar_entries(
                _vector_entries_path(index_path, index),
                max_bytes,
            )
        )
    return index


def _vector_index_metadata(
    op: LocalVectorizeOperation,
    source_path: Path,
) -> dict[str, object]:
    return {
        "source_root": str(source_path),
        "glob": op.glob,
        "recursive": op.recursive,
        "chunk_size": op.chunk_size,
        "chunk_overlap": op.chunk_overlap,
        "encoding": op.encoding,
        "gofer_version": _gofer_version(),
        "embedding_strategy": op.embedding_strategy,
        "search_strategy": op.search_strategy,
    }


def _vector_index_compatible(
    index: dict[str, Any] | None,
    metadata: dict[str, object],
) -> bool:
    if not index or index.get("version") != VECTOR_INDEX_VERSION:
        return False
    existing = index.get("metadata")
    if not isinstance(existing, dict):
        return False
    compared_keys = (
        "source_root",
        "glob",
        "recursive",
        "chunk_size",
        "chunk_overlap",
        "encoding",
        "embedding_strategy",
        "search_strategy",
    )
    return all(existing.get(key) == metadata.get(key) for key in compared_keys)


def _vector_index_file_records(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not index:
        return {}
    files = index.get("files")
    if not isinstance(files, dict):
        return {}
    return {str(file_id): record for file_id, record in files.items() if isinstance(record, dict)}


def _vector_index_entries_by_file(index: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    entries_by_file: dict[str, list[dict[str, Any]]] = {}
    if not index:
        return entries_by_file
    for entry in index.get("entries", []):
        if not isinstance(entry, dict):
            continue
        file_id = entry.get("file_id")
        if not isinstance(file_id, str):
            path = entry.get("path")
            file_id = str(path) if path is not None else ""
        if file_id:
            entries_by_file.setdefault(file_id, []).append(entry)
    return entries_by_file


def _resolve_fan_items(
    source: FanSource,
    ctx: ExecutionContext,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
    path_base: Path | None = None,
    data_dir: Path | None = None,
    require_path_access: Callable[[Path, Literal["read", "write", "execute"]], None] | None = None,
) -> list[dict[str, object]]:
    if isinstance(source, CountFanSource):
        require_reference_capability("nodes[type=loop].source.count")
        count = ctx.resolve_dynamic_count(source.count)
        if count > limits.max_fanout_items:
            raise ResourceLimitError(
                f"count fan-out exceeded limit {limits.max_fanout_items} items (got {count})"
            )
        return [{"index": str(i)} for i in range(count)]
    if isinstance(source, TabularFanSource):
        path = _resolve_workflow_path(source.path, path_base)
        if require_path_access is not None:
            require_path_access(path, "read")
        return _load_tabular(
            path,
            max_items=limits.max_fanout_items,
            max_file_read_bytes=limits.max_file_read_bytes,
            max_aggregate_read_bytes=limits.max_aggregate_read_bytes,
        )
    if isinstance(source, DirectoryFanSource):
        source_path = _resolve_workflow_path(source.path, path_base)
        if require_path_access is not None:
            require_path_access(source_path, "read")
        items: list[dict[str, object]] = []
        aggregate_bytes = 0
        scanned = 0
        for p in source_path.glob(source.glob):
            scanned += 1
            if scanned > limits.max_files_scanned:
                raise ResourceLimitError(
                    f"directory fan-out scan exceeded limit {limits.max_files_scanned} paths"
                )
            if p.is_file():
                if len(items) >= limits.max_fanout_items:
                    raise ResourceLimitError(
                        f"directory fan-out exceeded limit {limits.max_fanout_items} items"
                    )
                entry: dict[str, object] = {
                    **_file_path_data(p),
                }
                if source.include_content:
                    if require_path_access is not None:
                        require_path_access(p, "read")
                    size = p.stat().st_size
                    require_limit(size, limits.max_file_read_bytes, f"{p} size")
                    aggregate_bytes += size
                    if aggregate_bytes > limits.max_aggregate_read_bytes:
                        raise ResourceLimitError(
                            "directory fan-out content exceeded aggregate limit "
                            f"{limits.max_aggregate_read_bytes} bytes "
                            f"(got {aggregate_bytes} bytes)"
                        )
                    entry["file_content"] = p.read_text(errors="replace")
                items.append(entry)
        return sorted(items, key=lambda item: str(item.get("path", "")))
    if isinstance(source, TriggerEventsFanSource):
        events = ctx.trigger.get("events", [])
        if not isinstance(events, list):
            return []
        items = []
        aggregate_bytes = 0
        for idx, event in enumerate(events):
            if len(items) >= limits.max_fanout_items:
                raise ResourceLimitError(
                    f"trigger-event fan-out exceeded limit {limits.max_fanout_items} items"
                )
            if not isinstance(event, dict):
                continue
            item = {
                **event,
                "index": str(idx),
                "event_json": json.dumps(event),
            }
            event_path_value = event.get("path")
            if event_path_value:
                event_path = Path(str(event_path_value))
                item.update(_file_path_data(event_path))
                item.setdefault("name", event_path.name)
                item.setdefault("directory", str(event_path.parent))
            if source.include_content and event_path_value:
                file_path = Path(str(event_path_value))
                if require_path_access is not None:
                    require_path_access(file_path, "read")
                if file_path.exists() and file_path.is_file():
                    size = file_path.stat().st_size
                    require_limit(size, limits.max_file_read_bytes, f"{file_path} size")
                    aggregate_bytes += size
                    if aggregate_bytes > limits.max_aggregate_read_bytes:
                        raise ResourceLimitError(
                            "trigger-event fan-out content exceeded aggregate limit "
                            f"{limits.max_aggregate_read_bytes} bytes "
                            f"(got {aggregate_bytes} bytes)"
                        )
                    item["file_content"] = file_path.read_text(errors="replace")
            items.append(item)
        return items
    if isinstance(source, InfiniteFanSource):
        return []
    raise ValueError(f"Unknown fan source type: {source}")  # pragma: no cover


@dataclass
class QueuedNode:
    node_id: str
    after_loop_origin: str | None = None
    loop_origin: str | None = None
    loop_item: dict[str, object] | None = None
    item_context: dict[str, dict[str, object]] = field(default_factory=dict)
    loop_index: int | None = None
    loop_items: list[dict[str, object]] | None = None
    loop_infinite: bool = False
    loop_max_concurrency: int = 1
    loop_fail_fast: bool = False

    @property
    def key(self) -> tuple[str, str | None, int | None, str]:
        return (
            self.node_id,
            self.loop_origin or self.after_loop_origin,
            self.loop_index,
            _item_context_key(self.item_context),
        )


QueuedTaskKey = tuple[str, str | None, int | None, str]
_NODE_VALUE_UNSET = object()


def _item_context_key(item_context: dict[str, dict[str, object]] | None) -> str:
    if not item_context:
        return ""
    return json.dumps(item_context, sort_keys=True, default=str)


@dataclass
class NodeOutput:
    node_id: str
    success: bool
    output: str
    exit_code: int
    duration_seconds: float
    skipped: bool = False
    fan_outputs: list[tuple[str, str]] = field(default_factory=list)
    terminal_status: str | None = None
    loop_items: list[dict[str, object]] | None = None
    loop_infinite: bool = False
    loop_max_concurrency: int = 1
    loop_fail_fast: bool = False
    type: str = ""
    text: str | None = None
    value: object | None = field(default=_NODE_VALUE_UNSET)
    data: dict[str, object] = field(default_factory=dict)
    items: list[object] = field(default_factory=list)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.text is None:
            self.text = self.output
        if self.value is _NODE_VALUE_UNSET:
            self.value = self.text
        if not self.success and self.error is None:
            self.error = self.output

    def contract(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "type": self.type,
            "success": self.success,
            "text": self.text or "",
            "output": self.output,
            "value": self.value,
            "data": self.data,
            "items": self.items,
            "error": self.error,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "skipped": self.skipped,
            "terminal_status": self.terminal_status,
            "loop_items": self.loop_items,
            "loop_infinite": self.loop_infinite,
            "loop_max_concurrency": self.loop_max_concurrency,
            "loop_fail_fast": self.loop_fail_fast,
        }


class CheckpointSecretKeyUnavailable(ValueError):
    """Raised when a local encrypted checkpoint can no longer be opened."""


@dataclass
class ExecutionContext:
    node_outputs: dict[str, NodeOutput] = field(default_factory=dict)
    node_runs: dict[str, list[NodeOutput]] = field(default_factory=dict)
    trigger: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ``params`` remains a compatibility view of immutable invocation inputs.
        if self.inputs and self.params and self.inputs != self.params:
            raise ValueError("ExecutionContext inputs and params must describe the same values")
        if not self.inputs:
            self.inputs = dict(self.params)
        if not self.params:
            self.params = self.inputs

    def record(self, output: NodeOutput) -> None:
        self.node_outputs[output.node_id] = output
        self.node_runs.setdefault(output.node_id, []).append(output)

    def resolve_dynamic_count(self, value: int | str | None) -> int:
        if value is None:
            return 1
        if isinstance(value, int):
            return value
        if not str(value).strip():
            return 1
        if str(value).strip().isdigit():
            return int(str(value).strip())
        expression = parse_exact_reference(value)
        if expression is None:
            raise ValueError(f"Cannot resolve dynamic_count path: {value!r}")
        parts = expression.split(".")
        if parts and parts[0] in {ReferenceNamespace.PARAMS, ReferenceNamespace.INPUTS}:
            obj: Any = self.inputs
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.VARS:
            obj = self.variables
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.NODES:
            obj = {k: v.contract() for k, v in self.node_outputs.items()}
            parts = parts[1:]
        else:
            obj = {k: v.contract() for k, v in self.node_outputs.items()}
        for part in parts:
            if not isinstance(obj, dict):
                raise ValueError(f"Cannot resolve dynamic_count path: {value!r}")
            obj = obj.get(part)
        if obj is None:
            raise ValueError(f"Cannot resolve dynamic_count path: {value!r}")
        return int(obj)

    def resolve_path(self, value: str) -> object:
        return self.resolve_path_with_loop(value, None)

    def resolve_path_with_loop(
        self,
        value: str,
        loop_item: dict[str, object] | None = None,
        current_node_id: str | None = None,
        graph: WorkflowGraph | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> object:
        if value in self.node_outputs:
            return self.node_outputs[value].output
        expression = parse_exact_reference(value)
        if expression is None:
            raise ValueError(f"Cannot resolve path: {value!r}")
        parts = expression.split(".")
        if parts and parts[0] == ReferenceNamespace.TRIGGER:
            obj: Any = self.trigger
            parts = parts[1:]
        elif parts and parts[0] in {ReferenceNamespace.PARAMS, ReferenceNamespace.INPUTS}:
            obj = self.inputs
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.VARS:
            obj = self.variables
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.NODES:
            obj = {k: v.contract() for k, v in self.node_outputs.items()}
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.LOOP:
            obj = {
                "current": loop_item or {},
                "items": item_context or {},
                **(loop_item or {}),
            }
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.ITEM:
            obj = loop_item or {}
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.ITEMS:
            obj = item_context or {}
            parts = parts[1:]
        elif parts and parts[0] == ReferenceNamespace.PREVIOUS:
            if current_node_id is None or graph is None:
                obj = {}
            else:
                predecessors = [
                    self.node_outputs[pid]
                    for pid in graph._graph.predecessors(current_node_id)
                    if pid in self.node_outputs
                ]
                obj = predecessors[-1].contract() if predecessors else {}
            parts = parts[1:]
        else:
            obj = {k: v.contract() for k, v in self.node_outputs.items()}
        for part in parts:
            if isinstance(obj, list):
                obj = obj[int(part)]
                continue
            if not isinstance(obj, dict):
                raise ValueError(f"Cannot resolve path: {value!r}")
            obj = obj.get(part)
        return "" if obj is None else obj

    def predecessor_outputs(self, node_id: str, graph: WorkflowGraph) -> list[NodeOutput]:
        return [
            self.node_outputs[pid]
            for pid in graph._graph.predecessors(node_id)
            if pid in self.node_outputs
        ]


@dataclass
class ExecutionResult:
    workflow_id: str
    success: bool
    node_outputs: dict[str, NodeOutput]
    duration_seconds: float
    node_runs: dict[str, list[NodeOutput]] = field(default_factory=dict)
    log_path: Path | None = None
    usage_summary: dict[str, object] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeOptions:
    run_id: str | None = None
    from_node: str | None = None
    only_node: str | None = None
    skip_cache: bool = False
    force: bool = False


class WorkflowRunLog:
    _MAX_EVENT_STRING_BYTES = 2000
    _MAX_EVENT_LIST_ITEMS = 20
    _MAX_EVENT_DICT_ITEMS = 80
    _MAX_LIVE_AGENT_THOUGHTS = 20

    def __init__(
        self,
        workflow_id: str,
        base_dir: Path | None = None,
        limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
        existing_path: Path | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self._limits = limits
        self._node_log_bytes: dict[tuple[str, int | None, int | None], int] = {}
        self._node_log_omissions: set[tuple[tuple[str, int | None, int | None], str]] = set()
        self._active_node_runs: dict[str, tuple[int | None, int | None]] = {}
        self._run_log_bytes = 0
        self.started_at = datetime.now().astimezone()
        if existing_path is not None:
            self.path = existing_path
            self.events_path = self.path.with_suffix(".events.json")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._run_log_bytes = byte_len(self.path.read_text(encoding="utf-8"))
            else:
                self._append(f"{self._now()} - {self.workflow_id} started successfully\n")
                self._write_events_payload({"events": [], "nodes": {}})
            return
        timestamp = self.started_at.strftime("%Y-%m-%dT%H-%M-%S%f%z")
        root = base_dir or get_data_dir() / "logs"
        self.path = root / workflow_id / f"{timestamp}.log"
        self.events_path = self.path.with_suffix(".events.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._append(f"{self._now()} - {self.workflow_id} started successfully\n")
        self._write_events_payload({"events": [], "nodes": {}})

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def node(self, node_id: str, message: str) -> None:
        self._write("NODE", f"{node_id} - {message}")

    def begin_node_attempt(self, node_id: str, run_number: int, attempt: int) -> None:
        self._active_node_runs[node_id] = (run_number, attempt)

    def node_output(
        self,
        node_id: str,
        label: str,
        value: str,
        *,
        uncapped: bool = False,
    ) -> None:
        if not value:
            return
        self._write_node_event(node_id, label, value, uncapped=uncapped)

    def node_agent_event(self, node_id: str, label: str, value: str) -> None:
        if not value:
            return
        if label == "AGENT_THOUGHT":
            value = summarize_thought(value)
            if not value:
                return
        if not value.rstrip("\n").splitlines():
            return
        self._write_node_event(node_id, label, value, uncapped=label == "AGENT_MESSAGE")
        self._update_node_agent_event_data(node_id, label, value)

    def active_attempt_preview(self, node_id: str, data: dict[str, object]) -> None:
        if not data:
            return
        payload = self._read_events_payload()
        nodes = payload.setdefault("nodes", {})
        if not isinstance(nodes, dict):
            return
        node_state = nodes.setdefault(node_id, {"nodeId": node_id, "attempts": []})
        if not isinstance(node_state, dict):
            return
        run_number, attempt = self._active_node_runs.get(node_id, (None, None))
        compacted = self._compact_event_value(data)
        if not isinstance(compacted, dict):
            return
        self._update_active_attempt_preview(
            node_state,
            run_number=run_number,
            attempt=attempt,
            patch=compacted,
        )
        node_state["updatedAt"] = self._now()
        self._write_events_payload(payload)

    def complete(
        self,
        success: bool,
        reason: str | None = None,
        *,
        terminal_status: Literal["completed", "failed", "stopped"] | None = None,
    ) -> None:
        status = terminal_status or ("completed" if success else "failed")
        if status == "completed":
            self.info(f"{self.workflow_id} completed successfully")
            message = reason or "completed successfully"
        elif status == "stopped":
            message = reason or "stopped by user"
            self.info(f"{self.workflow_id} {message}")
        else:
            self.error(f"{self.workflow_id} failed due to {reason or 'unknown error'}")
            message = reason or "failed"
        self.event("workflow", status, message=message, success=success)

    def event(
        self,
        node_id: str,
        status: str,
        *,
        attempt: int | None = None,
        run_number: int | None = None,
        message: str = "",
        duration_seconds: float | None = None,
        exit_code: int | None = None,
        success: bool | None = None,
        skipped: bool = False,
        fan_out_item: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        payload = self._read_events_payload()
        events = payload.setdefault("events", [])
        nodes = payload.setdefault("nodes", {})
        if not isinstance(events, list) or not isinstance(nodes, dict):
            payload = {"events": [], "nodes": {}}
            events = payload["events"]
            nodes = payload["nodes"]
        events_list = cast(list[dict[str, object]], events)
        nodes_by_id = cast(dict[str, object], nodes)
        occurred_at = self._now()
        event_fan_out_item = self._compact_event_value(fan_out_item)
        if event_fan_out_item is not None and not isinstance(event_fan_out_item, dict):
            event_fan_out_item = {"value": event_fan_out_item}
        event_data = self._compact_event_value(data or {})
        if not isinstance(event_data, dict):
            event_data = {}
        event = {
            "nodeId": node_id,
            "status": status,
            "occurredAt": occurred_at,
            "attempt": attempt,
            "runNumber": run_number,
            "message": message,
            "durationSeconds": duration_seconds,
            "exitCode": exit_code,
            "success": success,
            "skipped": skipped,
            "fanOutItem": event_fan_out_item,
            "data": event_data,
        }
        events_list.append({key: value for key, value in event.items() if value is not None})
        if node_id != "workflow":
            node_state = nodes_by_id.setdefault(node_id, {"nodeId": node_id, "attempts": []})
            if isinstance(node_state, dict):
                if status != "edge_decision":
                    node_state.update(
                        {
                            "nodeId": node_id,
                            "status": status,
                            "updatedAt": occurred_at,
                            "durationSeconds": duration_seconds,
                            "exitCode": exit_code,
                            "success": success,
                            "skipped": skipped,
                            "message": message,
                        }
                    )
                if status == "started":
                    attempts = node_state.setdefault("attempts", [])
                    if isinstance(attempts, list):
                        attempts.append(
                            {
                                "attempt": attempt,
                                "runNumber": run_number,
                                "startedAt": occurred_at,
                                "fanOutItem": event_fan_out_item,
                                "inputs": event_data.get("inputs", {}),
                            }
                        )
                if status in {"completed", "failed", "stopped"}:
                    attempts = node_state.setdefault("attempts", [])
                    if isinstance(attempts, list) and attempts:
                        matching_attempt = self._matching_attempt(
                            attempts,
                            attempt=attempt,
                            run_number=run_number,
                            fan_out_item=event_fan_out_item,
                        )
                        matching_attempt.update(
                            {
                                "finishedAt": occurred_at,
                                "durationSeconds": duration_seconds,
                                "exitCode": exit_code,
                                "success": success,
                                "output": event_data.get("output", ""),
                                "error": event_data.get("error", ""),
                            }
                        )
                        for detail_key in ("inputs", "stdout", "stderr", "message", "prompt"):
                            if detail_key in event_data:
                                matching_attempt[detail_key] = event_data[detail_key]
                        if event_fan_out_item is not None:
                            aggregate_status, aggregate_success = self._fan_out_attempt_status(
                                attempts,
                                fallback_status=status,
                            )
                            node_state["status"] = aggregate_status
                            node_state["success"] = aggregate_success
                if event_data:
                    existing_data = cast(dict[str, object], node_state.get("data", {}))
                    if status == "edge_decision":
                        decisions = existing_data.setdefault("edgeDecisions", [])
                        if isinstance(decisions, list):
                            decisions.append(event_data)
                    else:
                        existing_data.update(event_data)
                    node_state["data"] = existing_data
        self._write_events_payload(payload)

    def update_node_data(self, node_id: str, data: dict[str, object]) -> None:
        payload = self._read_events_payload()
        nodes = payload.setdefault("nodes", {})
        if not isinstance(nodes, dict):
            return
        node_state = nodes.setdefault(node_id, {"nodeId": node_id, "attempts": []})
        if not isinstance(node_state, dict):
            return
        existing_data = cast(dict[str, object], node_state.get("data", {}))
        existing_data.update(data)
        node_state["data"] = existing_data
        node_state["updatedAt"] = self._now()
        self._write_events_payload(payload)

    def _update_node_agent_event_data(self, node_id: str, label: str, value: str) -> None:
        payload = self._read_events_payload()
        nodes = payload.setdefault("nodes", {})
        if not isinstance(nodes, dict):
            return
        node_state = nodes.setdefault(node_id, {"nodeId": node_id, "attempts": []})
        if not isinstance(node_state, dict):
            return
        existing_data = cast(dict[str, object], node_state.get("data", {}))
        value_preview = self._compact_event_value(value)
        if not isinstance(value_preview, str):
            value_preview = str(value_preview)
        if label == "AGENT_THOUGHT":
            thoughts = existing_data.setdefault("thoughts", [])
            if isinstance(thoughts, list):
                thoughts.append(value_preview)
                del thoughts[: -self._MAX_LIVE_AGENT_THOUGHTS]
            existing_data["latestThought"] = value_preview
            node_state["status"] = "started"
            node_state["message"] = value_preview
            run_number, attempt = self._active_node_runs.get(node_id, (None, None))
            self._update_active_attempt_preview(
                node_state,
                run_number=run_number,
                attempt=attempt,
                patch={"latestThought": value_preview},
            )
        elif label == "AGENT_MESSAGE":
            existing_data["message"] = value_preview
            node_state["message"] = value_preview
            run_number, attempt = self._active_node_runs.get(node_id, (None, None))
            self._update_active_attempt_preview(
                node_state,
                run_number=run_number,
                attempt=attempt,
                patch={"message": value_preview},
            )
        else:
            return
        node_state["data"] = existing_data
        node_state["updatedAt"] = self._now()
        self._write_events_payload(payload)

    @classmethod
    def _compact_event_value(cls, value: object) -> object:
        if isinstance(value, str):
            return truncate_text_bytes(
                value,
                cls._MAX_EVENT_STRING_BYTES,
                "event payload",
            )
        if isinstance(value, dict):
            compacted: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= cls._MAX_EVENT_DICT_ITEMS:
                    compacted["__truncated__"] = (
                        f"{len(value) - cls._MAX_EVENT_DICT_ITEMS} additional keys omitted"
                    )
                    break
                compacted[str(key)] = cls._compact_event_value(item)
            return compacted
        if isinstance(value, list):
            items = [cls._compact_event_value(item) for item in value[: cls._MAX_EVENT_LIST_ITEMS]]
            if len(value) > cls._MAX_EVENT_LIST_ITEMS:
                items.append(f"{len(value) - cls._MAX_EVENT_LIST_ITEMS} additional items omitted")
            return items
        return value

    @staticmethod
    def _update_active_attempt_preview(
        node_state: dict[str, object],
        *,
        run_number: int | None,
        attempt: int | None,
        patch: dict[str, object],
    ) -> None:
        attempts = node_state.get("attempts")
        if not isinstance(attempts, list):
            return
        for candidate in reversed(attempts):
            if not isinstance(candidate, dict):
                continue
            if candidate.get("finishedAt") is not None:
                continue
            if run_number is not None and candidate.get("runNumber") != run_number:
                continue
            if attempt is not None and candidate.get("attempt") != attempt:
                continue
            candidate.update(patch)
            return

    @staticmethod
    def _matching_attempt(
        attempts: list[object],
        *,
        attempt: int | None,
        run_number: int | None,
        fan_out_item: dict[str, object] | None,
    ) -> dict[str, object]:
        for candidate in reversed(attempts):
            if not isinstance(candidate, dict):
                continue
            if candidate.get("finishedAt") is not None:
                continue
            if attempt is not None and candidate.get("attempt") != attempt:
                continue
            if run_number is not None and candidate.get("runNumber") != run_number:
                continue
            if candidate.get("fanOutItem") != fan_out_item:
                continue
            return candidate
        for candidate in reversed(attempts):
            if isinstance(candidate, dict):
                return candidate
        attempts.append({})
        return cast(dict[str, object], attempts[-1])

    @staticmethod
    def _fan_out_attempt_status(
        attempts: list[object],
        *,
        fallback_status: str,
    ) -> tuple[str, bool | None]:
        fan_attempts = [
            attempt
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("fanOutItem") is not None
        ]
        if not fan_attempts:
            return fallback_status, None
        if any(attempt.get("finishedAt") is None for attempt in fan_attempts):
            return "started", None
        if any(attempt.get("success") is False for attempt in fan_attempts):
            return "failed", False
        if all(attempt.get("success") is True for attempt in fan_attempts):
            return "completed", True
        return fallback_status, None

    def _write(self, level: str, message: str) -> None:
        self._append(f"{self._now()} - {level} - {message}\n")

    def _write_uncapped(self, level: str, message: str) -> None:
        self._append_uncapped(f"{self._now()} - {level} - {message}\n")

    def _node_log_key(self, node_id: str) -> tuple[str, int | None, int | None]:
        run_number, attempt = self._active_node_runs.get(node_id, (None, None))
        return (node_id, run_number, attempt)

    def _write_node_event(
        self,
        node_id: str,
        label: str,
        value: str,
        *,
        uncapped: bool = False,
    ) -> None:
        body = "".join(f"{line}\n" for line in value.rstrip("\n").splitlines())
        if not body:
            return
        if label == "AGENT_THOUGHT":
            body = self._truncate_agent_thought(
                body,
                self._limits.max_log_message_bytes,
                f"{node_id} {label}",
            )
        if not uncapped:
            body = self._fit_node_body(node_id, body, label)
        if not body:
            return
        if uncapped:
            self._write_uncapped("NODE", f"{node_id} - {label}:")
        else:
            self._write("NODE", f"{node_id} - {label}:")
        if uncapped:
            self._append_node_body_uncapped(node_id, body)
        else:
            self._append_node_body(node_id, body)

    def _fit_node_body(self, node_id: str, body: str, label: str) -> str:
        key = self._node_log_key(node_id)
        remaining_node = self._limits.max_log_bytes_per_node - self._node_log_bytes.get(key, 0)
        remaining_run = self._limits.max_log_bytes_per_run - self._run_log_bytes
        limit = max(0, min(remaining_node, remaining_run))
        if limit <= 0:
            self._write_node_omission_line(node_id, label)
            return ""
        body = truncate_text_bytes(body, limit, f"{node_id} {label}")
        return self._ensure_body_newline(body, limit)

    def _truncate_agent_thought(self, body: str, max_bytes: int, label: str) -> str:
        if byte_len(body) <= max_bytes:
            return self._ensure_body_newline(body, max_bytes)
        if max_bytes <= 0:
            return ""
        suffix = f"\n[{label} truncated at {max_bytes} bytes]\n".encode()
        encoded = body.encode("utf-8", errors="replace")
        head_size = max(0, max_bytes - len(suffix))
        return (encoded[:head_size] + suffix)[:max_bytes].decode("utf-8", errors="replace")

    def _ensure_body_newline(self, body: str, max_bytes: int) -> str:
        if body.endswith("\n"):
            return body
        if max_bytes <= 0:
            return ""
        if byte_len(body) < max_bytes:
            return body + "\n"
        encoded = body.encode("utf-8", errors="replace")
        return (encoded[: max_bytes - 1] + b"\n").decode("utf-8", errors="replace")

    def _append_node_body(self, node_id: str, body: str) -> None:
        key = self._node_log_key(node_id)
        written = self._append(body)
        self._node_log_bytes[key] = self._node_log_bytes.get(key, 0) + written

    def _append_node_body_uncapped(self, node_id: str, body: str) -> None:
        key = self._node_log_key(node_id)
        written = self._append_uncapped(body)
        self._node_log_bytes[key] = self._node_log_bytes.get(key, 0) + written

    def _write_node_omission_line(self, node_id: str, label: str) -> None:
        key = (self._node_log_key(node_id), label)
        if key in self._node_log_omissions:
            return
        self._node_log_omissions.add(key)
        self.node(
            node_id,
            f"{label} omitted; log limit exceeded "
            f"(node limit {self._limits.max_log_bytes_per_node} bytes, "
            f"run limit {self._limits.max_log_bytes_per_run} bytes)",
        )

    def _append(self, text: str) -> int:
        remaining_run = self._limits.max_log_bytes_per_run - self._run_log_bytes
        if remaining_run <= 0:
            return 0
        text = truncate_text_bytes(text, remaining_run, "run log")
        return self._append_uncapped(text)

    def _append_uncapped(self, text: str) -> int:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        written = byte_len(text)
        self._run_log_bytes += written
        return written

    def _read_events_payload(self) -> dict[str, object]:
        if not self.events_path.exists():
            return {"events": [], "nodes": {}}
        try:
            payload = json.loads(self.events_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"events": [], "nodes": {}}
        return payload if isinstance(payload, dict) else {"events": [], "nodes": {}}

    def _write_events_payload(self, payload: dict[str, object]) -> None:
        payload = {
            "workflowId": self.workflow_id,
            "runId": self.path.name,
            "logPath": str(self.path),
            "startedAt": self.started_at.isoformat(),
            **payload,
        }
        text = json.dumps(payload, default=str)
        tmp_path = self.events_path.with_name(
            f".{self.events_path.name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
        )
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, self.events_path)

    def _now(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")


class WorkflowExecutor:
    _SNAPSHOT_ARTIFACT_KEY = "__gofer_artifact__"
    _SENSITIVE_SNAPSHOT_VERSION = 1

    def __init__(
        self,
        workflow: AgenticWorkflow,
        subscriptions: dict[str, Subscription],
        dry_run: bool = False,
        log_base_dir: Path | None = None,
        run_log_path: Path | None = None,
        workflow_path: Path | None = None,
        max_total_node_runs: int | None = None,
        cancel_event: threading.Event | None = None,
        stop_file: Path | None = None,
        data_dir: Path | None = None,
        http_client: HttpClient | None = None,
        approval_store: ApprovalStore | None = None,
        notification_adapter: NotificationAdapter | None = None,
        run_log_update_callback: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._workflow = workflow
        self._subscriptions = subscriptions
        self._dry_run = dry_run
        self._log_base_dir = log_base_dir
        self._run_log_path = run_log_path
        self._workflow_path = workflow_path
        self._path_base = workflow_path.parent if workflow_path is not None else None
        self._max_total_node_runs = max_total_node_runs or workflow.config.max_total_node_runs
        self._pass_cancel_event = cancel_event is not None or stop_file is not None
        self._cancel_event = cancel_event or threading.Event()
        self._stop_file = stop_file
        self._run_stop_file: Path | None = None
        self._stop_monitor_done = threading.Event()
        self._trigger_context: dict[str, Any] = {}
        self._run_parameters: dict[str, Any] = {}
        self._run_log: WorkflowRunLog | None = None
        self._agent_run_memory: dict[str, list[dict[str, str]]] = {}
        self._limits = workflow.config.resource_limits
        self._http_client = http_client or UrllibHttpClient()
        store_data_dir = data_dir or (log_base_dir.parent if log_base_dir is not None else None)
        self._data_dir = store_data_dir
        self._approval_store = approval_store or ApprovalStore(store_data_dir)
        self._notification_adapter = notification_adapter or MultiChannelNotificationAdapter()
        self._run_log_update_callback = run_log_update_callback
        self._resume_options: ResumeOptions | None = None
        self._resume_task_entries: dict[str, dict[str, object]] = {}
        self._resume_seed_outputs: dict[str, NodeOutput] = {}
        self._resume_only_dependencies: set[str] = set()
        self._resume_cache_bypass_nodes: set[str] = set()
        self._execution_cache_snapshots: dict[QueuedTaskKey, tuple[dict[str, object], str]] = {}

    def with_resume_options(self, options: ResumeOptions) -> WorkflowExecutor:
        self._resume_options = options
        return self

    def with_trigger_context(self, trigger_context: dict[str, Any]) -> WorkflowExecutor:
        self._trigger_context = trigger_context
        return self

    def with_parameters(self, parameters: dict[str, Any] | None) -> WorkflowExecutor:
        self._run_parameters = parameters or {}
        return self

    def _parameter_config(self) -> WorkflowConfig:
        if self._workflow.component is None or not self._workflow.component.inputs:
            return self._workflow.config
        parameters = {
            **self._workflow.component.inputs,
            **self._workflow.config.declared_inputs,
        }
        return self._workflow.config.model_copy(update={"parameters": parameters, "inputs": {}})

    def _masked_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        return masked_workflow_parameters(self._parameter_config(), params)

    def _masked_variables(self, variables: dict[str, Any]) -> dict[str, Any]:
        return masked_workflow_variables(self._workflow.config, variables)

    def _initial_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        variables: dict[str, Any] = {}
        for name, declaration in self._workflow.config.variables.items():
            context: dict[str, object] = {
                ReferenceNamespace.INPUTS: inputs,
                ReferenceNamespace.PARAMS: inputs,
                ReferenceNamespace.VARS: variables,
            }
            value = self._render_reference_value(declaration.initial, context)
            variables[name] = coerce_workflow_variable(name, declaration, value)
        return variables

    def _log(self) -> WorkflowRunLog:
        if self._run_log is None:
            raise RuntimeError("Workflow run log has not been initialized")
        return self._run_log

    def _notify_run_log_update(self) -> None:
        if self._run_log_update_callback is None or self._run_log is None:
            return
        try:
            self._run_log_update_callback(self._workflow.config.id, self._run_log.path)
        except Exception:
            return

    def _load_child_workflow(self, workflow_id: str) -> tuple[AgenticWorkflow, Path]:
        target_id = workflow_id.strip()
        if not target_id:
            raise ValueError("Workflow node requires a target workflow")
        base = self._path_base or self._data_dir or get_data_dir()
        candidate = base / f"{target_id}.toml"
        if candidate.exists():
            return AgenticWorkflow.from_file(candidate), candidate
        for path in sorted(base.rglob("*.toml")):
            try:
                workflow = AgenticWorkflow.from_file(path)
            except Exception:
                continue
            if workflow.config.id == target_id:
                return workflow, path
        raise ValueError(f"Workflow '{target_id}' not found in {base}")

    def _load_subflow_component(self, op: SubflowOperation) -> tuple[AgenticWorkflow, Path]:
        target_id = op.component_id.strip()
        if not target_id:
            raise ValueError("Subflow node requires a component_id")
        base = self._path_base or self._data_dir or get_data_dir()
        if op.source_path is not None:
            source_path = op.source_path.expanduser()
            candidate = source_path if source_path.is_absolute() else base / source_path
            if not candidate.exists():
                raise ValueError(f"Subflow component source '{op.source_path}' not found")
            workflow = AgenticWorkflow.from_file(candidate)
            if workflow.component is not None and workflow.component.id != target_id:
                raise ValueError(
                    f"Subflow component '{target_id}' does not match source component "
                    f"'{workflow.component.id}'"
                )
            return workflow, candidate
        candidate = base / f"{target_id}.toml"
        if candidate.exists():
            workflow = AgenticWorkflow.from_file(candidate)
            if workflow.component is None or workflow.component.id == target_id:
                return workflow, candidate
        for path in sorted(base.rglob("*.toml")):
            try:
                workflow = AgenticWorkflow.from_file(path)
            except Exception:
                continue
            if workflow.component is not None and workflow.component.id == target_id:
                return workflow, path
        raise ValueError(f"Subflow component '{target_id}' not found in {base}")

    def _render_subflow_parameter_bindings(
        self,
        op: SubflowOperation,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        bindings = {**op.parameter_bindings, **op.input_bindings}
        conflicts = set(op.parameter_bindings) & set(op.input_bindings)
        if conflicts:
            raise ValueError(
                "Subflow input binding(s) declared twice: " + ", ".join(sorted(conflicts))
            )
        if not bindings:
            return {}
        template_context = self._template_context(node, ctx, graph, loop_item, item_context)
        return cast(
            dict[str, Any],
            self._render_http_value(
                bindings,
                template_context,
                (
                    "nodes[type=subflow].input_bindings.*"
                    if op.input_bindings
                    else "nodes[type=subflow].parameter_bindings.*"
                ),
            ),
        )

    @staticmethod
    def _subflow_output_selector(spec: object) -> str | None:
        if isinstance(spec, dict):
            for key in ("source", "from", "path", "value", "node"):
                value = spec.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None
        if not isinstance(spec, str) or not spec.strip():
            return None
        value = spec.strip()
        if value in {
            "string",
            "text",
            "multiline",
            "number",
            "boolean",
            "date",
            "time",
            "datetime",
            "file",
            "folder",
            "enum",
            "secret",
        }:
            return None
        return value

    @staticmethod
    def _resolve_subflow_contract_path(
        contracts: dict[str, object],
        selector: str,
    ) -> object:
        path = selector.strip()
        if path.startswith("{{") and path.endswith("}}"):
            path = path[2:-2].strip()
        if path in contracts:
            selected = contracts[path]
            return selected.get("value") if isinstance(selected, dict) else selected
        obj: object = contracts
        for part in path.split("."):
            if isinstance(obj, list):
                obj = obj[int(part)]
                continue
            if not isinstance(obj, dict):
                raise ValueError(f"Cannot resolve subflow output path: {selector!r}")
            obj = obj.get(part)
        return "" if obj is None else obj

    def _subflow_declared_outputs(
        self,
        op: SubflowOperation,
        component: object,
        child_result: ExecutionResult,
    ) -> dict[str, object]:
        if not isinstance(op, SubflowOperation) or not op.output_contract:
            return {}
        contracts = {
            node_id: output.contract() for node_id, output in child_result.node_outputs.items()
        }
        template_context: dict[str, object] = {
            **contracts,
            ReferenceNamespace.NODES: contracts,
            ReferenceNamespace.INPUTS: child_result.inputs,
            ReferenceNamespace.PARAMS: child_result.inputs,
            ReferenceNamespace.VARS: child_result.variables,
            "outputs": contracts,
            "workflow": {"id": child_result.workflow_id},
        }
        contract_root: dict[str, object] = dict(template_context)
        component_outputs = getattr(component, "outputs", {}) if component is not None else {}
        declared: dict[str, object] = {}
        missing_outputs: list[str] = []
        for name, parent_spec in op.output_contract.items():
            selector = self._subflow_output_selector(parent_spec)
            if selector is None and isinstance(component_outputs, dict):
                selector = self._subflow_output_selector(component_outputs.get(name))
            if selector is not None:
                if selector.startswith("{{") and selector.endswith("}}"):
                    declared[name] = self._render_reference_value(selector, template_context)
                else:
                    declared[name] = self._resolve_subflow_contract_path(contract_root, selector)
                continue
            if name in contracts:
                declared[name] = contracts[name].get("value")
                continue
            missing_outputs.append(name)
        if missing_outputs:
            raise ValueError(
                "Subflow did not produce required output(s): " + ", ".join(sorted(missing_outputs))
            )
        return declared

    def _log_agent_result(
        self,
        node_id: str,
        result: AgentResult,
        prefix: str = "",
        streamed_thought_count: int = 0,
    ) -> None:
        for thought in result.thoughts[streamed_thought_count:]:
            value = summarize_thought(thought)
            if not value:
                continue
            value = f"{prefix}{value}" if prefix else value
            self._log().node_agent_event(
                node_id,
                "AGENT_THOUGHT",
                value,
            )
        message = result.message if result.message is not None else result.output
        if prefix:
            message = f"{prefix}{message}"
        self._log().node_agent_event(
            node_id,
            "AGENT_MESSAGE",
            message,
        )

    def _agent_prompt_preview(
        self,
        agent_config: AgentConfig,
        context: dict[str, object],
        prompt_override: str | None,
        memory: list[dict[str, str]] | None = None,
    ) -> str:
        prompt_text = (
            PromptManager._interpolate(prompt_override, context)
            if prompt_override is not None
            else PromptManager().load(agent_config.prompt_path, context)
            if agent_config.prompt_path is not None
            else ""
        )
        if piped := context.get("_piped_input"):
            prompt_text = f"{piped}\n\n{prompt_text}"
        if file_content := context.get("file_content"):
            prompt_text = f"{prompt_text}\n\n{file_content}"
        if row := context.get("_row"):
            prompt_text = f"{prompt_text}\n\n{row}"
        if memory:
            prompt_text = format_agent_memory(memory, prompt_text)
        return prompt_text

    def _agent_memory(self, node_id: str, mode: str) -> list[dict[str, str]]:
        if mode == "run":
            return list(self._agent_run_memory.get(node_id, []))
        if mode == "all":
            return self._load_agent_memory(node_id)
        return []

    def _remember_agent_result(self, node_id: str, mode: str, result: AgentResult) -> None:
        if mode not in {"run", "all"}:
            return
        message = result.message if result.message is not None else result.output
        prompt = result.current_prompt or result.prompt
        if not prompt and not message:
            return
        turns = self._agent_memory(node_id, mode)
        if prompt:
            turns.append({"role": "user", "body": prompt})
        if message:
            turns.append({"role": "assistant", "body": message})
        turns = turns[-40:]
        if mode == "run":
            self._agent_run_memory[node_id] = turns
        else:
            self._save_agent_memory(node_id, turns)

    def _agent_memory_key(
        self,
        node_id: str,
        loop_item: dict[str, object] | None = None,
    ) -> str:
        if not loop_item:
            return node_id
        for key in ("item_id", "id", "path", "file_path", "index"):
            value = loop_item.get(key)
            if value not in (None, ""):
                return f"{node_id}--{_safe_path_part(str(value))}"
        digest = hashlib.sha256(
            json.dumps(loop_item, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        return f"{node_id}--{digest}"

    def _agent_memory_path(self, node_id: str) -> Path:
        base = self._log_base_dir.parent if self._log_base_dir is not None else get_data_dir()
        workflow_id = _safe_path_part(self._workflow.config.id)
        safe_node_id = _safe_path_part(node_id)
        return base / "agent-memory" / workflow_id / f"{safe_node_id}.json"

    def _load_agent_memory(self, node_id: str) -> list[dict[str, str]]:
        path = self._agent_memory_path(node_id)
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(loaded, list):
            return []
        turns = []
        for item in loaded:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            body = str(item.get("body", "")).strip()
            if role and body:
                turns.append({"role": role, "body": body})
        return turns

    def _save_agent_memory(self, node_id: str, turns: list[dict[str, str]]) -> None:
        path = self._agent_memory_path(node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(turns, indent=2), encoding="utf-8")

    async def _compact_agent_memory_if_needed(
        self,
        node_id: str,
        op: AgentOperation | CommonLlmTaskOperation,
        mode: str,
        turns: list[dict[str, str]],
        agent_config: AgentConfig,
        subscription: Subscription,
    ) -> list[dict[str, str]]:
        if mode not in {"run", "all"}:
            return turns
        if _turns_size(turns) <= AGENT_MEMORY_COMPACT_CHAR_LIMIT:
            return turns

        self._log().info(f"Compacting agent context for agent node {node_id}")
        recent = turns[-AGENT_MEMORY_RECENT_TURNS:]
        older = turns[:-AGENT_MEMORY_RECENT_TURNS]
        compaction_prompt = _agent_memory_compaction_prompt(older)
        try:
            provider_settings = self._provider_settings_for_operation(agent_config, op)
            profile_timeout = provider_settings.timeout
            effective_timeout = (
                min(180.0, profile_timeout) if profile_timeout is not None else 180.0
            )
            effective_env = {**resolved_provider_env(provider_settings), **agent_config.env}
            if _accepts_execute_kwarg(subscription, "provider_settings"):
                result = await subscription.execute(
                    prompt=compaction_prompt,
                    working_dir=agent_config.working_dir,
                    tools=agent_config.tools,
                    mcp_servers=agent_config.mcp_servers,
                    env=effective_env,
                    timeout=effective_timeout,
                    cancel_event=self._cancel_event if self._pass_cancel_event else None,
                    extra_paths=configured_extra_paths(agent_config),
                    max_output_bytes=self._limits.max_subprocess_output_bytes,
                    provider_settings=provider_settings,
                )
            else:
                result = await subscription.execute(
                    prompt=compaction_prompt,
                    working_dir=agent_config.working_dir,
                    tools=agent_config.tools,
                    mcp_servers=agent_config.mcp_servers,
                    env=effective_env,
                    timeout=effective_timeout,
                    cancel_event=self._cancel_event if self._pass_cancel_event else None,
                    extra_paths=configured_extra_paths(agent_config),
                    max_output_bytes=self._limits.max_subprocess_output_bytes,
                )
            summary = (
                (result.message or result.output).strip()
                if result.success
                else _fallback_turn_summary(older)
            )
            if not summary:
                summary = _fallback_turn_summary(older)
        except Exception:  # noqa: BLE001
            summary = _fallback_turn_summary(older)

        compacted_turns = [
            {
                "role": "system",
                "body": f"Compacted prior agent node context:\n{summary}",
            },
            *recent,
        ]
        if mode == "run":
            self._agent_run_memory[node_id] = compacted_turns
        else:
            self._save_agent_memory(node_id, compacted_turns)
        return compacted_turns

    def _agent_config_for_operation(
        self,
        agent_config: AgentConfig,
        prompt_path: Path | None,
        working_dir: Path | None,
    ) -> AgentConfig:
        updates: dict[str, Path | list[Path]] = {}
        extra_paths = [
            *agent_config.extra_paths,
            *[
                entry.path
                for entry in self._workflow.config.filesystem_access
                if entry.read and entry.write
            ],
        ]
        if extra_paths:
            updates["extra_paths"] = self._provider_extra_paths(extra_paths)
        if prompt_path is not None:
            updates["prompt_path"] = _resolve_workflow_path(prompt_path, self._path_base)
        if working_dir is not None:
            updates["working_dir"] = _resolve_workflow_path(working_dir, self._path_base)
        return agent_config.model_copy(update=updates) if updates else agent_config

    def _provider_extra_paths(self, paths: list[Path]) -> list[Path]:
        resolved_paths: list[Path] = []
        seen: set[Path] = set()
        for configured_path in paths:
            resolved = _resolve_workflow_path(configured_path, self._path_base)
            provider_path = resolved
            if resolved.exists() and resolved.is_file():
                provider_path = resolved.parent
            elif not resolved.exists() and resolved.suffix:
                provider_path = resolved.parent
            provider_path = provider_path.resolve()
            if provider_path in seen:
                continue
            seen.add(provider_path)
            resolved_paths.append(provider_path)
        return resolved_paths

    def _path_has_workflow_access(
        self,
        path: Path,
        permission: Literal["read", "write", "execute"],
    ) -> bool:
        if self._path_base is None:
            return True
        resolved_path = _resolved_for_access(path)
        trusted_root = _resolved_for_access(self._path_base)
        if resolved_path == trusted_root or trusted_root in resolved_path.parents:
            root_entry = self._project_root_access_entry(trusted_root)
            return getattr(root_entry, permission) if root_entry is not None else True
        for entry in self._workflow.config.filesystem_access:
            if not getattr(entry, permission):
                continue
            if self._access_entry_covers_path(entry, resolved_path):
                return True
        return False

    def _access_entry_covers_path(
        self,
        entry: FilesystemAccessEntry,
        resolved_path: Path,
    ) -> bool:
        entry_path = _resolve_workflow_path(entry.path, self._path_base)
        resolved_entry = _resolved_for_access(entry_path)
        return resolved_path == resolved_entry or resolved_entry in resolved_path.parents

    def _project_root_access_entry(
        self,
        trusted_root: Path,
    ) -> FilesystemAccessEntry | None:
        for entry in self._workflow.config.filesystem_access:
            entry_path = _resolve_workflow_path(entry.path, self._path_base)
            if _resolved_for_access(entry_path) == trusted_root:
                return entry
        return None

    def _require_workflow_path_access(
        self,
        path: Path,
        permission: Literal["read", "write", "execute"],
    ) -> None:
        if self._path_has_workflow_access(path, permission):
            return
        raise PermissionError(
            f"Workflow filesystem access denied for {permission} on {path}. "
            "Add the path to workflow filesystem access or move it into the "
            "trusted project folder."
        )

    def _provider_settings_for_operation(
        self,
        agent_config: AgentConfig,
        op: AgentOperation | CommonLlmTaskOperation,
    ) -> ResolvedProviderSettings:
        settings = resolve_provider_settings(
            agent_subscription=agent_config.subscription,
            profile_name=agent_config.profile,
            agent_model=agent_config.model,
            agent_effort=agent_config.effort,
            operation_profile=op.profile,
            operation_model=op.model,
            operation_effort=op.effort,
            operation_timeout=op.timeout,
            data_dir=self._data_dir,
        )
        validate_provider_settings(settings)
        return settings

    def _trigger_secret_values(self, ctx: ExecutionContext, graph: WorkflowGraph) -> set[str]:
        secret_values = _trigger_secret_values_for_logs(ctx.trigger)
        for name, declaration in self._parameter_config().declared_inputs.items():
            if declaration.is_secret and name in ctx.inputs:
                secret_values.update(_collect_leaf_strings(ctx.inputs[name]))
        for name, variable_declaration in self._workflow.config.variables.items():
            if variable_declaration.secret and name in ctx.variables:
                secret_values.update(_collect_leaf_strings(ctx.variables[name]))
        for node in graph.nodes_in_order():
            op = node.operation
            if op.type != OperationType.HTTP_REQUEST:
                continue
            assert isinstance(op, HttpRequestOperation)
            configured_secret_fields = {field.lower() for field in op.secret_fields}
            try:
                template_context = self._template_context(node, ctx, graph)
                rendered_url = str(
                    self._render_http_value(
                        op.url, template_context, "nodes[type=http_request].url"
                    )
                )
                rendered_headers = cast(
                    dict[str, object],
                    self._render_http_value(
                        op.headers, template_context, "nodes[type=http_request].headers.*"
                    ),
                )
                rendered_params = cast(
                    dict[str, object],
                    self._render_http_value(
                        op.params, template_context, "nodes[type=http_request].params.*"
                    ),
                )
                rendered_json = self._render_http_value(
                    op.json_payload, template_context, "nodes[type=http_request].json.*"
                )
                rendered_body = self._render_http_value(
                    op.body, template_context, "nodes[type=http_request].body"
                )
            except Exception:  # noqa: BLE001
                continue

            if _is_sensitive_field("url", configured_secret_fields):
                secret_values.update(_collect_leaf_strings(rendered_url))
            secret_values.update(
                _collect_sensitive_template_values(
                    op.url,
                    configured_secret_fields,
                    template_context,
                    "url",
                )
            )
            secret_values.update(
                _collect_sensitive_template_values(
                    op.headers,
                    configured_secret_fields,
                    template_context,
                )
            )
            secret_values.update(
                _collect_sensitive_template_values(
                    op.params,
                    configured_secret_fields,
                    template_context,
                )
            )
            secret_values.update(
                _collect_sensitive_template_values(
                    op.json_payload,
                    configured_secret_fields,
                    template_context,
                )
            )
            secret_values.update(
                _collect_sensitive_template_values(
                    op.body,
                    configured_secret_fields,
                    template_context,
                    "body",
                )
            )
            secret_values.update(
                _collect_configured_secret_values(
                    rendered_headers,
                    configured_secret_fields,
                )
            )
            secret_values.update(
                _collect_configured_secret_values(
                    rendered_params,
                    configured_secret_fields,
                )
            )
            secret_values.update(
                _collect_configured_secret_values(
                    rendered_json,
                    configured_secret_fields,
                )
            )
            if isinstance(rendered_body, str):
                secret_values.update(
                    _collect_configured_secret_text_values(
                        rendered_body,
                        configured_secret_fields,
                    )
                )
            else:
                secret_values.update(
                    _collect_configured_secret_values(
                        rendered_body,
                        configured_secret_fields,
                    )
                )
        return {value for value in secret_values if value}

    def _public_node_output(
        self,
        output: NodeOutput,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
    ) -> NodeOutput:
        secret_values = self._trigger_secret_values(ctx, graph)
        node = graph._nodes.get(output.node_id)
        if node is not None and isinstance(node.operation, HttpRequestOperation):
            configured = {field.lower() for field in node.operation.secret_fields}
            secret_values.update(_collect_configured_secret_values(output.data, configured))
            secret_values.update(_collect_configured_secret_text_values(output.output, configured))
        return _mask_node_output(output, secret_values)

    def _public_scope_outputs(
        self,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
    ) -> tuple[dict[str, NodeOutput], dict[str, list[NodeOutput]]]:
        return (
            {
                node_id: self._public_node_output(output, ctx, graph)
                for node_id, output in ctx.node_outputs.items()
            },
            {
                node_id: [self._public_node_output(output, ctx, graph) for output in runs]
                for node_id, runs in ctx.node_runs.items()
            },
        )

    def _approval_checkpoint_path(self, run_id: str, node_id: str) -> Path:
        return self._approval_store.request_path(
            self._workflow.config.id,
            run_id,
            node_id,
        ).with_suffix(".checkpoint.json")

    @staticmethod
    def _artifact_root(snapshot_path: Path) -> Path:
        return snapshot_path.parent / f"{snapshot_path.stem}.artifacts"

    @staticmethod
    def _safe_artifact_name(value: object) -> str:
        text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-._")
        if not text:
            text = "artifact"
        if len(text) > 80:
            digest = hashlib.sha256(str(value).encode()).hexdigest()[:12]
            text = f"{text[:64]}-{digest}"
        return text

    def _write_snapshot_artifact(
        self,
        snapshot_path: Path,
        category: str,
        name: object,
        payload: object,
    ) -> dict[str, str]:
        root = self._artifact_root(snapshot_path)
        safe_category = self._safe_artifact_name(category)
        safe_name = self._safe_artifact_name(name)
        artifact_path = root / safe_category / f"{safe_name}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        return {self._SNAPSHOT_ARTIFACT_KEY: str(artifact_path.relative_to(root))}

    def _snapshot_ref(
        self,
        snapshot_path: Path,
        category: str,
        name: object,
        payload: object,
    ) -> dict[str, str]:
        return self._write_snapshot_artifact(snapshot_path, category, name, payload)

    @staticmethod
    def _snapshot_node_output_contract(output: NodeOutput) -> dict[str, object]:
        contract = output.contract()
        if output.type not in {
            str(OperationType.AGENT),
            str(OperationType.COMMON_LLM_TASK),
        }:
            return contract
        data = contract.get("data")
        if not isinstance(data, dict):
            return contract
        compact_data: dict[str, object] = {}
        for key in (
            "message",
            "agent_id",
            "usage",
            "cacheKey",
            "reused",
        ):
            if key in data:
                compact_data[key] = data[key]
        omitted: list[str] = []
        for key in ("prompt", "thoughts", "inputs"):
            if key in data:
                omitted.append(key)
        if omitted:
            compact_data["omittedFromCheckpoint"] = omitted
        return {**contract, "data": compact_data}

    def _snapshot_task_entry(self, entry: dict[str, object]) -> dict[str, object]:
        compacted = dict(entry)
        output = entry.get("output")
        if isinstance(output, dict):
            compacted["output"] = self._compact_checkpoint_contract(output)
            output_type = str(output.get("type") or "")
            if output_type in {
                str(OperationType.AGENT),
                str(OperationType.COMMON_LLM_TASK),
            }:
                compacted.pop("inputs", None)
                compacted["inputsOmittedFromCheckpoint"] = True
        return compacted

    @staticmethod
    def _compact_checkpoint_contract(contract: dict[str, object]) -> dict[str, object]:
        output_type = str(contract.get("type") or "")
        if output_type not in {
            str(OperationType.AGENT),
            str(OperationType.COMMON_LLM_TASK),
        }:
            return contract
        data = contract.get("data")
        if not isinstance(data, dict):
            return contract
        compact_data: dict[str, object] = {}
        for key in (
            "message",
            "agent_id",
            "usage",
            "cacheKey",
            "reused",
        ):
            if key in data:
                compact_data[key] = data[key]
        omitted = [key for key in ("prompt", "thoughts", "inputs") if key in data]
        if omitted:
            compact_data["omittedFromCheckpoint"] = omitted
        return {**contract, "data": compact_data}

    def _hydrate_snapshot_artifacts(self, snapshot_path: Path, payload: object) -> object:
        if isinstance(payload, dict):
            artifact_ref = payload.get(self._SNAPSHOT_ARTIFACT_KEY)
            if isinstance(artifact_ref, str):
                root = self._artifact_root(snapshot_path)
                artifact_path = (root / artifact_ref).resolve()
                try:
                    artifact_path.relative_to(root.resolve())
                    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    return {}
                return self._hydrate_snapshot_artifacts(snapshot_path, artifact_payload)
            return {
                str(key): self._hydrate_snapshot_artifacts(snapshot_path, value)
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self._hydrate_snapshot_artifacts(snapshot_path, item) for item in payload]
        return payload

    def _write_approval_checkpoint(
        self,
        path: Path,
        *,
        node_id: str,
        ctx: ExecutionContext,
        trigger_context: dict[str, object],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        node_outputs = {
            output_id: self._snapshot_ref(
                path,
                "approval-node-outputs",
                output_id,
                self._public_node_output(output, ctx, self._workflow.graph).contract(),
            )
            for output_id, output in ctx.node_outputs.items()
        }
        path.write_text(
            json.dumps(
                {
                    "workflowId": self._workflow.config.id,
                    "nodeId": node_id,
                    "trigger": trigger_context,
                    "params": self._masked_parameters(ctx.inputs),
                    "inputs": self._masked_parameters(ctx.inputs),
                    "variables": self._masked_variables(ctx.variables),
                    "clearedVariables": sorted(
                        set(self._workflow.config.variables) - set(ctx.variables)
                    ),
                    "sensitiveScope": self._seal_snapshot_secrets(
                        path, self._sensitive_snapshot_values(ctx)
                    ),
                    "nodeOutputs": node_outputs,
                },
                default=str,
            ),
            encoding="utf-8",
        )

    def _refresh_pending_approval_checkpoints(self, ctx: ExecutionContext) -> None:
        if self._run_log is None:
            return
        run_id = self._run_log.path.name
        for request in self._approval_store.list_pending(self._workflow.config.id):
            if request.run_id != run_id or not request.checkpoint_path:
                continue
            self._write_approval_checkpoint(
                Path(request.checkpoint_path),
                node_id=request.node_id,
                ctx=ctx,
                trigger_context=self._trigger_context,
            )

    def _event_preview(self, value: object, limit: int = 4000) -> object:
        if isinstance(value, str):
            return truncate_text_bytes(value, limit, "run event")
        if isinstance(value, dict):
            return {str(key): self._event_preview(item, limit) for key, item in value.items()}
        if isinstance(value, list):
            return [self._event_preview(item, limit) for item in value[:20]]
        return value

    def _node_event_data(
        self,
        output: NodeOutput,
        *,
        edge_decisions: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        output_data = self._event_preview(output.data)
        data: dict[str, object] = {
            "output": self._event_preview(output.output),
            "data": output_data,
            "items": self._event_preview(output.items),
            "error": self._event_preview(output.error or ""),
        }
        if isinstance(output_data, dict):
            for key, value in output_data.items():
                data.setdefault(str(key), value)
        if edge_decisions is not None:
            data["edgeDecisions"] = edge_decisions
        if output.loop_items is not None:
            item_summaries = [
                {
                    "index": index,
                    "item": self._event_preview(item),
                    "status": "queued",
                    "success": None,
                    "nodeId": "",
                    "durationSeconds": 0,
                    "exitCode": None,
                    "output": "",
                    "error": "",
                }
                for index, item in enumerate(output.loop_items)
            ]
            data["fanOut"] = {
                "itemCount": len(output.loop_items),
                "successCount": 0,
                "failureCount": 0,
                "runningCount": 0,
                "items": item_summaries,
                "maxConcurrency": output.loop_max_concurrency,
                "failFast": output.loop_fail_fast,
            }
        return data

    def _skipped_node_event_data(
        self,
        node_id: str,
        graph: WorkflowGraph,
        ctx: ExecutionContext,
    ) -> tuple[str, dict[str, object]]:
        incoming_decisions: list[dict[str, object]] = []
        for upstream_id in graph._graph.predecessors(node_id):
            edge = graph.get_edge_config(upstream_id, node_id)
            output = ctx.node_outputs.get(upstream_id)
            if output is None:
                incoming_decisions.append(
                    {
                        "from": upstream_id,
                        "to": node_id,
                        "condition": str(edge.condition),
                        "outputPattern": edge.output_pattern or "",
                        "matched": False,
                        "reason": f"{upstream_id} did not run",
                    }
                )
                continue
            matched = self._edge_matches(edge, output, graph)
            incoming_decisions.append(
                {
                    "from": upstream_id,
                    "to": node_id,
                    "condition": str(edge.condition),
                    "outputPattern": edge.output_pattern or "",
                    "matched": matched,
                    "reason": (
                        f"{upstream_id} -> {node_id} matched ({edge.condition})"
                        if matched
                        else f"{upstream_id} -> {node_id} skipped ({edge.condition})"
                    ),
                }
            )

        skipped_edges = [
            decision for decision in incoming_decisions if not bool(decision.get("matched"))
        ]
        if skipped_edges:
            message = str(skipped_edges[0]["reason"])
        elif incoming_decisions:
            message = "node was not reached after incoming conditions matched"
        else:
            message = "node was not reached"
        return message, {
            "skipReason": message,
            "incomingEdgeDecisions": incoming_decisions,
        }

    def _record_unrun_node_events(
        self,
        graph: WorkflowGraph,
        ctx: ExecutionContext,
        run_log: WorkflowRunLog,
        *,
        stopped: bool,
    ) -> None:
        for node_id in graph._nodes:
            if node_id in ctx.node_runs:
                continue
            message, data = self._skipped_node_event_data(node_id, graph, ctx)
            if stopped and not self._has_completed_false_incoming_edge(data):
                message = "stopped by user before node started"
                run_log.node(node_id, "stopped")
                run_log.event(
                    node_id,
                    "stopped",
                    message=message,
                    success=False,
                    data={
                        "stopReason": message,
                        "incomingEdgeDecisions": data.get("incomingEdgeDecisions", []),
                    },
                )
                continue
            run_log.node(node_id, "skipped")
            run_log.event(
                node_id,
                "skipped",
                message=message,
                skipped=True,
                data=data,
            )

    @staticmethod
    def _has_completed_false_incoming_edge(data: dict[str, object]) -> bool:
        decisions = data.get("incomingEdgeDecisions")
        if not isinstance(decisions, list):
            return False
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            if bool(decision.get("matched")):
                continue
            reason = decision.get("reason")
            if isinstance(reason, str) and reason.endswith(" did not run"):
                continue
            return True
        return False

    def _record_loop_item_output(
        self,
        task: QueuedNode,
        output: NodeOutput,
        loop_item_outputs: dict[str, dict[int, list[NodeOutput]]],
        completed_loop_items: set[tuple[str, int]],
        *,
        terminal: bool,
    ) -> None:
        if task.loop_origin is None or task.loop_index is None or task.loop_items is None:
            return
        origin_outputs = loop_item_outputs.setdefault(task.loop_origin, {})
        item_outputs = origin_outputs.setdefault(task.loop_index, [])
        item_outputs.append(output)
        if terminal:
            completed_loop_items.add((task.loop_origin, task.loop_index))
        self._refresh_loop_fan_out_summary(
            task.loop_origin,
            task.loop_items,
            origin_outputs,
            completed_loop_items,
        )

    def _refresh_loop_fan_out_summary(
        self,
        loop_origin: str,
        loop_items: list[dict[str, object]],
        item_outputs: dict[int, list[NodeOutput]],
        completed_loop_items: set[tuple[str, int]],
    ) -> None:
        run_log = self._run_log
        if run_log is None:
            return
        item_summaries: list[dict[str, object]] = []
        success_count = 0
        failure_count = 0
        for index, item in enumerate(loop_items):
            outputs = item_outputs.get(index, [])
            is_complete = (loop_origin, index) in completed_loop_items
            item_success = bool(outputs) and all(output.success for output in outputs)
            if is_complete and item_success:
                success_count += 1
            elif is_complete:
                failure_count += 1
            last_output = outputs[-1] if outputs else None
            first_error = next(
                (output.error or output.output for output in outputs if not output.success),
                "",
            )
            item_summaries.append(
                {
                    "index": index,
                    "item": self._event_preview(item),
                    "status": (
                        "completed"
                        if is_complete and item_success
                        else "failed"
                        if is_complete
                        else "running"
                        if outputs
                        else "queued"
                    ),
                    "success": item_success if is_complete else None,
                    "nodeId": last_output.node_id if last_output else "",
                    "durationSeconds": sum(output.duration_seconds for output in outputs),
                    "exitCode": last_output.exit_code if last_output else None,
                    "output": self._event_preview(last_output.output if last_output else ""),
                    "error": self._event_preview(first_error),
                }
            )
        run_log.update_node_data(
            loop_origin,
            {
                "fanOut": {
                    "itemCount": len(loop_items),
                    "successCount": success_count,
                    "failureCount": failure_count,
                    "runningCount": sum(
                        1
                        for index in item_outputs
                        if (loop_origin, index) not in completed_loop_items
                    ),
                    "items": item_summaries,
                }
            },
        )

    @staticmethod
    def _node_output_from_contract(node_id: str, data: object) -> NodeOutput:
        if not isinstance(data, dict):
            return NodeOutput(
                node_id=node_id,
                success=True,
                output="",
                exit_code=0,
                duration_seconds=0,
            )
        output_data = data.get("data")
        output_items = data.get("items")
        loop_items = data.get("loop_items")
        return NodeOutput(
            node_id=str(data.get("node_id") or node_id),
            success=bool(data.get("success", True)),
            output=str(data.get("output") or data.get("text") or ""),
            exit_code=int(data.get("exit_code") or 0),
            duration_seconds=float(data.get("duration_seconds") or 0),
            skipped=bool(data.get("skipped", False)),
            terminal_status=(
                str(data.get("terminal_status")) if data.get("terminal_status") else None
            ),
            loop_items=loop_items if isinstance(loop_items, list) else None,
            loop_infinite=bool(data.get("loop_infinite", False)),
            loop_max_concurrency=int(data.get("loop_max_concurrency") or 1),
            loop_fail_fast=bool(data.get("loop_fail_fast", False)),
            type=str(data.get("type") or ""),
            text=str(data.get("text") or data.get("output") or ""),
            value=data.get("value"),
            data=output_data if isinstance(output_data, dict) else {},
            items=output_items if isinstance(output_items, list) else [],
            error=str(data.get("error")) if data.get("error") is not None else None,
        )

    def _run_resume_path(self) -> Path | None:
        if self._run_log is None:
            return None
        return self._run_log.path.with_suffix(".resume.json")

    def _snapshot_secret_key_path(self, snapshot_path: Path) -> Path:
        base = self._data_dir or (
            self._log_base_dir.parent if self._log_base_dir is not None else snapshot_path.parent
        )
        return base / ".checkpoint-secrets.key"

    def _snapshot_secret_key(self, snapshot_path: Path, *, create: bool) -> bytes | None:
        key_path = self._snapshot_secret_key_path(snapshot_path)
        try:
            key = key_path.read_bytes()
        except OSError:
            if not create:
                return None
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = stdlib_secrets.token_bytes(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            try:
                descriptor = os.open(key_path, flags, 0o600)
            except FileExistsError:
                return key_path.read_bytes()
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
        if len(key) != 32:
            raise ValueError(f"Invalid checkpoint secret key: {key_path}")
        return key

    def _seal_snapshot_secrets(
        self,
        snapshot_path: Path,
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        if not any(payload.values()):
            return None
        key = self._snapshot_secret_key(snapshot_path, create=True)
        assert key is not None
        nonce = stdlib_secrets.token_bytes(16)
        plaintext = json.dumps(payload, default=str, separators=(",", ":")).encode()
        stream = bytearray()
        counter = 0
        while len(stream) < len(plaintext):
            stream.extend(
                hmac.digest(key, b"gofer-checkpoint" + nonce + counter.to_bytes(8, "big"), "sha256")
            )
            counter += 1
        ciphertext = bytes(
            left ^ right for left, right in zip(plaintext, stream[: len(plaintext)], strict=True)
        )
        tag = hmac.digest(key, b"gofer-checkpoint-tag" + nonce + ciphertext, "sha256")
        return {
            "version": self._SENSITIVE_SNAPSHOT_VERSION,
            "nonce": base64.urlsafe_b64encode(nonce).decode(),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode(),
            "tag": base64.urlsafe_b64encode(tag).decode(),
        }

    def _open_snapshot_secrets(
        self,
        snapshot_path: Path,
        sealed: object,
    ) -> dict[str, object]:
        if not isinstance(sealed, dict):
            return {}
        key = self._snapshot_secret_key(snapshot_path, create=False)
        if key is None:
            raise CheckpointSecretKeyUnavailable(
                "Checkpoint contains encrypted workflow inputs or variables, but the local "
                "checkpoint key is unavailable. Supply the secret inputs again."
            )
        try:
            nonce = base64.urlsafe_b64decode(str(sealed["nonce"]))
            ciphertext = base64.urlsafe_b64decode(str(sealed["ciphertext"]))
            tag = base64.urlsafe_b64decode(str(sealed["tag"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("Checkpoint secret payload is invalid") from exc
        expected = hmac.digest(key, b"gofer-checkpoint-tag" + nonce + ciphertext, "sha256")
        if not hmac.compare_digest(tag, expected):
            raise ValueError("Checkpoint secret payload could not be authenticated")
        stream = bytearray()
        counter = 0
        while len(stream) < len(ciphertext):
            stream.extend(
                hmac.digest(key, b"gofer-checkpoint" + nonce + counter.to_bytes(8, "big"), "sha256")
            )
            counter += 1
        plaintext = bytes(
            left ^ right for left, right in zip(ciphertext, stream[: len(ciphertext)], strict=True)
        )
        try:
            payload = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Checkpoint secret payload is invalid") from exc
        return cast(dict[str, object], payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _contains_redacted_snapshot_value(value: object) -> bool:
        if value == "***":
            return True
        if isinstance(value, dict):
            return any(
                WorkflowExecutor._contains_redacted_snapshot_value(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(WorkflowExecutor._contains_redacted_snapshot_value(item) for item in value)
        return False

    def _resume_sensitive_scope(
        self,
        snapshot_path: Path | None,
        payload: dict[str, object],
        saved_inputs: object,
    ) -> tuple[dict[str, object], bool]:
        sealed = payload.get("sensitiveScope")
        if snapshot_path is None or sealed is None:
            return {}, False
        try:
            return self._open_snapshot_secrets(snapshot_path, sealed), False
        except CheckpointSecretKeyUnavailable as exc:
            redacted_inputs = {
                str(name)
                for name, value in (saved_inputs.items() if isinstance(saved_inputs, dict) else ())
                if value == "***"
            }
            missing = sorted(redacted_inputs - self._run_parameters.keys())
            if missing:
                names = ", ".join(missing)
                raise CheckpointSecretKeyUnavailable(
                    f"{exc} Missing replacement inputs: {names}."
                ) from exc
            return {}, True

    def _sensitive_snapshot_values(self, ctx: ExecutionContext) -> dict[str, object]:
        graph = self._workflow.graph
        sensitive_outputs = {
            node_id: output.contract()
            for node_id, output in ctx.node_outputs.items()
            if self._public_node_output(output, ctx, graph).contract() != output.contract()
        }
        sensitive_runs = {
            node_id: [output.contract() for output in runs]
            for node_id, runs in ctx.node_runs.items()
            if any(
                self._public_node_output(output, ctx, graph).contract() != output.contract()
                for output in runs
            )
        }
        return {
            "inputs": {
                name: ctx.inputs[name]
                for name, declaration in self._parameter_config().declared_inputs.items()
                if declaration.is_secret and name in ctx.inputs
            },
            "variables": {
                name: ctx.variables[name]
                for name, declaration in self._workflow.config.variables.items()
                if declaration.secret and name in ctx.variables
            },
            "nodeOutputs": sensitive_outputs,
            "nodeRuns": sensitive_runs,
        }

    def _cleanup_resume_checkpoints_for_fresh_run(self) -> None:
        if self._run_log is None:
            return
        if self._resume_options is not None or self._run_log_path is not None:
            return
        log_dir = self._run_log.path.parent
        current_resume = self._run_log.path.with_suffix(".resume.json")
        current_artifacts = self._artifact_root(current_resume)
        for resume_path in log_dir.glob("*.resume.json"):
            if resume_path == current_resume:
                continue
            resume_path.unlink(missing_ok=True)
            shutil.rmtree(self._artifact_root(resume_path), ignore_errors=True)
        for artifact_root in log_dir.glob("*.resume.artifacts"):
            if artifact_root == current_artifacts:
                continue
            shutil.rmtree(artifact_root, ignore_errors=True)

    def _cache_root(self) -> Path | None:
        base = self._data_dir or (self._log_base_dir.parent if self._log_base_dir else None)
        if base is None:
            return None
        return base / "node-cache" / self._workflow.config.id

    def _global_cache_path(self) -> Path | None:
        root = self._cache_root()
        if root is None:
            return None
        return root / "cache.json"

    def _run_checkpoint_path(self, run_id: str) -> Path | None:
        base = self._log_base_dir or (self._data_dir / "logs" if self._data_dir else None)
        if base is None:
            return None
        safe_run_id = run_id.replace("/", "").replace("\\", "")
        return base / self._workflow.config.id / safe_run_id

    @staticmethod
    def _task_cache_key(node_id: str, loop_item: dict[str, object] | None) -> str:
        if loop_item is None:
            return node_id
        index = loop_item.get("index")
        if index is not None:
            return f"{node_id}::{index}"
        item_hash = hashlib.sha256(
            json.dumps(loop_item, sort_keys=True, default=str).encode()
        ).hexdigest()
        return f"{node_id}::{item_hash}"

    def _read_resume_payload(self, run_id: str) -> dict[str, object]:
        log_path = self._run_checkpoint_path(run_id)
        if log_path is None:
            return {}
        candidates = [
            log_path.with_suffix(".resume.json"),
            log_path.with_suffix(".outputs.json"),
        ]
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return cast(dict[str, object], self._hydrate_snapshot_artifacts(candidate, payload))
        return {}

    def _read_global_cache(self) -> dict[str, dict[str, object]]:
        path = self._global_cache_path()
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        payload = self._hydrate_snapshot_artifacts(path, payload)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _write_global_cache_entry(self, entry: dict[str, object]) -> None:
        path = self._global_cache_path()
        if path is None:
            return
        entries = self._read_global_cache()
        key = str(entry.get("cacheKey") or "")
        if not key:
            return
        entries[key] = entry
        stored_entries = {
            entry_key: self._snapshot_ref(path, "entries", entry_key, entry_value)
            for entry_key, entry_value in entries.items()
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "workflowId": self._workflow.config.id,
                    "updatedAt": datetime.now().astimezone().isoformat(),
                    "entries": stored_entries,
                },
                default=str,
            ),
            encoding="utf-8",
        )

    def _write_resume_checkpoint(self, ctx: ExecutionContext) -> None:
        path = self._run_resume_path()
        if path is None:
            return
        shutil.rmtree(self._artifact_root(path), ignore_errors=True)
        node_outputs = {
            node_id: self._snapshot_ref(
                path,
                "node-outputs",
                node_id,
                self._snapshot_node_output_contract(
                    self._public_node_output(output, ctx, self._workflow.graph)
                ),
            )
            for node_id, output in ctx.node_outputs.items()
        }
        node_runs = {
            node_id: [
                self._snapshot_ref(
                    path,
                    "node-runs",
                    f"{node_id}-{index}",
                    self._snapshot_node_output_contract(
                        self._public_node_output(output, ctx, self._workflow.graph)
                    ),
                )
                for index, output in enumerate(runs)
            ]
            for node_id, runs in ctx.node_runs.items()
        }
        secret_values = self._trigger_secret_values(ctx, self._workflow.graph)
        task_entries = {
            key: self._snapshot_ref(
                path,
                "task-entries",
                key,
                cast(
                    dict[str, object],
                    _mask_known_secret_values(self._snapshot_task_entry(entry), secret_values),
                ),
            )
            for key, entry in self._resume_task_entries.items()
        }
        path.write_text(
            json.dumps(
                {
                    "workflowId": self._workflow.config.id,
                    "runId": self._log().path.name,
                    "trigger": ctx.trigger,
                    "params": self._masked_parameters(ctx.inputs),
                    "inputs": self._masked_parameters(ctx.inputs),
                    "variables": self._masked_variables(ctx.variables),
                    "clearedVariables": sorted(
                        set(self._workflow.config.variables) - set(ctx.variables)
                    ),
                    "sensitiveScope": self._seal_snapshot_secrets(
                        path, self._sensitive_snapshot_values(ctx)
                    ),
                    "nodeOutputs": node_outputs,
                    "nodeRuns": node_runs,
                    "taskEntries": task_entries,
                    "updatedAt": datetime.now().astimezone().isoformat(),
                },
                default=str,
            ),
            encoding="utf-8",
        )

    def _operation_fingerprint(self, node: GraphNode) -> dict[str, object]:
        return cast(
            dict[str, object],
            node.operation.model_dump(mode="json", exclude_none=True),
        )

    def _referenced_file_fingerprints(self, node: GraphNode) -> dict[str, object]:
        op = node.operation
        paths: list[Path] = []
        for attr in (
            "path",
            "source_path",
            "script_path",
            "template_path",
            "source_path",
            "index_path",
        ):
            value = getattr(op, attr, None)
            if isinstance(value, Path):
                paths.append(value)
        source = getattr(op, "source", None)
        source_path = getattr(source, "path", None)
        if isinstance(source_path, Path):
            paths.append(source_path)
        result: dict[str, object] = {}
        for raw_path in paths:
            path = (
                raw_path if raw_path.is_absolute() else (self._path_base or Path.cwd()) / raw_path
            )
            try:
                stat = path.stat()
            except OSError:
                result[str(raw_path)] = {"exists": False}
                continue
            result[str(raw_path)] = {
                "exists": True,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        return result

    def _cache_key_for(
        self,
        node: GraphNode,
        inputs: dict[str, object],
        loop_item: dict[str, object] | None,
        ctx: ExecutionContext,
    ) -> str:
        payload = {
            "nodeId": node.node_id,
            "operation": self._operation_fingerprint(node),
            "inputs": inputs,
            "workflowInputs": ctx.inputs,
            "workflowVariables": ctx.variables,
            "trigger": ctx.trigger,
            "loopItem": loop_item,
            "files": self._referenced_file_fingerprints(node),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _resume_signature_for(self, node: GraphNode, ctx: ExecutionContext) -> str:
        """Fingerprint stable inputs without using the checkpoint's final variable values."""
        payload = {
            "nodeId": node.node_id,
            "operation": self._operation_fingerprint(node),
            "nodeInputs": node.inputs,
            "workflowInputs": ctx.inputs,
            "trigger": ctx.trigger,
            "variableDeclarations": {
                name: declaration.model_dump(mode="json", exclude_none=True)
                for name, declaration in self._workflow.config.variables.items()
            },
            "files": self._referenced_file_fingerprints(node),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _cacheable_by_default(self, node: GraphNode) -> bool:
        return node.operation.type not in {
            OperationType.AGENT,
            OperationType.COMMON_LLM_TASK,
            OperationType.APPROVAL_GATE,
            OperationType.HTTP_REQUEST,
            OperationType.NOTIFICATION,
            OperationType.LOOP,
        }

    def _entry_output(self, entry: dict[str, object]) -> NodeOutput | None:
        output = entry.get("output")
        if not isinstance(output, dict):
            return None
        return self._node_output_from_contract(str(output.get("node_id") or ""), output)

    def _load_resume_state(self, graph: WorkflowGraph) -> ExecutionContext:
        options = self._resume_options
        payload: dict[str, object] = {}
        snapshot_path: Path | None = None
        if options is not None and not options.force:
            payload = self._read_resume_payload(options.run_id or "")
            checkpoint_path = self._run_checkpoint_path(options.run_id or "")
            if checkpoint_path is not None:
                snapshot_path = checkpoint_path.with_suffix(".resume.json")
        saved_inputs = payload.get("inputs", payload.get("params"))
        sensitive_scope, sensitive_scope_unavailable = self._resume_sensitive_scope(
            snapshot_path, payload, saved_inputs
        )
        restored_inputs = (
            {str(name): value for name, value in saved_inputs.items() if value != "***"}
            if isinstance(saved_inputs, dict)
            else {}
        )
        sensitive_inputs = sensitive_scope.get("inputs")
        if isinstance(sensitive_inputs, dict):
            restored_inputs.update(sensitive_inputs)
        resolved_inputs = resolve_workflow_parameters(
            self._parameter_config(),
            {**restored_inputs, **self._run_parameters},
            invocation_id=self._log().path.name,
        )
        ctx = ExecutionContext(
            trigger=self._trigger_context,
            inputs=resolved_inputs,
            variables=self._initial_variables(resolved_inputs),
        )
        self._resume_task_entries = {}
        self._resume_seed_outputs = {}
        self._resume_only_dependencies = set()
        self._resume_cache_bypass_nodes = set()
        if options is None or options.force:
            return ctx

        trigger = payload.get("trigger")
        if isinstance(trigger, dict) and not self._trigger_context:
            ctx.trigger = trigger
        saved_variables = payload.get("variables")
        if isinstance(saved_variables, dict):
            ctx.variables = {
                name: coerce_workflow_variable(name, declaration, saved_variables[name])
                for name, declaration in self._workflow.config.variables.items()
                if name in saved_variables and saved_variables[name] != "***"
            } | {
                name: value
                for name, value in ctx.variables.items()
                if name not in saved_variables or saved_variables[name] == "***"
            }
        sensitive_variables = sensitive_scope.get("variables")
        if isinstance(sensitive_variables, dict):
            for name, value in sensitive_variables.items():
                declaration = self._workflow.config.variables.get(str(name))
                if declaration is not None:
                    ctx.variables[str(name)] = coerce_workflow_variable(
                        str(name), declaration, value
                    )
        cleared_variables = payload.get("clearedVariables")
        if isinstance(cleared_variables, list):
            for name in cleared_variables:
                if isinstance(name, str) and name in self._workflow.config.variables:
                    ctx.variables.pop(name, None)
        task_entries = payload.get("taskEntries")
        if isinstance(task_entries, dict):
            self._resume_task_entries = {
                str(key): cast(dict[str, object], value)
                for key, value in task_entries.items()
                if isinstance(value, dict)
            }
        if not options.skip_cache:
            for key, entry in self._read_global_cache().items():
                if isinstance(entry, dict):
                    self._resume_task_entries.setdefault(str(key), entry)

        invalidated = self._invalidated_nodes(graph, options)
        if sensitive_scope_unavailable:
            saved_outputs_for_recovery = payload.get("nodeOutputs")
            if isinstance(saved_outputs_for_recovery, dict):
                for output_id, output_data in saved_outputs_for_recovery.items():
                    if self._contains_redacted_snapshot_value(output_data):
                        node_id = str(output_id)
                        invalidated.update({node_id, *self._descendant_nodes(graph, node_id)})
        if options.from_node or options.only_node:
            self._resume_cache_bypass_nodes = set(invalidated)
        if options.only_node:
            self._resume_only_dependencies = self._ancestor_nodes(graph, options.only_node)
        saved_outputs = payload.get("nodeOutputs")
        sensitive_outputs = sensitive_scope.get("nodeOutputs")
        if isinstance(saved_outputs, dict):
            for node in (
                node for generation in graph.topological_generations() for node in generation
            ):
                node_id = node.node_id
                if node.operation.type == OperationType.LOOP:
                    invalidated.update({node_id, *self._descendant_nodes(graph, node_id)})
                    continue
                output_data = (
                    sensitive_outputs.get(node_id, saved_outputs.get(node_id))
                    if isinstance(sensitive_outputs, dict)
                    else saved_outputs.get(node_id)
                )
                if output_data is None:
                    continue
                if node_id in invalidated:
                    continue
                output = self._node_output_from_contract(node_id, output_data)
                if not output.success:
                    continue
                if options.only_node and node_id == options.only_node:
                    continue
                if not self._saved_output_matches_current_cache_key(node, ctx, graph):
                    invalidated.update({node_id, *self._descendant_nodes(graph, node_id)})
                    continue
                output.data = {**output.data, "reused": True}
                ctx.record(output)
                self._resume_seed_outputs[node_id] = output
        return ctx

    def _saved_output_matches_current_cache_key(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
    ) -> bool:
        entry = self._resume_task_entries.get(self._task_cache_key(node.node_id, None))
        if not isinstance(entry, dict):
            return True
        resume_signature = entry.get("resumeSignature")
        if not isinstance(resume_signature, str):
            # Older checkpoints did not capture a pre-execution scope. Re-run
            # rather than comparing against the checkpoint's final variables.
            return False
        return resume_signature == self._resume_signature_for(node, ctx)

    def _invalidated_nodes(self, graph: WorkflowGraph, options: ResumeOptions) -> set[str]:
        if options.only_node:
            return {options.only_node}
        if options.from_node:
            return {options.from_node, *self._descendant_nodes(graph, options.from_node)}
        return {
            entry_node
            for entry_node, entry in (
                (str(key).split("::", 1)[0], value)
                for key, value in self._resume_task_entries.items()
            )
            if not bool(entry.get("success"))
        }

    def _descendant_nodes(self, graph: WorkflowGraph, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(graph._graph.successors(node_id)) if node_id in graph._nodes else []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph._graph.successors(current))
        return seen

    def _ancestor_nodes(self, graph: WorkflowGraph, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(graph._graph.predecessors(node_id)) if node_id in graph._nodes else []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph._graph.predecessors(current))
        return seen

    def _record_reused_seed_outputs(self) -> None:
        for node_id, output in self._resume_seed_outputs.items():
            self._log().event(
                node_id,
                "reused",
                message="reused output from resumed run",
                exit_code=output.exit_code,
                success=output.success,
                skipped=True,
                data={**self._node_event_data(output), "reused": True},
            )

    def _should_run_task(self, task: QueuedNode) -> bool:
        options = self._resume_options
        if options is None or options.only_node is None:
            return True
        return task.node_id == options.only_node or task.node_id in self._resume_only_dependencies

    def _cached_output_for_task(
        self,
        node: GraphNode,
        inputs: dict[str, object],
        loop_item: dict[str, object] | None,
        ctx: ExecutionContext,
    ) -> NodeOutput | None:
        options = self._resume_options
        if options is None or options.force or options.skip_cache:
            return None
        if node.operation.type == OperationType.LOOP:
            return None
        if node.node_id in self._resume_cache_bypass_nodes:
            return None
        task_key = self._task_cache_key(node.node_id, loop_item)
        entry = self._resume_task_entries.get(task_key)
        if entry is None and not self._cacheable_by_default(node):
            return None
        cache_key = self._cache_key_for(node, inputs, loop_item, ctx)
        if entry is None:
            entry = self._read_global_cache().get(cache_key)
        if not isinstance(entry, dict):
            return None
        if not bool(entry.get("success")):
            return None
        if entry.get("cacheKey") != cache_key:
            return None
        output = self._entry_output(entry)
        if output is None:
            return None
        output.data = {**output.data, "reused": True, "cacheKey": cache_key}
        return output

    def _persist_node_result(
        self,
        task: QueuedNode,
        output: NodeOutput,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
    ) -> None:
        node = graph._nodes.get(task.node_id)
        if node is None:
            return
        public_output = self._public_node_output(output, ctx, graph)
        contains_sensitive_output = public_output.contract() != output.contract()
        cache_snapshot = self._execution_cache_snapshots.pop(task.key, None)
        if cache_snapshot is None:
            try:
                inputs = self._resolve_node_inputs(
                    node, ctx, graph, task.loop_item, task.item_context
                )
            except ValueError:
                inputs = {}
            cache_key = self._cache_key_for(node, inputs, task.loop_item, ctx)
        else:
            inputs, cache_key = cache_snapshot
        entry: dict[str, object] = {
            "nodeId": task.node_id,
            "taskKey": self._task_cache_key(task.node_id, task.loop_item),
            "loopItem": task.loop_item,
            "success": public_output.success,
            "exitCode": public_output.exit_code,
            "cacheKey": cache_key,
            "resumeSignature": self._resume_signature_for(node, ctx),
            "cacheable": self._cacheable_by_default(node) and not contains_sensitive_output,
            "inputs": inputs,
            "operation": self._operation_fingerprint(node),
            "output": self._snapshot_node_output_contract(public_output),
            "finishedAt": datetime.now().astimezone().isoformat(),
        }
        stored_entry = self._snapshot_task_entry(entry)
        self._resume_task_entries[str(entry["taskKey"])] = stored_entry
        self._write_resume_checkpoint(ctx)
        if output.success and bool(entry["cacheable"]):
            self._write_global_cache_entry(stored_entry)

    async def run(self) -> ExecutionResult:
        structured_errors = structured_workflow_errors(self._workflow)
        if structured_errors:
            raise ValueError(
                "Structured output configuration is invalid: " + "; ".join(structured_errors)
            )
        if not self._dry_run:
            missing_secrets = missing_workflow_secrets(
                self._workflow,
                workflow_path=self._workflow_path,
                data_dir=self._data_dir,
            )
            if missing_secrets:
                raise ValueError(
                    "Missing required secret(s): "
                    + ", ".join(missing_secrets)
                    + ". Set GOFER_SECRET_<NAME> or <NAME> before running."
                )
        if self._stop_file is not None:
            self._stop_file.unlink(missing_ok=True)
        else:
            data_dir = self._log_base_dir.parent if self._log_base_dir is not None else None
            clear_workflow_stop(self._workflow.config.id, data_dir)
        monitor = self._start_stop_monitor()
        self._run_log = WorkflowRunLog(
            self._workflow.config.id,
            self._log_base_dir,
            self._limits,
            existing_path=self._run_log_path,
        )
        self._notify_run_log_update()
        self._cleanup_resume_checkpoints_for_fresh_run()
        run_log = self._log()
        if self._stop_file is not None:
            data_dir = self._stop_file.parent.parent
            self._run_stop_file = workflow_run_stop_path(
                self._workflow.config.id,
                run_log.path.name,
                data_dir,
            )
            self._run_stop_file.unlink(missing_ok=True)
        graph = self._workflow.graph
        ctx = self._load_resume_state(graph)
        start = time.monotonic()
        halted = False
        halt_reason: str | None = None
        terminal_success: bool | None = None
        total_node_runs = 0
        run_counts: dict[str, int] = {}
        completed_loops: set[str] = set()
        queued_after_loop_edges: set[tuple[str, str]] = set()
        started_after_loop_edges: set[tuple[str, str]] = set()
        loop_next_index: dict[str, int] = {}
        loop_item_outputs: dict[str, dict[int, list[NodeOutput]]] = {}
        completed_loop_items: set[tuple[str, int]] = set()
        live_loop_started_item_keys: dict[str, set[str]] = {}
        try:
            run_log.info(f"dry_run={self._dry_run}")
            run_log.info(f"max_total_node_runs={self._max_total_node_runs}")
            if self._resume_options is not None:
                run_log.info(
                    "resume="
                    + json.dumps(
                        {
                            "run_id": self._resume_options.run_id,
                            "from_node": self._resume_options.from_node,
                            "only_node": self._resume_options.only_node,
                            "skip_cache": self._resume_options.skip_cache,
                            "force": self._resume_options.force,
                        },
                        default=str,
                    )
                )
            if ctx.trigger:
                trigger_text = json.dumps(
                    _masked_trigger_context_for_log(ctx.trigger),
                    default=str,
                )
                trigger_text = _replace_known_secrets(
                    trigger_text,
                    self._trigger_secret_values(ctx, graph),
                )
                run_log.info(f"trigger={trigger_text}")
            if ctx.params:
                run_log.info(
                    "params="
                    + json.dumps(
                        self._masked_parameters(ctx.params),
                        default=str,
                    )
                )
            if ctx.variables:
                run_log.info(
                    "vars="
                    + json.dumps(
                        self._masked_variables(ctx.variables),
                        default=str,
                    )
                )

            self._record_reused_seed_outputs()
            queue: deque[QueuedNode] = deque()
            queued_tasks: set[QueuedTaskKey] = set()
            running_tasks: set[QueuedTaskKey] = set()
            if ctx.node_outputs:
                self._queue_resume_frontier(graph, ctx, queue, queued_tasks, running_tasks)
            else:
                queue.extend(QueuedNode(node_id) for node_id in self._initial_node_ids(graph))
                queued_tasks.update(task.key for task in queue)
            for task in queue:
                run_log.event(task.node_id, "queued", message="start node queued")
            run_log.info(f"start_nodes={[task.node_id for task in queue]}")

            send, receive = anyio.create_memory_object_stream[tuple[QueuedNode, NodeOutput, bool]](
                1000
            )

            async def run_queued_node(
                task: QueuedNode,
                node: GraphNode,
                run_number: int,
            ) -> None:
                results: dict[str, NodeOutput] = {}
                halt_flag: list[bool] = [False]
                await self._run_node(
                    node,
                    ctx,
                    results,
                    halt_flag,
                    graph,
                    run_number,
                    loop_item=task.loop_item,
                    item_context=task.item_context,
                    execution_task_key=task.key,
                )
                await send.send((task, results[task.node_id], halt_flag[0]))

            async with anyio.create_task_group() as tg:
                async with receive:
                    while (queue or running_tasks) and not halted:
                        while queue and not halted:
                            if self._stop_requested():
                                halted = True
                                halt_reason = "stopped by user"
                                run_log.error(halt_reason)
                                run_log.event(
                                    "workflow",
                                    "stopped",
                                    message=halt_reason,
                                    success=False,
                                )
                                tg.cancel_scope.cancel()
                                break

                            task = queue.popleft()
                            queued_tasks.discard(task.key)
                            if not self._should_run_task(task):
                                continue
                            if (
                                task.node_id in self._resume_seed_outputs
                                and task.loop_origin is None
                                and task.after_loop_origin is None
                            ):
                                continue
                            if task.loop_origin is not None:
                                active_loop_tasks = sum(
                                    1 for key in running_tasks if key[1] == task.loop_origin
                                )
                                if active_loop_tasks >= task.loop_max_concurrency:
                                    queue.append(task)
                                    queued_tasks.add(task.key)
                                    break
                            if task.after_loop_origin is not None:
                                edge_key = (task.after_loop_origin, task.node_id)
                                if edge_key in started_after_loop_edges:
                                    continue
                                started_after_loop_edges.add(edge_key)
                            node_id = task.node_id
                            node = graph._nodes[node_id]
                            expanded_tasks = self._for_each_tasks(node, task, ctx, graph)
                            if expanded_tasks is not None:
                                if expanded_tasks:
                                    loop_next_index.setdefault(
                                        node_id,
                                        min(
                                            len(expanded_tasks),
                                            len(expanded_tasks[0].loop_items or []),
                                        ),
                                    )
                                for expanded_task in expanded_tasks:
                                    if (
                                        expanded_task.key in queued_tasks
                                        or expanded_task.key in running_tasks
                                    ):
                                        continue
                                    queue.append(expanded_task)
                                    queued_tasks.add(expanded_task.key)
                                    run_log.event(
                                        expanded_task.node_id,
                                        "queued",
                                        fan_out_item=expanded_task.loop_item,
                                        message=f"queued by for_each {node_id}",
                                        data={"forEach": node.for_each or ""},
                                    )
                                continue
                            total_node_runs += 1
                            if total_node_runs > self._max_total_node_runs:
                                halted = True
                                halt_reason = (
                                    "maximum node run limit exceeded "
                                    f"({self._max_total_node_runs}); check recursive edges"
                                )
                                run_log.error(halt_reason)
                                tg.cancel_scope.cancel()
                                break

                            run_counts[node_id] = run_counts.get(node_id, 0) + 1
                            run_number = run_counts[node_id]
                            running_tasks.add(task.key)
                            run_log.event(
                                node_id,
                                "queued",
                                run_number=run_number,
                                fan_out_item=task.loop_item,
                                message="node ready to start",
                            )
                            tg.start_soon(run_queued_node, task, node, run_number)

                        if halted or not running_tasks:
                            break

                        task, output, node_halted = await receive.receive()
                        node_id = task.node_id
                        running_tasks.discard(task.key)
                        ctx.record(output)
                        self._persist_node_result(task, output, ctx, graph)
                        self._refresh_pending_approval_checkpoints(ctx)
                        if output.terminal_status is not None:
                            self._record_loop_item_output(
                                task,
                                output,
                                loop_item_outputs,
                                completed_loop_items,
                                terminal=True,
                            )
                            if output.terminal_status == "break":
                                run_log.info(
                                    output.output.strip()
                                    or f"loop {task.loop_origin or node_id} break triggered"
                                )
                                if task.loop_origin is not None:
                                    queue = deque(
                                        queued
                                        for queued in queue
                                        if queued.loop_origin != task.loop_origin
                                    )
                                    queued_tasks = {queued.key for queued in queue}
                                    self._queue_loop_done_successors(
                                        task.loop_origin,
                                        graph,
                                        queue,
                                        queued_tasks,
                                        running_tasks,
                                        completed_loops,
                                        queued_after_loop_edges,
                                    )
                                continue
                            halted = True
                            terminal_success = output.terminal_status == "pass"
                            halt_reason = output.output.strip() or None
                            tg.cancel_scope.cancel()
                            break
                        if node_halted:
                            if task.loop_origin is None or task.loop_fail_fast:
                                self._record_loop_item_output(
                                    task,
                                    output,
                                    loop_item_outputs,
                                    completed_loop_items,
                                    terminal=True,
                                )
                                halted = True
                                if self._stop_requested():
                                    halt_reason = "stopped by user"
                                tg.cancel_scope.cancel()
                                break

                        scheduled_successors = self._queue_successor_tasks(
                            output,
                            task,
                            graph,
                            ctx,
                            queue,
                            queued_tasks,
                            running_tasks,
                            live_loop_started_item_keys,
                        )
                        self._record_loop_item_output(
                            task,
                            output,
                            loop_item_outputs,
                            completed_loop_items,
                            terminal=not scheduled_successors,
                        )
                        if output.loop_items is not None:
                            loop_next_index.setdefault(
                                output.node_id,
                                min(output.loop_max_concurrency, len(output.loop_items)),
                            )

                        if output.loop_items is not None and not scheduled_successors:
                            self._queue_loop_done_successors(
                                node_id,
                                graph,
                                queue,
                                queued_tasks,
                                running_tasks,
                                completed_loops,
                                queued_after_loop_edges,
                            )

                        if (
                            task.loop_origin
                            and (task.loop_infinite or task.loop_items is not None)
                            and not scheduled_successors
                            and not self._stop_requested()
                        ):
                            iteration_key = (task.loop_origin, task.loop_index)
                            has_iteration_work = any(
                                key[1] == iteration_key[0] and key[2] == iteration_key[1]
                                for key in queued_tasks | running_tasks
                            )
                            if not has_iteration_work:
                                next_index = loop_next_index.get(
                                    task.loop_origin,
                                    (task.loop_index or 0) + 1,
                                )
                                live_next = self._next_live_loop_item(
                                    task.loop_origin,
                                    graph,
                                    ctx,
                                    task.loop_items,
                                    live_loop_started_item_keys,
                                )
                                next_item: dict[str, object]
                                if live_next is not None:
                                    next_index, next_item = live_next
                                elif task.loop_items is not None and next_index >= len(
                                    task.loop_items
                                ):
                                    has_loop_work = any(
                                        key[1] == task.loop_origin
                                        for key in queued_tasks | running_tasks
                                    )
                                    if not has_loop_work:
                                        self._queue_loop_done_successors(
                                            task.loop_origin,
                                            graph,
                                            queue,
                                            queued_tasks,
                                            running_tasks,
                                            completed_loops,
                                            queued_after_loop_edges,
                                        )
                                    continue
                                if live_next is not None:
                                    pass
                                elif task.loop_infinite:
                                    next_item = {"index": str(next_index)}
                                else:
                                    if task.loop_items is None:
                                        continue
                                    next_item = task.loop_items[next_index]
                                    self._mark_live_loop_item_started(
                                        task.loop_origin,
                                        graph,
                                        next_item,
                                        live_loop_started_item_keys,
                                    )
                                loop_next_index[task.loop_origin] = max(
                                    loop_next_index.get(task.loop_origin, 0),
                                    next_index + 1,
                                )
                                origin_node = graph._nodes.get(task.loop_origin)
                                if origin_node is not None and origin_node.for_each:
                                    next_node_ids = [task.loop_origin]
                                else:
                                    next_node_ids = [
                                        successor_id
                                        for successor_id in graph._graph.successors(
                                            task.loop_origin
                                        )
                                        if graph.get_edge_config(
                                            task.loop_origin,
                                            successor_id,
                                        ).condition
                                        != EdgeConditionType.AFTER_LOOP
                                    ]
                                source_node_id = (
                                    self._for_each_source_node_id(origin_node, graph)
                                    if origin_node is not None and origin_node.for_each
                                    else task.loop_origin
                                )
                                for successor_id in next_node_ids:
                                    item_context = dict(task.item_context)
                                    if source_node_id is not None:
                                        item_context[source_node_id] = next_item
                                    if origin_node is not None and origin_node.for_each:
                                        item_context[origin_node.node_id] = next_item
                                    next_task = QueuedNode(
                                        successor_id,
                                        loop_origin=task.loop_origin,
                                        loop_item=next_item,
                                        item_context=item_context,
                                        loop_index=next_index,
                                        loop_items=task.loop_items,
                                        loop_infinite=task.loop_infinite,
                                        loop_max_concurrency=task.loop_max_concurrency,
                                        loop_fail_fast=task.loop_fail_fast,
                                    )
                                    if (
                                        next_task.key in queued_tasks
                                        or next_task.key in running_tasks
                                    ):
                                        continue
                                    queue.append(next_task)
                                    queued_tasks.add(next_task.key)

            self._record_unrun_node_events(
                graph,
                ctx,
                run_log,
                stopped=halt_reason == "stopped by user" or self._stop_requested(),
            )

            total = time.monotonic() - start
            success = (
                terminal_success
                if terminal_success is not None
                else not halted
                and all(
                    o.success
                    or graph._nodes[o.node_id].allow_failure
                    or (
                        o.type == str(OperationType.APPROVAL_GATE)
                        and self._has_failure_route(graph._nodes[o.node_id], o, graph)
                    )
                    for runs in ctx.node_runs.values()
                    for o in runs
                    if not o.skipped
                )
            )
            public_outputs, public_runs = self._public_scope_outputs(ctx, graph)
            public_halt_reason = halt_reason
            if halt_reason is not None:
                for node_id, raw_output in ctx.node_outputs.items():
                    if halt_reason == raw_output.output.strip():
                        public_halt_reason = public_outputs[node_id].output.strip()
                        break
            reason = None if success else public_halt_reason or self._failure_reason(public_outputs)
            terminal_status: Literal["completed", "failed", "stopped"] | None = (
                "stopped" if reason == "stopped by user" else None
            )
            run_log.complete(success, reason, terminal_status=terminal_status)
            self._notify_run_log_update()
            return ExecutionResult(
                workflow_id=self._workflow.config.id,
                success=success,
                node_outputs=public_outputs,
                node_runs=public_runs,
                duration_seconds=total,
                log_path=run_log.path,
                usage_summary=summarize_node_outputs(ctx.node_outputs, ctx.node_runs),
                parameters=self._masked_parameters(ctx.params),
                inputs=self._masked_parameters(ctx.inputs),
                variables=self._masked_variables(ctx.variables),
            )
        except BaseException as exc:
            run_log.complete(False, str(exc))
            self._notify_run_log_update()
            raise
        finally:
            self._stop_monitor_done.set()
            if monitor is not None:
                monitor.join(timeout=1)
            if self._stop_file is not None:
                if self._run_stop_file is not None:
                    self._run_stop_file.unlink(missing_ok=True)

    async def resume_from_approval(self, request: ApprovalRequest) -> ExecutionResult | None:
        claimed = self._approval_store.claim_resume(
            request.workflow_id,
            request.run_id,
            request.node_id,
        )
        if claimed is None or claimed.decision is None:
            return None
        try:
            result = await self._resume_from_claimed_approval(claimed)
        except BaseException:
            self._approval_store.release_resume(
                claimed.workflow_id,
                claimed.run_id,
                claimed.node_id,
            )
            raise
        if result is None:
            self._approval_store.release_resume(
                claimed.workflow_id,
                claimed.run_id,
                claimed.node_id,
            )
        return result

    async def _resume_from_claimed_approval(
        self,
        claimed: ApprovalRequest,
    ) -> ExecutionResult | None:
        if claimed.decision is None or not claimed.checkpoint_path:
            return None
        checkpoint_path = Path(claimed.checkpoint_path)
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        checkpoint = self._hydrate_snapshot_artifacts(checkpoint_path, checkpoint)
        if not isinstance(checkpoint, dict):
            return None

        log_path = Path(claimed.log_path) if claimed.log_path else None
        self._run_log = WorkflowRunLog(
            self._workflow.config.id,
            self._log_base_dir,
            self._limits,
            existing_path=log_path,
        )
        self._notify_run_log_update()
        run_log = self._log()
        checkpoint_trigger = checkpoint.get("trigger")
        checkpoint_params = checkpoint.get("inputs", checkpoint.get("params"))
        safe_checkpoint_inputs = (
            {name: value for name, value in checkpoint_params.items() if value != "***"}
            if isinstance(checkpoint_params, dict)
            else self._run_parameters
        )
        sensitive_scope, sensitive_scope_unavailable = self._resume_sensitive_scope(
            checkpoint_path, checkpoint, checkpoint_params
        )
        if sensitive_scope_unavailable and (
            self._contains_redacted_snapshot_value(checkpoint.get("variables"))
            or self._contains_redacted_snapshot_value(checkpoint.get("nodeOutputs"))
        ):
            raise CheckpointSecretKeyUnavailable(
                "The replacement secret inputs were accepted, but this approval checkpoint "
                "also contains secret variable or node-output state that cannot be rebuilt. "
                "Restart the workflow from the beginning."
            )
        sensitive_inputs = sensitive_scope.get("inputs")
        if isinstance(sensitive_inputs, dict):
            safe_checkpoint_inputs.update(sensitive_inputs)
        resolved_inputs = resolve_workflow_parameters(
            self._parameter_config(),
            {**safe_checkpoint_inputs, **self._run_parameters},
            invocation_id=run_log.path.name,
        )
        ctx = ExecutionContext(
            trigger=checkpoint_trigger if isinstance(checkpoint_trigger, dict) else {},
            inputs=resolved_inputs,
            variables=self._initial_variables(resolved_inputs),
        )
        checkpoint_variables = checkpoint.get("variables")
        if isinstance(checkpoint_variables, dict):
            for name, value in checkpoint_variables.items():
                declaration = self._workflow.config.variables.get(str(name))
                if declaration is not None and value != "***":
                    ctx.variables[str(name)] = coerce_workflow_variable(
                        str(name), declaration, value
                    )
        sensitive_variables = sensitive_scope.get("variables")
        if isinstance(sensitive_variables, dict):
            for name, value in sensitive_variables.items():
                declaration = self._workflow.config.variables.get(str(name))
                if declaration is not None:
                    ctx.variables[str(name)] = coerce_workflow_variable(
                        str(name), declaration, value
                    )
        cleared_variables = checkpoint.get("clearedVariables")
        if isinstance(cleared_variables, list):
            for name in cleared_variables:
                if isinstance(name, str) and name in self._workflow.config.variables:
                    ctx.variables.pop(name, None)
        saved_outputs = checkpoint.get("nodeOutputs")
        if isinstance(saved_outputs, dict):
            for output_id, output_data in saved_outputs.items():
                sensitive_outputs = sensitive_scope.get("nodeOutputs")
                if isinstance(sensitive_outputs, dict):
                    output_data = sensitive_outputs.get(str(output_id), output_data)
                ctx.record(self._node_output_from_contract(str(output_id), output_data))

        start = time.monotonic()
        decision = claimed.decision
        approved = decision.decision == "approved"
        output_text = (
            f"approval {decision.decision} by {decision.decided_by}"
            f"{': ' + decision.notes if decision.notes else ''}"
        )
        approve_command = self._approval_command(
            "approve",
            claimed.run_id,
            claimed.node_id,
            claimed.approvers,
        )
        reject_command = self._approval_command(
            "reject",
            claimed.run_id,
            claimed.node_id,
            claimed.approvers,
        )
        approval_output = NodeOutput(
            node_id=claimed.node_id,
            success=approved,
            output=output_text,
            exit_code=0 if approved else 1,
            duration_seconds=0,
            type=str(OperationType.APPROVAL_GATE),
            value=decision.decision,
            data={
                "status": "decided",
                "decision": decision.decision,
                "approved": approved,
                "decidedBy": decision.decided_by,
                "notes": decision.notes,
                "message": claimed.message,
                "runId": claimed.run_id,
                "approvers": list(claimed.approvers),
                "timeoutSeconds": claimed.timeout_seconds,
                "followUpCommand": approve_command,
                "approveCommand": approve_command,
                "rejectCommand": reject_command,
                "manifest_id": claimed.subject_manifest_id,
            },
            error=output_text if not approved else None,
        )
        ctx.record(approval_output)
        run_log.node(
            claimed.node_id,
            f"approval decision: decision={decision.decision} by={decision.decided_by}",
        )
        run_log.event(
            claimed.node_id,
            "approval_decision",
            message=f"approval {decision.decision}",
            success=approved,
            data={
                "decision": decision.decision,
                "manifestId": claimed.subject_manifest_id,
            },
        )
        if decision.notes:
            run_log.node_output(claimed.node_id, "approval notes", decision.notes)

        graph = self._workflow.graph
        queue: deque[QueuedNode] = deque()
        queued: set[QueuedTaskKey] = set()
        running: set[QueuedTaskKey] = set()
        completed_loops: set[str] = set()
        queued_after_loop_edges: set[tuple[str, str]] = set()
        started_after_loop_edges: set[tuple[str, str]] = set()
        loop_next_index: dict[str, int] = {}
        loop_item_outputs: dict[str, dict[int, list[NodeOutput]]] = {}
        completed_loop_items: set[tuple[str, int]] = set()
        live_loop_started_item_keys: dict[str, set[str]] = {}
        self._queue_resume_frontier(graph, ctx, queue, queued, running)
        if approval_output.loop_items is not None:
            loop_next_index.setdefault(
                approval_output.node_id,
                min(approval_output.loop_max_concurrency, len(approval_output.loop_items)),
            )

        halted = False
        terminal_success: bool | None = None
        halt_reason: str | None = None
        run_counts: dict[str, int] = {}
        while queue and not halted:
            task = queue.popleft()
            queued.discard(task.key)
            if (
                task.node_id in ctx.node_outputs
                and task.loop_origin is None
                and task.after_loop_origin is None
            ):
                continue
            if task.after_loop_origin is not None:
                edge_key = (task.after_loop_origin, task.node_id)
                if edge_key in started_after_loop_edges:
                    continue
                started_after_loop_edges.add(edge_key)
            node = graph._nodes[task.node_id]
            expanded_tasks = self._for_each_tasks(node, task, ctx, graph)
            if expanded_tasks is not None:
                if expanded_tasks:
                    loop_next_index.setdefault(
                        task.node_id,
                        min(len(expanded_tasks), len(expanded_tasks[0].loop_items or [])),
                    )
                for expanded_task in expanded_tasks:
                    if expanded_task.key in queued or expanded_task.key in running:
                        continue
                    queue.append(expanded_task)
                    queued.add(expanded_task.key)
                continue
            run_counts[task.node_id] = run_counts.get(task.node_id, 0) + 1
            run_log.event(
                task.node_id,
                "queued",
                run_number=run_counts[task.node_id],
                fan_out_item=task.loop_item,
                message="node ready to start",
            )
            results: dict[str, NodeOutput] = {}
            halt_flag = [False]
            await self._run_node(
                node,
                ctx,
                results,
                halt_flag,
                graph,
                run_counts[task.node_id],
                loop_item=task.loop_item,
                item_context=task.item_context,
                execution_task_key=task.key,
            )
            output = results[task.node_id]
            ctx.record(output)
            if output.terminal_status is not None:
                self._record_loop_item_output(
                    task,
                    output,
                    loop_item_outputs,
                    completed_loop_items,
                    terminal=True,
                )
                halted = True
                terminal_success = output.terminal_status == "pass"
                halt_reason = output.output.strip() or None
                break
            if halt_flag[0]:
                self._record_loop_item_output(
                    task,
                    output,
                    loop_item_outputs,
                    completed_loop_items,
                    terminal=True,
                )
                halted = True
                halt_reason = (
                    "stopped by user" if self._stop_requested() else output.error or output.output
                )
                break
            scheduled_successors = self._queue_successor_tasks(
                output,
                task,
                graph,
                ctx,
                queue,
                queued,
                running,
                live_loop_started_item_keys,
            )
            self._record_loop_item_output(
                task,
                output,
                loop_item_outputs,
                completed_loop_items,
                terminal=not scheduled_successors,
            )
            if output.loop_items is not None:
                loop_next_index.setdefault(
                    output.node_id,
                    min(output.loop_max_concurrency, len(output.loop_items)),
                )
            if output.loop_items is not None and not scheduled_successors:
                self._queue_loop_done_successors(
                    task.node_id,
                    graph,
                    queue,
                    queued,
                    running,
                    completed_loops,
                    queued_after_loop_edges,
                )

            if (
                task.loop_origin
                and (task.loop_infinite or task.loop_items is not None)
                and not scheduled_successors
                and not self._stop_requested()
            ):
                iteration_key = (task.loop_origin, task.loop_index)
                has_iteration_work = any(
                    key[1] == iteration_key[0] and key[2] == iteration_key[1]
                    for key in queued | running
                )
                if not has_iteration_work:
                    next_index = loop_next_index.get(
                        task.loop_origin,
                        (task.loop_index or 0) + 1,
                    )
                    live_next = self._next_live_loop_item(
                        task.loop_origin,
                        graph,
                        ctx,
                        task.loop_items,
                        live_loop_started_item_keys,
                    )
                    next_item: dict[str, object]
                    if live_next is not None:
                        next_index, next_item = live_next
                    elif task.loop_items is not None and next_index >= len(task.loop_items):
                        has_loop_work = any(key[1] == task.loop_origin for key in queued | running)
                        if not has_loop_work:
                            self._queue_loop_done_successors(
                                task.loop_origin,
                                graph,
                                queue,
                                queued,
                                running,
                                completed_loops,
                                queued_after_loop_edges,
                            )
                        continue
                    if live_next is not None:
                        pass
                    elif task.loop_infinite:
                        next_item = {"index": str(next_index)}
                    else:
                        if task.loop_items is None:
                            continue
                        next_item = task.loop_items[next_index]
                        self._mark_live_loop_item_started(
                            task.loop_origin,
                            graph,
                            next_item,
                            live_loop_started_item_keys,
                        )
                    loop_next_index[task.loop_origin] = max(
                        loop_next_index.get(task.loop_origin, 0),
                        next_index + 1,
                    )
                    origin_node = graph._nodes.get(task.loop_origin)
                    if origin_node is not None and origin_node.for_each:
                        next_node_ids = [task.loop_origin]
                    else:
                        next_node_ids = [
                            successor_id
                            for successor_id in graph._graph.successors(task.loop_origin)
                            if graph.get_edge_config(
                                task.loop_origin,
                                successor_id,
                            ).condition
                            != EdgeConditionType.AFTER_LOOP
                        ]
                    source_node_id = (
                        self._for_each_source_node_id(origin_node, graph)
                        if origin_node is not None and origin_node.for_each
                        else task.loop_origin
                    )
                    for successor_id in next_node_ids:
                        item_context = dict(task.item_context)
                        if source_node_id is not None:
                            item_context[source_node_id] = next_item
                        if origin_node is not None and origin_node.for_each:
                            item_context[origin_node.node_id] = next_item
                        next_task = QueuedNode(
                            successor_id,
                            loop_origin=task.loop_origin,
                            loop_item=next_item,
                            item_context=item_context,
                            loop_index=next_index,
                            loop_items=task.loop_items,
                            loop_infinite=task.loop_infinite,
                            loop_max_concurrency=task.loop_max_concurrency,
                            loop_fail_fast=task.loop_fail_fast,
                        )
                        if next_task.key in queued or next_task.key in running:
                            continue
                        queue.append(next_task)
                        queued.add(next_task.key)

        self._record_unrun_node_events(
            graph,
            ctx,
            run_log,
            stopped=halt_reason == "stopped by user" or self._stop_requested(),
        )
        success = (
            terminal_success
            if terminal_success is not None
            else not halted
            and all(
                output.success
                or graph._nodes[output.node_id].allow_failure
                or (
                    output.type == str(OperationType.APPROVAL_GATE)
                    and self._has_failure_route(graph._nodes[output.node_id], output, graph)
                )
                for runs in ctx.node_runs.values()
                for output in runs
                if not output.skipped
            )
        )
        public_outputs, public_runs = self._public_scope_outputs(ctx, graph)
        public_halt_reason = halt_reason
        if halt_reason is not None:
            for node_id, raw_output in ctx.node_outputs.items():
                if halt_reason == raw_output.output.strip():
                    public_halt_reason = public_outputs[node_id].output.strip()
                    break
        reason = None if success else public_halt_reason or self._failure_reason(public_outputs)
        terminal_status: Literal["completed", "failed", "stopped"] | None = (
            "stopped" if reason == "stopped by user" else None
        )
        run_log.complete(success, reason, terminal_status=terminal_status)
        self._notify_run_log_update()
        return ExecutionResult(
            workflow_id=self._workflow.config.id,
            success=success,
            node_outputs=public_outputs,
            node_runs=public_runs,
            duration_seconds=time.monotonic() - start,
            log_path=run_log.path,
            usage_summary=summarize_node_outputs(ctx.node_outputs, ctx.node_runs),
            parameters=self._masked_parameters(ctx.params),
            inputs=self._masked_parameters(ctx.inputs),
            variables=self._masked_variables(ctx.variables),
        )

    def _start_stop_monitor(self) -> threading.Thread | None:
        if self._stop_file is None:
            return None

        def monitor() -> None:
            while not self._stop_monitor_done.wait(0.1):
                if (self._stop_file and self._stop_file.exists()) or (
                    self._run_stop_file and self._run_stop_file.exists()
                ):
                    self._cancel_event.set()
                    return

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        return thread

    def _initial_node_ids(self, graph: WorkflowGraph) -> list[str]:
        roots = [
            node_id
            for node_id in graph._nodes
            if not [pred_id for pred_id in graph._graph.predecessors(node_id) if pred_id != node_id]
        ]
        if roots:
            return roots
        return [next(iter(graph._nodes))] if graph._nodes else []

    def _approval_command(
        self,
        action: Literal["approve", "reject"],
        run_id: str,
        node_id: str,
        approvers: Sequence[str] | None = None,
    ) -> str:
        command = f"gof workflow {action} {run_id} {node_id} --workflow {self._workflow.config.id}"
        if approvers:
            command = f"{command} --by {shlex.quote(str(approvers[0]))}"
        return command

    def _queue_resume_frontier(
        self,
        graph: WorkflowGraph,
        ctx: ExecutionContext,
        queue: deque[QueuedNode],
        queued_tasks: set[QueuedTaskKey],
        running_tasks: set[QueuedTaskKey],
    ) -> None:
        completed = set(ctx.node_outputs)
        for node_id in self._initial_node_ids(graph):
            if node_id in completed:
                continue
            task = QueuedNode(node_id)
            if task.key in queued_tasks or task.key in running_tasks:
                continue
            queue.append(task)
            queued_tasks.add(task.key)

        for output in list(ctx.node_outputs.values()):
            current_task = QueuedNode(output.node_id)
            before = len(queue)
            self._queue_successor_tasks(
                output,
                current_task,
                graph,
                ctx,
                queue,
                queued_tasks,
                running_tasks,
            )
            if len(queue) == before:
                continue
            filtered = deque(
                task
                for task in queue
                if task.node_id not in completed or task.loop_origin is not None
            )
            if len(filtered) != len(queue):
                queue.clear()
                queue.extend(filtered)
                queued_tasks.clear()
                queued_tasks.update(task.key for task in queue)

    def _all_inputs_ready(
        self,
        node_id: str,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> bool:
        for pred_id in graph._graph.predecessors(node_id):
            if pred_id == node_id:
                continue
            output = self._matching_predecessor_output(pred_id, ctx, item_context)
            if output is None:
                return False
            edge = graph.get_edge_config(pred_id, node_id)
            if not self._edge_matches(edge, output, graph):
                return False
        return True

    def _matching_predecessor_output(
        self,
        pred_id: str,
        ctx: ExecutionContext,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> NodeOutput | None:
        if not item_context:
            return ctx.node_outputs.get(pred_id)
        context_key = _item_context_key(item_context)
        for output in reversed(ctx.node_runs.get(pred_id, [])):
            if output.data.get("_itemContextKey") == context_key:
                return output
        if pred_id in item_context:
            return ctx.node_outputs.get(pred_id)
        latest_output = ctx.node_outputs.get(pred_id)
        if latest_output is not None and not latest_output.data.get("_itemContextKey"):
            return latest_output
        return None

    def _for_each_source_node_id(
        self,
        node: GraphNode,
        graph: WorkflowGraph,
    ) -> str:
        if not node.for_each:
            return node.node_id
        expression = parse_exact_reference(node.for_each)
        if expression is None:
            return node.node_id
        root = expression.split(".", 1)[0]
        if root in graph._nodes:
            return root
        return node.node_id

    def _normalize_for_each_items(self, value: object) -> list[dict[str, object]]:
        if value in ("", None):
            return []
        if not isinstance(value, list):
            raise ValueError("for_each must resolve to a list")
        items: list[dict[str, object]] = []
        for index, item in enumerate(value[: self._limits.max_fanout_items]):
            if isinstance(item, dict):
                normalized = dict(item)
            else:
                normalized = {"value": item}
            normalized.setdefault("index", str(index))
            items.append(normalized)
        return items

    def _resolve_for_each_settings(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        task: QueuedNode,
    ) -> tuple[int, bool]:
        require_reference_capability("nodes.*.max_concurrency")
        require_reference_capability("nodes.*.fail_fast")
        if (
            isinstance(node.max_concurrency, int)
            and not isinstance(node.max_concurrency, bool)
            and isinstance(node.fail_fast, bool)
        ):
            return node.max_concurrency, node.fail_fast
        template_context = self._template_context(
            node,
            ctx,
            graph,
            task.loop_item,
            task.item_context,
        )
        resolved_max_concurrency = self._render_reference_value(
            node.max_concurrency,
            template_context,
            node_id=node.node_id,
            field_path="max_concurrency",
        )
        if (
            isinstance(resolved_max_concurrency, bool)
            or not isinstance(resolved_max_concurrency, (int, float))
            or not float(resolved_max_concurrency).is_integer()
        ):
            raise ValueError(
                f"Workflow '{self._workflow.config.id}' invocation "
                f"'{self._log().path.name}', node '{node.node_id}', field "
                f"'max_concurrency': resolved value must be an integer"
            )
        resolved_fail_fast = self._render_reference_value(
            node.fail_fast,
            template_context,
            node_id=node.node_id,
            field_path="fail_fast",
        )
        if not isinstance(resolved_fail_fast, bool):
            raise ValueError(
                f"Workflow '{self._workflow.config.id}' invocation "
                f"'{self._log().path.name}', node '{node.node_id}', field "
                f"'fail_fast': resolved value must be a boolean"
            )
        return int(resolved_max_concurrency), resolved_fail_fast

    def _for_each_tasks(
        self,
        node: GraphNode,
        task: QueuedNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
    ) -> list[QueuedNode] | None:
        if not node.for_each:
            return None
        require_reference_capability("nodes.*.for_each")
        if task.loop_origin == node.node_id and task.loop_items is not None:
            return None
        max_concurrency, fail_fast = self._resolve_for_each_settings(
            node,
            ctx,
            graph,
            task,
        )
        items = self._normalize_for_each_items(
            ctx.resolve_path_with_loop(
                node.for_each,
                task.loop_item,
                current_node_id=node.node_id,
                graph=graph,
                item_context=task.item_context,
            )
        )
        if not items:
            return []
        initial_count = min(
            max(1, min(max_concurrency, self._limits.max_fanout_concurrency)),
            len(items),
        )
        source_node_id = self._for_each_source_node_id(node, graph)
        expanded: list[QueuedNode] = []
        for index in range(initial_count):
            item = items[index]
            item_context = dict(task.item_context)
            item_context[source_node_id] = item
            item_context[node.node_id] = item
            expanded.append(
                QueuedNode(
                    node.node_id,
                    loop_origin=node.node_id,
                    loop_item=item,
                    item_context=item_context,
                    loop_index=index,
                    loop_items=items,
                    loop_infinite=False,
                    loop_max_concurrency=max(
                        1,
                        min(max_concurrency, self._limits.max_fanout_concurrency),
                    ),
                    loop_fail_fast=fail_fast,
                )
            )
        return expanded

    def _successor_tasks(
        self,
        successor_id: str,
        output: NodeOutput,
        current_task: QueuedNode,
    ) -> list[QueuedNode]:
        if output.loop_infinite:
            return [
                QueuedNode(
                    successor_id,
                    loop_origin=output.node_id,
                    loop_item={"index": "0"},
                    item_context={
                        **current_task.item_context,
                        output.node_id: {"index": "0"},
                    },
                    loop_index=0,
                    loop_items=None,
                    loop_infinite=True,
                    loop_max_concurrency=min(1, self._limits.max_fanout_concurrency),
                    loop_fail_fast=False,
                )
            ]
        if output.loop_items is not None:
            if not output.loop_items:
                return []
            initial_count = min(output.loop_max_concurrency, len(output.loop_items))
            return [
                QueuedNode(
                    successor_id,
                    loop_origin=output.node_id,
                    loop_item=output.loop_items[index],
                    item_context={
                        **current_task.item_context,
                        output.node_id: output.loop_items[index],
                    },
                    loop_index=index,
                    loop_items=output.loop_items,
                    loop_infinite=False,
                    loop_max_concurrency=output.loop_max_concurrency,
                    loop_fail_fast=output.loop_fail_fast,
                )
                for index in range(initial_count)
            ]
        return [
            QueuedNode(
                successor_id,
                loop_origin=current_task.loop_origin,
                loop_item=current_task.loop_item,
                item_context=current_task.item_context,
                loop_index=current_task.loop_index,
                loop_items=current_task.loop_items,
                loop_infinite=current_task.loop_infinite,
                loop_max_concurrency=current_task.loop_max_concurrency,
                loop_fail_fast=current_task.loop_fail_fast,
            )
        ]

    def _successor_await_all_inputs_ready(
        self,
        successor: GraphNode,
        output: NodeOutput,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        current_task: QueuedNode,
    ) -> bool:
        if not successor.await_all_inputs:
            return True
        if output.loop_items is not None:
            return True
        return self._all_inputs_ready(successor.node_id, ctx, graph, current_task.item_context)

    def _loop_source_for_origin(
        self,
        loop_origin: str | None,
        graph: WorkflowGraph,
    ) -> FanSource | None:
        if not loop_origin:
            return None
        node = graph._nodes.get(loop_origin)
        if node is None or not isinstance(node.operation, LoopOperation):
            return None
        return node.operation.source

    def _is_live_loop_source(self, source: FanSource | None) -> bool:
        return isinstance(source, DirectoryFanSource)

    def _live_loop_item_key(self, source: FanSource | None, item: dict[str, object] | None) -> str:
        if item is None:
            return ""
        if isinstance(source, DirectoryFanSource):
            path = item.get("path") or item.get("file_path")
            return f"path:{path}" if path is not None else ""
        return ""

    def _mark_live_loop_item_started(
        self,
        loop_origin: str | None,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None,
        live_loop_started_item_keys: dict[str, set[str]],
    ) -> None:
        source = self._loop_source_for_origin(loop_origin, graph)
        if not self._is_live_loop_source(source):
            return
        item_key = self._live_loop_item_key(source, loop_item)
        if item_key:
            live_loop_started_item_keys.setdefault(str(loop_origin), set()).add(item_key)

    def _next_live_loop_item(
        self,
        loop_origin: str,
        graph: WorkflowGraph,
        ctx: ExecutionContext,
        loop_items: list[dict[str, object]] | None,
        live_loop_started_item_keys: dict[str, set[str]],
    ) -> tuple[int, dict[str, object]] | None:
        source = self._loop_source_for_origin(loop_origin, graph)
        if not self._is_live_loop_source(source):
            return None
        assert source is not None
        if loop_items is None:
            return None
        started_keys = live_loop_started_item_keys.setdefault(loop_origin, set())
        existing_indexes = {
            self._live_loop_item_key(source, item): index
            for index, item in enumerate(loop_items)
            if self._live_loop_item_key(source, item)
        }
        current_items = _resolve_fan_items(
            source,
            ctx,
            self._limits,
            self._path_base,
            self._data_dir,
            self._require_workflow_path_access,
        )
        for item in current_items:
            item_key = self._live_loop_item_key(source, item)
            if not item_key or item_key in started_keys:
                continue
            started_keys.add(item_key)
            if item_key in existing_indexes:
                index = existing_indexes[item_key]
                loop_items[index] = item
                return index, item
            loop_items.append(item)
            return len(loop_items) - 1, item
        return None

    def _queue_successor_tasks(
        self,
        output: NodeOutput,
        current_task: QueuedNode,
        graph: WorkflowGraph,
        ctx: ExecutionContext,
        queue: deque[QueuedNode],
        queued_tasks: set[QueuedTaskKey],
        running_tasks: set[QueuedTaskKey],
        live_loop_started_item_keys: dict[str, set[str]] | None = None,
    ) -> bool:
        scheduled = False
        for successor_id in graph._graph.successors(output.node_id):
            edge = graph.get_edge_config(output.node_id, successor_id)
            if edge.condition == EdgeConditionType.AFTER_LOOP:
                continue
            matched = self._edge_matches(edge, output, graph)
            self._log().event(
                output.node_id,
                "edge_decision",
                message=(
                    f"{output.node_id} -> {successor_id} "
                    f"{'matched' if matched else 'skipped'} ({edge.condition})"
                ),
                success=matched,
                data={
                    "from": output.node_id,
                    "to": successor_id,
                    "condition": str(edge.condition),
                    "outputPattern": edge.output_pattern or "",
                    "matched": matched,
                },
            )
            if not matched:
                continue
            successor = graph._nodes[successor_id]
            if not self._successor_await_all_inputs_ready(
                successor,
                output,
                ctx,
                graph,
                current_task,
            ):
                continue
            for successor_task in self._successor_tasks(successor_id, output, current_task):
                if successor_task.key in queued_tasks or successor_task.key in running_tasks:
                    continue
                queue.append(successor_task)
                queued_tasks.add(successor_task.key)
                if live_loop_started_item_keys is not None:
                    self._mark_live_loop_item_started(
                        successor_task.loop_origin,
                        graph,
                        successor_task.loop_item,
                        live_loop_started_item_keys,
                    )
                self._log().event(
                    successor_id,
                    "queued",
                    fan_out_item=successor_task.loop_item,
                    message=f"queued by {output.node_id}",
                    data={"from": output.node_id},
                )
                scheduled = True
        return scheduled

    def _queue_loop_done_successors(
        self,
        loop_node_id: str,
        graph: WorkflowGraph,
        queue: deque[QueuedNode],
        queued_tasks: set[QueuedTaskKey],
        running_tasks: set[QueuedTaskKey],
        completed_loops: set[str],
        queued_after_loop_edges: set[tuple[str, str]],
    ) -> None:
        if loop_node_id in completed_loops:
            return
        completed_loops.add(loop_node_id)
        for successor_id in graph._graph.successors(loop_node_id):
            edge = graph.get_edge_config(loop_node_id, successor_id)
            if edge.condition != EdgeConditionType.AFTER_LOOP:
                continue
            edge_key = (loop_node_id, successor_id)
            if edge_key in queued_after_loop_edges:
                continue
            queued_after_loop_edges.add(edge_key)
            self._log().event(
                loop_node_id,
                "edge_decision",
                message=f"{loop_node_id} -> {successor_id} matched ({edge.condition})",
                success=True,
                data={
                    "from": loop_node_id,
                    "to": successor_id,
                    "condition": str(edge.condition),
                    "outputPattern": edge.output_pattern or "",
                    "matched": True,
                },
            )
            task = QueuedNode(successor_id, after_loop_origin=loop_node_id)
            if task.key in queued_tasks or task.key in running_tasks:
                continue
            queue.append(task)
            queued_tasks.add(task.key)
            self._log().event(
                successor_id,
                "queued",
                message=f"queued after loop {loop_node_id}",
                data={"from": loop_node_id, "condition": str(edge.condition)},
            )

    async def _run_node(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        results: dict[str, NodeOutput],
        halt_flag: list[bool],
        graph: WorkflowGraph,
        run_number: int = 1,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
        execution_task_key: QueuedTaskKey | None = None,
    ) -> None:
        if self._dry_run:
            log.info("[dry-run] would execute node %s", node.node_id)
            self._log().node(
                node.node_id,
                self._run_log_message(run_number, "dry-run would execute"),
            )
            results[node.node_id] = NodeOutput(
                node_id=node.node_id, success=True, output="", exit_code=0, duration_seconds=0.0
            )
            self._log().event(node.node_id, "completed", success=True, message="dry run")
            return

        attempt = 0
        output: NodeOutput | None = None
        while True:
            resolution_error: Exception | None = None
            try:
                inputs = self._resolve_node_inputs(node, ctx, graph, loop_item, item_context)
                if execution_task_key is not None:
                    self._execution_cache_snapshots[execution_task_key] = (
                        copy.deepcopy(inputs),
                        self._cache_key_for(node, inputs, loop_item, ctx),
                    )
                cached_output = self._cached_output_for_task(node, inputs, loop_item, ctx)
            except Exception as exc:  # noqa: BLE001
                inputs = {}
                cached_output = None
                resolution_error = exc
            if cached_output is not None:
                results[node.node_id] = cached_output
                self._log().node(
                    node.node_id,
                    self._run_log_message(run_number, "reused cached output"),
                )
                self._log().event(
                    node.node_id,
                    "reused",
                    attempt=attempt + 1,
                    run_number=run_number,
                    fan_out_item=loop_item,
                    message="reused cached output",
                    duration_seconds=0,
                    exit_code=cached_output.exit_code,
                    success=cached_output.success,
                    skipped=True,
                    data={**self._node_event_data(cached_output), "reused": True},
                )
                return
            self._log().begin_node_attempt(node.node_id, run_number, attempt + 1)
            attempt_started = time.monotonic()
            self._log().event(
                node.node_id,
                "started",
                attempt=attempt + 1,
                run_number=run_number,
                fan_out_item=loop_item,
                message=f"attempt {attempt + 1} started",
                data={"inputs": self._event_preview(inputs)},
            )
            self._log().node(
                node.node_id,
                self._run_log_message(run_number, f"attempt {attempt + 1} started"),
            )
            try:
                if resolution_error is not None:
                    raise resolution_error
                output = await self._execute_operation(
                    node,
                    ctx,
                    graph,
                    loop_item,
                    item_context,
                    run_number=run_number,
                    attempt=attempt + 1,
                )
            except Exception as exc:  # noqa: BLE001
                self._log().error(f"{node.node_id} raised exception: {exc}")
                output = NodeOutput(
                    node_id=node.node_id,
                    success=False,
                    output=str(exc),
                    exit_code=1,
                    duration_seconds=0.0,
                )
            if item_context:
                output.data = {
                    **output.data,
                    "item": loop_item or {},
                    "items": item_context,
                    "_itemContextKey": _item_context_key(item_context),
                }
            results[node.node_id] = output
            public_output = self._public_node_output(output, ctx, graph)
            logged_output = public_output.output
            if public_output.type == str(OperationType.HTTP_REQUEST):
                preview = public_output.data.get("responsePreview")
                if isinstance(preview, dict):
                    preview_body = preview.get("body")
                    if isinstance(preview_body, str):
                        logged_output = preview_body
                elif isinstance(public_output.data.get("error"), str):
                    logged_output = str(public_output.data["error"])
            self._log().node_output(
                node.node_id,
                "node output",
                logged_output,
                uncapped=output.type
                in {str(OperationType.AGENT), str(OperationType.COMMON_LLM_TASK)},
            )
            self._log().node(
                node.node_id,
                self._run_log_message(
                    run_number,
                    f"attempt {attempt + 1} finished success={output.success} "
                    f"exit_code={output.exit_code} duration={output.duration_seconds:.2f}s",
                ),
            )
            status = "completed" if output.success else "failed"
            if self._stop_requested():
                status = "stopped"
            self._log().event(
                node.node_id,
                status,
                attempt=attempt + 1,
                run_number=run_number,
                fan_out_item=loop_item,
                message=(
                    f"attempt {attempt + 1} finished "
                    f"success={output.success} exit_code={output.exit_code}"
                ),
                duration_seconds=output.duration_seconds or (time.monotonic() - attempt_started),
                exit_code=output.exit_code,
                success=output.success,
                data=self._node_event_data(public_output),
            )
            if output.success or attempt >= node.retry_count:
                break
            attempt += 1
            self._log().node(
                node.node_id,
                self._run_log_message(
                    run_number, f"retrying after {node.retry_delay_seconds:.2f}s"
                ),
            )
            self._log().event(
                node.node_id,
                "retried",
                attempt=attempt + 1,
                run_number=run_number,
                message=f"retrying after {node.retry_delay_seconds:.2f}s",
            )
            if self._stop_requested():
                break
            await anyio.sleep(node.retry_delay_seconds)

        if output is not None and not output.success:
            if (
                not node.allow_failure
                and node.on_failure == "halt"
                and not self._has_failure_route(node, output, graph)
            ):
                halt_flag[0] = True

        if self._stop_requested():
            halt_flag[0] = True

    def _run_log_message(self, run_number: int, message: str) -> str:
        if run_number == 1:
            return message
        return f"run {run_number} {message}"

    def _has_failure_route(self, node: GraphNode, output: NodeOutput, graph: WorkflowGraph) -> bool:
        for successor_id in graph._graph.successors(node.node_id):
            edge = graph.get_edge_config(node.node_id, successor_id)
            if edge.condition in {
                EdgeConditionType.ON_FAILURE,
                EdgeConditionType.OUTPUT_MATCHES,
                EdgeConditionType.OUTPUT_NO_MATCH,
            } and self._edge_matches(edge, output, graph):
                return True
        return False

    def _edge_matches(
        self,
        edge: EdgeConfig,
        output: NodeOutput,
        graph: WorkflowGraph,
    ) -> bool:
        if edge.condition == EdgeConditionType.AFTER_LOOP:
            return False
        if edge.condition == EdgeConditionType.OUTPUT_NO_MATCH:
            return not self._has_matching_output_match_edge(output, graph)
        matched = bool(edge.evaluate(output))
        if edge.condition == EdgeConditionType.OUTPUT_FIELD:
            self._log().event(
                output.node_id,
                "output_field_evaluated",
                message=(
                    f"structured predicate {edge.explanation()}: "
                    f"{'matched' if matched else 'did not match'}"
                ),
                data={
                    "toNode": edge.to_node,
                    "field": edge.field,
                    "operator": edge.operator.value if edge.operator is not None else None,
                    "matched": matched,
                    "explanation": edge.explanation(),
                },
            )
        return matched

    def _has_matching_output_match_edge(
        self,
        output: NodeOutput,
        graph: WorkflowGraph,
    ) -> bool:
        for successor_id in graph._graph.successors(output.node_id):
            candidate = graph.get_edge_config(output.node_id, successor_id)
            if candidate.condition != EdgeConditionType.OUTPUT_MATCHES:
                continue
            if candidate.evaluate(output):
                return True
        return False

    def _resolve_piped_outputs(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> list[str]:
        piped = []
        for pred_id in graph._graph.predecessors(node.node_id):
            pred_node = graph._nodes.get(pred_id)
            if pred_node is None or not pred_node.pipe_output:
                continue
            if pred_node.operation.type == OperationType.LOOP and loop_item is not None:
                piped.append(json.dumps(loop_item, default=str))
                continue
            if (
                output := self._matching_predecessor_output(pred_id, ctx, item_context)
            ) is not None:
                piped.append(output.output)
        return piped

    def _resolve_input_value(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        value: str,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
        field_pattern: str = "nodes.*.inputs.*",
        error_field: str | None = None,
    ) -> object:
        require_reference_capability(field_pattern)
        diagnostic_field = error_field or field_pattern
        expression = parse_exact_reference(value)
        if expression is None:
            return value
        parts = expression.split(".")
        root = parts[0] if parts else ""
        known_roots = {
            "trigger",
            "params",
            "inputs",
            "vars",
            "nodes",
            "loop",
            "item",
            "items",
            "previous",
            "workflow",
            "run",
            "secret",
            *ctx.node_outputs.keys(),
        }
        if root not in known_roots:
            if value.strip().startswith("{{"):
                raise ValueError(
                    f"Workflow '{self._workflow.config.id}' invocation "
                    f"'{self._log().path.name}', node '{node.node_id}', field "
                    f"'{diagnostic_field}': unresolved reference '{expression}'"
                )
            return value
        if root == ReferenceNamespace.SECRET:
            if len(parts) != 2:
                return value
            # Missing secrets are execution failures, not literal fallback values.
            return _read_secret(parts[1])
        try:
            if root == ReferenceNamespace.WORKFLOW:
                obj: object = {
                    "id": self._workflow.config.id,
                    "name": self._workflow.config.name,
                    "path": str(self._workflow_path) if self._workflow_path else "",
                }
                return _resolve_exact_input_path(obj, parts[1:], value)
            if root == ReferenceNamespace.RUN:
                run_log = self._log()
                obj = {
                    "id": run_log.path.name,
                    "logPath": str(run_log.path),
                    "approveCommand": self._approval_command(
                        "approve",
                        run_log.path.name,
                        node.node_id,
                        getattr(node.operation, "approvers", []),
                    ),
                    "rejectCommand": self._approval_command(
                        "reject",
                        run_log.path.name,
                        node.node_id,
                        getattr(node.operation, "approvers", []),
                    ),
                }
                return _resolve_exact_input_path(obj, parts[1:], value)
            return ctx.resolve_path_with_loop(
                value,
                loop_item,
                current_node_id=node.node_id,
                graph=graph,
                item_context=item_context,
            )
        except Exception as exc:
            raise ValueError(
                f"Workflow '{self._workflow.config.id}' invocation "
                f"'{self._log().path.name}', node '{node.node_id}', field "
                f"'{diagnostic_field}': unresolved reference '{expression}'"
            ) from exc

    def _resolve_node_inputs(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            key: self._resolve_input_value(
                node,
                ctx,
                graph,
                value,
                loop_item,
                item_context,
                error_field=f"inputs.{key}",
            )
            for key, value in node.inputs.items()
        }

    def _input_text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)

    def _explicit_stdin(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> bytes | None:
        inputs = self._resolve_node_inputs(node, ctx, graph, loop_item, item_context)
        if "stdin" not in inputs:
            return None
        return self._input_text(inputs["stdin"]).encode()

    def _positional_input_args(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> list[str]:
        inputs = self._resolve_node_inputs(node, ctx, graph, loop_item, item_context)
        indexed: list[tuple[int, str]] = []
        for key, value in inputs.items():
            if key == "stdin":
                indexed.append((1, self._input_text(value)))
            elif key.startswith("stdin") and key[5:].isdigit():
                indexed.append((int(key[5:]), self._input_text(value)))
        return [value for _, value in sorted(indexed)]

    def _positional_input_env(self, positional_args: list[str]) -> dict[str, str]:
        return {
            f"GOFER_INPUT_ARG_{index}": value
            for index, value in enumerate(positional_args, start=1)
        }

    def _bash_command_with_positional_env(
        self,
        command: str,
        positional_arg_count: int,
    ) -> str:
        if positional_arg_count <= 0 or sys.platform == "win32":
            return command
        args = " ".join(
            f'"${{GOFER_INPUT_ARG_{index}:-}}"' for index in range(1, positional_arg_count + 1)
        )
        return f"set -- {args}; {command}"

    def _is_stdin_input_key(self, key: str) -> bool:
        return key == "stdin" or (key.startswith("stdin") and key[5:].isdigit())

    def _resolved_env(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        base_env: dict[str, str],
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, str] | None:
        require_reference_capability(f"nodes[type={node.operation.type.value}].env.*")
        template_context = self._template_context(node, ctx, graph, loop_item, item_context)
        env = {
            key: PromptManager._interpolate(value, template_context)
            for key, value in base_env.items()
        }
        if loop_item:
            for key, value in loop_item.items():
                if key.lower() == "path":
                    continue
                if isinstance(value, (str, int, float, bool)):
                    env.setdefault(key.upper(), self._input_text(value))
        for key, value in self._resolve_node_inputs(
            node,
            ctx,
            graph,
            loop_item,
            item_context,
        ).items():
            if self._is_stdin_input_key(key):
                continue
            if key.startswith("env.") and len(key) > 4:
                env[key[4:]] = self._input_text(value)
            else:
                env[key] = self._input_text(value)
        return env or None

    def _render_runtime_string(
        self,
        value: str,
        field_pattern: str,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
        extra_context: dict[str, object] | None = None,
    ) -> str:
        require_reference_capability(field_pattern)
        template_context = self._template_context(node, ctx, graph, loop_item, item_context)
        if extra_context:
            template_context.update(extra_context)
        rendered = self._render_reference_value(
            value,
            template_context,
            node_id=node.node_id,
            field_path=field_pattern,
        )
        return rendered if isinstance(rendered, str) else str(rendered)

    def _input_context(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            key: value
            for key, value in self._resolve_node_inputs(
                node,
                ctx,
                graph,
                loop_item,
                item_context,
            ).items()
            if key != "stdin" and not key.startswith("env.")
        }

    def _template_context(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        run_log = self._log()
        raw_approvers = getattr(node.operation, "approvers", [])
        command_approvers = raw_approvers if isinstance(raw_approvers, list) else []
        return {
            "__gofer_node_id": node.node_id,
            **{key: output.contract() for key, output in ctx.node_outputs.items()},
            ReferenceNamespace.WORKFLOW: {
                "id": self._workflow.config.id,
                "name": self._workflow.config.name,
                "path": str(self._workflow_path) if self._workflow_path else "",
            },
            ReferenceNamespace.RUN: {
                "id": run_log.path.name,
                "logPath": str(run_log.path),
                "approveCommand": self._approval_command(
                    "approve",
                    run_log.path.name,
                    node.node_id,
                    command_approvers,
                ),
                "rejectCommand": self._approval_command(
                    "reject",
                    run_log.path.name,
                    node.node_id,
                    command_approvers,
                ),
            },
            ReferenceNamespace.PARAMS: ctx.params,
            ReferenceNamespace.INPUTS: ctx.inputs,
            ReferenceNamespace.VARS: ctx.variables,
            ReferenceNamespace.NODES: {
                key: output.contract() for key, output in ctx.node_outputs.items()
            },
            ReferenceNamespace.TRIGGER: ctx.trigger,
            ReferenceNamespace.LOOP: {
                "current": loop_item or {},
                "items": item_context or {},
                **(loop_item or {}),
            },
            ReferenceNamespace.ITEM: loop_item or {},
            ReferenceNamespace.ITEMS: item_context or {},
            ReferenceNamespace.PREVIOUS: (
                ctx.predecessor_outputs(node.node_id, graph)[-1].contract()
                if ctx.predecessor_outputs(node.node_id, graph)
                else {}
            ),
            **self._input_context(node, ctx, graph, loop_item, item_context),
        }

    def _render_http_value(
        self,
        value: object,
        template_context: dict[str, object],
        field_pattern: str,
    ) -> object:
        require_reference_capability(field_pattern)
        context_node_id = template_context.get("__gofer_node_id")
        return self._render_reference_value(
            value,
            template_context,
            node_id=str(context_node_id) if context_node_id is not None else None,
            field_path=field_pattern,
        )

    def _render_reference_value(
        self,
        value: object,
        template_context: dict[str, object],
        *,
        node_id: str | None = None,
        field_path: str = "workflow.variables.initial",
    ) -> object:
        def unresolved_error(expression: str) -> ValueError:
            run_id = self._run_log.path.name if self._run_log is not None else "pending"
            return ValueError(
                f"Workflow '{self._workflow.config.id}' invocation '{run_id}', node "
                f"'{node_id or '<initialization>'}', field '{field_path}': unresolved "
                f"reference '{expression}'"
            )

        if isinstance(value, Path):
            return self._render_reference_value(
                str(value),
                template_context,
                node_id=node_id,
                field_path=field_path,
            )
        if isinstance(value, str):
            secret = _secret_name(value)
            if secret is not None:
                return _read_secret(secret)
            rendered_source = _replace_secret_tokens(value)
            exact = parse_exact_reference(rendered_source)
            if exact is not None and rendered_source.strip().startswith("{{"):
                current: object = template_context
                for part in exact.split("."):
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                        current = current[int(part)]
                    else:
                        raise unresolved_error(exact)
                return current
            rendered = PromptManager._interpolate(rendered_source, template_context)
            unresolved = re.search(r"\{\{\s*([^{}]+?)\s*\}\}", rendered)
            if unresolved is not None:
                raise unresolved_error(unresolved.group(1).strip())
            return rendered
        if isinstance(value, dict):
            return {
                str(key): self._render_reference_value(
                    item,
                    template_context,
                    node_id=node_id,
                    field_path=f"{field_path}.{key}",
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._render_reference_value(
                    item,
                    template_context,
                    node_id=node_id,
                    field_path=f"{field_path}.{index}",
                )
                for index, item in enumerate(value)
            ]
        return value

    def _runtime_reference_paths(
        self,
        value: object,
        field_path: tuple[str, ...] = (),
    ) -> Iterator[tuple[str, ...]]:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            if re.search(r"\{\{\s*[^{}]+?\s*\}\}", value):
                yield field_path
            return
        if isinstance(value, dict):
            for key, item in value.items():
                yield from self._runtime_reference_paths(
                    item,
                    (*field_path, str(key)),
                )
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from self._runtime_reference_paths(
                    item,
                    (*field_path, str(index)),
                )

    def _http_output_mapping(
        self,
        response_data: dict[str, object],
        mapping: dict[str, str],
    ) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, path in mapping.items():
            selected[key] = _extract_dotted_path(response_data, path)
        return selected

    def _resolve_pipe_stdin(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
    ) -> bytes | None:
        explicit = self._explicit_stdin(node, ctx, graph, loop_item, item_context)
        if explicit is not None:
            return explicit
        piped = self._resolve_piped_outputs(node, ctx, graph, loop_item, item_context)
        if not piped:
            return None
        return "\n".join(piped).encode()

    async def _execute_operation(
        self,
        node: GraphNode,
        ctx: ExecutionContext,
        graph: WorkflowGraph,
        loop_item: dict[str, object] | None = None,
        item_context: dict[str, dict[str, object]] | None = None,
        *,
        run_number: int = 1,
        attempt: int = 1,
    ) -> NodeOutput:
        op = node.operation
        source_op = op
        recursively_resolved_types = {
            OperationType.PASS,
            OperationType.FAIL,
            OperationType.BREAK,
            OperationType.BASH_COMMAND,
            OperationType.PYTHON_SCRIPT,
            OperationType.SHELL_SCRIPT,
            OperationType.READ_FILE,
            OperationType.WRITE_FILE,
            OperationType.COPY_FILE,
            OperationType.MOVE_FILE,
            OperationType.DELETE_FILE,
            OperationType.FILE,
            OperationType.FOLDER,
            OperationType.OPEN_RESOURCE,
            OperationType.PROMPT_FILE,
            OperationType.COMMON_LLM_TASK,
            OperationType.LOCAL_VECTORIZE,
            OperationType.LOCAL_SEARCH,
            OperationType.HTTP_REQUEST,
            OperationType.WORKFLOW,
            OperationType.SUBFLOW,
            OperationType.AGENT,
        }
        if op.type in recursively_resolved_types:
            serialized_operation = op.model_dump(by_alias=True)
            deferred_fields: dict[str, object] = {}
            if isinstance(op, PromptFileOperation):
                deferred_fields["template"] = serialized_operation.pop("template")
            elif isinstance(op, CommonLlmTaskOperation):
                deferred_fields["target"] = serialized_operation.pop("target")
                deferred_fields["instructions"] = serialized_operation.pop("instructions")
            elif isinstance(op, HttpRequestOperation):
                for field_name in ("url", "headers", "params", "json", "body"):
                    deferred_fields[field_name] = serialized_operation.pop(field_name)
            child_output_contract: dict[str, object] | None = None
            if isinstance(op, SubflowOperation):
                child_output_contract = op.output_contract
                serialized_operation.pop("output_contract", None)
            for reference_path in self._runtime_reference_paths(serialized_operation):
                if reference_path and reference_path != ("type",):
                    require_operation_reference_capability(
                        op.type.value,
                        reference_path,
                    )
            rendered_operation = self._render_reference_value(
                serialized_operation,
                self._template_context(node, ctx, graph, loop_item, item_context),
                node_id=node.node_id,
                field_path="operation",
            )
            if not isinstance(rendered_operation, dict):
                raise ValueError(f"Node '{node.node_id}' operation must resolve to an object")
            rendered_operation.update(deferred_fields)
            if child_output_contract is not None:
                # Output selectors are evaluated in the child result scope after
                # execution, not in the caller's invocation scope.
                rendered_operation["output_contract"] = child_output_contract
            try:
                op = op.__class__.model_validate(rendered_operation)
            except ValueError as exc:
                run_id = self._log().path.name
                raise ValueError(
                    f"Workflow '{self._workflow.config.id}' invocation '{run_id}', node "
                    f"'{node.node_id}', field 'operation': resolved value failed type "
                    f"validation: {exc}"
                ) from exc
        start = time.monotonic()

        if op.type == OperationType.START:
            assert isinstance(op, StartOperation)
            self._log().node(node.node_id, "start")
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output="",
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
            )

        if op.type == OperationType.PASS:
            assert isinstance(op, PassOperation)
            message = op.message.strip() or "workflow passed"
            self._log().node(node.node_id, f"pass: {message}")
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=message,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                terminal_status="pass",
                type=str(op.type),
                data={"message": message},
            )

        if op.type == OperationType.FAIL:
            assert isinstance(op, FailOperation)
            message = op.message.strip() or "workflow failed"
            self._log().node(node.node_id, f"fail: {message}")
            return NodeOutput(
                node_id=node.node_id,
                success=False,
                output=message,
                exit_code=1,
                duration_seconds=time.monotonic() - start,
                terminal_status="fail",
                type=str(op.type),
                data={"message": message},
            )

        if op.type == OperationType.BREAK:
            assert isinstance(op, BreakOperation)
            message = op.message.strip() or "loop break"
            self._log().node(node.node_id, f"break: {message}")
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=message,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                terminal_status="break",
                type=str(op.type),
                data={"message": message},
            )

        if op.type in {OperationType.WORKFLOW, OperationType.SUBFLOW}:
            subflow_op: SubflowOperation | None = None
            if op.type == OperationType.SUBFLOW:
                assert isinstance(op, SubflowOperation)
                subflow_op = op
                target_workflow, target_path = self._load_subflow_component(subflow_op)
                boundary = "subflow"
            else:
                assert isinstance(op, WorkflowCallOperation)
                target_workflow, target_path = self._load_child_workflow(op.workflow_id)
                boundary = "workflow call"
            if target_workflow.config.id == self._workflow.config.id:
                raise ValueError("Workflow nodes cannot call their own workflow")
            component_config = target_workflow.component
            if (
                subflow_op is not None
                and subflow_op.version
                and component_config is not None
                and component_config.version != subflow_op.version
            ):
                raise ValueError(
                    f"Subflow component '{component_config.id}' "
                    f"version {component_config.version} does not satisfy "
                    f"requested version {subflow_op.version}"
                )
            if subflow_op is not None:
                self._log().event(
                    node.node_id,
                    "subflow_started",
                    message=(
                        "subflow "
                        f"{component_config.id if component_config else subflow_op.component_id} "
                        "started"
                    ),
                    data={
                        "subflow": {
                            "component_id": (
                                component_config.id if component_config else subflow_op.component_id
                            ),
                            "version": (
                                component_config.version
                                if component_config
                                else subflow_op.version or ""
                            ),
                            "source_path": str(target_path),
                            "workflow_id": target_workflow.config.id,
                        }
                    },
                )
            self._log().node(
                node.node_id,
                (
                    f"{boundary} started: "
                    f"{target_workflow.config.name} ({target_workflow.config.id})"
                ),
            )
            child_executor = WorkflowExecutor(
                target_workflow,
                self._subscriptions,
                dry_run=self._dry_run,
                log_base_dir=(self._data_dir or target_path.parent) / "logs",
                workflow_path=target_path,
                data_dir=self._data_dir,
                cancel_event=self._cancel_event if self._pass_cancel_event else None,
                http_client=self._http_client,
                approval_store=self._approval_store,
                notification_adapter=self._notification_adapter,
                run_log_update_callback=self._run_log_update_callback,
            )
            if subflow_op is not None:
                child_executor.with_parameters(
                    self._render_subflow_parameter_bindings(
                        subflow_op,
                        node,
                        ctx,
                        graph,
                        loop_item,
                        item_context,
                    )
                )
            elif isinstance(op, WorkflowCallOperation) and op.input_bindings:
                child_executor.with_parameters(
                    cast(
                        dict[str, Any],
                        self._render_http_value(
                            op.input_bindings,
                            self._template_context(node, ctx, graph, loop_item, item_context),
                            "nodes[type=workflow].input_bindings.*",
                        ),
                    )
                )
            child_result = await child_executor.run()
            message = (
                f"{'subflow' if subflow_op is not None else 'workflow'} "
                f"{target_workflow.config.name} "
                f"{'succeeded' if child_result.success else 'failed'}"
            )
            self._log().node(
                node.node_id,
                (
                    f"{boundary} finished: "
                    f"success={child_result.success} log={child_result.log_path}"
                ),
            )
            if subflow_op is not None:
                self._log().event(
                    node.node_id,
                    "subflow_completed",
                    message=message,
                    success=child_result.success,
                    duration_seconds=time.monotonic() - start,
                    data={
                        "subflow": {
                            "component_id": (
                                component_config.id if component_config else subflow_op.component_id
                            ),
                            "version": (
                                component_config.version
                                if component_config
                                else subflow_op.version or ""
                            ),
                            "source_path": str(target_path),
                            "workflow_id": target_workflow.config.id,
                            "log_path": (
                                str(child_result.log_path) if child_result.log_path else ""
                            ),
                        }
                    },
                )
            declared_outputs = (
                self._subflow_declared_outputs(subflow_op, component_config, child_result)
                if subflow_op is not None
                else {}
            )
            output_data: dict[str, object] = {
                "workflow_id": target_workflow.config.id,
                "workflow_name": target_workflow.config.name,
                "component_id": (
                    component_config.id
                    if component_config
                    else (subflow_op.component_id if subflow_op is not None else "")
                ),
                "component_version": (
                    component_config.version
                    if component_config
                    else (subflow_op.version or "" if subflow_op is not None else "")
                ),
                "success": child_result.success,
                "log_path": str(child_result.log_path) if child_result.log_path else "",
                "duration_seconds": child_result.duration_seconds,
            }
            if declared_outputs:
                output_data["outputs"] = declared_outputs
                output_data.update(declared_outputs)
            output_text = (
                json.dumps(declared_outputs, sort_keys=True, default=str)
                if declared_outputs
                else message
            )
            output_data["message"] = output_text
            return NodeOutput(
                node_id=node.node_id,
                success=child_result.success,
                output=output_text,
                exit_code=0 if child_result.success else 1,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                value=declared_outputs or output_text,
                data=output_data,
            )

        if op.type == OperationType.APPROVAL_GATE:
            assert isinstance(op, ApprovalGateOperation)
            template_context = self._template_context(node, ctx, graph, loop_item, item_context)
            require_reference_capability("nodes[type=approval_gate].timeout_seconds")
            resolved_timeout = self._render_reference_value(
                op.timeout_seconds,
                template_context,
                node_id=node.node_id,
                field_path="operation.timeout_seconds",
            )
            timeout_seconds = float(str(resolved_timeout)) if resolved_timeout is not None else None
            require_reference_capability("nodes[type=approval_gate].approvers.*")
            resolved_approvers = self._render_reference_value(
                op.approvers,
                template_context,
                node_id=node.node_id,
                field_path="operation.approvers",
            )
            if not isinstance(resolved_approvers, list):
                raise ValueError(
                    f"Workflow '{self._workflow.config.id}' invocation "
                    f"'{self._log().path.name}', node '{node.node_id}', field "
                    "'operation.approvers': expected an array"
                )
            approvers = [str(value) for value in resolved_approvers]
            require_reference_capability("nodes[type=approval_gate].notify")
            resolved_notify = self._render_reference_value(
                op.notify,
                template_context,
                node_id=node.node_id,
                field_path="operation.notify",
            )
            if not isinstance(resolved_notify, bool):
                raise ValueError(
                    f"Workflow '{self._workflow.config.id}' invocation "
                    f"'{self._log().path.name}', node '{node.node_id}', field "
                    "'operation.notify': expected a boolean"
                )
            require_reference_capability("nodes[type=approval_gate].timeout_decision")
            timeout_decision = str(
                self._render_reference_value(
                    op.timeout_decision,
                    template_context,
                    node_id=node.node_id,
                    field_path="operation.timeout_decision",
                )
            )
            if timeout_decision not in {"reject", "timeout"}:
                raise ValueError("Approval timeout_decision must resolve to 'reject' or 'timeout'")
            subject_manifest_id: str | None = None
            if op.subject is not None:
                require_reference_capability("nodes[type=approval_gate].subject")
                subject = self._render_reference_value(
                    op.subject,
                    template_context,
                    node_id=node.node_id,
                    field_path="operation.subject",
                )
                if isinstance(subject, dict):
                    raw_manifest_id = subject.get("manifest_id")
                    subject_manifest_id = (
                        str(raw_manifest_id) if raw_manifest_id is not None else None
                    )
                elif subject:
                    subject_manifest_id = str(subject)
            message = str(
                self._render_http_value(
                    op.message, template_context, "nodes[type=approval_gate].message"
                )
            )
            assert isinstance(source_op, ApprovalGateOperation)
            secret_values = _collect_secret_values(source_op.message)
            masked_message = _replace_known_secrets(message, secret_values)
            run_id = self._log().path.name
            checkpoint_path = self._approval_checkpoint_path(run_id, node.node_id)
            self._write_approval_checkpoint(
                checkpoint_path,
                node_id=node.node_id,
                ctx=ctx,
                trigger_context=self._trigger_context,
            )
            request = ApprovalRequest(
                workflow_id=self._workflow.config.id,
                run_id=run_id,
                node_id=node.node_id,
                message=masked_message,
                approvers=approvers,
                timeout_seconds=timeout_seconds,
                timeout_decision=cast(Literal["reject", "timeout"], timeout_decision),
                workflow_path=str(self._workflow_path) if self._workflow_path else None,
                log_path=str(self._log().path),
                checkpoint_path=str(checkpoint_path),
                subject_manifest_id=subject_manifest_id,
            )
            request_path = self._approval_store.create_or_update(request)
            approve_command = self._approval_command(
                "approve",
                run_id,
                node.node_id,
                approvers,
            )
            reject_command = self._approval_command(
                "reject",
                run_id,
                node.node_id,
                approvers,
            )
            self._log().node(
                node.node_id,
                f"approval pending: run_id={run_id} request={request_path}",
            )
            self._log().node_output(node.node_id, "approval message", masked_message)
            if resolved_notify:
                try:
                    await self._notification_adapter.send(
                        Notification(
                            title=str(
                                self._render_http_value(
                                    op.notification_title,
                                    template_context,
                                    "nodes[type=approval_gate].notification_title",
                                )
                            ),
                            body=(
                                f"{masked_message}\n\n"
                                f"Approve with: {approve_command}\n"
                                f"Reject with: {reject_command}"
                            ),
                        )
                    )
                except Exception as exc:
                    self._log().node(
                        node.node_id,
                        f"approval notification failed: {exc}",
                    )
            decision = await wait_for_decision(
                self._approval_store,
                self._workflow.config.id,
                run_id,
                node.node_id,
                timeout_seconds=timeout_seconds,
            )
            if decision is None:
                decision_value: ApprovalDecisionValue = (
                    "timeout" if timeout_decision == "timeout" else "rejected"
                )
                self._approval_store.decide(
                    self._workflow.config.id,
                    run_id,
                    node.node_id,
                    decision_value,
                    decided_by="gofer",
                    notes=f"Timed out after {op.timeout_seconds} seconds",
                )
                decided_by = "gofer"
                notes = f"Timed out after {op.timeout_seconds} seconds"
            else:
                decision_value = decision.decision
                decided_by = decision.decided_by
                notes = decision.notes
            approved = decision_value == "approved"
            output_text = (
                f"approval {decision_value} by {decided_by}{': ' + notes if notes else ''}"
            )
            self._log().node(
                node.node_id,
                f"approval decision: decision={decision_value} by={decided_by}",
            )
            self._log().event(
                node.node_id,
                "approval_decision",
                message=f"approval {decision_value}",
                success=approved,
                data={
                    "decision": decision_value,
                    "manifestId": subject_manifest_id,
                },
            )
            if notes:
                self._log().node_output(node.node_id, "approval notes", notes)
            return NodeOutput(
                node_id=node.node_id,
                success=approved,
                output=output_text,
                exit_code=0 if approved else 1,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                value=decision_value,
                data={
                    "status": "decided",
                    "decision": decision_value,
                    "approved": approved,
                    "decidedBy": decided_by,
                    "notes": notes,
                    "message": masked_message,
                    "runId": run_id,
                    "requestPath": str(request_path),
                    "approvers": approvers,
                    "timeoutSeconds": timeout_seconds,
                    "followUpCommand": approve_command,
                    "approveCommand": approve_command,
                    "rejectCommand": reject_command,
                    "manifest_id": subject_manifest_id,
                },
                error=output_text if not approved else None,
            )

        if op.type == OperationType.LOOP:
            assert isinstance(op, LoopOperation)
            source_payload = self._render_reference_value(
                op.source.model_dump(),
                self._template_context(node, ctx, graph, loop_item, item_context),
            )
            if not isinstance(source_payload, dict):
                raise ValueError(f"Node '{node.node_id}' loop source must resolve to an object")
            resolved_source = op.source.__class__.model_validate(source_payload)
            if isinstance(resolved_source, InfiniteFanSource):
                self._log().node(node.node_id, "loop source: infinite")
                return NodeOutput(
                    node_id=node.node_id,
                    success=True,
                    output="infinite loop started",
                    exit_code=0,
                    duration_seconds=time.monotonic() - start,
                    loop_items=[],
                    loop_infinite=True,
                    loop_max_concurrency=1,
                    type=str(op.type),
                    text="infinite loop started",
                )
            items = _resolve_fan_items(
                resolved_source,
                ctx,
                self._limits,
                self._path_base,
                self._data_dir,
                self._require_workflow_path_access,
            )
            output = json.dumps(items, default=str)
            self._log().node(node.node_id, f"loop source: {resolved_source.type}")
            self._log().node(node.node_id, f"loop items: {len(items)}")
            source_path = getattr(resolved_source, "path", None)
            resolved_source_path = (
                str(_resolve_workflow_path(source_path, self._path_base))
                if isinstance(source_path, Path)
                else ""
            )
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                loop_items=items,
                loop_max_concurrency=max(
                    1,
                    min(resolved_source.max_concurrency, self._limits.max_fanout_concurrency),
                ),
                loop_fail_fast=bool(getattr(resolved_source, "fail_fast", False)),
                type=str(op.type),
                items=list(items),
                data={
                    "source_type": resolved_source.type,
                    "count": len(items),
                    "source_path": resolved_source_path,
                    "glob": str(getattr(resolved_source, "glob", "")),
                    "include_content": bool(getattr(resolved_source, "include_content", False)),
                    "max_concurrency": int(getattr(resolved_source, "max_concurrency", 1)),
                    "fail_fast": bool(getattr(resolved_source, "fail_fast", False)),
                },
            )

        if op.type == OperationType.BASH_COMMAND:
            assert isinstance(op, BashCommandOperation)
            stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
            positional_args = self._positional_input_args(
                node,
                ctx,
                graph,
                loop_item,
                item_context,
            )
            rendered_command = self._render_runtime_string(
                op.command,
                "nodes[type=bash_command].command",
                node,
                ctx,
                graph,
                loop_item,
                item_context,
            )
            rendered_command_for_shell = self._bash_command_with_positional_env(
                rendered_command,
                len(positional_args),
            )
            cmd = command_shell_args(rendered_command_for_shell)
            working_dir = (
                _resolve_workflow_path(
                    Path(
                        self._render_runtime_string(
                            str(op.working_dir),
                            "nodes[type=bash_command].working_dir",
                            node,
                            ctx,
                            graph,
                            loop_item,
                            item_context,
                        )
                    ),
                    self._path_base,
                )
                if op.working_dir is not None
                else None
            )
            secret_values = self._trigger_secret_values(ctx, graph)
            masked_command = _replace_known_secrets(rendered_command, secret_values)
            self._log().node(node.node_id, f"command: {masked_command}")
            self._log().node(node.node_id, f"command shell: {cmd[0]}")
            rc, stdout, stderr = await run_subprocess(
                cmd,
                cancel_event=self._cancel_event,
                cwd=working_dir,
                env={
                    **(self._resolved_env(node, ctx, graph, op.env, loop_item, item_context) or {}),
                    **self._positional_input_env(positional_args),
                },
                timeout=node.timeout_seconds,
                stdin=stdin,
                max_output_bytes=self._limits.max_subprocess_output_bytes,
            )
            masked_stdout = _replace_known_secrets(stdout, secret_values)
            masked_stderr = _replace_known_secrets(stderr, secret_values)
            self._log().node_output(node.node_id, "stdout", masked_stdout)
            self._log().node_output(node.node_id, "stderr", masked_stderr)
            return NodeOutput(
                node_id=node.node_id,
                success=rc == 0,
                output=masked_stdout or masked_stderr,
                exit_code=rc,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "stdout": masked_stdout,
                    "stderr": masked_stderr,
                    "command": masked_command,
                },
                error=masked_stderr if rc != 0 else None,
            )

        elif op.type in (OperationType.PYTHON_SCRIPT, OperationType.SHELL_SCRIPT):
            assert isinstance(op, (PythonScriptOperation, ShellScriptOperation))
            interpreter = "python" if op.type == OperationType.PYTHON_SCRIPT else "bash"
            script_path = _resolve_workflow_path(op.script_path, self._path_base)
            self._require_workflow_path_access(script_path, "execute")
            rendered_args = [
                self._render_runtime_string(
                    arg,
                    f"nodes[type={op.type.value}].args.*",
                    node,
                    ctx,
                    graph,
                    loop_item,
                    item_context,
                )
                for arg in op.args
            ]
            positional_args = self._positional_input_args(
                node,
                ctx,
                graph,
                loop_item,
                item_context,
            )
            cmd = [interpreter, str(script_path)] + rendered_args + positional_args
            stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
            secret_values = self._trigger_secret_values(ctx, graph)
            masked_command = _replace_known_secrets(" ".join(cmd), secret_values)
            self._log().node(node.node_id, f"command: {masked_command}")
            rc, stdout, stderr = await run_subprocess(
                cmd,
                cancel_event=self._cancel_event,
                env=self._resolved_env(node, ctx, graph, op.env, loop_item, item_context),
                timeout=node.timeout_seconds,
                stdin=stdin,
                max_output_bytes=self._limits.max_subprocess_output_bytes,
            )
            masked_stdout = _replace_known_secrets(stdout, secret_values)
            masked_stderr = _replace_known_secrets(stderr, secret_values)
            self._log().node_output(node.node_id, "stdout", masked_stdout)
            self._log().node_output(node.node_id, "stderr", masked_stderr)
            return NodeOutput(
                node_id=node.node_id,
                success=rc == 0,
                output=masked_stdout or masked_stderr,
                exit_code=rc,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "stdout": masked_stdout,
                    "stderr": masked_stderr,
                    "script_path": str(script_path),
                },
                error=masked_stderr if rc != 0 else None,
            )

        elif op.type == OperationType.READ_FILE:
            assert isinstance(op, ReadFileOperation)
            path = _resolve_workflow_path(op.path, self._path_base)
            self._require_workflow_path_access(path, "read")
            self._log().node(node.node_id, f"read file: {path}")
            content = read_text_limited(
                path,
                encoding=op.encoding,
                errors=op.errors,
                max_bytes=self._limits.max_file_read_bytes,
            )
            self._log().node_output(node.node_id, "file content", content)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=content,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    **_file_path_data(path),
                    "content": content,
                },
            )

        elif op.type == OperationType.WRITE_FILE:
            assert isinstance(op, WriteFileOperation)
            stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
            path = _resolve_workflow_path(op.path, self._path_base)
            self._require_workflow_path_access(path, "write")
            content = op.content
            if content == "" and stdin is not None:
                content = stdin.decode(op.encoding)
            _prepare_destination(path, op.create_dirs, op.overwrite or op.append)
            mode = "a" if op.append else "w"
            with path.open(mode, encoding=op.encoding) as fh:
                fh.write(content)
            action = "appended" if op.append else "wrote"
            output = f"{action} {len(content)} characters to {path}"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    **_file_path_data(path),
                    "content": content,
                    "action": action,
                    "bytes_written": len(content.encode(op.encoding)),
                    "characters_written": len(content),
                },
            )

        elif op.type == OperationType.COPY_FILE:
            assert isinstance(op, CopyFileOperation)
            source_path = _resolve_workflow_path(op.source_path, self._path_base)
            destination_path = _resolve_workflow_path(
                op.destination_path,
                self._path_base,
            )
            self._require_workflow_path_access(source_path, "read")
            self._require_workflow_path_access(destination_path, "write")
            _copy_path(source_path, destination_path, op.create_dirs, op.overwrite)
            output = f"copied {source_path} to {destination_path}"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "source_path": str(source_path),
                    "destination_path": str(destination_path),
                    "source_name": source_path.name,
                    "destination_name": destination_path.name,
                    "destination_directory": str(destination_path.parent),
                },
            )

        elif op.type == OperationType.MOVE_FILE:
            assert isinstance(op, MoveFileOperation)
            source_path = _resolve_workflow_path(op.source_path, self._path_base)
            destination_path = _resolve_workflow_path(
                op.destination_path,
                self._path_base,
            )
            self._require_workflow_path_access(source_path, "write")
            self._require_workflow_path_access(destination_path, "write")
            _move_path(source_path, destination_path, op.create_dirs, op.overwrite)
            output = f"moved {source_path} to {destination_path}"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "source_path": str(source_path),
                    "destination_path": str(destination_path),
                    "source_name": source_path.name,
                    "destination_name": destination_path.name,
                    "destination_directory": str(destination_path.parent),
                },
            )

        elif op.type == OperationType.DELETE_FILE:
            assert isinstance(op, DeleteFileOperation)
            path = _resolve_workflow_path(op.path, self._path_base)
            self._require_workflow_path_access(path, "write")
            if not path.exists():
                if op.missing_ok:
                    output = f"{path} did not exist"
                    self._log().node(node.node_id, output)
                    return NodeOutput(
                        node_id=node.node_id,
                        success=True,
                        output=output,
                        exit_code=0,
                        duration_seconds=time.monotonic() - start,
                        type=str(op.type),
                        data={"path": str(path), "missing": True},
                    )
                raise FileNotFoundError(path)

            if op.use_trash:
                trash_path = _trash_path(path)
                output = f"moved {path} to trash at {trash_path}"
            else:
                _remove_path(path, recursive=op.recursive)
                output = f"deleted {path}"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    **(_folder_path_data(path) if path.is_dir() else _file_path_data(path)),
                    "trash_path": str(trash_path) if op.use_trash else "",
                    "deleted": not op.use_trash,
                },
            )

        elif op.type == OperationType.FILE:
            assert isinstance(op, FileOperation)
            path = _resolve_workflow_path(op.path, self._path_base)
            self._require_workflow_path_access(path, "read")
            output = str(path)
            self._log().node(node.node_id, f"file path: {output}")
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data=_file_path_data(path),
            )

        elif op.type == OperationType.FOLDER:
            assert isinstance(op, FolderOperation)
            path = _resolve_workflow_path(op.path, self._path_base)
            self._require_workflow_path_access(path, "read")
            output = str(path)
            self._log().node(node.node_id, f"folder path: {output}")
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data=_folder_path_data(path),
            )

        elif op.type == OperationType.OPEN_RESOURCE:
            assert isinstance(op, OpenResourceOperation)
            target = op.target.strip()
            if not target:
                raise ValueError("Open target is required")
            resource_type = op.resource_type
            self._log().node(node.node_id, f"open {resource_type}: {target}")
            if resource_type in {"auto", "url"} and "://" in target:
                opened = webbrowser.open(target)
                if not opened:
                    raise RuntimeError(f"Could not open URL: {target}")
            elif resource_type in {"auto", "file", "folder"}:
                resolved_target = _resolve_workflow_path(Path(target), self._path_base)
                self._require_workflow_path_access(resolved_target, "read")
                target = str(resolved_target)
                if sys.platform == "win32":
                    os.startfile(target)  # type: ignore[attr-defined]
                else:
                    cmd = open_resource_args(target, resource_type, op.args)
                    rc, stdout, stderr = await run_subprocess(
                        cmd,
                        cancel_event=self._cancel_event,
                        timeout=node.timeout_seconds,
                        max_output_bytes=self._limits.max_subprocess_output_bytes,
                    )
                    self._log().node_output(node.node_id, "stdout", stdout)
                    self._log().node_output(node.node_id, "stderr", stderr)
                    if rc != 0:
                        return NodeOutput(
                            node_id=node.node_id,
                            success=False,
                            output=stderr or stdout,
                            exit_code=rc,
                            duration_seconds=time.monotonic() - start,
                            type=str(op.type),
                            data={"target": target, "stdout": stdout, "stderr": stderr},
                            error=stderr or stdout,
                        )
            elif sys.platform == "win32" and resource_type != "app":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                cmd = open_resource_args(target, resource_type, op.args)
                rc, stdout, stderr = await run_subprocess(
                    cmd,
                    cancel_event=self._cancel_event,
                    timeout=node.timeout_seconds,
                    max_output_bytes=self._limits.max_subprocess_output_bytes,
                )
                self._log().node_output(node.node_id, "stdout", stdout)
                self._log().node_output(node.node_id, "stderr", stderr)
                if rc != 0:
                    return NodeOutput(
                        node_id=node.node_id,
                        success=False,
                        output=stderr or stdout,
                        exit_code=rc,
                        duration_seconds=time.monotonic() - start,
                        type=str(op.type),
                        data={"target": target, "stdout": stdout, "stderr": stderr},
                        error=stderr or stdout,
                    )
            output = f"opened {target}"
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={"target": target, "resource_type": resource_type},
            )

        elif op.type == OperationType.PROMPT_FILE:
            assert isinstance(op, PromptFileOperation)
            if op.template_path is not None:
                template_path = _resolve_workflow_path(
                    Path(
                        self._render_runtime_string(
                            str(op.template_path),
                            "nodes[type=prompt_file].template_path",
                            node,
                            ctx,
                            graph,
                            loop_item,
                            item_context,
                        )
                    ),
                    self._path_base,
                )
                self._require_workflow_path_access(template_path, "read")
                template = read_text_limited(
                    template_path,
                    encoding=op.encoding,
                    errors="strict",
                    max_bytes=self._limits.max_file_read_bytes,
                )
            else:
                template = op.template
            variables: dict[str, object] = {}
            require_reference_capability("nodes[type=prompt_file].variables.*")
            for key, value in op.variables.items():
                variables[key] = self._resolve_input_value(
                    node,
                    ctx,
                    graph,
                    value,
                    loop_item,
                    item_context,
                    "nodes[type=prompt_file].variables.*",
                )
            variables.update(self._input_context(node, ctx, graph, loop_item, item_context))
            stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
            if stdin is not None:
                variables["_piped_input"] = stdin.decode(op.encoding)
            require_reference_capability("nodes[type=prompt_file].template")
            rendered = PromptManager._interpolate(template, variables)
            output_path = _resolve_workflow_path(
                Path(
                    self._render_runtime_string(
                        str(op.output_path),
                        "nodes[type=prompt_file].output_path",
                        node,
                        ctx,
                        graph,
                        loop_item,
                        item_context,
                    )
                ),
                self._path_base,
            )
            self._require_workflow_path_access(output_path, "write")
            _prepare_destination(output_path, op.create_dirs, op.overwrite)
            output_path.write_text(rendered, encoding=op.encoding)
            output = f"wrote prompt file {output_path}"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    **_file_path_data(output_path),
                    "content": rendered,
                    "inputs": variables,
                    "prompt": rendered,
                },
            )

        elif op.type == OperationType.COMMON_LLM_TASK:
            assert isinstance(op, CommonLlmTaskOperation)
            rendered_working_dir = Path(
                self._render_runtime_string(
                    str(op.working_dir),
                    "nodes[type=common_llm_task].working_dir",
                    node,
                    ctx,
                    graph,
                    loop_item,
                    item_context,
                )
            )
            agent_config = self._workflow.agents.get(op.agent_id)
            if agent_config is None:
                raise ValueError(f"Agent '{op.agent_id}' not registered in workflow")
            sub = self._subscriptions.get(agent_config.subscription)
            if sub is None:
                raise ValueError(f"No subscription for '{agent_config.subscription}'")
            agent_config = self._agent_config_for_operation(
                agent_config,
                None,
                rendered_working_dir,
            )
            input_ctx = {
                key: self._resolve_input_value(
                    node,
                    ctx,
                    graph,
                    value,
                    loop_item,
                    item_context,
                    "nodes[type=common_llm_task].input_mapping.*",
                )
                for key, value in op.input_mapping.items()
            }
            input_ctx.update(self._input_context(node, ctx, graph, loop_item, item_context))
            stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
            if stdin is not None:
                input_ctx["_piped_input"] = stdin.decode()
            rendered_target = self._render_runtime_string(
                op.target,
                "nodes[type=common_llm_task].target",
                node,
                ctx,
                graph,
                loop_item,
                item_context,
                input_ctx,
            )
            rendered_instructions = self._render_runtime_string(
                op.instructions,
                "nodes[type=common_llm_task].instructions",
                node,
                ctx,
                graph,
                loop_item,
                item_context,
                input_ctx,
            )
            prompt = common_llm_task_prompt(op.task, rendered_target, rendered_instructions)
            schema_name, output_schema = resolve_output_schema(
                op.output_schema, self._workflow.config.output_schemas
            )
            output_instruction = (
                structured_output_instruction(output_schema) if output_schema is not None else None
            )
            memory_key = self._agent_memory_key(node.node_id, loop_item)
            memory = await self._compact_agent_memory_if_needed(
                memory_key,
                op,
                op.memory,
                self._agent_memory(memory_key, op.memory),
                agent_config,
                sub,
            )
            prompt_preview = self._agent_prompt_preview(
                agent_config,
                input_ctx,
                prompt,
                memory,
            )
            if output_instruction:
                prompt_preview = f"{prompt_preview}\n\n{output_instruction}".strip()
            self._log().active_attempt_preview(
                node.node_id,
                {
                    "inputs": input_ctx,
                    "prompt": prompt_preview,
                },
            )
            provider_settings = self._provider_settings_for_operation(agent_config, op)
            agent_timeout = provider_settings.timeout
            common_streamed_thoughts: list[str] = []

            def on_common_agent_thought(thought: str) -> None:
                summary = summarize_thought(thought)
                if not summary:
                    return
                common_streamed_thoughts.append(summary)
                self._log().node_agent_event(node.node_id, "AGENT_THOUGHT", summary)

            result = await Agent(agent_config, sub).run(
                input_ctx,
                cancel_event=self._cancel_event if self._pass_cancel_event else None,
                prompt_override=prompt,
                prompt_suffix=output_instruction,
                memory=memory,
                max_output_bytes=self._limits.max_subprocess_output_bytes,
                timeout=agent_timeout,
                on_thought=on_common_agent_thought,
                provider_settings=provider_settings,
            )
            self._log_agent_result(
                node.node_id,
                result,
                streamed_thought_count=len(common_streamed_thoughts),
            )
            self._remember_agent_result(memory_key, op.memory, result)
            usage = usage_from_metadata(
                provider=result.provider or agent_config.subscription,
                profile=result.profile or agent_config.profile,
                model=result.model or agent_config.model,
                prompt=result.prompt or prompt,
                output=result.output,
                duration_seconds=result.duration_seconds,
                pricing=agent_config.pricing,
                metadata=result.usage_metadata,
            )
            result_output = result.output
            result_message = result.message if result.message is not None else result.output
            success = result.success
            agent_structured_value: object | None = None
            agent_structured_error: str | None = (
                "Provider did not return structured output: "
                + (result.message or result.output or f"exit code {result.exit_code}")
                if output_schema is not None and not result.success
                else None
            )
            agent_repair_audit: list[dict[str, object]] = []
            if success and output_schema is not None:
                (
                    result,
                    agent_structured_value,
                    agent_structured_error,
                    agent_repair_audit,
                ) = await self._repair_structured_agent_result(
                    node.node_id,
                    op,
                    result,
                    output_schema,
                    agent_config,
                    sub,
                    provider_settings,
                    agent_timeout,
                )
                if agent_structured_error is not None:
                    success = False
                    result_output = agent_structured_error
                else:
                    result_output = result.output
            return NodeOutput(
                node_id=node.node_id,
                success=success,
                output=result_output,
                exit_code=result.exit_code if success else 1,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "message": result_message,
                    "agent_id": op.agent_id,
                    **(
                        {
                            "structuredValue": agent_structured_value,
                            "rawResponse": result.output,
                            "outputSchema": schema_name or output_schema,
                        }
                        if output_schema is not None and agent_structured_error is None
                        else {
                            "rawResponse": result.output,
                            "outputSchema": schema_name or output_schema,
                            "structuredOutputError": agent_structured_error,
                        }
                        if output_schema is not None
                        else {}
                    ),
                    **(
                        {"structuredOutputRepairs": agent_repair_audit}
                        if agent_repair_audit
                        else {}
                    ),
                    "usage": usage.model_dump(),
                    "omittedFromNodeOutput": ["inputs", "prompt", "thoughts"],
                },
                value=(agent_structured_value if output_schema is not None else _NODE_VALUE_UNSET),
                error=result_output if not success else None,
            )

        elif op.type == OperationType.LOCAL_VECTORIZE:
            assert isinstance(op, LocalVectorizeOperation)
            source_path = _resolve_workflow_path(op.source_path, self._path_base)
            index_path = _resolve_workflow_path(op.index_path, self._path_base)
            entries_path = _default_vector_entries_path(index_path)
            self._require_workflow_path_access(source_path, "read")
            self._require_workflow_path_access(index_path, "write")
            self._require_workflow_path_access(index_path.parent, "write")
            self._require_workflow_path_access(entries_path, "write")
            temp_changed_entries_path = index_path.with_name(
                f".{index_path.name}.{os.getpid()}.changed.jsonl"
            )
            temp_entries_path = index_path.with_name(
                f".{index_path.name}.{os.getpid()}.entries.jsonl"
            )
            vector_target = source_path
            iterator = (
                (vector_target.rglob(op.glob) if op.recursive else vector_target.glob(op.glob))
                if vector_target.is_dir()
                else iter([vector_target])
            )
            files: list[Path] = []
            discovered: dict[str, dict[str, object]] = {}
            aggregate_bytes = 0
            for file_path in iterator:
                if not file_path.is_file():
                    continue
                self._require_workflow_path_access(file_path, "read")
                if len(files) >= self._limits.max_files_scanned:
                    raise ResourceLimitError(
                        f"local_vectorize scanned files exceeded limit "
                        f"{self._limits.max_files_scanned}"
                    )
                files.append(file_path)
                stat = file_path.stat()
                size = stat.st_size
                require_limit(size, self._limits.max_file_read_bytes, f"{file_path} size")
                aggregate_bytes += size
                if aggregate_bytes > self._limits.max_aggregate_read_bytes:
                    raise ResourceLimitError(
                        "local_vectorize input exceeded aggregate limit "
                        f"{self._limits.max_aggregate_read_bytes} bytes "
                        f"(got {aggregate_bytes} bytes)"
                    )
                file_id = _vector_file_id(file_path)
                discovered[file_id] = {
                    "path": str(file_path),
                    "mtime_ns": stat.st_mtime_ns,
                    "size": size,
                }

            metadata = _vector_index_metadata(op, source_path)
            strategy = _local_vector_strategy(op.embedding_strategy, op.search_strategy)
            if op.mode != "full" and index_path.exists():
                self._require_workflow_path_access(index_path, "read")
            existing_index = (
                None
                if op.mode == "full"
                else _load_vector_index(
                    index_path,
                    self._limits.max_vector_index_bytes,
                    include_entries=False,
                )
            )
            if existing_index is not None and isinstance(existing_index.get("entries_file"), str):
                self._require_workflow_path_access(
                    _vector_entries_path(index_path, existing_index),
                    "read",
                )
            compatible = _vector_index_compatible(existing_index, metadata)
            old_files = _vector_index_file_records(existing_index) if compatible else {}

            file_records: dict[str, dict[str, object]] = {}
            added_files = 0
            updated_files = 0
            unchanged_files = 0
            unreadable_files = 0
            changed_entries_bytes = 0
            temp_changed_entries: Path | None = None
            unchanged_file_ids: set[str] = set()

            def ensure_changed_entries_file() -> Path:
                nonlocal temp_changed_entries
                if temp_changed_entries is None:
                    index_path.parent.mkdir(parents=True, exist_ok=True)
                    temp_changed_entries = temp_changed_entries_path
                    temp_changed_entries.write_text("", encoding="utf-8")
                return temp_changed_entries

            for file_id, file_info in discovered.items():
                file_path = Path(str(file_info["path"]))
                try:
                    file_hash = _hash_file(file_path, self._limits.max_file_read_bytes)
                except OSError as exc:
                    unreadable_files += 1
                    self._log().error(f"{node.node_id} could not read {file_path}: {exc}")
                    continue
                file_info["hash"] = file_hash
                old_record = old_files.get(file_id)
                is_file_unchanged = (
                    old_record is not None
                    and old_record.get("mtime_ns") == file_info["mtime_ns"]
                    and old_record.get("size") == file_info["size"]
                    and old_record.get("hash") == file_hash
                )
                if compatible and is_file_unchanged:
                    reused_record = cast(dict[str, object], old_record)
                    file_records[file_id] = {
                        **reused_record,
                        "path": str(file_path),
                    }
                    unchanged_file_ids.add(file_id)
                    unchanged_files += 1
                    continue
                if op.mode == "validate":
                    continue
                try:
                    text = read_text_limited(
                        file_path,
                        encoding=op.encoding,
                        errors="replace",
                        max_bytes=self._limits.max_file_read_bytes,
                    )
                except OSError as exc:
                    unreadable_files += 1
                    self._log().error(f"{node.node_id} could not read {file_path}: {exc}")
                    continue
                chunks = _chunk_text(text, op.chunk_size, op.chunk_overlap)
                file_records[file_id] = {
                    **file_info,
                    "hash": file_hash,
                    "chunk_count": len(chunks),
                }
                with ensure_changed_entries_file().open("a", encoding="utf-8") as entries_file:
                    for chunk_index, chunk in enumerate(chunks):
                        entry = {
                            **_file_path_data(file_path),
                            "file_id": file_id,
                            "chunk": chunk_index,
                            "text": chunk,
                            "vector": strategy.embed(chunk),
                            "metadata": {
                                "file_path": str(file_path),
                                "file_name": file_path.name,
                                "mtime_ns": file_info["mtime_ns"],
                                "size": file_info["size"],
                                "hash": file_hash,
                            },
                        }
                        changed_entries_bytes += _write_vector_entry(entries_file, entry)
                if old_record is None:
                    added_files += 1
                else:
                    updated_files += 1

            deleted_file_ids = sorted(set(old_files) - set(discovered)) if compatible else []
            stale_file_ids = []
            if op.mode == "validate":
                for file_id, file_info in discovered.items():
                    old_record = old_files.get(file_id)
                    if old_record is None:
                        stale_file_ids.append(file_id)
                        continue
                    if (
                        old_record.get("mtime_ns") != file_info["mtime_ns"]
                        or old_record.get("size") != file_info["size"]
                        or old_record.get("hash") != file_info.get("hash")
                    ):
                        stale_file_ids.append(file_id)
                file_records = old_files if compatible else {}
                unchanged_file_ids = set(old_files)
            current = (
                compatible
                and not added_files
                and not updated_files
                and not deleted_file_ids
                and not stale_file_ids
                and not unreadable_files
            )
            existing_metadata = (
                existing_index.get("metadata", {}) if isinstance(existing_index, dict) else {}
            )
            stored_last_update_time = (
                existing_metadata.get("last_update_time")
                if isinstance(existing_metadata, dict)
                and isinstance(existing_metadata.get("last_update_time"), str)
                else None
            )
            should_write = op.mode != "validate" and (op.mode == "full" or not current)
            last_update_time = (
                datetime.now().astimezone().isoformat()
                if should_write
                else stored_last_update_time or datetime.now().astimezone().isoformat()
            )
            index_metadata = {
                **metadata,
                "last_update_time": last_update_time,
            }
            chunk_count = 0
            for record in file_records.values():
                record_chunk_count = record.get("chunk_count", 0)
                if isinstance(record_chunk_count, int):
                    chunk_count += record_chunk_count
            index_document_data: dict[str, object] = {
                "version": VECTOR_INDEX_VERSION,
                "source_path": str(source_path),
                "glob": op.glob,
                "metadata": index_metadata,
                "files": file_records,
                "entries_file": _default_vector_entries_path(index_path).name,
                "entry_count": chunk_count,
            }
            exact_index_bytes = (
                _vector_index_disk_size(index_path, existing_index)
                if not should_write
                else byte_len(json.dumps(index_document_data, default=str)) + changed_entries_bytes
            )
            if exact_index_bytes > self._limits.max_vector_index_bytes:
                if temp_changed_entries is not None:
                    temp_changed_entries.unlink(missing_ok=True)
                raise ResourceLimitError(
                    "local_vectorize index exceeded limit "
                    f"{self._limits.max_vector_index_bytes} bytes "
                    f"(got {exact_index_bytes} bytes)"
                )
            if op.mode == "validate":
                status = "current" if current else "stale"
                output = (
                    f"validated {index_path}: {status}; "
                    f"{len(file_records)} indexed files, {chunk_count} chunks, "
                    f"{len(stale_file_ids)} stale/new files, {len(deleted_file_ids)} deleted files"
                )
            else:
                if should_write:
                    index_path.parent.mkdir(parents=True, exist_ok=True)
                    written_entry_count = 0
                    sidecar_bytes = 0
                    with temp_entries_path.open("w", encoding="utf-8") as entries_file:
                        if compatible and unchanged_file_ids:
                            for entry in _iter_vector_index_entries(
                                index_path,
                                existing_index,
                                self._limits.max_vector_index_bytes,
                            ):
                                entry_file_id = entry.get("file_id")
                                if not isinstance(entry_file_id, str):
                                    entry_path = entry.get("path")
                                    entry_file_id = (
                                        str(entry_path) if entry_path is not None else ""
                                    )
                                if entry_file_id in unchanged_file_ids:
                                    sidecar_bytes += _write_vector_entry(entries_file, entry)
                                    written_entry_count += 1
                        if temp_changed_entries is not None:
                            with temp_changed_entries.open(encoding="utf-8") as changed_file:
                                for line in changed_file:
                                    sidecar_bytes += byte_len(line)
                                    entries_file.write(line)
                                    written_entry_count += 1
                    index_document_data["entry_count"] = written_entry_count
                    exact_index_bytes = (
                        byte_len(json.dumps(index_document_data, default=str)) + sidecar_bytes
                    )
                    if exact_index_bytes > self._limits.max_vector_index_bytes:
                        temp_entries_path.unlink(missing_ok=True)
                        if temp_changed_entries is not None:
                            temp_changed_entries.unlink(missing_ok=True)
                        raise ResourceLimitError(
                            "local_vectorize index exceeded limit "
                            f"{self._limits.max_vector_index_bytes} bytes "
                            f"(got {exact_index_bytes} bytes)"
                        )
                    index_path.write_text(
                        json.dumps(index_document_data, default=str),
                        encoding="utf-8",
                    )
                    temp_entries_path.replace(entries_path)
                    chunk_count = written_entry_count
                    if temp_changed_entries is not None:
                        temp_changed_entries.unlink(missing_ok=True)
                if current:
                    output = (
                        f"index current: {chunk_count} chunks from "
                        f"{len(file_records)} files at {index_path}"
                    )
                else:
                    output = (
                        f"indexed {chunk_count} chunks from {len(file_records)} files to "
                        f"{index_path} ({added_files} added, {updated_files} updated, "
                        f"{len(deleted_file_ids)} deleted, {unchanged_files} unchanged)"
                    )
            status = "current" if current else "stale" if op.mode == "validate" else "updated"
            self._log().node(node.node_id, output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "message": output,
                    "source_path": str(source_path),
                    "index_path": str(index_path),
                    "mode": op.mode,
                    "current": current,
                    "status": status,
                    "strategy": op.embedding_strategy,
                    "search_strategy": op.search_strategy,
                    "file_count": len(files),
                    "scanned_file_count": len(files),
                    "indexed_file_count": len(file_records),
                    "chunk_count": chunk_count,
                    "index_size_bytes": exact_index_bytes,
                    "last_update_time": last_update_time,
                    "added_files": added_files,
                    "updated_files": updated_files,
                    "unchanged_files": unchanged_files,
                    "deleted_files": len(deleted_file_ids),
                    "stale_files": len(stale_file_ids),
                    "unreadable_files": unreadable_files,
                    "metadata": index_metadata,
                },
            )

        elif op.type == OperationType.LOCAL_SEARCH:
            assert isinstance(op, LocalSearchOperation)
            index_path = _resolve_workflow_path(op.index_path, self._path_base)
            self._require_workflow_path_access(index_path, "read")
            index = _load_vector_index(
                index_path,
                self._limits.max_vector_index_bytes,
                include_entries=False,
            )
            if index is None:
                raise FileNotFoundError(index_path)
            if index.get("entries") is None and isinstance(index.get("entries_file"), str):
                entries_path = _vector_entries_path(index_path, index)
                self._require_workflow_path_access(entries_path, "read")
                index["entries"] = list(
                    _iter_vector_sidecar_entries(
                        entries_path,
                        self._limits.max_vector_index_bytes,
                    )
                )
            index_metadata = index.get("metadata", {})
            if not isinstance(index_metadata, dict):
                index_metadata = {}
            indexed_embedding_strategy = index_metadata.get("embedding_strategy")
            indexed_search_strategy = index_metadata.get("search_strategy")
            strategy = _local_vector_strategy(
                indexed_embedding_strategy
                if isinstance(indexed_embedding_strategy, str)
                else op.embedding_strategy,
                indexed_search_strategy
                if isinstance(indexed_search_strategy, str)
                else op.search_strategy,
            )
            query_vector = strategy.embed(op.query)
            ranked: list[tuple[float, int, dict[str, Any]]] = []
            for entry in index.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                score = strategy.score(query_vector, entry.get("vector", {}))
                entry_chunk = entry.get("chunk", 0)
                chunk_index = int(entry_chunk) if isinstance(entry_chunk, int | str | float) else 0
                ranked.append((score, chunk_index, entry))
            ranked.sort(
                key=lambda item: (
                    -item[0],
                    str(item[2].get("path", "")),
                    item[1],
                )
            )
            result_limit = max(1, min(op.top_k, self._limits.max_fanout_items))
            results: list[dict[str, object]] = []
            result_text_bytes = 0
            for score, _chunk_index, entry in ranked:
                if len(results) >= result_limit:
                    break
                if score < op.score_threshold:
                    continue
                text = truncate_text_bytes(
                    str(entry.get("text", "")),
                    self._limits.max_file_read_bytes,
                    label="search result text",
                )
                result_text_bytes += byte_len(text)
                if result_text_bytes > self._limits.max_aggregate_read_bytes:
                    raise ResourceLimitError(
                        "local_search results exceeded aggregate limit "
                        f"{self._limits.max_aggregate_read_bytes} bytes"
                    )
                result_item = {
                    "score": round(score, 4),
                    "path": entry.get("path"),
                    "chunk": entry.get("chunk"),
                    "text": text,
                }
                if op.include_snippets:
                    result_item["snippet"] = text
                if op.include_file_metadata:
                    result_metadata = entry.get("metadata")
                    if isinstance(result_metadata, dict):
                        result_item["metadata"] = result_metadata
                    else:
                        result_item["metadata"] = {
                            key: entry.get(key)
                            for key in ("file_name", "file_extension", "parent_path", "directory")
                            if key in entry
                        }
                results.append(result_item)
            output = json.dumps(results, indent=2)
            message = f"local_search returned {len(results)} results from {index_path}"
            require_limit(
                byte_len(output),
                self._limits.max_aggregate_read_bytes,
                "local_search output",
            )
            self._log().node_output(node.node_id, "search results", output)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=output,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                items=list(results),
                data={
                    "message": message,
                    "index_path": str(index_path),
                    "query": op.query,
                    "top_k": result_limit,
                    "score_threshold": op.score_threshold,
                    "strategy": strategy.search_strategy,
                    "embedding_strategy": (
                        index_metadata.get("embedding_strategy")
                        if index_metadata
                        else "legacy_hash_token"
                    ),
                    "index_metadata": index_metadata,
                    "results": results,
                },
            )

        elif op.type == OperationType.HTTP_REQUEST:
            assert isinstance(op, HttpRequestOperation)
            assert isinstance(source_op, HttpRequestOperation)
            template_context = self._template_context(node, ctx, graph, loop_item, item_context)

            def resolve_http_config(value: object, field: str) -> object:
                require_reference_capability(f"nodes[type=http_request].{field}")
                return self._render_reference_value(
                    value,
                    template_context,
                    node_id=node.node_id,
                    field_path=f"operation.{field}",
                )

            timeout_seconds = float(str(resolve_http_config(op.timeout_seconds, "timeout_seconds")))
            retry_attempts = int(str(resolve_http_config(op.retry.attempts, "retry.*")))
            retry_backoff = float(str(resolve_http_config(op.retry.backoff_seconds, "retry.*")))
            retry_statuses_value = resolve_http_config(op.retry.retry_on_statuses, "retry.*")
            if not isinstance(retry_statuses_value, list):
                raise ValueError("HTTP retry_on_statuses must resolve to an array")
            retry_statuses = [int(value) for value in retry_statuses_value]
            expected_statuses_value = resolve_http_config(op.expected_statuses, "expected_statuses")
            if not isinstance(expected_statuses_value, list):
                raise ValueError("HTTP expected_statuses must resolve to an array")
            expected_statuses = [int(value) for value in expected_statuses_value]
            network_allowlist_value = resolve_http_config(op.network_allowlist, "network_allowlist")
            if not isinstance(network_allowlist_value, list):
                raise ValueError("HTTP network_allowlist must resolve to an array")
            network_allowlist = [str(value) for value in network_allowlist_value]
            method = str(
                self._render_http_value(
                    op.method, template_context, "nodes[type=http_request].method"
                )
            ).upper()
            url_template = op.url
            url = str(
                self._render_http_value(
                    url_template, template_context, "nodes[type=http_request].url"
                )
            )
            rendered_headers = cast(
                dict[str, object],
                self._render_http_value(
                    op.headers, template_context, "nodes[type=http_request].headers.*"
                ),
            )
            headers = {key: str(value) for key, value in rendered_headers.items()}
            rendered_params = cast(
                dict[str, object],
                self._render_http_value(
                    op.params, template_context, "nodes[type=http_request].params.*"
                ),
            )
            params = {key: str(value) for key, value in rendered_params.items()}
            url = append_query_params(url, params)
            body: bytes | None = None
            rendered_json = self._render_http_value(
                op.json_payload, template_context, "nodes[type=http_request].json.*"
            )
            rendered_body = self._render_http_value(
                op.body, template_context, "nodes[type=http_request].body"
            )
            if rendered_json is not None:
                body = json.dumps(rendered_json).encode()
                headers.setdefault("Content-Type", "application/json")
            elif rendered_body not in (None, ""):
                body = str(rendered_body).encode()

            configured_secret_fields = {field.lower() for field in op.secret_fields}
            secret_values = (
                _collect_secret_values(source_op.url)
                | _collect_secret_values(source_op.headers)
                | _collect_secret_values(source_op.params)
                | _collect_secret_values(source_op.json_payload)
                | _collect_secret_values(source_op.body)
                | _collect_sensitive_template_values(
                    source_op.url,
                    configured_secret_fields,
                    template_context,
                    "url",
                )
                | _collect_sensitive_template_values(
                    source_op.headers,
                    configured_secret_fields,
                    template_context,
                )
                | _collect_sensitive_template_values(
                    source_op.params,
                    configured_secret_fields,
                    template_context,
                )
                | _collect_sensitive_template_values(
                    source_op.json_payload,
                    configured_secret_fields,
                    template_context,
                )
                | _collect_sensitive_template_values(
                    source_op.body,
                    configured_secret_fields,
                    template_context,
                    "body",
                )
                | (
                    _collect_leaf_strings(url)
                    if _is_sensitive_field("url", configured_secret_fields)
                    else set()
                )
                | _collect_configured_secret_values(
                    headers,
                    configured_secret_fields,
                )
                | _collect_configured_secret_values(
                    params,
                    configured_secret_fields,
                )
            )
            if rendered_json is not None:
                secret_values.update(
                    _collect_configured_secret_values(
                        rendered_json,
                        configured_secret_fields,
                    )
                )
            elif isinstance(rendered_body, str):
                secret_values.update(
                    _collect_configured_secret_text_values(
                        rendered_body,
                        configured_secret_fields,
                    )
                )
            else:
                secret_values.update(
                    _collect_configured_secret_values(
                        rendered_body,
                        configured_secret_fields,
                    )
                )
            sensitive_query_keys = {
                key.lower()
                for key, value in op.params.items()
                if _secret_reference_names(value)
                or _is_sensitive_field(key, configured_secret_fields)
            }
            url_is_sensitive = bool(_secret_reference_names(url_template)) or _is_sensitive_field(
                "url",
                configured_secret_fields,
            )
            masked_url = _mask_http_url(
                url,
                configured=configured_secret_fields,
                secret_values=secret_values,
                url_sensitive=url_is_sensitive,
                sensitive_query_keys=sensitive_query_keys,
            )
            masked_headers = _mask_http_value(
                headers,
                configured_secret_fields,
                secret_values=secret_values,
            )
            if rendered_json is not None:
                masked_body = _mask_http_value(
                    rendered_json,
                    configured_secret_fields,
                    secret_values=secret_values,
                )
            elif isinstance(rendered_body, str):
                masked_body = _mask_http_text(
                    rendered_body,
                    configured_secret_fields,
                    secret_values=secret_values,
                )
            else:
                masked_body = _mask_http_value(
                    rendered_body,
                    configured_secret_fields,
                    secret_values=secret_values,
                )
            self._log().node(node.node_id, f"http request: {method} {masked_url}")
            self._log().node_output(
                node.node_id,
                "request",
                json.dumps(
                    {"headers": masked_headers, "body": masked_body},
                    default=str,
                    indent=2,
                ),
            )
            try:
                policy_result = validate_http_request_url(
                    url,
                    allowlist=network_allowlist,
                )
            except NetworkPolicyViolation as exc:
                error_text = str(exc)
                self._log().node(node.node_id, error_text)
                return NodeOutput(
                    node_id=node.node_id,
                    success=False,
                    output=error_text,
                    exit_code=1,
                    duration_seconds=time.monotonic() - start,
                    type=str(op.type),
                    value=error_text,
                    data={
                        "url": masked_url,
                        "method": method,
                        "attempts": 0,
                        "error": error_text,
                    },
                    error=error_text,
                )
            if policy_result.allowed_by is not None:
                self._log().node(
                    node.node_id,
                    f"http request network allowlist matched: {policy_result.allowed_by}",
                )

            attempts = max(1, retry_attempts)
            response = None
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    response = await self._http_client.send(
                        HttpRequest(
                            method=method,
                            url=url,
                            headers=headers,
                            body=body,
                            timeout_seconds=timeout_seconds,
                            network_allowlist=network_allowlist,
                        )
                    )
                    last_error = None
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    response = None
                    masked_error = _replace_known_secrets(str(exc), secret_values)
                    self._log().node(
                        node.node_id,
                        f"http request error on attempt {attempt}: {masked_error}",
                    )
                    if attempt >= attempts:
                        break
                    if retry_backoff > 0:
                        await anyio.sleep(retry_backoff)
                    continue
                should_retry = attempt < attempts and response.status in set(retry_statuses)
                if not should_retry:
                    break
                if retry_backoff > 0:
                    await anyio.sleep(retry_backoff)

            if response is None:
                error_text = "HTTP request did not produce a response"
                if last_error is not None:
                    error_text = (
                        f"HTTP request failed after {attempts} "
                        f"attempt{'s' if attempts != 1 else ''}: {last_error}"
                    )
                error_text = _replace_known_secrets(error_text, secret_values)
                return NodeOutput(
                    node_id=node.node_id,
                    success=False,
                    output=error_text,
                    exit_code=1,
                    duration_seconds=time.monotonic() - start,
                    type=str(op.type),
                    value=error_text,
                    data={
                        "url": masked_url,
                        "method": method,
                        "attempts": attempts,
                        "error": error_text,
                    },
                    error=error_text,
                )
            redirect_location = response.headers.get("Location") or response.headers.get("location")
            if 300 <= response.status < 400 and redirect_location:
                redirect_url = urllib.parse.urljoin(url, redirect_location)
                try:
                    validate_http_request_url(
                        redirect_url,
                        allowlist=network_allowlist,
                    )
                except NetworkPolicyViolation as exc:
                    error_text = str(exc)
                    self._log().node(node.node_id, error_text)
                    return NodeOutput(
                        node_id=node.node_id,
                        success=False,
                        output=error_text,
                        exit_code=1,
                        duration_seconds=time.monotonic() - start,
                        type=str(op.type),
                        value=error_text,
                        data={
                            "url": masked_url,
                            "method": method,
                            "attempts": attempts,
                            "status": response.status,
                            "error": error_text,
                        },
                        error=error_text,
                    )
            body_text = response.body.decode("utf-8", errors="replace")
            parsed_json: object | None = None
            json_error: json.JSONDecodeError | None = None
            if op.response_mode in {"auto", "json"} or op.output_mapping:
                try:
                    parsed_json = json.loads(body_text) if body_text else None
                except json.JSONDecodeError as exc:
                    if op.response_mode == "json":
                        json_error = exc
            masked_json = _mask_http_value(
                parsed_json,
                configured_secret_fields,
                secret_values=secret_values,
            )
            if parsed_json is not None:
                masked_body_text = json.dumps(masked_json, default=str)
            else:
                masked_body_text = _mask_http_text(
                    body_text,
                    configured_secret_fields,
                    secret_values=secret_values,
                )
            response_data: dict[str, object] = {
                "status": response.status,
                "headers": dict(response.headers),
                "body": body_text,
                "json": parsed_json,
            }
            selected = self._http_output_mapping(response_data, op.output_mapping)
            if op.response_mode == "json" and parsed_json is not None:
                output = json.dumps(parsed_json, default=str)
                output_value: object = parsed_json
            elif op.response_mode == "none":
                output = ""
                output_value = None
            else:
                output = body_text
                output_value = body_text
            success = response.status in set(expected_statuses) and json_error is None
            error_text = body_text
            if json_error is not None:
                error_text = (
                    f"Invalid JSON response: {json_error.msg} "
                    f"at line {json_error.lineno} column {json_error.colno}; "
                    f"body={body_text}"
                )
            self._log().node(
                node.node_id,
                f"http response: status={response.status} success={success}",
            )
            self._log().node_output(node.node_id, "response body", masked_body_text)
            return NodeOutput(
                node_id=node.node_id,
                success=success,
                output=output,
                exit_code=0 if success else 1,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                value=output_value,
                data={
                    **response_data,
                    "selected": selected,
                    "responsePreview": {
                        **response_data,
                        "selected": selected,
                        "url": url,
                        "method": method,
                    },
                    "url": masked_url,
                    "method": method,
                },
                error=error_text if not success else None,
            )

        elif op.type == OperationType.NOTIFICATION:
            assert isinstance(op, NotificationOperation)
            assert isinstance(source_op, NotificationOperation)
            template_context = self._template_context(node, ctx, graph, loop_item, item_context)
            op_data = source_op.model_dump()
            secret_values = _collect_secret_values(op_data)
            title = str(
                self._render_http_value(
                    op.title, template_context, "nodes[type=notification].title"
                )
            )
            notification_body = str(
                self._render_http_value(op.body, template_context, "nodes[type=notification].body")
            )
            webhook_url = (
                str(
                    self._render_http_value(
                        op.webhook_url,
                        template_context,
                        "nodes[type=notification].webhook_url",
                    )
                )
                if op.webhook_url is not None
                else None
            )
            headers = cast(
                dict[str, str],
                self._render_http_value(
                    op.headers, template_context, "nodes[type=notification].headers.*"
                ),
            )
            payload = self._render_http_value(
                op.payload, template_context, "nodes[type=notification].payload.*"
            )
            email_from = (
                str(
                    self._render_http_value(
                        op.email_from,
                        template_context,
                        "nodes[type=notification].email_from",
                    )
                )
                if op.email_from is not None
                else None
            )
            email_to = cast(
                list[str],
                self._render_http_value(
                    op.email_to, template_context, "nodes[type=notification].email_to.*"
                ),
            )
            smtp_host = (
                str(
                    self._render_http_value(
                        op.smtp_host,
                        template_context,
                        "nodes[type=notification].smtp_host",
                    )
                )
                if op.smtp_host is not None
                else None
            )
            smtp_username = (
                str(
                    self._render_http_value(
                        op.smtp_username,
                        template_context,
                        "nodes[type=notification].smtp_username",
                    )
                )
                if op.smtp_username is not None
                else None
            )
            smtp_password = (
                str(
                    self._render_http_value(
                        op.smtp_password,
                        template_context,
                        "nodes[type=notification].smtp_password",
                    )
                )
                if op.smtp_password is not None
                else None
            )

            def resolve_notification_config(value: object, field: str) -> object:
                require_reference_capability(f"nodes[type=notification].{field}")
                return self._render_reference_value(
                    value,
                    template_context,
                    node_id=node.node_id,
                    field_path=f"operation.{field}",
                )

            channel = str(resolve_notification_config(op.channel, "channel"))
            urgency = str(resolve_notification_config(op.urgency, "urgency"))
            if channel not in {"desktop", "slack", "teams", "webhook", "email"}:
                raise ValueError(f"Invalid notification channel: {channel}")
            if urgency not in {"low", "normal", "critical"}:
                raise ValueError(f"Invalid notification urgency: {urgency}")
            smtp_port = int(str(resolve_notification_config(op.smtp_port, "smtp_port")))
            smtp_starttls_value = resolve_notification_config(op.smtp_starttls, "smtp_starttls")
            if not isinstance(smtp_starttls_value, bool):
                raise ValueError("Notification smtp_starttls must resolve to a boolean")
            timeout_seconds = float(
                str(resolve_notification_config(op.timeout_seconds, "timeout_seconds"))
            )
            expected_statuses_value = resolve_notification_config(
                op.expected_statuses, "expected_statuses"
            )
            if not isinstance(expected_statuses_value, list):
                raise ValueError("Notification expected_statuses must resolve to an array")
            expected_statuses = [int(value) for value in expected_statuses_value]
            network_allowlist_value = resolve_notification_config(
                op.network_allowlist, "network_allowlist"
            )
            if not isinstance(network_allowlist_value, list):
                raise ValueError("Notification network_allowlist must resolve to an array")
            network_allowlist = [str(value) for value in network_allowlist_value]
            retry_statuses_value = resolve_notification_config(
                op.retry.retry_on_statuses, "retry.*"
            )
            if not isinstance(retry_statuses_value, list):
                raise ValueError("Notification retry_on_statuses must resolve to an array")
            retry = HttpRetryPolicy(
                attempts=int(str(resolve_notification_config(op.retry.attempts, "retry.*"))),
                backoff_seconds=float(
                    str(resolve_notification_config(op.retry.backoff_seconds, "retry.*"))
                ),
                retry_on_statuses=[int(value) for value in retry_statuses_value],
            )
            masked_title = _replace_known_secrets(title, secret_values)
            masked_body = _replace_known_secrets(notification_body, secret_values)
            masked_webhook_url = "***" if webhook_url is not None else None
            masked_headers = cast(
                dict[str, object],
                _mask_http_value(headers, set(), secret_values=secret_values),
            )
            masked_payload = _mask_http_value(payload, set(), secret_values=secret_values)
            masked_email_from = (
                _replace_known_secrets(email_from, secret_values)
                if email_from is not None
                else None
            )
            masked_email_to = cast(
                list[object],
                _mask_http_value(email_to, set(), secret_values=secret_values),
            )
            masked_smtp_host = (
                _replace_known_secrets(smtp_host, secret_values) if smtp_host is not None else None
            )
            masked_smtp_username = "***" if smtp_username is not None else None
            notification = Notification(
                title=title,
                body=notification_body,
                channel=channel,
                urgency=urgency,
                webhook_url=webhook_url,
                headers=headers,
                payload=payload,
                email_from=email_from,
                email_to=email_to,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                smtp_starttls=smtp_starttls_value,
                timeout_seconds=timeout_seconds,
                retry=retry,
                expected_statuses=expected_statuses,
                network_allowlist=network_allowlist,
            )
            await self._notification_adapter.send(notification)
            self._log().node(node.node_id, f"notification sent: {channel}")
            self._log().node_output(node.node_id, "notification body", masked_body)
            return NodeOutput(
                node_id=node.node_id,
                success=True,
                output=masked_body,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                value=masked_body,
                data={
                    "title": masked_title,
                    "body": masked_body,
                    "channel": channel,
                    "urgency": urgency,
                    "webhookUrl": masked_webhook_url,
                    "headers": masked_headers,
                    "payload": masked_payload,
                    "emailFrom": masked_email_from,
                    "emailTo": masked_email_to,
                    "smtpHost": masked_smtp_host,
                    "smtpPort": smtp_port,
                    "smtpUsername": masked_smtp_username,
                    "timeoutSeconds": timeout_seconds,
                    "expectedStatuses": expected_statuses,
                    "networkAllowlist": network_allowlist,
                },
            )

        elif op.type == OperationType.AGENT:
            assert isinstance(op, AgentOperation)
            rendered_working_dir = Path(
                self._render_runtime_string(
                    str(op.working_dir),
                    "nodes[type=agent].working_dir",
                    node,
                    ctx,
                    graph,
                    loop_item,
                    item_context,
                )
            )
            rendered_prompt_path = (
                Path(
                    self._render_runtime_string(
                        str(op.prompt_path),
                        "nodes[type=agent].prompt_path",
                        node,
                        ctx,
                        graph,
                        loop_item,
                        item_context,
                    )
                )
                if op.prompt_path is not None
                else None
            )
            agent_config = self._workflow.agents.get(op.agent_id)
            if agent_config is None:
                raise ValueError(f"Agent '{op.agent_id}' not registered in workflow")
            sub = self._subscriptions.get(agent_config.subscription)
            if sub is None:
                raise ValueError(f"No subscription for '{agent_config.subscription}'")
            agent_config = self._agent_config_for_operation(
                agent_config,
                rendered_prompt_path,
                rendered_working_dir,
            )

            agent_input_ctx: dict[str, object] = {
                k: self._resolve_input_value(
                    node,
                    ctx,
                    graph,
                    v,
                    loop_item,
                    item_context,
                    "nodes[type=agent].input_mapping.*",
                )
                for k, v in op.input_mapping.items()
            }
            explicit_inputs = bool(node.inputs)
            agent_input_ctx.update(self._input_context(node, ctx, graph, loop_item, item_context))
            if not explicit_inputs:
                stdin = self._resolve_pipe_stdin(node, ctx, graph, loop_item, item_context)
                if stdin is not None:
                    agent_input_ctx["_piped_input"] = stdin.decode()

            prompt_override = f"/{op.skill_name.strip().lstrip('/')}" if op.skill_name else None
            schema_name, output_schema = resolve_output_schema(
                op.output_schema, self._workflow.config.output_schemas
            )
            output_instruction = (
                structured_output_instruction(output_schema) if output_schema is not None else None
            )
            if op.fan_source is not None:
                self._log().node(
                    node.node_id,
                    "agent fan_source is deprecated; use a loop node feeding this agent",
                )
            if op.dynamic_count != 1:
                self._log().node(
                    node.node_id,
                    "agent dynamic_count is deprecated; use a loop node feeding this agent",
                )
            if loop_item and not explicit_inputs:
                agent_input_ctx = {**agent_input_ctx, **loop_item}
            memory_key = self._agent_memory_key(node.node_id, loop_item)
            memory = await self._compact_agent_memory_if_needed(
                memory_key,
                op,
                op.memory,
                self._agent_memory(memory_key, op.memory),
                agent_config,
                sub,
            )
            prompt_preview = self._agent_prompt_preview(
                agent_config,
                agent_input_ctx,
                prompt_override,
                memory,
            )
            if output_instruction:
                prompt_preview = f"{prompt_preview}\n\n{output_instruction}".strip()
            self._log().active_attempt_preview(
                node.node_id,
                {
                    "inputs": agent_input_ctx,
                    "prompt": prompt_preview,
                },
            )
            provider_settings = self._provider_settings_for_operation(agent_config, op)
            agent_timeout = provider_settings.timeout
            agent_streamed_thoughts: list[str] = []

            def on_agent_thought(thought: str) -> None:
                summary = summarize_thought(thought)
                if not summary:
                    return
                agent_streamed_thoughts.append(summary)
                self._log().node_agent_event(node.node_id, "AGENT_THOUGHT", summary)

            result = await Agent(agent_config, sub).run(
                agent_input_ctx,
                cancel_event=self._cancel_event if self._pass_cancel_event else None,
                prompt_override=prompt_override,
                prompt_suffix=output_instruction,
                memory=memory,
                max_output_bytes=self._limits.max_subprocess_output_bytes,
                timeout=agent_timeout,
                on_thought=on_agent_thought,
                provider_settings=provider_settings,
            )
            self._log_agent_result(
                node.node_id,
                result,
                streamed_thought_count=len(agent_streamed_thoughts),
            )
            self._remember_agent_result(memory_key, op.memory, result)
            usage = usage_from_metadata(
                provider=result.provider or agent_config.subscription,
                profile=result.profile or agent_config.profile,
                model=result.model or agent_config.model,
                prompt=result.prompt or "",
                output=result.output,
                duration_seconds=result.duration_seconds,
                pricing=agent_config.pricing,
                metadata=result.usage_metadata,
            )
            result_output = result.output
            result_message = result.message if result.message is not None else result.output
            success = result.success
            structured_value: object | None = None
            structured_error: str | None = (
                "Provider did not return structured output: "
                + (result.message or result.output or f"exit code {result.exit_code}")
                if output_schema is not None and not result.success
                else None
            )
            repair_audit: list[dict[str, object]] = []
            if success and output_schema is not None:
                (
                    result,
                    structured_value,
                    structured_error,
                    repair_audit,
                ) = await self._repair_structured_agent_result(
                    node.node_id,
                    op,
                    result,
                    output_schema,
                    agent_config,
                    sub,
                    provider_settings,
                    agent_timeout,
                )
                if structured_error is not None:
                    success = False
                    result_output = structured_error
                else:
                    result_output = result.output
            return NodeOutput(
                node_id=node.node_id,
                success=success,
                output=result_output,
                exit_code=result.exit_code if success else 1,
                duration_seconds=time.monotonic() - start,
                type=str(op.type),
                data={
                    "message": result_message,
                    "agent_id": op.agent_id,
                    **(
                        {
                            "structuredValue": structured_value,
                            "rawResponse": result.output,
                            "outputSchema": schema_name or output_schema,
                        }
                        if output_schema is not None and structured_error is None
                        else {
                            "rawResponse": result.output,
                            "outputSchema": schema_name or output_schema,
                            "structuredOutputError": structured_error,
                        }
                        if output_schema is not None
                        else {}
                    ),
                    **({"structuredOutputRepairs": repair_audit} if repair_audit else {}),
                    "usage": usage.model_dump(),
                    "omittedFromNodeOutput": ["inputs", "prompt", "thoughts"],
                },
                value=(structured_value if output_schema is not None else _NODE_VALUE_UNSET),
                error=result_output if not success else None,
            )

        raise ValueError(f"Unknown operation type: {op.type}")

    async def _repair_structured_agent_result(
        self,
        node_id: str,
        op: AgentOperation | CommonLlmTaskOperation,
        result: AgentResult,
        schema: dict[str, Any],
        agent_config: AgentConfig,
        subscription: Subscription,
        provider_settings: ResolvedProviderSettings,
        timeout: float | None,
    ) -> tuple[AgentResult, object | None, str | None, list[dict[str, object]]]:
        audit: list[dict[str, object]] = []
        try:
            return result, parse_and_validate_output(result.output, schema), None, audit
        except StructuredOutputError as exc:
            validation_error = str(exc)

        if op.repair_attempts == 0:
            self._log().node(node_id, f"structured output validation failed: {validation_error}")
            return result, None, validation_error, audit

        audit.append(
            {
                "attempt": 0,
                "rawResponse": result.output,
                "validationError": validation_error,
            }
        )
        for attempt in range(1, op.repair_attempts + 1):
            repair_prompt = (
                "Your previous response did not satisfy the required structured output. "
                "Correct it without repeating any unrelated work. Return only the corrected "
                "JSON value.\n\nValidation error:\n"
                f"{validation_error}\n\nPrevious response:\n{result.output}\n\n"
                f"{structured_output_instruction(schema)}"
            )
            self._log().event(
                node_id,
                "structured_output_repair",
                attempt=attempt,
                message=f"structured output repair attempt {attempt}/{op.repair_attempts}",
            )
            result = await Agent(agent_config, subscription).run(
                {},
                cancel_event=self._cancel_event if self._pass_cancel_event else None,
                prompt_override=repair_prompt,
                max_output_bytes=self._limits.max_subprocess_output_bytes,
                timeout=timeout,
                provider_settings=provider_settings,
            )
            repair_usage = usage_from_metadata(
                provider=result.provider or agent_config.subscription,
                profile=result.profile or agent_config.profile,
                model=result.model or agent_config.model,
                prompt=result.prompt or repair_prompt,
                output=result.output,
                duration_seconds=result.duration_seconds,
                pricing=agent_config.pricing,
                metadata=result.usage_metadata,
            )
            entry: dict[str, object] = {
                "attempt": attempt,
                "rawResponse": result.output,
                "usage": repair_usage.model_dump(),
            }
            if not result.success:
                validation_error = "Structured output repair provider call failed: " + (
                    result.message or result.output or f"exit code {result.exit_code}"
                )
                entry["validationError"] = validation_error
                audit.append(entry)
                continue
            try:
                value = parse_and_validate_output(result.output, schema)
            except StructuredOutputError as exc:
                validation_error = str(exc)
                entry["validationError"] = validation_error
                audit.append(entry)
                continue
            entry["validated"] = True
            audit.append(entry)
            self._log().node(node_id, f"structured output repair succeeded on attempt {attempt}")
            return result, value, None, audit

        message = (
            f"Structured output repair exhausted after {op.repair_attempts} attempt(s): "
            f"{validation_error}"
        )
        self._log().node(node_id, message)
        return result, None, message, audit

    def _bounded_agent_thought(self, node_id: str, value: str) -> str:
        return truncate_text_bytes(
            value,
            self._limits.max_log_message_bytes,
            f"{node_id} AGENT_THOUGHT",
        )

    def _stop_requested(self) -> bool:
        return bool(
            (self._cancel_event and self._cancel_event.is_set())
            or (self._stop_file and self._stop_file.exists())
            or (self._run_stop_file and self._run_stop_file.exists())
        )

    def _failure_reason(self, outputs: dict[str, NodeOutput]) -> str:
        for node_id, output in outputs.items():
            if not output.success:
                if output.type == str(OperationType.HTTP_REQUEST):
                    detail = (output.error or "").strip()
                    if not detail:
                        preview = output.data.get("responsePreview")
                        if isinstance(preview, dict):
                            preview_body = preview.get("body")
                            if isinstance(preview_body, str):
                                detail = preview_body.strip()
                else:
                    detail = ""
                if not detail:
                    detail = output.output.strip() or f"exit code {output.exit_code}"
                return f"node {node_id} failed: {detail}"
        return "workflow halted before all nodes completed"
