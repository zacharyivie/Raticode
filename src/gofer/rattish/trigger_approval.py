"""The workflow version a trigger may run, and what changed since it was approved.

A trigger starts a workflow with nobody watching, so it runs only the version the user
approved. Approving saves a snapshot of the workflow's text and of each file it runs as
code: agent prompt files, ``prompt-file`` templates, Python and shell scripts, and child
workflows together with their own such files. Each ``mcp-tool`` step's server definition
from Rem's MCP server list (its command and arguments, or its URL) is covered too, under
the key ``mcp-server:<name>``, and so is each regular file inside the project that its
command or arguments name, as a script (``tools/server.py`` in ``python3
tools/server.py``). Modules a Python script imports and files a shell script sources
aren't covered. A server started by a package runner (``npx``, ``uvx`` and
the like) without an exact version can run a newer release under the same definition, so
Review changes warns about it and approving it asks a second time (``mcp_packages``).
Before each triggered start the scheduler
takes a fresh snapshot and compares fingerprints, and during the run each file is
checked again right before it is used (``approved_files``).

A snapshot keeps each file's SHA-256 and, for text files up to a size limit, the text
itself, so the change can be shown as a diff before the user approves it again. Paths
that come from run-time values (a ``with`` binding or a ``{{...}}`` template) can't be
known before the run and aren't covered.

This module only reads files. ``gofer.rattish.triggers`` stores approvals and decides who
may make them.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.rattish.mcp_packages import UNPINNED_WARNING, unpinned_packages
from gofer.rattish.project_paths import project_path

APPROVAL_SKIP_MESSAGE = "Workflow changed since you approved it."
UNAPPROVED_SKIP_MESSAGE = "Waiting for you to approve this workflow version."
# Text kept per file so the change can be shown later. Larger files keep only a hash.
MAX_FILE_TEXT_CHARS = 200_000
# Text kept per snapshot, across all its files.
MAX_SNAPSHOT_TEXT_CHARS = 1_000_000
MAX_DIFF_LINES = 2000
MAX_CHILD_DEPTH = 8
MAX_FILES = 200
SNAPSHOT_VERSION = 1

# Configuration fields that name a file the node runs as code, and what to call it.
_CODE_FIELDS = {
    "prompt_path": "prompt",
    "template_path": "prompt",
    "script_path": "script",
}
ROLE_LABELS = {
    "workflow": "Workflow",
    "child_workflow": "Child workflow",
    "prompt": "Prompt file",
    "script": "Script",
    "mcp_server": "MCP server",
}


@dataclass(frozen=True, slots=True)
class CodeFile:
    """One file a workflow runs as code, as it was when the snapshot was taken."""

    path: str
    role: str
    sha256: str | None
    text: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "role": self.role, "sha256": self.sha256, "text": self.text}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> CodeFile:
        text = value.get("text")
        sha = value.get("sha256")
        return cls(
            path=str(value.get("path") or ""),
            role=str(value.get("role") or "workflow"),
            sha256=str(sha) if isinstance(sha, str) else None,
            text=text if isinstance(text, str) else None,
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A workflow and the files it runs as code. ``complete`` is false when it didn't compile.

    A workflow that doesn't compile can't say which files it uses, so its snapshot reads the
    files the last approved snapshot named instead.
    """

    files: tuple[CodeFile, ...]
    complete: bool = True
    workflow_name: str = ""
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        listing = sorted((item.path, item.role, item.sha256 or "") for item in self.files)
        digest = hashlib.sha256(json.dumps(listing, separators=(",", ":")).encode("utf-8"))
        object.__setattr__(self, "fingerprint", f"sha256:{digest.hexdigest()}")

    def to_json(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "fingerprint": self.fingerprint,
            "complete": self.complete,
            "workflowName": self.workflow_name,
            "files": [item.to_json() for item in self.files],
        }

    @classmethod
    def from_json(cls, value: Any) -> Snapshot | None:
        if not isinstance(value, Mapping) or not isinstance(value.get("files"), list):
            return None
        files = tuple(
            CodeFile.from_json(item) for item in value["files"] if isinstance(item, Mapping)
        )
        return cls(
            files=files,
            complete=value.get("complete") is not False,
            workflow_name=str(value.get("workflowName") or ""),
        )

    def entry_missing(self) -> bool:
        return (
            bool(self.files) and self.files[0].role == "workflow" and self.files[0].sha256 is None
        )


def _read(path: Path) -> tuple[str | None, str | None]:
    """A file's SHA-256 and its text, or None for each that can't be had."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return digest, None
    return digest, text if len(text) <= MAX_FILE_TEXT_CHARS else None


def _is_dynamic(node: Mapping[str, Any], field_name: str) -> bool:
    """Whether a field's value is only known at run time."""
    binding = field_name.replace("_", "-")
    if any(item.get("name") == binding for item in node.get("bindings") or []):
        return True
    value = (node.get("configuration") or {}).get(field_name)
    return isinstance(value, str) and ("{{" in value or "}}" in value)


class _Collector:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.files: dict[str, CodeFile] = {}
        self.text_budget = MAX_SNAPSHOT_TEXT_CHARS

    def add(self, path: Path, role: str) -> bool:
        key = str(path)
        if key in self.files or len(self.files) >= MAX_FILES:
            return False
        digest, text = _read(path)
        if text is not None:
            if len(text) > self.text_budget:
                text = None
            else:
                self.text_budget -= len(text)
        self.files[key] = CodeFile(key, role, digest, text)
        return True

    def walk(self, ir: Mapping[str, Any], depth: int) -> None:
        root = Path(str(ir["source"]["project_root"]))
        for node in ir.get("nodes") or []:
            configuration = node.get("configuration") or {}
            for name, role in _CODE_FIELDS.items():
                value = configuration.get(name)
                if isinstance(value, str) and value and not _is_dynamic(node, name):
                    self.add(project_path(root, value), role)
            if node.get("type") == "workflow" and depth < MAX_CHILD_DEPTH:
                self.child(node, root, depth)
            if node.get("runtime_handler") == "raticode.mcp_tool":
                working = project_path(root, str(configuration.get("working_dir") or "."))
                self.mcp_server(str(configuration.get("server") or ""), working, root)

    def mcp_server(
        self, name: str, working_dir: Path | None = None, root: Path | None = None
    ) -> None:
        """Cover an MCP server's definition from Rem's list, so a change asks again.

        With the step's ``working_dir`` and project ``root``, each project file its
        command or arguments name is covered as a script (``mcp_tool.project_files``).
        """
        from gofer.rattish import mcp_tool

        key = mcp_tool.approval_key(name)
        if not name or len(self.files) >= MAX_FILES:
            return
        try:
            server: dict[str, Any] | None = mcp_tool.library_server(self.data_dir, name)
        except mcp_tool.McpToolError:
            server = None
        if key not in self.files:
            text = mcp_tool.definition_text(server) if server is not None else None
            if text is not None:
                self.text_budget -= len(text)
            self.files[key] = CodeFile(
                key, mcp_tool.APPROVAL_ROLE, mcp_tool.definition_digest(server), text
            )
        if server is not None and root is not None:
            # Two steps can start one server from different folders, so each step's
            # files count.
            for path in mcp_tool.project_files(server, working_dir, root):
                self.add(path, "script")

    def child(self, node: Mapping[str, Any], root: Path, depth: int) -> None:
        from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
        from gofer.rattish.diagnostics import RattishError
        from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

        resolution = (node.get("resolutions") or {}).get("workflow")
        if not isinstance(resolution, Mapping):
            return
        try:
            if resolution.get("source_kind") == "registry":
                registered = find_registered_workflow(
                    str(resolution["source"]), registry_dir=self.data_dir
                )
                child_path = registered.entrypoint.resolve()
                child_root, child_id = registered.project_root, registered.workflow_id
            else:
                child_path = project_path(root, str(resolution["source"])).resolve()
                child_root, child_id = root, str(resolution.get("workflow_id") or "")
        except (KeyError, OSError, RattishWorkspaceError):
            return
        if not self.add(child_path, "child_workflow"):
            return  # Already read, so a workflow that calls itself stops here.
        try:
            child = compile_rattish_file(
                child_path,
                data_dir=self.data_dir,
                workflow_id=child_id or None,
                project_root=child_root,
            )
        except (OSError, ValueError, RattishArtifactError, RattishError):
            return
        self.walk(child.ir, depth + 1)


def take_snapshot(
    source_path: str | Path, data_dir: Path, *, known: Iterable[CodeFile] = ()
) -> Snapshot:
    """The workflow at ``source_path`` and the files it runs as code, read now.

    ``known`` is the last approved snapshot's files. When the workflow doesn't compile,
    those paths are read again, so a change to any of them still shows.
    """
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError

    source = Path(source_path).expanduser().resolve()
    collector = _Collector(Path(data_dir))
    collector.add(source, "workflow")
    name = ""
    complete = True
    try:
        compiled = compile_rattish_file(source, data_dir=Path(data_dir))
    except (OSError, ValueError, RattishArtifactError, RattishError):
        complete = False
        for item in known:
            if item.role == "mcp_server":
                collector.mcp_server(item.path.removeprefix("mcp-server:"))
            else:
                collector.add(Path(item.path), item.role)
    else:
        name = str(compiled.ir["workflow"].get("name") or "")
        collector.walk(compiled.ir, 0)
    return Snapshot(tuple(collector.files.values()), complete=complete, workflow_name=name)


def shown_path(path: str, project_root: Path | None) -> str:
    """``path`` relative to the project when it is inside it."""
    if project_root is not None:
        try:
            return Path(path).relative_to(project_root).as_posix()
        except ValueError:
            pass
    return path


def describe_changes(
    approved: Snapshot | None, current: Snapshot, *, project_root: Path | None = None
) -> dict[str, Any]:
    """Each file that differs between the approved and current snapshots, with a diff.

    ``change`` is ``changed``, ``added`` (now used, wasn't before), ``removed`` (no longer
    used) or ``missing`` (still named but not on disk). The diffs together are cut at
    ``MAX_DIFF_LINES`` lines.
    """
    before = {item.path: item for item in (approved.files if approved else ())}
    after = {item.path: item for item in current.files}
    order = [*after, *(path for path in before if path not in after)]
    files: list[dict[str, Any]] = []
    budget = MAX_DIFF_LINES
    truncated = False
    for path in order:
        old, new = before.get(path), after.get(path)
        if old is not None and new is not None and old.sha256 == new.sha256:
            continue
        if new is None:
            change = "removed"
        elif new.sha256 is None:
            change = "missing"
        elif old is None:
            change = "added"
        else:
            change = "changed"
        shown = shown_path(path, project_root)
        known = new if new is not None else old
        role = known.role if known is not None else "workflow"
        old_text = "" if old is None or old.sha256 is None else old.text
        new_text = "" if new is None or new.sha256 is None else new.text
        entry: dict[str, Any] = {
            "path": shown,
            # An MCP server's definition has no file to open.
            "absolutePath": "" if role == "mcp_server" else path,
            "role": role,
            "roleLabel": ROLE_LABELS.get(role, role),
            "change": change,
            "diff": "",
            "diffAvailable": True,
            "truncated": False,
        }
        unpinned = (
            _unpinned_in(new) if role == "mcp_server" and change in {"added", "changed"} else []
        )
        if unpinned:
            entry["unpinned"] = unpinned
            entry["warning"] = UNPINNED_WARNING
        if old_text is None or new_text is None:
            # Binary, too large to keep, or saved before its text was kept.
            entry["diffAvailable"] = False
        else:
            lines = list(
                difflib.unified_diff(
                    old_text.splitlines(),
                    new_text.splitlines(),
                    fromfile=f"approved/{shown}",
                    tofile=f"now/{shown}",
                    lineterm="",
                )
            )
            if len(lines) > budget:
                lines = lines[:budget]
                entry["truncated"] = truncated = True
            budget -= len(lines)
            entry["diff"] = "\n".join(lines)
        files.append(entry)
    return {
        "approvedFingerprint": approved.fingerprint if approved else None,
        "fingerprint": current.fingerprint,
        "files": files,
        "truncated": truncated,
        "unseen": unseen_files(files),
    }


def _unpinned_in(item: CodeFile | None) -> list[str]:
    """The packages an MCP server's definition fetches with no exact version."""
    from gofer.rattish import mcp_tool

    if item is None:
        return []
    return unpinned_packages(mcp_tool.definition_from_text(item.text))


# Files whose contents nobody needs to read before approving: the workflow no longer runs
# a removed file, and a missing one has nothing in it.
_NOTHING_TO_READ = {"removed", "missing"}


def unseen_reason(item: Mapping[str, Any]) -> str | None:
    """Why Review changes didn't show all of a file's change, or None when it did.

    ``cut`` means its diff was cut short, ``unreadable`` that it is binary or too large
    to show at all, and ``unpinned`` that an MCP server runs a package with no exact
    version, so the code it runs can change without its definition changing.
    """
    if item.get("change") in _NOTHING_TO_READ:
        return None
    if item.get("unpinned"):
        return "unpinned"
    if not item.get("diffAvailable", True):
        return "unreadable"
    if item.get("truncated"):
        return "cut"
    return None


def unseen_files(files: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The files whose change wasn't shown in full, for a second confirmation."""
    result = []
    for item in files:
        reason = unseen_reason(item)
        if reason is not None:
            shown = {
                "path": item.get("path"),
                "absolutePath": item.get("absolutePath"),
                "roleLabel": item.get("roleLabel"),
                "reason": reason,
            }
            if reason == "unpinned":
                shown["packages"] = list(item.get("unpinned") or [])
            result.append(shown)
    return result


UNSEEN_REASONS = {
    "cut": "diff cut short",
    "unreadable": "binary or too large to show",
    "unpinned": "package version not pinned: each run fetches the newest release",
}


def unseen_list_text(unseen: Iterable[Mapping[str, Any]]) -> str:
    """'prompts/a.md (diff cut short), data.bin (binary or too large to show)'."""
    return ", ".join(
        f"{item.get('path')} ({UNSEEN_REASONS.get(str(item.get('reason')), 'not shown')})"
        for item in unseen
    )


def unseen_message(unseen: Iterable[Mapping[str, Any]]) -> str:
    """Why approving needs a second confirmation, naming each file and reason."""
    items = list(unseen)
    unpinned = [item for item in items if item.get("reason") == "unpinned"]
    parts = []
    if len(unpinned) < len(items):
        parts.append(
            "Some changes weren't shown in full: "
            f"{unseen_list_text(item for item in items if item.get('reason') != 'unpinned')}. "
            "Open those files to read them"
        )
    if unpinned:
        parts.append(
            f"{unseen_list_text(unpinned)}, so a run can use a release you haven't seen. "
            "Pin an exact version in Rem's MCP server list (pkg@1.2.3 or pkg==1.2.3)"
        )
    return "; ".join(parts) + ", then confirm that you approve changes you haven't seen."


def _has_unpinned(unseen: Iterable[Mapping[str, Any]]) -> tuple[bool, bool]:
    """Whether ``unseen`` holds files not shown in full, and MCP servers with unpinned packages."""
    reasons = [item.get("reason") for item in unseen]
    return any(reason != "unpinned" for reason in reasons), "unpinned" in reasons


def unseen_lines(unseen: Iterable[Mapping[str, Any]]) -> list[str]:
    """One indented line per file for a terminal: its path and why it asks a second time."""
    lines = []
    for item in unseen:
        reason = UNSEEN_REASONS.get(str(item.get("reason")), "not shown")
        packages = item.get("packages") or []
        if packages:
            reason = f"{reason}: {', '.join(str(package) for package in packages)}"
        lines.append(f"  {item.get('absolutePath') or item.get('path')} ({reason})")
    return lines


UNPINNED_ADVICE = (
    "Pin an exact version in Rem's MCP server list (pkg@1.2.3 or pkg==1.2.3) so each run "
    "uses the release you approved. Pinning fixes the top-level package only."
)


def unseen_note(unseen: Sequence[Mapping[str, Any]]) -> str:
    """The line under a terminal's changes when approving them asks a second time."""
    not_shown, unpinned = _has_unpinned(unseen)
    parts = []
    if not_shown:
        parts.append("Some changes aren't shown in full. Open those files to read them.")
    if unpinned:
        parts.append(
            "An MCP server runs a package with no exact version, so each run fetches the "
            f"newest release. {UNPINNED_ADVICE}"
        )
    parts.append("Approving them needs --unseen or a second yes.")
    if not_shown and not unpinned:
        # The wording the CLI has always used.
        return (
            "Some changes aren't shown in full. Open those files to read them; approving "
            "them needs --unseen or a second yes."
        )
    return " ".join(parts)


def unseen_heading(unseen: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """The line above a terminal's list of such files, and the advice under it."""
    not_shown, unpinned = _has_unpinned(unseen)
    if not unpinned:
        return (
            "These files' changes weren't shown in full:",
            "Open them to read them before you approve.",
        )
    if not not_shown:
        return ("These MCP servers can run a release you haven't seen:", UNPINNED_ADVICE)
    return (
        "These changes weren't shown in full or can change between runs:",
        f"Open the files to read them before you approve. {UNPINNED_ADVICE}",
    )


def unseen_refusal(unseen: Sequence[Mapping[str, Any]]) -> str:
    """A terminal's refusal when approving needed --unseen and didn't get it."""
    heading, advice = unseen_heading(unseen)
    not_shown, unpinned = _has_unpinned(unseen)
    if not unpinned:
        advice = "Open them to read them, then add --unseen to approve changes you haven't seen."
    else:
        advice = f"{advice} Or add --unseen to approve {'them' if not_shown else 'it'} as it is."
    return "\n".join([f"Not approved. {heading}", *unseen_lines(unseen), advice])


def approved_after_studio_save(
    approved: Snapshot, current: Snapshot, source_path: str | Path
) -> Snapshot:
    """What saving the workflow in Studio approves: only the workflow file it wrote.

    The user wrote and saw the workflow file, so its new text is approved. Every other file
    keeps the version approved before, or stays unapproved when the workflow now names a
    file it didn't, so a prompt, script or child workflow that changed meanwhile still
    shows in Review changes. A file the workflow no longer names drops out, as the saved
    workflow text shows.
    """
    entry = str(Path(source_path).expanduser().resolve())
    before = {item.path: item for item in approved.files}
    files: list[CodeFile] = []
    for item in current.files:
        if item.path == entry:
            files.append(item)
        elif item.path in before:
            files.append(before[item.path])
    return Snapshot(tuple(files), complete=current.complete, workflow_name=current.workflow_name)


def unified_diff_text(changes: Mapping[str, Any]) -> str:
    """The changes from ``describe_changes`` as one text, for a terminal or the phone."""
    parts: list[str] = []
    notes = {
        "removed": "No longer used by the workflow.",
        "missing": "Named by the workflow but missing on disk.",
    }
    for item in changes.get("files") or []:
        header = f"{item['roleLabel']}: {item['path']} ({item['change']})"
        parts.append(header)
        if item["change"] in notes and not item["diff"]:
            parts.append(notes[item["change"]])
        elif not item["diffAvailable"]:
            parts.append("Binary or too large to show. Its contents changed.")
        elif item["diff"]:
            parts.append(item["diff"])
        if item["truncated"]:
            parts.append("… diff cut short. Open the file to read the rest.")
        if item.get("unpinned"):
            packages = ", ".join(item["unpinned"])
            parts.append(f"Warning: {UNPINNED_WARNING} ({packages}).")
        parts.append("")
    return "\n".join(parts).rstrip("\n")
