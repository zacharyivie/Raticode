"""The per-organization change counter and the reads it lets skip decoding."""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gofer.ui.organization_store as store_module
from gofer.core.usage_ledger import ledger_mark, record_invocation, usage_db
from gofer.ui.organization_store import OrganizationStore
from gofer.ui.rem_home import RemHome
from tests.unit.test_organizations import call, company
from tests.unit.test_organizations import manager as manager
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import org as org


def count(store: OrganizationStore, oid: str) -> int:
    with store.connect() as db:
        return store._change_count(db, oid)


@contextmanager
def traced(store: OrganizationStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every SQL statement the store runs while the block runs."""
    statements: list[str] = []
    original = store.connect

    @contextmanager
    def connect() -> Iterator[sqlite3.Connection]:
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "connect", connect)
    yield statements
    monkeypatch.setattr(store, "connect", original)


def no_decoding(store: OrganizationStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("The organization was decoded")

    monkeypatch.setattr(store, "_get", refuse)


# One test per write kind the counter follows --------------------------------------

WRITES = {
    ("organizations", "insert"): "INSERT INTO organizations VALUES (:oid, '', 0, '{}', '{}')",
    ("organizations", "update"): "UPDATE organizations SET revision=revision + 100 WHERE id=:oid",
    ("organizations", "delete"): "DELETE FROM organizations WHERE id=:oid",
    ("organization_tasks", "insert"): (
        "INSERT INTO organization_tasks VALUES (:oid, 'extra', 'todo', NULL, NULL, "
        '\'{"id": "extra", "title": "Extra"}\')'
    ),
    ("organization_tasks", "update"): (
        "UPDATE organization_tasks SET status='blocked' WHERE organization=:oid"
    ),
    ("organization_tasks", "delete"): "DELETE FROM organization_tasks WHERE organization=:oid",
    ("organization_comments", "insert"): (
        "INSERT INTO organization_comments VALUES (:oid, 'first', 99, '{\"body\": \"Hi\"}')"
    ),
    ("organization_records", "insert"): (
        "INSERT INTO organization_records VALUES (:oid, 'memories', 'm1', '{}')"
    ),
    ("organization_records", "update"): (
        "UPDATE organization_records SET body='{\"changed\": true}' WHERE organization=:oid"
    ),
    ("organization_records", "delete"): (
        "DELETE FROM organization_records WHERE organization=:oid"
    ),
}


@pytest.mark.parametrize("table,kind", list(WRITES), ids=lambda value: str(value))
def test_each_write_kind_raises_the_counter(manager, tmp_path, table, kind) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="First")
    store = manager.store
    with store.connect() as db:
        db.execute(
            "INSERT INTO organization_records VALUES (?, 'memories', 'm0', '{}')", (org["id"],)
        )
    oid = str(uuid.uuid4()) if (table, kind) == ("organizations", "insert") else org["id"]
    before = count(store, oid)
    with store.connect() as db:
        db.execute(WRITES[(table, kind)], {"oid": oid})
    assert count(store, oid) > before


@pytest.mark.parametrize("kind", ["update", "delete"])
def test_task_conversations_stay_append_only(manager, tmp_path, kind) -> Any:
    """Comments can't change or go away, so only their inserts can move the counter."""
    org = company(manager, tmp_path)
    task = call(manager, org, "task_create", title="First")
    call(manager, org, "comment", taskId=task["id"], body="Hello")
    before = count(manager.store, org["id"])
    statement = (
        "UPDATE organization_comments SET body='{}'"
        if kind == "update"
        else "DELETE FROM organization_comments"
    )
    with pytest.raises(sqlite3.IntegrityError), manager.store.connect() as db:
        db.execute(statement)
    assert count(manager.store, org["id"]) == before


def test_a_write_that_changes_nothing_keeps_the_counter(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    before = count(manager.store, org["id"])
    current = manager.store.get("", org["id"])
    with manager.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        manager.store._write_runtime(db, org["id"], current["runtime"])
    assert count(manager.store, org["id"]) == before


def test_actions_move_the_read_version(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    versions = [call(manager, org, "read")["version"]]
    task = call(manager, org, "task_create", title="Write")
    versions.append(call(manager, org, "read")["version"])
    call(manager, org, "comment", taskId=task["id"], body="Said")
    versions.append(call(manager, org, "read")["version"])
    assert len(set(versions)) == 3
    # A store started again names its versions afresh, so a restored copy can't match.
    again = OrganizationStore(manager.store.path.parent)
    assert again.read_version(org["id"]) != manager.store.read_version(org["id"])


# Reads that skip decoding ----------------------------------------------------------


def test_unchanged_read_decodes_no_tasks_or_records(manager, tmp_path, monkeypatch) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Kept")
    version = call(manager, org, "read")["version"]
    no_decoding(manager.store, monkeypatch)
    with traced(manager.store, monkeypatch) as statements:
        assert call(manager, org, "read", sinceVersion=version) == {
            "notModified": True,
            "version": version,
        }
    assert statements and not any(
        table in statement
        for statement in statements
        for table in ("organization_tasks", "organization_records", "organization_comments")
    )


def test_read_with_a_stale_version_returns_the_new_state(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    version = call(manager, org, "read")["version"]
    call(manager, org, "task_create", title="Fresh")
    result = call(manager, org, "read", sinceVersion=version)
    assert result["version"] != version
    assert [t["title"] for t in result["runtime"]["tasks"]] == ["Fresh"]
    with pytest.raises(ValueError, match="not found"):
        manager.call("", "user", {"action": "read", "organizationId": "missing", "params": {}})


def test_paused_organization_dispatch_reads_one_row(manager, tmp_path, monkeypatch) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Waiting", assignee="builder")
    no_decoding(manager.store, monkeypatch)
    with traced(manager.store, monkeypatch) as statements:
        manager._dispatch_org(org["projectRoot"], org["id"])
    assert len(statements) == 1
    assert "FROM organizations WHERE id=" in statements[0]


def test_archived_organization_dispatch_reads_one_row(manager, tmp_path, monkeypatch) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "archive", actor="user", archived=True)
    no_decoding(manager.store, monkeypatch)
    with traced(manager.store, monkeypatch) as statements:
        manager._dispatch_org(org["projectRoot"], org["id"])
    assert len(statements) == 1


# Shared decoded copies --------------------------------------------------------------


def test_snapshots_decode_again_only_after_a_change(manager, tmp_path, monkeypatch) -> Any:
    org = company(manager, tmp_path)
    first = manager.store.snapshots()
    decoded: list[str] = []
    original = manager.store.get_versioned

    def get_versioned(project: str, oid: str) -> Any:
        decoded.append(oid)
        return original(project, oid)

    monkeypatch.setattr(manager.store, "get_versioned", get_versioned)
    assert manager.store.snapshots()[0] is first[0] and not decoded
    call(manager, org, "task_create", title="New")
    decoded.clear()  # The action's own reads.
    changed = manager.store.snapshots()[0]
    assert decoded == [org["id"]] and changed is not first[0]
    assert [t["title"] for t in changed["runtime"]["tasks"]] == ["New"]


def test_auto_review_check_and_rem_requests_reuse_the_copies(manager, tmp_path, monkeypatch) -> Any:
    company(manager, tmp_path)
    manager.auto_review = lambda *_args: None
    manager.store.snapshots()
    no_decoding(manager.store, monkeypatch)
    manager._next_auto = 0.0
    manager._auto_reviews()
    assert RemHome._organization_requests(SimpleNamespace(organizations=manager)) == []


def test_board_keeps_usage_until_the_ledger_or_organization_changes(board, org, monkeypatch) -> Any:
    home, _, _ = board
    manager, identity = org
    task = manager.call(
        "", "user", {"action": "task_create", "organizationId": identity, "params": {"title": "W"}}
    )

    def attempt(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["attempts"] = {
            "turn-1": {"id": "turn-1", "taskId": task["id"], "finishedAt": "x", "usage": {}}
        }
        return {"source": "test"}

    manager.store.mutate("", identity, "user", "seed", attempt)
    data = home.kanban.data_dir
    record_invocation(
        invocation_id="one",
        provider="codex",
        metadata={"total_tokens": 10},
        data_dir=data,
        run_id="turn-1",
    )
    counted: list[str] = []
    original = store_module.organization_ticket_usage

    def usage(value: dict[str, Any], where: Path) -> Any:
        counted.append(value["id"])
        return original(value, where)

    monkeypatch.setattr(store_module, "organization_ticket_usage", usage)

    def shown() -> Any:
        found = next(t for t in home.kanban.board()["tickets"] if t["ticket_id"] == task["id"])
        return found["usage"]["total_tokens"]

    assert shown() == 10 and len(counted) == 1
    assert shown() == 10 and len(counted) == 1
    record_invocation(
        invocation_id="two",
        provider="codex",
        metadata={"total_tokens": 5},
        data_dir=data,
        run_id="turn-1",
    )
    assert shown() == 15 and len(counted) == 2
    # A replayed call updates its row in place; the ledger counts that too.
    record_invocation(
        invocation_id="two",
        provider="codex",
        metadata={"total_tokens": 7},
        data_dir=data,
        run_id="turn-1",
    )
    assert shown() == 17 and len(counted) == 3
    manager.call(
        "", "user", {"action": "task_create", "organizationId": identity, "params": {"title": "X"}}
    )
    assert shown() == 17 and len(counted) == 4


# The usage ledger's opens -------------------------------------------------------------


def test_read_only_ledger_open_runs_no_schema_and_commits_nothing(tmp_path, monkeypatch) -> Any:
    with usage_db(tmp_path):
        pass
    statements: list[str] = []
    connect = sqlite3.connect

    def tracing(*args: Any, **kwargs: Any) -> Any:
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", tracing)
    with usage_db(tmp_path) as db:
        db.execute("SELECT COUNT(*) FROM usage_invocations").fetchone()
        changes = db.total_changes
    assert changes == 0
    assert statements == ["PRAGMA user_version", "SELECT COUNT(*) FROM usage_invocations"]


def test_an_older_ledger_is_prepared_once_and_counts_replays(tmp_path: Path) -> Any:
    db = sqlite3.connect(tmp_path / "usage.sqlite3")
    db.executescript("""
        CREATE TABLE usage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE usage_invocations (
            id TEXT PRIMARY KEY, provider TEXT NOT NULL, profile TEXT,
            model TEXT, observed_at TEXT NOT NULL, status TEXT NOT NULL,
            tokens TEXT NOT NULL, estimated INTEGER NOT NULL DEFAULT 0,
            partial INTEGER NOT NULL DEFAULT 0
        );
    """)
    db.close()
    assert ledger_mark(tmp_path) == (0, 0)
    record_invocation(invocation_id="a", provider="codex", data_dir=tmp_path, run_id="r")
    first = ledger_mark(tmp_path)
    record_invocation(invocation_id="a", provider="codex", data_dir=tmp_path, run_id="r")
    second = ledger_mark(tmp_path)
    assert first is not None and second is not None
    assert first[0] == second[0] and second[1] == first[1] + 1
    with usage_db(tmp_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 1
        assert db.execute("SELECT run_id FROM usage_invocations").fetchone()[0] == "r"
