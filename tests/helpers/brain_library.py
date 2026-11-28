"""Shared fixtures, sample brains and waits for Second Brain library tests."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary

# Building the graph after a scan takes longer than other library tasks.
GRAPH_TIMEOUT = 20

REPO = "/work/Raticode/Raticode"


def wait_idle(library: BrainLibrary, timeout: float = 15) -> None:
    """Wait until no library task is running, failing the test after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain tasks did not finish")


def wait_task(library: BrainLibrary, task: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
    """Wait for one library task to stop running and return its receipt."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = library.task(task["id"])
        if receipt["state"] != "running":
            return receipt
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")


@pytest.fixture
def library(tmp_path: Path) -> Iterator[BrainLibrary]:
    """A Second Brain library in the test's folder, with no background monitor."""
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def html(title: str, body: str, meta: str = "") -> str:
    return f"<html><head><title>{title}</title>{meta}</head><body><p>{body}</p></body></html>"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_brain(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    """A brain with project folders, written projects, links and one archived Rem thread."""
    root = tmp_path / "brain"
    write(
        root, "Reports/Raticode/org-audit.html", html("Organizations audit", "Tickets approvals.")
    )
    write(
        root, "Reports/Taskurotta/release.html", html("Release handbook", "Signing notarization.")
    )
    write(
        root,
        "reports/rem/Reports/Raticode/search.html",
        html("Search audit", "Vector search repair notes."),
    )
    write(root, "reports/rem/youtube/digest.md", "# Digest\n\nVideos about embeddings.\n")
    write(
        root,
        "Notes/vectors.md",
        "---\nproject: Agent Lab\n---\n# Vectors\n\nSee [[Notes/plain]] for cosine notes.\n",
    )
    write(root, "Notes/plain.md", "# Plain\n\nCosine similarity basics.\n")
    write(
        root, "Notes/meta.html", html("Meta", "Body.", '<meta name="project" content="Raticode">')
    )
    write(root, "Inbox/loose.md", "# Loose\n\nUnfiled idea.\n")
    archive = root / "Rem Conversations"
    write(archive, "README.raticode.md", "# Rem conversation archive\n")
    snapshot = {
        "messages": [{"body": f"Saved [link](<{root}/Reports/Raticode/org-audit.html>)"}],
    }
    # Like conversation-archive.cjs, whose JSON.stringify keeps letters such as "ñ" as they are.
    write(archive, "threads/abc.json", json.dumps(snapshot, ensure_ascii=False))
    write(
        archive,
        "index.json",
        json.dumps(
            {
                "version": 1,
                "threads": {
                    "abc": {
                        "threadId": "thread-1",
                        "title": "Organizations audit thread",
                        "projectRoot": f"{REPO}/.worktrees/org-fix",
                        "updatedAt": "2026-11-04T10:00:00Z",
                        "messageCount": 4,
                        "snapshot": "threads/abc.json",
                    }
                },
            }
        ),
    )
    brain = library.open({"root": str(root)})
    wait_idle(library, timeout=GRAPH_TIMEOUT)
    return library.brain(brain["id"]), root


def doc_at(library: BrainLibrary, path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents") if doc["path"] == path)


def topic_brain(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    """A brain of four cooking notes and four search notes, two clear topics."""
    root = tmp_path / "topics"
    cooking = "pasta tomato sauce basil garlic simmer noodles kitchen recipe"
    vectors = "vector embedding cosine index retrieval search ranking corpus"
    for index in range(4):
        write(root, f"cook/c{index}.md", f"# Cooking {index}\n\n{cooking} variant{index}\n")
        write(root, f"search/s{index}.md", f"# Search {index}\n\n{vectors} variant{index}\n")
    brain = library.open({"root": str(root)})
    wait_idle(library, timeout=GRAPH_TIMEOUT)
    return brain
