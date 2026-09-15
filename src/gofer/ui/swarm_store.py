"""Store archived runs separately so live updates never rewrite their history."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class SwarmStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute(
            "CREATE TABLE IF NOT EXISTS swarm_history "
            "(swarm_id TEXT NOT NULL, run_id TEXT NOT NULL, body TEXT NOT NULL, summary TEXT, "
            "PRIMARY KEY(swarm_id, run_id))"
        )
        if "summary" not in {row[1] for row in db.execute("PRAGMA table_info(swarm_history)")}:
            db.execute("ALTER TABLE swarm_history ADD COLUMN summary TEXT")
            db.execute(
                "UPDATE swarm_history SET summary=json_object('id', run_id, "
                "'task', json_extract(body, '$.task'), 'state', json_extract(body, '$.state'))"
            )
            db.commit()
        db.execute(
            "CREATE TABLE IF NOT EXISTS swarm_summaries "
            "(id TEXT PRIMARY KEY, project TEXT NOT NULL, body TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS swarm_items "
            "(swarm_id TEXT, run_id TEXT, collection TEXT, position INTEGER, body TEXT, "
            "PRIMARY KEY(swarm_id, run_id, collection, position))"
        )
        db.execute("CREATE INDEX IF NOT EXISTS swarm_summary_project ON swarm_summaries(project)")

    def archive(self, swarm: dict[str, Any]) -> dict[str, Any]:
        body = {key: value for key, value in swarm.items() if key != "history"}
        for run in swarm.get("history", []):
            self.db.execute(
                "INSERT INTO swarm_history (swarm_id, run_id, body, summary) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(swarm_id, run_id) DO NOTHING",
                (
                    swarm["id"],
                    run["id"],
                    json.dumps(run),
                    json.dumps({key: run.get(key) for key in ("id", "task", "state")}),
                ),
            )
        return body

    def history(self, swarm_id: str, *, summaries: bool = False) -> list[dict[str, Any]]:
        if summaries:
            return [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT summary FROM swarm_history WHERE swarm_id=? ORDER BY rowid", (swarm_id,)
                )
            ]
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT body FROM swarm_history WHERE swarm_id=? ORDER BY rowid", (swarm_id,)
            )
        ]

    def pack(self, swarm: dict[str, Any]) -> dict[str, Any]:
        body = self.archive(swarm)
        body["_storageVersion"] = 1
        if not body.get("run"):
            return body
        run = dict(body["run"])
        body["run"] = run
        run["agentStates"] = {key: dict(value) for key, value in run["agentStates"].items()}
        groups = [("run", run)] + [
            (f"agent:{key}", value) for key, value in run["agentStates"].items()
        ]
        for owner, record in groups:
            for field in ("events", "messages", "traces", "attempts"):
                if field not in record:
                    continue
                values = record.pop(field)
                collection = f"{owner}:{field}"
                for position, value in enumerate(values):
                    self.db.execute(
                        "INSERT INTO swarm_items VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(swarm_id, run_id, collection, position) DO UPDATE "
                        "SET body=excluded.body WHERE body != excluded.body",
                        (swarm["id"], run["id"], collection, position, json.dumps(value)),
                    )
                self.db.execute(
                    "DELETE FROM swarm_items WHERE swarm_id=? AND run_id=? "
                    "AND collection=? AND position>=?",
                    (swarm["id"], run["id"], collection, len(values)),
                )
                record[field] = {"storedItems": len(values)}
        # Archived runs have their own immutable copy.
        self.db.execute(
            "DELETE FROM swarm_items WHERE swarm_id=? AND run_id!=?", (swarm["id"], run["id"])
        )
        return body

    def load(self, encoded: str) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(encoded)
        if body.pop("_storageVersion", 0) not in {0, 1}:
            raise ValueError("Unsupported swarm storage version")
        run = body.get("run")
        if not run:
            return body
        groups = [("run", run)] + [
            (f"agent:{key}", value) for key, value in run["agentStates"].items()
        ]
        for owner, record in groups:
            for field in ("events", "messages", "traces", "attempts"):
                if not isinstance(record.get(field), dict):
                    continue
                record[field] = [
                    json.loads(row[0])
                    for row in self.db.execute(
                        "SELECT body FROM swarm_items WHERE swarm_id=? AND run_id=? "
                        "AND collection=? ORDER BY position",
                        (body["id"], run["id"], f"{owner}:{field}"),
                    )
                ]
        return body

    def activity(
        self, swarm_id: str, agent_id: str, event: dict[str, Any], timestamp: str
    ) -> dict[str, Any]:
        """Write one trace without loading or rewriting board/transcript history.

        The caller owns the transaction, including the matching summary update.
        Trace positions grow monotonically between full saves; only 100 survive.
        """
        row = self.db.execute("SELECT body FROM swarms WHERE id=?", (swarm_id,)).fetchone()
        metadata: dict[str, Any] = json.loads(row[0])
        run = metadata["run"]
        state = run["agentStates"][agent_id]
        state.update(activity=str(event.get("text", ""))[:1000], lastActivityAt=timestamp)
        collection = f"agent:{agent_id}:traces"
        position = self.db.execute(
            "SELECT COALESCE(MAX(position), -1)+1 FROM swarm_items "
            "WHERE swarm_id=? AND run_id=? AND collection=?",
            (swarm_id, run["id"], collection),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO swarm_items VALUES (?, ?, ?, ?, ?)",
            (swarm_id, run["id"], collection, position, json.dumps(event)),
        )
        self.db.execute(
            "DELETE FROM swarm_items WHERE swarm_id=? AND run_id=? AND collection=? AND position<?",
            (swarm_id, run["id"], collection, position - 99),
        )
        state["traces"] = {"storedItems": min(100, position + 1)}
        self.db.execute(
            "UPDATE swarm_items SET body=json_set(body, '$.lastActivityAt', ?) "
            "WHERE swarm_id=? AND run_id=? AND collection='run:attempts' "
            "AND json_extract(body, '$.id')=?",
            (timestamp, swarm_id, run["id"], state.get("attemptId")),
        )
        metadata["updatedAt"] = timestamp
        self.db.execute("UPDATE swarms SET body=? WHERE id=?", (json.dumps(metadata), swarm_id))
        return metadata

    def page(
        self, swarm_id: str, run_id: str, section: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        offset, limit = params.get("offset", 0), params.get("limit", 20)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Use offset >= 0 and limit between 1 and 100")
        collection = "run:messages" if section == "board" else f"run:{section}"
        clause = "swarm_id=? AND run_id=? AND collection=?"
        args: list[Any] = [swarm_id, run_id, collection]
        if params.get("milestoneId"):
            clause += " AND json_extract(body, '$.milestoneId')=?"
            args.append(params["milestoneId"])
        total = self.db.execute(
            f"SELECT COUNT(*) FROM swarm_items WHERE {clause}", args
        ).fetchone()[0]
        items = [
            json.loads(row[0])
            for row in self.db.execute(
                f"SELECT body FROM swarm_items WHERE {clause} ORDER BY position LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
        ]
        return {
            "items": items,
            "total": total,
            "nextOffset": offset + limit if offset + limit < total else None,
        }
