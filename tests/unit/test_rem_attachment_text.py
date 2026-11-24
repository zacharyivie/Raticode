"""Rem's tool-free chat reads attached text and HTML files in full, a bounded part at a time."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_attachment_text as attachment_text
from gofer.ui.chat_media import attachment_thread_dir, store_chat_attachments
from gofer.ui.rem_attachment_text import (
    first_part,
    html_to_text,
    read_attachment,
    read_part,
)
from gofer.ui.rem_home import (
    ATTACHMENT_TEXT_BUDGET,
    ATTACHMENT_TEXT_LIMIT,
    REPLY_RULES,
    attachment_context,
)
from gofer.ui.rem_home_lookups import (
    ATTACHMENT_PART_CHARS,
    ATTACHMENT_READ_CHARS,
    LABELS,
    RULES,
    Lookup,
    RemLookups,
)


def upload(data_dir: Path, name: str, content: bytes, kind: str, thread: str = "rem-home"):
    stored = store_chat_attachments(
        {
            "threadId": thread,
            "files": [{"name": name, "type": kind, "data": base64.b64encode(content).decode()}],
        },
        data_dir,
    )["attachments"][0]
    return {**stored, "path": str(attachment_thread_dir(data_dir, thread) / stored["storageName"])}


def report_html() -> str:
    """A themed report like the 33 KB one Rem couldn't read: a long style sheet, a drawing,
    then prose and a proposals table that starts past the old 20,000-character cut."""
    style = "<style>" + ".card{color:#123456;margin:0 auto;padding:4px}\n" * 220 + "</style>"
    drawing = "<svg><title>Proposal order</title><text>A</text></svg>"
    prose = "".join(
        f"<p>Finding {i}: the measured cost went down by {i}%.</p>\n" for i in range(250)
    )
    rows = "".join(
        f"<tr><td>{letter} · Proposal {letter}</td><td>~{n}k chars</td>"
        f"<td>{'△ Planned · riskier' if letter == 'G' else '○ Planned'}</td></tr>\n"
        for n, letter in enumerate("DBCAEHFGI", start=1)
    )
    return (
        "<!doctype html><html><head><title>Rem token efficiency</title>"
        f"{style}<script>let secret = 1;</script></head><body>{drawing}"
        "<h1>Where Rem's tokens go</h1>"
        "<div><b>Code measured</b>dev = test</div>"
        f"{prose}"
        "<h2><span>05</span>The proposals</h2>"
        "<table><caption>Nine proposals</caption>"
        "<tr><th>Proposal</th><th>Saves</th><th>Status</th></tr>\n"
        f"{rows}</table><p>Token savings are estimates.</p></body></html>"
    )


def test_html_reads_as_text_with_headings_lists_and_table_rows():
    text = html_to_text(
        "<html><head><title>Plan</title><style>p{color:red}</style></head><body>"
        "<svg><title>Chart caption</title></svg><script>alert(1)</script>"
        "<h2><span>01</span>Steps</h2><ul><li>First</li><li>Second <b>bold</b></li></ul>"
        '<p>See <a href="https://example.com/x">the page</a> and <a href="#top">top</a>.</p>'
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1 | 2</td><td>three\n words</td></tr>"
        "</table><pre>  indented\n    code</pre></body></html>"
    )
    assert text.startswith("Title: Plan\n\n## 01 Steps")
    assert "color:red" not in text and "alert" not in text and "Chart caption" not in text
    assert "- First\n- Second bold" in text
    assert "See the page (https://example.com/x) and top." in text
    assert "| A | B |\n| --- | --- |\n| 1 \\| 2 | three words |" in text
    assert "  indented\n    code" in text


def test_an_html_report_sent_as_text_plain_is_read_in_full(tmp_path: Path):
    """The report that was cut off: its table now reaches Rem in the request itself."""
    source = report_html().encode()
    assert len(source) > 20000 and source.index(b"<table") > 20000
    item = upload(tmp_path, "rem-token-efficiency.html", source, "text/plain")
    [described] = attachment_context([item])
    assert described["converted_from"] == "html"
    assert "truncated" not in described and "read_rest" not in described
    assert described["text_chars"] == len(described["text"]) < ATTACHMENT_TEXT_LIMIT
    text = described["text"]
    assert text.startswith("Title: Rem token efficiency\n\n# Where Rem's tokens go")
    assert "Code measured dev = test" in text
    assert "## 05 The proposals" in text
    assert "| G · Proposal G | ~8k chars | △ Planned · riskier |" in text
    assert ".card{" not in text and "secret" not in text


def test_html_without_an_html_name_is_recognised_by_its_start(tmp_path: Path):
    path = tmp_path / "page"
    path.write_bytes(b"\n<!DOCTYPE html><html><body><p>Hello</p><style>x{}</style></body></html>")
    found = read_attachment(path, name="page", media_type="application/octet-stream")
    assert found is not None and found.html and found.text == "Hello"


def test_a_text_file_of_comments_is_read_at_once(tmp_path: Path):
    # The HTML check once backtracked through every way to split the comments: 28 took
    # 25 seconds and blocked the whole backend, and a few hundred fit in its window.
    notes = tmp_path / "notes.md"
    notes.write_bytes(b"<!-- a -->\n" * 300 + b"# Notes\n")
    started = time.monotonic()
    found = read_attachment(notes, name="notes.md", media_type="text/markdown")
    assert time.monotonic() - started < 2
    assert found is not None and not found.html and found.text.endswith("# Notes\n")
    page = tmp_path / "page"
    page.write_bytes(b"<!-- built -->\n<!-- by hand -->\n<!DOCTYPE html><p>Hi</p>")
    found = read_attachment(page, name="page", media_type="application/octet-stream")
    assert found is not None and found.html and found.text == "Hi"


def test_binary_files_are_not_read_as_text(tmp_path: Path):
    path = tmp_path / "photo.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00" + os.urandom(64))
    assert read_attachment(path, name="photo.bin", media_type="image/png") is None
    log = tmp_path / "server.out"
    log.write_text("plain log line\n")
    found = read_attachment(log, name="server.out", media_type="application/octet-stream")
    assert found is not None and found.text == "plain log line\n" and not found.html


def test_a_long_file_is_read_part_by_part_and_marked(tmp_path: Path):
    lines = "".join(f"line {i:05d} of the long log\n" for i in range(4000))
    path = tmp_path / "long.log"
    path.write_text(lines)
    found = read_attachment(path, name="long.log", media_type="text/plain")
    assert found is not None
    first = first_part(found, 10000)
    assert first["truncated"] and first["text_chars"] == len(lines)
    # Parts end at a line break, so the next one starts at a line.
    assert first["text"].endswith("\n") and first["shown"] == [0, first["next_offset"]]
    parts, offset = [first["text"]], first["next_offset"]
    while True:
        part = read_part(found, offset=offset, query=None, limit=10000)
        parts.append(part["text"])
        assert part["shown"][0] == offset
        if not part.get("truncated"):
            break
        offset = part["next_offset"]
    assert "".join(parts) == lines


def test_a_query_jumps_to_the_line_its_words_are_on(tmp_path: Path):
    path = tmp_path / "notes.md"
    path.write_text("Intro\n" + "filler words here\n" * 500 + "## Proposals\n| G | riskier |\n")
    found = read_attachment(path, name="notes.md", media_type="")
    assert found is not None
    part = read_part(found, offset=None, query="proposals", limit=1000)
    assert part["text"] == "## Proposals\n| G | riskier |\n"
    assert part["shown"][0] == found.text.index("## Proposals")
    missing = read_part(found, offset=6, query="nowhere", limit=40)
    assert missing["query_not_found"] and missing["text"].startswith("filler")


def test_only_the_start_of_a_huge_file_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(attachment_text, "MAX_READ_BYTES", 100)
    attachment_text._cached.cache_clear()
    path = tmp_path / "huge.txt"
    path.write_text("x" * 500)
    found = read_attachment(path, name="huge.txt", media_type="text/plain")
    attachment_text._cached.cache_clear()
    assert found is not None and len(found.text) == 100 and not found.complete
    assert "start a task" in first_part(found, 1000)["read_limit"]


def test_attachments_past_the_limit_point_to_the_rest(tmp_path: Path):
    long_text = "".join(f"row {i}: value\n" for i in range(4000)).encode()
    first = upload(tmp_path, "first.csv", long_text, "text/csv")
    second = upload(tmp_path, "second.csv", long_text, "text/csv")
    third = upload(tmp_path, "third.csv", long_text, "text/csv")
    image = upload(tmp_path, "shot.png", b"\x89PNG\r\n\x1a\n\x00" * 4, "image/png")
    described = attachment_context([first, second, third, image])
    shown = described[0]
    assert shown["truncated"] and shown["text_chars"] == len(long_text)
    assert len(shown["text"]) <= ATTACHMENT_TEXT_LIMIT
    assert shown["read_rest"] == {
        "kind": "attachment",
        "path": first["path"],
        "offset": shown["next_offset"],
    }
    assert sum(len(d.get("text", "")) for d in described) <= ATTACHMENT_TEXT_BUDGET
    # Once the message's budget is spent, a file still says how long it is and how to read it.
    unshown = described[2]
    assert "text" not in unshown and unshown["text_not_shown"]
    assert unshown["text_chars"] == len(long_text)
    assert unshown["read_rest"] == {"kind": "attachment", "path": third["path"], "offset": 0}
    assert set(described[3]) == {"name", "type", "size", "path"}


def lookups(data_dir: Path | None) -> RemLookups:
    return RemLookups(config={}, tasks=lambda: [], data_dir=data_dir)


def test_attachment_lookup_reads_rem_chat_files_by_path_or_name(tmp_path: Path):
    item = upload(tmp_path, "report.html", report_html().encode(), "text/plain")
    whole = attachment_context([item])[0]["text"]
    [by_path, by_name, from_offset, by_query] = lookups(tmp_path).run(
        [
            Lookup(kind="attachment", path=item["path"]),
            Lookup(kind="attachment", path=item["storageName"]),
            Lookup(kind="attachment", path=item["path"], offset=100),
            Lookup(kind="attachment", path=item["path"], query="the proposals"),
        ]
    )
    assert by_path["text"] == by_name["text"] == whole
    assert by_path["name"] == "report.html" and by_path["converted_from"] == "html"
    assert from_offset["text"] == whole[100:] and from_offset["shown"] == [100, len(whole)]
    assert by_query["text"].startswith("## 05 The proposals\n\n")
    assert by_query["lookup"] == {
        "kind": "attachment",
        "path": item["path"],
        "query": "the proposals",
    }


def test_attachment_lookup_reads_nothing_outside_rem_chat_uploads(tmp_path: Path):
    other = upload(tmp_path, "thread.txt", b"another thread's file", "text/plain", thread="t-1")
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    image = upload(tmp_path, "shot.png", b"\x89PNG\r\n\x1a\n\x00\x00", "image/png")
    folder = attachment_thread_dir(tmp_path, "rem-home")
    link = folder / ("0" * 32 + "-link.txt")
    link.symlink_to(secret)
    results = lookups(tmp_path).run(
        [
            Lookup(kind="attachment", path=other["path"]),
            Lookup(kind="attachment", path=str(secret)),
            Lookup(kind="attachment", path=str(link)),
        ]
    ) + lookups(tmp_path).run(
        [
            Lookup(kind="attachment", path=f"../t-1/{other['storageName']}"),
            Lookup(kind="attachment", path=image["path"]),
        ]
    )
    assert [r["unavailable"] for r in results] == [
        "Only files attached in Rem's chat can be read this way.",
        "Only files attached in Rem's chat can be read this way.",
        "No file attached in Rem's chat has that path, or it's no longer available.",
        "Only files attached in Rem's chat can be read this way.",
        "That attachment isn't a text file, so it can't be read here; start a task to inspect it.",
    ]
    assert "unavailable" in lookups(None).run([Lookup(kind="attachment", path=other["path"])])[0]
    with pytest.raises(ValueError):
        Lookup(kind="attachment")


def test_one_read_returns_a_bounded_amount_of_attachment_text(tmp_path: Path):
    text = "".join(f"entry {i} with some words\n" for i in range(20000)).encode()
    item = upload(tmp_path, "big.txt", text, "text/plain")
    results: list[dict[str, Any]] = lookups(tmp_path).run(
        [Lookup(kind="attachment", path=item["path"], offset=n * 30000) for n in range(4)]
    )
    assert all(len(r.get("text", "")) <= ATTACHMENT_PART_CHARS for r in results)
    assert sum(len(r.get("text", "")) for r in results) <= ATTACHMENT_READ_CHARS
    assert "next read" in results[-1]["unavailable"]


def test_rem_rules_say_to_read_the_rest_of_a_truncated_attachment():
    assert "{kind:attachment,path,offset?,query?}" in RULES
    assert LABELS["attachment"] == "Reading the attached file"
    assert "truncated:true means you have only part of it" in REPLY_RULES
    assert "read the rest yourself with the read_rest lookup" in REPLY_RULES
