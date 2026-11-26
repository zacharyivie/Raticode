"""Which providers may read which brains.

Shared by report generation, background research jobs and every agent turn: a brain
with Allow cloud processing off reaches only providers on localhost. Agent tokens get
the same rule on the Second Brain API unless the owner created them with
`Raticode tokens create --private-brains`.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gofer.core.provider_profiles import load_provider_profiles
from gofer.ui.brain_folders import LEFT_OUT_REFUSAL, is_left_out


def local_profile(profile: Any) -> bool:
    if profile.subscription not in {"openai_api", "anthropic_api"} or not profile.api_base_url:
        return False
    parsed = urlparse(profile.api_base_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False


def research_profiles(data_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "id": profile.subscription,
            "model": profile.model,
            "local": local_profile(profile),
            "effort": profile.effort,
        }
        for name, profile in load_provider_profiles(data_dir).items()
    ]


def provider_is_local(provider: dict[str, Any], data_dir: Path) -> bool:
    profile = load_provider_profiles(data_dir).get(provider.get("profile") or "")
    return bool(profile and profile.subscription == provider.get("id") and local_profile(profile))


class PrivateBrainError(ValueError):
    """A turn's folder is, sits inside or holds a brain that keeps cloud processing off."""


# How long a check waits for the library or Rem's store while another process writes.
_BUSY_TIMEOUT = 30.0


def _read_rows(database: Path, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
    db = sqlite3.connect(database, timeout=_BUSY_TIMEOUT)
    try:
        db.execute("PRAGMA query_only=ON")
        return db.execute(query, params).fetchall()
    finally:
        db.close()


def registered_brains(data_dir: Path) -> list[dict[str, Any]]:
    """Every brain registered in this data folder's library, read without changing it.

    A library that can't be read raises PrivateBrainError, so no turn starts without
    knowing which brains are private.
    """
    database = data_dir / "second-brains" / "library.sqlite3"
    if not database.is_file():
        return []
    try:
        rows = _read_rows(database, "SELECT body FROM brains")
        return [json.loads(row[0]) for row in rows]
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise PrivateBrainError(
            f"Raticode couldn't read the Second Brain library to check which brains are "
            f"private: {exc}"
        ) from exc


def memory_folder(data_dir: Path) -> Path | None:
    """The Settings > Memory folder while Second Brain is on for Rem, or None.

    It was approved when the user chose it, so it reaches Rem without Allow cloud
    processing (see second_brain.library_brain_config).
    """
    database = data_dir / "rem-home.sqlite3"
    if not database.is_file():
        return None
    try:
        rows = _read_rows(database, "SELECT body FROM settings WHERE key='config'")
        brain = (json.loads(rows[0][0]) if rows else {}).get("brain") or {}
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(brain, dict) or brain.get("enabled") is not True or not brain.get("root"):
        return None
    return Path(str(brain["root"]))


def _canonical(path: Path | str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def private_brain_folders(
    folders: Iterable[Path | str],
    brains: Iterable[dict[str, Any]],
    *,
    approved: Iterable[Path | str | None] = (),
) -> list[tuple[str, Path]]:
    """Private brains inside the given folders, as (name, folder), to hide from the turn.

    A brain is private while Allow cloud processing is off. ``approved`` folders, such
    as the Settings > Memory folder, were approved when the user chose them and never
    count. Raises PrivateBrainError, naming the brain and both fixes, when a folder is
    a private brain or sits inside one.
    """
    allowed = {_canonical(path) for path in approved if path}
    private: list[tuple[str, Path]] = []
    for brain in brains:
        if (brain.get("settings") or {}).get("allowCloud") or not brain.get("root"):
            continue
        root = _canonical(brain["root"])
        if root not in allowed:
            private.append((str(brain.get("name") or root.name), root))
    hidden: list[tuple[str, Path]] = []
    for value in folders:
        folder = _canonical(value)
        for name, root in private:
            if folder == root or folder.is_relative_to(root):
                where = "is" if folder == root else "is inside"
                raise PrivateBrainError(
                    f"{folder} {where} the Second Brain {name}, which doesn't allow cloud "
                    f"processing. Turn on Allow cloud processing in {name}'s Brain settings, "
                    "or use a provider on localhost."
                )
            if root.is_relative_to(folder) and all(root != seen for _n, seen in hidden):
                hidden.append((name, root))
    return hidden


def brain_is_private(brain: dict[str, Any]) -> bool:
    """Whether [brain] keeps Allow cloud processing off."""
    return not (brain.get("settings") or {}).get("allowCloud")


def private_folders_inside(root: Path, brains: Iterable[dict[str, Any]]) -> set[Path]:
    """Folders of the private brains among [brains] that sit inside [root], not at it.

    Their files belong to that private brain, so an index of [root] (a brain's or the
    Settings > Memory folder's) leaves them out. [root] is taken as given, so pass it
    resolved; the brains' folders are resolved here.
    """
    outer = Path(root)
    inside: set[Path] = set()
    for brain in brains:
        if not brain_is_private(brain) or not brain.get("root"):
            continue
        folder = _canonical(brain["root"])
        if folder != outer and folder.is_relative_to(outer):
            inside.add(folder)
    return inside


# The error code an agent token gets when a request names a private brain.
AGENT_BRAIN_PRIVATE = "brain_private"
# The error code an agent token gets when a read names a document left out of search.
AGENT_DOCUMENT_LEFT_OUT = "document_left_out"
# Second Brain reads that look up one document by ``params["id"]``.
_DOCUMENT_ACTIONS = frozenset({"original", "related", "connections"})


def _agent_named_brains(library: Any, action: str, params: dict[str, Any]) -> list[str]:
    """The brain IDs an agent token's Second Brain read names, directly or by document."""
    named: list[Any] = []
    if action == "search":
        named = list(params.get("brainIds") or [params.get("brainId")])
    elif action in {"projects", "topics"}:
        named = [params.get("brainId")]
    elif action == "report_sources":
        named = [params.get("brainId"), *(params.get("otherBrains") or [])]
        named += [
            library.entity("documents", str(doc_id))["brainId"]
            for doc_id in params.get("docIds") or []
        ]
    elif action == "job_sources":
        job = params.get("job")
        named = [job.get("brainId")] if isinstance(job, dict) else []
    elif action in _DOCUMENT_ACTIONS and params.get("id") is not None:
        named = [library.entity("documents", str(params["id"]))["brainId"]]
    return [str(item) for item in named if item not in (None, "")]


def _private_brain_holding(library: Any, doc_id: str) -> dict[str, Any] | None:
    """The private brain whose folder holds this document's file, or None.

    A brain registered inside another brain's folder keeps its own privacy: the outer
    brain may still have rows for its files from before it was registered or made
    private, until the outer brain's next refresh drops them.
    """
    try:
        doc = library.entity("documents", doc_id)
        holder = library.brain(doc["brainId"])
    except (ValueError, KeyError):
        return None
    path = _canonical(Path(holder["root"]) / str(doc.get("path") or ""))
    for brain in library.brains():
        if brain["id"] == holder["id"] or not brain_is_private(brain):
            continue
        if path.is_relative_to(_canonical(brain["root"])):
            return dict(brain)
    return None


def _agent_named_documents(action: str, params: dict[str, Any]) -> list[str]:
    if action == "report_sources":
        return [str(doc_id) for doc_id in params.get("docIds") or []]
    if action in _DOCUMENT_ACTIONS and params.get("id") is not None:
        return [str(params["id"])]
    return []


def agent_brain_refusal(library: Any, action: str, params: dict[str, Any]) -> str | None:
    """Why an agent token without ``--private-brains`` can't make this read, or None.

    A read that names a brain with Allow cloud processing off is refused, and so is one
    about a document in such a brain, or whose file sits in such a brain's folder. An
    unknown brain is left to the library, which answers it as a bad request; an unknown
    document raises ValueError the same way.
    """
    for doc_id in _agent_named_documents(action, params):
        holder = _private_brain_holding(library, doc_id)
        if holder is not None:
            name = str(holder.get("name") or holder["id"])
            return (
                f"The Second Brain {name} keeps Allow cloud processing off, so agent tokens "
                f"can't read it. Turn on Allow cloud processing in {name}'s Brain settings, "
                "or use a token created with `Raticode tokens create --private-brains`."
            )
    for brain_id in dict.fromkeys(_agent_named_brains(library, action, params)):
        try:
            brain = library.brain(brain_id)
        except ValueError:
            continue
        if brain_is_private(brain):
            name = str(brain.get("name") or brain_id)
            return (
                f"The Second Brain {name} keeps Allow cloud processing off, so agent tokens "
                f"can't read it. Turn on Allow cloud processing in {name}'s Brain settings, "
                "or use a token created with `Raticode tokens create --private-brains`."
            )
    return None


def agent_left_out_refusal(library: Any, action: str, params: dict[str, Any]) -> str | None:
    """Why an agent token can't read this document, or None.

    Every agent token, ``--private-brains`` or not, is refused an original, related or
    connections read about a document left out of search, with read_note's words. The
    Library's own window still opens it for the user. An unknown document is left to
    the library.
    """
    if action not in _DOCUMENT_ACTIONS or params.get("id") is None:
        return None
    try:
        doc = library.entity("documents", str(params["id"]))
    except ValueError:
        return None
    return LEFT_OUT_REFUSAL if is_left_out(doc) else None


def agent_left_out_result(library: Any, action: str, result: Any) -> Any:
    """Drop left-out documents from a related, connections or passages read for an agent token.

    passages answers a left-out document as missing, like one that is gone.
    """
    if action == "related" and isinstance(result, list):
        hidden = _left_out_ids(library, result)
        return [doc_id for doc_id in result if doc_id not in hidden]
    if action == "passages" and isinstance(result, dict):
        documents = result.get("documents") or []
        hidden = _left_out_ids(library, [doc.get("id") for doc in documents])
        if not hidden:
            return result
        return {
            **result,
            "documents": [doc for doc in documents if doc.get("id") not in hidden],
            "missing": [
                *(result.get("missing") or []),
                *(d["id"] for d in documents if d.get("id") in hidden),
            ],
        }
    if action != "connections" or not isinstance(result, dict):
        return result
    links = result.get("links") or {}
    similar = result.get("similar") or []
    hidden = _left_out_ids(
        library,
        [
            *(links.get("cites") or []),
            *(links.get("citedBy") or []),
            *(item.get("docId") for item in similar),
        ],
    )
    if not hidden:
        return result
    return {
        **result,
        "links": {key: [i for i in ids if i not in hidden] for key, ids in links.items()},
        "similar": [item for item in similar if item.get("docId") not in hidden],
    }


def _left_out_ids(library: Any, doc_ids: Iterable[Any]) -> set[str]:
    ids = [str(doc_id) for doc_id in doc_ids if doc_id]
    brain_ids = [brain["id"] for brain in library.brains()]
    return {doc_id for doc_id, doc in library.documents(ids, brain_ids).items() if is_left_out(doc)}


def agent_brain_result(library: Any, action: str, result: Any) -> Any:
    """Drop private brains' documents from a read whose sources can span brains.

    report_sources and job_sources can reach other brains through a collection or a
    saved search, after the brains the request named were checked. passages names
    documents by ID from any brain; a private one is answered as missing.
    """
    if action not in {"report_sources", "job_sources", "passages"} or not isinstance(result, dict):
        return result
    private = {brain["id"] for brain in library.brains() if brain_is_private(brain)}
    if not private:
        return result

    def allowed(source: dict[str, Any]) -> bool:
        brain_id = source.get("brainId")
        if brain_id is None:
            try:
                brain_id = library.entity("documents", str(source.get("id")))["brainId"]
            except ValueError:
                return False
        if brain_id in private:
            return False
        return _private_brain_holding(library, str(source.get("id"))) is None

    if action == "passages":
        shown = [doc for doc in result.get("documents", []) if allowed(doc)]
        kept = {doc["id"] for doc in shown}
        hidden = [doc["id"] for doc in result.get("documents", []) if doc["id"] not in kept]
        return {**result, "documents": shown, "missing": [*result.get("missing", []), *hidden]}
    sources = [source for source in result.get("sources", []) if allowed(source)]
    return {**result, "sources": sources}


def agent_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """The library snapshot as an agent token without ``--private-brains`` sees it.

    Private brains stay in the list with their ID, name and the state ``private``, so
    `Raticode brain list` can say why. Their documents, reports, tasks and every other
    record that belongs to them are left out.
    """
    if payload.get("unchanged"):
        return payload
    brains = payload.get("brains") or []
    private = {brain["id"] for brain in brains if brain_is_private(brain)}
    if not private:
        return payload
    # Brain and document IDs are unique random strings, so a record that mentions one
    # anywhere (a duplicate issue's docs, a family's members, a task's sources) is
    # about that brain and is left out whole.
    hidden = private | {
        doc["id"] for doc in payload.get("documents") or [] if doc.get("brainId") in private
    }

    def mentions(value: Any) -> bool:
        if isinstance(value, str):
            return value in hidden
        if isinstance(value, dict):
            return any(mentions(item) for item in value.values())
        if isinstance(value, list):
            return any(mentions(item) for item in value)
        return False

    def visible(item: Any) -> bool:
        if isinstance(item, dict) and item.get("brainId") in private:
            return False
        return not mentions(item)

    result = dict(payload)
    for key, value in payload.items():
        if key != "brains" and isinstance(value, list):
            result[key] = [item for item in value if visible(item)]
    result["brains"] = [
        {"id": brain["id"], "name": brain.get("name", ""), "state": "private"}
        if brain["id"] in private
        else brain
        for brain in brains
    ]
    return result


def turn_private_brains(
    folders: Iterable[Path | str],
    *,
    provider: dict[str, Any],
    data_dir: Path,
    approved: Iterable[Path | str | None] = (),
) -> list[tuple[str, Path]]:
    """Private brains a turn with ``provider`` must not see in its folders.

    ``data_dir`` holds the library, Rem's settings and the provider profiles. A provider
    on localhost may read every brain. Others are refused a folder that is or sits
    inside a private brain (PrivateBrainError) and get back the private brains inside
    their folders, which the turn must hide or refuse. Read again for every turn, so
    turning the setting off stops a running thread at its next turn.
    """
    if provider_is_local(provider, data_dir):
        return []
    return private_brain_folders(
        folders,
        registered_brains(data_dir),
        approved=[*approved, memory_folder(data_dir)],
    )
