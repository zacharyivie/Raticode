"""Self-work's decision guardrails: work that breaks a decision in force never goes ahead.

The user's decisions (``rem_decisions``) bind Rem's self-work. Four checks hold that line
in code, with no model involved:

1. Authoring: a ticket a research run proposes is not saved when it contradicts a
   decision in force (``proposal_hits``). The rejection is recorded with the decision IDs.
2. Pulling from Backlog: a ticket that contradicts one is never moved to To do. It goes
   to Blocked, marked "Violates decisions" with the IDs and the reason (``marker``), and
   stays out of self-work's picks until the user changes the ticket or a decision
   (``still_marked``).
3. Starting a ticket, from To do or because someone moved it to In progress: checked
   again with the claim; a ticket that contradicts one is blocked the same way and no
   worker starts.
4. Review: the check phase fails work done for a ticket that contradicts a decision
   (``rem_home._decision_check``), beside the record checks it already makes.

Self-work also can't make itself an exception. Only the user changes a decision
(``rem_decisions.change``). A request Rem files that would weaken a decision in force,
because it names one, uses a record's signal words, covers paths a record forbids or asks
for an exception, waits for the user as a conflict even with auto mode on (``weakens``).

What counts as a contradiction: the ticket (or proposal) names the decision in
``conflicts_with``, or its text contains a signal word of a decision record in force.
Plain decisions without a record have no signal words, so only a named conflict counts
for them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from gofer.ui import rem_decision_records as records

# Words in a decision request that ask to set a decision in force aside.
EXCEPTION_WORDS = re.compile(
    r"\b(?:exceptions?|exempt(?:s|ed|ion)?|waive[ds]?|waiving|waivers?|overrides?|overriding|"
    r"bypass(?:es|ed|ing)?|carve[- ]?outs?|except (?:for|when|in|where)|notwithstanding|"
    r"suspend(?:s|ed|ing)? (?:the )?decision|ignore (?:the )?decision)\b",
    re.IGNORECASE,
)
DECISION_ID = re.compile(r"\bD-\d+\b")
REASON_CHARS = 1000


def in_force(book: dict[str, Any], project: str | None) -> list[dict[str, Any]]:
    """The decisions in force for one project."""
    from gofer.ui import rem_decisions as decision_book

    key = decision_book.project_key(project)
    if key is None:
        return []
    return [
        i
        for i in book["items"]
        if i.get("project") == key and i["status"] in decision_book.IN_FORCE
    ]


def decisions_key(decided: list[dict[str, Any]]) -> str:
    """Names the decisions in force as they read now, so a marker knows when one changed."""
    body = json.dumps(
        sorted(
            [
                str(i["id"]),
                str(i["status"]),
                str(i.get("answer") or ""),
                records.record_of(i)["signals"],
            ]
            for i in decided
        )
    )
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _text(title: str, description: str, done_when: str = "") -> str:
    return f"{title}\n{description}\n{done_when}"


def _hits(text: str, named: list[str], decided: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Each decision in force the text contradicts, with why: ``{id, title, why}``."""
    by_id = {i["id"]: i for i in decided}
    found: dict[str, dict[str, str]] = {}
    for identity in named:
        item = by_id.get(identity)
        if item is not None and identity not in found:
            found[identity] = {
                "id": identity,
                "title": str(item.get("question") or ""),
                "why": f"it says itself that it contradicts {identity}",
            }
    for item in decided:
        if item.get("remember_as") != "record" or item["id"] in found:
            continue
        words = [s for s in records.record_of(item)["signals"] if records.signal_in(s, text)]
        if words:
            listed = ", ".join(f"“{w}”" for w in words[:3])
            found[item["id"]] = {
                "id": item["id"],
                "title": str(item.get("question") or ""),
                "why": f"it mentions {listed}, which {item['id']} names as a sign of "
                "contradicting it",
            }
    return list(found.values())


def proposal_hits(proposal: Any, decided: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What a research run's proposal contradicts; any hit means it isn't saved."""
    done_when = getattr(proposal, "done_when", []) or []
    return _hits(
        _text(proposal.title, proposal.description, "\n".join(done_when)),
        list(getattr(proposal, "conflicts_with", []) or []),
        decided,
    )


def _named(ticket: dict[str, Any]) -> list[str]:
    """Decisions a ticket's proposal said it contradicts. The user approving such a
    proposal by hand was their own call, so it no longer counts."""
    proposal = ticket.get("proposal") or {}
    if proposal.get("state") == "approved" and proposal.get("by") == "user":
        return []
    return [str(x) for x in proposal.get("conflicts_with") or []]


def ticket_hits(ticket: dict[str, Any], decided: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What a board ticket contradicts; any hit means self-work never starts it."""
    return _hits(
        _text(ticket["title"], ticket.get("description", ""), str(ticket.get("done_when") or "")),
        _named(ticket),
        decided,
    )


def ticket_key(ticket: dict[str, Any]) -> str:
    """Changes when the user edits what the ticket asks for."""
    body = json.dumps(
        [
            ticket["title"],
            ticket.get("description", ""),
            str(ticket.get("done_when") or ""),
            ticket.get("project_dir"),
            _named(ticket),
        ]
    )
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def reason(hits: list[dict[str, str]]) -> str:
    """The sentence a blocked ticket shows."""
    parts = [
        f"{h['id']} ({h['title']}): {h['why']}" if h["title"] else f"{h['id']}: {h['why']}"
        for h in hits[:5]
    ]
    text = (
        "Violates decisions: "
        + "; ".join(parts)
        + ". Self-work won't start it. Change the ticket, or change the decision in Decisions, "
        "and Rem checks it again."
    )
    return text[:REASON_CHARS]


def marker(
    ticket: dict[str, Any],
    hits: list[dict[str, str]],
    decided: list[dict[str, Any]],
    before: str,
    stamp: str,
) -> dict[str, Any]:
    """What a blocked ticket keeps: the IDs, the reason and what it was checked against."""
    earlier = ticket.get("violates_decisions") or {}
    return {
        "ids": [h["id"] for h in hits],
        "reason": reason(hits),
        # Where it goes back to once it no longer contradicts anything.
        "from": earlier.get("from") or (before if before in {"backlog", "todo"} else "backlog"),
        "at": earlier.get("at") or stamp,
        "ticket_key": ticket_key(ticket),
        "decisions_key": decisions_key(decided),
    }


def still_marked(ticket: dict[str, Any], decided: list[dict[str, Any]]) -> bool:
    """Whether a marked ticket is unchanged since it was marked, ticket and decisions both,
    so checking it again would only find the same thing."""
    mark = ticket.get("violates_decisions") or {}
    return bool(
        mark
        and mark.get("ticket_key") == ticket_key(ticket)
        and mark.get("decisions_key") == decisions_key(decided)
    )


def _request_text(request: Any) -> str:
    rec = request.recommendation
    parts = [
        request.topic.replace("-", " ").replace(".", " "),
        request.question,
        rec.answer if rec else "",
        rec.why if rec else "",
        request.tried,
    ]
    if request.record is not None:
        parts += [
            request.record.context,
            request.record.options,
            request.record.consequences,
            request.record.check,
        ]
    return "\n".join(str(p or "") for p in parts)


def _overlap(globs: list[str], forbids: list[str]) -> bool:
    return any(
        records.glob_match(g, f) or records.glob_match(f, g) or g == f
        for g in globs
        for f in forbids
    )


def weakens(request: Any, decided: list[dict[str, Any]]) -> tuple[list[str], str]:
    """Whether a decision request would set a decision in force aside; ``(ids, why)``.

    It does when it names a decision in force, uses a record's signal words, covers paths
    a record forbids, or asks for an exception, waiver, override or bypass. Such a request
    is never Rem's own to apply: it waits for the user, auto mode or not. ``([], "")``
    when there are no decisions in force or none of that is true.
    """
    if not decided:
        return [], ""
    text = _request_text(request)
    ids = {i["id"] for i in decided}
    found: dict[str, None] = dict.fromkeys(x for x in DECISION_ID.findall(text) if x in ids)
    found.update(
        dict.fromkeys(
            records.signal_hits([i for i in decided if i.get("remember_as") == "record"], text)
        )
    )
    if request.record is not None:
        mine = [*request.record.applies_to, *request.record.forbids]
        for item in decided:
            if item.get("remember_as") == "record" and _overlap(
                mine, records.record_of(item)["forbids"]
            ):
                found[item["id"]] = None
    asks = EXCEPTION_WORDS.search(text)
    if not found and not asks:
        return [], ""
    listed = ", ".join(found)
    why = (
        f"It would weaken {listed}, a decision in force"
        if listed
        else f"It asks for an exception (“{asks.group(0) if asks else ''}”) while decisions "
        "are in force"
    )
    return list(found), (
        why + ". Self-work can't make exceptions to decisions for itself, so this waits for "
        "you, even with auto mode on. Use this to add it, or turn it down to keep the "
        "decisions as they are."
    )
