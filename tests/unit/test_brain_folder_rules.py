"""Folder rules: search a folder normally, list it after other results, or leave it out."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from gofer.ui.brain_folders import (
    apply_rule,
    checked_rules,
    lowered_folder,
    rule_for,
)
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_idle


def note(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {path.stem}\n\n{text}\n", encoding="utf-8")
    return path


def test_rules_validate_and_the_nearest_folder_wins():
    rules = checked_rules({"Archive/": "leave-out", "Archive/Keep": "normal", "Old": "list-after"})
    assert rules == {"Archive": "leave-out", "Archive/Keep": "normal", "Old": "list-after"}
    assert rule_for("Archive/2024/a.md", rules) == ("Archive", "leave-out")
    assert rule_for("Archive/Keep/b.md", rules) is None
    assert rule_for("Archived/c.md", rules) is None
    assert rule_for("top.md", rules) is None
    assert lowered_folder("Old/x/d.md", rules) == "Old"
    for bad in ({"../out": "leave-out"}, {"/abs": "leave-out"}, {"A": "hide"}, ["A"]):
        with pytest.raises(ValueError):
            checked_rules(bad)

    # Only a folder rule's own exclusion is undone; one set in Edit metadata stays.
    by_rule = {"path": "Archive/a.md"}
    assert apply_rule(by_rule, rules) and by_rule == {
        "path": "Archive/a.md",
        "excluded": True,
        "excludedBy": "Archive",
    }
    assert not apply_rule(by_rule, rules)
    assert apply_rule(by_rule, {}) and by_rule == {"path": "Archive/a.md", "excluded": False}
    by_user = {"path": "Archive/a.md", "excluded": True}
    apply_rule(by_user, rules)
    assert "excludedBy" not in by_user
    apply_rule(by_user, {})
    assert by_user["excluded"] is True


def test_folder_rules_leave_out_and_lower_documents_and_travel_with_the_brain(
    library, tmp_path, monkeypatch
):
    root = tmp_path / "brain"
    files = {
        "notes": note(root, "Notes/plan.md", "Harbor rollout plan for the pilot."),
        "archive": note(root, "Archive/plan-2024.md", "Harbor rollout plan from 2024."),
        "keep": note(root, "Archive/Keep/plan-kept.md", "Harbor rollout plan worth keeping."),
        "old": note(root, "Old/plan-old.md", "Harbor rollout plan, superseded draft."),
    }
    brain = library.open({"root": str(root)})
    wait_idle(library)
    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}

    def ranked(query: str = "harbor rollout") -> list[str]:
        hits = library.search({"brainId": brain["id"], "query": query})["hits"]
        ids = {doc["id"]: doc["path"] for doc in library.entities("documents", brain["id"])}
        return [ids[doc_id] for doc_id in dict.fromkeys(hit["docId"] for hit in hits)]

    assert set(ranked()) == {path.relative_to(root).as_posix() for path in files.values()}
    before = {name: path.read_bytes() for name, path in files.items()}

    library.update_brain(
        {
            "id": brain["id"],
            "patch": {
                "settings": {
                    "folderRules": {
                        "Archive": "leave-out",
                        "Archive/Keep": "normal",
                        "Old": "list-after",
                    },
                    "allowCloud": True,
                }
            },
        }
    )
    order = ranked()
    assert "Archive/plan-2024.md" not in order
    assert order[-1] == "Old/plan-old.md"
    assert set(order) == {"Notes/plan.md", "Archive/Keep/plan-kept.md", "Old/plan-old.md"}
    hits = library.search({"brainId": brain["id"], "query": "superseded"})["hits"]
    assert hits[0]["status"] == "lowered" and hits[0]["folder"] == "Old"
    archived = library.entity("documents", docs["Archive/plan-2024.md"]["id"])
    assert archived["excluded"] is True and archived["excludedBy"] == "Archive"

    # The rules are portable metadata; the files are untouched.
    portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
    assert portable["brain"]["settings"]["folderRules"]["Archive"] == "leave-out"
    assert {name: path.read_bytes() for name, path in files.items()} == before

    # A file added to a left-out folder later is never indexed.
    note(root, "Archive/new.md", "Harbor rollout plan added later.")
    library.scan(brain["id"])
    assert "Archive/new.md" not in ranked()
    added = next(
        d for d in library.entities("documents", brain["id"]) if d["path"].endswith("new.md")
    )
    assert added["excludedBy"] == "Archive"

    # Edit metadata can't put a file back while its folder is left out.
    with pytest.raises(ValueError, match="Brain settings › Folders"):
        library.mutate(
            {"kind": "documents", "id": archived["id"], "item": {**archived, "excluded": False}}
        )
    # Sending the flag unchanged with another edit is fine.
    library.mutate(
        {"kind": "documents", "id": archived["id"], "item": {**archived, "title": "Old plan"}}
    )
    # Leave out of search for a single file, in Edit metadata.
    notes_doc = library.entity("documents", docs["Notes/plan.md"]["id"])
    library.mutate(
        {"kind": "documents", "id": notes_doc["id"], "item": {**notes_doc, "excluded": True}}
    )
    assert "Notes/plan.md" not in ranked()

    from gofer.ui import second_brain

    def serve(call, definitions, rules, name):
        assert "status lowered" in call("rules", {})
        results = call("search", {"format": "json", "query": "harbor rollout"})["results"]
        paths = [item["path"] for item in results]
        assert paths == ["Archive/Keep/plan-kept.md", "Old/plan-old.md"]
        assert results[1]["status"] == "lowered" and results[1]["folder"] == "Old"
        assert results[0]["status"] == "current"
        text = call("search", {"query": "superseded"})[0]["text"]
        assert "status: lowered · folder: Old" in text
        listing = call("search", {"format": "json", "query": ""})["results"]
        assert [item["path"] for item in listing] == [
            "Archive/Keep/plan-kept.md",
            "Old/plan-old.md",
        ]
        with pytest.raises(ValueError, match="left out of search"):
            call("read_note", {"documentId": archived["id"]})
        with pytest.raises(ValueError, match="left out of search"):
            call("read_note", {"documentId": notes_doc["id"]})
        lowered = call("read_note", {"documentId": docs["Old/plan-old.md"]["id"]})
        assert lowered["status"] == "lowered" and lowered["folder"] == "Old"

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")

    # Packages carry the rules: a copy leaves the same folder out.
    package = tmp_path / "brain.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(package)}, threading.Event(), lambda _: None
    )
    copy = library.import_package(
        {"path": str(package), "destination": str(tmp_path / "copy"), "identity": "copy"},
        threading.Event(),
        lambda _: None,
    )
    wait_idle(library)
    copied = library.brain(copy["brainId"])
    assert copied["settings"]["folderRules"]["Archive"] == "leave-out"
    copied_hits = library.search({"brainId": copy["brainId"], "query": "harbor rollout"})["hits"]
    copied_paths = {library.entity("documents", hit["docId"])["path"] for hit in copied_hits}
    assert copied_paths == {"Archive/Keep/plan-kept.md", "Old/plan-old.md"}

    # Searching the folder normally again puts its files back, but not the file the
    # user left out in Edit metadata. An empty rule set leaves brain.json as it was.
    library.update_brain({"id": brain["id"], "patch": {"settings": {"folderRules": {}}}})
    assert set(ranked()) == {
        "Archive/plan-2024.md",
        "Archive/new.md",
        "Archive/Keep/plan-kept.md",
        "Old/plan-old.md",
    }
    assert "excludedBy" not in library.entity("documents", archived["id"])
    portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
    assert "folderRules" not in portable["brain"]["settings"]
    assert {name: path.read_bytes() for name, path in files.items()} == before
