"""A delegated worker's large recalled background, kept in a file it reads on demand.

A worker's instructions carry the background Rem recalled for it: the original request,
the user's memories, the project's decisions and every recalled chat message and thread
excerpt. The recalled part can run to tens of thousands of characters, sent again
whenever a provider session starts over. Past ``FILE_OVER`` characters it goes to a
private file in the run's own folder under Raticode's data folder, outside the project
and its worktree, and the instructions keep a short index of it plus the rule to open
the file before asking the user anything it might answer.

Antigravity is granted only the project folder, so it couldn't open the file; its
workers keep the whole background inline, as before (see ``RULES_FILE_READERS`` in
rem_home for the same limit on review rules).

A self-work research run's turns also get ``context-usage.json`` in the same folder: the
measured sizes of Rem's standing prompt parts and own calls (turn_context.standing_parts),
so research on token and context use starts from real numbers. Sizes only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from gofer.ui.rem_home_store import encode
from gofer.utils.atomic_output import atomic_binary_output, mkdir_without_links

# Recalled context that encodes to more than this many characters goes to the file.
FILE_OVER = 12_000
# How much of each recalled message's text its index entry shows.
PREVIEW_CHARS = 150
# The index stops listing messages past this many characters and counts the rest, so a
# background of many short messages can't grow an index as long as the file.
INDEX_LIMIT = 4_000
# Providers that can't open a file in Raticode's data folder.
INLINE_PROVIDERS = frozenset({"antigravity"})
# Where the file goes: the run's own folder for one role. Sandboxed Claude Code turns
# use the same folder as their scratch folder, which their sandbox can read, and
# scratch_cleanup removes one a crashed turn left behind.
RUN_FOLDER = ("rem-work", "turn-scratch")
# A self-work research run's turns also get the measured sizes of Rem's prompts in this
# file of the same folder, over this many days (write_context_usage).
CONTEXT_USAGE_FILE = "context-usage.json"
CONTEXT_USAGE_DAYS = 7


def run_folder(data_dir: Path, run_id: str, role: str) -> Path:
    """The run's private folder for one role, outside the project and its worktree."""
    return data_dir.joinpath(*RUN_FOLDER, f"{run_id}-{role}")


def needs_file(context: dict[str, Any], provider: str) -> bool:
    """Whether this background's recalled context is big enough to go to a file."""
    recalled = context.get("recalled_context")
    return provider not in INLINE_PROVIDERS and bool(recalled) and len(encode(recalled)) > FILE_OVER


def _date(message: dict[str, Any]) -> Any:
    return message.get("created_at") or message.get("createdAt") or message.get("completedAt")


def index(recalled: list[dict[str, Any]]) -> dict[str, Any]:
    """Each recalled message's ID, role, date and start, and each thread's ID and title."""
    messages: list[dict[str, Any]] = []
    threads: list[dict[str, Any]] = []
    left_out = 0
    used = 0
    for entry in recalled:
        for message in entry.get("history") or []:
            body = str(message.get("body") or "")
            item = {
                "id": message.get("id"),
                "role": message.get("role"),
                "date": _date(message),
                "start": body[:PREVIEW_CHARS],
            }
            size = len(encode(item)) + 2
            if left_out or used + size > INDEX_LIMIT:
                left_out += 1
                continue
            used += size
            messages.append(item)
        for thread in entry.get("threads") or []:
            threads.append({"thread_id": thread.get("thread_id"), "title": thread.get("title")})
    result: dict[str, Any] = {"messages": messages, "threads": threads}
    if left_out:
        result["more_messages_in_file"] = left_out
    return result


def write_file(folder: Path, text: str) -> Path:
    """Write ``text`` to a 0600 file in ``folder`` (made 0700), named by its content.

    Named by content, so a resumed session's instructions stay the same while the
    background does. The file replaces whatever has its name.
    """
    payload = text.encode("utf-8")
    return _write(folder, f"background-{hashlib.sha256(payload).hexdigest()[:16]}.json", payload)


def _write(folder: Path, name: str, payload: bytes) -> Path:
    """Write ``payload`` to a 0600 file ``name`` in ``folder`` (made 0700)."""
    mkdir_without_links(folder)
    folder.chmod(0o700)
    target = folder.resolve() / name
    with atomic_binary_output(target) as output:
        output.write(payload)
    return target


def write_context_usage(folder: Path, data_dir: Path) -> Path:
    """Write the measured sizes of Rem's prompts to ``CONTEXT_USAGE_FILE`` in ``folder``.

    It holds ``turn_context.standing_parts`` over CONTEXT_USAGE_DAYS: the standing prompt
    parts that sent the most text and each kind of Rem's own calls with its sizes and the
    cache tokens its providers reported. Sizes and fixed labels only, never prompt text.
    The name stays the same, so a resumed session's instructions do too. Raises OSError
    or sqlite3.Error when the usage ledger or the folder can't be used.
    """
    from gofer.core.turn_context import standing_parts

    usage = standing_parts(data_dir, days=CONTEXT_USAGE_DAYS)
    return _write(folder, CONTEXT_USAGE_FILE, encode(usage).encode("utf-8"))


def context_usage_rule(path: Path) -> str:
    """The instructions' line naming the context usage file."""
    return (
        f"\nMeasured prompt sizes for Rem and its workers over the last {CONTEXT_USAGE_DAYS} "
        f"days, sizes only, are in the file {path} (JSON): the standing prompt parts that sent "
        "the most text and each kind of Rem's own calls with its cache use. Read it when "
        "you look at token or context use, instead of estimating sizes yourself."
    )


def filed_context(context: dict[str, Any], path: Path) -> dict[str, Any]:
    """The background the instructions keep when the full one is in the file at ``path``."""
    kept = {k: v for k, v in context.items() if k != "recalled_context"}
    return {
        **kept,
        "background_file": str(path),
        "recalled_context_index": index(context["recalled_context"]),
    }


def file_rule(path: Path) -> str:
    """The rule the instructions give with the index."""
    return (
        f" The full background, with every recalled message and thread excerpt, is in the "
        f"file {path} (JSON); recalled_context_index lists what it holds. Open that file "
        "before asking the user anything it might answer, and whenever you need a "
        "message or thread in full."
    )
