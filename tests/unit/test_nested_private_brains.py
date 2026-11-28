"""A private brain inside a cloud-allowed brain's folder stays private.

The outer brain's index used to walk into the inner brain's folder and serve its notes
as its own, so agent tokens and cloud turns read them through the allowed brain.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_privacy import agent_brain_refusal, agent_brain_result


# Keeps its data under data/, where these tests put the brains' parent folder.
@pytest.fixture
def library(tmp_path: Path) -> Iterator[BrainLibrary]:
    service = BrainLibrary(tmp_path / "data", start=False)
    yield service
    service.close()


def set_cloud(library: BrainLibrary, brain_id: str, cloud: bool) -> None:
    with library.connect() as db:
        body: dict[str, Any] = json.loads(
            db.execute("SELECT body FROM brains WHERE id=?", (brain_id,)).fetchone()[0]
        )
    body["settings"]["allowCloud"] = cloud
    library.put_brain(body)


def paths(library: BrainLibrary, brain_id: str) -> set[str]:
    return {
        doc["path"] for doc in library.entities("documents", brain_id) if not doc.get("missing")
    }


@pytest.fixture
def nested(library: BrainLibrary, tmp_path: Path) -> tuple[str, str]:
    outer_root = tmp_path / "notes"
    inner_root = outer_root / "journal"
    inner_root.mkdir(parents=True)
    (outer_root / "public.md").write_text("# Public\n\nShared plans.\n", encoding="utf-8")
    (inner_root / "diary.md").write_text("# Diary\n\nzanzibar is private.\n", encoding="utf-8")
    inner = library.open({"root": str(inner_root), "name": "Journal"})["id"]
    outer = library.open({"root": str(outer_root), "name": "Notes"})["id"]
    set_cloud(library, outer, True)
    for brain_id in (inner, outer):
        library.scan(brain_id, threading.Event(), None)
    return outer, inner


def test_the_outer_brain_leaves_the_private_brains_folder_out(library, nested):
    outer, inner = nested
    assert paths(library, outer) == {"public.md"}
    assert paths(library, inner) == {"diary.md"}
    assert not library.needs_scan(outer)


def test_making_the_inner_brain_private_later_drops_its_files_from_the_outer(library, nested):
    outer, inner = nested
    set_cloud(library, inner, True)
    library.scan(outer, threading.Event(), None)
    assert "journal/diary.md" in paths(library, outer)
    set_cloud(library, inner, False)
    assert library.needs_scan(outer)
    library.scan(outer, threading.Event(), None)
    assert paths(library, outer) == {"public.md"}


def test_agent_tokens_cant_read_an_outer_row_for_a_private_brains_file(library, nested):
    outer, inner = nested
    # A row the outer brain indexed before the inner one was registered or made private.
    set_cloud(library, inner, True)
    library.scan(outer, threading.Event(), None)
    stale = next(
        doc for doc in library.entities("documents", outer) if doc["path"] == "journal/diary.md"
    )
    set_cloud(library, inner, False)
    refusal = agent_brain_refusal(library, "original", {"id": stale["id"]})
    assert refusal and "Journal keeps Allow cloud processing off" in refusal
    sources = agent_brain_result(
        library, "report_sources", {"sources": [{"id": stale["id"], "brainId": outer}]}
    )
    assert sources["sources"] == []
    public = next(doc for doc in library.entities("documents", outer) if doc["path"] == "public.md")
    assert agent_brain_refusal(library, "original", {"id": public["id"]}) is None
