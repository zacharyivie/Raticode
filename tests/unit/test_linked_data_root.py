"""Raticode works when a folder above its data folder is a symbolic link.

Fedora Kinoite and Silverblue link ``/home`` to ``var/home``. Writers open every
folder without following links, so the data folder and folders the user picks are
resolved once where they enter; a link below them is still refused.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.rattish.bundles import export_rattish_bundle
from gofer.rattish.result_cache import ResultCache, cache_root
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.brain_bibliography import bibliography_operation
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.server import GoferUiServer, HeadlessSession
from gofer.utils.paths import get_data_dir
from tests.helpers.brain_library import wait_task

ONE_NODE = """Rattish: 1

Workflow:
  name: Linked data folder

Node execute:
  type: bash-command
  command: "printf ready"
"""


def test_linked_tmp_path_has_a_link_above_it(linked_tmp_path: Path) -> None:
    assert linked_tmp_path.resolve() != linked_tmp_path


@pytest.mark.parametrize("variable", ["GOFER_DATA_DIR", "XDG_DATA_HOME", "HOME"])
def test_data_folder_is_resolved_when_a_folder_above_it_is_a_link(
    linked_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    if variable == "HOME":
        monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv(variable, str(linked_tmp_path))

    data_dir = get_data_dir()

    assert data_dir == data_dir.resolve()
    assert data_dir.is_relative_to(linked_tmp_path.resolve())


@pytest.mark.anyio
async def test_run_cache_approval_and_export_work_under_a_linked_data_folder(
    linked_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOFER_DATA_DIR", str(linked_tmp_path / "data"))
    data_dir = get_data_dir()
    project = linked_tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Linked data folder", registry_dir=data_dir)
    workflow.entrypoint.write_text(ONE_NODE, encoding="utf-8")

    result = await run_rattish_file(workflow.entrypoint, data_dir=data_dir)

    assert result.document["status"] == "passed", result.document
    assert result.path.is_file()

    cache = ResultCache(data_dir)
    assert cache.store(workflow.workflow_id, "a" * 64, "execute", {"stdout": "ready"}, 60)
    cached = cache.lookup(workflow.workflow_id, "a" * 64, 60)
    assert cached is not None and cached.output == {"stdout": "ready"}

    request = ApprovalRequest(workflow.workflow_id, result.document["run_id"], "gate", "Go on?")
    assert ApprovalStore(data_dir).create_or_update(request).is_file()

    exported = linked_tmp_path / "exports" / "linked.raticode"
    export_rattish_bundle(workflow.workflow_id, exported, registry_dir=data_dir)
    assert exported.is_file()


def test_link_below_the_resolved_data_folder_is_still_refused(
    linked_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOFER_DATA_DIR", str(linked_tmp_path / "data"))
    data_dir = get_data_dir()
    outside = linked_tmp_path / "outside"
    outside.mkdir()
    data_dir.mkdir()
    cache_root(data_dir).symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        ResultCache(data_dir).store("flow", "a" * 64, "execute", "answer", 60)

    assert list(outside.iterdir()) == []


def test_backend_entry_points_resolve_a_data_folder_passed_to_them(
    linked_tmp_path: Path,
) -> None:
    data_dir = linked_tmp_path / "data"
    server = GoferUiServer(("127.0.0.1", 0), data_dir)
    try:
        assert server.data_dir == data_dir.resolve() != data_dir
    finally:
        server.server_close()
    session = HeadlessSession.prepare("127.0.0.1", linked_tmp_path / "headless", None)
    try:
        assert session.data_dir == (linked_tmp_path / "headless").resolve()
    finally:
        session.close()


def test_brain_backup_and_export_write_to_a_linked_folder(linked_tmp_path: Path) -> None:
    library = BrainLibrary(linked_tmp_path / "app", start=False)
    try:
        root = linked_tmp_path.resolve() / "brain"
        root.mkdir()
        (root / "notes.md").write_text("# Notes\n\nKept through a link.\n")
        brain = library.open({"root": str(root)})
        for task in library.snapshot()["tasks"]:
            wait_task(library, task)
        folder = linked_tmp_path / "backups"
        library.update_brain({"id": brain["id"], "patch": {"backup": {"folder": str(folder)}}})

        receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))

        assert receipt["state"] == "done", receipt.get("error")
        assert Path(receipt["result"]["path"]).is_file()

        package = linked_tmp_path / "exports" / "brain.zip"
        os.makedirs(package.parent)
        receipt = wait_task(
            library,
            library.start_task("export", {"brainId": brain["id"], "destination": str(package)}),
        )
        assert receipt["state"] == "done", receipt.get("error")
        assert package.is_file()

        references = linked_tmp_path / "exports" / "references.json"
        bibliography_operation(
            library, {"brainId": brain["id"], "export": True, "path": str(references)}
        )
        assert references.is_file()
    finally:
        library.close()
