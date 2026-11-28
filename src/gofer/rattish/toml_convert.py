"""Convert a workflow saved in the old TOML format into a Rattish workflow.

Raticode no longer loads TOML workflows. This module reads one with the standard
library's ``tomllib`` only, writes the matching ``workflow.rattish`` and lists what
it couldn't carry over, so a user's old workflow isn't lost. It never changes or
deletes the TOML file.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gofer.rattish.workspaces import RegisteredWorkflow

MAX_TOML_BYTES = 4 * 1024 * 1024

_PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")
# A placeholder in converted text: not part of an escaped {{{{literal}}}}.
_LIVE_PLACEHOLDER = re.compile(r"(?<!\{)\{\{(?!\{)([^{}]+)\}\}(?!\})")
_REFERENCE_ROOTS = frozenset(
    {"inputs", "params", "vars", "secret", "nodes", "trigger", "workflow", "run", "loop"}
    | {"item", "items", "previous"}
)
_RESERVED_WORDS = frozenset(
    "rattish workflow node needs to with from default when otherwise and or not contains "
    "matches exists is null true false none succeeded failed pass fail".split()
)
# Rattish reads a binding as a predicate when one of these words sits between hyphens,
# so node and binding names avoid them as segments.
_RESERVED_SEGMENTS = {"and": "plus", "or": "alt", "contains": "has", "matches": "fits"}
_PROVIDERS = {
    "claude_code": "claude-code",
    "openai_api": "openai-api",
    "anthropic_api": "anthropic-api",
}
_SENSITIVE_NAME = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|auth(?:orization)?|bearer|credential|key|pass(?:word)?|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
_JSON_MEMBER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*")
# The output field that held a step's text in a TOML run, by Rattish node type.
# None means the whole output.
_MAIN_FIELD: dict[str, str | None] = {
    "bash-command": "stdout",
    "python-script": "stdout",
    "shell-script": "stdout",
    "read-file": "content",
    "prompt-file": "prompt",
    "write-file": "path",
    "copy-file": "destination_path",
    "move-file": "destination_path",
    "delete-file": "path",
    "file": "path",
    "folder": "path",
    "open-resource": "target",
    "http-request": "body",
    "approval-gate": "decision",
    "notification": "body",
    "agent": None,
    "common-llm-task": None,
    "local-search": None,
    "local-vectorize": None,
}
_TEXT_OUTPUT = {"agent", "common-llm-task"}
_STDIN_TYPES = {"bash-command", "python-script", "shell-script"}
# Step types that used a TOML predecessor's piped output; the rest ignored it.
_PIPED_CONSUMERS = _STDIN_TYPES | {"write-file", "prompt-file", "agent", "common-llm-task"}
_AGENT_TYPES = {"agent", "common-llm-task"}
_GRAPH_FIELDS = {
    "id",
    "type",
    "label",
    "inputs",
    "pipe_output",
    "allow_failure",
    "await_all_inputs",
    "timeout_seconds",
    "retry_count",
    "retry_delay_seconds",
    "max_concurrency",
    "fail_fast",
    "for_each",
    "on_failure",
}
_UNSUPPORTED_TYPES = {
    "loop": "loops aren't converted; rebuild it with a loop node",
    "break": "break steps belong to a loop, which isn't converted",
    "subflow": "subflows aren't converted; save the component as its own workflow",
    "workflow": "the called TOML workflow must be converted first, then called by workflow-id",
}
_OPERATORS = {
    "equals": "==",
    "not_equals": "!=",
    "greater_than": ">",
    "greater_than_or_equal": ">=",
    "less_than": "<",
    "less_than_or_equal": "<=",
    "matches": "matches",
}
_INPUT_TYPES = {"string", "number", "integer", "boolean"}


class TomlConversionError(ValueError):
    """The TOML file can't be read, or its conversion can't be used as it is."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.problems = problems


@dataclass(frozen=True)
class TomlConversion:
    source: str
    workflow_name: str
    notes: tuple[str, ...]
    unsupported: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflowName": self.workflow_name,
            "notes": list(self.notes),
            "unsupported": list(self.unsupported),
        }


def convert_toml_workflow(path: Path, *, source_label: str = "workflow.rattish") -> TomlConversion:
    """Convert one TOML workflow file. ``source_label`` names the result in printed commands."""
    converter = _Converter(path.expanduser())
    return converter.convert(source_label)


def convert_toml_into_project(
    path: Path,
    project_root: Path,
    *,
    registry_dir: Path | None = None,
    name: str | None = None,
    allow_partial: bool = False,
) -> tuple[RegisteredWorkflow, TomlConversion]:
    """Convert a TOML workflow and register the result in ``project_root``.

    Refuses when something couldn't be converted, unless ``allow_partial``; the
    registered workflow must compile either way. The TOML file stays as it is.
    """
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.workspaces import RattishWorkspaceError, install_registered_workflow

    conversion = convert_toml_workflow(path)
    if conversion.unsupported and not allow_partial:
        raise TomlConversionError(
            f"{path.name} has parts Raticode can't convert. Convert anyway with allow partial "
            "and finish those parts by hand.",
            conversion.unsupported,
        )
    workflow_name = (name or "").strip() or conversion.workflow_name
    staged = Path(tempfile.mkdtemp(prefix="raticode-toml-convert-"))
    try:
        (staged / "workflow.rattish").write_text(conversion.source, encoding="utf-8")
        try:
            registered = install_registered_workflow(
                project_root,
                staged,
                workflow_name,
                path.stem,
                registry_dir=registry_dir,
            )
        except RattishError as exc:
            problems = tuple(
                f"{item.code}: {item.message}"
                for item in exc.diagnostics
                if item.severity == "error"
            )
            raise TomlConversionError(
                f"The converted {path.name} doesn't pass rattish check.", problems
            ) from exc
        except RattishWorkspaceError as exc:
            raise TomlConversionError(str(exc)) from exc
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return registered, _relabel(conversion, str(registered.entrypoint))


def legacy_toml_workflow_files(data_dir: Path) -> list[Path]:
    """TOML workflow files at the top of ``data_dir``, which Raticode no longer loads."""
    try:
        candidates = sorted(data_dir.glob("*.toml"))
    except OSError:
        return []
    found: list[Path] = []
    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.stat().st_size > MAX_TOML_BYTES:
                continue
            document = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        if _looks_like_workflow(document):
            found.append(candidate)
    return found


def _looks_like_workflow(document: Mapping[str, Any]) -> bool:
    return isinstance(document.get("workflow"), dict) or isinstance(document.get("nodes"), list)


def _relabel(conversion: TomlConversion, source_label: str) -> TomlConversion:
    quoted = _quote_arg(source_label)
    return TomlConversion(
        source=conversion.source,
        workflow_name=conversion.workflow_name,
        notes=tuple(
            note.replace(_quote_arg("workflow.rattish"), quoted) for note in conversion.notes
        ),
        unsupported=conversion.unsupported,
    )


def _quote_arg(value: str) -> str:
    # Without ensure_ascii a shell would get "\u00f1" instead of the letter.
    return value if re.fullmatch(r"[\w./~@:+-]+", value) else json.dumps(value, ensure_ascii=False)


class _Raw(str):
    """Text that is already Rattish source, such as a reference or a duration."""


@dataclass
class _Binding:
    source: str
    default: bool = False


@dataclass
class _Node:
    node_id: str
    old_id: str
    node_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    bindings: dict[str, _Binding] = field(default_factory=dict)
    needs: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    allow_fail: bool = False
    finish: str | None = None
    common: dict[str, Any] = field(default_factory=dict)
    comment: str = ""


class _Converter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.base = path.parent
        self.notes: list[str] = []
        self.unsupported: list[str] = []
        self.inputs: dict[str, dict[str, Any]] = {}
        self.input_order: list[str] = []
        self.nodes: dict[str, _Node] = {}
        self.ids: dict[str, str] = {}
        self.raw_nodes: dict[str, dict[str, Any]] = {}
        self.predecessors: dict[str, list[str]] = {}
        self.edges: list[dict[str, Any]] = []
        self.output_schemas: dict[str, Any] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self._contracts: dict[str, dict[str, Any]] = {}
        self.binding_lines: dict[int, tuple[str, str]] = {}

    # Reading -----------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            if self.path.stat().st_size > MAX_TOML_BYTES:
                raise TomlConversionError(f"{self.path.name} is larger than 4 MB.")
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TomlConversionError(f"Can't read {self.path}: {exc.strerror or exc}") from exc
        except UnicodeDecodeError as exc:
            raise TomlConversionError(f"{self.path.name} isn't UTF-8 text.") from exc
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise TomlConversionError(f"{self.path.name} isn't valid TOML: {exc}") from exc
        if not _looks_like_workflow(document):
            raise TomlConversionError(
                f"{self.path.name} isn't a Raticode workflow: it has no [workflow] table or nodes."
            )
        return document

    def convert(self, source_label: str) -> TomlConversion:
        document = self._load()
        workflow = document.get("workflow") if isinstance(document.get("workflow"), dict) else {}
        assert isinstance(workflow, dict)
        name = str(workflow.get("name") or workflow.get("id") or self.path.stem).strip()
        name = name or self.path.stem
        self.output_schemas = dict(workflow.get("output_schemas") or {})
        agents = document.get("agents") or {}
        self.agents = {str(k): v for k, v in agents.items() if isinstance(v, dict)}
        self._workflow_inputs(workflow)
        self._workflow_extras(workflow, document)
        raw_nodes = [item for item in document.get("nodes") or [] if isinstance(item, dict)]
        used: set[str] = set()
        for index, raw in enumerate(raw_nodes):
            old_id = str(raw.get("id") or f"node-{index + 1}")
            new_id = _identifier(old_id, used)
            used.add(new_id)
            if old_id in self.ids:
                self.unsupported.append(f"node {old_id}: the ID is used twice; the second copy")
                continue
            self.ids[old_id] = new_id
            self.raw_nodes[old_id] = raw
            if new_id != old_id:
                self.notes.append(f"Node {old_id} is now {new_id}: Rattish IDs use this form.")
        self._read_edges(document)
        for old_id, raw in self.raw_nodes.items():
            self.nodes[old_id] = self._node(old_id, raw)
        self._routes()
        self._agent_sandbox_note()
        defaults: set[tuple[str, str]] = set()
        source = self._render(name, workflow, defaults)
        # The compiler proves which steps may not have run; each pass can uncover more.
        for _ in range(6):
            found = set(self._bindings_needing_defaults(source)) - defaults
            if not found:
                break
            defaults |= found
            source = self._render(name, workflow, defaults)
        source = _formatted(source)
        conversion = TomlConversion(
            source=source,
            workflow_name=name,
            notes=tuple(dict.fromkeys(self.notes)),
            unsupported=tuple(dict.fromkeys(self.unsupported)),
        )
        return _relabel(conversion, source_label)

    def _contract(self, node_type: str) -> dict[str, Any]:
        if node_type not in self._contracts:
            from gofer.rattish.artifacts import rattish_asset_root

            path = rattish_asset_root() / "contracts" / f"{node_type}.json"
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = {}
            schema = document.get("configuration_schema") or {}
            self._contracts[node_type] = dict(schema.get("properties") or {})
        return self._contracts[node_type]

    # Workflow --------------------------------------------------------------

    def _workflow_inputs(self, workflow: Mapping[str, Any]) -> None:
        for table in ("parameters", "inputs"):
            for raw_name, spec in (workflow.get(table) or {}).items():
                if isinstance(spec, dict):
                    self._declare_input(str(raw_name), _input_from_parameter(spec))
        for raw_name, spec in (workflow.get("variables") or {}).items():
            if not isinstance(spec, dict):
                continue
            initial = spec.get("initial")
            if isinstance(initial, str) and _PLACEHOLDER.search(initial):
                self.unsupported.append(
                    f"workflow variable {raw_name}: its starting value {initial!r} uses other "
                    "values; set the input by hand"
                )
                initial = None
            declaration = _input_from_parameter(
                {
                    "type": spec.get("type") if spec.get("type") in _INPUT_TYPES else "string",
                    "description": spec.get("description"),
                    "default": initial,
                    "secret": spec.get("secret", False),
                }
            )
            self._declare_input(str(raw_name), declaration)

    def _declare_input(self, raw_name: str, declaration: dict[str, Any]) -> str:
        name = _identifier(raw_name, set())
        if name not in self.inputs:
            self.inputs[name] = declaration
            self.input_order.append(name)
        return name

    def _workflow_extras(self, workflow: Mapping[str, Any], document: Mapping[str, Any]) -> None:
        source = _quote_arg("workflow.rattish")
        schedule = workflow.get("schedule")
        if isinstance(schedule, dict) and schedule.get("cron_expression"):
            command = (
                f"Raticode rattish triggers add cron {source} --cron "
                f"{json.dumps(str(schedule['cron_expression']))}"
            )
            if schedule.get("timezone"):
                command += f" --timezone {_quote_arg(str(schedule['timezone']))}"
            command += _trigger_inputs(schedule)
            self.notes.append(
                f"The schedule is a trigger on each computer now. Add it with: {command}"
            )
        watch = workflow.get("watch")
        if isinstance(watch, dict) and watch.get("path"):
            command = (
                f"Raticode rattish triggers add file {source} --path "
                f"{_quote_arg(str(watch['path']))}"
            )
            if watch.get("glob") not in (None, "*"):
                command += f" --glob {json.dumps(str(watch['glob']))}"
            command += " --recursive" if watch.get("recursive") else " --no-recursive"
            if watch.get("debounce_seconds") not in (None, 1.0):
                command += f" --debounce {watch['debounce_seconds']}"
            command += _trigger_inputs(watch)
            self.notes.append(
                f"The folder watch is a trigger on each computer now. Add it with: {command}"
            )
            if watch.get("mode") not in (None, "batch"):
                self.unsupported.append(
                    f"watch mode {watch['mode']}: file triggers start one run for each batch "
                    "of changes"
                )
        for trigger_id, webhook in sorted((workflow.get("webhooks") or {}).items()):
            if not isinstance(webhook, dict):
                continue
            label = _quote_arg(str(trigger_id))
            command = f"Raticode rattish triggers add webhook {source} --label {label}"
            self.notes.append(
                f"Webhook {trigger_id} is a trigger with its own address and token now. "
                f"Add it with: {command}"
            )
            if webhook.get("token") or webhook.get("token_env"):
                self.notes.append(
                    f"Webhook {trigger_id}'s old token isn't copied. The new trigger makes its own."
                )
            if webhook.get("input_bindings") or webhook.get("fanout_path"):
                self.unsupported.append(
                    f"webhook {trigger_id}: its input bindings and fan-out read the request "
                    "body; read trigger values in the workflow instead"
                )
        if workflow.get("run_continuously"):
            self.unsupported.append(
                "run_continuously: Rattish has no always-running mode; use a cron or file trigger"
            )
        if workflow.get("filesystem_access"):
            self.notes.append(
                "filesystem_access isn't carried over: agent steps run in the sandbox, which "
                "allows their working folder; add other folders with extra-paths."
            )
        if workflow.get("resource_limits"):
            self.notes.append(
                "resource_limits aren't carried over; set timeout, max-runs or max-cost-usd "
                "in the workflow block instead."
            )
        components = document.get("component") or document.get("components")
        if components:
            self.unsupported.append(
                "[component] tables: subflow components aren't converted; save each as its "
                "own workflow and call it with a workflow node"
            )

    def _agent_sandbox_note(self) -> None:
        if any(node.node_type in _AGENT_TYPES for node in self.nodes.values()):
            self.notes.append(
                "Agent steps run in Raticode's sandbox (sandbox: on), which limits their "
                "writes to the working folder. Set sandbox: off on a step that needs more."
            )

    # Graph -----------------------------------------------------------------

    def _read_edges(self, document: Mapping[str, Any]) -> None:
        for raw in document.get("edges") or []:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("from") or raw.get("from_node") or "")
            target = str(raw.get("to") or raw.get("to_node") or "")
            if source not in self.raw_nodes or target not in self.raw_nodes:
                self.unsupported.append(
                    f"edge {source or '?'} -> {target or '?'}: it names a node that doesn't exist"
                )
                continue
            edge = dict(raw)
            edge["from"], edge["to"] = source, target
            edge["condition"] = str(raw.get("condition") or "always")
            self.edges.append(edge)
            predecessors = self.predecessors.setdefault(target, [])
            if source not in predecessors:
                predecessors.append(source)

    def _may_fail(self, old_id: str) -> bool:
        raw = self.raw_nodes[old_id]
        return bool(raw.get("allow_failure")) or any(
            edge["from"] == old_id and edge["condition"] == "on_failure" for edge in self.edges
        )

    def _routes(self) -> None:
        for edge in self.edges:
            source = self.nodes.get(edge["from"])
            target = self.nodes.get(edge["to"])
            if source is None or target is None:
                continue
            route = self._route(edge, source, target)
            if route is not None and route not in source.routes:
                source.routes.append(route)
        for old_id, node in self.nodes.items():
            # Without needs, a Rattish step also runs when the workflow starts. A TOML step
            # waited for every step with an edge into it.
            node.needs = [self.ids[item] for item in self.predecessors.get(old_id, [])]
            if len(node.needs) > 1 and self.raw_nodes[old_id].get("await_all_inputs") is False:
                self.unsupported.append(
                    f"node {old_id}: await_all_inputs = false; it now waits for "
                    f"{', '.join(self.predecessors[old_id])}"
                )
            if node.finish and node.routes:
                node.finish = None

    def _route(self, edge: Mapping[str, Any], source: _Node, target: _Node) -> str | None:
        condition = edge["condition"]
        where = f"edge {source.old_id} -> {target.old_id}"
        if condition == "always":
            return target.node_id
        if condition == "on_success":
            return f"{target.node_id} when succeeded" if source.allow_fail else target.node_id
        if condition == "on_failure":
            return f"{target.node_id} when failed"
        if condition in {"output_matches", "output_no_match"}:
            pattern = edge.get("output_pattern")
            text = self._text_reference(source)
            if pattern is None and condition == "output_no_match":
                return f"{target.node_id} otherwise"
            if text is None or pattern is None:
                self.unsupported.append(f"{where}: output match on a step without text output")
                return None
            predicate = f"{text} matches {json.dumps(str(pattern), ensure_ascii=False)}"
            if condition == "output_no_match":
                predicate = f"not ({predicate})"
            return f"{target.node_id} when {predicate}"
        if condition == "output_field":
            field_predicate = self._field_predicate(source, edge)
            if field_predicate is None:
                self.unsupported.append(f"{where}: its output_field condition isn't converted")
                return None
            return f"{target.node_id} when {field_predicate}"
        self.unsupported.append(f"{where}: condition {condition} isn't converted")
        return None

    def _field_predicate(self, source: _Node, edge: Mapping[str, Any]) -> str | None:
        selector = _selectors(str(edge.get("field") or ""))
        operator = str(edge.get("operator") or "")
        if selector is None:
            return None
        reference = f"node.{source.node_id}.output{selector}"
        value = edge.get("value")
        if operator == "exists":
            return f"exists {reference}"
        if operator in {"in", "not_in"} and isinstance(value, list) and value:
            literals = [_comparable(item) for item in value]
            if any(item is None for item in literals):
                return None
            joined = " or ".join(f"{reference} == {item}" for item in literals)
            return f"({joined})" if operator == "in" else f"not ({joined})"
        symbol = _OPERATORS.get(operator)
        literal = _comparable(value)
        if symbol is None or literal is None:
            return None
        return f"{reference} {symbol} {literal}"

    def _text_reference(self, node: _Node) -> str | None:
        main = _MAIN_FIELD.get(node.node_type)
        if main is not None:
            return f"node.{node.node_id}.output.{main}"
        if node.node_type in _TEXT_OUTPUT and "output-schema" not in node.fields:
            return f"node.{node.node_id}.output"
        return None

    def _output_reference(self, old_id: str, rest: list[str] | None = None) -> str | None:
        """The Rattish reference for a TOML ``ID.output…`` path, or None."""
        node_id = self.ids.get(old_id)
        raw = self.raw_nodes.get(old_id)
        if node_id is None or raw is None:
            return None
        node_type = _node_type(str(raw.get("type") or ""))
        rest = rest or ["output"]
        head, tail = rest[0], rest[1:]
        if head == "output" and not tail:
            main = _MAIN_FIELD.get(node_type)
            return f"node.{node_id}.output" + (f".{main}" if main else "")
        if head in {"output", "value", "data"} and tail:
            selector = _selectors(".".join(tail))
            return None if selector is None else f"node.{node_id}.output{selector}"
        return None

    # Nodes -----------------------------------------------------------------

    def _node(self, old_id: str, raw: dict[str, Any]) -> _Node:
        raw_type = str(raw.get("type") or "")
        node_type = _node_type(raw_type)
        node = _Node(node_id=self.ids[old_id], old_id=old_id, node_type=node_type)
        node.allow_fail = self._may_fail(old_id)
        self._common(node, raw)
        where = f"node {old_id}"
        if raw_type in {"start", "pass", "fail"}:
            node.node_type = "bash-command"
            node.fields["command"] = "exit 1" if raw_type == "fail" else "exit 0"
            if raw_type != "start":
                node.finish = raw_type
            if raw.get("message"):
                node.comment = str(raw["message"])
            return node
        if raw_type in _UNSUPPORTED_TYPES or not self._contract(node_type):
            reason = _UNSUPPORTED_TYPES.get(raw_type, f"type {raw_type!r} has no Rattish step")
            self.unsupported.append(f"{where}: {reason}")
            node.node_type = "bash-command"
            node.fields["command"] = f"echo {json.dumps(f'Convert {old_id} by hand')} && exit 1"
            return node
        if node_type in _AGENT_TYPES:
            self._agent(node, raw)
        else:
            self._operation(node, raw)
        self._explicit_inputs(node, raw)
        self._piped_input(node, raw)
        return node

    def _common(self, node: _Node, raw: Mapping[str, Any]) -> None:
        where = f"node {node.old_id}"
        timeout = _duration(raw.get("timeout_seconds"))
        if timeout is not None:
            node.common["timeout"] = _Raw(timeout)
        if isinstance(raw.get("retry_count"), int) and raw["retry_count"] > 0:
            node.common["retry-count"] = raw["retry_count"]
            delay = _duration(raw.get("retry_delay_seconds"))
            if delay is not None and delay != "1s":
                node.common["retry-delay"] = _Raw(delay)
        if raw.get("for_each"):
            self.unsupported.append(f"{where}: for_each fan-out isn't converted; use a loop node")
        if raw.get("label"):
            self.notes.append(
                f"Node {node.node_id}'s label {raw['label']!r} isn't kept; Rattish shows the ID."
            )
        if raw.get("on_failure") not in (None, "halt"):
            self.unsupported.append(f"{where}: on_failure = {raw['on_failure']!r} isn't converted")

    def _operation(self, node: _Node, raw: Mapping[str, Any]) -> None:
        contract = self._contract(node.node_type)
        where = f"node {node.old_id}"
        for key, value in raw.items():
            if key in _GRAPH_FIELDS:
                continue
            if key == "retry" and isinstance(value, dict):
                value = _retry(value)
            if key not in contract:
                self.unsupported.append(f"{where}: field {key} isn't carried over")
                continue
            self._set_field(node, key, value, contract.get(key) or {})

    def _set_field(self, node: _Node, key: str, value: Any, schema: Mapping[str, Any]) -> None:
        if value is None or value == [] or value == {}:
            return
        if schema.get("x-rattish-type") == "Path" and isinstance(value, str):
            value = self._path(value)
        elif (
            isinstance(schema.get("items"), dict)
            and schema["items"].get("x-rattish-type") == "Path"
            and isinstance(value, list)
        ):
            value = [self._path(str(item)) for item in value]
        if key == "env" and isinstance(value, dict):
            value = self._env(node, value)
        elif key in {"headers", "params"} and isinstance(value, dict):
            value = {
                str(name): self._secret_value(node, f"{key}.{name}", str(name), item)
                for name, item in value.items()
            }
        elif key == "smtp_password" and isinstance(value, str):
            value = self._secret_value(node, key, key, value)
        value = self._templated(node, value)
        node.fields[key.replace("_", "-")] = value

    def _path(self, value: str) -> str:
        if not value or "{{" in value or value.startswith("~"):
            return value
        path = Path(value)
        return value if path.is_absolute() else str((self.base / path).resolve())

    def _env(self, node: _Node, env: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in env.items():
            result[str(name)] = self._secret_value(node, f"env.{name}", str(name), value)
        return result

    def _secret_value(self, node: _Node, field_name: str, secret_hint: str, value: Any) -> str:
        """Keep a plaintext credential out of the source and say how to save it."""
        text = str(value)
        if not text or "{{" in text or not _SENSITIVE_NAME.search(secret_hint):
            return text
        secret_name = re.sub(r"[^A-Za-z0-9_]+", "_", secret_hint).strip("_").upper() or "SECRET"
        local = self._bind(
            node, _identifier(f"secret-{secret_name}", set()), f'secret["{secret_name}"]'
        )
        self.notes.append(
            f"Node {node.node_id}'s {field_name} held a plain value. It now reads the workflow "
            f"secret {secret_name}; save it with: Raticode rattish secrets set {secret_name}"
        )
        return "{{" + local + "}}"

    def _agent(self, node: _Node, raw: Mapping[str, Any]) -> None:
        where = f"node {node.old_id}"
        agent_id = str(raw.get("agent_id") or "")
        agent = self.agents.get(agent_id)
        if agent is None:
            self.unsupported.append(f"{where}: agent {agent_id!r} isn't defined in [agents]")
            agent = {}
        subscription = str(agent.get("subscription") or "codex")
        node.fields["provider"] = _Raw(_PROVIDERS.get(subscription, subscription.replace("_", "-")))
        for key in ("profile", "model", "effort"):
            value = raw.get(key) or agent.get(key)
            if value:
                node.fields[key] = str(value)
        working_dir = raw.get("working_dir") or agent.get("working_dir")
        if working_dir:
            node.fields["working-dir"] = self._path(str(working_dir))
        if node.node_type == "common-llm-task":
            for key in ("task", "target", "instructions"):
                if raw.get(key) not in (None, ""):
                    node.fields[key] = raw[key]
        memory = raw.get("memory")
        if memory not in (None, "none"):
            node.fields["memory"] = _Raw(str(memory))
        if node.node_type == "agent":
            for key in ("tools", "mcp_servers"):
                if agent.get(key):
                    node.fields[key.replace("_", "-")] = list(agent[key])
            if agent.get("extra_paths"):
                node.fields["extra-paths"] = [
                    self._path(str(item)) for item in agent["extra_paths"]
                ]
            if agent.get("env"):
                node.fields["env"] = self._env(node, agent["env"])
            if raw.get("skill_name"):
                node.fields["skill"] = str(raw["skill_name"]).strip().lstrip("/")
            else:
                prompt = self._agent_prompt(
                    node, raw.get("prompt_path") or agent.get("prompt_path")
                )
                if prompt is not None:
                    node.fields["prompt"] = prompt
        timeout = _duration(raw.get("timeout"))
        if timeout is not None and "timeout" not in node.common:
            node.common["timeout"] = _Raw(timeout)
        schema = raw.get("output_schema")
        if isinstance(schema, str):
            schema = self.output_schemas.get(schema)
            if schema is None:
                self.unsupported.append(
                    f"{where}: output schema {raw['output_schema']!r} is missing"
                )
        if isinstance(schema, dict):
            node.fields["output-schema"] = schema
        if isinstance(raw.get("repair_attempts"), int) and raw["repair_attempts"] > 0:
            node.fields["repair-attempts"] = raw["repair_attempts"]
        if raw.get("fan_source") or raw.get("dynamic_count", 1) not in (1, "1"):
            self.unsupported.append(f"{where}: agent fan-out isn't converted; use a loop node")
        for key, value in (raw.get("input_mapping") or {}).items():
            self._map_input(node, str(key), value)
        for key in ("target", "instructions", "prompt"):
            if key in node.fields:
                node.fields[key] = self._templated(node, node.fields[key])
        if node.node_type == "agent" and "file_content" in (raw.get("input_mapping") or {}):
            prompt = str(node.fields.get("prompt", ""))
            local = _identifier("file_content", set())
            if "{{" + local not in prompt:
                node.fields["prompt"] = f"{prompt}\n\n{{{{{local}}}}}".lstrip()

    def _agent_prompt(self, node: _Node, prompt_path: Any) -> str | None:
        if not prompt_path:
            return None
        candidates = [Path(str(prompt_path)).expanduser()]
        if not candidates[0].is_absolute():
            candidates = [self.base / candidates[0], self.base / "prompts" / candidates[0]]
        for candidate in candidates:
            try:
                return candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        node.fields["prompt-path"] = str(candidates[0].resolve())
        self.notes.append(
            f"Node {node.node_id}'s prompt file {prompt_path} couldn't be read, so it is still "
            "used from there. Rename its {{placeholders}} to the step's with names."
        )
        return None

    def _map_input(self, node: _Node, key: str, value: Any) -> str | None:
        """Bind one TOML input_mapping or inputs entry. Returns the local name."""
        local = _identifier(key, set())
        if isinstance(value, str):
            reference = self._reference(node, value)
            if reference is not None:
                return self._bind(node, local, reference)
            if self._looks_like_reference(value):
                self.unsupported.append(
                    f"node {node.old_id}: input {key} = {value!r} isn't converted"
                )
                return None
        if isinstance(value, (str, int, float, bool)):
            node.fields.setdefault("__literals__", {})[local] = value
            return local
        self.unsupported.append(f"node {node.old_id}: input {key} isn't converted")
        return None

    def _reference(self, node: _Node, value: str) -> str | None:
        stripped = value.strip()
        match = re.fullmatch(r"\{\{\s*([^{}]+?)\s*\}\}", stripped)
        expression = match.group(1) if match else stripped
        if not re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[\w-]+)+", expression):
            return None
        parts = expression.split(".")
        root = parts[0]
        if root in {"inputs", "params", "vars"} and len(parts) >= 2:
            name = self._declare_input(parts[1], {"schema": {"type": "string"}})
            selector = _selectors(".".join(parts[2:])) if len(parts) > 2 else ""
            return f"input.{name}{selector or ''}"
        if root == "secret" and len(parts) == 2:
            return f'secret["{parts[1]}"]'
        if root == "nodes" and len(parts) >= 2:
            parts = parts[1:]
            root = parts[0]
        if root in self.raw_nodes:
            return self._output_reference(root, parts[1:])
        return None

    def _looks_like_reference(self, value: str) -> bool:
        expression = value.strip()
        match = re.fullmatch(r"\{\{\s*([^{}]+?)\s*\}\}", expression)
        expression = match.group(1) if match else expression
        if not re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[\w-]+)+", expression):
            return bool(match)
        root = expression.split(".")[0]
        return root in _REFERENCE_ROOTS or root in self.raw_nodes

    def _bind(self, node: _Node, local: str, source: str) -> str:
        existing = node.bindings.get(local)
        if existing is not None and existing.source != source:
            index = 2
            while f"{local}-{index}" in node.bindings:
                index += 1
            local = f"{local}-{index}"
        node.bindings[local] = _Binding(source)
        return local

    def _explicit_inputs(self, node: _Node, raw: Mapping[str, Any]) -> None:
        inputs = raw.get("inputs") or {}
        if not isinstance(inputs, dict):
            return
        for key, value in inputs.items():
            key = str(key)
            if node.node_type in _STDIN_TYPES:
                if re.fullmatch(r"stdin\d+", key):
                    self.unsupported.append(
                        f"node {node.old_id}: positional input {key} isn't converted"
                    )
                    continue
                env_name = key[4:] if key.startswith("env.") else key
                if key != "stdin" and not _ENV_NAME.fullmatch(env_name):
                    self.notes.append(
                        f"Node {node.node_id}'s input {key} now arrives as the environment "
                        f"variable {_identifier(env_name, set()).replace('-', '_').upper()}."
                    )
                key = "stdin" if key == "stdin" else env_name
            local = self._map_input(node, key, value)
            if local is not None and local in node.fields.get("__literals__", {}):
                if node.node_type in _STDIN_TYPES and key != "stdin":
                    literal = node.fields["__literals__"].pop(local)
                    env = node.fields.setdefault("env", {})
                    env[local.replace("-", "_").upper()] = str(literal)

    def _piped_input(self, node: _Node, raw: Mapping[str, Any]) -> None:
        piped = [
            item
            for item in self.predecessors.get(node.old_id, [])
            if self.raw_nodes[item].get("pipe_output")
        ]
        explicit = isinstance(raw.get("inputs"), dict) and bool(raw.get("inputs"))
        if not piped or "stdin" in node.bindings or node.node_type not in _PIPED_CONSUMERS:
            self._finish_templates(node, [])
            return
        locals_: list[str] = []
        string_sources: list[str] = []
        for item in piped:
            producer = self.nodes.get(item)
            producer_type = (
                producer.node_type
                if producer is not None
                else _node_type(str(self.raw_nodes[item].get("type") or ""))
            )
            reference = self._output_reference(item)
            if reference is None:
                continue
            if _MAIN_FIELD.get(producer_type) is not None or producer_type in _TEXT_OUTPUT:
                string_sources.append(reference)
            local = "piped-input" if len(piped) == 1 else _identifier(self.ids[item], set())
            locals_.append(self._bind(node, local, reference))
        if node.node_type in _STDIN_TYPES or node.node_type == "write-file":
            takes_stdin = node.node_type != "write-file" or not node.fields.get("content")
            if takes_stdin and len(string_sources) == 1 and len(locals_) == 1:
                node.bindings["stdin"] = node.bindings.pop(locals_[0])
            elif takes_stdin and locals_:
                for local in locals_:
                    node.bindings.pop(local, None)
                self.unsupported.append(
                    f"node {node.old_id}: standard input joined from "
                    f"{', '.join(piped)} isn't converted; bind one with stdin"
                )
            else:
                for local in locals_:
                    node.bindings.pop(local, None)
            self._finish_templates(node, [])
            return
        if node.node_type == "agent" and not explicit and "skill" not in node.fields:
            joined = "\n".join("{{" + local + "}}" for local in locals_)
            prompt = str(node.fields.get("prompt", ""))
            node.fields["prompt"] = f"{joined}\n\n{prompt}" if prompt else joined
        elif node.node_type == "agent" and "skill" in node.fields:
            self.notes.append(
                f"Node {node.node_id} runs a skill, so the output of {', '.join(piped)} is "
                "passed to it as with values instead of text before the request."
            )
        self._finish_templates(node, locals_)

    def _finish_templates(self, node: _Node, piped_locals: list[str]) -> None:
        """Resolve {{_piped_input}} and literal locals, and drop unused piped bindings."""
        literals: dict[str, Any] = node.fields.pop("__literals__", {})
        joined = "\n".join("{{" + local + "}}" for local in piped_locals)
        used: set[str] = set()

        def resolve(text: str) -> str:
            def replace(match: re.Match[str]) -> str:
                name = match.group(1).strip()
                if name == "__PIPED__":
                    used.update(piped_locals)
                    return joined
                if name in literals:
                    return _escape_braces(str(literals[name]))
                used.add(name.split(".")[0])
                return match.group(0)

            return _LIVE_PLACEHOLDER.sub(replace, text)

        for key, value in list(node.fields.items()):
            node.fields[key] = _map_strings(value, resolve)
        if node.node_type in {"prompt-file", "common-llm-task"}:
            for local in piped_locals:
                if local not in used:
                    node.bindings.pop(local, None)

    def _templated(self, node: _Node, value: Any) -> Any:
        return _map_strings(value, lambda text: self._template(node, text))

    def _template(self, node: _Node, text: str) -> str:
        parts: list[str] = []
        position = 0
        for match in _PLACEHOLDER.finditer(text):
            parts.append(_escape_braces(text[position : match.start()]))
            parts.append(self._placeholder(node, match.group(1).strip(), match.group(0)))
            position = match.end()
        parts.append(_escape_braces(text[position:]))
        return "".join(parts)

    def _placeholder(self, node: _Node, expression: str, original: str) -> str:
        if expression == "_piped_input":
            return "{{__PIPED__}}"
        parts = expression.split(".")
        raw = self.raw_nodes.get(node.old_id, {})
        mapped = {
            _identifier(str(key), set())
            for table in ("input_mapping", "inputs")
            for key in (raw.get(table) or {})
        }
        local = _identifier(parts[0], set()) if re.fullmatch(r"[\w-]+", parts[0]) else ""
        if local and local in mapped and parts[0] not in self.raw_nodes:
            selector = _selectors(".".join(parts[1:])) if len(parts) > 1 else ""
            if selector is None:
                return self._unconverted(node, original)
            return "{{" + local + selector + "}}"
        reference = self._reference(node, expression) if len(parts) > 1 else None
        if reference is not None:
            if parts[0] in {"inputs", "params", "vars"}:
                name = _identifier(parts[1], set())
                selector = _selectors(".".join(parts[2:])) if len(parts) > 2 else ""
                bound = self._bind(node, name, f"input.{name}")
                return "{{" + bound + (selector or "") + "}}"
            name = (
                f"secret-{parts[1]}"
                if parts[0] == "secret"
                else (parts[1] if parts[0] == "nodes" else parts[0])
            )
            bound = self._bind(node, _identifier(name, set()), reference)
            return "{{" + bound + "}}"
        if len(parts) == 1 and local:
            input_name = self._declare_input(
                parts[0], {"schema": {"type": "string"}, "default": ""}
            )
            self.notes.append(
                f"Node {node.node_id} used {{{{{parts[0]}}}}}, which nothing in the workflow "
                f"sets, so it is the workflow input {input_name} now (empty unless you set it)."
            )
            bound = self._bind(node, local, f"input.{input_name}")
            return "{{" + bound + "}}"
        return self._unconverted(node, original)

    def _unconverted(self, node: _Node, original: str) -> str:
        self.unsupported.append(f"node {node.old_id}: placeholder {original} isn't converted")
        return _escape_braces(original)

    # Rendering -------------------------------------------------------------

    def _render(
        self, name: str, workflow: Mapping[str, Any], defaults: set[tuple[str, str]]
    ) -> str:
        lines = ["rattish: 1", "", f"# Converted from {self.path.name}.", "workflow:"]
        lines.append(f"  name: {json.dumps(name, ensure_ascii=False)}")
        max_runs = workflow.get("max_total_node_runs")
        if isinstance(max_runs, int) and max_runs > 0:
            lines.append(f"  max-runs: {max_runs}")
        if self.input_order:
            lines.append("  inputs:")
            for input_name in self.input_order:
                declaration = self.inputs[input_name]
                lines.append(f"    {input_name}:")
                lines.append(f"      schema: {_json(declaration['schema'])}")
                if "default" in declaration:
                    lines.append(f"      default: {_json(declaration['default'])}")
        self.binding_lines = {}
        for node in self.nodes.values():
            lines.append("")
            if node.comment:
                lines.extend(f"# {line}" for line in node.comment.splitlines() if line.strip())
            lines.append(f"node {node.node_id}:")
            lines.append(f"  type: {node.node_type}")
            for key, value in node.fields.items():
                lines.extend(_field_lines(key, value, lenient=key in {"command", "prompt"}))
            if node.bindings:
                lines.append("  with:")
                for local, binding in node.bindings.items():
                    self.binding_lines[len(lines) + 1] = (node.node_id, local)
                    if binding.default or (node.node_id, local) in defaults:
                        lines.append(f"    {local}:")
                        lines.append(f"      from: {binding.source}")
                        lines.append('      default: ""')
                    else:
                        lines.append(f"    {local}: {binding.source}")
            if node.allow_fail and node.finish != "fail":
                lines.append("  allow-fail: true")
            for key, value in node.common.items():
                lines.append(f"  {key}: {_render_value(value)}")
            if len(node.needs) == 1:
                lines.append(f"  needs: {node.needs[0]}")
            elif node.needs:
                lines.append("  needs:")
                lines.extend(f"    - {item}" for item in node.needs)
            if node.finish:
                lines.append(f"  finish: {node.finish}")
            elif len(node.routes) == 1:
                lines.append(f"  to: {node.routes[0]}")
            elif node.routes:
                lines.append("  to:")
                lines.extend(f"    - {route}" for route in node.routes)
        return "\n".join(lines) + "\n"

    def _bindings_needing_defaults(self, source: str) -> list[tuple[str, str]]:
        """Bindings whose step may not have run; they get an empty default."""
        from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_source
        from gofer.rattish.diagnostics import RattishError

        try:
            result = compile_rattish_source(source, self.base / "workflow.rattish")
            diagnostics = result.diagnostics
        except RattishError as exc:
            diagnostics = exc.diagnostics
        except (RattishArtifactError, OSError, ValueError):
            return []
        found: list[tuple[str, str]] = []
        for diagnostic in diagnostics:
            if diagnostic.code != "RATTISH_BINDING_DEFAULT_REQUIRED":
                continue
            located = self.binding_lines.get(diagnostic.span.start.line)
            if located is not None:
                found.append(located)
        return found


# Helpers -------------------------------------------------------------------


def _identifier(raw: str, used: set[str]) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()
    segments = [_RESERVED_SEGMENTS.get(item, item) for item in text.split("-") if item]
    if not segments:
        segments = ["step"]
    if not segments[0][0].isalpha():
        segments.insert(0, "n")
    identifier = "-".join(segments)
    if identifier in _RESERVED_WORDS:
        identifier = f"{identifier}-step"
    candidate, index = identifier, 2
    while candidate in used:
        candidate = f"{identifier}-{index}"
        index += 1
    return candidate


def _node_type(raw_type: str) -> str:
    return raw_type.strip().lower().replace("_", "-")


def _duration(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    milliseconds = round(float(value) * 1000)
    if milliseconds <= 0:
        return None
    return f"{milliseconds}ms" if milliseconds % 1000 else f"{milliseconds // 1000}s"


def _retry(value: Mapping[str, Any]) -> dict[str, Any]:
    retry: dict[str, Any] = {}
    if value.get("attempts") is not None:
        retry["attempts"] = value["attempts"]
    if value.get("backoff_seconds"):
        retry["backoff"] = _duration(value["backoff_seconds"]) or "0s"
    if value.get("retry_on_statuses"):
        retry["retry_on_statuses"] = value["retry_on_statuses"]
    return retry


def _input_from_parameter(spec: Mapping[str, Any]) -> dict[str, Any]:
    kind = spec.get("type") if spec.get("type") in _INPUT_TYPES else "string"
    schema: dict[str, Any] = {"type": kind}
    if spec.get("description") or spec.get("label"):
        schema["description"] = str(spec.get("description") or spec.get("label"))
    if spec.get("choices"):
        schema["enum"] = list(spec["choices"])
    for source, target in (
        ("min", "minimum"),
        ("max", "maximum"),
        ("min_length", "minLength"),
        ("max_length", "maxLength"),
        ("pattern", "pattern"),
    ):
        if spec.get(source) is not None:
            schema[target] = spec[source]
    if spec.get("secret"):
        schema["format"] = "password"
    declaration: dict[str, Any] = {"schema": schema}
    if spec.get("default") is not None:
        declaration["default"] = spec["default"]
    elif not spec.get("required"):
        if kind == "string":
            declaration["default"] = ""
        elif kind == "boolean":
            declaration["default"] = False
    return declaration


def _trigger_inputs(trigger: Mapping[str, Any]) -> str:
    values = {**(trigger.get("params") or {}), **(trigger.get("inputs") or {})}
    return "".join(
        f" --input {_quote_arg(f'{_identifier(str(key), set())}={value}')}"
        for key, value in values.items()
    )


def _selectors(path: str) -> str | None:
    if not path:
        return ""
    selector = ""
    for part in path.split("."):
        if part.isdigit():
            selector += f"[{part}]"
        elif _JSON_MEMBER.fullmatch(part):
            selector += f".{part}"
        elif part:
            selector += f"[{json.dumps(part)}]"
        else:
            return None
    return selector


def _comparable(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return json.dumps(value, ensure_ascii=False)
    return None


def _escape_braces(text: str) -> str:
    return text.replace("{{", "{{{{").replace("}}", "}}}}")


def _map_strings(value: Any, function: Any) -> Any:
    if isinstance(value, _Raw):
        return value
    if isinstance(value, str):
        return function(value)
    if isinstance(value, list):
        return [_map_strings(item, function) for item in value]
    if isinstance(value, dict):
        return {key: _map_strings(item, function) for key, item in value.items()}
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_value(value: Any) -> str:
    if isinstance(value, _Raw):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return json.dumps(value, ensure_ascii=False)


def _block_safe(text: str, *, lenient: bool) -> bool:
    if "\n" not in text.rstrip("\n") or "\r" in text:
        return False
    lines = text.rstrip("\n").split("\n")
    if any(line != line.rstrip() for line in lines):
        return False
    if not any(line and not line[0].isspace() for line in lines):
        return False
    return lenient or (text.endswith("\n") and not text.endswith("\n\n"))


def _field_lines(key: str, value: Any, *, lenient: bool) -> list[str]:
    if (
        isinstance(value, str)
        and not isinstance(value, _Raw)
        and _block_safe(value, lenient=lenient)
    ):
        body = value.rstrip("\n").split("\n")
        return [f"  {key}: |", *(f"    {line}" if line else "" for line in body)]
    return [f"  {key}: {_render_value(value)}"]


def _formatted(source: str) -> str:
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.formatter import format_rattish

    try:
        return format_rattish(source)
    except (RattishError, ValueError, RuntimeError):
        return source
