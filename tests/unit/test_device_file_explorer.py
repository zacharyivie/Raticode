"""The phone's file explorer: read-only browsing of open projects, by catalog ID."""

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from gofer.devices import file_explorer as module
from gofer.devices.desktop_control import Refused
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_desktop_control import phone as phone


def _offers(app: Any, peer: str, before: int | None) -> list[dict[str, Any]]:
    return [e for e in app.pending(peer, before) if e["type"] == "file.offer"]


def _outbox(app: Any) -> int:
    return app.registry.db.execute("SELECT MAX(sequence) FROM device_outbox").fetchone()[0] or 0


@pytest.fixture
def desk(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    roots = {p["name"]: Path(p["root"]) for p in context["projects"]}
    ids = {p["name"]: p["id"] for p in context["projects"]}
    notes = roots["Notes"]
    (notes / "docs" / "deep").mkdir(parents=True)
    (notes / "docs" / "readme.md").write_text("# Notes\n\nHello\n")
    (notes / "docs" / "deep" / "app.py").write_text("print('hi')\n")
    (notes / "Zeta.txt").write_text("z\n")
    (notes / "alpha.kt").write_text("fun main() {}\n")
    (notes / ".hidden").write_text("dot\n")
    (notes / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
    (notes / ".env").write_text("TOKEN=not-real\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private\n")
    (notes / "escape").symlink_to(outside)
    (notes / "escape.txt").symlink_to(outside / "private.txt")
    return app, peer, send, roots, ids, outside


def test_explorer_needs_phone_access_and_is_advertised(phone):
    app, peer, _, send = phone
    for kind, op in (
        ("desktop.read", "files.list"),
        ("desktop.read", "files.text"),
        ("desktop.action", "files.open"),
    ):
        assert send(kind, op, {"project_id": "x", "path": "a"})["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["file_explorer"] is True
    # Turning thread sync off turns the explorer off too.
    app.workspace.enable(peer, False)
    assert send("desktop.read", "files.list")["code"] == "unauthorized"


def test_projects_and_folders_are_listed_without_full_paths(desk):
    _, _, send, roots, ids, outside = desk
    listed = send("desktop.read", "files.list")["value"]
    assert {p["name"]: p["project_id"] for p in listed["projects"]} == ids
    assert listed["more"] == 0
    root = send("desktop.read", "files.list", {"project_id": ids["Notes"]})["value"]
    text = json.dumps(root)
    assert str(roots["Notes"]) not in text and str(outside) not in text
    assert root["project"] == "Notes" and root["path"] == ""
    names = [(e["name"], e["type"]) for e in root["entries"]]
    # Folders first, then everything else by name regardless of case; dotfiles show,
    # as in the desktop explorer.
    assert names == [
        ("docs", "folder"),
        (".env", "file"),
        (".hidden", "file"),
        ("alpha.kt", "file"),
        ("escape", "link"),
        ("escape.txt", "link"),
        ("picture.png", "file"),
        ("Zeta.txt", "file"),
    ]
    by_name = {e["name"]: e for e in root["entries"]}
    assert by_name["alpha.kt"]["size"] == len("fun main() {}\n")
    assert by_name["alpha.kt"]["modified"].endswith("Z")
    # A secret-looking file is listed and marked, so the phone says why it won't open.
    assert by_name[".env"]["locked"] is True and "locked" not in by_name["alpha.kt"]
    deep = send("desktop.read", "files.list", {"project_id": ids["Notes"], "path": "docs/deep"})
    assert [e["name"] for e in deep["value"]["entries"]] == ["app.py"]
    assert deep["value"]["path"] == "docs/deep"


def test_text_files_are_read_and_binary_ones_are_marked(desk):
    _, _, send, _, ids, _ = desk
    read = send(
        "desktop.read", "files.text", {"project_id": ids["Notes"], "path": "docs/readme.md"}
    )
    value = read["value"]
    assert value["text"] == "# Notes\n\nHello\n" and not value["truncated"] and not value["binary"]
    assert value["name"] == "readme.md" and value["size"] == 15
    image = send("desktop.read", "files.text", {"project_id": ids["Notes"], "path": "picture.png"})
    assert image["value"]["binary"] is True and image["value"]["text"] == ""


def test_long_text_is_cut_at_a_line_end(desk):
    _, _, send, roots, ids, _ = desk
    line = "x" * 99 + "\n"
    (roots["Notes"] / "big.log").write_text(line * 4000)
    value = send("desktop.read", "files.text", {"project_id": ids["Notes"], "path": "big.log"})[
        "value"
    ]
    assert value["truncated"] is True and value["size"] == 400_000
    assert value["text"].endswith("\n") and len(value["text"]) <= module.TEXT_LIMIT
    assert set(value["text"].splitlines()) == {"x" * 99}


def test_text_encodings():
    assert module._decode("héllo".encode(), True) == "héllo"
    assert module._decode("﻿bom".encode(), True) == "bom"
    assert module._decode("wide".encode("utf-16"), True) == "wide"
    # A cut inside a character at the end of a long file still reads.
    assert module._decode("ab€".encode()[:-1], False) == "ab"
    assert module._decode(b"\x00\x01binary", True) is None
    assert module._decode(bytes(range(1, 32)) * 10, True) is None
    # Latin-1 bytes late in a mostly UTF-8 file are replaced, as editors show them.
    assert module._decode(b"a" * 6000 + b"\xe9", True) == "a" * 6000 + "�"


def test_links_paths_and_secrets_are_refused(desk):
    _, _, send, _, ids, _ = desk
    notes = ids["Notes"]

    def read(path: Any, op: str = "files.text", kind: str = "desktop.read") -> dict[str, Any]:
        answer: dict[str, Any] = send(kind, op, {"project_id": notes, "path": path})
        return answer

    # Links are listed but never followed, to a folder or a file.
    assert read("escape", "files.list")["code"] == "not_found"
    assert read("escape/private.txt")["code"] == "not_found"
    assert read("escape.txt")["code"] == "unauthorized"
    assert read("escape.txt", "files.open", "desktop.action")["code"] == "unauthorized"
    # Paths are relative names only.
    for bad in ("../outside/private.txt", "/etc/hostname", "docs//readme.md", "docs/./x", "a\\b"):
        assert read(bad)["code"] == "invalid", bad
    assert read("docs/missing.md")["code"] == "not_found"
    assert read("docs")["code"] == "invalid"
    assert read("alpha.kt", "files.list")["code"] == "not_found"
    # Secret-looking files stay on the desktop whichever way the phone asks.
    assert read(".env")["code"] == "unauthorized"
    assert read(".env", "files.open", "desktop.action")["code"] == "unauthorized"
    # Tool sign-ins kept in a project, and a Git config whose remote URL can hold a token.
    root = desk[3]["Notes"]
    (root / ".git").mkdir()
    for path in (".npmrc", ".pypirc", ".netrc", ".git/config", "terraform.tfstate"):
        (root / path).write_text("//registry.example/:_authToken=not-real\n")
        assert read(path)["code"] == "unauthorized", path
        assert read(path, "files.open", "desktop.action")["code"] == "unauthorized", path
    # An unknown or closed project.
    gone = send("desktop.read", "files.list", {"project_id": "closed", "path": ""})
    assert gone["code"] == "not_found" and gone["detail"] == module.NOT_OPEN


def test_opening_a_file_offers_it_on_the_phones_file_channel(desk):
    app, peer, send, roots, ids, _ = desk
    before = _outbox(app)
    result = send(
        "desktop.action", "files.open", {"project_id": ids["Notes"], "path": "picture.png"}
    )
    assert result["ok"], result
    value = result["value"]
    channel = app.desktop.files_thread(peer)
    picture = roots["Notes"] / "picture.png"
    assert (value["name"], value["size"], value["thread_id"], value["path"]) == (
        "picture.png",
        picture.stat().st_size,
        channel,
        "picture.png",
    )
    assert str(roots["Notes"]) not in json.dumps(value)
    offers = _offers(app, peer, before)
    assert [o["payload"]["file_id"] for o in offers] == [value["file_id"]]
    chunk = app.handle(
        peer, event("file.request", 700, thread_id=channel, payload={"file_id": value["file_id"]})
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == picture.read_bytes()
    # Opening it again replaces an offer the phone never downloaded.
    first = send("desktop.action", "files.open", {"project_id": ids["Notes"], "path": "alpha.kt"})
    again = send("desktop.action", "files.open", {"project_id": ids["Notes"], "path": "alpha.kt"})
    assert first["value"]["file_id"] != again["value"]["file_id"]
    state = app.registry.db.execute(
        "SELECT state FROM device_files WHERE id=?", (first["value"]["file_id"],)
    ).fetchone()
    assert state is None or state[0] in {"cancelled", "expired"}


def test_files_over_the_phone_limit_are_refused_with_a_reason(desk, monkeypatch):
    _, _, send, roots, ids, _ = desk
    monkeypatch.setattr(module, "MAX_FILE", 100)
    (roots["Notes"] / "clip.mp4").write_bytes(b"\0" * 101)
    result = send("desktop.action", "files.open", {"project_id": ids["Notes"], "path": "clip.mp4"})
    assert result["code"] == "unavailable" and result["detail"] == module.TOO_LARGE


def test_listing_is_bounded_and_skips_names_that_could_mislead(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ENTRY_LIMIT", 3)
    tmp_path = tmp_path / "folder"
    tmp_path.mkdir()
    for n in range(5):
        (tmp_path / f"f{n}.txt").write_text("x")
    (tmp_path / "evil‮txt.exe").write_text("x")
    listed = module.listing(str(tmp_path), [])
    assert [e["name"] for e in listed["entries"]] == ["f0.txt", "f1.txt", "f2.txt"]
    assert listed["more"] == 2 and listed["hidden"] == 1


def test_a_project_closed_while_reading_is_not_shown():
    data = {"project_id": "a", "root": "/old", "value": {"path": "", "entries": []}}
    with pytest.raises(Refused) as caught:
        module.read("files.list", data, {"a": {"root": "/new", "name": "A"}})
    assert caught.value.code == "not_found"
    listed = module.read(
        "files.list",
        {"projects": [{"project_id": "a", "root": "/old"}], "more": 0},
        {"a": {"root": "/new", "name": "A"}},
    )
    assert listed["projects"] == []


def test_reads_never_change_the_folder(desk):
    _, _, send, roots, ids, _ = desk
    root = roots["Notes"]

    def snapshot() -> list[tuple[str, int, int]]:
        return sorted(
            (str(p.relative_to(root)), p.lstat().st_size, p.lstat().st_mtime_ns)
            for p in root.rglob("*")
        )

    before = snapshot()
    for path in ("", "docs", "docs/deep"):
        send("desktop.read", "files.list", {"project_id": ids["Notes"], "path": path})
    send("desktop.read", "files.text", {"project_id": ids["Notes"], "path": "docs/readme.md"})
    assert snapshot() == before
    assert os.listdir(root)
