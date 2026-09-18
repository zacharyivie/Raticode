from __future__ import annotations

import threading

import pytest

from gofer.ui.chat_jobs import ChatJobs


def test_disconnect_does_not_stop_job_and_replay_is_sequenced(tmp_path):
    jobs = ChatJobs(tmp_path)
    release = threading.Event()
    done = threading.Event()

    def run(emit):
        emit({"type": "thought", "text": "working"})
        assert release.wait(3)
        emit({"type": "final", "message": {"body": "finished without a UI"}})
        done.set()

    jobs.start("thread", "turn", run)
    subscriber = jobs.events("thread", "turn")
    assert next(subscriber)["sequence"] == 1
    subscriber.close()
    release.set()
    assert done.wait(3)
    replay = list(jobs.events("thread", "turn", after=1))
    assert [event["sequence"] for event in replay] == [2]
    assert replay[0]["message"]["body"] == "finished without a UI"
    # Completed output remains readable by a new backend instance.
    assert len(list(ChatJobs(tmp_path).events("thread", "turn"))) == 2


def test_jobs_bound_concurrency_reject_duplicates_and_validate_paths(tmp_path):
    jobs = ChatJobs(tmp_path, max_active=1)
    release = threading.Event()
    jobs.start("thread", "turn", lambda emit: release.wait(3))
    try:
        with pytest.raises(ValueError, match="Too many"):
            jobs.start("other", "turn", lambda emit: None)
        with pytest.raises(ValueError):
            jobs.events("../outside", "turn")
        with pytest.raises(ValueError):
            jobs.events("thread", "turn", -1)
    finally:
        release.set()
        list(jobs.events("thread", "turn"))
    with pytest.raises(FileExistsError):
        jobs.start("thread", "turn", lambda emit: None)


def test_failure_is_saved_and_incomplete_journal_is_reported(tmp_path):
    jobs = ChatJobs(tmp_path)

    def fail(emit):
        raise RuntimeError("provider failed")

    jobs.start("thread", "turn", fail)
    assert list(jobs.events("thread", "turn"))[0]["error"] == "provider failed"
    path = tmp_path / "chat-jobs" / "thread" / "interrupted.jsonl"
    path.write_text('{"type":"thought","sequence":1}\n')
    events = list(jobs.events("thread", "interrupted"))
    assert events[-1]["type"] == "error"
    assert "Backend stopped" in events[-1]["error"]
