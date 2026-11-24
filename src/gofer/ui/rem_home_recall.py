"""Bounded, permission-checked reads of Rem's durable chat and linked workers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from gofer.ui.rem_home_lookups import MAX_LOOKUPS, Lookup
from gofer.ui.rem_home_store import RemHomeStore, with_author
from gofer.ui.rem_thread_listing import LINKED_LISTED


class ThreadRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=128)
    before_message_id: str | None = Field(default=None, max_length=128)


class Recall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=3
    )
    threads: list[ThreadRead] = Field(default_factory=list, max_length=5)
    message_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=60
    )
    before_message_id: str | None = Field(default=None, max_length=128)
    # Backend-run, read-only lookups (rem_home_lookups); never stored with delegated work.
    lookups: list[Lookup] = Field(default_factory=list, max_length=MAX_LOOKUPS)

    def reads_chat(self) -> bool:
        """Whether this request reads chat or threads, not only lookups."""
        return (
            bool(self.queries or self.threads or self.message_ids or self.before_message_id)
            or not self.lookups
        )


STOP_WORDS = frozenset(
    "a an and are as at be been build can daily do earlier for from have here i in is it "
    "me my of on or please report script should task that the this to tool tools want was "
    "we what when with would you your".split()
)


# Self-work reports list dozens of task titles, so a keyword search matches them for
# almost any query. Unless the search is about self-work, or the report is read by its
# ID or by paging, they rank after other matches, at most this many come back, and
# each shows only its start (lead line and summary).
SELF_WORK_REPORT = "self_work_report"
REPORT_CUT_CHARS = 600
REPORTS_CUT_LISTED = 2
SELF_WORK_WORDS = re.compile(r"self[\s_-]?work", re.IGNORECASE)


def about_self_work(*searches: str) -> bool:
    return any(SELF_WORK_WORDS.search(search) for search in searches)


def remembered_ids(history: list[dict[str, Any]]) -> list[str]:
    """The message IDs a delegated worker's background rereads in full.

    A cut self-work report is left out: rereading it by ID would return all of it, and
    the recorded searches find it again, cut the same way.
    """
    return [m["id"] for m in history if not m.get("self_work_report_cut")][:60]


def shown_lengths(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Each shown message's ID and how much of its text a prompt part already holds."""
    return {str(m["id"]): len(str(m.get("body") or "")) for m in messages if m.get("id")}


def thread_key(thread: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """A thread excerpt's identity: the thread and the page of messages it shows."""
    return (
        str(thread.get("thread_id")),
        tuple(str(m.get("id")) for m in thread.get("messages") or []),
    )


def send_once(
    recalled: dict[str, Any],
    shown: dict[str, int],
    shown_threads: set[tuple[str, tuple[str, ...]]],
) -> dict[str, Any]:
    """A copy of a gather result without what another part of the prompt already shows.

    A message is left out when ``shown`` holds its ID with at least as much text, so a
    copy the history part cut short still comes through whole. A thread is left out when
    the same page of it was already shown. What is kept is added to ``shown`` and
    ``shown_threads``, so a later read in the same prompt leaves it out too. Callers keep
    the unfiltered result for the reads a delegated worker replays.
    """
    history = []
    for message in recalled.get("history") or []:
        size = len(str(message.get("body") or ""))
        if shown.get(str(message.get("id")), -1) >= size:
            continue
        history.append(message)
        shown[str(message.get("id"))] = size
    threads = []
    for thread in recalled.get("threads") or []:
        key = thread_key(thread)
        if key in shown_threads:
            continue
        threads.append(thread)
        shown_threads.add(key)
    return {**recalled, "history": history, "threads": threads}


def merge_recalled(
    entries: list[dict[str, Any]],
    leave_out: frozenset[str] = frozenset(),
    positions: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Replayed reads with each message ID and thread ID once across all of them.

    A chat message stays where it first appears, with its longest excerpt from any read;
    IDs in ``leave_out`` are shown elsewhere and dropped. A thread stays where it first
    appears too, holding every page any read showed of it once, in thread order
    (``positions`` maps a thread message's ID to its place). Reads left empty go.
    """
    positions = positions or {}
    longest: dict[str, dict[str, Any]] = {}
    pages: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        for message in entry.get("history") or []:
            identity = str(message.get("id"))
            body = len(str(message.get("body") or ""))
            if identity not in longest or body > len(str(longest[identity].get("body") or "")):
                longest[identity] = message
        for thread in entry.get("threads") or []:
            pages.setdefault(str(thread.get("thread_id")), []).append(thread)
    sent_messages, sent_threads = set(leave_out), set()
    merged = []
    for entry in entries:
        history = []
        for message in entry.get("history") or []:
            identity = str(message.get("id"))
            if identity not in sent_messages:
                sent_messages.add(identity)
                history.append(longest[identity])
        threads = []
        for thread in entry.get("threads") or []:
            identity = str(thread.get("thread_id"))
            if identity not in sent_threads:
                sent_threads.add(identity)
                threads.append(merged_thread(pages[identity], positions))
        result = {**entry, "history": history, "threads": threads}
        if history or threads or set(result) - {"history", "threads"}:
            merged.append(result)
    return merged


def merged_thread(pages: list[dict[str, Any]], positions: Mapping[str, int]) -> dict[str, Any]:
    """One excerpt of a thread from every page read of it, each message once."""
    shown = [page for page in pages if not page.get("unavailable")]
    if len(shown) < 2:
        return shown[0] if shown else pages[0]
    messages: dict[str, dict[str, Any]] = {}
    for page in shown:
        for message in page.get("messages") or []:
            identity = str(message.get("id"))
            body = len(str(message.get("body") or ""))
            if identity not in messages or body > len(str(messages[identity].get("body") or "")):
                messages[identity] = message
    first_seen = {identity: n for n, identity in enumerate(messages)}
    ordered = sorted(
        messages,
        key=lambda i: (positions.get(i, len(positions) + first_seen[i]), first_seen[i]),
    )
    earliest = ordered[0] if ordered else None
    # More to read before the merged excerpt only if the page holding its start said so.
    more_before = next(
        (
            page.get("more_before")
            for page in shown
            if earliest and any(str(m.get("id")) == earliest for m in page.get("messages") or [])
        ),
        None,
    )
    return {**shown[0], "messages": [messages[i] for i in ordered], "more_before": more_before}


def terms(query: str) -> list[str]:
    return list(
        dict.fromkeys(
            w for w in re.findall(r"\w+", query.casefold()) if len(w) > 2 and w not in STOP_WORDS
        )
    )[:12]


def excerpt(message: dict[str, Any], query: str = "") -> dict[str, Any]:
    """Keep source IDs and report links, and mark shortened source text explicitly."""
    result: dict[str, Any] = {
        k: message[k]
        for k in ("id", "role", "sender", "kind", "created_at", "work", "refers_to")
        if k in message
    }
    body = str(message.get("body") or "")
    start = 0
    if len(body) > 6000:
        offsets = [body.casefold().find(w) for w in terms(query)]
        start = max(0, min((i for i in offsets if i >= 0), default=0) - 1000)
    result["body"] = body[start : start + 6000]
    if start or len(body) > 6000:
        result["truncated"] = True
    if message.get("attachments"):
        result["attachments"] = [
            {k: a[k] for k in ("name", "type", "size", "path") if k in a}
            for a in message["attachments"]
        ]
    return result


def bounded_messages(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Fit the newest source excerpts while preserving their chronological order."""
    result = []
    for message in reversed(messages):
        if budget <= 0:
            break
        item = dict(message)
        if len(item["body"]) > budget:
            item.update(body=item["body"][:budget], truncated=True)
        budget -= len(item["body"])
        result.append(item)
    return list(reversed(result))


class RemRecall:
    def __init__(self, store: RemHomeStore, config: dict[str, Any]) -> None:
        self.store, self.config = store, config

    def permitted(self, run: dict[str, Any]) -> bool:
        project = run["recipe"].get("project_dir")
        if project and project not in {p["root"] for p in self.config.get("projects", [])}:
            return False
        previous = run.get("config", {}).get("brain") or {}
        current = self.config.get("brain") or {}
        if previous.get("enabled") and (
            not current.get("enabled")
            or previous.get("root") != current.get("root")
            or not set(previous.get("brainIds", [])).issubset(current.get("brainIds", []))
        ):
            return False
        return True

    def readable(self, db: Any, message: dict[str, Any]) -> bool:
        """Whether Rem may read a chat message: its linked task is neither deleted nor closed."""
        linked = message.get("work") or {}
        if not linked.get("run_id"):
            return True
        try:
            run = self.store.get(db, "runs", linked["run_id"])
            task = self.store.get(db, "tasks", run["task_id"])
        except KeyError:
            # A finished one-time task removed after 30 days: the
            # message is still conversation history.
            return True
        return self.permitted(run) and not task.get("deleted_at")

    def referenced(self, identity: str, query: str) -> dict[str, Any]:
        """The message the user replied to, read in full wherever it is in the chat.

        It is marked ``unavailable`` once the chat no longer has it or Rem may no longer read it.
        """
        with self.store.read() as db:
            row = db.execute("SELECT body FROM messages WHERE id=?", (identity,)).fetchone()
            message = json.loads(row[0]) if row else None
            if not message or not self.readable(db, message):
                return {"id": identity, "unavailable": True}
        return excerpt(message, query)

    def history(self, request: Recall, query: str) -> list[dict[str, Any]]:
        """Search the entire conversation, including neighbors of older matching messages.

        Self-work reports are kept short unless the search is about self-work or the
        report is read by ID or by paging (see ``SELF_WORK_REPORT``).
        """
        searches = request.queries or [query]
        full_reports = about_self_work(*searches, query)
        # Read by ID or by paging: always whole.
        explicit: set[int] = set()
        with self.store.connect() as db:
            selected = {
                r[0]: 0 for r in db.execute("SELECT seq FROM messages ORDER BY seq DESC LIMIT 6")
            }
            report = f"(coalesce(json_extract(body, '$.kind'), '') = '{SELF_WORK_REPORT}')"
            for search in searches:
                words = terms(search[:1000])
                if not words:
                    continue
                score = " + ".join(
                    "(instr(lower(json_extract(body, '$.body')), ?) > 0)" for _ in words
                )
                rows = db.execute(
                    f"SELECT seq, ({score}) AS score, {report} AS report FROM messages "
                    "WHERE score > 0 ORDER BY "
                    + ("" if full_reports else "report, ")
                    + "score DESC, seq DESC LIMIT 8",
                    words,
                ).fetchall()
                for row in rows:
                    if row["report"] and not full_reports:
                        # Found by words a report lists: no neighbors, read after the rest.
                        selected.setdefault(row["seq"], 0)
                        continue
                    for seq in range(max(1, row["seq"] - 1), row["seq"] + 3):
                        priority = row["score"] * 100 + (10 if seq == row["seq"] else 0)
                        selected[seq] = max(selected.get(seq, 0), priority)
            if request.before_message_id:
                anchor = db.execute(
                    "SELECT seq FROM messages WHERE id=?", (request.before_message_id,)
                ).fetchone()
                if anchor:
                    paged = [
                        r[0]
                        for r in db.execute(
                            "SELECT seq FROM messages WHERE seq<? ORDER BY seq DESC LIMIT 30",
                            (anchor[0],),
                        )
                    ]
                    selected.update((seq, 10000) for seq in paged)
                    explicit.update(paged)
            if request.message_ids:
                named = [
                    r[0]
                    for r in db.execute(
                        "SELECT seq FROM messages WHERE id IN ("
                        + ",".join("?" for _ in request.message_ids)
                        + ")",
                        request.message_ids,
                    )
                ]
                selected.update((seq, 5000) for seq in named)
                explicit.update(named)
            if not selected:
                return []
            rows = db.execute(
                "SELECT seq,body FROM messages WHERE seq IN ("
                + ",".join("?" for _ in selected)
                + ") ORDER BY seq",
                sorted(selected),
            ).fetchall()
            messages = []
            budget = 24000
            cut_reports = 0
            for row in sorted(rows, key=lambda r: (selected[r["seq"]], r["seq"]), reverse=True):
                message = json.loads(row["body"])
                if not self.readable(db, message):
                    continue
                item = excerpt(message, " ".join(request.queries) or query)
                if (
                    message.get("kind") == SELF_WORK_REPORT
                    and not full_reports
                    and row["seq"] not in explicit
                ):
                    if cut_reports >= REPORTS_CUT_LISTED:
                        continue
                    cut_reports += 1
                    body = str(message.get("body") or "")
                    if len(body) > REPORT_CUT_CHARS:
                        item.update(
                            body=body[:REPORT_CUT_CHARS],
                            truncated=True,
                            self_work_report_cut=True,
                        )
                if len(item["body"]) > budget:
                    item.update(body=item["body"][:budget], truncated=True)
                budget -= len(item["body"])
                messages.append((row["seq"], item))
                if budget <= 0:
                    break
            return [item for _, item in sorted(messages)]

    def _linked(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Every task thread linked from the chat that Rem may read, newest first."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT t.body, r.body FROM threads t JOIN runs r "
                "ON r.id=json_extract(t.body, '$.managedRunId') JOIN tasks task ON task.id=r.task "
                "WHERE json_extract(task.body, '$.deleted_at') IS NULL AND EXISTS "
                "(SELECT 1 FROM messages m WHERE json_extract(m.body, '$.work.run_id')=r.id) "
                "ORDER BY t.rowid DESC"
            ).fetchall()
        pairs = [(json.loads(row[0]), json.loads(row[1])) for row in rows]
        return [(thread, run) for thread, run in pairs if self.permitted(run)]

    def linked_threads(self) -> list[dict[str, Any]]:
        """Every readable linked task thread in brief, for the threads lookup."""
        return [
            {
                "thread_id": thread["id"],
                "run_id": run["id"],
                "task_id": run["task_id"],
                "title": thread["title"],
                "project_dir": run["recipe"].get("project_dir"),
                "state": run["state"],
                "archived": bool(thread.get("archived")),
                "updated_at": thread.get("updatedAt"),
            }
            for thread, run in self._linked()
        ]

    def index(self, query: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every readable linked task thread, best match for ``query`` first."""
        linked = {m.get("work", {}).get("run_id") for m in history if m.get("work")}
        words = terms(query)
        entries = []
        pairs = self._linked()
        with self.store.connect() as db:
            for thread, run in pairs:
                text = (thread["title"] + " " + run["recipe"]["prompt"]).casefold()
                score = sum(w in text for w in words) + (4 if run["id"] in linked else 0)
                if words:
                    matches = " OR ".join(
                        "instr(lower(json_extract(body, '$.body')), ?) > 0" for _ in words
                    )
                    found = db.execute(
                        "SELECT 1 FROM thread_messages WHERE thread=? AND "
                        "coalesce(json_extract(body, '$.kind'), '') != 'thought' AND ("
                        + matches
                        + ") LIMIT 1",
                        [thread["id"], *words],
                    ).fetchone()
                    score += 2 if found else 0
                entries.append(
                    (
                        score,
                        {
                            "thread_id": thread["id"],
                            "run_id": run["id"],
                            "task_id": run["task_id"],
                            "title": thread["title"],
                            "role": thread["managedRole"],
                            "project_dir": run["recipe"].get("project_dir"),
                            "state": run["state"],
                            "archived": bool(thread.get("archived")),
                            "goal": run["recipe"]["prompt"][:300],
                            "relevant": score > 0,
                        },
                    )
                )
        # Stable: equal scores stay newest first.
        entries.sort(key=lambda entry: entry[0], reverse=True)
        return [entry for _, entry in entries]

    def read_thread(self, request: ThreadRead, query: str) -> dict[str, Any]:
        # Check against *all* linked runs, rather than trusting a model-provided ID.
        with self.store.connect() as db:
            row = db.execute(
                "SELECT t.body, r.body FROM threads t JOIN runs r "
                "ON r.id=json_extract(t.body, '$.managedRunId') JOIN tasks task ON task.id=r.task "
                "WHERE t.id=? AND json_extract(task.body, '$.deleted_at') IS NULL AND EXISTS "
                "(SELECT 1 FROM messages m WHERE json_extract(m.body, '$.work.run_id')=r.id)",
                (request.thread_id,),
            ).fetchone()
            if not row or not self.permitted(json.loads(row[1])):
                return {"thread_id": request.thread_id, "unavailable": True}
            thread, run = json.loads(row[0]), json.loads(row[1])
            anchor = None
            if request.before_message_id:
                anchor = db.execute(
                    "SELECT seq FROM thread_messages WHERE thread=? AND id=?",
                    (request.thread_id, request.before_message_id),
                ).fetchone()
                if not anchor:
                    return {"thread_id": request.thread_id, "unavailable": True}
            rows = db.execute(
                "SELECT seq,body FROM thread_messages WHERE thread=? "
                "AND seq<? AND coalesce(json_extract(body, '$.kind'), '') != 'thought' "
                "ORDER BY seq DESC LIMIT 21",
                (request.thread_id, anchor[0] if anchor else 2**63 - 1),
            ).fetchall()
        messages = bounded_messages(
            [excerpt(with_author(json.loads(r["body"])), query) for r in reversed(rows[:20])], 12000
        )
        return {
            "thread_id": request.thread_id,
            "title": thread["title"],
            "project_dir": run["recipe"].get("project_dir"),
            "state": run["state"],
            "archived": bool(thread.get("archived")),
            "messages": messages,
            "more_before": messages[0]["id"]
            if messages and (len(rows) > 20 or len(messages) < len(rows))
            else None,
            "artifacts": [
                {k: a[k] for k in ("path", "sha256") if k in a} for a in run.get("artifacts", [])
            ],
        }

    def gather(self, request: Recall, query: str, *, include_index: bool = True) -> dict[str, Any]:
        history = self.history(request, query)
        search = " ".join([*request.queries, query])
        index = self.index(search, history) if include_index else []
        reads = (
            request.threads
            or [ThreadRead(thread_id=t["thread_id"]) for t in index if t["relevant"]][:3]
        )
        result: dict[str, Any] = {
            "history": history,
            "threads": [self.read_thread(read, search) for read in reads],
        }
        if include_index:
            # The best few go in the prompt; a threads lookup finds the others.
            result["linked_threads"] = index[:LINKED_LISTED]
            result["linked_threads_total"] = len(index)
        return result
