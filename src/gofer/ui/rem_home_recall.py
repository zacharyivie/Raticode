"""Bounded, permission-checked reads of Rem's durable chat and linked workers."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from gofer.ui.rem_home_store import RemHomeStore


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


STOP_WORDS = frozenset(
    "a an and are as at be been build can daily do earlier for from have here i in is it "
    "me my of on or please report script should task that the this to tool tools want was "
    "we what when with would you your".split()
)


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
        for k in ("id", "role", "sender", "kind", "created_at", "work")
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

    def history(self, request: Recall, query: str) -> list[dict[str, Any]]:
        """Search the entire conversation, including neighbors of older matching messages."""
        with self.store.connect() as db:
            selected = {
                r[0]: 0 for r in db.execute("SELECT seq FROM messages ORDER BY seq DESC LIMIT 6")
            }
            for search in request.queries or [query]:
                words = terms(search[:1000])
                if not words:
                    continue
                score = " + ".join(
                    "(instr(lower(json_extract(body, '$.body')), ?) > 0)" for _ in words
                )
                rows = db.execute(
                    f"SELECT seq, ({score}) AS score FROM messages WHERE score > 0 "
                    "ORDER BY score DESC, seq DESC LIMIT 8",
                    words,
                ).fetchall()
                for row in rows:
                    for seq in range(max(1, row["seq"] - 1), row["seq"] + 3):
                        priority = row["score"] * 100 + (10 if seq == row["seq"] else 0)
                        selected[seq] = max(selected.get(seq, 0), priority)
            if request.before_message_id:
                anchor = db.execute(
                    "SELECT seq FROM messages WHERE id=?", (request.before_message_id,)
                ).fetchone()
                if anchor:
                    selected.update(
                        (r[0], 10000)
                        for r in db.execute(
                            "SELECT seq FROM messages WHERE seq<? ORDER BY seq DESC LIMIT 30",
                            (anchor[0],),
                        )
                    )
            if request.message_ids:
                selected.update(
                    (r[0], 5000)
                    for r in db.execute(
                        "SELECT seq FROM messages WHERE id IN ("
                        + ",".join("?" for _ in request.message_ids)
                        + ")",
                        request.message_ids,
                    )
                )
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
            for row in sorted(rows, key=lambda r: (selected[r["seq"]], r["seq"]), reverse=True):
                message = json.loads(row["body"])
                linked = message.get("work") or {}
                if linked.get("run_id"):
                    run = self.store.get(db, "runs", linked["run_id"])
                    task = self.store.get(db, "tasks", run["task_id"])
                    if not self.permitted(run) or task.get("deleted_at"):
                        continue
                item = excerpt(message, " ".join(request.queries) or query)
                if len(item["body"]) > budget:
                    item.update(body=item["body"][:budget], truncated=True)
                budget -= len(item["body"])
                messages.append((row["seq"], item))
                if budget <= 0:
                    break
            return [item for _, item in sorted(messages)]

    def index(self, query: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        linked = {m.get("work", {}).get("run_id") for m in history if m.get("work")}
        words = terms(query)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT t.body, r.body FROM threads t JOIN runs r "
                "ON r.id=json_extract(t.body, '$.managedRunId') JOIN tasks task ON task.id=r.task "
                "WHERE json_extract(task.body, '$.deleted_at') IS NULL AND EXISTS "
                "(SELECT 1 FROM messages m WHERE json_extract(m.body, '$.work.run_id')=r.id) "
                "ORDER BY t.rowid DESC"
            ).fetchall()
        entries = []
        with self.store.connect() as db:
            for row in rows:
                thread, run = json.loads(row[0]), json.loads(row[1])
                if not self.permitted(run):
                    continue
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
        entries.sort(key=lambda entry: entry[0], reverse=True)
        return [entry for _, entry in entries[:60]]

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
            [excerpt(json.loads(r["body"]), query) for r in reversed(rows[:20])], 12000
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
        result = {
            "history": history,
            "threads": [self.read_thread(read, search) for read in reads],
        }
        if include_index:
            result["linked_threads"] = index
        return result
