"""Second Brain Library health: link, permission, twin and lifecycle checks.

Each function takes the BrainLibrary first and reaches its state and other methods
through it at call time, so patches on the library keep working. The filesystem
check caches stay on the library.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from gofer.ui.brain_backups import backup_health
from gofer.ui.brain_documents import read_source, source_path
from gofer.ui.brain_library import (
    HEALTH_RECHECK_SECONDS,
    link_targets,
    linked_document,
    linked_path,
    open_to_others,
)
from gofer.ui.case_twins import brain_twins, twin_message
from gofer.ui.report_links import (
    ABSOLUTE,
    OUTSIDE,
    TEMPORARY,
    LinkCheck,
    check_link,
    rewrite_links,
)
from gofer.utils.atomic_output import atomic_binary_output

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary


def brain_availability(library: BrainLibrary) -> tuple[tuple[str, bool], ...]:
    """Which brain folders exist, without decoding the brain records."""
    with library.connect() as db:
        rows = db.execute("SELECT id,root FROM brains ORDER BY rowid").fetchall()
    return tuple((str(row[0]), Path(row[1]).is_dir()) for row in rows)


def recheck_health(library: BrainLibrary) -> None:
    """Run Library health's filesystem checks again on the next snapshot."""
    library._fs_force = True


def fs_due(library: BrainLibrary) -> bool:
    return (
        library._fs_force
        or library._fs_checked_at is None
        or time.monotonic() - library._fs_checked_at >= HEALTH_RECHECK_SECONDS
    )


def fs_window(library: BrainLibrary) -> None:
    """Forget filesystem results once a minute, or when a recheck was asked for."""
    if not library._fs_due():
        return
    library._fs_force = False
    library._fs_checked_at = time.monotonic()
    library._fs_generation += 1
    library._link_checks.clear()
    library._exists.clear()
    library._sources.clear()
    library._open_checks.clear()
    library._twin_checks.clear()


def cached_link_check(library: BrainLibrary, link: str, root: Path, folder: Path) -> LinkCheck:
    key = (link, str(root), str(folder))
    if key not in library._link_checks:
        library._link_checks[key] = check_link(link, root, folder)
    return library._link_checks[key]


def path_exists(library: BrainLibrary, path: Path | str) -> bool:
    key = str(path)
    if key not in library._exists:
        library._exists[key] = Path(key).exists()
    return library._exists[key]


def source_exists(library: BrainLibrary, root: Path, relative: str) -> bool:
    """Whether a file inside the brain exists; ValueError for an unsafe path."""
    key = f"{root}\0{relative}"
    if key not in library._sources:
        try:
            library._sources[key] = source_path(root, relative).exists()
        except ValueError as exc:
            library._sources[key] = str(exc)
    found = library._sources[key]
    if isinstance(found, str):
        raise ValueError(found)
    return found


def cached_open_check(library: BrainLibrary, root: Path) -> list[dict[str, Any]]:
    key = str(root)
    if key not in library._open_checks:
        library._open_checks[key] = open_to_others(root)
    return library._open_checks[key]


def health(library: BrainLibrary, payload: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = library.access_health(payload["brains"])
    for brain in payload["brains"]:
        kept = backup_health(brain) if brain.get("state") == "available" else None
        if kept and kept["id"] not in brain.get("ignoredIssues", []):
            issues.append(kept)
    hashes: dict[str, str] = {}
    # A note that repeats the same dead link reports it once.
    seen: set[str] = set()
    roots = {brain["id"]: Path(brain["root"]) for brain in payload["brains"]}
    # Each brain's link lookup tables, built once for every link that names a document.
    brain_documents: dict[str, list[dict[str, Any]]] = {}
    for item in payload["documents"]:
        brain_documents.setdefault(item["brainId"], []).append(item)
    brain_targets = {
        brain_id: link_targets(documents) for brain_id, documents in brain_documents.items()
    }
    for doc in payload["documents"]:

        def issue(kind: str, detail: str, fix: str, **extra: Any) -> None:
            key = f"{kind}:{doc['id']}:{doc['version']}:{detail}"
            issue_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            if issue_id not in doc.get("ignoredIssues", []) and issue_id not in seen:
                seen.add(issue_id)
                issues.append(
                    {
                        "id": issue_id,
                        "kind": kind,
                        "docId": doc["id"],
                        "detail": detail,
                        "fix": fix,
                        **extra,
                    }
                )

        if doc["extraction"] in {"ocr", "failed"} and not doc.get("excluded"):
            issue(
                "extraction",
                doc.get("error") or "This document needs optical character recognition.",
                "Run OCR" if doc["extraction"] == "ocr" else "Extract again",
            )
        if not doc["author"] or not doc["dates"]["published"]:
            issue("metadata", "Author or publication date is unknown.", "Edit metadata")
        if doc["version"] in hashes:
            issue(
                "duplicate",
                "Another document has the same source content.",
                "Compare copies",
                docs=[hashes[doc["version"]], doc["id"]],
            )
        else:
            hashes[doc["version"]] = doc["id"]
        root = roots[doc["brainId"]]
        for link in doc.get("links", []):
            checked = library._check_link(link, root, root / Path(doc["path"]).parent)
            if checked.reason == ABSOLUTE:
                shown = Path(checked.resolved).relative_to(root.resolve()).as_posix()
                if library._path_exists(checked.resolved):
                    issue(
                        "link",
                        f"Absolute path to a file in this brain: {link}. It breaks when "
                        f"the brain moves; make it relative ({checked.relative}).",
                        "Make links relative",
                        target=link,
                        reason="absolute",
                    )
                else:
                    issue(
                        "link",
                        f"Linked file is missing: {shown} (written as an absolute path).",
                        "Repair link",
                        target=link,
                        reason="missing",
                        absolute=True,
                    )
                continue
            if checked.reason in {TEMPORARY, OUTSIDE}:
                gone = not library._path_exists(checked.resolved)
                if checked.reason == OUTSIDE and not gone:
                    # Links to files in other projects are often deliberate. They
                    # are only dead once the file is gone; saving still warns.
                    continue
                where = "a temporary folder" if checked.reason == TEMPORARY else "outside the brain"
                gone_text = " The file no longer exists." if gone else ""
                issue(
                    "link",
                    f"Link points to {where}: {link}.{gone_text}",
                    "Repair link",
                    target=link,
                    reason=checked.reason,
                )
                continue
            if urlparse(link).scheme or link.startswith("#") or "?" in link:
                continue
            clean = link.split("#")[0]
            try:
                exists = library._source_exists(root, linked_path(doc, clean)) or bool(
                    linked_document(doc, clean, brain_targets[doc["brainId"]])
                )
            except ValueError:
                exists = False
            if not exists:
                issue(
                    "link",
                    f"Linked document is missing: {clean}",
                    "Repair link",
                    target=clean,
                    reason="missing",
                )
        if any(
            c["docId"] == doc["id"] and c["state"] in {"relocate", "unavailable", "changed"}
            for report in payload["reports"]
            for c in report["citations"]
        ):
            issue("citation", "A report cites a previous source version.", "Review citations")
    issues += library._lifecycle_health(payload)
    return issues


def lifecycle_health(library: BrainLibrary, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Replaced-by suggestions for report families, and check-by reminders."""
    from datetime import date

    from gofer.ui.brain_families import check_due, report_families

    documents = {doc["id"]: doc for doc in payload["documents"]}
    if "families" not in payload:
        payload["families"] = report_families(payload["documents"], library._passage_cache)
    issues: list[dict[str, Any]] = []

    def add(doc: dict[str, Any], kind: str, detail: str, fix: str, **extra: Any) -> None:
        key = f"{kind}:{doc['id']}:{doc['version']}:{extra.get('newerId', '')}:{detail}"
        issue_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        if issue_id not in doc.get("ignoredIssues", []):
            issues.append(
                {
                    "id": issue_id,
                    "kind": kind,
                    "docId": doc["id"],
                    "detail": detail,
                    "fix": fix,
                    **extra,
                }
            )

    for family in payload["families"]:
        suggestion = family["suggestion"]
        if not suggestion:
            continue
        older, newer = documents[suggestion["olderId"]], documents[suggestion["newerId"]]
        add(
            older,
            "family",
            f"Shares {suggestion['shared']} of its {suggestion['olderPassages']} long "
            f"passages with the newer “{newer['title']}”. It may be an older version.",
            "Review suggestion",
            familyId=family["id"],
            newerId=newer["id"],
            share=suggestion["share"],
        )
    today = date.today()
    for doc in payload["documents"]:
        if check_due(doc, today):
            add(
                doc,
                "review",
                f"Check-by date {doc['checkBy']} has arrived. "
                "Confirm the report is still accurate.",
                "Mark checked",
                checkBy=doc["checkBy"],
            )
    return issues


def access_health(library: BrainLibrary, brains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Folder-level problems: brain folders, or their Git folders, other accounts can
    read, and case-only twin files a backup can't hold."""
    issues = []
    for brain in brains:
        if brain.get("state") != "available":
            continue
        for item in library._open_to_others(Path(brain["root"])):
            key = f"permissions:{brain['id']}:{item['path']}:{item['mode']:o}"
            issue_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            if issue_id in brain.get("ignoredIssues", []):
                continue
            what = "brain folder" if item["part"] == "folder" else "brain's Git history"
            issues.append(
                {
                    "id": issue_id,
                    "kind": "permissions",
                    "brainId": brain["id"],
                    "path": item["path"],
                    "detail": (
                        f"Other accounts on this computer can read the {what}"
                        f" (permissions {item['mode']:o})."
                    ),
                    "fix": "Make private",
                }
            )
        root = Path(brain["root"])
        if str(root) not in library._twin_checks:
            library._twin_checks[str(root)] = brain_twins(root)
        for first, second in library._twin_checks[str(root)]:
            key = f"twins:{brain['id']}:{first}:{second}"
            issue_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            if issue_id in brain.get("ignoredIssues", []):
                continue
            issues.append(
                {
                    "id": issue_id,
                    "kind": "twins",
                    "brainId": brain["id"],
                    "path": str((root / first).parent),
                    "paths": [first, second],
                    "detail": twin_message(first, second),
                    "fix": "Open folder",
                }
            )
    return issues


def absolute_links(
    library: BrainLibrary, brain_id: str, *, apply: bool = False, expected: Any = None
) -> dict[str, Any]:
    """Preview, or apply, making absolute links inside a brain relative.

    The preview counts every absolute link in the brain's notes that resolves
    inside the brain folder. Applying needs the preview's count, so nothing is
    rewritten unless the user saw exactly what changes. Links to temporary or
    outside folders are counted and left as written. Running it again after it
    has been applied finds nothing to change.
    """
    brain = library.brain(brain_id)
    root = Path(brain["root"])
    if not root.is_dir():
        raise ValueError("Brain folder is unavailable")
    planned: list[tuple[dict[str, Any], str, str]] = []
    changes: list[dict[str, Any]] = []
    left = {TEMPORARY: 0, OUTSIDE: 0}
    missing = 0
    for doc in sorted(library.entities("documents", brain_id), key=lambda item: item["path"]):
        if doc.get("missing") or Path(doc["path"]).suffix.lower() not in {
            ".md",
            ".markdown",
            ".html",
            ".htm",
        }:
            continue
        try:
            data = read_source(root, doc["path"])
            text = data.decode(doc.get("encoding", "utf-8"))
            folder = source_path(root, doc["path"]).parent
        except (OSError, ValueError):
            continue
        repaired, rewritten, flagged = rewrite_links(text, doc["path"], root, folder)
        for check in flagged:
            left[str(check.reason)] += 1
        if not rewritten:
            continue
        planned.append((doc, text, repaired))
        for check in rewritten:
            exists = Path(check.resolved).exists()
            missing += not exists
            changes.append(
                {
                    "docId": doc["id"],
                    "path": doc["path"],
                    "old": check.link,
                    "new": check.relative,
                    "targetExists": exists,
                }
            )
    summary: dict[str, Any] = {
        "count": len(changes),
        "documents": len(planned),
        "missingTargets": missing,
        "temporary": left[TEMPORARY],
        "outside": left[OUTSIDE],
        "changes": changes[:500],
    }
    if not apply:
        return summary
    if expected is None or int(expected) != len(changes):
        raise ValueError(
            "The links changed since the preview. Preview the repair again before applying it."
        )
    written = 0
    for doc, _, repaired in planned:
        with atomic_binary_output(source_path(root, doc["path"])) as handle:
            handle.write(repaired.encode(doc.get("encoding", "utf-8")))
        written += 1
    return {**summary, "changes": changes[:50], "applied": True, "written": written}
