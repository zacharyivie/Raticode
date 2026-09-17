"""Interactive arrow-key TUI editor for workflow and agent configs."""

from __future__ import annotations

import enum
import io
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import questionary
from prompt_toolkit import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console
from rich.style import Style
from rich.text import Text

from gofer.core.agent import AgentConfig
from gofer.core.graph import GraphNode
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
from gofer.core.workflow import AgenticWorkflow, ScheduleConfig, WatchConfig

# ── Field descriptor types ───────────────────────────────────────────────────


_AnyOp = (
    StartOperation
    | PassOperation
    | FailOperation
    | BreakOperation
    | LoopOperation
    | BashCommandOperation
    | PythonScriptOperation
    | ShellScriptOperation
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
    | AgentOperation
)


class FieldKind(enum.Enum):
    STRING = "string"
    PATH = "path"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    LIST_STR = "list"
    DICT_STR_STR = "dict"
    CHOICE = "choice"


def _no_op_validator(v: Any) -> str | None:  # noqa: ANN401
    return None


@dataclass
class FieldDescriptor:
    key: str
    label: str
    kind: FieldKind
    value: Any  # noqa: ANN401
    optional: bool = False
    choices: list[str] = field(default_factory=list)
    default: Any = None  # noqa: ANN401
    validator: Callable[[Any], str | None] = field(default_factory=lambda: _no_op_validator)
    read_only: bool = False


@dataclass
class Section:
    title: str
    fields: list[FieldDescriptor]


# ── Value formatting and coercion ────────────────────────────────────────────


def _format_value(fd: FieldDescriptor) -> str:
    v = fd.value
    if v is None:
        return "(none)"
    if fd.kind == FieldKind.LIST_STR:
        items = v if isinstance(v, list) else []
        return ", ".join(str(i) for i in items) if items else "(empty)"
    if fd.kind == FieldKind.DICT_STR_STR:
        d = v if isinstance(v, dict) else {}
        return ", ".join(f"{k}={val}" for k, val in d.items()) if d else "(empty)"
    if fd.kind == FieldKind.BOOL:
        return "yes" if v else "no"
    return str(v)


def _coerce(kind: FieldKind, raw: str) -> Any:  # noqa: ANN401
    match kind:
        case FieldKind.INT:
            return int(raw)
        case FieldKind.FLOAT:
            return float(raw)
        case FieldKind.PATH:
            return Path(raw).expanduser()
        case _:
            return raw


def _format_json_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True)


def _parse_json_field(value: object) -> object | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    parsed: object = json.loads(text)
    return parsed


def _parse_output_schema_field(value: object) -> str | dict[str, Any] | None:
    if value is None or isinstance(value, dict):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict):
        raise ValueError("Output schema must be a schema name or JSON object")
    return parsed


# ── TUI Application ──────────────────────────────────────────────────────────

_LABEL_W = 22
_VALUE_W = 36
_HELP = " [↑↓] Navigate   [Enter] Edit   [Del] Clear   [s] Save   [q] Quit"
_SECTION_WIDTH = 70


class FieldEditorApp:
    def __init__(self, sections: list[Section], title: str = "Editor") -> None:
        self._sections = sections
        self._title = title
        self._flat: list[FieldDescriptor] = [fd for sec in sections for fd in sec.fields]
        self._cursor = 0
        self._scroll_offset = 0
        self._error: str | None = None
        self._saved = False

    def run(self) -> bool:
        self._pending_edit: FieldDescriptor | None = None

        while True:
            kb = self._build_key_bindings()
            layout = Layout(
                Window(
                    content=FormattedTextControl(self._get_formatted_text, focusable=True),
                    wrap_lines=False,
                )
            )
            app: Application[None] = Application(
                layout=layout,
                key_bindings=kb,
                full_screen=True,
                mouse_support=False,
            )
            app.run()

            if self._pending_edit is not None:
                # Clear screen so only the active prompt is visible
                print("\033[2J\033[H", end="", flush=True)
                self._edit_field(self._pending_edit)
                self._pending_edit = None
                # Loop back to re-enter the full-screen app
            else:
                break

        return self._saved

    # ── Rendering ────────────────────────────────────────────────────────────

    def _get_formatted_text(self) -> ANSI:
        term = shutil.get_terminal_size(fallback=(120, 40))
        height = max(5, term.lines - 1)
        width = term.columns

        all_lines, cursor_line = self._render_all()

        # Keep cursor in view
        if cursor_line < self._scroll_offset:
            self._scroll_offset = cursor_line
        elif cursor_line >= self._scroll_offset + height - 2:
            self._scroll_offset = max(0, cursor_line - height + 3)

        visible = all_lines[self._scroll_offset : self._scroll_offset + height]

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, highlight=False, width=width)
        for line in visible:
            console.print(line, end="\n", markup=False)

        return ANSI(buf.getvalue())

    def _render_all(self) -> tuple[list[Text], int]:
        lines: list[Text] = []
        cursor_line = 0
        flat_idx = 0

        # Title
        lines.append(Text(f"  {self._title}", style=Style(bold=True, color="cyan")))
        lines.append(Text(""))

        for sec in self._sections:
            pad = max(0, _SECTION_WIDTH - len(sec.title) - 5)
            lines.append(
                Text(
                    f"  ── {sec.title} " + "─" * pad,
                    style=Style(dim=True),
                )
            )

            for fd in sec.fields:
                is_cursor = flat_idx == self._cursor
                if is_cursor:
                    cursor_line = len(lines)
                lines.append(self._render_field_row(fd, is_cursor))
                flat_idx += 1

            lines.append(Text(""))

        if self._error:
            lines.append(Text(f"  ✖ {self._error}", style=Style(color="red")))
            lines.append(Text(""))

        lines.append(Text(_HELP, style=Style(dim=True)))
        return lines, cursor_line

    def _render_field_row(self, fd: FieldDescriptor, is_cursor: bool) -> Text:
        cursor_char = "►" if is_cursor else " "
        label = f"{cursor_char} {fd.label}"
        value_str = _format_value(fd)
        kind_tag = "(read-only)" if fd.read_only else f"{fd.kind.value}{'?' if fd.optional else ''}"

        row = Text()
        row.append(f"  {label:<{_LABEL_W}}")
        row.append(f"  {value_str:<{_VALUE_W}}")
        row.append(f"  {kind_tag}", style=Style(dim=True))

        if is_cursor:
            row.stylize(Style(reverse=True), start=0, end=2 + _LABEL_W + 2 + _VALUE_W)

        if fd.read_only:
            row.stylize(Style(dim=True))

        return row

    # ── Key bindings ─────────────────────────────────────────────────────────

    def _build_key_bindings(self) -> KeyBindings:
        kb: KeyBindings = KeyBindings()
        total = len(self._flat)

        @kb.add("up")
        @kb.add("k")
        def _up(event: KeyPressEvent) -> None:
            if self._cursor > 0:
                self._cursor -= 1
            self._error = None

        @kb.add("down")
        @kb.add("j")
        def _down(event: KeyPressEvent) -> None:
            if self._cursor < total - 1:
                self._cursor += 1
            self._error = None

        @kb.add("pageup")
        def _pageup(event: KeyPressEvent) -> None:
            self._cursor = max(0, self._cursor - 10)
            self._error = None

        @kb.add("pagedown")
        def _pagedown(event: KeyPressEvent) -> None:
            self._cursor = min(total - 1, self._cursor + 10)
            self._error = None

        @kb.add("enter")
        def _enter(event: KeyPressEvent) -> None:
            fd = self._flat[self._cursor]
            if fd.read_only:
                self._error = f"'{fd.label}' is read-only"
                return
            self._error = None
            self._pending_edit = fd
            event.app.exit()

        @kb.add("delete")
        @kb.add("c-d")
        def _delete(event: KeyPressEvent) -> None:
            fd = self._flat[self._cursor]
            if fd.read_only:
                self._error = f"'{fd.label}' is read-only"
                return
            if not fd.optional:
                self._error = f"'{fd.label}' is required — cannot clear"
                return
            fd.value = None
            self._error = None

        @kb.add("s")
        @kb.add("c-s")
        def _save(event: KeyPressEvent) -> None:
            self._saved = True
            event.app.exit()

        @kb.add("q")
        @kb.add("escape")
        @kb.add("c-c")
        def _quit(event: KeyPressEvent) -> None:
            self._saved = False
            event.app.exit()

        return kb

    # ── Field editing ─────────────────────────────────────────────────────────

    def _edit_field(self, fd: FieldDescriptor) -> None:
        try:
            if fd.kind == FieldKind.BOOL:
                result = questionary.confirm(fd.label, default=bool(fd.value)).ask()
                if result is not None:
                    fd.value = result

            elif fd.kind == FieldKind.CHOICE:
                result = questionary.select(
                    fd.label,
                    choices=fd.choices,
                    default=str(fd.value) if fd.value is not None else None,
                ).ask()
                if result is not None:
                    fd.value = result

            elif fd.kind == FieldKind.LIST_STR:
                current = ", ".join(str(x) for x in fd.value) if fd.value else ""
                raw = questionary.text(f"{fd.label} (comma-separated)", default=current).ask()
                if raw is not None:
                    fd.value = [x.strip() for x in raw.split(",") if x.strip()]

            elif fd.kind == FieldKind.DICT_STR_STR:
                d: dict[str, str] = fd.value if isinstance(fd.value, dict) else {}
                current = ", ".join(f"{k}={v}" for k, v in d.items())
                raw = questionary.text(
                    f"{fd.label} (KEY=VALUE, comma-separated)", default=current
                ).ask()
                if raw is not None:
                    parsed: dict[str, str] = {}
                    for pair in raw.split(","):
                        pair = pair.strip()
                        if "=" in pair:
                            k, _, v = pair.partition("=")
                            parsed[k.strip()] = v.strip()
                    fd.value = parsed

            else:
                current = str(fd.value) if fd.value is not None else ""
                raw = questionary.text(fd.label, default=current).ask()
                if raw is None:
                    return
                if not raw.strip() and fd.optional:
                    fd.value = None
                    return
                try:
                    coerced = _coerce(fd.kind, raw.strip())
                except (ValueError, TypeError) as exc:
                    self._error = f"Invalid value: {exc}"
                    return
                err = fd.validator(coerced)
                if err:
                    self._error = err
                    return
                fd.value = coerced

        except KeyboardInterrupt:
            pass


# ── Workflow ↔ sections ───────────────────────────────────────────────────────


def workflow_to_sections(wf: AgenticWorkflow) -> list[Section]:
    sched = wf.config.schedule
    watch = wf.config.watch
    wf_fields: list[FieldDescriptor] = [
        FieldDescriptor("config.id", "ID", FieldKind.STRING, wf.config.id, read_only=True),
        FieldDescriptor("config.name", "Name", FieldKind.STRING, wf.config.name),
        FieldDescriptor(
            "config.max_total_node_runs",
            "Max Total Node Runs",
            FieldKind.INT,
            wf.config.max_total_node_runs,
        ),
        FieldDescriptor(
            "config.schedule.cron_expression",
            "Cron Expression",
            FieldKind.STRING,
            sched.cron_expression if sched else None,
            optional=True,
        ),
        FieldDescriptor(
            "config.schedule.timezone",
            "Schedule Timezone",
            FieldKind.STRING,
            sched.timezone if sched else "UTC",
            optional=True,
        ),
        FieldDescriptor(
            "config.watch.path",
            "Watch Path",
            FieldKind.PATH,
            watch.path if watch else None,
            optional=True,
        ),
        FieldDescriptor(
            "config.watch.glob",
            "Watch Glob",
            FieldKind.STRING,
            watch.glob if watch else "*",
            optional=True,
        ),
        FieldDescriptor(
            "config.watch.recursive",
            "Watch Recursive",
            FieldKind.BOOL,
            watch.recursive if watch else False,
            default=False,
        ),
        FieldDescriptor(
            "config.watch.debounce_seconds",
            "Watch Debounce Seconds",
            FieldKind.FLOAT,
            watch.debounce_seconds if watch else 1.0,
        ),
        FieldDescriptor(
            "config.watch.mode",
            "Watch Mode",
            FieldKind.CHOICE,
            watch.mode if watch else "batch",
            choices=["batch", "queue", "fanout"],
        ),
        FieldDescriptor(
            "config.watch.max_concurrency",
            "Watch Max Concurrency",
            FieldKind.INT,
            watch.max_concurrency if watch else 1,
        ),
    ]
    sections: list[Section] = [Section("Workflow", wf_fields)]

    for gen in wf.graph.topological_generations():
        for node in gen:
            sections.append(_node_to_section(node))

    return sections


def _node_to_section(node: GraphNode) -> Section:
    op = node.operation
    op_fields = _operation_fields(node.node_id, op)
    shared: list[FieldDescriptor] = [
        FieldDescriptor(
            f"nodes.{node.node_id}.retry_count",
            "Retry Count",
            FieldKind.INT,
            node.retry_count,
            default=0,
        ),
        FieldDescriptor(
            f"nodes.{node.node_id}.retry_delay_seconds",
            "Retry Delay (s)",
            FieldKind.FLOAT,
            node.retry_delay_seconds,
            default=1.0,
        ),
        FieldDescriptor(
            f"nodes.{node.node_id}.timeout_seconds",
            "Timeout (s)",
            FieldKind.FLOAT,
            node.timeout_seconds,
            optional=True,
        ),
        FieldDescriptor(
            f"nodes.{node.node_id}.pipe_output",
            "Pipe Output",
            FieldKind.BOOL,
            node.pipe_output,
            default=False,
        ),
    ]
    return Section(f"Node: {node.node_id} [{op.type}]", op_fields + shared)


def _operation_fields(
    node_id: str,
    op: _AnyOp,
) -> list[FieldDescriptor]:
    prefix = f"nodes.{node_id}"
    node_id_fd = FieldDescriptor(
        f"{prefix}.node_id", "Node ID", FieldKind.STRING, node_id, read_only=True
    )

    if isinstance(op, StartOperation):
        return [node_id_fd]

    if isinstance(op, (PassOperation, FailOperation, BreakOperation)):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.message", "Message", FieldKind.STRING, op.message),
        ]

    if isinstance(op, LoopOperation):
        return [node_id_fd, *_fan_source_fields(prefix, op.source)]

    if isinstance(op, BashCommandOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.command", "Command", FieldKind.STRING, op.command),
            FieldDescriptor(
                f"{prefix}.working_dir",
                "Working Dir",
                FieldKind.PATH,
                op.working_dir,
                optional=True,
            ),
            FieldDescriptor(f"{prefix}.env", "Environment", FieldKind.DICT_STR_STR, dict(op.env)),
        ]

    if isinstance(op, PythonScriptOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.script_path", "Script Path", FieldKind.PATH, op.script_path),
            FieldDescriptor(f"{prefix}.args", "Arguments", FieldKind.LIST_STR, list(op.args)),
            FieldDescriptor(f"{prefix}.env", "Environment", FieldKind.DICT_STR_STR, dict(op.env)),
        ]

    if isinstance(op, ShellScriptOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.script_path", "Script Path", FieldKind.PATH, op.script_path),
            FieldDescriptor(f"{prefix}.args", "Arguments", FieldKind.LIST_STR, list(op.args)),
            FieldDescriptor(f"{prefix}.env", "Environment", FieldKind.DICT_STR_STR, dict(op.env)),
        ]

    if isinstance(op, ReadFileOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.path", "Path", FieldKind.PATH, op.path),
            FieldDescriptor(f"{prefix}.encoding", "Encoding", FieldKind.STRING, op.encoding),
            FieldDescriptor(f"{prefix}.errors", "Errors", FieldKind.STRING, op.errors),
        ]

    if isinstance(op, WriteFileOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.path", "Path", FieldKind.PATH, op.path),
            FieldDescriptor(f"{prefix}.content", "Content", FieldKind.STRING, op.content),
            FieldDescriptor(f"{prefix}.encoding", "Encoding", FieldKind.STRING, op.encoding),
            FieldDescriptor(f"{prefix}.create_dirs", "Create Dirs", FieldKind.BOOL, op.create_dirs),
            FieldDescriptor(f"{prefix}.overwrite", "Overwrite", FieldKind.BOOL, op.overwrite),
            FieldDescriptor(f"{prefix}.append", "Append", FieldKind.BOOL, op.append),
        ]

    if isinstance(op, (CopyFileOperation, MoveFileOperation)):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.source_path", "Source Path", FieldKind.PATH, op.source_path),
            FieldDescriptor(
                f"{prefix}.destination_path",
                "Destination Path",
                FieldKind.PATH,
                op.destination_path,
            ),
            FieldDescriptor(f"{prefix}.create_dirs", "Create Dirs", FieldKind.BOOL, op.create_dirs),
            FieldDescriptor(f"{prefix}.overwrite", "Overwrite", FieldKind.BOOL, op.overwrite),
        ]

    if isinstance(op, DeleteFileOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.path", "Path", FieldKind.PATH, op.path),
            FieldDescriptor(f"{prefix}.use_trash", "Use Trash", FieldKind.BOOL, op.use_trash),
            FieldDescriptor(f"{prefix}.recursive", "Recursive", FieldKind.BOOL, op.recursive),
            FieldDescriptor(f"{prefix}.missing_ok", "Missing OK", FieldKind.BOOL, op.missing_ok),
        ]

    if isinstance(op, (FileOperation, FolderOperation)):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.path", "Path", FieldKind.PATH, op.path),
        ]

    if isinstance(op, OpenResourceOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.target", "Target", FieldKind.STRING, op.target),
            FieldDescriptor(
                f"{prefix}.resource_type",
                "Resource Type",
                FieldKind.CHOICE,
                op.resource_type,
                choices=["auto", "file", "folder", "url", "app"],
            ),
            FieldDescriptor(f"{prefix}.args", "Arguments", FieldKind.LIST_STR, list(op.args)),
        ]

    if isinstance(op, PromptFileOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.output_path", "Output Path", FieldKind.PATH, op.output_path),
            FieldDescriptor(f"{prefix}.template", "Template", FieldKind.STRING, op.template),
            FieldDescriptor(
                f"{prefix}.template_path",
                "Template Path",
                FieldKind.PATH,
                op.template_path,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.variables",
                "Variables",
                FieldKind.DICT_STR_STR,
                dict(op.variables),
            ),
            FieldDescriptor(f"{prefix}.encoding", "Encoding", FieldKind.STRING, op.encoding),
            FieldDescriptor(f"{prefix}.create_dirs", "Create Dirs", FieldKind.BOOL, op.create_dirs),
            FieldDescriptor(f"{prefix}.overwrite", "Overwrite", FieldKind.BOOL, op.overwrite),
        ]

    if isinstance(op, CommonLlmTaskOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.agent_id", "Agent ID", FieldKind.STRING, op.agent_id),
            FieldDescriptor(
                f"{prefix}.task",
                "Task",
                FieldKind.CHOICE,
                op.task,
                choices=[
                    "review",
                    "summarize",
                    "explain",
                    "extract",
                    "rewrite",
                    "classify",
                ],
            ),
            FieldDescriptor(f"{prefix}.target", "Target", FieldKind.STRING, op.target),
            FieldDescriptor(
                f"{prefix}.instructions",
                "Instructions",
                FieldKind.STRING,
                op.instructions,
            ),
            FieldDescriptor(f"{prefix}.working_dir", "Working Dir", FieldKind.PATH, op.working_dir),
            *_llm_common_fields(prefix, op),
        ]

    if isinstance(op, LocalVectorizeOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.source_path", "Source Path", FieldKind.PATH, op.source_path),
            FieldDescriptor(f"{prefix}.index_path", "Index Path", FieldKind.PATH, op.index_path),
            FieldDescriptor(f"{prefix}.glob", "Glob", FieldKind.STRING, op.glob),
            FieldDescriptor(f"{prefix}.recursive", "Recursive", FieldKind.BOOL, op.recursive),
            FieldDescriptor(f"{prefix}.chunk_size", "Chunk Size", FieldKind.INT, op.chunk_size),
            FieldDescriptor(
                f"{prefix}.chunk_overlap",
                "Chunk Overlap",
                FieldKind.INT,
                op.chunk_overlap,
            ),
            FieldDescriptor(f"{prefix}.encoding", "Encoding", FieldKind.STRING, op.encoding),
            FieldDescriptor(
                f"{prefix}.mode",
                "Mode",
                FieldKind.CHOICE,
                op.mode,
                choices=["incremental", "full", "validate", "compact"],
            ),
            FieldDescriptor(
                f"{prefix}.embedding_strategy",
                "Embedding Strategy",
                FieldKind.STRING,
                op.embedding_strategy,
            ),
            FieldDescriptor(
                f"{prefix}.search_strategy",
                "Search Strategy",
                FieldKind.STRING,
                op.search_strategy,
            ),
        ]

    if isinstance(op, LocalSearchOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.index_path", "Index Path", FieldKind.PATH, op.index_path),
            FieldDescriptor(f"{prefix}.query", "Query", FieldKind.STRING, op.query),
            FieldDescriptor(f"{prefix}.top_k", "Top K", FieldKind.INT, op.top_k),
            FieldDescriptor(
                f"{prefix}.score_threshold",
                "Score Threshold",
                FieldKind.FLOAT,
                op.score_threshold,
            ),
            FieldDescriptor(
                f"{prefix}.include_snippets",
                "Include Snippets",
                FieldKind.BOOL,
                op.include_snippets,
            ),
            FieldDescriptor(
                f"{prefix}.include_file_metadata",
                "Include Metadata",
                FieldKind.BOOL,
                op.include_file_metadata,
            ),
            FieldDescriptor(
                f"{prefix}.embedding_strategy",
                "Embedding Strategy",
                FieldKind.STRING,
                op.embedding_strategy,
            ),
            FieldDescriptor(
                f"{prefix}.search_strategy",
                "Search Strategy",
                FieldKind.STRING,
                op.search_strategy,
            ),
        ]

    if isinstance(op, HttpRequestOperation):
        return [
            node_id_fd,
            FieldDescriptor(
                f"{prefix}.method",
                "Method",
                FieldKind.CHOICE,
                op.method,
                choices=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
            ),
            FieldDescriptor(f"{prefix}.url", "URL", FieldKind.STRING, op.url),
            FieldDescriptor(
                f"{prefix}.headers", "Headers", FieldKind.DICT_STR_STR, dict(op.headers)
            ),
            FieldDescriptor(
                f"{prefix}.params",
                "Query Params",
                FieldKind.DICT_STR_STR,
                dict(op.params),
            ),
            FieldDescriptor(
                f"{prefix}.json_payload",
                "JSON Body",
                FieldKind.STRING,
                _format_json_field(op.json_payload),
            ),
            FieldDescriptor(f"{prefix}.body", "Raw Body", FieldKind.STRING, op.body or ""),
            FieldDescriptor(
                f"{prefix}.http_timeout_seconds",
                "HTTP Timeout (s)",
                FieldKind.FLOAT,
                op.timeout_seconds,
            ),
            FieldDescriptor(
                f"{prefix}.retry.attempts",
                "Retry Attempts",
                FieldKind.INT,
                op.retry.attempts,
            ),
            FieldDescriptor(
                f"{prefix}.retry.backoff_seconds",
                "Retry Backoff (s)",
                FieldKind.FLOAT,
                op.retry.backoff_seconds,
            ),
            FieldDescriptor(
                f"{prefix}.retry.retry_on_statuses",
                "Retry Statuses",
                FieldKind.LIST_STR,
                [str(status) for status in op.retry.retry_on_statuses],
            ),
            FieldDescriptor(
                f"{prefix}.expected_statuses",
                "Expected Statuses",
                FieldKind.LIST_STR,
                [str(status) for status in op.expected_statuses],
            ),
            FieldDescriptor(
                f"{prefix}.response_mode",
                "Response Mode",
                FieldKind.CHOICE,
                op.response_mode,
                choices=["auto", "json", "text", "none"],
            ),
            FieldDescriptor(
                f"{prefix}.output_mapping",
                "Output Mapping",
                FieldKind.DICT_STR_STR,
                dict(op.output_mapping),
            ),
            FieldDescriptor(
                f"{prefix}.secret_fields",
                "Secret Fields",
                FieldKind.LIST_STR,
                list(op.secret_fields),
            ),
        ]

    if isinstance(op, ApprovalGateOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.message", "Message", FieldKind.STRING, op.message),
            FieldDescriptor(
                f"{prefix}.approval_timeout_seconds",
                "Approval Timeout (s)",
                FieldKind.FLOAT,
                op.timeout_seconds,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.timeout_decision",
                "Timeout Decision",
                FieldKind.CHOICE,
                op.timeout_decision,
                choices=["reject", "timeout"],
            ),
            FieldDescriptor(
                f"{prefix}.approvers",
                "Approvers",
                FieldKind.LIST_STR,
                op.approvers if isinstance(op.approvers, list) else [op.approvers],
            ),
            FieldDescriptor(f"{prefix}.notify", "Notify", FieldKind.BOOL, op.notify),
            FieldDescriptor(
                f"{prefix}.notification_title",
                "Notification Title",
                FieldKind.STRING,
                op.notification_title,
            ),
            FieldDescriptor(
                f"{prefix}.subject",
                "Approval Subject",
                FieldKind.STRING,
                op.subject,
                optional=True,
            ),
        ]

    if isinstance(op, NotificationOperation):
        return [
            node_id_fd,
            FieldDescriptor(f"{prefix}.title", "Title", FieldKind.STRING, op.title),
            FieldDescriptor(f"{prefix}.body", "Body", FieldKind.STRING, op.body),
            FieldDescriptor(
                f"{prefix}.channel",
                "Channel",
                FieldKind.CHOICE,
                op.channel,
                choices=["desktop", "slack", "teams", "webhook", "email"],
            ),
            FieldDescriptor(
                f"{prefix}.urgency",
                "Urgency",
                FieldKind.CHOICE,
                op.urgency,
                choices=["low", "normal", "critical"],
            ),
            FieldDescriptor(
                f"{prefix}.webhook_url",
                "Webhook URL",
                FieldKind.STRING,
                op.webhook_url,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.headers", "Headers", FieldKind.DICT_STR_STR, dict(op.headers)
            ),
            FieldDescriptor(
                f"{prefix}.payload",
                "Payload (JSON)",
                FieldKind.STRING,
                _format_json_field(op.payload),
            ),
            FieldDescriptor(
                f"{prefix}.email_from",
                "Email From",
                FieldKind.STRING,
                op.email_from,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.email_to",
                "Email Recipients",
                FieldKind.LIST_STR,
                op.email_to if isinstance(op.email_to, list) else [op.email_to],
            ),
            FieldDescriptor(
                f"{prefix}.smtp_host",
                "SMTP Host",
                FieldKind.STRING,
                op.smtp_host,
                optional=True,
            ),
            FieldDescriptor(f"{prefix}.smtp_port", "SMTP Port", FieldKind.STRING, op.smtp_port),
            FieldDescriptor(
                f"{prefix}.smtp_username",
                "SMTP Username",
                FieldKind.STRING,
                op.smtp_username,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.smtp_password",
                "SMTP Password",
                FieldKind.STRING,
                op.smtp_password,
                optional=True,
            ),
            FieldDescriptor(
                f"{prefix}.smtp_starttls",
                "SMTP STARTTLS",
                FieldKind.BOOL,
                op.smtp_starttls,
            ),
            FieldDescriptor(
                f"{prefix}.notification_timeout_seconds",
                "Timeout (s)",
                FieldKind.STRING,
                op.timeout_seconds,
            ),
            FieldDescriptor(
                f"{prefix}.retry.attempts",
                "Retry Attempts",
                FieldKind.STRING,
                op.retry.attempts,
            ),
            FieldDescriptor(
                f"{prefix}.retry.backoff_seconds",
                "Retry Backoff (s)",
                FieldKind.STRING,
                op.retry.backoff_seconds,
            ),
            FieldDescriptor(
                f"{prefix}.retry.retry_on_statuses",
                "Retry Statuses",
                FieldKind.LIST_STR,
                (
                    [str(status) for status in op.retry.retry_on_statuses]
                    if isinstance(op.retry.retry_on_statuses, list)
                    else [op.retry.retry_on_statuses]
                ),
            ),
            FieldDescriptor(
                f"{prefix}.expected_statuses",
                "Expected Statuses",
                FieldKind.LIST_STR,
                (
                    [str(status) for status in op.expected_statuses]
                    if isinstance(op.expected_statuses, list)
                    else [op.expected_statuses]
                ),
            ),
            FieldDescriptor(
                f"{prefix}.network_allowlist",
                "Network Allowlist",
                FieldKind.LIST_STR,
                (
                    op.network_allowlist
                    if isinstance(op.network_allowlist, list)
                    else [op.network_allowlist]
                ),
            ),
        ]

    if not isinstance(op, AgentOperation):
        return [node_id_fd]

    return [
        node_id_fd,
        FieldDescriptor(f"{prefix}.agent_id", "Agent ID", FieldKind.STRING, op.agent_id),
        FieldDescriptor(f"{prefix}.prompt_path", "Prompt Path", FieldKind.PATH, op.prompt_path),
        FieldDescriptor(f"{prefix}.working_dir", "Working Dir", FieldKind.PATH, op.working_dir),
        *_llm_common_fields(prefix, op),
        FieldDescriptor(
            f"{prefix}.skill_name",
            "Skill Name",
            FieldKind.STRING,
            op.skill_name,
            optional=True,
        ),
        FieldDescriptor(
            f"{prefix}.dynamic_count",
            "Dynamic Count",
            FieldKind.STRING,
            str(op.dynamic_count),
        ),
    ]


def _llm_common_fields(
    prefix: str,
    op: AgentOperation | CommonLlmTaskOperation,
) -> list[FieldDescriptor]:
    return [
        FieldDescriptor(
            f"{prefix}.profile", "Profile", FieldKind.STRING, op.profile, optional=True
        ),
        FieldDescriptor(f"{prefix}.model", "Model", FieldKind.STRING, op.model, optional=True),
        FieldDescriptor(
            f"{prefix}.timeout",
            "Agent Timeout (s)",
            FieldKind.FLOAT,
            op.timeout,
            optional=True,
        ),
        FieldDescriptor(
            f"{prefix}.memory",
            "Memory",
            FieldKind.CHOICE,
            op.memory,
            choices=["none", "run", "all"],
        ),
        FieldDescriptor(
            f"{prefix}.input_mapping",
            "Input Mapping",
            FieldKind.DICT_STR_STR,
            dict(op.input_mapping),
        ),
        FieldDescriptor(
            f"{prefix}.output_schema",
            "Output Schema (name or JSON)",
            FieldKind.STRING,
            (
                json.dumps(op.output_schema, sort_keys=True)
                if isinstance(op.output_schema, dict)
                else op.output_schema
            ),
            optional=True,
        ),
        FieldDescriptor(
            f"{prefix}.repair_attempts",
            "Structured Output Repair Attempts",
            FieldKind.INT,
            op.repair_attempts,
        ),
    ]


def _fan_source_fields(prefix: str, source: object) -> list[FieldDescriptor]:
    fields = [
        FieldDescriptor(
            f"{prefix}.source.type",
            "Source Type",
            FieldKind.CHOICE,
            getattr(source, "type", "count"),
            choices=["count", "tabular", "directory", "trigger_events", "infinite"],
        ),
        FieldDescriptor(
            f"{prefix}.source.max_concurrency",
            "Max Concurrency",
            FieldKind.INT,
            getattr(source, "max_concurrency", 1),
        ),
        FieldDescriptor(
            f"{prefix}.source.fail_fast",
            "Fail Fast",
            FieldKind.BOOL,
            getattr(source, "fail_fast", False),
        ),
    ]
    if isinstance(source, CountFanSource):
        fields.append(
            FieldDescriptor(f"{prefix}.source.count", "Count", FieldKind.STRING, source.count)
        )
    elif isinstance(source, TabularFanSource):
        fields.append(FieldDescriptor(f"{prefix}.source.path", "Path", FieldKind.PATH, source.path))
    elif isinstance(source, DirectoryFanSource):
        fields.extend(
            [
                FieldDescriptor(f"{prefix}.source.path", "Path", FieldKind.PATH, source.path),
                FieldDescriptor(f"{prefix}.source.glob", "Glob", FieldKind.STRING, source.glob),
                FieldDescriptor(
                    f"{prefix}.source.include_content",
                    "Include Content",
                    FieldKind.BOOL,
                    source.include_content,
                ),
            ]
        )
    elif isinstance(source, TriggerEventsFanSource):
        fields.append(
            FieldDescriptor(
                f"{prefix}.source.include_content",
                "Include Content",
                FieldKind.BOOL,
                source.include_content,
            )
        )
    return fields


def sections_to_workflow(sections: list[Section], wf: AgenticWorkflow) -> None:
    fm: dict[str, Any] = {fd.key: fd.value for sec in sections for fd in sec.fields}

    new_name: str = fm.get("config.name") or wf.config.name
    cron: str | None = fm.get("config.schedule.cron_expression") or None
    tz: str = fm.get("config.schedule.timezone") or "UTC"
    schedule = ScheduleConfig(cron_expression=cron, timezone=tz) if cron else None
    watch_path = fm.get("config.watch.path")
    watch = None
    if watch_path:
        watch = WatchConfig(
            path=_as_path(watch_path, Path(".")),
            glob=fm.get("config.watch.glob") or "*",
            recursive=bool(fm.get("config.watch.recursive")),
            debounce_seconds=float(fm.get("config.watch.debounce_seconds") or 1.0),
            mode=fm.get("config.watch.mode") or "batch",
            max_concurrency=int(fm.get("config.watch.max_concurrency") or 1),
        )
    wf.config = wf.config.model_copy(
        update={
            "name": new_name,
            "schedule": schedule,
            "watch": watch,
            "max_total_node_runs": int(fm.get("config.max_total_node_runs") or 1000),
        }
    )

    for gen in wf.graph.topological_generations():
        for node in gen:
            nid = node.node_id
            p = f"nodes.{nid}"
            op = node.operation

            if isinstance(op, StartOperation):
                new_op: _AnyOp = StartOperation(type=op.type)
            elif isinstance(op, PassOperation):
                new_op = PassOperation(
                    type=op.type,
                    message=fm.get(f"{p}.message") or "",
                )
            elif isinstance(op, FailOperation):
                new_op = FailOperation(
                    type=op.type,
                    message=fm.get(f"{p}.message") or "",
                )
            elif isinstance(op, BreakOperation):
                new_op = BreakOperation(
                    type=op.type,
                    message=fm.get(f"{p}.message") or "",
                )
            elif isinstance(op, LoopOperation):
                new_op = LoopOperation(
                    type=op.type,
                    source=_build_fan_source(fm, p, op.source),
                )
            elif isinstance(op, BashCommandOperation):
                wd_raw = fm.get(f"{p}.working_dir")
                new_op = BashCommandOperation(
                    type=op.type,
                    command=fm.get(f"{p}.command") or op.command,
                    working_dir=_as_path_or_none(wd_raw),
                    env=fm.get(f"{p}.env") or {},
                )
            elif isinstance(op, PythonScriptOperation):
                new_op = PythonScriptOperation(
                    type=op.type,
                    script_path=_as_path(fm.get(f"{p}.script_path"), op.script_path),
                    args=fm.get(f"{p}.args") or [],
                    env=fm.get(f"{p}.env") or {},
                )
            elif isinstance(op, ShellScriptOperation):
                new_op = ShellScriptOperation(
                    type=op.type,
                    script_path=_as_path(fm.get(f"{p}.script_path"), op.script_path),
                    args=fm.get(f"{p}.args") or [],
                    env=fm.get(f"{p}.env") or {},
                )
            elif isinstance(op, ReadFileOperation):
                new_op = ReadFileOperation(
                    type=op.type,
                    path=_as_path(fm.get(f"{p}.path"), op.path),
                    encoding=fm.get(f"{p}.encoding") or op.encoding,
                    errors=fm.get(f"{p}.errors") or op.errors,
                )
            elif isinstance(op, WriteFileOperation):
                new_op = WriteFileOperation(
                    type=op.type,
                    path=_as_path(fm.get(f"{p}.path"), op.path),
                    content=fm.get(f"{p}.content") or "",
                    encoding=fm.get(f"{p}.encoding") or op.encoding,
                    create_dirs=bool(fm.get(f"{p}.create_dirs")),
                    overwrite=bool(fm.get(f"{p}.overwrite")),
                    append=bool(fm.get(f"{p}.append")),
                )
            elif isinstance(op, CopyFileOperation):
                new_op = CopyFileOperation(
                    type=op.type,
                    source_path=_as_path(fm.get(f"{p}.source_path"), op.source_path),
                    destination_path=_as_path(fm.get(f"{p}.destination_path"), op.destination_path),
                    create_dirs=bool(fm.get(f"{p}.create_dirs")),
                    overwrite=bool(fm.get(f"{p}.overwrite")),
                )
            elif isinstance(op, MoveFileOperation):
                new_op = MoveFileOperation(
                    type=op.type,
                    source_path=_as_path(fm.get(f"{p}.source_path"), op.source_path),
                    destination_path=_as_path(fm.get(f"{p}.destination_path"), op.destination_path),
                    create_dirs=bool(fm.get(f"{p}.create_dirs")),
                    overwrite=bool(fm.get(f"{p}.overwrite")),
                )
            elif isinstance(op, DeleteFileOperation):
                new_op = DeleteFileOperation(
                    type=op.type,
                    path=_as_path(fm.get(f"{p}.path"), op.path),
                    use_trash=bool(fm.get(f"{p}.use_trash")),
                    recursive=bool(fm.get(f"{p}.recursive")),
                    missing_ok=bool(fm.get(f"{p}.missing_ok")),
                )
            elif isinstance(op, FileOperation):
                new_op = FileOperation(
                    type=op.type,
                    path=_as_path(fm.get(f"{p}.path"), op.path),
                )
            elif isinstance(op, FolderOperation):
                new_op = FolderOperation(
                    type=op.type,
                    path=_as_path(fm.get(f"{p}.path"), op.path),
                )
            elif isinstance(op, OpenResourceOperation):
                new_op = OpenResourceOperation(
                    type=op.type,
                    target=fm.get(f"{p}.target") or op.target,
                    resource_type=fm.get(f"{p}.resource_type") or op.resource_type,
                    args=fm.get(f"{p}.args") or [],
                )
            elif isinstance(op, PromptFileOperation):
                template_path_raw = fm.get(f"{p}.template_path")
                new_op = PromptFileOperation(
                    type=op.type,
                    output_path=_as_path(fm.get(f"{p}.output_path"), op.output_path),
                    template=fm.get(f"{p}.template") or "",
                    template_path=(
                        _as_path_or_none(template_path_raw)
                        if f"{p}.template_path" in fm
                        else _as_path_or_none(template_path_raw)
                    ),
                    variables=fm.get(f"{p}.variables") or {},
                    encoding=fm.get(f"{p}.encoding") or op.encoding,
                    create_dirs=bool(fm.get(f"{p}.create_dirs")),
                    overwrite=bool(fm.get(f"{p}.overwrite")),
                )
            elif isinstance(op, CommonLlmTaskOperation):
                new_op = CommonLlmTaskOperation(
                    type=op.type,
                    agent_id=fm.get(f"{p}.agent_id") or op.agent_id,
                    task=fm.get(f"{p}.task") or op.task,
                    target=fm.get(f"{p}.target") or "",
                    instructions=fm.get(f"{p}.instructions") or "",
                    working_dir=_as_path(fm.get(f"{p}.working_dir"), op.working_dir),
                    profile=_empty_to_none(fm.get(f"{p}.profile")),
                    model=_empty_to_none(fm.get(f"{p}.model")),
                    timeout=_optional_float(fm.get(f"{p}.timeout")),
                    memory=fm.get(f"{p}.memory") or op.memory,
                    input_mapping=fm.get(f"{p}.input_mapping") or {},
                    output_schema=_parse_output_schema_field(fm.get(f"{p}.output_schema")),
                    repair_attempts=int(fm.get(f"{p}.repair_attempts") or 0),
                )
            elif isinstance(op, LocalVectorizeOperation):
                new_op = LocalVectorizeOperation(
                    type=op.type,
                    source_path=_as_path(fm.get(f"{p}.source_path"), op.source_path),
                    index_path=_as_path(fm.get(f"{p}.index_path"), op.index_path),
                    glob=fm.get(f"{p}.glob") or op.glob,
                    recursive=bool(fm.get(f"{p}.recursive")),
                    chunk_size=int(fm.get(f"{p}.chunk_size") or op.chunk_size),
                    chunk_overlap=int(fm.get(f"{p}.chunk_overlap") or op.chunk_overlap),
                    encoding=fm.get(f"{p}.encoding") or op.encoding,
                    mode=fm.get(f"{p}.mode") or op.mode,
                    embedding_strategy=(fm.get(f"{p}.embedding_strategy") or op.embedding_strategy),
                    search_strategy=fm.get(f"{p}.search_strategy") or op.search_strategy,
                )
            elif isinstance(op, LocalSearchOperation):
                new_op = LocalSearchOperation(
                    type=op.type,
                    index_path=_as_path(fm.get(f"{p}.index_path"), op.index_path),
                    query=fm.get(f"{p}.query") or op.query,
                    top_k=int(fm.get(f"{p}.top_k") or op.top_k),
                    score_threshold=float(fm.get(f"{p}.score_threshold") or op.score_threshold),
                    include_snippets=bool(fm.get(f"{p}.include_snippets")),
                    include_file_metadata=bool(fm.get(f"{p}.include_file_metadata")),
                    embedding_strategy=(fm.get(f"{p}.embedding_strategy") or op.embedding_strategy),
                    search_strategy=fm.get(f"{p}.search_strategy") or op.search_strategy,
                )
            elif isinstance(op, HttpRequestOperation):
                retry_statuses = [
                    int(status)
                    for status in (fm.get(f"{p}.retry.retry_on_statuses") or [])
                    if str(status).strip().isdigit()
                ]
                new_op = HttpRequestOperation(
                    type=op.type,
                    method=fm.get(f"{p}.method") or op.method,
                    url=fm.get(f"{p}.url") or op.url,
                    headers=fm.get(f"{p}.headers") or {},
                    params=fm.get(f"{p}.params") or {},
                    json=_parse_json_field(fm.get(f"{p}.json_payload")),
                    body=fm.get(f"{p}.body") or None,
                    timeout_seconds=float(
                        fm.get(f"{p}.http_timeout_seconds") or op.timeout_seconds
                    ),
                    retry=HttpRetryPolicy(
                        attempts=int(fm.get(f"{p}.retry.attempts") or op.retry.attempts),
                        backoff_seconds=float(
                            fm.get(f"{p}.retry.backoff_seconds") or op.retry.backoff_seconds
                        ),
                        retry_on_statuses=retry_statuses,
                    ),
                    expected_statuses=[
                        int(status)
                        for status in (fm.get(f"{p}.expected_statuses") or [])
                        if str(status).strip().isdigit()
                    ]
                    or op.expected_statuses,
                    response_mode=fm.get(f"{p}.response_mode") or op.response_mode,
                    output_mapping=fm.get(f"{p}.output_mapping") or {},
                    secret_fields=fm.get(f"{p}.secret_fields") or [],
                )
            elif isinstance(op, ApprovalGateOperation):
                new_op = ApprovalGateOperation(
                    type=op.type,
                    message=fm.get(f"{p}.message") or op.message,
                    timeout_seconds=_optional_float(fm.get(f"{p}.approval_timeout_seconds")),
                    timeout_decision=(fm.get(f"{p}.timeout_decision") or op.timeout_decision),
                    approvers=fm.get(f"{p}.approvers") or [],
                    notify=bool(fm.get(f"{p}.notify")),
                    notification_title=(fm.get(f"{p}.notification_title") or op.notification_title),
                    subject=_empty_to_none(fm.get(f"{p}.subject")),
                )
            elif isinstance(op, NotificationOperation):
                email_to = _string_list_or_reference(fm.get(f"{p}.email_to"))
                notification_retry_statuses = _integer_list_or_reference(
                    fm.get(f"{p}.retry.retry_on_statuses")
                )
                notification_expected_statuses = _integer_list_or_reference(
                    fm.get(f"{p}.expected_statuses")
                )
                network_allowlist = _string_list_or_reference(fm.get(f"{p}.network_allowlist"))
                new_op = NotificationOperation(
                    type=op.type,
                    title=fm.get(f"{p}.title") or op.title,
                    body=fm.get(f"{p}.body") or "",
                    channel=fm.get(f"{p}.channel") or op.channel,
                    urgency=fm.get(f"{p}.urgency") or op.urgency,
                    webhook_url=_empty_to_none(fm.get(f"{p}.webhook_url")),
                    headers=fm.get(f"{p}.headers") or {},
                    payload=_parse_json_field(fm.get(f"{p}.payload")),
                    email_from=_empty_to_none(fm.get(f"{p}.email_from")),
                    email_to=cast(Any, email_to),
                    smtp_host=_empty_to_none(fm.get(f"{p}.smtp_host")),
                    smtp_port=cast(
                        Any,
                        _number_or_reference(fm.get(f"{p}.smtp_port"), int, op.smtp_port),
                    ),
                    smtp_username=_empty_to_none(fm.get(f"{p}.smtp_username")),
                    smtp_password=_empty_to_none(fm.get(f"{p}.smtp_password")),
                    smtp_starttls=cast(
                        Any,
                        fm.get(f"{p}.smtp_starttls", op.smtp_starttls),
                    ),
                    timeout_seconds=cast(
                        Any,
                        _number_or_reference(
                            fm.get(f"{p}.notification_timeout_seconds"),
                            float,
                            op.timeout_seconds,
                        ),
                    ),
                    retry=HttpRetryPolicy(
                        attempts=cast(
                            Any,
                            _number_or_reference(
                                fm.get(f"{p}.retry.attempts"), int, op.retry.attempts
                            ),
                        ),
                        backoff_seconds=cast(
                            Any,
                            _number_or_reference(
                                fm.get(f"{p}.retry.backoff_seconds"),
                                float,
                                op.retry.backoff_seconds,
                            ),
                        ),
                        retry_on_statuses=cast(Any, notification_retry_statuses),
                    ),
                    expected_statuses=cast(Any, notification_expected_statuses),
                    network_allowlist=cast(Any, network_allowlist),
                )
            elif isinstance(op, AgentOperation):
                dc_raw = fm.get(f"{p}.dynamic_count", "1")
                dc: int | str
                try:
                    dc = int(str(dc_raw))
                except (ValueError, TypeError):
                    dc = str(dc_raw)
                prompt_path_raw = fm.get(f"{p}.prompt_path")
                new_op = AgentOperation(
                    type=op.type,
                    agent_id=fm.get(f"{p}.agent_id") or op.agent_id,
                    prompt_path=(
                        _as_path_or_none(prompt_path_raw)
                        if f"{p}.prompt_path" in fm
                        else _as_path_or_none(prompt_path_raw)
                    ),
                    working_dir=_as_path(fm.get(f"{p}.working_dir"), op.working_dir),
                    profile=_empty_to_none(fm.get(f"{p}.profile")),
                    model=_empty_to_none(fm.get(f"{p}.model")),
                    timeout=_optional_float(fm.get(f"{p}.timeout")),
                    skill_name=_empty_to_none(fm.get(f"{p}.skill_name")),
                    dynamic_count=cast(Any, dc),
                    memory=fm.get(f"{p}.memory") or op.memory,
                    input_mapping=fm.get(f"{p}.input_mapping") or {},
                    output_schema=_parse_output_schema_field(fm.get(f"{p}.output_schema")),
                    repair_attempts=int(fm.get(f"{p}.repair_attempts") or 0),
                    fan_source=op.fan_source,
                )
            else:
                continue

            rc = fm.get(f"{p}.retry_count")
            rd = fm.get(f"{p}.retry_delay_seconds")
            ts = fm.get(f"{p}.timeout_seconds")
            po = fm.get(f"{p}.pipe_output")

            wf.graph._nodes[nid] = node.model_copy(
                update={
                    "operation": new_op,
                    "retry_count": int(rc) if rc is not None else 0,
                    "retry_delay_seconds": float(rd) if rd is not None else 1.0,
                    "timeout_seconds": float(ts) if ts is not None else None,
                    "pipe_output": bool(po) if po is not None else False,
                }
            )


# ── Agent ↔ sections ──────────────────────────────────────────────────────────


def agent_to_sections(cfg: AgentConfig) -> list[Section]:
    fields: list[FieldDescriptor] = [
        FieldDescriptor(
            "agent.agent_id", "Agent ID", FieldKind.STRING, cfg.agent_id, read_only=True
        ),
        FieldDescriptor(
            "agent.subscription",
            "Subscription",
            FieldKind.CHOICE,
            cfg.subscription,
            choices=[
                "claude_code",
                "codex",
                "cursor",
                "copilot",
                "opencode",
                "antigravity",
                "grok",
            ],
        ),
        FieldDescriptor("agent.working_dir", "Working Dir", FieldKind.PATH, cfg.working_dir),
        FieldDescriptor("agent.prompt_path", "Prompt Path", FieldKind.PATH, cfg.prompt_path),
        FieldDescriptor("agent.tools", "Tools", FieldKind.LIST_STR, list(cfg.tools)),
        FieldDescriptor(
            "agent.mcp_servers",
            "MCP Servers",
            FieldKind.LIST_STR,
            list(cfg.mcp_servers),
        ),
        FieldDescriptor(
            "agent.extra_paths",
            "Extra Sandbox Paths",
            FieldKind.LIST_STR,
            list(cfg.extra_paths),
        ),
        FieldDescriptor("agent.env", "Environment", FieldKind.DICT_STR_STR, dict(cfg.env)),
    ]
    return [Section(f"Agent: {cfg.agent_id}", fields)]


def sections_to_agent(sections: list[Section], cfg: AgentConfig) -> AgentConfig:
    fm: dict[str, Any] = {fd.key: fd.value for sec in sections for fd in sec.fields}
    prompt_path_raw = fm.get("agent.prompt_path")
    return cfg.model_copy(
        update={
            "subscription": fm.get("agent.subscription") or cfg.subscription,
            "working_dir": _as_path(fm.get("agent.working_dir"), cfg.working_dir),
            "prompt_path": (
                cfg.prompt_path if prompt_path_raw is None else _as_path_or_none(prompt_path_raw)
            ),
            "tools": fm.get("agent.tools") or [],
            "mcp_servers": fm.get("agent.mcp_servers") or [],
            "extra_paths": [
                _as_path(path, Path(".")) for path in fm.get("agent.extra_paths") or []
            ],
            "env": fm.get("agent.env") or {},
        }
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_fan_source(fm: dict[str, Any], prefix: str, fallback: FanSource) -> FanSource:
    source_type = str(fm.get(f"{prefix}.source.type") or fallback.type)
    max_concurrency = _required_int(
        fm.get(f"{prefix}.source.max_concurrency", fallback.max_concurrency)
    )
    fail_fast = bool(fm.get(f"{prefix}.source.fail_fast"))

    if source_type == "tabular":
        path = _as_path(
            fm.get(f"{prefix}.source.path"),
            fallback.path if isinstance(fallback, TabularFanSource) else Path("."),
        )
        return TabularFanSource(
            type="tabular",
            path=path,
            max_concurrency=max_concurrency,
            fail_fast=fail_fast,
        )
    if source_type == "directory":
        path = _as_path(
            fm.get(f"{prefix}.source.path"),
            fallback.path if isinstance(fallback, DirectoryFanSource) else Path("."),
        )
        glob = fallback.glob if isinstance(fallback, DirectoryFanSource) else "*"
        return DirectoryFanSource(
            type="directory",
            path=path,
            glob=str(fm.get(f"{prefix}.source.glob") or glob),
            include_content=bool(fm.get(f"{prefix}.source.include_content")),
            max_concurrency=max_concurrency,
            fail_fast=fail_fast,
        )
    if source_type == "trigger_events":
        return TriggerEventsFanSource(
            type="trigger_events",
            include_content=bool(fm.get(f"{prefix}.source.include_content")),
            max_concurrency=max_concurrency,
            fail_fast=fail_fast,
        )
    if source_type == "infinite":
        return InfiniteFanSource(
            type="infinite",
            max_concurrency=max_concurrency,
            fail_fast=fail_fast,
        )

    fallback_count: int | str | None = fallback.count if isinstance(fallback, CountFanSource) else 1
    count_raw = fm.get(f"{prefix}.source.count", fallback_count)
    try:
        count: int | str | None = int(str(count_raw))
    except (TypeError, ValueError):
        count = str(count_raw) if count_raw is not None else None
    return CountFanSource(
        type="count",
        count=cast(Any, count),
        max_concurrency=max_concurrency,
        fail_fast=fail_fast,
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return _required_int(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return _required_float(value)


def _required_int(value: object) -> int:
    return int(str(value))


def _required_float(value: object) -> float:
    return float(str(value))


def _is_runtime_reference(value: object) -> bool:
    text = str(value).strip()
    return text.startswith("{{") and text.endswith("}}")


def _number_or_reference(
    value: object,
    converter: Callable[[str], int | float],
    fallback: object,
) -> int | float | str:
    if value is None or value == "":
        value = fallback
    if _is_runtime_reference(value):
        return str(value).strip()
    return converter(str(value))


def _integer_list_or_reference(value: object) -> list[int] | str:
    values = value if isinstance(value, list) else [value]
    normalized = [str(item).strip() for item in values if item is not None and str(item).strip()]
    if len(normalized) == 1 and _is_runtime_reference(normalized[0]):
        return normalized[0]
    return [int(item) for item in normalized]


def _string_list_or_reference(value: object) -> list[str] | str:
    values = value if isinstance(value, list) else [value]
    normalized = [str(item).strip() for item in values if item is not None and str(item).strip()]
    if len(normalized) == 1 and _is_runtime_reference(normalized[0]):
        return normalized[0]
    return normalized


def _empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_path(raw: Any, fallback: Path) -> Path:  # noqa: ANN401
    if raw is None:
        return fallback
    if isinstance(raw, Path):
        return raw
    return Path(str(raw)).expanduser()


def _as_path_or_none(raw: Any) -> Path | None:  # noqa: ANN401
    if raw is None:
        return None
    if isinstance(raw, Path):
        return raw
    s = str(raw).strip()
    return Path(s).expanduser() if s else None
