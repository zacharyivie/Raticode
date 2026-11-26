"""Exact search ranks documents by their best passages and keeps each one's passages together."""

from __future__ import annotations

import threading
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


def build(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    root.mkdir()
    # One short passage that says "compost" three times: the single best passage.
    (root / "pantry.md").write_text(
        "---\ntitle: Pantry list\n---\n"
        "## Bin\n\nCompost compost compost.\n\n"
        "## Shop\n\nBuy rice and oats.\n",
        encoding="utf-8",
    )
    # Three passages that each mention compost once: a document about compost.
    (root / "diary.md").write_text(
        "---\ntitle: Garden diary\n---\n"
        "## Monday\n\nSpread compost on the north bed before the rain came in.\n\n"
        "## Wednesday\n\nTurned the compost pile and mixed in the dry autumn leaves.\n\n"
        "## Friday\n\nThe finished compost went under the new tomato plants.\n\n"
        "## Sunday\n\nRested.\n",
        encoding="utf-8",
    )
    (root / "other.md").write_text(
        "---\ntitle: Bike repairs\n---\n## Chain\n\nOiled the chain and the meaning of gears.\n",
        encoding="utf-8",
    )
    brain = library.open({"root": str(root)})
    wait_idle(library)
    return brain


def titles(library: BrainLibrary) -> dict[str, str]:
    return {doc["id"]: doc["title"] for doc in library.entities("documents")}


def test_document_with_several_matching_passages_outranks_one_lucky_passage(library, tmp_path):
    brain = build(library, tmp_path)
    names = titles(library)
    hits = library.search({"brainId": brain["id"], "query": "compost"})["hits"]
    order = [names[doc_id] for doc_id in dict.fromkeys(hit["docId"] for hit in hits)]
    assert order == ["Garden diary", "Pantry list"]
    # Each document's passages are listed together, best first.
    docs = [names[hit["docId"]] for hit in hits]
    assert docs == ["Garden diary"] * 3 + ["Pantry list"]
    diary = [hit["score"] for hit in hits if names[hit["docId"]] == "Garden diary"]
    assert diary == sorted(diary, reverse=True)


def test_identical_documents_keep_their_passages_together(library, tmp_path):
    root = tmp_path / "copies"
    root.mkdir()
    # The passages score differently, so the search returns them strongest first across
    # both copies: one's best, two's best, one's second, two's second.
    body = (
        "## Bin\n\nCompost compost.\n\n"
        "## Wednesday\n\nTurned the compost pile and mixed in the dry autumn leaves today.\n"
    )
    for name in ("one", "two"):
        (root / f"{name}.md").write_text(f"---\ntitle: Copy {name}\n---\n{body}", encoding="utf-8")
    (root / "bike.md").write_text(
        "---\ntitle: Bike\n---\n## Chain\n\nOiled it.\n", encoding="utf-8"
    )
    brain = library.open({"root": str(root)})
    wait_idle(library)
    names = titles(library)
    hits = library.search({"brainId": brain["id"], "query": "compost"})["hits"]
    docs = [names[hit["docId"]] for hit in hits]
    assert sorted(docs) == ["Copy one", "Copy one", "Copy two", "Copy two"]
    # Equal scores, yet each copy's passages are listed together, not interleaved.
    assert docs[0] == docs[1] and docs[2] == docs[3] and docs[0] != docs[2]


def test_combined_search_fuses_the_document_order(library, tmp_path, monkeypatch):
    brain = build(library, tmp_path)
    current = library.brain(brain["id"])
    current["settings"]["semantic"] = True
    library.put_brain(current)

    def vector(text: str) -> list[float]:
        return [1.0, 0.0] if "meaning" in text else [0.0, 1.0]

    monkeypatch.setattr(library, "_embed_many", lambda texts, *a: [vector(t) for t in texts])
    monkeypatch.setattr(library, "_embedding", lambda *a, **k: [1.0, 0.0])
    library.embed(brain["id"], threading.Event())
    names = titles(library)
    hits = library.search({"brainId": brain["id"], "query": "compost", "mode": "hybrid"})["hits"]
    order = [names[doc_id] for doc_id in dict.fromkeys(hit["docId"] for hit in hits)]
    # Meaning finds only the bike note, so the diary and the pantry list keep the
    # word ranking's document order in the fused list.
    assert order.index("Garden diary") < order.index("Pantry list")
    docs = [names[hit["docId"]] for hit in hits]
    assert docs[docs.index("Garden diary") :][:3] == ["Garden diary"] * 3
