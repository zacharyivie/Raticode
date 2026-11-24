"""Run a workflow with another model on some nodes, without touching its source.

``Raticode rattish test --model NODE=PROVIDER:MODEL`` and the Tests panel's Try another
model use this. The override is applied to a copy of the source held in memory, with
the same ``set_field`` edits the graph editor makes, and that copy is compiled at the
workflow's own path. The source file, its triggers and their approvals never see it.
The compiler checks the copy as strictly as a saved edit, so a model the provider
can't take, or a node that sets an effort the new provider lacks, is refused before
anything runs.

``use_model`` is the other half: once a candidate wins, it writes ``provider:`` and
``model:`` into the node through the editor service (``RattishEditorService``), so the
graph stays valid and every trigger asks for approval again, as for any other edit.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.rattish.artifacts import CompiledArtifact, compile_rattish_source
from gofer.rattish.diagnostics import RattishError

MAX_OVERRIDES = 20
_NODE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")
_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,63}")
_MODEL = re.compile(r"[^\s\"'\\]{1,200}")


class ModelOverrideError(ValueError):
    """An override that can't be used, with a sentence for the user."""


@dataclass(frozen=True)
class ModelOverride:
    """Run ``node`` with ``provider`` and ``model`` instead of what its source says."""

    node: str
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.node}={self.provider}:{self.model}"

    def to_json(self) -> dict[str, str]:
        return {"node": self.node, "provider": self.provider, "model": self.model}


def parse_model_override(text: str) -> ModelOverride:
    """``NODE=PROVIDER:MODEL``; the model may contain colons, as in ``llama3:8b``."""
    node, separator, rest = str(text).strip().partition("=")
    provider, colon, model = rest.partition(":")
    if not separator or not colon:
        raise ModelOverrideError(
            f"{text!r} is not NODE=PROVIDER:MODEL, for example draft=codex:gpt-5.5."
        )
    return model_override(node.strip(), provider.strip(), model.strip())


def model_override(node: Any, provider: Any, model: Any) -> ModelOverride:
    """One checked override from its three parts, as the window and phone send them."""
    if not isinstance(node, str) or not _NODE.fullmatch(node):
        raise ModelOverrideError(f"{node!r} is not a node ID.")
    if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider.lower()):
        raise ModelOverrideError(f"{provider!r} is not a provider ID.")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ModelOverrideError(f"{model!r} is not a model name.")
    return ModelOverride(node, provider.lower(), model)


def model_overrides_from_json(value: Any) -> list[ModelOverride]:
    """Overrides from a request body: a list of ``{node, provider, model}`` objects."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ModelOverrideError("models must be a list of {node, provider, model} objects.")
    return [
        model_override(item.get("node"), item.get("provider"), item.get("model")) for item in value
    ]


def model_nodes(ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The nodes that call a model, with the provider and model each uses now."""
    nodes = []
    for node in ir.get("nodes") or []:
        resolution = (node.get("resolutions") or {}).get("provider")
        if resolution:
            nodes.append(
                {
                    "node": str(node["id"]),
                    "type": str(node.get("type") or ""),
                    "provider": str(resolution["provider_id"]),
                    "model": resolution.get("model"),
                }
            )
    return nodes


def check_overrides(
    ir: Mapping[str, Any], overrides: Sequence[ModelOverride]
) -> list[ModelOverride]:
    """The overrides with node IDs as the workflow spells them; refuses unusable ones."""
    if not overrides:
        raise ModelOverrideError("Name at least one node and the model to try on it.")
    if len(overrides) > MAX_OVERRIDES:
        raise ModelOverrideError(f"Try at most {MAX_OVERRIDES} node models at a time.")
    current = {item["node"].lower(): item for item in model_nodes(ir)}
    known = {str(node["id"]).lower() for node in ir.get("nodes") or []}
    checked: list[ModelOverride] = []
    seen: set[str] = set()
    for override in overrides:
        key = override.node.lower()
        if key not in known:
            raise ModelOverrideError(f"The workflow has no node {override.node!r}.")
        if key not in current:
            raise ModelOverrideError(f"Node {override.node!r} doesn't call a model.")
        if key in seen:
            raise ModelOverrideError(f"Node {override.node!r} is named more than once.")
        seen.add(key)
        now = current[key]
        if now["provider"] == override.provider and now["model"] == override.model:
            raise ModelOverrideError(
                f"Node {now['node']!r} already uses {override.provider}:{override.model}."
            )
        checked.append(ModelOverride(now["node"], override.provider, override.model))
    return sorted(checked, key=lambda item: item.node)


def variant_record(ir: Mapping[str, Any], overrides: Sequence[ModelOverride]) -> dict[str, Any]:
    """What a test result or run saves about the models it tried, with what they replaced."""
    current = {item["node"]: item for item in model_nodes(ir)}
    return {
        "key": variant_key(overrides),
        "models": [
            {
                **override.to_json(),
                "fromProvider": current.get(override.node, {}).get("provider"),
                "fromModel": current.get(override.node, {}).get("model"),
            }
            for override in overrides
        ],
    }


def variant_key(overrides: Iterable[ModelOverride] | Iterable[Mapping[str, Any]]) -> str:
    """One string per set of overrides, the same whatever order they were given in."""
    parts = []
    for item in overrides:
        if isinstance(item, ModelOverride):
            parts.append(item.label)
        else:
            parts.append(f"{item.get('node')}={item.get('provider')}:{item.get('model')}")
    return ";".join(sorted(parts))


def override_mutations(override: ModelOverride) -> list[dict[str, Any]]:
    """The graph editor's ``set_field`` edits that put this provider and model on the node.

    The provider is written bare, as authors write it; the model as a quoted string.
    """
    target = {"node": override.node}
    return [
        {
            "kind": "set_field",
            "target": target,
            "field": "provider",
            "valueSource": override.provider,
        },
        {"kind": "set_field", "target": target, "field": "model", "value": override.model},
    ]


def override_source(source: str, overrides: Sequence[ModelOverride], source_id: str) -> str:
    """The source with ``provider:`` and ``model:`` set on each node, as the editor sets them."""
    from gofer.rattish.editor import RattishEditorError, _apply_source_mutation
    from gofer.rattish.parser import parse_rattish_recovering

    updated = source
    for override in overrides:
        for mutation in override_mutations(override):
            ast = parse_rattish_recovering(updated, source_id=source_id).ast
            if ast is None:
                raise ModelOverrideError("The workflow source can't be read, so it can't be tried.")
            try:
                updated, _ = _apply_source_mutation(updated, ast, mutation)
            except RattishEditorError as exc:
                raise ModelOverrideError(str(exc)) from exc
    return updated


def compile_variant(
    compiled: CompiledArtifact,
    overrides: Sequence[ModelOverride],
    *,
    data_dir: Path,
) -> CompiledArtifact:
    """The workflow compiled with the overrides, from the source ``compiled`` was built from.

    Raises ``ModelOverrideError`` with the compiler's first error when the variant
    doesn't compile, for example a model name the provider contract refuses.
    """
    source_path = compiled.source_path
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ModelOverrideError(f"Could not read {source_path}: {exc}") from exc
    if source_fingerprint(source) != compiled.ir["source"]["source_fingerprint"]:
        raise ModelOverrideError(
            "The workflow source changed while the other model was being prepared. Try again."
        )
    checked = check_overrides(compiled.ir, overrides)
    text = override_source(source, checked, source_path.name)
    project_root = Path(str(compiled.ir["source"]["project_root"]))
    try:
        result = compile_rattish_source(
            text,
            source_path,
            data_dir=data_dir,
            workflow_id=str(compiled.ir["workflow"]["id"]),
            project_root=project_root,
        )
    except RattishError as exc:
        raise ModelOverrideError(_variant_problem(exc.diagnostics)) from exc
    errors = [item for item in result.diagnostics if item.severity == "error"]
    if errors or result.ir is None:
        raise ModelOverrideError(_variant_problem(errors))
    diagnostics = tuple(item.to_json() for item in result.diagnostics)
    return CompiledArtifact(result.ir, diagnostics, False, source_path)


def source_fingerprint(source: str) -> str:
    """The fingerprint the compiler gives this source text."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return f"sha256:{hashlib.sha256(normalized.encode()).hexdigest()}"


def _variant_problem(diagnostics: Sequence[Any]) -> str:
    message = next((str(item.message) for item in diagnostics), "it doesn't compile")
    return f"The workflow can't run with that model: {message}"


def use_model(
    source_path: Path,
    overrides: Sequence[ModelOverride],
    *,
    data_dir: Path,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Write ``provider:`` and ``model:`` into each node through the editor service.

    A registered workflow goes through ``RattishEditorService.mutate_document``, the
    path the graph editor uses, which refuses an edit that would break a compiling
    workflow and refuses a source that changed since ``expected_revision``. A loose
    file gets the same edits and the same compile check before it is replaced.
    Returns the new source revision and the edits made.
    """
    from gofer.rattish.artifacts import _registered_source, compile_rattish_file
    from gofer.rattish.editor import (
        DEFAULT_RATTISH_EDITOR_SERVICE,
        RattishEditorError,
        source_revision,
    )

    compiled = compile_rattish_file(source_path, data_dir=data_dir)
    if expected_revision and compiled.ir["source"]["source_fingerprint"] != expected_revision:
        raise ModelOverrideError(
            "The workflow changed since this result was tested. Test the model again."
        )
    checked = check_overrides(compiled.ir, overrides)
    # The same check a test run makes, so nothing is written that wouldn't run.
    compile_variant(compiled, checked, data_dir=data_dir)
    mutations = [mutation for item in checked for mutation in override_mutations(item)]
    registered = _registered_source(compiled.source_path, data_dir)
    try:
        if registered is not None:
            source = compiled.source_path.read_text(encoding="utf-8")
            result = DEFAULT_RATTISH_EDITOR_SERVICE.mutate_document(
                registered.workflow_id,
                mutations,
                source_revision(source),
                data_dir=data_dir,
            )
            revision = result.get("savedRevision")
        else:
            source = compiled.source_path.read_text(encoding="utf-8")
            text = override_source(source, checked, compiled.source_path.name)
            from gofer.utils.atomic_output import atomic_binary_output

            with atomic_binary_output(compiled.source_path) as output:
                output.write(text.encode("utf-8"))
            revision = source_revision(text)
    except RattishEditorError as exc:
        raise ModelOverrideError(str(exc)) from exc
    except OSError as exc:
        raise ModelOverrideError(f"Could not save {compiled.source_path}: {exc}") from exc
    updated = compile_rattish_file(compiled.source_path, data_dir=data_dir)
    return {
        "models": [item.to_json() for item in checked],
        "revision": updated.ir["source"]["source_fingerprint"],
        "editorRevision": revision,
    }
