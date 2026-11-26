"""Rem's turn context lists Second Brain notes briefly: path, title, summary, matched passage."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary
from gofer.ui.rem_home_lookups import RULES, Lookup, RemLookups
from gofer.ui.second_brain import (
    CONTEXT_KNOWLEDGE_CHARS,
    CONTEXT_MIN_PASSAGE_CHARS,
    CONTEXT_PASSAGE_CHARS,
    SecondBrain,
    add_context_note,
)
from gofer.ui.server import GoferUiServer
from tests.helpers.brain_library import wait_idle

THEMED_REPORT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Orbit review</title>
<style>
:root { --void: #0B0620; --magenta: #FF3CC7; }
body { background: linear-gradient(#0B0620, #2A0E6B); font-family: Inter, sans-serif; }
h1 { background-clip: text; filter: drop-shadow(0 0 8px #FF3CC7); }
</style></head>
<body>
<header class="hero">
<svg aria-hidden="true" viewBox="0 0 100 40"><defs><linearGradient id="sun">
<stop offset="0" stop-color="#FFE08A"/></linearGradient></defs>
<text x="4" y="20">SKYLINE TOWER LABEL</text><circle cx="80" cy="10" r="6"/></svg>
<div class="card"><h1>Orbit review</h1></div>
</header>
<main>
<section><h2><span class="num">01</span> Summary</h2>
<p>The launch window opens next quarter and the team is on track for it.</p></section>
<section><h2><span class="num">02</span> Risks</h2>
<p>Telemetry gaps remain the largest risk to the schedule this year.</p>
<p>Fuel margins are thin but recoverable with the spare tank.</p></section>
<section><h2><span class="num">03</span> Decisions</h2>
<p>The crew chose the zircon heat shield after the second review.</p></section>
</main>
<script>console.log("never shown")</script>
</body></html>
"""

MARKDOWN_NOTE = (
    "# Garden plan\n\n"
    "Plant marigold seedlings along the south fence in early spring.\n\n"
    "## Beds\n\n"
    "- Tomatoes in bed one\n- Basil in bed two\n\n"
    "```\nwater: daily\nshade: none\n```\n\n"
    "## Notes\n\n" + ("Long paragraph about compost and mulch. " * 60).strip() + "\n\n"
    "## Later\n\n" + "\n\n".join(f"Week {n} chores are weeding." for n in range(200)) + "\n"
)


@pytest.fixture
def library(tmp_path: Path) -> Iterator[BrainLibrary]:
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "Reports").mkdir(parents=True)
    (brain / "Reports" / "orbit.html").write_text(THEMED_REPORT, encoding="utf-8")
    (brain / "garden.md").write_text(MARKDOWN_NOTE, encoding="utf-8")
    return brain


class Store:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @contextmanager
    def connect(self) -> Iterator[None]:
        yield None

    def setting(self, db: None, name: str) -> dict[str, Any]:
        assert name == "config"
        return self.config


def server(tmp_path: Path, library: BrainLibrary, root: Path, brain_ids: list[str]) -> Any:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    config = {
        "projects": [],
        "brain": {"enabled": True, "root": str(root), "brainIds": brain_ids},
    }
    return SimpleNamespace(
        rem_home=SimpleNamespace(store=Store(config)),
        _authorize_rem_home=lambda config, recipe: None,
        _private_rem_projects=lambda config, recipe, roots: {},
        data_dir=data,
        organizations=SimpleNamespace(store=SimpleNamespace(summaries=lambda: [])),
        second_brains=library,
        swarms=SimpleNamespace(list=lambda root: []),
        chat_jobs=SimpleNamespace(active_snapshot=lambda: []),
    )


def registered(library: BrainLibrary, root: Path) -> dict[str, Any]:
    brain = library.open({"root": str(root)})
    wait_idle(library)
    return brain


def knowledge(fake: Any, query: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = GoferUiServer._rem_home_context(fake, query)["knowledge"]
    return found


def by_path(library: BrainLibrary, path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents") if doc["path"] == path)


@pytest.mark.parametrize("indexed", [True, False])
def test_themed_html_report_reads_as_text_without_style_or_svg(
    tmp_path: Path, library: BrainLibrary, root: Path, indexed: bool
) -> None:
    ids = [registered(library, root)["id"]] if indexed else []
    notes = knowledge(server(tmp_path, library, root, ids), "launch window")
    assert [note["path"] for note in notes] == [str(root / "Reports" / "orbit.html")]
    text = notes[0]["passage"]
    assert "The launch window opens next quarter" in text
    assert "## 02 Risks" in text and "zircon heat shield" in text
    for markup in ("<", "#0B0620", "background", "SKYLINE", "stop-color", "console.log"):
        assert markup not in text


def test_excerpt_starts_at_the_matched_passage(
    tmp_path: Path, library: BrainLibrary, root: Path
) -> None:
    brain = registered(library, root)
    fake = server(tmp_path, library, root, [brain["id"]])
    report = knowledge(fake, "fuel margins")[0]["passage"]
    # The section's heading leads, then the matched passage; earlier sections are left out.
    assert report.startswith("## 02 Risks\n\nFuel margins are thin")
    assert "launch window" not in report and "zircon heat shield" in report

    garden = knowledge(fake, "weeding")[0]["passage"]
    assert garden.startswith("## Later\n\nWeek ")
    assert "marigold" not in garden
    assert len(garden) <= CONTEXT_PASSAGE_CHARS


def test_markdown_note_comes_out_as_the_raw_read_starts(
    tmp_path: Path, library: BrainLibrary, root: Path
) -> None:
    today = SecondBrain(root, "html").call("read_note", {"path": "garden.md"})["content"]
    # Without an indexed brain the note is read from its file, from the paragraph its
    # words first match, under its file name.
    raw = knowledge(server(tmp_path, library, root, []), "marigold")
    plant = today.index("Plant marigold")
    assert raw == [
        {
            "path": str(root / "garden.md"),
            "title": "garden",
            "passage": today[plant : plant + CONTEXT_PASSAGE_CHARS],
        }
    ]
    later = knowledge(server(tmp_path, library, root, []), "compost")[0]["passage"]
    assert later.startswith("Long paragraph about compost")

    brain = registered(library, root)
    notes = knowledge(server(tmp_path, library, root, [brain["id"]]), "marigold")
    assert notes == [
        {
            "path": str(root / "garden.md"),
            "title": "Garden plan",
            "passage": today[:CONTEXT_PASSAGE_CHARS],
        }
    ]


def test_excluded_and_missing_documents_are_skipped(
    tmp_path: Path, library: BrainLibrary, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = registered(library, root)
    report = by_path(library, "Reports/orbit.html")
    library.put("documents", {**report, "excluded": True})
    search = library.search

    def with_stale_hits(params: dict[str, Any]) -> dict[str, Any]:
        found = search(params)
        gone = {"docId": "doc-gone", "blockId": "b-gone", "score": 9.0, "matchKind": "content"}
        return {**found, "hits": [gone, *found["hits"]]}

    monkeypatch.setattr(library, "search", with_stale_hits)
    notes = knowledge(server(tmp_path, library, root, [brain["id"]]), "launch marigold")
    assert [note["path"] for note in notes] == [str(root / "garden.md")]


def test_document_without_passages_falls_back_to_the_raw_read(
    tmp_path: Path, library: BrainLibrary, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = registered(library, root)
    report = by_path(library, "Reports/orbit.html")
    hit = library.search({"brainIds": [brain["id"]], "query": "zircon", "mode": "exact"})
    library.put("documents", {**report, "blocks": [], "extraction": "failed"})
    monkeypatch.setattr(library, "search", lambda params: hit)
    notes = knowledge(server(tmp_path, library, root, [brain["id"]]), "zircon")
    assert len(notes) == 1
    # Read from the file, still as text, from the paragraph the query matched.
    assert notes[0]["title"] == "Orbit review"
    assert notes[0]["passage"].startswith("The crew chose the zircon heat shield")
    assert "<" not in notes[0]["passage"]


def test_at_most_five_notes(tmp_path: Path, library: BrainLibrary, root: Path) -> None:
    for index in range(8):
        (root / f"comet-{index}.md").write_text(f"# Comet {index}\n\nComet sighting.\n")
    brain = registered(library, root)
    notes = knowledge(server(tmp_path, library, root, [brain["id"]]), "comet")
    assert len(notes) == 5
    assert len({note["path"] for note in notes}) == 5


def long_note(index: int) -> str:
    """A 20,000-character Markdown note with a summary and 'nebula' deep inside it."""
    head = f"---\ntitle: Survey {index}\nsummary: Survey {index} of the outer rings.\n---\n\n"
    filler = "\n\n".join(f"Ring {index} reading {n} was steady and calm." for n in range(400))
    later = "\n\n".join(f"Echo {index} line {n} faded out." for n in range(400))
    body = f"# Survey {index}\n\n{filler}\n\nThe nebula drifted past ring {index}.\n\n{later}"
    text = head + body
    assert len(text) >= 20_000
    return (text + "\n" + "Tail line. " * 2000)[:20_000]


def test_five_long_notes_fit_the_knowledge_cap_as_summary_and_passage(
    tmp_path: Path, library: BrainLibrary, root: Path
) -> None:
    for index in range(5):
        (root / f"survey-{index}.md").write_text(long_note(index), encoding="utf-8")
    brain = registered(library, root)
    notes = knowledge(server(tmp_path, library, root, [brain["id"]]), "nebula")
    assert len(json.dumps(notes, ensure_ascii=False)) <= CONTEXT_KNOWLEDGE_CHARS
    assert notes, "at least one matching note is listed"
    for note in notes:
        index = int(Path(note["path"]).stem.rsplit("-", 1)[1])
        assert note["path"] == str(root / f"survey-{index}.md")
        assert note["title"] == f"Survey {index}"
        assert note["summary"] == f"Survey {index} of the outer rings."
        # The passage is the one the search matched, not the note's opening.
        assert f"The nebula drifted past ring {index}." in note["passage"]
        assert "reading 0 was" not in note["passage"]
        assert len(note["passage"]) <= CONTEXT_PASSAGE_CHARS
    # Each full note alone was 20,000 characters; the old context sent 6,000 of each.
    assert sum(len(note["passage"]) for note in notes) < 5 * 6000


def test_note_lookup_reads_past_the_listed_passage(
    tmp_path: Path, library: BrainLibrary, root: Path
) -> None:
    for index in range(5):
        (root / f"survey-{index}.md").write_text(long_note(index), encoding="utf-8")
    brain = registered(library, root)
    fake = server(tmp_path, library, root, [brain["id"]])
    listed = knowledge(fake, "nebula")[0]
    lookups = RemLookups(config=fake.rem_home.store.config, tasks=lambda: [], brain_library=library)
    found = lookups.run([Lookup(kind="note", path=listed["path"])])[0]
    text = found["text"]
    assert len(text) > CONTEXT_PASSAGE_CHARS
    # Text from beyond the note's first 1,200 characters, which the context left out.
    ring = Path(listed["path"]).stem.rsplit("-", 1)[1]
    assert f"Ring {ring} reading 100 was steady" in text[CONTEXT_PASSAGE_CHARS:]
    assert f"Ring {ring} reading 100 was steady" not in listed["passage"]
    # The chat's lookup help says so.
    assert "workspace.knowledge" in RULES and "reads the rest of that note" in RULES


def test_notes_past_the_cap_get_a_shorter_passage_then_stop() -> None:
    def build(index: int) -> Any:
        def note(limit: int) -> dict[str, Any]:
            return {
                "path": f"/brain/notes/n{index}.md",
                "title": f"Note {index}",
                "summary": "s" * 200,
                "passage": ("p" * 5000)[:limit],
            }

        return note

    notes: list[dict[str, Any]] = []
    added = [add_context_note(notes, build(index)) for index in range(7)]
    size = len(json.dumps(notes, ensure_ascii=False))
    assert size <= CONTEXT_KNOWLEDGE_CHARS
    # Five full notes, a sixth cut to fill the room, and none after it.
    assert added == [True] * 6 + [False]
    assert [len(n["passage"]) for n in notes[:5]] == [CONTEXT_PASSAGE_CHARS] * 5
    assert CONTEXT_MIN_PASSAGE_CHARS <= len(notes[5]["passage"]) < CONTEXT_PASSAGE_CHARS
    assert size > CONTEXT_KNOWLEDGE_CHARS - 10


def test_rem_sees_the_newest_desktop_threads_and_how_many_there_are(tmp_path: Path) -> None:
    fake = server(tmp_path, None, tmp_path, [])  # type: ignore[arg-type]
    fake.rem_home.store.config.update(
        brain={"enabled": False},
        workspace_snapshot={
            "threads": [
                {"id": f"t{n}", "title": f"Thread {n}", "updatedAt": f"2026-11-{n + 1:02d}"}
                for n in range(30)
            ]
        },
    )
    desktop = GoferUiServer._rem_home_context(fake, "anything")["desktop"]
    assert [t["id"] for t in desktop["threads"]] == [f"t{n}" for n in range(29, 4, -1)]
    assert desktop["threads_total"] == 30
    # The stored snapshot keeps every thread for the threads lookup.
    assert len(fake.rem_home.store.config["workspace_snapshot"]["threads"]) == 30
