"""The Second Brain search test set: fixture baseline, pair collection and saved runs."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "brain-search"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "brain_search_test_set", REPO_ROOT / "scripts" / "brain-search-test-set.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load_script()


def test_fixture_search_keeps_its_recorded_top5(tmp_path: Path) -> None:
    """Fails when a ranking change drops top-5 on the fixture below the recorded baseline.

    After a change that improves ranking, record the new baseline with
    ``scripts/brain-search-test-set.py fixture --record``.
    """
    baseline = json.loads((FIXTURE / "baseline.json").read_text(encoding="utf-8"))
    queries = json.loads((FIXTURE / "queries.json").read_text(encoding="utf-8"))["pairs"]
    assert 25 <= len(queries) <= 40
    run = script.fixture_run(tmp_path)
    assert run["pairs"] == baseline["pairs"] == len(queries)
    for mode, recorded in baseline["metrics"].items():
        top5 = run["metrics"][mode]["top5"]
        missed = [
            (pair["query"], result["ranks"][mode])
            for pair, result in zip(queries, run["results"], strict=True)
            if not result["ranks"][mode] or result["ranks"][mode] > 5
        ]
        assert top5 >= recorded["top5"], f"{mode} top-5 {top5:.4f} < {recorded['top5']}: {missed}"


def wait_idle(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain tasks did not finish")


def write_thread(archive: Path, key: str, body: str, *, task: bool, title: str = "") -> None:
    thread: dict[str, Any] = {
        "thread": {"id": f"thread-{key}", "title": title or key, "createdAt": f"2026-11-0{key}"},
        "messages": [
            {"id": "m1", "role": "user", "body": body},
            {"id": "m2", "role": "assistant", "body": "Done."},
        ],
    }
    if task:
        thread["thread"]["managedRole"] = "execute"
    (archive / "threads" / f"{key}.json").write_text(json.dumps(thread), encoding="utf-8")


def make_brain(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "brain"
    (root / "Daily Notes").mkdir(parents=True)
    (root / "Reports").mkdir()
    (root / "Daily Notes" / "harbor survey.md").write_text(
        "# Harbor survey\n\nThe harbor survey found silt near the north pier and two cracked "
        "bollards on the ferry dock.\n",
        encoding="utf-8",
    )
    (root / "Reports" / "budget.md").write_text(
        "# Budget\n\nThe ferry budget covers dredging and dock repairs.\n", encoding="utf-8"
    )
    archive = root / "Rem Conversations"
    (archive / "threads").mkdir(parents=True)
    (archive / "attachments").mkdir()
    (archive / "attachments" / "log.md").write_text("# Log\n\nharbor\n", encoding="utf-8")
    (archive / "README.raticode.md").write_text("# Rem conversation archive\n")
    link = (root / "Daily Notes" / "harbor survey.md").as_uri()
    write_thread(
        archive,
        "1",
        f"Plan the dredging work near the north pier for the ferry operator.\n"
        f"Use {link} as background.",
        task=True,
    )
    # A chat thread, a file outside the brain, an archive file and a vague request.
    write_thread(
        archive, "2", "Summarize Reports/budget.md for the ferry board meeting.", task=False
    )
    write_thread(archive, "3", f"Read {tmp_path}/elsewhere.md and fix it.", task=True)
    write_thread(
        archive, "4", "Look at Rem Conversations/attachments/log.md for the error.", task=True
    )
    write_thread(
        archive, "5", f"Fix this: {root}/Reports/budget.md", task=True, title="Ferry budget fixes"
    )
    threads = {
        key: {"threadId": f"thread-{key}", "snapshot": f"threads/{key}.json"} for key in "12345"
    }
    (archive / "index.json").write_text(json.dumps({"version": 1, "threads": threads}))
    data = tmp_path / "data"
    library = BrainLibrary(data, start=False)
    try:
        library.open({"root": str(root)})
        wait_idle(library)
    finally:
        library.close()
    return root, data


def test_collect_prune_and_run_keep_a_private_test_set(tmp_path: Path, capsys) -> None:
    root, data = make_brain(tmp_path)
    assert script.main(["collect", "--data-dir", str(data)]) == 0
    folders = list((data / "second-brains" / "search-test-sets").iterdir())
    assert len(folders) == 1
    pairs_file = folders[0] / "pairs.json"
    assert stat.S_IMODE(pairs_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(folders[0].stat().st_mode) == 0o700
    pairs = json.loads(pairs_file.read_text(encoding="utf-8"))["pairs"]
    # Task threads only, files in the brain only, never the archive's own files.
    assert {pair["path"] for pair in pairs} == {"Daily Notes/harbor survey.md", "Reports/budget.md"}
    by_path = {pair["path"]: pair for pair in pairs}
    harbor = by_path["Daily Notes/harbor survey.md"]
    assert harbor["query"] == "Plan the dredging work near the north pier for the ferry operator"
    assert harbor["querySource"] == "first"
    # "Fix this:" is too short to be a request, so the thread title stands in.
    assert by_path["Reports/budget.md"]["query"] == "Ferry budget fixes"
    assert by_path["Reports/budget.md"]["querySource"] == "title"

    budget_id = by_path["Reports/budget.md"]["id"]
    assert script.main(["prune", budget_id, "--data-dir", str(data)]) == 0
    # Collecting again neither brings the pruned pair back nor undoes a hand edit.
    stored = json.loads(pairs_file.read_text(encoding="utf-8"))
    stored["pairs"][0]["query"] = "dredging north pier"
    pairs_file.write_text(json.dumps(stored), encoding="utf-8")
    assert script.main(["collect", "--data-dir", str(data)]) == 0
    stored = json.loads(pairs_file.read_text(encoding="utf-8"))
    assert [pair["query"] for pair in stored["pairs"]] == ["dredging north pier"]
    assert stored["pruned"] == [budget_id]

    capsys.readouterr()
    assert script.main(["run", "--data-dir", str(data)]) == 0
    assert script.main(["run", "--data-dir", str(data)]) == 0
    output = capsys.readouterr().out
    assert "exact: top-1 1.000  top-5 1.000  top-10 1.000  top-20 1.000  MRR 1.000" in output
    assert "task-prompt: top-1 1.000" in output
    assert "Rem's tool, document in first 50 passages: 1.000" in output
    assert "Rem's tool, passages that are only a heading: 0.000" in output
    assert "exact: top1 +0.000" in output
    runs = sorted((folders[0] / "runs").glob("*.json"))
    assert runs
    saved = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert saved["modes"] == ["exact"]
    assert saved["results"][0]["ranks"] == {"exact": 1}
    assert saved["results"][0]["inToolPassages"] is True
    assert saved["results"][0]["headingOnlyPassages"] == 0
    assert saved["toolHeadingOnly"] == 0
    assert stat.S_IMODE(runs[-1].stat().st_mode) == 0o600


def test_test_set_is_never_saved_in_the_repository() -> None:
    brain = {"id": "brain-x", "root": str(REPO_ROOT / "tests")}
    with pytest.raises(SystemExit, match="inside the repository"):
        script.store_folder(REPO_ROOT / "data", brain, "data")
    with pytest.raises(SystemExit, match="inside the repository"):
        script.store_folder(Path("/elsewhere"), brain, "brain")


def test_queries_drop_file_names_and_query_syntax() -> None:
    text = 'Check "Reports/a b.md" OR -draft field:value at https://example.com/x today please now'
    query, source = script.request_query(text, "Reports/a b.md", "Title", ["Reports/a b.md"])
    assert source == "first"
    assert query == "Check or draft field value at today please now"


def test_documents_added_after_the_request_never_count_against_it(tmp_path: Path) -> None:
    """A report written after the request outranks the target, but the rank ignores it."""
    root = tmp_path / "brain"
    root.mkdir()
    target = root / "lighthouse.md"
    target.write_text(
        "# Lighthouse\n\nThe keeper logged the lens repair schedule with the harbor office.\n",
        encoding="utf-8",
    )
    later = root / "lighthouse-report.md"
    later.write_text(
        "# Lighthouse lens repair schedule\n\nLighthouse lens repair schedule: the lighthouse "
        "lens repair schedule moved, so the lens repair schedule report covers it again.\n",
        encoding="utf-8",
    )
    january = datetime(2026, 1, 5, tzinfo=UTC).timestamp()
    june = datetime(2026, 6, 5, tzinfo=UTC).timestamp()
    os.utime(target, (january, january))
    os.utime(later, (june, june))
    library = BrainLibrary(tmp_path / "data", start=False)
    try:
        brain = library.open({"root": str(root)})
        wait_idle(library)
        brain = library.brain(brain["id"])
        known = script.known_documents(library, brain["id"])
        pair = {
            "id": "p1",
            "query": "lighthouse lens repair schedule",
            "path": "lighthouse.md",
            "docId": known["lighthouse.md"],
            "querySource": "rem-search",
            "time": "2026-03-01T12:00:00Z",
        }
        appeared = script.document_dates(library, brain["id"])
        # Added just now, but modified in June: the earlier date says when it appeared.
        assert appeared[known["lighthouse-report.md"]] == datetime.fromtimestamp(june, UTC)
        everything = script.evaluate(library, brain, [pair], modes=["exact"])
        as_of = script.evaluate(library, brain, [pair], modes=["exact"], appeared=appeared)
        undated = script.evaluate(
            library, brain, [{**pair, "time": None}], modes=["exact"], appeared=appeared
        )
    finally:
        library.close()
    assert everything["results"][0]["ranks"] == {"exact": 2}
    assert as_of["results"][0]["ranks"] == {"exact": 1}
    assert as_of["results"][0]["laterDocuments"] == 1
    assert as_of["sources"]["rem-search"]["metrics"]["exact"]["top1"] == 1.0
    # A pair with no time leaves nothing out.
    assert undated["results"][0]["ranks"] == {"exact": 2}


def journal_line(sequence: int, time: str, trace: dict[str, Any] | None = None, **record: Any):
    if trace is not None:
        message = {"id": f"m{sequence}", "role": "assistant", "kind": "thought", "trace": trace}
        record = {"type": "message", "message": message}
    return json.dumps({"version": 1, "time": time, "sequence": sequence, **record})


def tool(trace_id: str, title: str, phase: str, arguments: dict[str, Any] | None, **extra: Any):
    text = json.dumps(arguments, indent=2) if arguments is not None else "{}"
    status = "complete" if phase == "result" else "running"
    return {
        "id": trace_id,
        "kind": "tool",
        "title": title,
        "input": text,
        "phase": phase,
        "status": status,
        **extra,
    }


def test_rem_searches_followed_by_a_read_become_pairs(tmp_path: Path, capsys) -> None:
    root, data = make_brain(tmp_path)
    library = BrainLibrary(data, start=False)
    try:
        brain = library.brains()[0]
        known = script.known_documents(library, brain["id"])
    finally:
        library.close()
    harbor_id = known["Daily Notes/harbor survey.md"]
    search, read = "mcp__second_brain__search", "mcp__second_brain__read_note"
    lines = [
        journal_line(
            1,
            "2026-11-20T10:00:00.000Z",
            type="thread",
            thread={
                "id": "rem-thread",
                "title": "Ferry questions",
                "managedRole": "execute",
                "createdAt": "2026-11-20T09:59:00.000Z",
            },
        ),
        # Claude Code: the arguments arrive in the update record after the start.
        journal_line(2, "2026-11-20T10:00:01.000Z", tool("toolu_1", search, "start", None)),
        journal_line(
            3,
            "2026-11-20T10:00:02.000Z",
            tool("toolu_1", search, "update", {"query": "silt north pier"}),
        ),
        journal_line(4, "2026-11-20T10:00:03.000Z", tool("toolu_2", read, "start", None)),
        journal_line(
            5,
            "2026-11-20T10:00:04.000Z",
            tool("toolu_2", read, "update", {"brainId": brain["id"], "documentId": harbor_id}),
        ),
        # A search with no read before the next search gives no pair.
        journal_line(
            6,
            "2026-11-20T10:01:00.000Z",
            tool("toolu_3", search, "start", {"query": "nothing opened"}),
        ),
        # Codex: bare names, the server in detail; trace IDs repeat in later turns.
        journal_line(
            7,
            "2026-11-20T10:02:00.000Z",
            tool(
                "item_1",
                "search",
                "start",
                {"query": "ferry dock repairs budget"},
                detail="second_brain",
            ),
        ),
        journal_line(
            8,
            "2026-11-20T10:02:01.000Z",
            tool(
                "item_2",
                "read_note",
                "start",
                {"path": f"{root}/Reports/budget.md"},
                detail="second_brain",
            ),
        ),
        # A read in another brain, an archive file and a repeat read don't count.
        journal_line(
            9,
            "2026-11-20T10:02:02.000Z",
            tool(
                "item_3",
                "read_note",
                "start",
                {"brainId": "brain-other", "path": "Reports/budget.md"},
                detail="second_brain",
            ),
        ),
        journal_line(
            10,
            "2026-11-20T10:02:03.000Z",
            tool(
                "item_4",
                "read_note",
                "start",
                {"path": "Rem Conversations/attachments/log.md"},
                detail="second_brain",
            ),
        ),
        journal_line(
            11,
            "2026-11-20T10:02:04.000Z",
            tool(
                "item_2", "read_note", "start", {"path": "Reports/budget.md"}, detail="second_brain"
            ),
        ),
        # A bare search counts when its input names the brain; another tool's doesn't.
        journal_line(
            12,
            "2026-11-20T10:02:30.000Z",
            tool("item_1", "search", "start", {"brainId": brain["id"], "query": "harbor bollards"}),
        ),
        journal_line(
            12,
            "2026-11-20T10:03:00.000Z",
            tool("item_1", "search", "start", {"query": "web results"}, detail="web"),
        ),
        journal_line(
            13,
            "2026-11-20T10:03:01.000Z",
            tool("item_5", "read_note", "start", {"documentId": harbor_id}, detail="second_brain"),
        ),
        "not json",
    ]
    archive = root / "Rem Conversations"
    (archive / "threads" / "rem.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    pairs = script.search_pairs(root, known, brain["id"])
    found = [(pair["query"], pair["path"], pair["time"]) for pair in pairs]
    assert found == [
        ("silt north pier", "Daily Notes/harbor survey.md", "2026-11-20T10:00:01.000Z"),
        ("ferry dock repairs budget", "Reports/budget.md", "2026-11-20T10:02:00.000Z"),
        ("harbor bollards", "Daily Notes/harbor survey.md", "2026-11-20T10:02:30.000Z"),
    ]
    assert {pair["querySource"] for pair in pairs} == {"rem-search"}
    assert pairs[0]["docId"] == harbor_id
    assert pairs[0]["thread"] == {
        "id": "rem-thread",
        "title": "Ferry questions",
        "kind": "task",
        "createdAt": "2026-11-20T09:59:00.000Z",
    }

    assert script.main(["collect", "--data-dir", str(data)]) == 0
    stored = next((data / "second-brains" / "search-test-sets").iterdir()) / "pairs.json"
    test_set = json.loads(stored.read_text(encoding="utf-8"))
    sources = sorted(pair["querySource"] for pair in test_set["pairs"])
    assert sources == ["first", "rem-search", "rem-search", "rem-search", "title"]
    prompt = next(pair for pair in test_set["pairs"] if pair["querySource"] == "first")
    assert prompt["time"] == "2026-11-01"
    # Prune drops a search pair like any other, and collect leaves it out afterwards.
    search_id = pairs[1]["id"]
    assert script.main(["prune", search_id, "--data-dir", str(data)]) == 0
    assert script.main(["collect", "--data-dir", str(data)]) == 0
    test_set = json.loads(stored.read_text(encoding="utf-8"))
    assert search_id not in {pair["id"] for pair in test_set["pairs"]}
    assert test_set["pruned"] == [search_id]

    capsys.readouterr()
    assert script.main(["run", "--data-dir", str(data)]) == 0
    output = capsys.readouterr().out
    assert "4 pairs (task-prompt 2, rem-search 2)" in output
    for label in ("exact", "task-prompt", "rem-search"):
        assert f"{label}: top-1 " in output
    assert "top-20" in output and "MRR" in output
