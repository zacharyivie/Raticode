"""The desktop app log as a paired phone's Developer log reads it."""

import json
import os
from pathlib import Path
from typing import Any

from gofer.devices import app_log
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone


def line(level: str, message: str, source: str = "backend", **extra: Any) -> str:
    record = {"time": "2026-11-06T12:00:00.000Z", "level": level, "source": source}
    return json.dumps({**record, "message": message, **extra}) + "\n"


def write(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)


def test_first_read_shows_the_newest_entries_then_follows_new_ones(tmp_path):
    log = tmp_path / "app.jsonl"
    write(log, line("info", "Started"), line("debug", "Detail"), line("error", "Failed"))
    first = app_log.read(str(log))
    assert first["available"] and first["reset"] and not first["skipped"]
    assert [e["message"] for e in first["entries"]] == ["Started", "Detail", "Failed"]
    assert first["log_file"] == "app.jsonl" and str(tmp_path) not in json.dumps(first)
    ids = [e["id"] for e in first["entries"]]
    assert len(set(ids)) == 3
    # Nothing new: the cursor stays and no entries repeat.
    again = app_log.read(str(log), first["cursor"])
    assert again["entries"] == [] and again["cursor"] == first["cursor"] and not again["reset"]
    write(log, line("warning", "Slow"))
    follow = app_log.read(str(log), first["cursor"])
    assert [(e["level"], e["message"]) for e in follow["entries"]] == [("warn", "Slow")]
    assert follow["entries"][0]["id"] not in ids


def test_a_line_still_being_written_waits_for_the_next_read(tmp_path):
    log = tmp_path / "app.jsonl"
    write(log, line("info", "Whole"))
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"time": "t", "level": "info", "mess')
    first = app_log.read(str(log))
    assert [e["message"] for e in first["entries"]] == ["Whole"]
    with log.open("a", encoding="utf-8") as handle:
        handle.write('age": "Finished"}\n')
    assert [e["message"] for e in app_log.read(str(log), first["cursor"])["entries"]] == [
        "Finished"
    ]


def test_levels_filter_on_the_desktop(tmp_path):
    log = tmp_path / "app.jsonl"
    write(log, line("debug", "a"), line("info", "b"), line("error", "c"), line("critical", "d"))
    shown = app_log.read(str(log), None, ["error"])
    assert [e["message"] for e in shown["entries"]] == ["c", "d"]
    # Nothing valid means every level, never an empty view by mistake.
    assert len(app_log.read(str(log), None, ["bogus"])["entries"]) == 4


def test_following_survives_the_rename_to_a_backup(tmp_path):
    log = tmp_path / "app.jsonl"
    write(log, line("info", "one"))
    first = app_log.read(str(log))
    write(log, line("info", "two"))
    log.rename(tmp_path / "app.jsonl.1")
    write(log, line("info", "three"))
    follow = app_log.read(str(log), first["cursor"])
    assert [e["message"] for e in follow["entries"]] == ["two", "three"]
    assert not follow["skipped"]
    # A cursor for a file that is gone shows the newest entries and says some were missed.
    gone = app_log.read(str(log), {"file": "0-0", "offset": 5})
    assert gone["skipped"] and not gone["reset"]
    assert [e["message"] for e in gone["entries"]] == ["one", "two", "three"]


def test_entries_are_redacted_and_shortened(tmp_path):
    log = tmp_path / "app.jsonl"
    secret = "Authorization: Bearer abc.def-123 token=xyz https://me:pw@host/x raticode://pair?s=1"
    write(log, line("info", secret), line("info", "x" * (app_log.MAX_MESSAGE + 10)))
    entries = app_log.read(str(log))["entries"]
    text = entries[0]["message"]
    assert "abc.def" not in text and "xyz" not in text and "me:pw" not in text and "s=1" not in text
    assert entries[1]["truncated"] and len(entries[1]["message"]) == app_log.MAX_MESSAGE


def test_bare_tokens_with_known_prefixes_are_redacted():
    tokens = [
        "rtk_agent_" + "a1B2" * 8,
        "ghp_" + "Z9" * 18,
        "github_pat_" + "x" * 30,
        "sk-ant-api03-" + "q" * 30,
        "xoxb-" + "1234567890-abcdef",
        "AKIA" + "ABCDEFGHIJKLMNOP",
    ]
    for token in tokens:
        text = app_log.redact(f"request failed with {token} in the body")
        assert token not in text and "[redacted]" in text, text
    # Ordinary words that share a prefix stay readable.
    for plain in ("task-123 failed", "sk-learn import", "ghost_town"):
        assert app_log.redact(plain) == plain


def test_large_answers_stay_inside_the_result_limit(tmp_path):
    log = tmp_path / "app.jsonl"
    write(log, *(line("info", "é" * 7000) for _ in range(300)))
    first = app_log.read(str(log))
    size = len(json.dumps(first, ensure_ascii=True))
    assert size < 48 * 24000
    assert first["entries"] and len(first["entries"]) < 300


def test_phone_reads_the_log_only_with_desktop_control(phone, tmp_path):
    app, peer, _, send = phone
    log = tmp_path / "app.jsonl"
    write(log, line("error", "Phone request refused", source="devices"))
    app.desktop.app_log_path = str(log)
    assert send("desktop.read", "developer.log")["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["developer_log"] is True
    view = send("desktop.read", "developer.log", {"levels": ["error"]})["value"]
    assert view["entries"][0]["source"] == "devices"
    following = send("desktop.read", "developer.log", {"after": view["cursor"]})["value"]
    assert following["entries"] == []
    # A backend the window didn't start has no app log, and the phone is told why.
    app.desktop.app_log_path = None
    missing = send("desktop.read", "developer.log")
    assert missing["code"] == "unavailable" and "desktop app" in missing["detail"]


def test_configured_path_comes_from_the_window(monkeypatch, tmp_path):
    monkeypatch.setenv("GOFER_APP_LOG", os.fspath(tmp_path / "app.jsonl"))
    assert app_log.configured_path() == os.fspath(tmp_path / "app.jsonl")
    monkeypatch.delenv("GOFER_APP_LOG")
    assert app_log.configured_path() is None
