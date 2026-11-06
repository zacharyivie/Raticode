"""Projects, provenance, connections, combined search and topics in the Second Brain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_documents import extract
from gofer.ui.brain_graph import (
    folder_project,
    project_key,
    project_root,
    root_name,
    windows,
)
from gofer.ui.brain_library import BrainLibrary

REPO = "/work/Raticode/Raticode"


def html(title: str, body: str, meta: str = "") -> str:
    return f"<html><head><title>{title}</title>{meta}</head><body><p>{body}</p></body></html>"


def wait_tasks(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def receipt_store(data_dir: Path, artifacts: list[dict[str, Any]], project_dir: str) -> None:
    db = sqlite3.connect(data_dir / "rem-home.sqlite3")
    db.executescript(
        "CREATE TABLE runs(id TEXT, task TEXT, occurrence TEXT, state TEXT, body TEXT);"
        "CREATE TABLE tasks(id TEXT, body TEXT);"
    )
    db.execute("INSERT INTO tasks VALUES('task-1', ?)", (json.dumps({"title": "Audit search"}),))
    db.execute(
        "INSERT INTO runs VALUES('run-1','task-1','','succeeded',?)",
        (
            json.dumps(
                {
                    "id": "run-1",
                    "task_id": "task-1",
                    "thread_id": "thread-task",
                    "recipe": {"type": "research", "project_dir": project_dir},
                    "config": {"provider": "claude_code", "model": "claude-opus-5-5"},
                    "finished_at": "2026-11-05T10:00:00+00:00",
                    "artifacts": artifacts,
                }
            ),
        ),
    )
    db.commit()
    db.close()


def build_brain(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "brain"
    write(
        root, "Reports/Raticode/org-audit.html", html("Organizations audit", "Tickets approvals.")
    )
    write(
        root, "Reports/Taskurotta/release.html", html("Release handbook", "Signing notarization.")
    )
    write(
        root,
        "reports/rem/Reports/Raticode/search.html",
        html("Search audit", "Vector search repair notes."),
    )
    write(root, "reports/rem/youtube/digest.md", "# Digest\n\nVideos about embeddings.\n")
    write(
        root,
        "Notes/vectors.md",
        "---\nproject: Agent Lab\n---\n# Vectors\n\nSee [[Notes/plain]] for cosine notes.\n",
    )
    write(root, "Notes/plain.md", "# Plain\n\nCosine similarity basics.\n")
    write(
        root, "Notes/meta.html", html("Meta", "Body.", '<meta name="project" content="Raticode">')
    )
    write(root, "Inbox/loose.md", "# Loose\n\nUnfiled idea.\n")
    archive = root / "Rem Conversations"
    write(archive, "README.raticode.md", "# Rem conversation archive\n")
    snapshot = {
        "messages": [{"body": f"Saved [link](<{root}/Reports/Raticode/org-audit.html>)"}],
    }
    write(archive, "threads/abc.json", json.dumps(snapshot))
    write(
        archive,
        "index.json",
        json.dumps(
            {
                "version": 1,
                "threads": {
                    "abc": {
                        "threadId": "thread-1",
                        "title": "Organizations audit thread",
                        "projectRoot": f"{REPO}/.worktrees/org-fix",
                        "updatedAt": "2026-11-04T10:00:00Z",
                        "messageCount": 4,
                        "snapshot": "threads/abc.json",
                    }
                },
            }
        ),
    )
    brain = library.open({"root": str(root)})
    wait_tasks(library)
    return library.brain(brain["id"]), root


def doc_at(library: BrainLibrary, path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents") if doc["path"] == path)


def test_project_names_roots_and_report_folders():
    assert project_key("Agent Evolution Trading") == project_key("agent-evolution-trading")
    assert project_root(f"{REPO}/.worktrees/connected-second-brain") == REPO
    assert root_name(REPO) == "Raticode"
    assert root_name("/p/Raticode Mobile/raticode-mobile") == "Raticode Mobile"
    assert root_name("/p/agent-evolution-trading") == "agent-evolution-trading"
    assert folder_project("Reports/Raticode/x.html") == "Raticode"
    assert folder_project("reports/rem/Reports/Raticode/x.html") == "Raticode"
    assert folder_project("reports/rem/raticode/x.md") == "raticode"
    assert folder_project("Second Brain/Reports/Taskurotta/x.html") == "Taskurotta"
    assert folder_project("Reports/loose.html") == ""
    assert folder_project("Notes/x.md") == ""


def test_html_meta_names_project_and_keywords():
    value = extract(
        b'<html><head><meta name="project" content="Raticode">'
        b'<meta name="keywords" content="semantic search, topics"></head><body>x</body></html>',
        ".html",
    )
    assert value["project"] == "Raticode"
    assert value["tags"] == ["semantic search", "topics"]


def test_windows_group_short_passages_and_keep_block_ids():
    doc = {
        "title": "T",
        "blocks": [{"id": f"b{i}", "text": "word " * 40} for i in range(12)],
    }
    parts = windows(doc)
    assert len(parts) < 12
    assert [block for _, blocks, _ in parts for block in blocks] == [f"b{i}" for i in range(12)]
    assert all(text.startswith("T\n") for _, _, text in parts)


def test_projects_join_folders_aliases_roots_and_explain_each_assignment(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    library.put(
        "projects",
        {
            "id": "project-raticode",
            "brainId": brain["id"],
            "name": "Raticode",
            "aliases": ["Taskurotta"],
            "roots": [REPO],
        },
    )
    result = library.projects(brain["id"])
    by_name = {project["name"]: project for project in result["projects"]}
    assert "Taskurotta" not in by_name and "raticode" not in by_name
    raticode = by_name["Raticode"]
    assignments = result["assignments"]
    release = assignments[doc_at(library, "Reports/Taskurotta/release.html")["id"]]
    assert release["projectId"] == raticode["id"] and release["basis"] == "folder"
    meta = assignments[doc_at(library, "Notes/meta.html")["id"]]
    assert meta["projectId"] == raticode["id"] and meta["basis"] == "written"
    lab = assignments[doc_at(library, "Notes/vectors.md")["id"]]
    assert by_name["Agent Lab"]["id"] == lab["projectId"] and lab["basis"] == "written"
    assert doc_at(library, "Inbox/loose.md")["id"] in result["unassigned"]
    # The archived thread ran in a worktree of the Raticode repository.
    thread = next(t for t in result["conversations"] if t["threadId"] == "thread-1")
    assert thread["projectId"] == raticode["id"]
    assert raticode["counts"]["conversations"] == 1
    assert raticode["counts"]["reports"] >= 3
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert {k: v for k, v in after.items() if ".raticode" not in str(k)} == {
        k: v for k, v in before.items() if ".raticode" not in str(k)
    }


def test_backfill_creates_task_records_once_and_never_edits_files(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    target = root / "reports/rem/Reports/Raticode/search.html"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt_store(
        tmp_path / "app",
        [
            {"path": str(target), "sha256": digest, "epoch": 0},
            {"path": str(root / "reports/rem/gone.html"), "sha256": "0" * 64, "epoch": 0},
            {"path": str(root / "Reports/Raticode/x.html"), "sha256": "1", "superseded": True},
        ],
        REPO,
    )
    original = target.read_bytes()
    first = library.backfill_provenance(brain["id"], conversations=True)
    assert first["created"] == 1 and first["skipped"] == 1
    record = next(
        r for r in library.entities("reports") if r["artifactPath"].endswith("search.html")
    )
    provenance = record["provenance"]
    assert provenance["savedVia"] == "rem_task" and provenance["hashMatches"] is True
    assert provenance["taskTitle"] == "Audit search" and provenance["projectRoot"] == REPO
    assert record["external"] and record["provider"] == "Rem task"
    assert doc_at(library, record["artifactPath"])["origin"] == "agent"
    again = library.backfill_provenance(brain["id"], conversations=True)
    assert again["created"] == 0 and again["updated"] == 0
    assert target.read_bytes() == original
    projects = library.projects(brain["id"])
    assignment = projects["assignments"][doc_at(library, record["artifactPath"])["id"]]
    assert assignment["basis"] == "observed" and "Audit search" in assignment["detail"]


def test_backfill_links_thread_reports_from_the_conversation_archive(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    doc = doc_at(library, "Reports/Raticode/org-audit.html")
    library.put(
        "reports",
        library._artifact_record(brain, doc["path"], doc["title"], {}) | {"provenance": {}},
    )
    result = library.backfill_provenance(brain["id"], conversations=True)
    assert result["updated"] == 1
    record = next(r for r in library.entities("reports") if r["artifactPath"] == doc["path"])
    assert record["provenance"]["threadId"] == "thread-1"
    assert record["provenance"]["basis"] == "conversation"


def test_record_artifact_registers_live_task_reports(library, tmp_path):
    brain, root = build_brain(library, tmp_path)
    path = write(root, "reports/rem/Reports/Raticode/new.html", html("New finding", "Body"))
    record = library.record_artifact(
        path, {"savedVia": "rem_task", "taskTitle": "Find things", "projectRoot": REPO}
    )
    assert record and record["title"] == "New finding" and record["external"]
    assert library.record_artifact(path, {"savedVia": "rem_task"})["id"] == record["id"]
    assert library.record_artifact(tmp_path / "elsewhere.html", {}) is None


def test_combined_search_fuses_document_ranks(library, tmp_path, monkeypatch):
    root = tmp_path / "brain"
    write(root, "a.md", "# A\n\nalpha appears here.\n")
    write(root, "b.md", "# B\n\nsomething unrelated in words.\n")
    write(root, "c.md", "# C\n\nalpha and the meaning we want.\n")
    brain = library.open({"root": str(root)})
    wait_tasks(library)
    current = library.brain(brain["id"])
    current["settings"]["semantic"] = True
    library.put_brain(current)

    def vector(text: str) -> list[float]:
        return [1.0, 0.0] if ("meaning" in text or "unrelated" in text) else [0.0, 1.0]

    monkeypatch.setattr(library, "_embed_many", lambda texts, *a: [vector(t) for t in texts])
    monkeypatch.setattr(library, "_embedding", lambda *a, **k: [1.0, 0.0])
    library.embed(brain["id"], threading.Event())
    with library.connect() as db:
        stored = db.execute("SELECT dim, length(data) FROM embeddings LIMIT 1").fetchone()
    assert tuple(stored) == (2, 8)
    titles = {doc["id"]: doc["title"] for doc in library.entities("documents")}
    hybrid = library.search({"brainId": brain["id"], "query": "alpha", "mode": "hybrid"})["hits"]
    assert titles[hybrid[0]["docId"]] == "C"
    assert hybrid[0]["fusedFrom"] == ["content", "meaning"]
    assert {titles[hit["docId"]] for hit in hybrid} == {"A", "B", "C"}
    semantic = library.search({"brainId": brain["id"], "query": "alpha", "mode": "semantic"})
    assert {titles[hit["docId"]] for hit in semantic["hits"]} == {"B", "C"}
    exact = library.search({"brainId": brain["id"], "query": "alpha", "mode": "exact"})
    assert {titles[hit["docId"]] for hit in exact["hits"]} == {"A", "C"}


def topic_brain(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "topics"
    cooking = "pasta tomato sauce basil garlic simmer noodles kitchen recipe"
    vectors = "vector embedding cosine index retrieval search ranking corpus"
    for index in range(4):
        write(root, f"cook/c{index}.md", f"# Cooking {index}\n\n{cooking} variant{index}\n")
        write(root, f"search/s{index}.md", f"# Search {index}\n\n{vectors} variant{index}\n")
    brain = library.open({"root": str(root)})
    wait_tasks(library)
    return brain


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


def test_project_and_topic_records_are_validated(library, tmp_path):
    brain, _ = build_brain(library, tmp_path)
    saved = library.mutate(
        {
            "kind": "projects",
            "id": "project-1",
            "item": {
                "brainId": brain["id"],
                "name": "Raticode",
                "aliases": ["Taskurotta", "Raticode", " "],
                "roots": [REPO + "/"],
            },
        }
    )
    stored = library.entity("projects", "project-1")
    assert saved is not None and stored["aliases"] == ["Taskurotta"] and stored["roots"] == [REPO]
    with pytest.raises(ValueError, match="absolute"):
        library.mutate(
            {
                "kind": "projects",
                "id": "project-2",
                "item": {"brainId": brain["id"], "name": "X", "roots": ["relative"]},
            }
        )
    with pytest.raises(ValueError, match="Name the topic"):
        library.mutate({"kind": "topics", "id": "t", "item": {"brainId": brain["id"], "name": ""}})


def test_rem_thread_tools_record_reads_use_combined_search_and_save_history(
    library, tmp_path, monkeypatch
):
    from gofer.ui import second_brain

    brain, root = build_brain(library, tmp_path)
    current = library.brain(brain["id"])
    current["settings"].update(allowCloud=True, semantic=True)
    library.put_brain(current)
    plain = doc_at(library, "Notes/plain.md")
    searched: list[str] = []
    original = BrainLibrary.search

    def spy(self, params):
        searched.append(params.get("mode", "exact"))
        return original(self, {**params, "mode": "exact"})

    monkeypatch.setattr(BrainLibrary, "search", spy)

    def serve(call, definitions, rules, name):
        call("search", {"query": "cosine"})
        call("read_note", {"documentId": plain["id"]})
        call("save_note", {"path": "Reports/Raticode/thread.md", "content": "# Thread\n\nBody."})

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(
        tmp_path / "app",
        [brain["id"]],
        "md",
        "auto",
        {"threadId": "thread-7", "projectRoot": REPO, "provider": "codex", "model": "gpt"},
    )
    assert searched == ["hybrid"]
    used = library.connections(plain["id"])["usedBy"]
    assert [(item["kind"], item["basis"], item["threadId"]) for item in used] == [
        ("thread", "observed", "thread-7")
    ]
    record = next(r for r in library.entities("reports") if r["artifactPath"].endswith("thread.md"))
    assert record["provenance"]["threadId"] == "thread-7"
    assert record["provenance"]["projectRoot"] == REPO and record["model"] == "gpt"


def test_task_report_hook_registers_records_without_failing_the_save(library, tmp_path):
    from types import SimpleNamespace

    from gofer.ui.rem_home_artifacts import record_artifact

    brain, root = build_brain(library, tmp_path)
    path = write(root, "reports/rem/Reports/Raticode/hook.html", html("Hook report", "Body"))
    run = {
        "id": "run-5",
        "task_id": "task-5",
        "thread_id": "thread-5",
        "recipe": {"title": "Check hooks", "project_dir": REPO},
        "config": {"provider": "claude_code", "model": "claude-opus-5-5"},
    }
    record_artifact(
        SimpleNamespace(brain_library=library, data_dir=tmp_path / "app"), run, path, "x"
    )
    record = next(r for r in library.entities("reports") if r["artifactPath"].endswith("hook.html"))
    assert record["provenance"]["taskTitle"] == "Check hooks" and record["title"] == "Hook report"

    class Broken:
        def record_artifact(self, *args):
            raise RuntimeError("database locked")

    # A failure to register is logged; the report itself stays saved.
    record_artifact(SimpleNamespace(brain_library=Broken(), data_dir=tmp_path), run, path, "x")


def test_linked_worktrees_resolve_to_their_repository(tmp_path):
    repo = tmp_path / "Raticode"
    worktree = tmp_path / "browser-update"
    (repo / ".git/worktrees/browser-update").mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {repo}/.git/worktrees/browser-update\n")
    assert project_root(str(worktree)) == str(repo)
    assert project_root(str(tmp_path / "plain")) == str(tmp_path / "plain")


def test_brains_with_meaning_on_but_no_vectors_reembed_once(library, tmp_path):
    brain = topic_brain(library, tmp_path)
    current = library.brain(brain["id"])
    assert library._needs_embedding(current) is False  # related meaning is off
    library._embedding_checked.clear()
    current["settings"]["semantic"] = True
    library.put_brain(current)
    assert library._needs_embedding(library.brain(brain["id"])) is True
    assert library._needs_embedding(library.brain(brain["id"])) is False
