"""How big each part of a Rem turn's prompt is, so a thread can show what it sends.

When Rem builds a turn's prompt it measures each part (identity, CLI and docs context,
skill index, resources, Second Brain rules, report theme, delegation background, user
memories, the editor context and the conversation) and the usage ledger keeps the sizes
with the turn. Only character counts and an estimated token count (characters / 4) are
stored, never the text.

A resumed provider session already holds the instructions and editor context it saw last
time, so a turn that doesn't resend them records those parts as not sent.

Rem's own tool-free calls (chat replies, answers to workers, conversation decisions, task
updates, self-work planning and organization request reviews) are recorded the same way
under a ``call`` kind. Their parts are the fixed rules, the rules for that call, each
top-level record of the prompt's JSON (``payload_sizes``) and what a lookup, read or
repair added before the next ask. They have no session, so every part is always sent.

Each of Rem's calls also records why it was asked (``ATTEMPTS``): the first ask, a re-ask
because the reply wasn't the requested JSON, a repair of fields that didn't validate, or
an ask after a read or lookup. A call whose answer still couldn't be used is marked
failed. Re-asks and failures are counted per call kind, so a prompt that often needs a
second ask stands out. Each call also keeps the version of its fixed rules
(``rules_version``), so the counts split by version show what a rules change did.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Every part in the order a prompt has them. "standing" parts repeat from turn to turn;
# the conversation is what the user and the agent wrote.
PARTS: tuple[tuple[str, str], ...] = (
    ("identity", "Identity and task instructions"),
    ("tools", "Tool settings and guidance"),
    ("commit", "Commit message format"),
    ("cli_docs", "CLI and docs context"),
    ("skills", "Skill index"),
    ("resources", "Resources"),
    ("brain", "Second Brain rules"),
    ("theme", "Report theme"),
    ("background", "Delegation background"),
    ("background_file", "Delegation background file"),
    ("memories", "User memories"),
    ("rules", "Standing rules"),
    ("editor", "Editor context"),
    ("conversation", "Conversation"),
)
LABELS = dict(PARTS)
# Parts a turn is handed as a file it opens when it needs them, never in its prompt: a
# delegated worker's large background (rem_worker_background). Recorded unsent.
FILE_PARTS = frozenset({"background_file"})
STANDING = frozenset(key for key, _ in PARTS if key != "conversation")
# Parts that travel in the instructions, which a resumed session may skip.
INSTRUCTION_PARTS = frozenset(STANDING - {"editor"} - FILE_PARTS)
ESTIMATE_NOTE = "Token counts are estimates: characters divided by 4."
# Turns older than this are removed when a new one is saved.
KEEP_DAYS = 90
_THREAD_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,200}$")

# Rem's own tool-free calls, by the kind each is recorded under.
REM_CALLS: dict[str, str] = {
    "chat_reply": "Chat reply",
    "worker_answer": "Answer to a worker",
    "conversation": "Conversation decision",
    "task_update": "Task update",
    "self_work_plan": "Self-work objective planning",
    "self_work_fit": "Self-work objective check",
    "self_work_stop": "Self-work stop check",
    "objective_ideas": "Ask Rem for a project's objectives",
    "organization": "Organization request review",
}
# Kinds no longer recorded, still named in the records kept from before (KEEP_DAYS).
RETIRED_CALLS: dict[str, str] = {"self_work": "Self-work checks (before they were split)"}
# Every kind a kept record can name: what views read; new records take REM_CALLS only.
CALL_LABELS: dict[str, str] = {**REM_CALLS, **RETIRED_CALLS}
# Why one of Rem's calls was asked. A re-ask (REASKS) corrects an answer that couldn't be
# used; a read is the planned next ask after a recall read or lookup, not a correction.
ATTEMPTS: dict[str, str] = {
    "first": "First ask",
    "reformat": "Asked again for the JSON format",
    "repair": "Asked again to repair invalid fields",
    "read": "Asked again after a read",
}
REASKS = frozenset({"reformat", "repair"})
# Names for the parts Rem's own calls send. A prompt record without one is named after
# its key, so a new record shows up without a change here.
REM_CALL_LABELS: dict[str, str] = {
    "rules": "Fixed rules",
    "turn_rules": "Rules for this call",
    "added": "Lookups, reads and repairs",
    "other": "Separators",
    "prompt": "Prompt",
    "current_request": "Current request",
    "history": "Recent chat history",
    "recalled_context.history": "Recalled messages",
    "recalled_context.threads": "Recalled thread excerpts",
    "recalled_context.linked_threads": "Linked threads",
    "recalled_context.lookups": "Lookup results",
    "related_history_and_threads": "Recalled history and threads",
    "projects": "Open projects",
    "tasks": "Open tasks",
    "board": "Board tickets",
    "memories": "Memories",
    "workspace.desktop": "Desktop thread list",
    "workspace.knowledge": "Second Brain notes",
    "workspace.workflows": "Workflows",
    "workspace.running_threads": "Running threads",
    "workspace.organizations": "Organizations",
    "decisions": "Decisions",
    "waiting_decisions": "Decisions waiting for the user",
    "self_work": "Self-work state",
    "organization_requests": "Organization requests",
    "delegated_context": "Delegation background",
    "receipts": "Tool receipts",
    "dialogue": "Task conversation",
    "goal": "Task goal",
    "criteria": "Acceptance criteria",
    "question": "Worker's question",
    "worker_reply": "Worker's reply",
    "worker_result": "Worker's result",
}
# What the call answers, or text it gained while running, rather than records that
# repeat from call to call.
REM_CALL_CONVERSATION = frozenset(
    {"current_request", "history", "added", "question", "worker_reply", "dialogue"}
    | {"worker_result", "request"}
)
# Records whose own keys are measured one level down, so the biggest of them stand out.
SPLIT_RECORDS = frozenset({"workspace", "recalled_context"})
_PART_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}(\.[A-Za-z0-9_]{1,60})?$")
# How many hex characters of the fixed rules' SHA-256 name their version.
RULES_VERSION_CHARS = 12
_RULES_VERSION = re.compile(rf"^[0-9a-f]{{{RULES_VERSION_CHARS}}}$")


def rules_version(system: str) -> str | None:
    """The version of a call's fixed rules: the first 12 hex characters of their SHA-256.

    None for a call with no fixed rules. Only the version is stored, never the text, so
    any change to the rules starts a new version in the counts (``rem_calls``).
    """
    if not system:
        return None
    return hashlib.sha256(system.encode("utf-8")).hexdigest()[:RULES_VERSION_CHARS]


def estimate_tokens(chars: int) -> int:
    """The estimate shown with every size: characters / 4, rounded up."""
    return (max(0, int(chars)) + 3) // 4


def measure(
    sizes: dict[str, int], *, instructions_sent: bool = True, editor_sent: bool = True
) -> list[dict[str, Any]]:
    """The parts of one turn, in prompt order, from their character counts.

    Parts with no characters are left out. ``instructions_sent`` and ``editor_sent`` are
    False when a resumed session skipped them.
    """
    parts = []
    for key, _label in PARTS:
        chars = int(sizes.get(key) or 0)
        if chars <= 0:
            continue
        sent = (
            instructions_sent
            if key in INSTRUCTION_PARTS
            else editor_sent
            if key == "editor"
            else key not in FILE_PARTS
        )
        parts.append(
            {"key": key, "chars": chars, "tokens_estimate": estimate_tokens(chars), "sent": sent}
        )
    return parts


def _known_part(key: Any, call: str | None) -> bool:
    if call is None:
        return key in LABELS
    return isinstance(key, str) and bool(_PART_KEY.match(key))


def part_label(key: str, call: str | None = None) -> str:
    """A part's name: from PARTS for a thread's turn, else Rem's call parts."""
    if call is None:
        return LABELS.get(key, key)
    if key in REM_CALL_LABELS:
        return REM_CALL_LABELS[key]
    words = key.replace(".", ": ").replace("_", " ")
    return words[:1].upper() + words[1:]


def _standing(key: str, call: str | None) -> bool:
    return key in STANDING if call is None else key not in REM_CALL_CONVERSATION


def payload_sizes(
    payload: Mapping[str, Any],
    encode: Callable[[Any], str],
    *,
    split: frozenset[str] = SPLIT_RECORDS,
) -> dict[str, int]:
    """Each top-level record's characters in ``encode(payload)``, sizes only.

    A record counts its key, the ": " after it and its value. Records named in ``split``
    are measured by their own keys instead (``workspace.desktop``). What the sizes leave
    out of the whole, the braces and separators, is ``other``, so the parts add up to
    ``len(encode(payload))``.
    """
    sizes: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not _PART_KEY.match(key) or "." in key:
            continue
        if key in split and isinstance(value, Mapping) and value:
            for child, inner in value.items():
                name = f"{key}.{child}"
                if isinstance(child, str) and _PART_KEY.match(name):
                    sizes[name] = len(encode(child)) + 2 + len(encode(inner))
            continue
        sizes[key] = len(encode(key)) + 2 + len(encode(value))
    sizes["other"] = max(0, len(encode(dict(payload))) - sum(sizes.values()))
    return sizes


def measured_prompt(
    rules: str, payload: Mapping[str, Any], encode: Callable[[Any], str]
) -> tuple[str, dict[str, int]]:
    """``rules`` then ``encode(payload)`` on the next line, as Rem's calls build a prompt.

    Returns the prompt, unchanged from building it by hand, and each part's size: the
    rules as ``turn_rules`` and the records from ``payload_sizes``.
    """
    head = rules + "\n" if rules else ""
    body = encode(dict(payload))
    sizes = {"turn_rules": len(head), **payload_sizes(payload, encode)}
    return head + body, sizes


def rem_call_parts(
    prompt: str, system: str, sizes: Mapping[str, int] | None = None
) -> list[dict[str, Any]]:
    """The parts of one of Rem's own calls, biggest record first.

    ``sizes`` are the parts of the prompt as first built (``measured_prompt``); whatever
    the prompt gained since, from a lookup, read or repair note, is ``added``. Without
    ``sizes`` the prompt is one part. Rem's calls have no session, so all are sent.
    """
    measured = {key: max(0, int(value)) for key, value in (sizes or {}).items()}
    if not measured:
        measured = {"prompt": len(prompt)}
    added = len(prompt) - sum(measured.values())
    if added > 0:
        measured["added"] = added
    measured = {"rules": len(system), **measured}
    return [
        {"key": key, "chars": chars, "tokens_estimate": estimate_tokens(chars), "sent": True}
        for key, chars in sorted(measured.items(), key=lambda item: -item[1])
        if chars > 0 and _PART_KEY.match(key)
    ]


def record_turn_context(
    db: sqlite3.Connection,
    *,
    invocation_id: str,
    parts: list[dict[str, Any]],
    resumed: bool,
    provider: str | None,
    model: str | None,
    thread_id: str | None,
    run_id: str | None,
    call: str | None = None,
    attempt: str = "first",
    rules_version: str | None = None,
) -> None:
    """Save one turn's part sizes in the usage ledger, replacing a replayed turn.

    ``call`` names the kind of one of Rem's own calls (REM_CALLS); None is a thread's turn.
    ``attempt`` (ATTEMPTS) says why a call was asked and ``rules_version`` which fixed
    rules it was sent with (see ``rules_version``); a thread's turn has neither.
    """
    if call is not None and call not in REM_CALLS:
        raise ValueError(f"Unknown Rem call kind '{call}'")
    if attempt not in ATTEMPTS:
        raise ValueError(f"Unknown attempt kind '{attempt}'")
    if rules_version is not None and not _RULES_VERSION.match(rules_version):
        raise ValueError("Invalid rules version")
    stored = [
        {
            "key": part["key"],
            "chars": int(part["chars"]),
            "tokens_estimate": estimate_tokens(int(part["chars"])),
            "sent": bool(part.get("sent", True)),
        }
        for part in parts
        if _known_part(part.get("key"), call)
        and isinstance(part.get("chars"), int)
        and not isinstance(part.get("chars"), bool)
    ]
    observed = datetime.now(UTC)
    db.execute(
        """INSERT INTO usage_turn_context (id, thread_id, run_id, provider, model,
        observed_at, resumed, parts, call, attempt, rules_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET parts=excluded.parts, resumed=excluded.resumed,
        rules_version=excluded.rules_version""",
        (
            invocation_id,
            thread_id or None,
            run_id or None,
            provider,
            model,
            observed.isoformat(),
            int(resumed),
            json.dumps(stored),
            call,
            attempt if call is not None else None,
            rules_version if call is not None else None,
        ),
    )
    db.execute(
        "DELETE FROM usage_turn_context WHERE observed_at < ?",
        ((observed - timedelta(days=KEEP_DAYS)).isoformat(),),
    )


def _attempt(row: Any) -> str:
    """Why a recorded call was asked; a call saved before attempts were kept was a first."""
    attempt = row["attempt"]
    return attempt if attempt in ATTEMPTS else "first"


def _turn_view(row: Any) -> dict[str, Any]:
    try:
        raw = json.loads(row["parts"])
    except (TypeError, ValueError):
        raw = []
    call = row["call"] if row["call"] in CALL_LABELS else None
    parts = []
    for part in raw if isinstance(raw, list) else []:
        if not isinstance(part, dict) or not _known_part(part.get("key"), call):
            continue
        chars = part.get("chars")
        if not isinstance(chars, int) or isinstance(chars, bool) or chars < 0:
            continue
        parts.append(
            {
                "key": part["key"],
                "label": part_label(part["key"], call),
                "chars": chars,
                "tokens_estimate": estimate_tokens(chars),
                "sent": part.get("sent") is not False,
                "standing": _standing(part["key"], call),
                **({"file": True} if call is None and part["key"] in FILE_PARTS else {}),
            }
        )
    sent = sum(p["chars"] for p in parts if p["sent"])
    return {
        "turn_id": row["id"],
        **(
            {
                "call": call,
                "call_label": CALL_LABELS[call],
                "attempt": _attempt(row),
                "attempt_label": ATTEMPTS[_attempt(row)],
                "failed": bool(row["failed"]),
                "rules_version": row["rules_version"],
            }
            if call
            else {}
        ),
        "observed_at": row["observed_at"],
        "provider": row["provider"],
        "model": row["model"],
        "run_id": row["run_id"],
        "resumed": bool(row["resumed"]),
        "parts": parts,
        "sent_chars": sent,
        "sent_tokens_estimate": estimate_tokens(sent),
        "total_chars": sum(p["chars"] for p in parts),
        "total_tokens_estimate": estimate_tokens(sum(p["chars"] for p in parts)),
    }


def thread_context(
    thread_id: str, data_dir: Path | None = None, *, limit: int = 10
) -> dict[str, Any]:
    """A thread's newest turns, newest first, each with its parts' sizes."""
    from gofer.core.usage_ledger import usage_db

    if not isinstance(thread_id, str) or not _THREAD_ID.match(thread_id):
        raise ValueError("Invalid thread ID")
    limit = max(1, min(int(limit), 100))
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT * FROM usage_turn_context WHERE thread_id=? AND call IS NULL "
            "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        count = db.execute(
            "SELECT COUNT(*) FROM usage_turn_context WHERE thread_id=? AND call IS NULL",
            (thread_id,),
        ).fetchone()[0]
    return {
        "thread_id": thread_id,
        "turn_count": int(count),
        "turns": [_turn_view(row) for row in rows],
        "estimate": ESTIMATE_NOTE,
        "kept_days": KEEP_DAYS,
    }


def standing_parts(
    data_dir: Path | None = None, *, days: int = 7, limit: int = 10
) -> dict[str, Any]:
    """The standing parts that sent the most text over the last ``days`` days.

    Ranked by the characters actually sent. A part a resumed session skipped counts
    toward its size but not toward what was sent. Parts of Rem's own calls rank with the
    threads' parts, each named with its call kind, and ``rem_calls`` sums each kind,
    with how many of its calls were re-asks (REASKS) and how many still failed.
    Each kind also sums the input, cache-read and cache-write tokens its providers
    reported (CALL_TOKENS), read from the token record that shares the size record's
    ID; ``reported_calls`` counts the calls with one, and a sum no call reported is None.
    ``turns`` counts the threads' turns and ``calls`` Rem's own calls.
    ``current_version`` is the rules version of a kind's newest call with its counts
    over every kept call (see ``rem_calls``' versions), or None when that call had no
    fixed rules or the kind's calls all came before versions were recorded.
    """
    from gofer.core.usage_ledger import usage_db

    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    totals: dict[tuple[str | None, str], dict[str, int]] = {}
    kinds: dict[str, dict[str, Any]] = {}
    turns = calls = 0
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT t.*, i.tokens AS invocation_tokens FROM usage_turn_context t "
            "LEFT JOIN usage_invocations i "
            "ON i.id = t.id AND t.call IS NOT NULL AND i.estimated = 0 "
            "WHERE t.observed_at >= ?",
            (cutoff,),
        ).fetchall()
        versions = _versions(db.execute(_VERSION_COUNTS.format(where="call IS NOT NULL")))
    for row in rows:
        view = _turn_view(row)
        call = view.get("call")
        if call:
            calls += 1
            kind = kinds.setdefault(
                call,
                {
                    "calls": 0,
                    "sent": 0,
                    "max": 0,
                    "reasked": 0,
                    "failed": 0,
                    "reported": 0,
                    **dict.fromkeys(CALL_TOKENS),
                },
            )
            kind["calls"] += 1
            kind["reasked"] += view["attempt"] in REASKS
            kind["failed"] += view["failed"]
            kind["sent"] += view["sent_chars"]
            kind["max"] = max(kind["max"], view["sent_chars"])
            tokens = _call_tokens(row["invocation_tokens"])
            if tokens:
                kind["reported"] += 1
                for field, value in tokens.items():
                    kind[field] = (kind[field] or 0) + value
        else:
            turns += 1
        for part in view["parts"]:
            if not part["standing"]:
                continue
            total = totals.setdefault(
                (call, part["key"]),
                {"turns": 0, "size": 0, "max": 0, "sent_turns": 0, "sent": 0},
            )
            total["turns"] += 1
            total["size"] += part["chars"]
            total["max"] = max(total["max"], part["chars"])
            if part["sent"]:
                total["sent_turns"] += 1
                total["sent"] += part["chars"]
    ranked = sorted(totals.items(), key=lambda item: (-item[1]["sent"], -item[1]["size"]))
    return {
        "days": days,
        "turns": turns,
        "calls": calls,
        "estimate": ESTIMATE_NOTE,
        "parts": [
            {
                "id": f"{call}:{key}" if call else key,
                "key": key,
                "label": f"{CALL_LABELS[call]}: {part_label(key, call)}"
                if call
                else part_label(key),
                **({"call": call, "call_label": CALL_LABELS[call]} if call else {}),
                "turns": total["turns"],
                "sent_turns": total["sent_turns"],
                "average_chars": round(total["size"] / total["turns"]),
                "average_tokens_estimate": estimate_tokens(round(total["size"] / total["turns"])),
                "max_chars": total["max"],
                "sent_chars": total["sent"],
                "sent_tokens_estimate": estimate_tokens(total["sent"]),
            }
            for (call, key), total in ranked[:limit]
        ],
        "rem_calls": [
            {
                "call": call,
                "label": CALL_LABELS[call],
                "calls": kind["calls"],
                "reasked": kind["reasked"],
                "failed": kind["failed"],
                "average_chars": round(kind["sent"] / kind["calls"]),
                "average_tokens_estimate": estimate_tokens(round(kind["sent"] / kind["calls"])),
                "max_chars": kind["max"],
                "max_tokens_estimate": estimate_tokens(kind["max"]),
                "sent_chars": kind["sent"],
                "sent_tokens_estimate": estimate_tokens(kind["sent"]),
                "reported_calls": kind["reported"],
                **{field: kind[field] for field in CALL_TOKENS},
                "current_version": _current_version(versions.get(call, [])),
            }
            for call, kind in sorted(kinds.items(), key=lambda item: -item[1]["sent"])
        ],
    }


# The token counts summed per kind of Rem's own call. input_tokens is all the input the
# provider read, cache reads and writes included (subscriptions.usage.normalize_usage).
CALL_TOKENS = ("input_tokens", "cache_read_tokens", "cache_write_tokens")


def _call_tokens(raw: Any) -> dict[str, int]:
    """The CALL_TOKENS a call's token record holds; empty without a record or counts."""
    try:
        tokens = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(tokens, dict):
        return {}
    return {
        field: value
        for field in CALL_TOKENS
        if isinstance(value := tokens.get(field), int)
        and not isinstance(value, bool)
        and value >= 0
    }


def rem_calls(
    data_dir: Path | None = None,
    *,
    limit: int = 10,
    call: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Rem's own newest calls, newest first, each with its parts' sizes.

    ``call`` keeps one kind (REM_CALLS, or a RETIRED_CALLS kind for the records kept from
    before it was split) and ``run_id`` the calls about one task run.
    ``by_kind`` counts every kept call that matches, per kind: all calls, the re-asks
    (REASKS: asked again because an answer couldn't be used), the failures (an answer
    still unusable) and each attempt kind (ATTEMPTS). Its ``versions`` split those calls
    by the fixed rules they were sent with, newest rules first: each version's calls,
    re-asks (``re_asked``), failures and when it was first and last seen. ``current``
    marks the version of the kind's newest call. Calls with no fixed rules, and those
    recorded before versions were, count under the version None.
    """
    from gofer.core.usage_ledger import usage_db

    if call is not None and call not in CALL_LABELS:
        raise ValueError(f"Unknown call kind; use one of: {', '.join(CALL_LABELS)}")
    if run_id is not None and not _THREAD_ID.match(run_id):
        raise ValueError("Invalid run ID")
    limit = max(1, min(int(limit), 100))
    where = "call IS NOT NULL"
    args: list[Any] = []
    if call is not None:
        where += " AND call=?"
        args.append(call)
    if run_id is not None:
        where += " AND run_id=?"
        args.append(run_id)
    with usage_db(data_dir) as db:
        rows = db.execute(
            f"SELECT * FROM usage_turn_context WHERE {where} "  # noqa: S608 - fixed clauses
            "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        counted = db.execute(_VERSION_COUNTS.format(where=where), args).fetchall()
    return {
        "call_count": sum(int(row["calls"]) for row in counted if row["call"] in CALL_LABELS),
        "by_kind": _by_kind(counted),
        "calls": [_turn_view(row) for row in rows],
        "kinds": dict(REM_CALLS),
        "estimate": ESTIMATE_NOTE,
        "kept_days": KEEP_DAYS,
    }


# Rem's calls counted per kind, attempt and rules version, with when each was seen.
_VERSION_COUNTS = (
    "SELECT call, attempt, rules_version, COUNT(*) AS calls, SUM(failed) AS failed, "
    "MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen "
    "FROM usage_turn_context WHERE {where} GROUP BY call, attempt, rules_version"
)


def _versions(counted: Any) -> dict[str, list[dict[str, Any]]]:
    """Per call kind, each rules version's counts, newest rules first (_VERSION_COUNTS).

    ``current`` marks the version whose call was seen last, the one the kind uses now.
    """
    found: dict[str, dict[str | None, dict[str, Any]]] = {}
    for row in counted:
        call = row["call"]
        if call not in CALL_LABELS:
            continue
        version = row["rules_version"] if row["rules_version"] else None
        entry = found.setdefault(call, {}).setdefault(
            version,
            {
                "version": version,
                "calls": 0,
                "re_asked": 0,
                "failed": 0,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            },
        )
        calls = int(row["calls"])
        entry["calls"] += calls
        entry["failed"] += int(row["failed"] or 0)
        if _attempt(row) in REASKS:
            entry["re_asked"] += calls
        entry["first_seen"] = min(entry["first_seen"], row["first_seen"])
        entry["last_seen"] = max(entry["last_seen"], row["last_seen"])
    result: dict[str, list[dict[str, Any]]] = {}
    for call, by_version in found.items():
        entries = sorted(
            by_version.values(),
            key=lambda entry: (entry["first_seen"], entry["last_seen"]),
            reverse=True,
        )
        newest = max(entries, key=lambda entry: entry["last_seen"])
        result[call] = [{**entry, "current": entry is newest} for entry in entries]
    return result


def _current_version(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The version a kind uses now, when its newest call had fixed rules."""
    current = next((entry for entry in versions if entry["current"]), None)
    if current is None or current["version"] is None:
        return None
    return {key: value for key, value in current.items() if key != "current"}


def _by_kind(counted: list[Any]) -> list[dict[str, Any]]:
    """Per call kind, most calls first: calls, re-asks, failures, each attempt kind and
    the same counts per rules version (_versions)."""
    versions = _versions(counted)
    kinds: dict[str, dict[str, Any]] = {}
    for row in counted:
        call = row["call"]
        if call not in CALL_LABELS:
            continue
        kind = kinds.setdefault(
            call,
            {
                "call": call,
                "label": CALL_LABELS[call],
                "calls": 0,
                "reasked": 0,
                "failed": 0,
                "attempts": dict.fromkeys(ATTEMPTS, 0),
                "versions": versions.get(call, []),
            },
        )
        attempt = _attempt(row)
        calls = int(row["calls"])
        kind["calls"] += calls
        kind["failed"] += int(row["failed"] or 0)
        kind["attempts"][attempt] += calls
        if attempt in REASKS:
            kind["reasked"] += calls
    return sorted(kinds.values(), key=lambda kind: (-kind["calls"], kind["call"]))


def safe_standing_parts(data_dir: Path | None = None) -> dict[str, Any] | None:
    """``standing_parts`` for pages that must still load when the ledger can't be read."""
    try:
        return standing_parts(data_dir)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not read turn context sizes (%s)", type(exc).__name__)
        return None
