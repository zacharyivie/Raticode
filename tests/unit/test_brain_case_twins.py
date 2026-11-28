"""Case-only twin files (a/Note.md and a/note.md) are found before they break a backup."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_packages
from gofer.ui.brain_backups import existing_backups
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.case_twins import CaseTwinError, case_twins, existing_twin
from gofer.ui.report_outputs import ReportTools
from gofer.ui.second_brain import SecondBrain
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_task

TWIN_ERROR = r"a/Note\.md and a/note\.md differ only by letter case"


def case_sensitive(folder: Path) -> bool:
    probe = folder / "case-probe"
    probe.write_text("x", encoding="utf-8")
    try:
        return not (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


@pytest.fixture
def twin_brain(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    (root / "a").mkdir(parents=True)
    if not case_sensitive(root):
        pytest.skip("This filesystem can't hold names that differ only by case")
    (root / "a" / "Note.md").write_text("# Upper\n\nFirst spelling.\n", encoding="utf-8")
    (root / "a" / "note.md").write_text("# Lower\n\nSecond spelling.\n", encoding="utf-8")
    (root / "other.md").write_text("# Other\n\nUnrelated.\n", encoding="utf-8")
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    return brain


def test_export_refuses_twins_before_reading_any_file(library, twin_brain, tmp_path, monkeypatch):
    reads: list[str] = []
    real_read = brain_packages.read_source

    def counted(root: Path, relative: str, *args: Any) -> bytes:
        reads.append(relative)
        return real_read(root, relative, *args)

    monkeypatch.setattr(brain_packages, "read_source", counted)
    for purpose in ("complete", "documents"):
        target = tmp_path / f"{purpose}.zip"
        with pytest.raises(CaseTwinError, match=TWIN_ERROR):
            library.export_package(
                {"brainId": twin_brain["id"], "destination": str(target), "purpose": purpose},
                threading.Event(),
                lambda _: None,
            )
        assert not target.exists()
    assert reads == []
    assert not [name for name in os.listdir(tmp_path) if name.startswith(".raticode-export-")]


def test_backup_fails_with_the_named_twins_and_writes_nothing(library, twin_brain, tmp_path):
    folder = tmp_path / "backups"
    library.update_brain({"id": twin_brain["id"], "patch": {"backup": {"folder": str(folder)}}})
    receipt = wait_task(library, library.start_task("backup", {"brainId": twin_brain["id"]}))
    assert receipt["state"] == "failed"
    assert "a/Note.md and a/note.md differ only by letter case" in receipt["error"]
    assert existing_backups(folder, library.brain(twin_brain["id"])) == []
    assert not [name for name in os.listdir(folder) if not name.startswith(".")]


def test_library_health_lists_twins_with_an_open_folder_action(library, twin_brain):
    twins = [item for item in library.snapshot()["health"] if item["kind"] == "twins"]
    assert len(twins) == 1
    issue = twins[0]
    assert issue["brainId"] == twin_brain["id"]
    assert issue["paths"] == ["a/Note.md", "a/note.md"]
    assert issue["path"] == str(Path(twin_brain["root"]) / "a")
    assert issue["fix"] == "Open folder"
    assert "a/Note.md and a/note.md differ only by letter case" in issue["detail"]

    (Path(twin_brain["root"]) / "a" / "note.md").unlink()
    library.recheck_health()
    assert not [item for item in library.snapshot()["health"] if item["kind"] == "twins"]


def test_ignored_twins_stay_hidden(library, twin_brain):
    issue = next(item for item in library.snapshot()["health"] if item["kind"] == "twins")
    library.request("ignore_health", {"ids": [issue["id"]]})
    library.recheck_health()
    assert not [item for item in library.snapshot()["health"] if item["kind"] == "twins"]


def save_note_tool(root: Path, path: str) -> Any:
    return SecondBrain(root, "md").call("save_note", {"path": path, "content": "# New\n\nText."})


def rem_artifacts_tool(root: Path, path: str) -> Any:
    # rem_artifacts.save_report is a ReportTools server rooted at the task's folder.
    return ReportTools(root, "md").call("save_report", {"path": path, "content": "# New\n\nText."})


@pytest.mark.parametrize("save", [save_note_tool, rem_artifacts_tool], ids=["save_note", "rem"])
def test_savers_refuse_a_case_only_twin_and_name_the_existing_file(tmp_path, save):
    root = tmp_path / "brain"
    (root / "Reports").mkdir(parents=True)
    if not case_sensitive(root):
        pytest.skip("This filesystem can't hold names that differ only by case")
    (root / "Reports" / "Plan.md").write_text("# Plan\n", encoding="utf-8")

    with pytest.raises(CaseTwinError, match=r"Reports/Plan\.md already exists, and Reports/plan"):
        save(root, "Reports/plan.md")
    # A new folder whose name twins an existing one is refused the same way.
    with pytest.raises(CaseTwinError, match=r"Reports already exists, and reports differs"):
        save(root, "reports/new.md")
    assert sorted(os.listdir(root)) == ["Reports"]
    assert os.listdir(root / "Reports") == ["Plan.md"]

    saved = save(root, "Reports/Other.md")
    assert Path(saved["path"]).is_file()


def test_existing_twins_on_disk_do_not_block_their_exact_spellings(tmp_path):
    root = tmp_path / "brain"
    (root / "Reports").mkdir(parents=True)
    if not case_sensitive(root):
        pytest.skip("This filesystem can't hold names that differ only by case")
    (root / "reports").mkdir()
    # Both folders exist already: writing into either exact spelling is fine.
    assert existing_twin(root, "reports/new.md") is None
    assert existing_twin(root, "Reports/new.md") is None
    assert existing_twin(root, "REPORTS/new.md") == ("Reports", "REPORTS")


def test_case_twins_pairs_each_extra_spelling_with_the_first():
    assert case_twins(["a/Note.md", "b.md", "a/note.md", "A/NOTE.md", "b.md"]) == [
        ("a/Note.md", "a/note.md"),
        ("a/Note.md", "A/NOTE.md"),
    ]


def test_accents_written_two_ways_are_twins_like_letter_case():
    # macOS sees "café" with one accented letter and with "e" plus an accent as one name.
    composed, decomposed = "notes/café.md", "notes/café.md"
    assert case_twins([composed, decomposed]) == [(composed, decomposed)]
    assert case_twins(["notes/Café.md", decomposed]) == [("notes/Café.md", decomposed)]


def test_restore_refuses_a_package_with_both_accent_spellings(tmp_path):
    import zipfile

    from gofer.ui.brain_documents import checked_archive

    package = tmp_path / "twins.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("files/café.md", "one")
        archive.writestr("files/café.md", "two")
    with pytest.raises(ValueError, match="duplicate or ambiguous"):
        checked_archive(package)
