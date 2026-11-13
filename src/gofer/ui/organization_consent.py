"""Approve several of Rem's consent requests in one user action ("Approve all").

The user confirms a list the window or phone showed them, and only those request IDs are
approved. A request that arrives after the list was shown is never in it. Each request
is approved through the same path as its own Approve button (``_decide``), one at a time,
re-reading the organization before each so revision checks see every earlier approval.

Order matters because some requests carry the revision Rem saw when it asked:
configuration changes (configure, restore, resume_all) are bound to the organization's
revision and ticket changes to the ticket's. Changes that only edit come first, oldest
revision first; starting the organization comes last, and only when everything before it
applied, so it never starts on settings the user meant to change first.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gofer.ui.organizations import OrganizationManager

# Requests bound to the organization's revision: applying one changes it for the rest.
ORGANIZATION_BOUND = {"configure", "restore", "resume_all"}
# Requests bound to one ticket's revision.
TICKET_BOUND = {"task_update", "approve", "review", "reopen"}
# Requests that start or end work; they go after every change they might depend on.
LAST = {"control": 1, "archive": 2}
# At most this many requests wait per organization (organizations._request_consent).
MAX_BATCH = 20

# A check run before each approval. It returns why this request must be skipped, or "".
Guard = Callable[[dict[str, Any], dict[str, Any]], str]


def _age_ok(request: dict[str, Any], ttl_seconds: float) -> bool:
    try:
        asked = datetime.fromisoformat(str(request.get("at")))
    except ValueError:
        return False
    return (datetime.now(UTC) - asked).total_seconds() < ttl_seconds


def approval_order(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The safe order to approve ``requests`` in.

    Edits first: organization-bound changes by the revision they expect, then by when
    Rem asked; everything else by when Rem asked. Starting work, then archiving, last.
    """

    def key(request: dict[str, Any]) -> tuple[int, int, int, str]:
        action = request.get("action")
        params = request.get("params") or {}
        expected = params.get("expectedRevision")
        if action in LAST:
            return (2, LAST[str(action)], 0, str(request.get("at")))
        if action in ORGANIZATION_BOUND:
            return (0, 0, expected if isinstance(expected, int) else 0, str(request.get("at")))
        return (1, 0, 0, str(request.get("at")))

    return sorted(requests, key=key)


def conflict(org: dict[str, Any], request: dict[str, Any]) -> str:
    """Why ``request`` can no longer apply as Rem asked, or "".

    A configuration request replaces the whole configuration Rem saw. Applying it after
    the organization moved on would undo the changes made since, so it is left waiting.
    """
    action = request.get("action")
    params = request.get("params") or {}
    expected = params.get("expectedRevision")
    if action in ORGANIZATION_BOUND and isinstance(expected, int):
        if expected != org["revision"]:
            return (
                f"Rem asked at revision {expected}, and the organization is now at revision "
                f"{org['revision']}. Left waiting: decline it and ask Rem again."
            )
    if action in TICKET_BOUND and isinstance(expected, int):
        task = next((t for t in org["runtime"]["tasks"] if t["id"] == params.get("taskId")), None)
        if task is None:
            return "Its ticket no longer exists. Left waiting: decline it."
        if task["revision"] != expected:
            return "Its ticket changed after Rem asked. Left waiting: decline it and ask Rem again."
    return ""


def approve_all(
    manager: OrganizationManager,
    project: str,
    organization_id: str,
    request_ids: Any,
    *,
    ttl_seconds: float,
    guard: Guard | None = None,
    decider: str = "user",
    instruction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Approve exactly ``request_ids`` in a safe order and report each one.

    ``decider`` is "user" for the user's own click. Rem approving because the user told
    it to in chat (organization_auto.approve_on_instruction) passes its own decider and
    the user's message in ``instruction``, and uses this same order and these checks.

    Each result is ``approved``, ``failed`` (approving it was tried and refused; the
    request is closed, as with its own Approve button) or ``skipped`` (not tried; a
    request still waiting stays waiting).
    """
    if (
        not isinstance(request_ids, list)
        or not request_ids
        or not all(isinstance(item, str) and item for item in request_ids)
    ):
        raise ValueError("Choose the requests to approve")
    if len(request_ids) > MAX_BATCH or len(set(request_ids)) != len(request_ids):
        raise ValueError(f"Choose at most {MAX_BATCH} different requests")
    org = manager.store.get(project, organization_id)
    stored = org["runtime"].get("consentRequests") or {}
    results: dict[str, dict[str, Any]] = {}
    chosen = []
    for rid in request_ids:
        request = stored.get(rid)
        if request is None:
            results[rid] = {"status": "skipped", "message": "Not a request of this organization."}
        elif request["status"] != "pending" or not _age_ok(request, ttl_seconds):
            results[rid] = {
                "summary": request.get("summary", ""),
                "status": "skipped",
                "message": "No longer waiting: it was decided already or is more than a day old.",
            }
        else:
            chosen.append(request)
    # Anything the user confirmed that didn't apply holds back starting the organization.
    held_back = len(chosen) < len(request_ids)
    for request in approval_order(chosen):
        rid = request["id"]
        entry: dict[str, Any] = {"summary": request.get("summary", "")}
        results[rid] = entry
        # Re-read before every request: the previous approval may have changed revisions.
        current = manager.store.get(project, organization_id)
        live = (current["runtime"].get("consentRequests") or {}).get(rid) or {}
        if live.get("status") != "pending":
            entry.update(status="skipped", message="Decided elsewhere while these were applied.")
            held_back = True
            continue
        if request.get("action") in LAST and held_back:
            entry.update(
                status="skipped",
                message=(
                    "Not applied, because another request in this list didn't apply. "
                    "Check the organization, then approve this one by itself."
                ),
            )
            continue
        reason = conflict(current, request) or (guard(current, request) if guard else "")
        if reason:
            entry.update(status="skipped", message=reason)
            held_back = True
            continue
        try:
            if decider == "user":
                manager.call(
                    project,
                    "user",
                    {
                        "action": "request_decide",
                        "organizationId": organization_id,
                        "params": {"requestId": rid, "decision": "approve"},
                    },
                )
            else:
                manager._decide(
                    project,
                    current,
                    {"requestId": rid, "decision": "approve"},
                    decider=decider,
                    instruction=instruction,
                )
        except ValueError as exc:
            entry.update(status="failed", message=str(exc))
            held_back = True
            continue
        entry.update(status="approved", message="")
    ordered = [
        {"requestId": rid, **results[rid]}
        for rid in [r["id"] for r in approval_order(chosen)]
        + [rid for rid in request_ids if rid not in {r["id"] for r in chosen}]
    ]
    return {
        "results": ordered,
        "approved": sum(item["status"] == "approved" for item in ordered),
        "organization": manager.public(manager.store.get(project, organization_id)),
    }
