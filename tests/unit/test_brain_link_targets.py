"""Index scans and Library health resolve links through lookup tables, not a full scan per link."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.ui.brain_library import link_targets, linked_document, linked_path


def old_linked_document(
    doc: dict[str, Any], link: str, documents: list[dict[str, Any]]
) -> str | None:
    """The resolver before lookup tables: every link scanned every document."""
    try:
        relative = linked_path(doc, link)
    except ValueError:
        return None
    matches = [item for item in documents if item["path"] == relative and not item.get("missing")]
    if not matches:
        vault_relative = link.split("#", 1)[0]
        matches = [
            item for item in documents if item["path"] == vault_relative and not item.get("missing")
        ]
    if not matches and not Path(link).suffix:
        matches = [
            item
            for item in documents
            if not item.get("missing")
            and (
                item["path"] in {relative + ".md", vault_relative + ".md"}
                or ("/" not in link and Path(item["path"]).stem == link)
            )
        ]
    return matches[0]["id"] if len(matches) == 1 else None


def document(path: str, *, missing: bool = False) -> dict[str, Any]:
    return {
        "id": f"id:{path}",
        "path": path,
        # Same folder rule as BrainLibrary._scan.
        "folder": str(Path(path).parent).replace(".", "", 1),
        "missing": missing,
    }


PATHS = [
    "index.md",
    "notes.md",
    "notes/a.md",
    "notes/b.md",
    "notes/deep/c.md",
    "notes/deep/a.md",
    "papers/study.pdf",
    "papers/study.md",
    "papers/My Paper.md",
    "readme.txt",
    "drafts/plan.md",
    "drafts/plan",
    "projects/plan.md",
    "solo.md",
]
DOCUMENTS = [document(path) for path in PATHS] + [document("gone.md", missing=True)]

LINKS = [
    # Relative to the document's folder, with and without anchors.
    "a.md",
    "b.md#intro",
    "../index.md",
    "../notes.md",
    "deep/c.md",
    "./b.md",
    # Rooted at the brain.
    "notes/b.md",
    "papers/study.pdf",
    "papers/My%20Paper.md",
    "papers/My Paper.md",
    # No suffix: path plus ".md" and bare stems.
    "a",
    "b",
    "c",
    "index",
    "plan",
    "drafts/plan",
    "study",
    "solo",
    "solo#part",
    "notes/deep/c",
    "../solo",
    # Missing, ambiguous and unsafe.
    "gone",
    "gone.md",
    "nothing.md",
    "nothing",
    "../../outside.md",
    "/etc/passwd",
    "C:/notes/a.md",
    "notes\\a.md",
    # Web addresses.
    "https://example.com/notes/a.md",
    "mailto:ada@example.com",
    "http://[broken",
]


def test_lookup_tables_give_the_same_cites_as_the_full_scan():
    targets = link_targets(DOCUMENTS)
    for doc in DOCUMENTS:
        old = {old_linked_document(doc, link, DOCUMENTS) for link in LINKS} - {None}
        new = {linked_document(doc, link, targets) for link in LINKS} - {None}
        assert new == old, doc["path"]
        for link in LINKS:
            assert linked_document(doc, link, targets) == old_linked_document(
                doc, link, DOCUMENTS
            ), (doc["path"], link)
    # The corpus exercises every rule: folder-relative, vault-rooted, ".md" and stem matches.
    cites = {
        link: linked_document(DOCUMENTS[2], link, targets)
        for link in ("b", "a", "../notes.md", "c")
    }
    # "a" fits notes/a.md by path plus ".md" and notes/deep/a.md by stem, so it names neither.
    assert cites == {
        "b": "id:notes/b.md",
        "a": None,
        "../notes.md": "id:notes.md",
        "c": "id:notes/deep/c.md",
    }


def test_ambiguous_and_missing_links_name_no_document():
    targets = link_targets(DOCUMENTS)
    root_doc = DOCUMENTS[0]
    # "plan" fits drafts/plan.md and projects/plan.md by stem.
    assert linked_document(root_doc, "plan", targets) is None
    assert linked_document(root_doc, "gone", targets) is None
    assert linked_document(root_doc, "solo", targets) == "id:solo.md"


def test_in_page_anchors_never_cite_a_document():
    targets = link_targets(DOCUMENTS)
    note = next(doc for doc in DOCUMENTS if doc["path"] == "notes/a.md")
    # The full scan read "#intro" as the note's own folder, notes, and cited notes.md.
    assert old_linked_document(note, "#intro", DOCUMENTS) == "id:notes.md"
    assert linked_document(note, "#intro", targets) is None


def test_web_addresses_are_skipped_before_any_lookup():
    named = [document("https:.md"), document("mailto:ada@example.com.md")]
    targets = link_targets(named)
    for link in ("https:", "mailto:ada@example.com", "http://[broken"):
        assert linked_document(named[0], link, targets) is None
