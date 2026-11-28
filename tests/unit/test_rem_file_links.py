"""Linking a file in Rem's chat: a clickable link, never a transfer, found without a model."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from gofer.ui.file_mentions import (
    LINKED_FILES_MAX,
    file_mentions,
    linked_files,
    with_file_mentions,
)
from gofer.ui.rem_home import RemHome, markdown_file_link, reply_links, wants_link
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home_api import api as api


def _file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def texts(value: str) -> list[str]:
    return [item["text"] for item in file_mentions(value)]


def test_full_paths_with_spaces_end_where_the_file_does(tmp_path: Path):
    video = _file(tmp_path / "My Videos" / "dock duplicate you know.MP4")
    notes = _file(tmp_path / "notes.md")
    assert texts(f"Here it is: {video} is ready.") == [str(video)]
    # Two paths in one sentence stay two links.
    assert texts(f"Compare {notes} and {video}, then delete {tmp_path}/gone.txt.") == [
        str(notes),
        str(video),
    ]
    # Brackets, quotes, emphasis, code spans and a line number around a path.
    assert texts(f"(see {notes})") == [str(notes)]
    assert texts(f"**{video}**") == [str(video)]
    assert texts(f'"{video}"') == [str(video)]
    assert texts(f"`{video}`") == [str(video)]
    assert texts(f"{notes}:12 has the bug") == [str(notes)]
    # Folders, missing files, web addresses and relative paths aren't file links.
    assert texts(f"Saved under {tmp_path} and {tmp_path}/missing file.txt") == []
    assert texts(f"https://example.com{notes} and docs/notes.md") == []
    item = file_mentions(f"Open {video}")[0]
    assert item == {"text": str(video), "path": str(video)}


def test_home_relative_paths_link_to_the_expanded_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    clip = _file(tmp_path / "Videos" / "team call.mp4")
    assert file_mentions("Watch ~/Videos/team call.mp4 tonight") == [
        {"text": "~/Videos/team call.mp4", "path": str(clip)}
    ]


def test_only_rems_messages_carry_mentions(tmp_path: Path):
    report = _file(tmp_path / "Reports" / "launch plan.html")
    messages = [
        {"id": "u", "role": "user", "body": f"what about {report}"},
        {"id": "r", "role": "rem", "body": "Saved it.", "details": f"Path: {report}"},
        {"id": "plain", "role": "rem", "body": "Nothing to open."},
    ]
    marked = with_file_mentions(messages)
    assert "file_mentions" not in marked[0] and "file_mentions" not in marked[2]
    assert marked[1]["file_mentions"] == [{"text": str(report), "path": str(report)}]
    # The stored messages are left alone.
    assert "file_mentions" not in messages[1]


@pytest.mark.parametrize(
    "request_text,link",
    [
        ("Link me the video", True),
        ("can you link the screenshots?", True),
        ("Send me a link to the report", True),
        ("share the link to that file", True),
        ("Give me links to both reports", True),
        ("Send me the video", False),
        ("Attach the screenshots", False),
        ("Link the report and send me the video too", False),
        ("Upload the linked file to my phone", False),
        ("Where is the report?", False),
    ],
)
def test_link_requests_are_told_apart_from_send_requests(request_text: str, link: bool):
    assert wants_link(request_text) is link


def test_markdown_file_links_keep_spaces_and_odd_names_intact():
    assert (
        markdown_file_link("/home/me/My Videos/clip.mp4")
        == "[clip.mp4](</home/me/My Videos/clip.mp4>)"
    )
    assert (
        markdown_file_link("/home/me/a[1]>b.txt") == "[a\\[1\\]>b.txt](/home/me/a%5B1%5D%3Eb.txt)"
    )


def test_links_need_an_existing_file_whose_path_rem_was_given(tmp_path: Path):
    known = _file(tmp_path / "Pictures" / "cat photo.jpg")
    project = _file(tmp_path / "project" / "plan.md")
    guessed = _file(tmp_path / "Pictures" / "other.jpg")
    links, refused = reply_links(
        [str(known), str(project), str(guessed), str(tmp_path / "gone.jpg")],
        {"projects": [{"root": str(project.parent)}]},
        records=f"The worker saved {known}.",
    )
    assert links == [markdown_file_link(str(known)), markdown_file_link(str(project))]
    assert refused == [
        "I couldn’t link other.jpg: its path isn’t in our conversation, so I won’t guess at it. "
        "Send me the full path and I’ll link it.",
        "I couldn’t link gone.jpg: that file doesn’t exist.",
    ]


def test_link_me_gives_a_clickable_link_instead_of_sending(home: RemHome, tmp_path: Path):
    # Raticode's own data is never sent without the exact path, but a link copies nothing.
    video = _file(home.data_dir / "rem-work" / "clips" / "dock duplicate you know.MP4", b"mp4")
    home.generate = lambda p, c, e: {"reply": "Here’s the video.", "files": [str(video)]}
    home.submit_message({"request_id": "link", "body": f"Link me the video, {video}"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert reply["body"] == f"Here’s the video.\n\n[{video.name}](<{video}>)"
    assert "files" not in reply and "may hold passwords" not in reply["body"]

    # A link Rem already wrote isn't repeated.
    written = f"Here: [{video.name}](<{video}>)"
    home.generate = lambda p, c, e: {"reply": written, "files": [str(video)]}
    home.submit_message({"request_id": "again", "body": f"link {video} please"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert reply["body"] == written and "files" not in reply

    # Asking for the file itself still attaches it, with the usual safeguards.
    home.generate = lambda p, c, e: {"reply": "Here’s the video.", "files": [str(video)]}
    home.submit_message({"request_id": "send", "body": f"Send me the video in {video.parent}"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert "may hold passwords, keys or sign-ins" in reply["body"] and "files" not in reply
    home.submit_message({"request_id": "named", "body": f"Send me {video}"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert [f["name"] for f in reply["files"]] == [video.name]


def test_the_prompt_tells_rem_that_a_link_is_not_a_transfer(home: RemHome):
    prompts: list[str] = []

    def generate(prompt: str, _config: object, _extra: object) -> dict[str, object]:
        prompts.append(prompt)
        return {"reply": "Sure."}

    home.generate = generate
    home.submit_message({"request_id": "ask", "body": "Link me the report"})
    home._reply()
    assert "[file name](<absolute path>), and leave files empty" in prompts[0]


def test_the_desktop_chat_learns_which_paths_to_link(api, tmp_path: Path):
    request, service = api
    report = _file(tmp_path / "Second Brain" / "launch plan.html")
    with service.store.connect() as db:
        service.store.message(db, f"Saved to {report} today.", identity="saved")
        service.store.message(db, f"Is {report} done?", identity="asked", role="user")
    messages = {m["id"]: m for m in request("GET", "/api/rem/conversation").json()["messages"]}
    assert messages["saved"]["file_mentions"] == [{"text": str(report), "path": str(report)}]
    assert "file_mentions" not in messages["asked"]


def test_files_rems_messages_link_to_show_as_attachments(tmp_path: Path):
    shot = _file(tmp_path / "evidence" / "screen 1.png", b"png!")
    log = _file(tmp_path / "evidence" / "run.log", b"ok")
    sent = _file(tmp_path / "sent.pdf")
    (tmp_path / "folder").mkdir()
    (tmp_path / "link.txt").symlink_to(log)
    secret = _file(tmp_path / "api_token.txt")
    body = (
        f"See [the screenshot](<{shot}>) and [log]({quote(str(log))}:12), "
        f"[the docs](https://raticode.dev/docs), [a folder](<{tmp_path / 'folder'}>), "
        f"[relative](notes/a.md), [missing](<{tmp_path / 'gone.txt'}>), "
        f"[symlink](<{tmp_path / 'link.txt'}>), [token](<{secret}>), "
        f"[again](<{shot}>) and [sent](<{sent}>)."
    )
    sent_files = [{"path": str(sent), "name": "sent.pdf"}]
    messages: list[dict[str, Any]] = [
        {"id": "r", "role": "rem", "body": body, "files": sent_files},
        {"id": "u", "role": "user", "body": f"[mine](<{shot}>)"},
    ]
    marked = with_file_mentions(messages, tmp_path / "data")
    assert marked[1] is messages[1]
    # Existing regular files only, once each, in order. The reply's own files aren't
    # repeated, and a secret-looking file keeps its link but gets no attachment.
    linked = marked[0]["linked_files"]
    assert [{k: v for k, v in item.items() if k != "type"} for item in linked] == [
        {"id": str(shot), "name": "screen 1.png", "path": str(shot), "href": str(shot), "size": 4},
        {
            "id": str(log),
            "name": "run.log",
            "path": str(log),
            "href": f"{quote(str(log))}:12",
            "size": 2,
        },
    ]
    # The type picks the tile's icon and preview, as for the user's attachments.
    assert linked[0]["type"] == "image/png"
    assert "linked_files" not in messages[0]
    # A message shows at most LINKED_FILES_MAX linked files as attachments.
    many = [_file(tmp_path / "many" / f"{n}.txt") for n in range(LINKED_FILES_MAX + 3)]
    text = " ".join(f"[{p.name}](<{p}>)" for p in many)
    assert [item["name"] for item in linked_files(text)] == [
        p.name for p in many[:LINKED_FILES_MAX]
    ]


def test_the_desktop_chat_shows_linked_files_under_rems_message(api, tmp_path: Path):
    request, service = api
    shot = _file(tmp_path / "shots" / "home.png", b"png")
    with service.store.connect() as db:
        service.store.message(db, f"Here is [the screenshot](<{shot}>).", identity="shot")
    messages = {m["id"]: m for m in request("GET", "/api/rem/conversation").json()["messages"]}
    assert [f["path"] for f in messages["shot"]["linked_files"]] == [str(shot)]


def _worker_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: Path | None = None
) -> tuple[list[Any], Path]:
    """A worker thread whose final reply links a file in ``folder``, a folder, a missing
    file and an SSH key, plus an instruction and a thought that link the same file."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    key = _file(home / ".ssh" / "config", b"Host *")
    evidence = (folder or tmp_path) / "evidence"
    shot = _file(evidence / "after.png", b"png!")
    (evidence / "logs").mkdir()
    body = (
        f"Fixed. [Screenshot](<{shot}>), [logs](<{evidence / 'logs'}>), "
        f"[gone](<{evidence / 'gone.txt'}>) and [ssh](<{key}>)."
    )
    messages: list[Any] = [
        {"id": "u1", "role": "user", "sender": "rem", "body": f"Check [this](<{shot}>)."},
        {"id": "t1", "role": "assistant", "kind": "thought", "body": f"[shot](<{shot}>)"},
        {"id": "a1", "role": "assistant", "kind": "final", "body": body},
        {"id": "a2", "role": "assistant", "kind": "final", "body": "No files."},
    ]
    return messages, shot


def test_worker_thread_replies_list_the_files_they_link(api, tmp_path: Path, monkeypatch):
    request, service = api
    messages, shot = _worker_thread(tmp_path, monkeypatch)
    thread = {"metadata": {"title": "Fix"}, "messages": messages, "running": False}
    monkeypatch.setattr(service.store, "thread", lambda identity: thread)
    shown = {m["id"]: m for m in request("GET", "/api/rem/threads/t").json()["messages"]}
    # The reply's existing file only: folders, missing files and SSH keys get no tile.
    assert [(f["name"], f["href"], f["size"]) for f in shown["a1"]["linked_files"]] == [
        ("after.png", str(shot), 4)
    ]
    # Instructions and thoughts don't, and neither does a reply without links.
    assert not any("linked_files" in shown[i] for i in ("u1", "t1", "a2"))


def test_a_sandboxed_worker_keeps_the_evidence_it_links(home: RemHome, tmp_path: Path):
    import re

    from gofer.ui.file_mentions import with_thread_linked_files
    from tests.unit.test_rem_home import recipe, wait_run

    original = home.source
    saved: list[Path] = []

    async def source(**kwargs: Any):
        named = re.search(
            r"evidence you link in (\S+)\. The rest of the scratch folder is removed",
            kwargs["agent_instructions"],
        )
        async for event in original(**kwargs):
            if named and event.get("type") == "final":
                evidence = Path(named[1])
                scratch = Path(kwargs["trusted_scratch_dir"])
                assert evidence == scratch / "evidence" and evidence.is_dir()
                shot = _file(evidence / "shot.png", b"png")
                _file(scratch / "claude-1000" / "leftover.txt")
                saved.append(shot)
                event = {"type": "final", "message": {"body": f"Done. [shot](<{shot}>)"}}
            yield event

    home.source = source
    home.configure({"provider": "claude_code"})
    # A task handed over from another computer runs unattended, so it is sandboxed.
    task = home.create_task(
        {"request_id": "remote:req", **recipe(tmp_path, type="research")},
        remote_origin={"computer_id": "desk-a", "request_id": "req", "run_id": "sender-run"},
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked", "failed"})
    # Every worker turn of the run saves to the same folder.
    (shot,) = set(saved)
    assert shot.read_bytes() == b"png"
    scratch = shot.parent.parent
    assert scratch.parent == home.data_dir / "rem-work" / "turn-scratch"
    assert sorted(p.name for p in scratch.iterdir()) == ["evidence"]
    thread = home.store.thread(run["thread_id"])["messages"]
    replies = [m for m in with_thread_linked_files(thread, home.data_dir) if m.get("linked_files")]
    assert replies and all(
        [f["path"] for f in m["linked_files"]] == [str(shot)] for m in replies
    ), run


def test_only_a_turns_evidence_folder_gets_tiles_in_raticode_data(tmp_path: Path):
    data = tmp_path / "data"
    scratch = data / "rem-work" / "turn-scratch" / "run-1-execute"
    shot = _file(scratch / "evidence" / "x.png")
    above = _file(scratch / "x.png")
    # A worker can't release other data by linking it from its evidence folder.
    _file(data / "logs" / "backend.log")
    (data / "rem-work" / "turn-scratch" / "run-2-execute").mkdir()
    (data / "rem-work" / "turn-scratch" / "run-2-execute" / "evidence").symlink_to(data / "logs")
    linked = data / "rem-work" / "turn-scratch" / "run-2-execute" / "evidence" / "backend.log"
    text = f"[shot](<{shot}>) [above](<{above}>) [log](<{linked}>)"
    assert [item["path"] for item in linked_files(text, data)] == [str(shot)]
    assert [item["path"] for item in linked_files(text, data / "elsewhere")] == [
        str(shot),
        str(above),
        str(linked),
    ]
