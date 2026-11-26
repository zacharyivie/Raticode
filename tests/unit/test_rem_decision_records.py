"""Decision records: one book of decisions, records in the repo, and checks that keep to them.

R6 (records stored in the project's repository and changed only with the user's approval),
R7 (workers get a capped index; Raticode's check and the reviewer hold the work to the
records whose paths it touches) and R8 (a proposal that contradicts a decision waits).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_decision_records as records
from gofer.ui import rem_decisions as decisions
from gofer.ui.rem_decisions import DecisionConflict, DecisionRefused, DecisionRequest
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import WorkerReply
from gofer.ui.rem_self_work import Proposal, contradicted
from tests.unit.test_rem_decisions import Store
from tests.unit.test_rem_home import home, recipe, wait_run  # noqa: F401
from tests.unit.test_rem_home_landing import shell

RESOURCES = {"shell": True, "web": False, "agents": False, "browser": False, "skills": []}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_AUTHOR_EMAIL": "tester@example.invalid",
    "GIT_COMMITTER_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "tester@example.invalid",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout


def repo(tmp_path: Path, *, branch: str = "dev") -> Path:
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    git(root, "init", "-q", "-b", branch)
    git(root, "config", "user.name", "Tester")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def record_request(answer: str = "Workflows are written in Rattish, never TOML", **kw: Any):
    return DecisionRequest.model_validate(
        {
            "topic": "adr:workflow-format",
            "question": "How are workflows written?",
            "recommendation": {
                "answer": answer,
                "why": "The compiler only reads Rattish",
                "evidence": [{"kind": "file", "ref": "src/main.py"}],
            },
            "remember_as": "record",
            "record": {
                "context": "Two formats were in use.",
                "options": "TOML: no source positions.",
                "applies_to": ["src/**"],
                "forbids": ["**/*.toml"],
                "signals": ["workflow toml"],
            },
            **kw,
        }
    )


def decide(home: RemHome, root: Path, topic: str, answer: str) -> None:  # noqa: F811
    home.change_decisions({"action": "add", "topic": topic, "answer": answer, "project": str(root)})


def files_on(root: Path, branch: str = "dev") -> list[str]:
    listed = git(root, "ls-tree", "-r", "--name-only", branch, "--", ".raticode/decisions")
    return [line for line in listed.splitlines() if line]


# Format -------------------------------------------------------------------------------


def test_a_record_renders_and_reads_back():
    item = {
        "id": "D-7",
        "question": "How are workflows written?",
        "answer": "Rattish, never TOML",
        "status": "confirmed",
        "decided_by": "user",
        "decided_at": "2026-11-20T10:00:00+00:00",
        "record": {"context": "Two formats.", "applies_to": ["src/**"], "forbids": ["*.toml"]},
    }
    text = records.render(item)
    assert text.startswith("---\nid: D-7\n") and "status: accepted" in text
    assert "## Decision\n\nRattish, never TOML" in text
    parsed = records.parse(text)
    assert parsed["title"] == "How are workflows written?"
    assert parsed["decision"] == "Rattish, never TOML" and parsed["status"] == "accepted"
    assert parsed["record"]["applies_to"] == ["src/**"]
    assert parsed["record"]["forbids"] == ["*.toml"]
    assert parsed["record"]["context"] == "Two formats."


def test_existing_nygard_and_madr_records_read_as_they_are():
    nygard = "# 3. Use Postgres\n\n## Status\n\nSuperseded by 5\n\n## Decision\n\nWe use it.\n"
    parsed = records.parse(nygard)
    assert parsed["title"] == "Use Postgres" and parsed["status"] == "superseded"
    assert parsed["decision"] == "We use it."
    madr = (
        "---\nstatus: proposed\ndate: 2024-01-01\n---\n# Use MADR\n\n"
        "## Context and Problem Statement\n\nWe need records.\n\n"
        "## Decision Outcome\n\nChosen option: MADR.\n"
    )
    parsed = records.parse(madr)
    assert parsed["status"] == "proposed" and parsed["title"] == "Use MADR"
    assert parsed["decision"] == "Chosen option: MADR."
    assert parsed["record"]["context"] == "We need records."


def test_path_globs():
    assert records.glob_match("src/gofer/a.py", "src/**")
    assert records.glob_match("a/b/workflow.toml", "**/*.toml")
    assert records.glob_match("workflow.toml", "**/*.toml")
    assert not records.glob_match("src/a.py", "*.toml")
    assert records.glob_match("docs/adr/0001.md", "docs/adr")
    assert not records.glob_match("docs/adrs.md", "docs/adr")


def test_a_leading_slash_means_the_project_root_and_patterns_match_in_linear_time():
    """Nightly audit, 22 November: ``/secrets/**`` stopped matching ``secrets/key.pem``
    after a06c1fee, and a pattern like ``**?**?…#`` ran for 48 seconds on one path, which
    a worker's decision request could put on every read of the decisions list."""
    import time

    fields = records.RecordFields.model_validate({"forbids": ["/secrets/**"]})
    assert fields.forbids == ["secrets/**"]
    assert records.glob_match("secrets/key.pem", "/secrets/**")
    assert records.any_match("secrets/key.pem", ["/secrets/**"])
    started = time.monotonic()
    assert not records.glob_match("a" * 60, "**?" * 9 + "#")
    assert not records.glob_match("src/" + "a/" * 40 + "x.py", "**/" * 12 + "*?*?*?#")
    assert time.monotonic() - started < 1
    # A trailing slash, ``**/`` with nothing before it and ``?`` keep their meaning.
    assert records.glob_match("docs/a/b.md", "docs/")
    assert records.glob_match("x.toml", "**/*.toml")
    assert records.glob_match("a1.py", "a?.py") and not records.glob_match("a/.py", "a?.py")


def test_dot_paths_keep_their_dots():
    fields = records.RecordFields.model_validate(
        {"applies_to": [".github/workflows/**", "./.env", "./src/**"], "forbids": ".env"}
    )
    assert fields.applies_to == [".github/workflows/**", ".env", "src/**"]
    assert fields.forbids == [".env"]
    assert records.glob_match(".github/workflows/ci.yml", ".github/workflows/**")
    assert not records.glob_match("github/workflows/ci.yml", ".github/workflows/**")
    assert not records.glob_match(".github/workflows/ci.yml", "github/workflows/**")
    assert records.glob_match(".env", ".env")
    assert records.glob_match("./.env", ".env")
    assert not records.glob_match("env", ".env")
    assert not records.glob_match(".env", "env")
    assert records.glob_match("./src/a.py", "src/**")


def test_signal_words_match_whole_words_in_any_case():
    item = {"id": "D-7", "record": {"signals": ["rust", "toml workflows", "c++"]}}
    assert records.signal_hits([item], "use Rust here") == ["D-7"]
    assert records.signal_hits([item], "We trust the checks; frustrated by rusty code") == []
    assert records.signal_hits([item], "Read TOML\n  Workflows from disk") == ["D-7"]
    assert records.signal_hits([item], "tomlworkflows") == []
    assert records.signal_hits([item], "Port it to C++.") == ["D-7"]


# R6: records in the repo, changed only with the user's approval -------------------------


def test_rem_adds_a_provisional_record_in_auto_mode_and_it_lands_in_the_repo(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    with home.store.connect() as db:
        outcome, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    assert outcome == "provisional"
    home.write_records(str(root))
    written = files_on(root)
    assert written == [
        f".raticode/decisions/{int(item['id'][2:]):04d}-how-are-workflows-written.md"
    ]
    text = git(root, "show", f"dev:{written[0]}")
    assert "status: provisional" in text and "decided_by: Rem, in auto mode" in text
    # The checkout of dev was clean, so it moved with the branch and shows the file.
    assert (root / written[0]).read_text() == text
    assert git(root, "status", "--porcelain") == ""
    stored = next(i for i in home.decisions()["items"] if i["id"] == item["id"])
    assert stored["record_path"] == written[0] and not stored.get("record_error")


def test_rem_can_never_change_an_existing_record_and_the_user_can(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    with home.store.connect() as db:
        _, first = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    home.write_records(str(root))
    path = files_on(root)[0]
    before = git(root, "show", f"dev:{path}")
    # Rem's different answer, even in auto mode, only waits for the user.
    with home.store.connect() as db:
        outcome, proposal = decisions.file_request(
            db,
            home.store,
            record_request("Workflows may also be TOML", replaces=first["id"]),
            project=str(root),
            decides_alone=True,
        )
        with pytest.raises(DecisionRefused, match="Only the user"):
            decisions.change(
                db, home.store, {"action": "edit", "id": first["id"], "answer": "x"}, actor="rem"
            )
        with pytest.raises(DecisionRefused, match="Only the user"):
            decisions.change(
                db,
                home.store,
                {"action": "replace", "id": first["id"], "answer": "x"},
                actor="rem",
            )
    assert outcome == "pending" and proposal["supersedes"] == first["id"]
    home.write_records(str(root))
    assert files_on(root) == [path] and git(root, "show", f"dev:{path}") == before
    # A file Raticode wrote isn't rewritten when the last change wasn't the user's.
    with home.store.read() as db:
        book = decisions.read(db, home.store)
    tampered = next(i for i in book["items"] if i["id"] == first["id"])
    tampered = {**tampered, "answer": "changed by Rem", "history": [{"actor": "rem"}]}
    book["items"] = [tampered if i["id"] == first["id"] else i for i in book["items"]]
    files = {path: before}
    assert records.pending_writes(book, str(root), files) == []
    # The user approves the replacement: the old record is marked replaced, a new one lands.
    home.change_decisions({"action": "use", "id": proposal["id"]})
    after = git(root, "show", f"dev:{path}")
    assert "status: superseded" in after and f"superseded_by: {proposal['id']}" in after
    assert len(files_on(root)) == 2
    # The user edits a record's parts; its file follows.
    home.change_decisions(
        {
            "action": "edit",
            "id": proposal["id"],
            "consequences": "Old TOML files need converting.",
            "note": "clarified",
        }
    )
    new_path = next(p for p in files_on(root) if p != path)
    assert "Old TOML files need converting." in git(root, "show", f"dev:{new_path}")


def test_the_user_replaces_a_record_and_both_files_link(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    view = home.change_decisions(
        {
            "action": "add",
            "project": str(root),
            "remember_as": "record",
            "question": "Which database?",
            "answer": "SQLite",
            "applies_to": ["src/db/**"],
        }
    )
    old = next(i for i in view["items"] if i["question"] == "Which database?")
    assert old["remember_as"] == "record" and old["record"]["applies_to"] == ["src/db/**"]
    view = home.change_decisions(
        {"action": "replace", "id": old["id"], "answer": "Postgres", "note": "scale"}
    )
    new = next(i for i in view["items"] if i.get("supersedes") == old["id"])
    assert new["record"]["applies_to"] == ["src/db/**"]  # kept unless given
    replaced = next(i for i in view["items"] if i["id"] == old["id"])
    assert replaced["status"] == "superseded" and replaced["superseded_by"] == new["id"]
    texts = [git(root, "show", f"dev:{p}") for p in files_on(root)]
    assert any("status: superseded" in t for t in texts)
    assert any(f'supersedes: ["{old["id"]}"]' in t and "Postgres" in t for t in texts)


def written_record(tmp_path: Path) -> tuple[sqlite3.Connection, Store, Path, dict[str, Any]]:
    """A record in force whose file Raticode wrote, as ``write_records`` leaves it."""
    db, store = sqlite3.connect(":memory:"), Store()
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    _, item = decisions.file_request(
        db, store, record_request(), project=str(root), decides_alone=True
    )
    book = decisions.read(db, store)
    [write] = records.pending_writes(book, str(root), {})
    records.mark_written(db, store, item["id"], write, {"ok": True, "commit": "a" * 40})
    files = {write["path"]: write["text"]}
    return db, store, root, files


def test_retiring_a_written_record_keeps_it_and_its_file_never_brings_it_back(
    tmp_path: Path,
):
    db, store, root, files = written_record(tmp_path)
    [path] = files
    before = decisions.read(db, store)
    [item] = before["items"]
    assert item["record_path"] == path and "status: provisional" in files[path]
    # Forget never turns into Retire's commit on the landing branch: it is refused and says
    # what to use instead (self-work report, 21 November afternoon).
    with pytest.raises(DecisionRefused, match="Retire it instead") as refused:
        decisions.change(db, store, {"action": "forget", "id": item["id"]}, actor="user")
    assert path in str(refused.value) and "commits that on the landing branch" in str(refused.value)
    assert decisions.read(db, store) == before
    decisions.change(db, store, {"action": "retire", "id": item["id"]}, actor="user")
    # The file is still on the landing branch, saying the old status, until it's rewritten.
    assert records.sync(db, store, str(root), files=files) == []
    after = decisions.read(db, store)
    assert after["next_number"] == before["next_number"]
    assert [i["id"] for i in after["items"]] == [item["id"]]
    [retired] = after["items"]
    assert retired["status"] == "rejected" and retired["retired_at"]
    assert retired["history"][-1]["action"] == "retired"
    assert retired["history"][-1]["actor"] == "user"
    # Workers and reviewers no longer get it.
    assert records.in_force_records(after, str(root)) == []
    assert decisions.for_prompt(after, [str(root)]) == []
    # Its file is rewritten as turned down, through the usual write on the landing branch.
    [write] = records.pending_writes(after, str(root), files)
    assert write["id"] == item["id"] and write["path"] == path and not write["new"]
    assert "status: rejected" in write["text"]
    records.mark_written(db, store, item["id"], write, {"ok": True, "commit": "b" * 40})
    files = {path: write["text"]}
    assert records.sync(db, store, str(root), files=files) == []
    final = decisions.read(db, store)
    assert final["next_number"] == before["next_number"] and len(final["items"]) == 1
    assert records.pending_writes(final, str(root), files) == []
    # Already retired: there's nothing left to do, and it still isn't deleted.
    for action, why in (("forget", "Retire it instead"), ("retire", "already closed")):
        with pytest.raises(DecisionRefused, match=why):
            decisions.change(db, store, {"action": action, "id": item["id"]}, actor="user")
    assert len(decisions.read(db, store)["items"]) == 1


def test_forget_still_deletes_a_decision_with_no_file(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    _, item = decisions.file_request(
        db, store, record_request(), project=str(root), decides_alone=True
    )
    # A record not written yet, and a plain fact, have no file to bring them back.
    with pytest.raises(DecisionRefused, match="forget this one instead"):
        decisions.change(db, store, {"action": "retire", "id": item["id"]}, actor="user")
    decisions.change(db, store, {"action": "forget", "id": item["id"]}, actor="user")
    assert decisions.read(db, store)["items"] == []
    # Rem can't retire one either.
    db, store, root, files = written_record(tmp_path / "other")
    [item] = decisions.read(db, store)["items"]
    with pytest.raises(DecisionRefused, match="Only the user"):
        decisions.change(db, store, {"action": "retire", "id": item["id"]}, actor="rem")


def test_trimming_old_decisions_keeps_retired_records(tmp_path: Path):
    db, store, root, files = written_record(tmp_path)
    [item] = decisions.read(db, store)["items"]
    decisions.change(db, store, {"action": "retire", "id": item["id"]}, actor="user")
    closed = [
        {"id": f"X-{n}", "status": "rejected", "updated_at": f"2000-01-01T00:00:{n:02d}"}
        for n in range(decisions.MAX_ITEMS)
    ]
    kept = decisions._trim([{**item, "status": "rejected", "updated_at": ""}, *closed])
    assert kept[0]["id"] == item["id"] and len(kept) == decisions.MAX_ITEMS


def test_retiring_a_record_rewrites_its_file_on_the_landing_branch(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    with home.store.connect() as db:
        _, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    home.write_records(str(root))
    [path] = files_on(root)
    count = len(home.decisions()["items"])
    head = git(root, "rev-parse", "dev")
    with pytest.raises(DecisionRefused, match="Retire it instead"):
        home.change_decisions({"action": "forget", "id": item["id"]})
    assert git(root, "rev-parse", "dev") == head, "a refused Forget commits nothing"
    view = home.change_decisions({"action": "retire", "id": item["id"]})
    retired = next(i for i in view["items"] if i["id"] == item["id"])
    assert retired["status"] == "rejected" and retired["retired_at"]
    assert files_on(root) == [path]
    assert "status: rejected" in git(root, "show", f"dev:{path}")
    subject = git(root, "log", "-1", "--format=%s", "dev")
    assert subject.startswith(f"chore: Retire decision {item['id']}")
    assert git(root, "status", "--porcelain") == ""
    view = home.decisions(str(root), sync=True)
    assert len(home.decisions()["items"]) == count
    assert next(i for i in view["items"] if i["id"] == item["id"])["status"] == "rejected"


def test_an_edit_committed_outside_raticode_reaches_the_store(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    folder = root / ".raticode" / "decisions"
    folder.mkdir(parents=True)
    (folder / "0001-tabs.md").write_text(
        '---\nstatus: accepted\ntitle: Indentation\napplies_to: ["src/**"]\n---\n'
        "## Decision\n\nUse four spaces.\n"
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "record")
    view = home.decisions(str(root), sync=True)
    item = next(
        i for i in view["items"] if i.get("record_path") == ".raticode/decisions/0001-tabs.md"
    )
    assert item["answer"] == "Use four spaces." and item["status"] == "confirmed"
    head = git(root, "rev-parse", "HEAD").strip()
    assert item["source"] == {"via": "repository", "commit": head[:12], "author": "Tester"}
    (folder / "0001-tabs.md").write_text(
        "---\nstatus: accepted\ntitle: Indentation\n---\n## Decision\n\nUse tabs.\n"
    )
    git(root, "commit", "-qam", "edit")
    item = next(i for i in home.decisions(str(root), sync=True)["items"] if i["id"] == item["id"])
    assert item["answer"] == "Use tabs." and item["edited_outside"] is True
    # Raticode doesn't write it back over the user's edit.
    assert "Use tabs." in git(root, "show", "dev:.raticode/decisions/0001-tabs.md")


def test_a_record_read_from_the_repository_names_the_commit_that_added_it(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    root = repo(tmp_path)
    folder = root / ".raticode" / "decisions"
    folder.mkdir(parents=True)
    path = ".raticode/decisions/0001-tabs.md"
    (root / path).write_text("---\nstatus: accepted\ntitle: Indentation\n---\nUse tabs.\n")
    git(root, "add", "-A")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "record"],
        check=True,
        env={
            **os.environ,
            **GIT_ENV,
            "GIT_AUTHOR_NAME": "Other Person",
            "GIT_AUTHOR_EMAIL": "other.person@example.invalid",
            "GIT_AUTHOR_DATE": "2026-03-04T05:06:07+02:00",
        },
    )
    added = git(root, "rev-parse", "HEAD").strip()
    # A later edit by someone else doesn't change who added it.
    (root / path).write_text("---\nstatus: accepted\ntitle: Indentation\n---\nUse spaces.\n")
    git(root, "commit", "-qam", "edit")
    files = records.folder_files(str(root), "dev", ".raticode/decisions")
    assert files is not None
    [changed] = records.sync(db, store, str(root), files=files, branch="dev")
    [item] = decisions.read(db, store)["items"]
    assert item["id"] == changed and item["source"] == {
        "via": "repository",
        "commit": added[:12],
        "author": "Other Person",
    }
    assert "example.invalid" not in json.dumps(item)
    assert item["decided_at"] == "2026-03-04T03:06:07+00:00"
    # Status, in force and decided_by are as before: a record file the user adds counts.
    assert item["status"] == "confirmed" and item["decided_by"] == "user"
    assert item["history"][-1]["action"] == "read"
    assert item["history"][-1]["note"] == f"{path}, added in {added[:12]}"


def test_a_record_file_naming_a_fact_decision_never_rewrites_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    decide(home, root, "checks.fast", "pytest -q")
    check = next(i for i in home.decisions()["items"] if i["topic"] == "checks.fast")
    # A cloned repo, or a worker committing straight to the landing branch, names the
    # check's id in a record file to turn the check into a no-op.
    folder = root / ".raticode" / "decisions"
    folder.mkdir(parents=True)
    (folder / "0009-check.md").write_text(
        f"---\nid: {check['id']}\nstatus: accepted\ntitle: Fast check\n---\n## Decision\n\ntrue\n"
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "record")
    items = home.decisions(str(root), sync=True)["items"]
    same = next(i for i in items if i["id"] == check["id"])
    assert same["answer"] == "pytest -q" and not same.get("record_path")
    # The file is read in as a record of its own, not as the check command.
    other = next(i for i in items if i.get("record_path") == ".raticode/decisions/0009-check.md")
    assert other["id"] != check["id"] and other["topic"].startswith("adr:")


def test_a_dirty_landing_checkout_keeps_the_record_in_the_store(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    (root / "src" / "main.py").write_text("print('changed')\n")
    with home.store.connect() as db:
        _, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    failed = home.write_records(str(root))
    assert failed and "uncommitted changes" in failed[0]["reason"]
    assert files_on(root) == []
    stored = next(i for i in home.decisions()["items"] if i["id"] == item["id"])
    assert "uncommitted changes" in stored["record_error"]
    assert (root / "src" / "main.py").read_text() == "print('changed')\n"


def test_reading_the_decisions_list_never_commits_and_a_dirty_checkout_stops_changing_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The desktop polls the list every 2.5 s; each poll used to try the commit again and
    save the same reason, so the revision moved and every poll downloaded the list."""
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    (root / "src" / "main.py").write_text("print('changed')\n")
    with home.store.connect() as db:
        _, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    tip = git(root, "rev-parse", "dev")
    calls: list[str] = []
    real = records.commit_file

    def counted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(records, "commit_file", counted)
    revisions = [home.decisions(sync=True)["revision"] for _ in range(5)]
    assert revisions == [revisions[0]] * 5
    assert git(root, "rev-parse", "dev") == tip
    assert calls == []
    assert files_on(root) == []
    stored = next(i for i in home.decisions()["items"] if i["id"] == item["id"])
    assert not stored.get("record_path")


def test_a_failed_record_write_is_tried_again_only_when_the_checkout_changes(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    (root / "src" / "main.py").write_text("print('changed')\n")
    with home.store.connect() as db:
        _, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    calls: list[str] = []
    real = records.commit_file

    def counted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(records, "commit_file", counted)
    first = home.write_records(str(root))
    assert len(calls) == 1 and "uncommitted changes" in first[0]["reason"]
    revision = home.decisions()["revision"]
    # Nothing it depends on changed: no new try, the same reason, the same revision.
    for _ in range(3):
        again = home.write_records(str(root))
        assert again == [{"id": item["id"], "reason": first[0]["reason"]}]
    home.check_records()
    assert len(calls) == 1 and home.decisions()["revision"] == revision
    # The checkout is clean again: the next try writes the file.
    git(root, "checkout", "--", "src/main.py")
    assert home.write_records(str(root)) == []
    assert len(calls) == 2 and len(files_on(root)) == 1
    stored = next(i for i in home.decisions()["items"] if i["id"] == item["id"])
    assert stored["record_path"] == files_on(root)[0] and not stored.get("record_error")


def test_the_background_check_writes_a_waiting_record_at_most_once_a_minute(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import gofer.ui.rem_home as rem_home_module

    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    with home.store.connect() as db:
        decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    clock = [1000.0]
    monkeypatch.setattr(rem_home_module.time, "monotonic", lambda: clock[0])
    written: list[str] = []

    def write(project: str) -> list[dict[str, Any]]:
        written.append(project)
        return []

    monkeypatch.setattr(home, "write_records", write)
    home.check_records()
    home.check_records()
    assert written == [str(root)]
    clock[0] += rem_home_module.RECORD_CHECK_SECONDS
    home.check_records()
    assert written == [str(root)] * 2


def test_changing_decisions_on_a_clean_checkout_writes_the_waiting_record(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    with home.store.connect() as db:
        _, item = decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    # Reading the list leaves it waiting; the user's next change writes it.
    home.decisions(sync=True)
    assert files_on(root) == []
    view = home.change_decisions(
        {"action": "add", "topic": "checks.fast", "answer": "true", "project": str(root)}
    )
    [path] = files_on(root)
    assert "status: provisional" in git(root, "show", f"dev:{path}")
    stored = next(i for i in view["items"] if i["id"] == item["id"])
    assert stored["record_path"] == path and not stored.get("record_error")
    assert git(root, "status", "--porcelain") == ""


def test_the_same_failure_twice_leaves_the_book_as_it_was(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    root = tmp_path / "app"
    root.mkdir()
    _, item = decisions.file_request(
        db, store, record_request(), project=str(root), decides_alone=True
    )
    [write] = records.pending_writes(decisions.read(db, store), str(root), {})
    failure = {"ok": False, "reason": "app has uncommitted changes on dev"}
    records.mark_written(db, store, item["id"], write, failure)
    before = decisions.read(db, store)
    records.mark_written(db, store, item["id"], write, failure)
    after = decisions.read(db, store)
    assert after["revision"] == before["revision"]
    assert after["updated_at"] == before["updated_at"]
    records.mark_written(db, store, item["id"], write, {"ok": False, "reason": "other"})
    assert decisions.read(db, store)["revision"] == before["revision"] + 1


def test_path_counts_read_each_branch_tip_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = repo(tmp_path)
    book: dict[str, Any] = {"items": []}
    items: list[dict[str, Any]] = [
        {
            "id": f"D-{n}",
            "project": str(root),
            "remember_as": "record",
            "status": "confirmed",
            "record": {"applies_to": ["src/**"], "forbids": [f"x{n}/**"]},
        }
        for n in range(4)
    ]
    monkeypatch.setattr(records, "branch_of", lambda _book, _project: "dev")
    tips: list[str] = []
    real = records._branch_tip

    def counted(project: str, branch: str) -> str | None:
        tips.append(branch)
        return real(project, branch)

    monkeypatch.setattr(records, "_branch_tip", counted)
    records.add_path_counts(book, items)
    assert tips == ["dev"]
    assert all(i["path_counts"]["applies_to"] == {"src/**": 1} for i in items)


def test_without_a_landing_branch_records_stay_in_the_store(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    with home.store.connect() as db:
        decisions.file_request(
            db, home.store, record_request(), project=str(root), decides_alone=True
        )
    assert home.write_records(str(root)) == []
    assert files_on(root) == []


# Conflicts: one recommended fix, always the user's ---------------------------------------


def two_records(db: sqlite3.Connection, store: Store, root: Path) -> list[dict[str, Any]]:
    made = []
    for answer in ("Tabs everywhere", "Spaces everywhere"):
        view = decisions.change(
            db,
            store,
            {
                "action": "add",
                "project": str(root),
                "remember_as": "record",
                "question": f"Indent: {answer}",
                "answer": answer,
            },
            actor="user",
        )
        made.append(view["items"][-1])
    return made


def test_conflicting_decisions_get_one_fix_that_waits_for_the_user(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    a, b = two_records(db, store, tmp_path)
    conflict = DecisionConflict.model_validate(
        {
            "ids": [a["id"], b["id"]],
            "why": "One says tabs, the other spaces.",
            "fix": {"answer": "Four spaces, tabs only in Makefiles", "why": "Matches the code"},
        }
    )
    outcome, fix = decisions.file_conflict(db, store, conflict, project=str(tmp_path))
    assert outcome == "pending" and fix is not None
    assert fix["supersedes_all"] == sorted([a["id"], b["id"]]) and fix["reversible"] is False
    # The same conflict again doesn't add a second card.
    again, same = decisions.file_conflict(db, store, conflict, project=str(tmp_path))
    assert again == "waiting" and same is not None and same["id"] == fix["id"]
    # Nothing changed until the user approves the one fix.
    book = decisions.read(db, store)
    assert {i["id"]: i["status"] for i in book["items"]}[a["id"]] == "confirmed"
    decisions.change(db, store, {"action": "use", "id": fix["id"]}, actor="user")
    book = decisions.read(db, store)
    status = {i["id"]: i["status"] for i in book["items"]}
    assert status[a["id"]] == status[b["id"]] == "superseded"
    assert status[fix["id"]] == "confirmed"


def test_a_conflict_naming_unknown_decisions_is_ignored(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    a, _ = two_records(db, store, tmp_path)
    conflict = DecisionConflict.model_validate(
        {"ids": [a["id"], "D-999"], "why": "x", "fix": {"answer": "y"}}
    )
    assert decisions.file_conflict(db, store, conflict, project=str(tmp_path)) == ("ignored", None)


def test_rem_reports_a_conflict_and_the_worker_sets_it_aside(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    with home.store.connect() as db:
        a, b = two_records(db, home.store, tmp_path)
    reply = WorkerReply.model_validate(
        {
            "next_message": "Carry on.",
            "decision_conflicts": [
                {"ids": [a["id"], b["id"]], "why": "Opposite rules.", "fix": {"answer": "Spaces"}}
            ],
        }
    )
    run = {"id": "r1", "task_id": "t1", "recipe": {"project_dir": str(tmp_path)}}
    filed = home._file_conflicts(run, reply)
    assert len(filed) == 1 and filed[0]["status"] == "pending"
    assert "Don't try to satisfy both" in reply.next_message


# R7: workers get a capped index ----------------------------------------------------------


def test_the_worker_index_stays_under_its_cap_with_many_records(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    for n in range(200):
        decisions.change(
            db,
            store,
            {
                "action": "add",
                "project": str(tmp_path),
                "remember_as": "record",
                "question": f"Decision number {n} about module{n}",
                "answer": f"Module {n} does one thing " + "x" * 80,
                "applies_to": [f"src/module{n}/**"],
            },
            actor="user",
        )
    book = decisions.read(db, store)
    listed = decisions.for_prompt(book, [str(tmp_path)], "Fix a bug in module150")
    assert sum(len(str(e)) for e in listed) <= decisions.PROMPT_CHARS
    assert listed[0]["decision"].startswith("Module 150 does one thing")
    assert "applies_to" not in listed[0] and "question" not in listed[0]
    assert listed[-1]["left_out"] == 200 - (len(listed) - 1)


def test_violations_and_matching(tmp_path: Path):
    db, store = sqlite3.connect(":memory:"), Store()
    decisions.change(
        db,
        store,
        {
            "action": "add",
            "project": str(tmp_path),
            "remember_as": "record",
            "question": "Workflow format",
            "answer": "Rattish, never TOML",
            "applies_to": ["src/**"],
            "forbids": ["**/*.toml"],
        },
        actor="user",
    )
    book = decisions.read(db, store)
    broken = records.violations(
        book,
        str(tmp_path),
        [("A", "src/flow.toml"), ("M", ".raticode/decisions/0001-x.md"), ("D", "old.toml")],
    )
    assert [b["path"] for b in broken] == [".raticode/decisions/0001-x.md", "src/flow.toml"]
    assert "Rattish, never TOML" in broken[1]["why"]
    found = records.matching(records.in_force_records(book, str(tmp_path)), ["src/a.py"])
    assert [i["question"] for i in found] == ["Workflow format"]
    assert records.matching(records.in_force_records(book, str(tmp_path)), ["README.md"]) == []


def worker_source(root: Path, review_result: str, record_id: str):
    calls: dict[str, list[dict[str, Any]]] = {"work": [], "review": []}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls["review"].append(kwargs)
            text = kwargs["agent_instructions"]
            ids = [record_id] if '"decision_records"' in text else []
            yield {
                "type": "thought",
                "text": "Independent check",
                "trace": {
                    "id": f"review-{len(calls['review'])}",
                    "category": "shell",
                    "phase": "result",
                    "status": "completed",
                    "exitCode": 0,
                    "output": "ok",
                },
            }
            verdict = {
                "criteria": [{"id": "check", "result": "pass", "evidence": "Saw it"}],
                "decisions": [
                    {"id": i, "result": review_result, "evidence": "Read the change"} for i in ids
                ],
                "feedback": "",
            }
            yield {"type": "final", "message": {"body": json.dumps(verdict)}}
            return
        calls["work"].append(kwargs)
        if len(calls["work"]) == 1:
            (root / "src" / "flow.toml").write_text("[workflow]\n")
        else:
            (root / "src" / "flow.toml").unlink(missing_ok=True)
            (root / "src" / "flow.rattish").write_text("Rattish: 1\n")
        yield shell("python -m pytest")
        yield {"type": "final", "message": {"body": f"Turn {len(calls['work'])} done."}}

    return source, calls


def project_with_record(home: RemHome, tmp_path: Path) -> tuple[Path, str]:  # noqa: F811
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    view = home.change_decisions(
        {
            "action": "add",
            "project": str(root),
            "remember_as": "record",
            "question": "How are workflows written?",
            "answer": "Rattish, never TOML",
            "applies_to": ["src/**"],
            "forbids": ["**/*.toml"],
        }
    )
    item = next(i for i in view["items"] if i["remember_as"] == "record")
    return root, item["id"]


def test_the_check_fails_a_change_that_breaks_a_decision_and_the_reviewer_checks_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root, record_id = project_with_record(home, tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    source, calls = worker_source(root, "pass", record_id)
    home.source = source
    task = home.create_task({"request_id": "records", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    # Turn 1 added a forbidden TOML file: Raticode's check sent it back, no reviewer saw it.
    assert len(calls["work"]) == 2 and len(calls["review"]) == 1
    repair = calls["work"][1]["messages"][-1]["body"]
    assert "Raticode's decision check failed" in repair and record_id in repair
    # The worker had the index and the rule from the start.
    first = calls["work"][0]["agent_instructions"]
    assert "project_decisions_rule" in first and "Rattish, never TOML" in first
    # The reviewer got the record in full because the change touched src/.
    review = calls["review"][0]["agent_instructions"]
    assert "decision_records" in review and record_id in review
    assert "Also check the change against each of them" in review
    with home.store.read() as db:
        stored = home.store.get(db, "runs", run["id"])
    assert stored["decision_check"]["passed"] and stored["decision_check"]["record_ids"] == [
        record_id
    ]
    assert stored["decision_review"] == [{"id": record_id, "result": "pass"}]


def test_a_reviewer_failing_a_decision_sends_the_work_back(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root, record_id = project_with_record(home, tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    source, calls = worker_source(root, "fail", record_id)
    home.source = source
    task = home.create_task({"request_id": "records-fail", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked", "failed"}, timeout=30)
    assert run["state"] != "succeeded", run
    assert len(calls["review"]) >= 1
    later = [c["messages"][-1]["body"] for c in calls["work"][2:]]
    assert any("breaks the project's decision records" in body for body in later)
    assert any(record_id in body for body in later)


def test_the_check_still_fails_work_the_worker_already_landed(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, record_id = project_with_record(home, tmp_path)
    record_path = files_on(root)[0]
    approved = git(root, "show", f"dev:{record_path}")
    # The run starts: the landing branch's tip is recorded.
    tips = {"dev": git(root, "rev-parse", "dev").strip()}
    # Another task lands unrelated work meanwhile; it isn't this run's change.
    git(root, "checkout", "-q", "-b", "other")
    (root / "src" / "other.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "other work")
    git(root, "checkout", "-q", "dev")
    git(root, "merge", "-q", "--ff-only", "other")
    # The worker works in its own worktree, commits a forbidden file and a record edit,
    # then fast-forwards dev as the worktree flow says, before Raticode checks anything.
    worktree = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "work", str(worktree), "dev")
    (worktree / "src" / "flow.toml").write_text("[workflow]\n")
    (worktree / record_path).write_text(approved.replace("Rattish, never TOML", "TOML is fine"))
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "work")
    git(root, "merge", "-q", "--ff-only", "work")
    # The run is still going: a sync now leaves the changed record alone.
    monkeypatch.setattr(home, "_writer_running", lambda project: True)
    home.write_records(str(root))
    item = next(i for i in home.decisions()["items"] if i["id"] == record_id)
    assert item["answer"] == "Rattish, never TOML" and not item.get("edited_outside")
    monkeypatch.setattr(home, "_update", lambda *a, **k: None)
    run = {
        "id": "run-1",
        "task_id": "task-1",
        "recipe": {"type": "develop", "project_dir": str(root)},
        "worktree": {"root": str(worktree)},
        "branch_tips": tips,
    }
    checked = home._decision_check(run, [])
    assert checked is not None and not checked["passed"]
    broken = {b["path"] for b in checked["broken"]}
    assert broken == {"src/flow.toml", record_path}
    assert "src/other.py" not in checked["changed"]
    assert [r["id"] for r in checked["records"]] == [record_id]
    # Once the run's commits are known, its record edit is refused, not read in as the
    # user's: the store keeps the approved decision and Raticode puts the file back.
    monkeypatch.setattr(home, "_writer_running", lambda project: False)
    home.write_records(str(root))
    item = next(i for i in home.decisions()["items"] if i["id"] == record_id)
    assert item["answer"] == "Rattish, never TOML" and not item.get("edited_outside")
    assert any(h["action"] == "kept the approved text" for h in item["history"])
    assert git(root, "show", f"dev:{record_path}") == approved
    # A record file a run adds is never read in as a decision either.
    git(worktree, "merge", "-q", "--ff-only", "dev")
    (worktree / ".raticode" / "decisions" / "0999-sneaky.md").write_text(
        "---\nstatus: accepted\ntitle: Sneaky\n---\n## Decision\n\nAnything goes.\n"
    )
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "more work")
    git(root, "merge", "-q", "--ff-only", "work")
    assert home._decision_check(run, []) is not None
    home.write_records(str(root))
    assert not [i for i in home.decisions()["items"] if i["question"] == "Sneaky"]


def test_a_record_edit_the_user_commits_mid_run_is_theirs_not_the_runs(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, record_id = project_with_record(home, tmp_path)
    record_path = files_on(root)[0]
    tips = {"dev": git(root, "rev-parse", "dev").strip()}
    worktree = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "work", str(worktree), "dev")
    # While the run works, the user edits the record right on dev and commits it.
    edited = (
        (root / record_path)
        .read_text()
        .replace("Rattish, never TOML", "Rattish only; TOML files are converted on import")
    )
    (root / record_path).write_text(edited)
    git(root, "commit", "-qam", "Clarify the workflow format decision")
    users = git(root, "rev-parse", "HEAD").strip()
    # The worker commits allowed work, rebases onto dev and fast-forwards it.
    (worktree / "src" / "flow.rattish").write_text("Rattish: 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "work")
    git(worktree, "rebase", "-q", "dev")
    git(root, "merge", "-q", "--ff-only", "work")
    monkeypatch.setattr(home, "_update", lambda *a, **k: None)
    run = {
        "id": "run-2",
        "task_id": "task-2",
        "recipe": {"type": "develop", "project_dir": str(root)},
        "worktree": {"root": str(worktree)},
        "branch_tips": tips,
    }
    checked = home._decision_check(run, [])
    assert checked is not None and checked["passed"], checked
    assert checked["changed"] == ["src/flow.rattish"]
    assert [r["id"] for r in checked["records"]] == [record_id]
    with home.store.read() as db:
        seen = records.ledger(db, home.store)
    assert users not in seen["runs"] and len(seen["runs"]) == 1
    # The user's edit is read in as theirs and stays on dev.
    monkeypatch.setattr(home, "_writer_running", lambda project: False)
    home.write_records(str(root))
    item = next(i for i in home.decisions()["items"] if i["id"] == record_id)
    assert item["edited_outside"] is True
    assert item["answer"] == "Rattish only; TOML files are converted on import"
    assert git(root, "show", f"dev:{record_path}") == edited


def test_a_worker_commit_that_leaves_no_reflog_entry_is_still_the_runs(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, _record_id = project_with_record(home, tmp_path)
    tips = {"dev": git(root, "rev-parse", "dev").strip()}
    worktree = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "work", str(worktree), "dev")
    (worktree / "src" / "flow.rattish").write_text("Rattish: 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "work")
    # The worker builds a commit that adds a record file and a forbidden file without
    # `git commit`, and moves to it with a reset: its reflog never says it made one.
    sneaky = ".raticode/decisions/0999-sneaky.md"
    (worktree / sneaky).write_text(
        "---\nstatus: accepted\ntitle: Sneaky\n---\n## Decision\n\nAnything goes.\n"
    )
    (worktree / "src" / "flow.toml").write_text("[workflow]\n")
    git(worktree, "add", "-A")
    tree = git(worktree, "write-tree").strip()
    hidden = git(worktree, "commit-tree", tree, "-p", "HEAD", "-m", "tidy").strip()
    git(worktree, "reset", "-q", "--hard", hidden)
    git(root, "merge", "-q", "--ff-only", "work")
    monkeypatch.setattr(home, "_update", lambda *a, **k: None)
    run = {
        "id": "run-3",
        "task_id": "task-3",
        "recipe": {"type": "develop", "project_dir": str(root)},
        "worktree": {"root": str(worktree)},
        "branch_tips": tips,
    }
    checked = home._decision_check(run, [])
    assert checked is not None and not checked["passed"], checked
    assert {b["path"] for b in checked["broken"]} == {sneaky, "src/flow.toml"}
    with home.store.read() as db:
        seen = records.ledger(db, home.store)
    assert hidden in seen["runs"]
    # Once the run is over, the file it added is still never read in as the user's decision.
    monkeypatch.setattr(home, "_writer_running", lambda project: False)
    home.write_records(str(root))
    assert not [i for i in home.decisions()["items"] if i["question"] == "Sneaky"]


# R8:a proposal that contradicts a decision waits ---------------------------------------


def test_a_proposal_that_contradicts_a_decision_is_flagged():
    decided = [
        {
            "id": "D-4",
            "remember_as": "record",
            "record": {"signals": ["workflow toml"]},
        },
        {"id": "D-5", "remember_as": "fact", "record": {}},
    ]
    named = Proposal.model_validate({"title": "Faster checks", "conflicts_with": ["D-5", "D-9"]})
    assert contradicted(named, decided) == ["D-5"]
    signal = Proposal.model_validate({"title": "Import", "description": "Read workflow TOML files"})
    assert contradicted(signal, decided) == ["D-4"]
    assert contradicted(Proposal.model_validate({"title": "Docs"}), decided) == []


# Path counts: what each record path matches on the landing branch ---------------------------


def counted_repo(tmp_path: Path) -> Path:
    root = repo(tmp_path)
    (root / "src" / "app.toml").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "toml")
    return root


def add_counted_record(home: RemHome, root: Path) -> str:  # noqa: F811
    home.change_decisions(
        {
            "action": "add",
            "project": str(root),
            "question": "How are workflows written?",
            "answer": "Rattish only",
            "remember_as": "record",
            "applies_to": ["src/**", "github/workflows/**"],
            "forbids": ["**/*.toml", "vendor/**"],
        }
    )
    return str(next(i["id"] for i in home.decisions()["items"] if i.get("remember_as") == "record"))


def test_count_notes_say_what_a_path_matches():
    assert records.count_note("applies_to", 0) == "matches no files"
    assert records.count_note("applies_to", 1) == "1 file"
    assert records.count_note("applies_to", 12) == "12 files"
    assert records.count_note("forbids", 0) == "no files now"
    assert records.count_note("forbids", 1) == "matches 1 file that exists now"
    assert records.count_note("forbids", 3) == "matches 3 files that exist now"
    assert records.count_note("applies_to", None) == ""


def test_records_in_force_carry_tracked_file_counts_per_path(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = counted_repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    record_id = add_counted_record(home, root)
    item = next(i for i in home.decisions(sync=True)["items"] if i["id"] == record_id)
    tip = git(root, "rev-parse", "dev").strip()
    assert item["path_counts"] == {
        "branch": "dev",
        "commit": tip[:12],
        "applies_to": {"src/**": 2, "github/workflows/**": 0},
        "forbids": {"**/*.toml": 1, "vendor/**": 0},
    }
    # Only records get counts; the landing branch decision itself is a plain fact.
    fact = next(i for i in home.decisions()["items"] if i.get("topic") == "branch.integration")
    assert "path_counts" not in fact
    # A new commit on the landing branch is a new tip, so the counts follow it.
    (root / "src" / "more.py").write_text("x = 2\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "more")
    item = next(i for i in home.decisions()["items"] if i["id"] == record_id)
    assert item["path_counts"]["applies_to"]["src/**"] == 3
    assert item["path_counts"]["commit"] == git(root, "rev-parse", "dev").strip()[:12]


def test_counts_are_cached_by_branch_tip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = counted_repo(tmp_path)
    calls: list[tuple[str, ...]] = []
    real = records._out

    def spy(project: Any, *args: str, **kwargs: Any) -> str | None:
        calls.append(args)
        return real(project, *args, **kwargs)

    monkeypatch.setattr(records, "_out", spy)
    first = records.path_counts(str(root), "dev", ["src/**"], ["**/*.toml"])
    second = records.path_counts(str(root), "dev", ["src/**"], ["**/*.toml"])
    assert first == second and first is not None
    assert sum(1 for c in calls if c[0] == "ls-tree") == 1
    # No landing branch, or one that doesn't exist: nothing to count on.
    assert records.path_counts(str(root), None, ["src/**"], []) is None
    assert records.path_counts(str(root), "nope", ["src/**"], []) is None


def test_typed_paths_are_counted_before_saving_and_never_refused(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = counted_repo(tmp_path)
    # Without a landing branch decision there is nothing to count on, and it says why.
    early = home.decision_paths({"project": str(root), "applies_to": ["src/**"]})
    assert early["path_counts"] is None and "landing branch" in early["reason"]
    decide(home, root, "branch.integration", "dev")
    added = home.decision_paths(
        {"project": str(root), "applies_to": ["./src/**", "docs/**"], "forbids": "src/*.toml"}
    )
    assert added["path_counts"]["applies_to"] == {"src/**": 2, "docs/**": 0}
    assert added["path_counts"]["forbids"] == {"src/*.toml": 1}
    record_id = add_counted_record(home, root)
    edited = home.decision_paths({"id": record_id, "applies_to": ["src/main.py"]})
    assert edited["path_counts"]["applies_to"] == {"src/main.py": 1}
    with pytest.raises(DecisionRefused):
        home.decision_paths({"id": "D-999", "applies_to": ["src/**"]})
    with pytest.raises(DecisionRefused):
        home.decision_paths({"applies_to": ["src/**"]})
    # A path that matches nothing still saves: it can be planned.
    home.change_decisions({"action": "edit", "id": record_id, "applies_to": ["planned/**"]})
    item = next(i for i in home.decisions()["items"] if i["id"] == record_id)
    assert item["record"]["applies_to"] == ["planned/**"]
    assert item["path_counts"]["applies_to"] == {"planned/**": 0}


def test_the_phone_gets_each_records_path_counts():
    from gofer.devices.desktop_control import _decision_item

    base = {
        "id": "D-4",
        "question": "Release flow?",
        "answer": "dev to stage",
        "status": "confirmed",
        "remember_as": "record",
        "project": "/p",
        "project_name": "p",
        "record": {"applies_to": ["github/workflows/**"], "forbids": ["secrets/**"]},
    }
    counts = {
        "branch": "dev",
        "commit": "abc",
        "applies_to": {"github/workflows/**": 0},
        "forbids": {"secrets/**": 3, "bad": -1, "worse": True},
    }
    entry = _decision_item({**base, "path_counts": counts})
    assert entry["path_counts"] == {
        "branch": "dev",
        "applies_to": {"github/workflows/**": 0},
        "forbids": {"secrets/**": 3},
    }
    assert _decision_item({**base, "path_counts": None})["path_counts"] is None
    fact = _decision_item({**base, "remember_as": "fact", "path_counts": counts})
    assert fact["path_counts"] is None
