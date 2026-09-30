"""Routine-scoped HMAC webhooks. A receipt can enqueue only its configured template."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

from gofer.devices.storage import OSSecretStore
from gofer.ui.organization_operations import trigger_routine
from gofer.ui.organization_store import OrganizationConflict, now


def configure(manager: Any, org: dict[str, Any], params: dict[str, Any], actor: str) -> Any:
    def save(current: dict[str, Any]) -> Any:
        routine = current["runtime"].get("routines", {}).get(params.get("routineId"))
        if not routine or routine["revision"] != params.get("expectedRevision"):
            raise OrganizationConflict("Routine changed; reload before changing its webhook")
        reference = params.get("secretRef")
        secret = current["runtime"].get("secrets", {}).get(reference)
        if not secret or secret["revoked"]:
            raise ValueError("Select an available OS keyring secret reference")
        current["runtime"].setdefault("webhooks", {})[routine["id"]] = {
            "secretRef": reference,
            "enabled": params.get("enabled") is True,
        }
        routine["revision"] += 1
        return {"routineId": routine["id"], "enabled": params.get("enabled") is True}

    result = manager.store.mutate(org["projectRoot"], org["id"], actor, "webhook_configured", save)
    return manager.public(result)


def receive(manager: Any, oid: str, rid: str, headers: Any, raw: bytes) -> dict[str, Any]:
    if len(raw) > 65536:
        raise ValueError("Webhook exceeds 64 KiB")
    timestamp, delivery = (
        headers.get("X-Raticode-Timestamp", ""),
        headers.get("X-Raticode-Delivery", ""),
    )
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or abs(time.time() - int(timestamp)) > 300:
        raise ValueError("Webhook timestamp is outside the five-minute window")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", delivery):
        raise ValueError("Supply a bounded webhook delivery ID")
    org = manager.store.get("", oid)
    hook = org["runtime"].get("webhooks", {}).get(rid)
    reference = org["runtime"].get("secrets", {}).get((hook or {}).get("secretRef"))
    if not hook or not hook["enabled"] or not reference or reference["revoked"]:
        raise ValueError("Webhook unavailable")
    secret = OSSecretStore(service="Raticode organization secrets").get(reference["account"])
    if not secret:
        raise ValueError("Webhook credential unavailable")
    signed = timestamp.encode() + b"." + delivery.encode() + b"." + raw
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    supplied = headers.get("X-Raticode-Signature", "")
    if not hmac.compare_digest(supplied.encode(), ("sha256=" + signature).encode()):
        raise ValueError("Webhook signature is invalid")
    if not isinstance(json.loads(raw), dict):
        raise ValueError("Webhook body must be a JSON object")
    digest = hashlib.sha256(raw).hexdigest()
    receipt: dict[str, Any] = {}

    def enqueue(current: dict[str, Any]) -> Any:
        if (
            current["runtime"].get("webhooks", {}).get(rid) != hook
            or current["runtime"].get("secrets", {}).get(hook["secretRef"]) != reference
        ):
            raise OrganizationConflict("Webhook credential changed; retry with its current key")
        receipts = current["runtime"].setdefault("webhookReceipts", {})
        key = rid + ":" + delivery
        if key in receipts:
            if receipts[key]["sha256"] != digest:
                raise OrganizationConflict("Webhook delivery ID reused with another body")
            receipt.update(receipts[key])
            return {}
        instant = time.time()
        if sum(r.get("receivedEpoch", 0) > instant - 60 for r in receipts.values()) >= 60:
            raise ValueError("Webhook rate exceeded; retry later with the same delivery ID")
        routine = current["runtime"].get("routines", {}).get(rid)
        if not routine or not routine["enabled"]:
            raise ValueError("Routine is disabled")
        task = trigger_routine(current, routine, "webhook:" + delivery)
        receipt.update(
            id=key,
            taskId=None if task.get("skipped") else task["id"],
            skipped=bool(task.get("skipped")),
            sha256=digest,
            receivedEpoch=instant,
            at=now(),
        )
        receipts[key] = dict(receipt)
        return receipt

    manager.store.mutate(org["projectRoot"], oid, "webhook:" + rid, "webhook_received", enqueue)
    manager._wake.set()
    return receipt
