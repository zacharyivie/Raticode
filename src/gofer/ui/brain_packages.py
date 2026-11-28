"""Second Brain backups and brain package export and import.

Each function takes the BrainLibrary first and reaches its state and other methods
through it at call time, so patches on the library keep working.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_backups import (
    KEPT_SMALLER,
    backup_path,
    backup_settings,
    clean_partials,
    due_key,
    format_size,
    prune,
    smaller_counts,
    validate_backup,
    verify_package,
)
from gofer.ui.brain_documents import (
    MAX_FILE,
    MAX_PACKAGE,
    checked_archive,
    read_source,
    source_path,
)
from gofer.ui.brain_library import KINDS, encode, evidence_references, identifier, now, write_json
from gofer.ui.case_twins import CaseTwinError, case_twins, twin_message
from gofer.utils.atomic_output import atomic_binary_output, open_binary_input
from gofer.utils.drive_format import write_error_message
from gofer.utils.paths import chosen_destination

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary

log = logging.getLogger(__name__)

# A save still being written (gofer.utils.atomic_output names it ".<name>.<hex>.tmp").
# It is renamed or removed at any moment, so packages never take it.
ATOMIC_TEMPORARY = re.compile(r"^\..+\.[0-9a-f]{32}\.tmp$")


ZIP_CHUNK = 64 * 1024


def write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    """Write one file into a zip in chunks, so compressing it never holds a second copy."""
    info = zipfile.ZipInfo(name, date_time=time.localtime(time.time())[:6])
    info.compress_type = archive.compression
    info.external_attr = 0o600 << 16  # as writestr sets it
    info.file_size = len(data)
    view = memoryview(data)
    with archive.open(info, "w") as member:
        for start in range(0, len(view), ZIP_CHUNK):
            member.write(view[start : start + ZIP_CHUNK])


def member_digest(archive: zipfile.ZipFile, name: str) -> str:
    """SHA-256 of one zip member, read back in chunks."""
    digest = hashlib.sha256()
    with archive.open(name) as member:
        while chunk := member.read(ZIP_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def backup_status(library: BrainLibrary, brain_id: str, **patch: Any) -> dict[str, Any]:
    with library.lock:
        brain = library.brain(brain_id)
        brain["backupStatus"] = {**brain.get("backupStatus", {}), **patch}
        library.put_brain(brain)
        return brain


def scheduled_backups(library: BrainLibrary, moment: datetime) -> None:
    for brain in library.brains():
        if library.stop.is_set():
            return
        key = due_key(backup_settings(brain), moment)
        if key is None or (brain.get("backupStatus") or {}).get("lastScheduled") == key:
            continue
        if not Path(brain["root"]).is_dir():
            # An unplugged drive: try again when the brain is back.
            continue
        try:
            # Claim the period first, so a failing backup waits for the next one
            # instead of retrying every 15 seconds. The failure shows in settings.
            library._backup_status(brain["id"], lastScheduled=key)
            library.start_task(
                "backup",
                {"brainId": brain["id"], "scheduled": True},
                label=f"Backing up {brain['name']}",
            )
        except (ValueError, OSError) as exc:
            log.debug("Skipped backing up brain %s (%s)", brain.get("id"), type(exc).__name__)


def backup_estimate(library: BrainLibrary, brain_id: str) -> dict[str, Any]:
    """Bytes a complete package would hold, and how much is Rem's conversation archive."""
    from gofer.ui.brain_graph import conversation_archives

    root = Path(library.brain(brain_id)["root"])
    archives = {path.name for path in conversation_archives(root)}
    total = conversations = 0
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if name not in {".raticode", ".git"} and not (Path(parent) / name).is_symlink()
        ]
        inside = Path(parent) != root and (Path(parent).relative_to(root).parts[0] in archives)
        for name in names:
            path = Path(parent) / name
            if path.is_symlink() or name in {".env", ".env.local", "credentials.json"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            conversations += size if inside else 0
    return {
        "total": total,
        "conversations": conversations,
        "archives": sorted(archives),
        "limit": MAX_PACKAGE,
    }


def backup(
    library: BrainLibrary,
    params: dict[str, Any],
    cancel: threading.Event,
    progress: Callable[[float], None],
) -> dict[str, Any]:
    """Write a complete package to the backup folder, verify the saved copy, then prune.

    A copy with less than half the files, documents or bytes of the last verified one
    is kept and nothing is pruned. ``acceptSmaller`` makes the new copy the one later
    backups compare with.
    """
    brain = library.brain(params["brainId"])
    # Re-check: the brain may have moved since the folder was chosen.
    settings = validate_backup(backup_settings(brain), Path(brain["root"]))
    if not settings["folder"]:
        raise ValueError("Choose a backup folder in Brain settings first")
    folder = Path(settings["folder"])
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(write_error_message(exc, folder)) from exc
    clean_partials(folder)
    moment = datetime.now(UTC)
    destination = backup_path(folder, brain, moment)
    while destination.exists():
        # Two backups within one second: names must stay unique and in order.
        moment += timedelta(seconds=1)
        destination = backup_path(folder, brain, moment)
    exported = library.export_package(
        {
            "brainId": brain["id"],
            "destination": str(destination),
            "purpose": "complete",
            "snapshots": True,
            "verify": True,
            "conversations": settings["conversations"],
        },
        cancel,
        lambda value: progress(value * 0.85),
    )
    try:
        checked = verify_package(destination)
    except Exception:
        # Never keep, or count, a copy that would not restore.
        destination.unlink(missing_ok=True)
        raise
    progress(95)
    verified = {
        "at": now(),
        "path": str(destination),
        "size": exported["size"],
        "files": checked["files"],
        "documents": checked["documents"],
        "conversations": exported["conversations"],
    }
    last = (library.brain(brain["id"]).get("backupStatus") or {}).get("lastVerified")
    smaller = [] if params.get("acceptSmaller") else smaller_counts(verified, last)
    if smaller:
        # An emptied, half-synced or unmounted brain folder: keep this copy and every
        # older one, and keep comparing with the last full copy until the user
        # accepts the smaller size.
        library._backup_status(
            brain["id"],
            lastAttempt={
                "at": verified["at"],
                "state": "kept",
                "error": "",
                "detail": KEPT_SMALLER,
                "smaller": smaller,
                **{key: verified[key] for key in ("path", "size", "files", "documents")},
                "conversations": verified["conversations"],
            },
        )
        return {**verified, "removed": [], "verified": True, "kept": True, "smaller": smaller}
    removed = prune(folder, brain, settings["keep"], destination)
    library._backup_status(
        brain["id"],
        lastVerified=verified,
        lastAttempt={"at": verified["at"], "state": "verified", "error": ""},
    )
    return {**verified, "removed": removed, "verified": True}


def export_package(
    library: BrainLibrary,
    params: dict[str, Any],
    cancel: threading.Event,
    progress: Callable[[float], None],
) -> dict[str, Any]:
    brain = library.brain(params["brainId"])
    destination = Path(params["destination"]).expanduser()
    if not destination.is_absolute() or destination.exists():
        raise ValueError("Choose a new absolute export filename")
    destination = chosen_destination(destination)
    root = Path(brain["root"])
    purpose = params.get("purpose", "complete")
    if purpose not in {"complete", "documents", "collection"}:
        raise ValueError("Choose documents, a collection or a complete brain")
    with library.lock:
        records = {kind: copy.deepcopy(library.entities(kind, brain["id"])) for kind in KINDS}
    if purpose == "collection":
        collection = library.entity("collections", params["collectionId"])
        ids = {item["docId"] for section in collection["sections"] for item in section["items"]}
        records["collections"] = [collection]
        records["documents"] = [doc for doc in records["documents"] if doc["id"] in ids]
        records["reports"] = [
            report
            for report in records["reports"]
            if report.get("collectionId") == collection["id"]
        ]
        ids |= {c["docId"] for report in records["reports"] for c in report["citations"]}
        records["documents"] = [
            doc for doc in library.entities("documents", brain["id"]) if doc["id"] in ids
        ]
        records["citations"] = [c for c in records["citations"] if c["docId"] in ids]
        records["savedSearches"] = []
        records["jobs"] = []
        records["projects"] = []
        records["topics"] = []
    for job in records["jobs"]:
        job.pop("provider", None)
        job["enabled"] = False
    for kind in ("projects", "topics"):
        if not records[kind]:
            # Packages without these newer records still import into older builds.
            del records[kind]
    referenced_ids = {ref["docId"] for ref in evidence_references(records)}
    archives: set[str] = set()
    if purpose != "collection" and not params.get("conversations", True):
        # Rem's conversation archive can be most of a brain. Leaving it out keeps
        # the package under its size limit; archived files that a report, citation
        # or collection uses still go in, so evidence keeps resolving.
        from gofer.ui.brain_graph import conversation_archives

        archives = {path.name for path in conversation_archives(root)}
        records["documents"] = [
            doc
            for doc in records["documents"]
            if doc["path"].split("/", 1)[0] not in archives or doc["id"] in referenced_ids
        ]
    included_ids = {doc["id"] for doc in records["documents"]}
    with library.connect() as db:
        frozen = {
            row[0]: json.loads(row[1])
            for row in db.execute(
                "SELECT id,body FROM external_sources WHERE brain=?", (brain["id"],)
            )
        }
    live = {doc["id"]: doc for doc in library.entities("documents")}
    external = []
    for doc_id in referenced_ids - included_ids:
        source = live.get(doc_id) or frozen.get(doc_id)
        if not source:
            raise ValueError("Referenced evidence is unavailable; restore its source brain")
        doc = copy.deepcopy(source)
        if doc_id not in live or not Path(library.brain(doc["brainId"])["root"]).is_dir():
            doc.update(missing=True, excluded=True, extraction="unavailable")
        external.append(doc)
    roots = {
        doc["id"]: Path(library.brain(doc["brainId"])["root"])
        for doc in [*records["documents"], *external]
        if not doc.get("missing")
    }
    original_paths = {doc["id"]: doc["path"] for doc in [*records["documents"], *external]}
    for doc in external:
        doc["path"] = f"Sources/{doc['brainId']}/{doc['path']}"
        doc["brainId"] = brain["id"]
    records["documents"] += external
    for doc in records["documents"]:
        doc.pop("fingerprint", None)
        doc.pop("fileIdentity", None)
    if not params.get("snapshots", True):
        for doc in records["documents"]:
            # Retain versions cited by reports or curated evidence even
            # when optional history is off.
            used = {
                c["version"]
                for report in records["reports"]
                for c in report["citations"]
                if c["docId"] == doc["id"]
            }
            used |= {
                ref.get("version")
                for ref in evidence_references(records)
                if ref["docId"] == doc["id"]
            }
            doc["versions"] = [entry for entry in doc["versions"] if entry["hash"] in used]
    sources = {
        f"files/{doc['path']}": (roots[doc["id"]], original_paths[doc["id"]])
        for doc in records["documents"]
        if not doc.get("missing")
    }
    # Files the folder walk found that no library document names. Another program
    # (Rem saving a note, a sync tool) may remove one before it is read.
    walked: set[str] = set()
    if purpose in {"complete", "documents"}:
        # Preserve vault configuration and attachments, without exporting
        # Raticode caches/secrets.
        for parent, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [
                name
                for name in dirs
                if name not in {".raticode", ".git"}
                and not (Path(parent) / name).is_symlink()
                and (purpose == "complete" or not name.startswith("."))
                and not (Path(parent) == root and name in archives)
            ]
            for name in names:
                path = Path(parent) / name
                if (
                    path.is_symlink()
                    or name in {".env", ".env.local", "credentials.json"}
                    or ATOMIC_TEMPORARY.match(name)
                ):
                    continue
                relative = path.relative_to(root).as_posix()
                if f"files/{relative}" not in sources:
                    walked.add(f"files/{relative}")
                    sources[f"files/{relative}"] = (root, relative)
    # Before any file is read: a package can't hold names that differ only by
    # case, and the archive check would refuse them only after reading everything.
    twins = case_twins(sources)
    if twins:
        first, second = (name.removeprefix("files/") for name in twins[0])
        raise CaseTwinError(twin_message(first, second))
    # Refuse before reading anything, naming the file, rather than failing partway.
    sizes: dict[str, int] = {}
    for name, (base, relative) in sources.items():
        try:
            sizes[name] = (base / relative).stat().st_size
        except OSError:
            continue
    largest = max(sizes.items(), key=lambda item: item[1], default=("", 0))
    if largest[1] > MAX_FILE:
        raise ValueError(
            f"{largest[0].removeprefix('files/')} is {format_size(largest[1])}, over"
            f" the {format_size(MAX_FILE)} limit for one file in a package"
        )
    if purpose != "documents":
        # The verifier rejects a package over its limit. A documents export is plain
        # files written one at a time, so it has no total limit.
        total = sum(sizes.values())
        if total > MAX_PACKAGE:
            hint = (
                " Leave out Rem's conversation archive, or move large files out of the brain."
                if params.get("conversations", True)
                else " Move large files out of the brain."
            )
            raise ValueError(
                f"This package would be {format_size(total)}, over the"
                f" {format_size(MAX_PACKAGE)} package limit.{hint}"
            )

    def read(name: str) -> bytes | None:
        base, relative = sources[name]
        try:
            return read_source(base, relative, MAX_FILE)
        except FileNotFoundError:
            # A library document that is gone still fails the package; a stray
            # file removed since the walk simply isn't in it.
            if name not in walked:
                raise
            return None

    # Complete and collection packages hash every file into a manifest written
    # first, so they read everything up front (within MAX_PACKAGE). A documents
    # export has no manifest: each file is read, hashed and written on its own.
    files: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    if purpose != "documents":
        for name in sources:
            if cancel.is_set():
                raise InterruptedError("Export cancelled")
            data = read(name)
            if data is not None:
                files[name] = data
        digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    manifest: dict[str, Any] = {
        "format": "raticode-brain",
        "version": 1,
        "exported": now(),
        "brain": {key: brain[key] for key in ("id", "name", "settings", "color")},
        "library": records,
        "files": digests,
    }
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A recognizable name, so a backup can remove one left by a killed process.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=".raticode-export-",
            suffix=".zip.partial",
            delete=False,
        ) as staging:
            temporary = Path(staging.name)
    except OSError as exc:
        raise ValueError(write_error_message(exc, destination.parent)) from exc
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if purpose == "documents":
                for index, name in enumerate(sources):
                    if cancel.is_set():
                        raise InterruptedError("Export cancelled")
                    entry = name.removeprefix("files/")
                    try:
                        data = read(name)
                    except OSError as exc:
                        # Not the destination's fault, so not a write error.
                        raise ValueError(f"Couldn't read {entry}: {exc.strerror or exc}") from exc
                    if data is not None:
                        digests[entry] = hashlib.sha256(data).hexdigest()
                        write_member(archive, entry, data)
                        del data
                    progress(90 * (index + 1) / max(1, len(sources)))
            else:
                archive.writestr("manifest.json", encode(manifest))
                for index, (name, data) in enumerate(files.items()):
                    if cancel.is_set():
                        raise InterruptedError("Export cancelled")
                    archive.writestr(name, data)
                    progress(90 * (index + 1) / max(1, len(files)))
        if purpose != "documents":
            checked_archive(temporary)
        if params.get("verify", True):
            with zipfile.ZipFile(temporary) as archive:
                for name, digest in digests.items():
                    if member_digest(archive, name) != digest:
                        raise ValueError("Export verification failed")
        if cancel.is_set():
            raise InterruptedError("Export cancelled")
        # Exclusive publication cannot overwrite a concurrent export.
        with (
            atomic_binary_output(destination, exclusive=True) as handle,
            open_binary_input(temporary) as source,
        ):
            shutil.copyfileobj(source, handle)
        return {
            "path": str(destination),
            "verified": bool(params.get("verify", True)),
            "documents": len(records["documents"]),
            "size": destination.stat().st_size,
            "conversations": not archives,
        }
    except InterruptedError:
        raise
    except OSError as exc:
        # The drive's own words, such as "[Errno 27] File too large", say nothing
        # about FAT32's 4 GB limit or a read-only mount.
        raise ValueError(write_error_message(exc, destination.parent)) from exc
    finally:
        temporary.unlink(missing_ok=True)


def import_package(
    library: BrainLibrary,
    params: dict[str, Any],
    cancel: threading.Event,
    progress: Callable[[float], None],
) -> dict[str, Any]:
    package = Path(params["path"])
    manifest, _ = checked_archive(package)
    destination = Path(params["destination"]).expanduser()
    if not destination.is_absolute() or destination.exists():
        raise ValueError("Choose a new destination folder; existing folders are never overwritten")
    destination = chosen_destination(destination)
    seed = manifest["brain"]
    # "restore-here" restores a registered brain whose folder is gone: the files go to
    # the destination and the registration moves there, as Locate does.
    here = params.get("identity") == "restore-here"
    restore = here or params.get("identity") == "restore"
    brain_id = seed["id"] if restore else identifier("brain")
    registered = next((brain for brain in library.brains() if brain["id"] == brain_id), None)
    if registered is not None and not here:
        raise ValueError(
            "The original brain is already registered. Import a copy instead."
            if Path(registered["root"]).is_dir()
            else "The original brain is registered but its folder is gone."
            " Choose Restore this brain here."
        )
    if here:
        library._check_restore_here(registered)
    records = copy.deepcopy(manifest["library"])
    if params.get("duplicates") == "merge":
        known = {doc["version"]: doc for doc in library.entities("documents")}
        for doc in records.get("documents", []):
            if doc["version"] in known:
                versions = {
                    version["hash"]: version
                    for version in [*known[doc["version"]]["versions"], *doc["versions"]]
                }
                doc["versions"] = list(versions.values())
    mapping = (
        {
            item["id"]: identifier(kind.rstrip("s"))
            for kind in KINDS
            for item in records.get(kind, [])
        }
        if not restore
        else {}
    )

    def remap(value: Any) -> Any:
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {
                key: (brain_id if key == "brainId" else remap(item)) for key, item in value.items()
            }
        return mapping.get(value, value) if isinstance(value, str) else value

    records = remap(records)
    for kind in KINDS:
        # The registration being restored keeps its own records; only other brains'
        # records can clash.
        occupied = {
            item["id"] for item in library.entities(kind) if not here or item["brainId"] != brain_id
        }
        if any(item["id"] in occupied for item in records.get(kind, [])):
            raise ValueError("A record identity is already registered. Import an independent copy.")
    for job in records.get("jobs", []):
        job["enabled"] = False
        job.pop("provider", None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".raticode-import-", dir=destination.parent))
    try:
        with zipfile.ZipFile(package) as archive:
            for index, (name, digest) in enumerate(manifest["files"].items()):
                if cancel.is_set():
                    raise InterruptedError("Import cancelled")
                if not name.startswith("files/"):
                    raise ValueError("Unsupported package entry")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"Package checksum does not match: {name}")
                target = source_path(staging, name.removeprefix("files/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                progress(85 * (index + 1) / max(1, len(manifest["files"])))
        metadata = staging / ".raticode/brain.json"
        metadata.parent.mkdir(exist_ok=True)
        seed.update(id=brain_id, name=seed["name"] if restore else f"{seed['name']} (copy)")
        write_json(
            metadata,
            {"format": "raticode-brain", "version": 1, "brain": seed, "library": records},
        )
        if cancel.is_set():
            raise InterruptedError("Import cancelled")
        if here:
            with library.lock:
                # The folder may have come back, or the brain been removed, while
                # the package was extracting.
                library._check_restore_here(
                    next((b for b in library.brains() if b["id"] == brain_id), None)
                )
                if destination.exists():
                    raise ValueError("Import destination appeared while extracting")
                staging.rename(destination)
                library._restore_here(brain_id, destination)
            return {
                "brainId": brain_id,
                "path": str(destination),
                "documents": len(records.get("documents", [])),
                "restoredHere": True,
            }
        if destination.exists():
            raise ValueError("Import destination appeared while extracting")
        staging.rename(destination)
        brain = library.open({"root": str(destination), "name": seed["name"]})
        return {
            "brainId": brain["id"],
            "path": str(destination),
            "documents": len(records.get("documents", [])),
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def check_restore_here(registered: dict[str, Any] | None) -> None:
    """Restoring over a registration is only for a brain whose folder is gone."""
    if registered is None:
        raise ValueError(
            "This brain isn't registered on this computer. Choose Restore this brain instead."
        )
    if Path(registered["root"]).is_dir():
        raise ValueError(
            f"{registered['name']} still has its folder at {registered['root']}."
            " Import a copy instead."
        )


def restore_here(library: BrainLibrary, brain_id: str, destination: Path) -> None:
    """Point a registration at restored files and scan them, as Locate does.

    The brain ID, settings, backup settings and status, and its library records stay,
    so Rem threads that chose the brain still have it.
    """
    root = destination.resolve(strict=True)
    if str(root) == library.brain(brain_id)["root"]:
        # Restored into the folder that went missing: nothing to move.
        library.start_task("index", {"brainId": brain_id, "force": True})
    else:
        library.update_brain({"id": brain_id, "patch": {"root": str(root)}})
