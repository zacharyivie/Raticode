"""Starter workflows that ship with Raticode and copy into a project's .raticode folder."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.rattish.artifacts import RattishArtifactError, rattish_asset_root
from gofer.rattish.workspaces import (
    WORKFLOW_ENTRYPOINT,
    WORKSPACE_DIRECTORY,
    RattishWorkspaceError,
    RegisteredWorkflow,
    _slugify,
    _write_text_atomic,
    install_registered_workflow,
)

STARTER_MANIFEST = "starter.json"
STARTER_TESTS = "workflow.tests.json"
STARTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# The display name is the first `name:` field of the Workflow declaration.
# A step that asks for bash: ``shell: bash`` on a node.
_BASH_SHELL_LINE = re.compile(r"^[ \t]+shell:[ \t]*\"?bash\"?[ \t]*(?:#.*)?$", re.IGNORECASE | re.M)
_WORKFLOW_NAME_LINE = re.compile(
    r"^(workflow:\n(?:#[^\n]*\n)*  name: )[^\n]*$", re.IGNORECASE | re.M
)


@dataclass(frozen=True, slots=True)
class Starter:
    starter_id: str
    name: str
    summary: str
    order: int
    docs_path: str
    providers: tuple[str, ...]
    secrets: tuple[str, ...]
    trigger: dict[str, str] | None
    root: Path
    uses_bash: bool = False

    def needs_git_for_windows(self) -> bool:
        """On Windows, a starter with ``shell: bash`` steps needs Git for Windows' bash."""
        if not self.uses_bash:
            return False
        from gofer.rattish import shells

        return shells._on_windows() and shells.find_bash() is None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.starter_id,
            "name": self.name,
            "summary": self.summary,
            "docsUrl": f"https://raticode.dev{self.docs_path}",
            "providers": list(self.providers),
            "secrets": list(self.secrets),
            "trigger": dict(self.trigger) if self.trigger else None,
            "hasTests": (self.root / STARTER_TESTS).is_file(),
            "needsGitForWindows": self.needs_git_for_windows(),
        }


def starters_root() -> Path:
    try:
        return rattish_asset_root() / "starters"
    except RattishArtifactError as exc:
        raise RattishWorkspaceError(str(exc)) from exc


def list_starters() -> tuple[Starter, ...]:
    """Every installed starter, in the order the app shows them."""
    root = starters_root()
    if not root.is_dir():
        return ()
    starters = [
        _read_starter(folder)
        for folder in root.iterdir()
        if folder.is_dir() and not folder.is_symlink() and (folder / STARTER_MANIFEST).is_file()
    ]
    return tuple(sorted(starters, key=lambda item: (item.order, item.starter_id)))


def find_starter(starter_id: str) -> Starter:
    wanted = starter_id.strip().lower()
    starters = list_starters()
    for starter in starters:
        if starter.starter_id == wanted:
            return starter
    choices = ", ".join(item.starter_id for item in starters) or "none are installed"
    raise RattishWorkspaceError(f"Unknown starter {starter_id!r}. Choose one of: {choices}.")


def create_workflow_from_starter(
    project_root: Path,
    starter_id: str,
    name: str | None = None,
    *,
    registry_dir: Path | None = None,
) -> RegisteredWorkflow:
    """Copy a starter into PROJECT/.raticode/<id>/ and register it.

    Secret references stay as ``secret["NAME"]`` text: nothing is resolved while copying.
    """
    starter = find_starter(starter_id)
    workflow_name = (name or "").strip() or starter.name
    staging = Path(tempfile.mkdtemp(prefix="raticode-starter-"))
    try:
        staged = staging / starter.starter_id
        shutil.copytree(
            starter.root,
            staged,
            symlinks=True,
            ignore=shutil.ignore_patterns(STARTER_MANIFEST, "__pycache__"),
        )
        entrypoint = staged / WORKFLOW_ENTRYPOINT
        source = entrypoint.read_text(encoding="utf-8")
        renamed, count = _WORKFLOW_NAME_LINE.subn(
            lambda match: match.group(1) + json.dumps(workflow_name, ensure_ascii=False),
            source,
            count=1,
        )
        if count != 1:
            raise RattishWorkspaceError(f"Starter {starter.starter_id} has no workflow name line")
        entrypoint.write_text(renamed, encoding="utf-8")
        workflow = install_registered_workflow(
            project_root,
            staged,
            workflow_name,
            _slugify(name) if name and name.strip() else starter.starter_id,
            registry_dir=registry_dir,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _point_tests_at(workflow, starter.starter_id)
    return workflow


def _point_tests_at(workflow: RegisteredWorkflow, starter_id: str) -> None:
    """Test cases name their sample files by folder; follow the workflow's own ID."""
    if workflow.workflow_id == starter_id:
        return
    tests = workflow.workflow_root / STARTER_TESTS
    if not tests.is_file():
        return
    old = f"{WORKSPACE_DIRECTORY}/{starter_id}/"
    new = f"{WORKSPACE_DIRECTORY}/{workflow.workflow_id}/"
    text = tests.read_text(encoding="utf-8")
    if old in text:
        _write_text_atomic(tests, text.replace(old, new))


def _read_starter(folder: Path) -> Starter:
    path = folder / STARTER_MANIFEST
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RattishWorkspaceError(f"Could not read starter {path}: {exc}") from exc
    starter_id = str(document.get("id", ""))
    if document.get("starterVersion") != 1 or starter_id != folder.name:
        raise RattishWorkspaceError(
            f"Starter manifest {path} is not a version 1 manifest for {folder.name}"
        )
    if not STARTER_ID_PATTERN.match(starter_id) or not (folder / WORKFLOW_ENTRYPOINT).is_file():
        raise RattishWorkspaceError(f"Starter {folder.name} is incomplete")
    trigger = document.get("trigger")
    return Starter(
        starter_id=starter_id,
        name=str(document["name"]),
        summary=str(document["summary"]),
        order=int(document.get("order", 100)),
        docs_path=str(document["docsPath"]),
        providers=tuple(str(item) for item in document.get("providers", [])),
        secrets=tuple(str(item) for item in document.get("secrets", [])),
        trigger={str(key): str(value) for key, value in trigger.items()} if trigger else None,
        root=folder,
        uses_bash=_uses_bash(folder / WORKFLOW_ENTRYPOINT),
    )


def _uses_bash(entrypoint: Path) -> bool:
    try:
        return bool(_BASH_SHELL_LINE.search(entrypoint.read_text(encoding="utf-8")))
    except (OSError, UnicodeError):
        return False
