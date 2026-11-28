"""Second Brain connections: projects, provenance, similar documents and topics.

Each function takes the BrainLibrary first and reaches its state and other methods
through it at call time, so patches on the library keep working.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_library import encode

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary

log = logging.getLogger(__name__)


def similar_documents(library: BrainLibrary, doc: dict[str, Any]) -> list[dict[str, Any]]:
    from gofer.ui.brain_graph import similar

    vectors = library._doc_vectors(doc["brainId"])
    paths = {item["id"]: item["path"] for item in library.entities("documents", doc["brainId"])}
    found = similar(doc["id"], vectors["ids"], vectors["matrix"], paths, vectors["threshold"])
    return [{**item, "basis": vectors["basis"]} for item in found]


def projects(library: BrainLibrary, brain_id: str) -> dict[str, Any]:
    from gofer.ui.brain_graph import conversations, resolve_projects

    brain = library.brain(brain_id)
    library._maybe_backfill(brain_id)
    return resolve_projects(
        brain,
        library.entities("documents", brain_id),
        library.entities("reports", brain_id),
        library.entities("projects", brain_id),
        conversations(Path(brain["root"])),
    )


def read_organizations(library: BrainLibrary) -> list[dict[str, Any]]:
    if library.organization_source is None:
        return []
    try:
        return list(library.organization_source())
    except Exception:  # noqa: BLE001 - organization trouble must not hide knowledge
        log.exception("Could not read organizations for the Second Brain")
        return []


def connections(library: BrainLibrary, doc_id: str) -> dict[str, Any]:
    """Everything known about one document, each relationship labelled with its basis."""
    from gofer.ui.brain_graph import organization_references

    doc = library.entity("documents", doc_id)
    brain_id = doc["brainId"]
    brain = library.brain(brain_id)
    documents = {item["id"]: item for item in library.entities("documents", brain_id)}
    reports = library.entities("reports", brain_id)
    record = next(
        (
            report
            for report in reports
            if doc["path"] in {report["artifactPath"], report["artifactPath"] + ".html"}
        ),
        None,
    )
    projects = library.projects(brain_id)
    names = {project["id"]: project["name"] for project in projects["projects"]}
    threads = {thread["threadId"]: thread for thread in projects["conversations"]}
    made_by: list[dict[str, Any]] = []
    if record is not None:
        provenance = dict(record.get("provenance") or {})
        if record.get("external"):
            made_by.append(
                {
                    "kind": provenance.get("savedVia") or "rem_thread",
                    "basis": provenance.get("basis") or "observed",
                    "reportId": record["id"],
                    "title": provenance.get("taskTitle") or provenance.get("threadTitle", ""),
                    "taskId": provenance.get("taskId", ""),
                    "threadId": provenance.get("threadId", ""),
                    "projectRoot": provenance.get("projectRoot", ""),
                    "provider": provenance.get("provider", ""),
                    "model": provenance.get("model", ""),
                    "at": provenance.get("at") or record.get("generatedAt", ""),
                    "hashMatches": provenance.get("hashMatches"),
                }
            )
        else:
            made_by.append(
                {
                    "kind": "brain_report",
                    "basis": "observed",
                    "reportId": record["id"],
                    "title": record.get("question", ""),
                    "provider": record.get("provider", ""),
                    "model": record.get("modelReported") or record.get("model", ""),
                    "at": record.get("generatedAt", ""),
                }
            )
    organizations = organization_references(library._organizations(), Path(brain["root"]))
    if doc["path"] in organizations["made"]:
        made_by.append(
            {"kind": "organization", "basis": "observed", **organizations["made"][doc["path"]]}
        )
    elif doc.get("organization"):
        made_by.append(
            {"kind": "organization", "basis": "written", "organization": doc["organization"]}
        )
    used_by: list[dict[str, Any]] = []
    for report in reports:
        cited = [c for c in report.get("citations", []) if c.get("docId") == doc_id]
        if cited:
            used_by.append(
                {
                    "kind": "report",
                    "basis": "cited",
                    "reportId": report["id"],
                    "title": report.get("title", ""),
                    "count": len(cited),
                    "changed": sum(c.get("state") not in {None, "found"} for c in cited),
                    "at": report.get("generatedAt", ""),
                }
            )
    with library.connect() as db:
        observed = [
            json.loads(row[0]) | {"version": row[1], "at": row[2]}
            for row in db.execute(
                "SELECT body, version, at FROM observations WHERE brain=? AND doc=?"
                " ORDER BY at DESC LIMIT 50",
                (brain_id, doc_id),
            )
        ]
    for item in observed:
        thread = threads.get(item.get("threadId", ""), {})
        used_by.append(
            {
                "kind": "thread",
                "basis": "observed",
                "relation": "read",
                "threadId": item.get("threadId", ""),
                "title": thread.get("title") or "Rem thread",
                "projectRoot": item.get("projectRoot", ""),
                "at": item["at"],
                "olderVersion": item["version"] != doc["version"],
            }
        )
    used_by.extend(organizations["used"].get(doc["path"], []))
    similar_items = library._similar(doc)
    assignment = projects["assignments"].get(doc_id)
    project = {**assignment, "name": names.get(assignment["projectId"], "")} if assignment else None
    suggested = None
    if project is None:
        votes = Counter(
            projects["assignments"][item["docId"]]["projectId"]
            for item in similar_items
            if not item["revision"] and item["docId"] in projects["assignments"]
        )
        if votes:
            pid, count = votes.most_common(1)[0]
            if count >= 2:
                suggested = {
                    "projectId": pid,
                    "name": names.get(pid, ""),
                    "basis": "suggested",
                    "detail": f"{count} of the {len(similar_items)} most similar documents",
                }
    topic = None
    try:
        topics = library.topics(brain_id)
        topic_id = topics["topicOf"].get(doc_id)
        topic = next(
            (
                {"id": t["id"], "label": t["label"], "size": t["size"]}
                for t in topics["topics"]
                if t["id"] == topic_id
            ),
            None,
        )
    except (ValueError, ImportError):
        topic = None
    cited_by = sorted(item["id"] for item in documents.values() if doc_id in item.get("cites", []))
    return {
        "docId": doc_id,
        "madeBy": made_by,
        "project": project,
        "suggestedProject": suggested,
        "links": {"cites": list(doc.get("cites", [])), "citedBy": cited_by},
        "usedBy": used_by,
        "similar": similar_items,
        "topic": topic,
    }


def topics(library: BrainLibrary, brain_id: str) -> dict[str, Any]:
    from gofer.ui.brain_graph import compute_topics

    vectors = library._doc_vectors(brain_id)
    named = library.entities("topics", brain_id)
    key = (vectors["key"], json.dumps(named, sort_keys=True))
    cached = library._topic_cache.get(brain_id)
    if cached and cached["key"] == key:
        return dict(cached["result"])
    documents = {doc["id"]: doc for doc in library.entities("documents", brain_id)}
    with library.connect() as db:
        row = db.execute("SELECT body FROM topic_state WHERE brain=?", (brain_id,)).fetchone()
    previous = json.loads(row[0]).get("topics", []) if row else []
    computed = compute_topics(
        [documents[doc_id] for doc_id in vectors["ids"]],
        vectors["ids"],
        vectors["matrix"],
        vectors["threshold"],
        previous,
        named,
    )
    assignments = library.projects(brain_id)["assignments"]
    for topic in computed["topics"]:
        tally = Counter(
            assignments[doc_id]["projectId"] for doc_id in topic["members"] if doc_id in assignments
        )
        topic["projects"] = [
            {"projectId": pid, "count": count} for pid, count in tally.most_common(3)
        ]
        pinned = next((item for item in named if item["id"] == topic["id"]), None)
        if pinned and set(pinned.get("members", [])) != set(topic["members"]):
            # A named topic follows its documents as the brain grows.
            library.put("topics", {**pinned, "members": topic["members"]})
    with library.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO topic_state VALUES(?,?)",
            (
                brain_id,
                encode(
                    {
                        "topics": [
                            {"id": t["id"], "members": t["members"]} for t in computed["topics"]
                        ]
                    }
                ),
            ),
        )
    result = {**computed, "basis": vectors["basis"], "documents": len(vectors["ids"])}
    key = (vectors["key"], json.dumps(library.entities("topics", brain_id), sort_keys=True))
    library._topic_cache[brain_id] = {"key": key, "result": result}
    return dict(result)


def topic_layout(library: BrainLibrary, brain_id: str, topic_id: str) -> dict[str, Any]:
    from gofer.ui.brain_graph import topic_layout

    topics = library.topics(brain_id)
    topic = next((item for item in topics["topics"] if item["id"] == topic_id), None)
    if topic is None:
        raise ValueError("That topic changed. Reopen the map.")
    vectors = library._doc_vectors(brain_id)
    documents = {doc["id"]: doc for doc in library.entities("documents", brain_id)}
    return {
        "topicId": topic_id,
        **topic_layout(topic["members"], vectors["ids"], vectors["matrix"], documents),
    }
