"""Narrow report output grants and hash-bound acceptance evidence."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_summaries import SUMMARY_RULE
from gofer.ui.report_outputs import (
    CITATION_RULES,
    RULES_TOOL,
    ReportTools,
    evidence_schema,
    save_report_description,
    summary_schema,
)
from gofer.ui.swarm_tools import TOOL_FAILURE_HINT, SwarmToolServer
from gofer.utils.atomic_output import mkdir_without_links, open_binary_input

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome

log = logging.getLogger(__name__)


def artifact_root(run: dict[str, Any]) -> Path:
    brain = run["config"].get("brain") or {}
    base = brain.get("root") if brain.get("enabled") else run["recipe"].get("project_dir")
    if not base:
        raise ValueError("A research report needs an authorized destination")
    return Path(base) / "reports" / "rem"


def validate_artifacts(run: dict[str, Any]) -> None:
    artifacts = [
        a
        for a in run.get("artifacts", [])
        if a.get("epoch") == run["epoch"] and not a.get("superseded")
    ]
    if not artifacts:
        raise ValueError(
            "No saved research artifact receipt. Save through rem_artifacts.save_report."
        )
    root = artifact_root(run).resolve(strict=True)
    for artifact in artifacts:
        path = Path(artifact["path"])
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("The report moved outside its approved destination")
        with open_binary_input(path) as handle:
            data = handle.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            raise ValueError("The saved report is empty or oversized")
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ValueError("The research artifact changed after saving")


REPORT_FIELDS = ("evidence", "title", "question")
PROJECT_FOLDER_NOTE = (
    "This report was saved in a project folder, not a Second Brain, so its {fields} "
    "{verb} ignored. Citations are only recorded for reports saved into a brain."
)
UNREGISTERED_NOTE = (
    "This report's folder isn't a brain in Second Brain's Library, so its {fields} "
    "{verb} not recorded."
)


def ignored_note(template: str, arguments: dict[str, Any]) -> str:
    fields = [name for name in REPORT_FIELDS if arguments.get(name)]
    if not fields:
        return ""
    names = fields[0] if len(fields) == 1 else ", ".join(fields[:-1]) + " and " + fields[-1]
    return template.format(fields=names, verb="was" if len(fields) == 1 else "were")


def readable_brains(library: Any, brain_config: dict[str, Any]) -> Callable[[str], None]:
    """A check that raises ValueError for a brain this task may not read.

    The task reads the brains chosen for Rem (brainIds). An older single-folder setup
    reads the registered brain at that folder. Like the task's second_brain tools, a
    brain whose cloud access was revoked or whose folder is gone is not readable.
    """
    selected = brain_config.get("brainIds")
    if isinstance(selected, list) and selected:
        allowed = {str(item) for item in selected}
    else:
        root = Path(str(brain_config.get("root") or "")).expanduser()
        allowed = {
            brain["id"]
            for brain in library.brains()
            if root.is_absolute() and Path(brain["root"]).resolve() == root.resolve()
        }

    def check(brain_id: str) -> None:
        if brain_id not in allowed:
            raise ValueError("This brain is not selected for the task")
        brain = library.brain(brain_id)
        if not brain["settings"].get("allowCloud") or not Path(brain["root"]).is_dir():
            raise ValueError("This brain is unavailable to the task")

    return check


def record_artifact(
    home: RemHome,
    run: dict[str, Any],
    path: Path,
    sha256: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Show a report saved into a Second Brain in its Reports and project pages.

    The save call's evidence is checked against the brains the task may read, and the
    citations, title and question go on the report record. Returns what to add to the
    tool reply: one mark per evidence entry, and a note when fields went unrecorded.
    """
    from gofer.ui.brain_library import BrainLibrary, evidence_reply

    brain_config = run["config"].get("brain") or {}
    in_brain = bool(brain_config.get("enabled"))
    reply: dict[str, Any] = {}
    if not in_brain:
        # A project folder: the fields are ignored. The record is still made when
        # that folder happens to sit inside a registered brain.
        note = ignored_note(PROJECT_FOLDER_NOTE, arguments or {})
        if note:
            reply["note"] = note
        arguments = {}
    arguments = arguments or {}
    shared: BrainLibrary | None = home.brain_library
    library = shared or BrainLibrary(home.data_dir, start=False, recover=False)
    try:
        citations, marks = (
            library.resolve_evidence(
                arguments.get("evidence"), readable_brains(library, brain_config)
            )
            if in_brain
            else ([], [])
        )
        record = library.record_artifact(
            path,
            {
                "savedVia": "rem_task",
                "source": "save_report",
                "runId": run["id"],
                "taskId": run.get("task_id", ""),
                "threadId": run.get("thread_id", ""),
                "taskTitle": run["recipe"].get("title", ""),
                "projectRoot": run["recipe"].get("project_dir", ""),
                "provider": run["config"].get("provider", ""),
                "model": run["config"].get("model", ""),
                "sha256": sha256,
            },
            title=str(arguments.get("title") or ""),
            question=str(arguments.get("question") or ""),
            citations=citations,
        )
        if record is None:
            note = ignored_note(UNREGISTERED_NOTE, arguments)
            if note:
                reply["note"] = note
        else:
            reply.update(evidence_reply(citations, marks))
    except Exception:  # noqa: BLE001 - the report itself is saved; its record can be backfilled
        log.exception("Could not register a saved report in the Second Brain")
        if arguments.get("evidence"):
            reply["note"] = (
                "The report was saved, but Raticode couldn't record its citations in the "
                "Second Brain."
            )
    finally:
        if shared is None:
            library.close()
    return reply


@contextmanager
def artifact_tools(
    home: RemHome, run: dict[str, Any], role: str
) -> Iterator[dict[str, Any] | None]:
    if role != "execute" or run["recipe"]["type"] != "research":
        yield None
        return
    home.authorize(run["config"], run["recipe"])
    root = artifact_root(run)
    mkdir_without_links(root)
    theme = run["config"].get("report_theme") or {"format": "html"}
    reports = ReportTools(root, theme.get("format", "html"), theme=theme)

    def call(name: str, arguments: dict[str, Any]) -> Any:
        if name == "rules":
            # The only full copy of the report rules this turn gets; the prompt and
            # save_report point here.
            return (
                reports.call("rules", {})
                + " "
                + CITATION_RULES
                + " Evidence, title and question are recorded when the report is saved "
                "into a Second Brain; in a project folder they are ignored and the reply "
                "says so."
            )
        if name != "save_report":
            raise ValueError("Unknown report tool.")
        with home.store.connect() as db:
            current = home.store.get(db, "runs", run["id"])
            if current["epoch"] != run["epoch"] or current["state"] == "cancelling":
                raise ValueError("This report grant was revoked")
            selected = home.store.setting(db, "config")
        refreshed = {**run["config"]}
        for field in ("projects", "brain"):
            refreshed[field] = selected.get(field, refreshed.get(field))
        home.authorize(refreshed, run["recipe"])
        if artifact_root({**run, "config": refreshed}) != root:
            raise ValueError("The report destination changed; update the task before saving")
        result = reports.call(
            name,
            {
                key: arguments[key]
                for key in ("path", "content", "format", "summary")
                if key in arguments
            },
        )
        try:
            with open_binary_input(Path(result["path"])) as handle:
                data = handle.read(2 * 1024 * 1024 + 1)
            receipt = {
                **result,
                "sha256": hashlib.sha256(data).hexdigest(),
                "epoch": run["epoch"],
            }
            with home.store.connect() as db:
                current = home.store.get(db, "runs", run["id"])
                receipt["superseded"] = current["epoch"] != run["epoch"]
                current.setdefault("artifacts", []).append(receipt)
                home.store.put(db, "runs", current)
                home.store.event(
                    db, "artifact.saved", {"run_id": run["id"], "path": result["path"]}
                )
            described = record_artifact(
                home,
                {**run, "config": refreshed},
                Path(result["path"]),
                receipt["sha256"],
                arguments,
            )
        except (OSError, sqlite3.Error) as exc:
            # The file exists now, so an identical retry would only report a
            # name clash. Say what happened and how to recover.
            log.exception("Rem report receipt was not recorded")
            shown = Path(result["path"]).relative_to(reports.root).as_posix()
            raise ValueError(
                f"The report was written to {shown}, but its receipt couldn't be recorded "
                f"({type(exc).__name__}). Save it again under a new name."
            ) from exc
        return {**result, **described}

    server = SwarmToolServer()
    url = server.register(
        call,
        [
            {
                "name": "save_report",
                # rules returns the full report format, theme and citation rules; the
                # prompt and this description point to it.
                "description": save_report_description(
                    "into this task's granted output folder", reports.report_format
                )
                + " To cite Second Brain sources, also pass evidence, title and question; "
                "rules explains them. " + SUMMARY_RULE,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "format": {"type": "string", "enum": ["html", "md", "pdf", "slides"]},
                        "title": {"type": "string"},
                        "question": {"type": "string"},
                        "summary": summary_schema(),
                        "evidence": evidence_schema(),
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False},
            },
            RULES_TOOL,
        ],
        "Save reports only through this granted output service. Call rules before writing one.",
        label="Report",
        failure_hint=TOOL_FAILURE_HINT,
    )
    try:
        yield {"name": "rem_artifacts", "type": "http", "url": url}
    finally:
        server.revoke(url)
        server.close()
