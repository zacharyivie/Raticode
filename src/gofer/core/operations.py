from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainValidator, WithJsonSchema, model_validator

from gofer.core.runtime_values import (
    RuntimeBool,
    RuntimeFloat,
    RuntimeInt,
    RuntimeIntList,
    RuntimeObjectList,
    RuntimeObjectMap,
    RuntimeStringList,
    RuntimeStringMap,
    is_exact_runtime_reference,
    runtime_literal_schema,
    runtime_literal_validator,
)


class OperationType(StrEnum):
    START = "start"
    PASS = "pass"
    FAIL = "fail"
    BREAK = "break"
    LOOP = "loop"
    PYTHON_SCRIPT = "python_script"
    SHELL_SCRIPT = "shell_script"
    BASH_COMMAND = "bash_command"
    AGENT = "agent"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    COPY_FILE = "copy_file"
    MOVE_FILE = "move_file"
    DELETE_FILE = "delete_file"
    FILE = "file"
    FOLDER = "folder"
    OPEN_RESOURCE = "open_resource"
    PROMPT_FILE = "prompt_file"
    COMMON_LLM_TASK = "common_llm_task"
    LOCAL_VECTORIZE = "local_vectorize"
    LOCAL_SEARCH = "local_search"
    HTTP_REQUEST = "http_request"
    APPROVAL_GATE = "approval_gate"
    NOTIFICATION = "notification"
    WORKFLOW = "workflow"
    SUBFLOW = "subflow"


class CountFanSource(BaseModel):
    type: Literal["count"]
    # Legacy dynamic-count paths and blank values remain supported here. New
    # typed references use the same string slot and are resolved before use.
    count: int | str | None = 1
    max_concurrency: RuntimeInt = 1
    fail_fast: RuntimeBool = False


class TabularFanSource(BaseModel):
    type: Literal["tabular"]
    path: Path
    max_concurrency: RuntimeInt = 1
    fail_fast: RuntimeBool = False


class DirectoryFanSource(BaseModel):
    type: Literal["directory"]
    path: Path
    glob: str = "*"
    include_content: RuntimeBool = False
    max_concurrency: RuntimeInt = 1
    fail_fast: RuntimeBool = False


class TriggerEventsFanSource(BaseModel):
    type: Literal["trigger_events"]
    include_content: RuntimeBool = False
    max_concurrency: RuntimeInt = 1
    fail_fast: RuntimeBool = False


class InfiniteFanSource(BaseModel):
    type: Literal["infinite"]
    max_concurrency: RuntimeInt = 1
    fail_fast: RuntimeBool = False


FanSource = Annotated[
    CountFanSource
    | TabularFanSource
    | DirectoryFanSource
    | TriggerEventsFanSource
    | InfiniteFanSource,
    Field(discriminator="type"),
]


class PythonScriptOperation(BaseModel):
    type: Literal[OperationType.PYTHON_SCRIPT]
    script_path: Path
    args: RuntimeStringList = []
    env: RuntimeStringMap = {}


class ShellScriptOperation(BaseModel):
    type: Literal[OperationType.SHELL_SCRIPT]
    script_path: Path
    args: RuntimeStringList = []
    env: RuntimeStringMap = {}


class BashCommandOperation(BaseModel):
    type: Literal[OperationType.BASH_COMMAND]
    command: str
    working_dir: Path | None = None
    env: RuntimeStringMap = {}


class StartOperation(BaseModel):
    type: Literal[OperationType.START]


class PassOperation(BaseModel):
    type: Literal[OperationType.PASS]
    message: str = ""


class FailOperation(BaseModel):
    type: Literal[OperationType.FAIL]
    message: str = ""


class BreakOperation(BaseModel):
    type: Literal[OperationType.BREAK]
    message: str = ""


class LoopOperation(BaseModel):
    type: Literal[OperationType.LOOP]
    source: FanSource


class ReadFileOperation(BaseModel):
    type: Literal[OperationType.READ_FILE]
    path: Path
    encoding: str = "utf-8"
    errors: str = "strict"


class WriteFileOperation(BaseModel):
    type: Literal[OperationType.WRITE_FILE]
    path: Path
    content: str = ""
    encoding: str = "utf-8"
    create_dirs: RuntimeBool = True
    overwrite: RuntimeBool = True
    append: RuntimeBool = False


class CopyFileOperation(BaseModel):
    type: Literal[OperationType.COPY_FILE]
    source_path: Path
    destination_path: Path
    create_dirs: RuntimeBool = True
    overwrite: RuntimeBool = False


class MoveFileOperation(BaseModel):
    type: Literal[OperationType.MOVE_FILE]
    source_path: Path
    destination_path: Path
    create_dirs: RuntimeBool = True
    overwrite: RuntimeBool = False


class DeleteFileOperation(BaseModel):
    type: Literal[OperationType.DELETE_FILE]
    path: Path
    use_trash: RuntimeBool = True
    recursive: RuntimeBool = False
    missing_ok: RuntimeBool = False


class FileOperation(BaseModel):
    type: Literal[OperationType.FILE]
    path: Path


class FolderOperation(BaseModel):
    type: Literal[OperationType.FOLDER]
    path: Path


class OpenResourceOperation(BaseModel):
    type: Literal[OperationType.OPEN_RESOURCE]
    target: str
    resource_type: Annotated[
        str,
        PlainValidator(runtime_literal_validator("auto", "file", "folder", "url", "app")),
        WithJsonSchema(runtime_literal_schema("auto", "file", "folder", "url", "app")),
    ] = "auto"
    args: RuntimeStringList = []


class PromptFileOperation(BaseModel):
    type: Literal[OperationType.PROMPT_FILE]
    output_path: Path
    template: str = ""
    template_path: Path | None = None
    variables: RuntimeStringMap = {}
    encoding: str = "utf-8"
    create_dirs: RuntimeBool = True
    overwrite: RuntimeBool = True


class CommonLlmTaskOperation(BaseModel):
    type: Literal[OperationType.COMMON_LLM_TASK]
    agent_id: str
    task: Annotated[
        str,
        PlainValidator(
            runtime_literal_validator(
                "review", "summarize", "explain", "extract", "rewrite", "classify"
            )
        ),
        WithJsonSchema(
            runtime_literal_schema(
                "review", "summarize", "explain", "extract", "rewrite", "classify"
            )
        ),
    ] = "summarize"
    target: str = ""
    instructions: str = ""
    working_dir: Path
    profile: str | None = None
    model: str | None = None
    effort: str | None = None
    timeout: RuntimeFloat | None = None
    memory: Annotated[
        str,
        PlainValidator(runtime_literal_validator("none", "run", "all")),
        WithJsonSchema(runtime_literal_schema("none", "run", "all")),
    ] = "none"
    input_mapping: RuntimeStringMap = {}
    output_schema: str | dict[str, Any] | None = None
    repair_attempts: RuntimeInt = 0

    @model_validator(mode="after")
    def _validate_repair_attempts(self) -> CommonLlmTaskOperation:
        if isinstance(self.repair_attempts, int) and not 0 <= self.repair_attempts <= 3:
            raise ValueError("repair_attempts must be between 0 and 3")
        return self


class LocalVectorizeOperation(BaseModel):
    type: Literal[OperationType.LOCAL_VECTORIZE]
    source_path: Path
    index_path: Path
    glob: str = "**/*"
    recursive: RuntimeBool = True
    chunk_size: RuntimeInt = 1200
    chunk_overlap: RuntimeInt = 120
    encoding: str = "utf-8"
    mode: Annotated[
        str,
        PlainValidator(runtime_literal_validator("incremental", "full", "validate", "compact")),
        WithJsonSchema(runtime_literal_schema("incremental", "full", "validate", "compact")),
    ] = "incremental"
    embedding_strategy: str = "hash_token_v1"
    search_strategy: str = "cosine_v1"


class LocalSearchOperation(BaseModel):
    type: Literal[OperationType.LOCAL_SEARCH]
    index_path: Path
    query: str
    top_k: RuntimeInt = 5
    score_threshold: RuntimeFloat = 0.0
    include_snippets: RuntimeBool = True
    include_file_metadata: RuntimeBool = True
    embedding_strategy: str = "hash_token_v1"
    search_strategy: str = "cosine_v1"


class HttpRetryPolicy(BaseModel):
    attempts: RuntimeInt = 1
    backoff_seconds: RuntimeFloat = 0.0
    retry_on_statuses: RuntimeIntList = []


class HttpRequestOperation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal[OperationType.HTTP_REQUEST]
    method: str = "GET"
    url: str
    headers: RuntimeStringMap = {}
    params: RuntimeStringMap = {}
    json_payload: object | None = Field(default=None, alias="json", serialization_alias="json")
    body: str | None = None
    timeout_seconds: RuntimeFloat = 30.0
    retry: HttpRetryPolicy = Field(default_factory=HttpRetryPolicy)
    expected_statuses: RuntimeIntList = [200]
    response_mode: Annotated[
        str,
        PlainValidator(runtime_literal_validator("auto", "json", "text", "none")),
        WithJsonSchema(runtime_literal_schema("auto", "json", "text", "none")),
    ] = "auto"
    output_mapping: RuntimeStringMap = {}
    secret_fields: RuntimeStringList = []
    network_allowlist: RuntimeStringList = []

    @model_validator(mode="after")
    def _validate_runtime_config_literals(self) -> HttpRequestOperation:
        for field_name in ("timeout_seconds", "expected_statuses", "network_allowlist"):
            value = getattr(self, field_name)
            if isinstance(value, str) and not is_exact_runtime_reference(value):
                raise ValueError(f"{field_name} must be a valid literal or exact runtime reference")
        return self


class ApprovalGateOperation(BaseModel):
    type: Literal[OperationType.APPROVAL_GATE]
    message: str
    timeout_seconds: RuntimeFloat | None = None
    timeout_decision: Annotated[
        str,
        PlainValidator(runtime_literal_validator("reject", "timeout")),
        WithJsonSchema(runtime_literal_schema("reject", "timeout")),
    ] = "timeout"
    approvers: RuntimeStringList = []
    notify: RuntimeBool = False
    notification_title: str = "Raticode approval needed"
    subject: str | None = None

    @model_validator(mode="after")
    def _validate_runtime_config_literals(self) -> ApprovalGateOperation:
        if self.timeout_decision not in {"reject", "timeout"} and not is_exact_runtime_reference(
            self.timeout_decision
        ):
            raise ValueError("timeout_decision must be reject, timeout, or an exact reference")
        for field_name in ("timeout_seconds", "approvers", "notify"):
            value = getattr(self, field_name)
            if isinstance(value, str) and not is_exact_runtime_reference(value):
                raise ValueError(f"{field_name} must be a valid literal or exact runtime reference")
        return self


class NotificationOperation(BaseModel):
    type: Literal[OperationType.NOTIFICATION]
    title: str = "Raticode notification"
    body: str = ""
    channel: Annotated[
        str,
        WithJsonSchema(runtime_literal_schema("desktop", "slack", "teams", "webhook", "email")),
    ] = "desktop"
    urgency: Annotated[
        str,
        WithJsonSchema(runtime_literal_schema("low", "normal", "critical")),
    ] = "normal"
    webhook_url: str | None = None
    headers: RuntimeStringMap = {}
    payload: object | None = None
    email_from: str | None = None
    email_to: RuntimeStringList = []
    smtp_host: str | None = None
    smtp_port: RuntimeInt = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: RuntimeBool = True
    timeout_seconds: RuntimeFloat = 30.0
    retry: HttpRetryPolicy = Field(default_factory=HttpRetryPolicy)
    expected_statuses: RuntimeIntList = [200, 201, 202, 204]
    network_allowlist: RuntimeStringList = []

    @model_validator(mode="after")
    def _validate_runtime_config_literals(self) -> NotificationOperation:
        if self.channel not in {"desktop", "slack", "teams", "webhook", "email"} and not (
            is_exact_runtime_reference(self.channel)
        ):
            raise ValueError(
                "channel must be desktop, slack, teams, webhook, email, or an exact reference"
            )
        if self.urgency not in {"low", "normal", "critical"} and not (
            is_exact_runtime_reference(self.urgency)
        ):
            raise ValueError("urgency must be low, normal, critical, or an exact reference")
        for field_name in (
            "email_to",
            "smtp_port",
            "smtp_starttls",
            "timeout_seconds",
            "expected_statuses",
            "network_allowlist",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str) and not is_exact_runtime_reference(value):
                raise ValueError(f"{field_name} must be a valid literal or exact runtime reference")
        return self


class WorkflowCallOperation(BaseModel):
    type: Literal[OperationType.WORKFLOW]
    workflow_id: str
    input_bindings: RuntimeObjectMap = {}


class SubflowOperation(BaseModel):
    type: Literal[OperationType.SUBFLOW]
    component_id: str
    version: str | None = None
    source_path: Path | None = None
    expanded: RuntimeBool = False
    parameter_bindings: RuntimeObjectMap = {}
    input_bindings: RuntimeObjectMap = {}
    output_contract: dict[str, object] = {}
    filesystem_access: RuntimeObjectList = []
    provider_requirements: RuntimeObjectList = []
    secret_requirements: RuntimeStringList = []


class AgentOperation(BaseModel):
    type: Literal[OperationType.AGENT]
    agent_id: str
    prompt_path: Path | None = None
    working_dir: Path
    profile: str | None = None
    model: str | None = None
    effort: str | None = None
    timeout: RuntimeFloat | None = None
    skill_name: str | None = None
    dynamic_count: RuntimeInt = 1
    memory: Annotated[
        str,
        PlainValidator(runtime_literal_validator("none", "run", "all")),
        WithJsonSchema(runtime_literal_schema("none", "run", "all")),
    ] = "none"
    input_mapping: RuntimeStringMap = {}
    output_schema: str | dict[str, Any] | None = None
    repair_attempts: RuntimeInt = 0
    # Deprecated: fan-out belongs on LoopOperation. Kept for old TOML compatibility.
    fan_source: FanSource | None = None

    @model_validator(mode="after")
    def _validate_repair_attempts(self) -> AgentOperation:
        if isinstance(self.repair_attempts, int) and not 0 <= self.repair_attempts <= 3:
            raise ValueError("repair_attempts must be between 0 and 3")
        return self


Operation = Annotated[
    StartOperation
    | PassOperation
    | FailOperation
    | BreakOperation
    | LoopOperation
    | PythonScriptOperation
    | ShellScriptOperation
    | BashCommandOperation
    | ReadFileOperation
    | WriteFileOperation
    | CopyFileOperation
    | MoveFileOperation
    | DeleteFileOperation
    | FileOperation
    | FolderOperation
    | OpenResourceOperation
    | PromptFileOperation
    | CommonLlmTaskOperation
    | LocalVectorizeOperation
    | LocalSearchOperation
    | HttpRequestOperation
    | ApprovalGateOperation
    | NotificationOperation
    | WorkflowCallOperation
    | SubflowOperation
    | AgentOperation,
    Field(discriminator="type"),
]
