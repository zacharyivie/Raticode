"""Second Brain polls cost nothing while nothing changes.

Every write to brains, entities, tasks and view preferences moves the library
revision. A poll that already has it gets ``{unchanged, revision}`` without the
library decoding entities or running health. Health is cached per revision and its
filesystem checks run again at most once a minute, or right away when asked.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_health, brain_library
from gofer.ui.brain_library import HEALTH_RECHECK_SECONDS, BrainLibrary, LinkTargets
from gofer.ui.server import GoferUiServer
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_idle


def opened(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text(
        "---\ntitle: Notes\nauthor: Ada\n---\n# Notes\n\n"
        "See [the plan](plan.md) and [the data](data.bin).\n",
        encoding="utf-8",
    )
    (root / "plan.md").write_text("# Plan\n\nShip it.\n", encoding="utf-8")
    # Not an indexed type: only the filesystem check knows whether it is there.
    (root / "data.bin").write_bytes(b"\0")
    brain = library.open({"root": str(root)})
    wait_idle(library)
    return brain, root


def link_issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [issue for issue in payload["health"] if issue["kind"] == "link"]


def expire_recheck(library: BrainLibrary) -> None:
    """Move the last filesystem check more than a minute into the past."""
    assert library._fs_checked_at is not None
    library._fs_checked_at -= HEALTH_RECHECK_SECONDS + 1


# --------------------------------------------------------------------- revision


def test_every_write_path_moves_the_revision(library, tmp_path):
    brain, root = opened(library, tmp_path)
    doc = next(d for d in library.entities("documents", brain["id"]) if d["path"] == "notes.md")
    report_root = root / "Reports"
    report_root.mkdir()

    def moves(write: Any) -> None:
        before = library.revision()
        write()
        assert library.revision() > before, write

    # brains
    moves(lambda: library.request("update_brain", {"id": brain["id"], "patch": {"name": "Ideas"}}))
    moves(lambda: library.put_brain({**library.brain(brain["id"]), "color": "teal"}))
    # entities
    moves(
        lambda: library.request(
            "mutate",
            {"kind": "documents", "id": doc["id"], "item": {**doc, "title": "Renamed"}},
        )
    )
    collection = {
        "id": "collection-1",
        "brainId": brain["id"],
        "name": "Reading",
        "sections": [],
    }
    moves(lambda: library.put("collections", collection))
    moves(
        lambda: library.request(
            "mutate", {"kind": "collections", "id": "collection-1", "delete": True}
        )
    )
    # view preferences
    moves(lambda: library.request("preferences", {"activeBrainId": brain["id"], "views": {}}))
    # tasks: starting one, its progress and result, dismissing it
    before = library.revision()
    task = library.request("task", {"kind": "index", "brainId": brain["id"]})
    wait_idle(library)
    assert library.revision() > before
    moves(lambda: library._task_update(task["id"], label="Renamed task"))
    moves(lambda: library.request("dismiss", {"id": task["id"]}))
    # health ignores write the brain or document record
    (root / "notes.md").write_text("# Notes\n\nSee [gone](gone.md).\n", encoding="utf-8")
    library.scan(brain["id"])
    issue = link_issues(library.snapshot())[0]
    moves(lambda: library.request("ignore_health", {"ids": [issue["id"]]}))
    # removing the brain
    moves(lambda: library.request("remove", {"id": brain["id"]}))


def test_a_scan_with_changes_moves_the_revision_and_reads_never_do(library, tmp_path):
    brain, root = opened(library, tmp_path)
    before = library.revision()
    library.snapshot()
    library.poll("")
    library.entities("documents")
    library.request("search", {"brainId": brain["id"], "text": "plan"})
    library._monitor_pass()
    assert library.revision() == before

    (root / "more.md").write_text("# More\n", encoding="utf-8")
    library.scan(brain["id"])
    assert library.revision() > before


def test_a_failed_write_leaves_the_revision_alone(library, tmp_path):
    opened(library, tmp_path)
    before = library.revision()
    with pytest.raises(RuntimeError), library.connect() as db:
        db.execute("INSERT OR REPLACE INTO preferences VALUES('view','{}')")
        raise RuntimeError("rolled back")
    assert library.revision() == before


def test_writes_from_another_library_instance_move_the_shared_revision(library, tmp_path):
    # Rem's Second Brain tools open their own BrainLibrary in another process.
    brain, _ = opened(library, tmp_path)
    token = library.snapshot()["revision"]
    other = BrainLibrary(tmp_path / "app", start=False, recover=False)
    try:
        other.put_brain({**other.brain(brain["id"]), "name": "Renamed elsewhere"})
    finally:
        other.close()
    result = library.poll(token)
    assert "unchanged" not in result
    assert result["brains"][0]["name"] == "Renamed elsewhere"


# ------------------------------------------------------------------------- poll


def test_poll_with_the_current_revision_skips_entities_and_health(library, tmp_path, monkeypatch):
    opened(library, tmp_path)
    token = library.poll("")["revision"]

    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("an unchanged poll must not decode entities or run health")

    monkeypatch.setattr(library, "entities", forbidden)
    monkeypatch.setattr(library, "health", forbidden)
    monkeypatch.setattr(library, "brains", forbidden)
    assert library.poll(token) == {"unchanged": True, "revision": token}
    assert library.request("snapshot", {"revision": token}) == {
        "unchanged": True,
        "revision": token,
    }


def test_a_poll_after_a_write_runs_the_patched_health(library, tmp_path, monkeypatch):
    # The partner of the unchanged-poll check above, which also passes when health is
    # never reached: a patched library.health must be the one a changed poll runs.
    brain, _ = opened(library, tmp_path)
    token = library.poll("")["revision"]
    calls: list[int] = []
    original = library.health

    def counted(payload: Any) -> Any:
        calls.append(1)
        return original(payload)

    monkeypatch.setattr(library, "health", counted)
    library.request("update_brain", {"id": brain["id"], "patch": {"name": "Ideas"}})
    result = library.poll(token)
    assert "unchanged" not in result
    assert len(calls) >= 1


def test_poll_returns_the_library_after_a_write(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    token = library.poll("")["revision"]
    library.request("update_brain", {"id": brain["id"], "patch": {"name": "Ideas"}})
    result = library.poll(token)
    assert "unchanged" not in result
    assert result["revision"] != token
    assert result["brains"][0]["name"] == "Ideas"
    assert library.poll(result["revision"])["unchanged"] is True


def test_an_unknown_or_missing_revision_gets_the_whole_library(library, tmp_path):
    opened(library, tmp_path)
    token = library.poll("")["revision"]
    assert "documents" in library.poll("")
    assert "documents" in library.poll("stale")
    # Another backend process tags its revisions differently, even at the same count.
    restarted = BrainLibrary(tmp_path / "app", start=False, recover=False)
    try:
        assert "documents" in restarted.poll(token)
    finally:
        restarted.close()


def test_a_brain_folder_that_disappears_ends_unchanged_polls(library, tmp_path):
    _, root = opened(library, tmp_path)
    token = library.poll("")["revision"]
    moved = tmp_path / "moved"
    root.rename(moved)
    result = library.poll(token)
    assert "unchanged" not in result
    assert result["brains"][0]["state"] == "unavailable"
    assert library.poll(result["revision"])["unchanged"] is True
    moved.rename(root)
    assert library.poll(result["revision"])["brains"][0]["state"] == "available"


# ----------------------------------------------------------------------- health


def test_health_runs_once_per_revision(library, tmp_path, monkeypatch):
    brain, _ = opened(library, tmp_path)
    calls: list[int] = []
    original = library.health

    def counted(payload: Any) -> Any:
        calls.append(1)
        return original(payload)

    monkeypatch.setattr(library, "health", counted)
    first = library.snapshot()
    second = library.snapshot()
    assert len(calls) <= 1
    assert first["health"] == second["health"]
    assert first["families"] == second["families"]
    calls.clear()
    library.request("update_brain", {"id": brain["id"], "patch": {"name": "Ideas"}})
    library.snapshot()
    assert calls == [1]


def test_link_checks_rerun_after_a_minute_not_on_every_poll(library, tmp_path):
    _, root = opened(library, tmp_path)
    token = library.poll("", recheck=True)["revision"]
    assert link_issues(library.snapshot()) == []
    # The linked file goes away without a write to the library.
    (root / "data.bin").unlink()
    assert library.poll(token) == {"unchanged": True, "revision": token}
    expire_recheck(library)
    result = library.poll(token)
    assert "unchanged" not in result
    assert [issue["target"] for issue in link_issues(result)] == ["data.bin"]
    assert library.poll(result["revision"]) == {"unchanged": True, "revision": result["revision"]}


def test_a_minute_recheck_that_finds_nothing_new_stays_unchanged(library, tmp_path):
    opened(library, tmp_path)
    token = library.poll("")["revision"]
    expire_recheck(library)
    assert library.poll(token) == {"unchanged": True, "revision": token}


def test_opening_library_health_rechecks_links_right_away(library, tmp_path):
    _, root = opened(library, tmp_path)
    token = library.poll("")["revision"]
    (root / "data.bin").unlink()
    result = library.poll(token, recheck=True)
    assert [issue["target"] for issue in link_issues(result)] == ["data.bin"]


def test_a_finished_scan_rechecks_links_right_away(library, tmp_path):
    brain, root = opened(library, tmp_path)
    assert link_issues(library.snapshot()) == []
    (root / "data.bin").unlink()
    assert link_issues(library.snapshot()) == []  # cached until the next recheck
    library.scan(brain["id"])  # nothing to index, but the scan finished
    assert [issue["target"] for issue in link_issues(library.snapshot())] == ["data.bin"]


def test_making_a_brain_private_rechecks_its_permissions(library, tmp_path):
    brain, root = opened(library, tmp_path)
    root.chmod(0o755)
    library.recheck_health()
    assert [i for i in library.snapshot()["health"] if i["kind"] == "permissions"]
    library.request("restrict_access", {"brainId": brain["id"]})
    assert not [i for i in library.snapshot()["health"] if i["kind"] == "permissions"]


def test_health_builds_each_brains_document_list_once(library, tmp_path, monkeypatch):
    brain, root = opened(library, tmp_path)
    (root / "notes.md").write_text(
        "# Notes\n\n[a](plan)\n[b](plan#top)\n[c](missing)\n[d](Plan)\n", encoding="utf-8"
    )
    library.scan(brain["id"])
    lists: list[int] = []
    original = brain_library.linked_document

    def recording(doc: dict[str, Any], link: str, targets: LinkTargets) -> Any:
        lists.append(id(targets))
        assert all(
            item["brainId"] == doc["brainId"]
            for items in targets.by_path.values()
            for item in items
        )
        return original(doc, link, targets)

    # The scan calls it from brain_library and health from brain_health.
    monkeypatch.setattr(brain_library, "linked_document", recording)
    monkeypatch.setattr(brain_health, "linked_document", recording)
    library.recheck_health()
    issues = link_issues(library.snapshot())
    assert len(lists) >= 3
    assert len(set(lists)) == 1
    assert {issue["target"] for issue in issues} == {"missing", "Plan"}


# -------------------------------------------------------------------------- API


@pytest.fixture
def api(tmp_path: Path):
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token="brain-poll-test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def get(server: GoferUiServer, query: str = "") -> dict[str, Any]:
    connection = http.client.HTTPConnection(
        str(server.server_address[0]), int(server.server_address[1]), timeout=10
    )
    connection.request(
        "GET",
        "/api/second-brains" + query,
        headers={"Authorization": "Bearer brain-poll-test"},
    )
    response = connection.getresponse()
    value = json.loads(response.read())
    connection.close()
    assert response.status == 200, value
    result: dict[str, Any] = value["result"]
    return result


def test_get_answers_unchanged_for_the_current_revision(api, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    api.second_brains.open({"root": str(root)})
    wait_idle(api.second_brains)
    full = get(api)
    assert full["brains"] and full["revision"]
    assert get(api, f"?revision={full['revision']}") == {
        "unchanged": True,
        "revision": full["revision"],
    }
    rechecked = get(api, f"?revision={full['revision']}&recheck=1")
    assert rechecked == {"unchanged": True, "revision": full["revision"]}
    api.second_brains.request("preferences", {"activeBrainId": full["brains"][0]["id"]})
    after = get(api, f"?revision={full['revision']}")
    assert after["activeBrainId"] == full["brains"][0]["id"]
    assert after["revision"] != full["revision"]
