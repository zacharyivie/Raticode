"""Project existing Rem events into shared conversation message shapes."""

from typing import Any


def project_device_turn(
    request: str, events: list[dict[str, Any]], state: str
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    group = f"phone-thoughts-{request}"
    terminal = False
    changes = None
    for event in events:
        kind = event.get("type")
        sequence = event.get("sequence", 0)
        base = {
            "id": f"phone-{request}-{sequence}",
            "role": "assistant",
            "deviceRequestId": request,
            "deviceSequence": sequence,
        }
        if kind == "thought" and event.get("text"):
            delta = event.get("deltaStreamId")
            if (
                delta
                and messages
                and messages[-1].get("kind") == "thought"
                and messages[-1].get("deltaStreamId") == delta
            ):
                messages[-1]["body"] += event["text"]
                continue
            messages.append(
                {
                    **base,
                    "body": event["text"],
                    "kind": "thought",
                    "groupId": group,
                    "deltaStreamId": delta,
                    "trace": event.get("trace"),
                }
            )
        elif kind == "changes":
            changes = event.get("changes")
        elif kind == "final":
            terminal = True
            body = event.get("message", {}).get("body", "")
            # The plain-text mirror uses this same ID, including older turns.
            messages.append({**base, "id": f"reply-{request}", "body": body, "kind": "final"})
            messages.append(
                {
                    **base,
                    "id": f"phone-summary-{request}",
                    "body": "",
                    "kind": "turn-summary",
                    "running": False,
                    "completedAt": event.get("completedAt"),
                    "durationMs": event.get("durationMs"),
                    "changes": event.get("changes") or changes,
                    "deviceSequence": sequence + 0.5,
                }
            )
        elif kind in {"error", "stopped"}:
            terminal = True
            messages.append(
                {
                    **base,
                    "kind": "error" if kind == "error" else "system",
                    "body": event.get("error") or "Rem stopped.",
                }
            )
        elif kind == "compaction":
            messages.append(
                {
                    **base,
                    "role": "system",
                    "kind": "system",
                    "body": event.get("message") or "Rem context compacted.",
                }
            )
    if events and not any(m["id"] == f"phone-summary-{request}" for m in messages):
        messages.append(
            {
                "id": f"phone-summary-{request}",
                "role": "assistant",
                "body": "",
                "kind": "turn-summary",
                "running": not terminal and state in {"accepted", "running", "cancel_requested"},
                "deviceRequestId": request,
                "deviceSequence": float(len(events) + 1),
                "changes": changes,
            }
        )
    return messages
