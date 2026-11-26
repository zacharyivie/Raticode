"""The Second Brain snapshot leaves passages to the ``passages`` action.

The view polls the snapshot after every library change. It carries each document's
version, passage count and stored-history size; the view fetches the passages of only
the documents whose version it lacks, and older versions when a version view opens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary
from tests.helpers.brain_library import wait_idle


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def opened(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "plan.md").write_text("# Plan\n\nShip on Monday.\n\n## Risks\n\nRain.\n", "utf-8")
    (root / "notes.md").write_text("# Notes\n\nNothing yet.\n", "utf-8")
    brain = library.open({"root": str(root)})
    wait_idle(library)
    return brain, root


def by_path(library: BrainLibrary, brain_id: str) -> dict[str, dict[str, Any]]:
    return {doc["path"]: doc for doc in library.entities("documents", brain_id)}


def test_snapshot_documents_carry_counts_instead_of_passages(library, tmp_path):
    brain, root = opened(library, tmp_path)
    plan = by_path(library, brain["id"])["plan.md"]
    (root / "plan.md").write_text("# Plan\n\nShip on Friday.\n\n## Risks\n\nRain.\n", "utf-8")
    library.scan(brain["id"])
    stored = library.entity("documents", plan["id"])
    assert stored["versions"], "the edit kept the earlier version"
    listed = {doc["id"]: doc for doc in library.snapshot()["documents"]}[plan["id"]]
    assert "blocks" not in listed and "versions" not in listed
    assert listed["version"] == stored["version"]
    assert listed["passageCount"] == len(stored["blocks"]) > 0
    assert listed["versionCount"] == len(stored["versions"])
    history = json.dumps(stored["versions"], ensure_ascii=False, separators=(",", ":"))
    assert listed["historySize"] == len(history.encode())
    # The view's fallback asks for everything in the snapshot itself.
    full = {doc["id"]: doc for doc in library.poll(passages=True)["documents"]}[plan["id"]]
    assert full["blocks"] == stored["blocks"] and full["versions"] == stored["versions"]


def test_passages_answer_only_the_named_documents(library, tmp_path):
    brain, _root = opened(library, tmp_path)
    docs = by_path(library, brain["id"])
    plan, notes = docs["plan.md"], docs["notes.md"]
    answer = library.request("passages", {"ids": [plan["id"], "doc-gone", plan["id"]]})
    assert answer == {
        "documents": [
            {
                "id": plan["id"],
                "brainId": brain["id"],
                "version": plan["version"],
                "blocks": plan["blocks"],
            }
        ],
        "missing": ["doc-gone"],
    }
    assert notes["blocks"][0]["text"] not in json.dumps(answer)
    with_history = library.request("passages", {"ids": [notes["id"]], "history": True})
    assert with_history["documents"][0]["versions"] == []
    with pytest.raises(ValueError, match="list of document IDs"):
        library.request("passages", {"ids": plan["id"]})


def test_passages_leave_out_brains_whose_folder_is_gone(library, tmp_path):
    brain, root = opened(library, tmp_path)
    plan = by_path(library, brain["id"])["plan.md"]
    root.rename(tmp_path / "moved")
    assert library.request("passages", {"ids": [plan["id"]]}) == {
        "documents": [],
        "missing": [plan["id"]],
    }
