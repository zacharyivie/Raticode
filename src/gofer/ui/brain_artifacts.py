"""Second Brain report records for files Rem saves, queued reports and provenance.

Each function takes the BrainLibrary first and reaches its state and other methods
through it at call time, so patches on the library keep working.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_documents import extract, read_source
from gofer.ui.brain_library import encode, identifier, now, pending_reports

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary

log = logging.getLogger(__name__)


def artifact_record(
    library: BrainLibrary,
    brain: dict[str, Any],
    relative: str,
    title: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    suffix = Path(relative).suffix.lower().lstrip(".")
    return {
        "id": identifier("report"),
        "brainId": brain["id"],
        "title": title or Path(relative).stem,
        "question": provenance.get("taskTitle") or "Saved by a Rem task",
        "artifactPath": relative,
        "generatedAt": provenance.get("at") or now(),
        "format": {"htm": "html", "markdown": "md"}.get(suffix, suffix or "html"),
        "provider": "Rem task" if provenance.get("savedVia") == "rem_task" else "Rem thread",
        "model": provenance.get("model") or "Not reported",
        "modelReported": "Not reported",
        "theme": "Authored styling",
        "template": "Saved artifact",
        "scope": "Task resources",
        "coverage": "Not reported",
        "filters": "Not reported",
        "retrievalGeneration": brain["index"]["generation"],
        "sourceVersions": [],
        "citations": [],
        "claims": [],
        "external": True,
        "draft": False,
        "outputHash": "sha256:" + str(provenance.get("sha256") or ""),
        "provenance": provenance,
    }


def describe_artifact(
    record: dict[str, Any],
    title: str,
    question: str,
    citations: list[dict[str, Any]] | None,
) -> None:
    """Put the title, question and checked citations a save call gave on its record."""
    if title:
        record["title"] = title[:300]
    if question:
        record["question"] = question[:2000]
    if citations:
        record["citations"] = citations
        record["sourceVersions"] = list(
            {
                (c["docId"], c["version"]): {"docId": c["docId"], "version": c["version"]}
                for c in citations
            }.values()
        )


def record_artifact(
    library: BrainLibrary,
    path: Path,
    provenance: dict[str, Any],
    *,
    title: str = "",
    question: str = "",
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Register a report a Rem task saved into a registered brain, with how it was made.

    `citations` come from resolve_evidence, already checked against the brains the
    task may read. `title` and `question` replace the ones Raticode would guess.
    """
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    for brain in library.brains():
        try:
            relative = resolved.relative_to(Path(brain["root"]).resolve()).as_posix()
        except (OSError, ValueError):
            continue
        with library.lock:
            existing = next(
                (
                    report
                    for report in library.entities("reports", brain["id"])
                    if report["artifactPath"] == relative
                ),
                None,
            )
            if existing is not None:
                # A Library refresh can register the task's receipt first. Give that
                # record what the save call said, once.
                backfilled = (existing.get("provenance") or {}).get("source") == "receipt"
                described = citations or title or question
                if backfilled and described and not existing["citations"]:
                    library._describe_artifact(existing, title, question, citations)
                    library.put("reports", existing)
                    library._portable(brain["id"])
                return existing
            if not title:
                try:
                    data = read_source(Path(brain["root"]), relative)
                    title = str(extract(data, resolved.suffix.lower()).get("title") or "")
                except (OSError, ValueError):
                    title = ""
            record = library._artifact_record(
                brain,
                relative,
                title,
                {"basis": "observed", "hashMatches": True, "at": now(), **provenance},
            )
            library._describe_artifact(record, title, question, citations)
            library.put("reports", record)
            library._portable(brain["id"])
        return record
    return None


def apply_pending_reports(library: BrainLibrary) -> int:
    """Store the report records save_note queued while the library was busy.

    A record is stored when the file at its artifactPath still has its output hash,
    and its document (once indexed) is labelled as a Rem report. A record whose
    brain or file is gone, or whose file changed since, is dropped. Returns how
    many were stored. A busy database leaves the rest queued for the next check.
    """
    stored = 0
    for path, report in pending_reports(library.folder):
        if library.stop.is_set():
            break
        if report is None:
            log.warning("Dropped an unreadable queued Second Brain report: %s", path.name)
            path.unlink(missing_ok=True)
            continue
        # A database error (busy again) propagates: this and later records stay queued.
        try:
            kept = library._apply_pending_report(report)
        except (OSError, ValueError) as exc:
            log.debug("Kept queued report %s (%s)", report["id"], type(exc).__name__)
            continue
        if kept is not None:
            stored += kept
            path.unlink(missing_ok=True)
    return stored


def apply_pending_report(library: BrainLibrary, report: dict[str, Any]) -> int | None:
    """1 when stored, 0 when dropped, None to try again later."""
    try:
        brain = library.brain(report["brainId"])
    except ValueError:
        return 0
    root = Path(brain["root"])
    if not root.is_dir():
        return None
    try:
        data = read_source(root, report["artifactPath"])
    except (FileNotFoundError, ValueError):
        # Deleted, or no longer a plain file inside the brain.
        return 0
    if "sha256:" + hashlib.sha256(data).hexdigest() != report["outputHash"]:
        log.info("Dropped queued report %s: its file changed since it was saved", report["id"])
        return 0
    with library.lock:
        existing = next(
            (
                item
                for item in library.entities("reports", brain["id"])
                if item["artifactPath"] == report["artifactPath"]
            ),
            None,
        )
        if existing is None:
            library.put("reports", report)
        elif existing.get("outputHash") == report["outputHash"] or existing["citations"]:
            return 0
        else:
            # A refresh registered the file first. Give that record what save_note said.
            library._describe_artifact(
                existing, report["title"], report["question"], report["citations"]
            )
            existing["provenance"] = {
                **(existing.get("provenance") or {}),
                **report["provenance"],
            }
            existing["outputHash"] = report["outputHash"]
            library.put("reports", existing)
        version = report["outputHash"].removeprefix("sha256:")
        for doc in library.documents_at(report["artifactPath"], [brain["id"]]).values():
            if doc["version"] == version and doc.get("origin") != "agent":
                library.edit_document(
                    doc["id"], lambda doc: doc.update(type="report", origin="agent")
                )
        library._portable(brain["id"])
    return 1


def observe(library: BrainLibrary, doc: dict[str, Any], kind: str, actor: dict[str, Any]) -> None:
    """Remember that a Rem thread read a document (one row per thread and document)."""
    key = str(actor.get("threadId") or "")
    if not key:
        return
    with library.connect() as db:
        db.execute(
            "INSERT INTO observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(brain,doc,kind,actor)"
            " DO UPDATE SET version=excluded.version, body=excluded.body, at=excluded.at",
            (doc["brainId"], doc["id"], doc["version"], kind, key, encode(actor), now()),
        )


def maybe_backfill(library: BrainLibrary, brain_id: str) -> None:
    database = library.folder.parent / "rem-home.sqlite3"
    try:
        stamp = database.stat().st_mtime
    except OSError:
        stamp = 0.0
    first = brain_id not in library._receipts_seen
    if not first and library._receipts_seen[brain_id] == stamp:
        return
    library._receipts_seen[brain_id] = stamp
    try:
        library.backfill_provenance(brain_id, conversations=first)
    except (OSError, ValueError, sqlite3.Error):
        log.exception("Second Brain provenance backfill failed")


def backfill_provenance(
    library: BrainLibrary, brain_id: str, *, conversations: bool
) -> dict[str, int]:
    """Attach provenance to existing reports. Never edits files or replaces known facts.

    Rem task receipts create a report record when none exists. Reports saved from a
    Rem thread without provenance get the thread whose archived conversation links
    them, when there is exactly one such link.
    """
    from gofer.ui.brain_graph import conversation_mentions, receipts

    brain = library.brain(brain_id)
    root = Path(brain["root"]).resolve()
    created = updated = skipped = 0
    with library.lock:
        documents = {
            doc["path"]: doc
            for doc in library.entities("documents", brain_id)
            if not doc.get("missing")
        }
        reports = {
            report["artifactPath"]: report for report in library.entities("reports", brain_id)
        }
        touched_docs = []
        for receipt in receipts(library.folder.parent):
            try:
                relative = Path(receipt["path"]).resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            doc = documents.get(relative)
            if doc is None:
                skipped += 1
                continue
            known = {doc["version"], *(v["hash"] for v in doc.get("versions", []))}
            provenance = {
                "savedVia": "rem_task",
                "basis": "observed",
                "source": "receipt",
                "hashMatches": receipt["sha256"] in known,
                **{k: v for k, v in receipt.items() if k != "path" and v},
            }
            existing = reports.get(relative)
            if existing is None:
                record = library._artifact_record(brain, relative, doc["title"], provenance)
                library.put("reports", record)
                reports[relative] = record
                created += 1
                touched_docs.append(doc)
            elif not existing.get("provenance"):
                existing["provenance"] = provenance
                library.put("reports", existing)
                updated += 1
        if conversations:
            missing = [
                path
                for path, report in reports.items()
                if report.get("external") and not report.get("provenance")
            ]
            for path, found in conversation_mentions(root, missing).items():
                report = reports[path]
                report["provenance"] = {
                    "savedVia": "conversation",
                    "basis": "conversation",
                    "source": "conversation archive",
                    **{k: v for k, v in found.items() if v},
                }
                library.put("reports", report)
                updated += 1
        for doc in touched_docs:
            library.edit_document(doc["id"], lambda doc: doc.update(type="report", origin="agent"))
        if created or updated:
            library._portable(brain_id)
    return {"created": created, "updated": updated, "skipped": skipped}
