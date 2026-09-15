from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.rattish.editor import (
    RattishEditorError,
    RattishEditorService,
    RattishRevisionConflict,
)
from gofer.rattish.workspaces import create_registered_workflow, find_registered_workflow


def registered_editor(tmp_path: Path) -> tuple[RattishEditorService, Path, Path]:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Editor Test", registry_dir=data_dir)
    return RattishEditorService(), data_dir, workflow.entrypoint


def valid_source(name: str = "Editor Test") -> str:
    return f"""Rattish: 1
Workflow:
  name: {json.dumps(name)}
Node prepare:
  type: bash-command
  command: echo ready
  to: finish
Node finish:
  type: bash-command
  command: echo done
  needs: prepare
"""


def test_open_document_returns_revisions_metadata_and_empty_preflight(
    tmp_path: Path,
) -> None:
    service, data_dir, _ = registered_editor(tmp_path)

    document = service.open_document("editor-test", data_dir=data_dir)

    assert document["dirty"] is False
    assert document["sourceRevision"] == document["savedRevision"]
    assert document["metadata"]["metadataVersion"] == 1
    assert document["compilation"]["state"] == "valid"
    assert document["preflight"]["ready"] is False
    assert document["runnable"] is False


def test_recovering_analysis_keeps_valid_and_invalid_nodes(tmp_path: Path) -> None:
    service, data_dir, _ = registered_editor(tmp_path)
    source = """Rattish: 1
Workflow:
  name: Recovery
Node broken:
  type: bash-command
  command
Node valid:
  type: bash-command
  command: echo valid
"""

    document = service.analyze_document("editor-test", source, data_dir=data_dir)

    assert document["dirty"] is True
    assert document["compilation"]["state"] == "invalid"
    assert document["runnable"] is False
    assert {node["id"]: node["status"] for node in document["graph"]["nodes"]} == {
        "valid": "valid",
        "broken": "invalid",
    }
    valid = next(node for node in document["graph"]["nodes"] if node["id"] == "valid")
    assert valid["contractAvailable"] is True
    assert document["invalidRegions"][0]["source"].startswith("Node broken:")


def test_semantic_errors_return_graph_without_partial_ir(tmp_path: Path) -> None:
    service, data_dir, _ = registered_editor(tmp_path)
    source = """Rattish: 1
Workflow:
  name: Unresolved route
Node start:
  type: bash-command
  command: echo start
  to: missing
"""

    document = service.analyze_document("editor-test", source, data_dir=data_dir)

    assert document["compilation"]["state"] == "invalid"
    assert document["graph"]["edges"][0]["status"] == "unresolved"
    assert "RATTISH_UNRESOLVED_NODE" in {
        diagnostic["code"] for diagnostic in document["diagnostics"]
    }
    assert document["graph"]["nodes"][0]["configuration"] is None


def test_analysis_retains_last_valid_fingerprint(tmp_path: Path) -> None:
    service, data_dir, _ = registered_editor(tmp_path)
    valid = service.analyze_document("editor-test", valid_source(), data_dir=data_dir)

    invalid = service.analyze_document(
        "editor-test",
        valid_source().replace("command: echo done", "command"),
        data_dir=data_dir,
    )

    assert valid["compilation"]["fingerprint"]
    assert invalid["compilation"]["fingerprint"] is None
    assert invalid["compilation"]["lastValidFingerprint"] == valid["compilation"]["fingerprint"]


def test_save_allows_invalid_source_and_rejects_stale_revision(tmp_path: Path) -> None:
    service, data_dir, entrypoint = registered_editor(tmp_path)
    opened = service.open_document("editor-test", data_dir=data_dir)
    invalid_source = opened["source"] + "Node broken:\n  type\n"

    saved = service.save_document(
        "editor-test",
        invalid_source,
        opened["sourceRevision"],
        data_dir=data_dir,
    )

    assert entrypoint.read_text(encoding="utf-8") == invalid_source
    assert saved["compilation"]["state"] == "invalid"
    assert saved["dirty"] is False
    with pytest.raises(RattishRevisionConflict) as caught:
        service.save_document(
            "editor-test",
            valid_source(),
            opened["sourceRevision"],
            data_dir=data_dir,
        )
    assert caught.value.resource == "source"


def test_valid_save_updates_registry_name_without_changing_id(tmp_path: Path) -> None:
    service, data_dir, _ = registered_editor(tmp_path)
    opened = service.open_document("editor-test", data_dir=data_dir)

    saved = service.save_document(
        "editor-test",
        valid_source("Renamed Display"),
        opened["sourceRevision"],
        data_dir=data_dir,
    )
    registered = find_registered_workflow("editor-test", registry_dir=data_dir)

    assert saved["workflow"]["name"] == "Renamed Display"
    assert registered.workflow_id == "editor-test"
    assert registered.name == "Renamed Display"


def test_metadata_save_validates_schema_and_revision(tmp_path: Path) -> None:
    service, data_dir, _ = registered_editor(tmp_path)
    opened = service.open_document("editor-test", data_dir=data_dir)
    metadata = opened["metadata"]
    metadata["canvas"]["nodes"]["prepare"] = {"x": 40, "y": 80}

    saved = service.save_metadata(
        "editor-test",
        metadata,
        opened["metadataRevision"],
        data_dir=data_dir,
    )

    assert saved["metadata"]["canvas"]["nodes"]["prepare"] == {"x": 40, "y": 80}
    with pytest.raises(RattishRevisionConflict):
        service.save_metadata(
            "editor-test",
            metadata,
            opened["metadataRevision"],
            data_dir=data_dir,
        )
    invalid = json.loads(json.dumps(saved["metadata"]))
    invalid["canvas"]["zoom"] = 0
    with pytest.raises(RattishEditorError, match="invalid workflow metadata"):
        service.save_metadata(
            "editor-test",
            invalid,
            saved["metadataRevision"],
            data_dir=data_dir,
        )
