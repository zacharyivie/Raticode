from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_families import (
    check_by,
    check_due,
    demote_replaced,
    replacement_status,
    report_families,
)
from gofer.ui.brain_library import BrainLibrary
from tests.helpers.brain_library import wait_idle


def paragraph(topic: str, index: int) -> str:
    words = " ".join(f"{topic}{index}word{n}" for n in range(30))
    return f"Finding {index} about {topic} rollout planning: {words}."


def html(title: str, paragraphs: list[str]) -> str:
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"


def doc(
    doc_id: str, texts: list[str], modified: str, brain: str = "b", **extra: Any
) -> dict[str, Any]:
    return {
        "id": doc_id,
        "brainId": brain,
        "path": f"Reports/{doc_id}.html",
        "title": doc_id.title(),
        "version": hashlib.sha256("".join(texts).encode()).hexdigest(),
        "extraction": "ready",
        "dates": {"modified": modified, "added": modified},
        "blocks": [{"id": f"{doc_id}-{n}", "text": text} for n, text in enumerate(texts)],
        **extra,
    }


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def test_families_need_most_long_passages_and_skip_boilerplate_and_copies():
    shared = [paragraph("plan", n) for n in range(5)]
    notice = paragraph("notice", 0)
    documents = [
        doc("v1", [*shared, paragraph("old", 0)], "2026-10-01T00:00:00+00:00"),
        doc("v2", [*shared, paragraph("new", 0), paragraph("new", 1)], "2026-11-01T00:00:00+00:00"),
        # Shares one long passage with v1 and v2: a quotation, not a version.
        doc("quote", [shared[0], paragraph("other", 0), paragraph("other", 1)], "2026-11-02"),
        # An exact copy is a duplicate, not a family member.
        doc("copy", [*shared, paragraph("new", 0), paragraph("new", 1)], "2026-11-03"),
    ]
    documents[3]["version"] = documents[1]["version"]
    # A series that repeats only a template notice is not a family.
    documents += [
        doc(
            f"day{n}",
            [notice, paragraph(f"day{n}x", 0), paragraph(f"day{n}y", 0)],
            f"2026-09-{n + 10}",
        )
        for n in range(8)
    ]
    families = report_families(documents)
    assert len(families) == 1
    family = families[0]
    members = [member["docId"] for member in family["members"]]
    # The copy joins through v1, but never through its identical twin v2.
    assert set(members) == {"v1", "v2", "copy"}
    assert "quote" not in members and not any(m.startswith("day") for m in members)
    suggestion = family["suggestion"]
    assert suggestion["olderId"] == "v1"
    assert suggestion["shared"] == 5 and suggestion["olderPassages"] == 6


def test_three_versions_suggest_one_at_a_time_toward_the_newest():
    shared = [paragraph("plan", n) for n in range(6)]
    documents = [
        doc("v1", [*shared, paragraph("a", 0)], "2026-09-01"),
        doc("v2", [*shared, paragraph("b", 0)], "2026-10-01"),
        doc("v3", [*shared, paragraph("c", 0)], "2026-11-01"),
    ]
    family = report_families(documents)[0]
    assert [member["docId"] for member in family["members"]] == ["v3", "v2", "v1"]
    assert family["currentId"] == "v3"
    first = family["suggestion"]
    assert first["newerId"] == "v3" and first["olderId"] in {"v1", "v2"}
    documents[[d["id"] for d in documents].index(first["olderId"])]["replacedBy"] = {"docId": "v3"}
    second = report_families(documents)[0]["suggestion"]
    assert second["newerId"] == "v3" and second["olderId"] != first["olderId"]
    for item in documents[:2]:
        item["replacedBy"] = {"docId": "v3"}
    assert report_families(documents)[0]["suggestion"] is None


def test_status_names_the_newer_report_and_follows_the_chain():
    v1 = doc("v1", [], "1", replacedBy={"docId": "v2"})
    v2 = doc("v2", [], "2", replacedBy={"docId": "v3"})
    v3 = doc("v3", [], "3", checkBy="2027-01-01")
    by_id = {item["id"]: item for item in (v1, v2, v3)}
    status = replacement_status(v1, by_id)
    assert status["status"] == "replaced"
    assert status["replacedBy"] == {"documentId": "v2", "title": "V2", "path": "Reports/v2.html"}
    assert status["latest"]["documentId"] == "v3"
    assert replacement_status(v3, by_id) == {"status": "current", "checkBy": "2027-01-01"}
    hits = [{"docId": "v1"}, {"docId": "v3"}, {"docId": "v2"}]
    assert [hit["docId"] for hit in demote_replaced(hits, {"v1", "v2"})] == ["v3", "v1", "v2"]


def test_check_by_validation_and_due_dates():
    assert check_by("") == "" and check_by("2026-12-01") == "2026-12-01"
    for bad in ("2026-13-01", "12/01/2026", "soon"):
        with pytest.raises(ValueError):
            check_by(bad)
    today = date(2026, 11, 13)
    assert check_due({"checkBy": "2026-11-13"}, today)
    assert not check_due({"checkBy": "2026-11-14"}, today)
    assert not check_due({"checkBy": "2026-11-01", "replacedBy": {"docId": "x"}}, today)


def write_reports(root: Path) -> dict[str, Path]:
    shared = [paragraph("plan", n) for n in range(5)]
    files = {
        "old": root / "Reports" / "plan-review.html",
        "new": root / "Reports" / "plan-review-final.html",
        "other": root / "Reports" / "unrelated.html",
    }
    files["old"].parent.mkdir(parents=True)
    files["old"].write_text(
        html("Plan review draft", [*shared, paragraph("draft", 0)]), encoding="utf-8"
    )
    files["new"].write_text(
        html("Plan review final", [*shared, paragraph("final", 0), paragraph("final", 1)]),
        encoding="utf-8",
    )
    files["other"].write_text(
        html("Unrelated", [paragraph("garden", 0), paragraph("garden", 1)]), encoding="utf-8"
    )
    now = time.time()
    os.utime(files["old"], (now - 86400 * 20, now - 86400 * 20))
    os.utime(files["new"], (now - 86400, now - 86400))
    return files


def test_confirmed_replacement_is_portable_ranks_lower_and_leaves_files_alone(
    library, tmp_path, monkeypatch
):
    root = tmp_path / "brain"
    root.mkdir()
    files = write_reports(root)
    before = {name: path.read_bytes() for name, path in files.items()}
    brain = library.open({"root": str(root)})
    wait_idle(library)
    docs = {item["path"]: item for item in library.entities("documents", brain["id"])}
    old = docs["Reports/plan-review.html"]
    new = docs["Reports/plan-review-final.html"]

    snapshot = library.snapshot()
    assert len(snapshot["families"]) == 1
    assert snapshot["families"][0]["suggestion"]["olderId"] == old["id"]
    suggestion = [issue for issue in snapshot["health"] if issue["kind"] == "family"]
    assert len(suggestion) == 1
    assert suggestion[0]["docId"] == old["id"] and suggestion[0]["newerId"] == new["id"]
    assert "Plan review final" in suggestion[0]["detail"]

    # Before confirming, the draft can outrank the final report for its own words.
    library.request("confirm_replacement", {"olderId": old["id"], "newerId": new["id"]})
    assert {name: path.read_bytes() for name, path in files.items()} == before
    portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
    stored = next(item for item in portable["library"]["documents"] if item["id"] == old["id"])
    assert stored["replacedBy"]["docId"] == new["id"]
    assert not [i for i in library.snapshot()["health"] if i["kind"] == "family"]

    hits = library.search({"brainId": brain["id"], "query": "plan0word1 rollout"})["hits"]
    order = list(dict.fromkeys(hit["docId"] for hit in hits))
    assert order.index(new["id"]) < order.index(old["id"])
    old_hit = next(hit for hit in hits if hit["docId"] == old["id"])
    assert old_hit["status"] == "replaced"
    assert old_hit["replacedBy"]["title"] == "Plan review final"

    # A rescan keeps the mark, and a report can't be replaced by its own older copy.
    library.scan(brain["id"])
    assert library.entity("documents", old["id"])["replacedBy"]["docId"] == new["id"]
    with pytest.raises(ValueError, match="already marked"):
        library.request("confirm_replacement", {"olderId": new["id"], "newerId": old["id"]})

    from gofer.ui import second_brain

    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})

    def serve(call, definitions, rules, name):
        assert "replacedBy names the newer report" in call("rules", {})
        results = call("search", {"format": "json", "query": "plan0word1"})["results"]
        paths = [item["path"] for item in results]
        assert paths.index(new["path"]) < paths.index(old["path"])
        replaced = next(item for item in results if item["documentId"] == old["id"])
        assert replaced["status"] == "replaced"
        assert replaced["replacedBy"]["documentId"] == new["id"]
        assert replaced["replacedBy"]["title"] == "Plan review final"
        current = next(item for item in results if item["documentId"] == new["id"])
        assert current["status"] == "current"
        listing = call("search", {"format": "json", "query": ""})["results"]
        assert listing[-1]["documentId"] == old["id"] and listing[-1]["status"] == "replaced"
        note = call("read_note", {"documentId": old["id"]})
        assert note["status"] == "replaced" and note["replacedBy"]["path"] == new["path"]

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")

    # The mark travels with the folder: another Raticode opening it sees it.
    library.request("remove", {"id": brain["id"]})
    elsewhere = BrainLibrary(tmp_path / "other-app", start=False)
    try:
        reopened = elsewhere.open({"root": str(root)})
        wait_idle(elsewhere)
        moved = elsewhere.entity("documents", old["id"])
        assert moved["brainId"] == reopened["id"]
        assert moved["replacedBy"]["docId"] == new["id"]
        elsewhere.request("clear_replacement", {"docId": old["id"]})
        assert "replacedBy" not in elsewhere.entity("documents", old["id"])
        assert [i for i in elsewhere.snapshot()["health"] if i["kind"] == "family"]
    finally:
        elsewhere.close()
    assert {name: path.read_bytes() for name, path in files.items()} == before


def test_check_by_date_raises_a_library_health_reminder(
    library: BrainLibrary, tmp_path: Path
) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    write_reports(root)
    brain = library.open({"root": str(root)})
    wait_idle(library)
    report = next(
        item
        for item in library.entities("documents", brain["id"])
        if item["path"] == "Reports/unrelated.html"
    )

    def mutate(value: str) -> dict[str, Any]:
        return library.mutate(
            {"kind": "documents", "id": report["id"], "item": {**report, "checkBy": value}}
        )

    def reminders() -> list[dict[str, Any]]:
        return [issue for issue in library.snapshot()["health"] if issue["kind"] == "review"]

    future = (date.today() + timedelta(days=30)).isoformat()
    assert mutate(future)["checkBy"] == future
    assert not reminders()
    past = (date.today() - timedelta(days=1)).isoformat()
    mutate(past)
    due = reminders()
    assert len(due) == 1 and due[0]["docId"] == report["id"] and past in due[0]["detail"]
    portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
    assert (
        next(d for d in portable["library"]["documents"] if d["id"] == report["id"])["checkBy"]
        == past
    )
    library.scan(brain["id"])
    assert library.entity("documents", report["id"])["checkBy"] == past
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        mutate("next week")
    library.request("ignore_health", {"ids": [due[0]["id"]]})
    assert not reminders()
    # Marking the report checked clears the date and the reminder.
    report = library.entity("documents", report["id"])
    assert mutate("")["checkBy"] == ""
    assert not reminders()
