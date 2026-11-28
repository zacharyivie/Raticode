"""Second Brain connections: provenance, project suggestions, topics and their errors."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.ui import brain_connections, brain_graph
from tests.helpers.brain_library import (
    REPO,
    build_brain,
    doc_at,
    topic_brain,
    write,
)
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture

BRAIN = "brain-1"


def note(**extra: Any) -> dict[str, Any]:
    return {"id": "doc-1", "brainId": BRAIN, "path": "Reports/x.html", "version": "v1"} | extra


def fake_library(
    root: Path,
    doc: dict[str, Any],
    *,
    reports: list[dict[str, Any]] | None = None,
    assignments: dict[str, dict[str, Any]] | None = None,
    similar: list[dict[str, Any]] | None = None,
    topics: Callable[[str], dict[str, Any]] | None = None,
    organizations: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    """Just the library calls connections() makes, with one stored document."""

    @contextmanager
    def connect() -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE observations(brain, doc, version, kind, actor, body, at)")
        try:
            yield db
        finally:
            db.close()

    def no_topics(brain_id: str) -> dict[str, Any]:
        return {"topicOf": {}, "topics": []}

    stored = {"documents": [doc], "reports": list(reports or [])}
    return SimpleNamespace(
        entity=lambda kind, doc_id: doc,
        brain=lambda brain_id: {"id": brain_id, "root": str(root)},
        entities=lambda kind, brain_id: stored[kind],
        projects=lambda brain_id: {
            "projects": [{"id": "project-1", "name": "Raticode"}],
            "conversations": [],
            "assignments": assignments or {},
        },
        _organizations=lambda: list(organizations or []),
        connect=connect,
        _similar=lambda item: list(similar or []),
        topics=topics or no_topics,
    )


def test_topics_group_similar_documents_label_them_and_keep_ids(library, tmp_path):
    brain = topic_brain(library, tmp_path)
    first = library.topics(brain["id"])
    assert first["basis"] == "words"
    paths = {doc["id"]: doc["path"] for doc in library.entities("documents")}
    groups = [{paths[d].split("/")[0] for d in topic["members"]} for topic in first["topics"]]
    assert sorted(map(sorted, groups)) == [["cook"], ["search"]]
    labels = " ".join(" ".join(topic["terms"]) for topic in first["topics"])
    assert "pasta" in labels or "tomato" in labels or "sauce" in labels
    assert all(-1.5 <= topic["x"] <= 1.5 for topic in first["topics"])
    # A user's name sticks to its topic, and recomputing keeps topic identities.
    topic = first["topics"][0]
    library.put(
        "topics",
        {"id": topic["id"], "brainId": brain["id"], "name": "Mine", "members": topic["members"]},
    )
    library._topic_cache.clear()
    second = library.topics(brain["id"])
    assert {t["id"] for t in second["topics"]} == {t["id"] for t in first["topics"]}
    assert next(t for t in second["topics"] if t["id"] == topic["id"])["label"] == "Mine"
    layout = library.topic_layout(brain["id"], topic["id"])
    assert {node["docId"] for node in layout["nodes"]} == set(topic["members"])


def test_connections_label_evidence_suggestions_and_organization_use(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    vectors = doc_at(library, "Notes/vectors.md")
    plain = doc_at(library, "Notes/plain.md")
    library.observe(plain, "read", {"threadId": "thread-1", "projectRoot": REPO})
    organization = {
        "id": "org-1",
        "config": {"name": "Lab", "slug": "lab", "projects": []},
        "runtime": {
            "tasks": [{"id": "task-9", "number": 9, "title": "Use cosine notes", "claim": ""}],
            "knowledge": {
                "k1": {
                    "id": "k1",
                    "title": "Plain",
                    "summary": "",
                    "body": "Cosine similarity basics.",
                    "version": 1,
                    "source": {"path": str(root / "Notes/plain.md")},
                }
            },
            "links": {
                "l1": {"id": "l1", "kind": "read", "from": "ticket:task-9", "to": "kb:k1"},
            },
            "claims": {
                "c1": {
                    "id": "c1",
                    "taskId": "task-9",
                    "text": "Uses the cosine basics",
                    "evidence": {"kind": "quote", "entryId": "k1", "quote": "Cosine similarity"},
                }
            },
        },
    }
    library.organization_source = lambda: [organization]
    result = library.connections(plain["id"])
    assert result["links"]["citedBy"] == [vectors["id"]]
    kinds = {(item["kind"], item["basis"]) for item in result["usedBy"]}
    assert ("thread", "observed") in kinds
    assert ("ticket", "observed") in kinds and ("claim", "asserted") in kinds
    ticket = next(item for item in result["usedBy"] if item["kind"] == "ticket")
    assert ticket["ticket"] == "LAB-9" and ticket["organization"] == "Lab"
    claim = next(item for item in result["usedBy"] if item["kind"] == "claim")
    assert claim["state"] == "ok"
    assert all(item["basis"] == "words" for item in result["similar"])
    # A broken organization store never hides the note's own connections.
    library.organization_source = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    assert library.connections(plain["id"])["links"]["citedBy"] == [vectors["id"]]


def test_external_task_report_names_the_task_thread_and_model(tmp_path):
    record = {
        "id": "report-1",
        # A report saved without its extension still matches the .html document.
        "artifactPath": "Reports/x",
        "external": True,
        "generatedAt": "2026-11-20T09:00:00Z",
        "provenance": {
            "savedVia": "rem_task",
            "basis": "receipt",
            "taskTitle": "Audit search",
            "taskId": "task-1",
            "threadId": "thread-1",
            "projectRoot": REPO,
            "provider": "claude_code",
            "model": "claude-opus-5-5",
            "at": "2026-11-20T10:00:00Z",
            "hashMatches": True,
        },
    }
    result = brain_connections.connections(
        fake_library(tmp_path, note(), reports=[record]), "doc-1"
    )
    assert result["madeBy"] == [
        {
            "kind": "rem_task",
            "basis": "receipt",
            "reportId": "report-1",
            "title": "Audit search",
            "taskId": "task-1",
            "threadId": "thread-1",
            "projectRoot": REPO,
            "provider": "claude_code",
            "model": "claude-opus-5-5",
            "at": "2026-11-20T10:00:00Z",
            "hashMatches": True,
        }
    ]
    # With no provenance written, it counts as an observed Rem thread report.
    bare = {**record, "provenance": None} | {"artifactPath": "Reports/x.html"}
    made = brain_connections.connections(fake_library(tmp_path, note(), reports=[bare]), "doc-1")
    assert made["madeBy"] == [
        {
            "kind": "rem_thread",
            "basis": "observed",
            "reportId": "report-1",
            "title": "",
            "taskId": "",
            "threadId": "",
            "projectRoot": "",
            "provider": "",
            "model": "",
            "at": "2026-11-20T09:00:00Z",
            "hashMatches": None,
        }
    ]


def test_brain_report_names_its_question_and_reported_model_and_cites_count(tmp_path):
    made = {
        "id": "report-2",
        "artifactPath": "Reports/x.html",
        "question": "How does search rank results?",
        "provider": "claude_code",
        "model": "opus",
        "modelReported": "claude-opus-5-5",
        "generatedAt": "2026-11-21T08:00:00Z",
    }
    citing = {
        "id": "report-3",
        "artifactPath": "Reports/other.html",
        "title": "Ranking review",
        "generatedAt": "2026-11-22T08:00:00Z",
        "citations": [
            {"docId": "doc-1", "state": "found"},
            {"docId": "doc-1", "state": "changed"},
            {"docId": "doc-1"},
            {"docId": "doc-2", "state": "changed"},
        ],
    }
    library = fake_library(tmp_path, note(), reports=[made, citing])
    result = brain_connections.connections(library, "doc-1")
    assert result["madeBy"] == [
        {
            "kind": "brain_report",
            "basis": "observed",
            "reportId": "report-2",
            "title": "How does search rank results?",
            "provider": "claude_code",
            "model": "claude-opus-5-5",
            "at": "2026-11-21T08:00:00Z",
        }
    ]
    assert result["usedBy"] == [
        {
            "kind": "report",
            "basis": "cited",
            "reportId": "report-3",
            "title": "Ranking review",
            "count": 3,
            "changed": 1,
            "at": "2026-11-22T08:00:00Z",
        }
    ]
    # Without a reported model, the requested one is named.
    del made["modelReported"]
    assert brain_connections.connections(library, "doc-1")["madeBy"][0]["model"] == "opus"


def test_organization_field_is_written_unless_an_organization_made_the_note(tmp_path):
    doc = note(organization="Lab")
    written = brain_connections.connections(fake_library(tmp_path, doc), "doc-1")
    assert written["madeBy"] == [
        {"kind": "organization", "basis": "written", "organization": "Lab"}
    ]
    assert brain_connections.connections(fake_library(tmp_path, note()), "doc-1")["madeBy"] == []
    mirrored = {
        "id": "org-1",
        "config": {"name": "Lab", "slug": "lab"},
        "runtime": {
            "knowledge": {
                "k1": {
                    "id": "k1",
                    "author": "Ada",
                    "createdAt": "2026-11-20T10:00:00Z",
                    "mirror": {"path": str(tmp_path / "Reports/x.html")},
                }
            }
        },
    }
    library = fake_library(tmp_path, doc, organizations=[mirrored])
    observed = brain_connections.connections(library, "doc-1")["madeBy"]
    assert observed == [
        {
            "kind": "organization",
            "basis": "observed",
            "organization": "Lab",
            "organizationId": "org-1",
            "entryId": "k1",
            "author": "Ada",
            "ticket": "",
            "at": "2026-11-20T10:00:00Z",
        }
    ]


def similar_to(*doc_ids: str, revision: str = "") -> list[dict[str, Any]]:
    return [{"docId": doc_id, "revision": False} for doc_id in doc_ids] + (
        [{"docId": revision, "revision": True}] if revision else []
    )


def test_project_is_suggested_when_two_similar_documents_share_it(tmp_path):
    shared = {"projectId": "project-1", "basis": "folder"}
    assignments = {"a": shared, "b": shared, "c": shared}
    # The revision of the same note doesn't vote, so two of the three agree.
    library = fake_library(
        tmp_path, note(), assignments=assignments, similar=similar_to("a", "b", revision="c")
    )
    result = brain_connections.connections(library, "doc-1")
    assert result["project"] is None
    assert result["suggestedProject"] == {
        "projectId": "project-1",
        "name": "Raticode",
        "basis": "suggested",
        "detail": "2 of the 3 most similar documents",
    }
    one_vote = fake_library(
        tmp_path, note(), assignments=assignments, similar=similar_to("a", "z", revision="c")
    )
    assert brain_connections.connections(one_vote, "doc-1")["suggestedProject"] is None
    # A note with its own project gets no suggestion.
    assigned = fake_library(
        tmp_path,
        note(),
        assignments={**assignments, "doc-1": {"projectId": "project-1", "basis": "written"}},
        similar=similar_to("a", "b"),
    )
    own = brain_connections.connections(assigned, "doc-1")
    assert own["project"] == {"projectId": "project-1", "basis": "written", "name": "Raticode"}
    assert own["suggestedProject"] is None


def test_observed_read_is_marked_older_after_the_note_changes(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    plain = doc_at(library, "Notes/plain.md")
    library.observe(plain, "read", {"threadId": "thread-1", "projectRoot": REPO})
    reads = [i for i in library.connections(plain["id"])["usedBy"] if i["kind"] == "thread"]
    assert [(r["title"], r["olderVersion"]) for r in reads] == [
        ("Organizations audit thread", False)
    ]
    write(root, "Notes/plain.md", "# Plain\n\nCosine similarity basics, rewritten.\n")
    library.scan(brain["id"])
    changed = doc_at(library, "Notes/plain.md")
    assert changed["id"] == plain["id"] and changed["version"] != plain["version"]
    reads = [i for i in library.connections(plain["id"])["usedBy"] if i["kind"] == "thread"]
    assert [(r["threadId"], r["olderVersion"]) for r in reads] == [("thread-1", True)]


def test_topic_is_named_and_left_out_when_topics_fail(tmp_path):
    def found(brain_id: str) -> dict[str, Any]:
        return {
            "topicOf": {"doc-1": "topic-1"},
            "topics": [
                {"id": "topic-0", "label": "Other", "size": 4},
                {"id": "topic-1", "label": "Search", "size": 3, "members": ["doc-1"]},
            ],
        }

    library = fake_library(tmp_path, note(), topics=found)
    topic = brain_connections.connections(library, "doc-1")["topic"]
    assert topic == {"id": "topic-1", "label": "Search", "size": 3}

    def broken(brain_id: str) -> dict[str, Any]:
        raise ValueError("Too few documents for topics")

    library = fake_library(tmp_path, note(), topics=broken)
    result = brain_connections.connections(library, "doc-1")
    assert result["topic"] is None and result["docId"] == "doc-1"


def test_topics_reuse_the_cache_and_named_topics_follow_their_documents(
    library, tmp_path, monkeypatch
):
    brain = topic_brain(library, tmp_path)
    calls: list[list[str]] = []
    compute = brain_graph.compute_topics

    def counted(documents, ids, *args):
        calls.append(list(ids))
        return compute(documents, ids, *args)

    monkeypatch.setattr(brain_graph, "compute_topics", counted)
    first = library.topics(brain["id"])
    assert library.topics(brain["id"]) == first
    assert len(calls) == 1
    # A name saved on half of a topic takes on all of the recomputed topic's documents.
    topic = first["topics"][0]
    library.put(
        "topics",
        {
            "id": topic["id"],
            "brainId": brain["id"],
            "name": "Mine",
            "members": topic["members"][:2],
        },
    )
    saved: list[dict[str, Any]] = []
    put = library.put

    def spy(kind: str, item: dict[str, Any]) -> None:
        saved.append(item)
        put(kind, item)

    monkeypatch.setattr(library, "put", spy)
    second = library.topics(brain["id"])
    assert len(calls) == 2
    assert [(item["id"], item["name"]) for item in saved] == [(topic["id"], "Mine")]
    stored = library.entity("topics", topic["id"])
    assert sorted(stored["members"]) == sorted(topic["members"])
    assert next(t for t in second["topics"] if t["id"] == topic["id"])["label"] == "Mine"
    # The cache key includes the updated name, so the next read reuses it.
    assert library.topics(brain["id"]) == second
    assert len(calls) == 2


def test_topic_layout_refuses_a_topic_that_is_gone(tmp_path):
    library = SimpleNamespace(topics=lambda brain_id: {"topics": [{"id": "topic-1"}]})
    with pytest.raises(ValueError, match=r"^That topic changed\. Reopen the map\.$"):
        brain_connections.topic_layout(library, BRAIN, "topic-2")


def test_organizations_are_empty_without_a_source_or_when_it_fails(caplog):
    assert brain_connections.read_organizations(SimpleNamespace(organization_source=None)) == []
    org = {"id": "org-1"}
    working = SimpleNamespace(organization_source=lambda: (item for item in [org]))
    assert brain_connections.read_organizations(working) == [org]

    def down() -> list[dict[str, Any]]:
        raise RuntimeError("organization store locked")

    with caplog.at_level("ERROR", logger="gofer.ui.brain_connections"):
        assert brain_connections.read_organizations(SimpleNamespace(organization_source=down)) == []
    assert "Could not read organizations" in caplog.text
