"""Tapping a file link on the phone fetches that file directly, with no model involved."""

import base64
import json
import os
from typing import Any

import pytest

from gofer.devices.desktop_control import link_paths
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_desktop_control import phone as phone

THREAD = "0f3c2a4e-1b2d-4c5e-8f90-123456789abc"


def _offers(app: Any, peer: str, before: int | None) -> list[dict[str, Any]]:
    return [e for e in app.pending(peer, before) if e["type"] == "file.offer"]


def _outbox(app: Any) -> Any:
    return app.registry.db.execute("SELECT MAX(sequence) FROM device_outbox").fetchone()[0]


@pytest.fixture
def linked(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    brain = tmp_path / "brain"
    (brain / "reports").mkdir(parents=True)
    report = brain / "reports" / "launch plan.html"
    report.write_bytes(b"<p>Plan</p>" * 3000)
    rem.store.config["brain"] = {"root": str(brain)}
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    rem.store.messages += [
        {
            "id": "reply-1",
            "role": "rem",
            "body": f"The [launch plan](<{report}>) and [notes]({outside}).",
            "created_at": "t3",
        },
        # The user can type any path into their own message.
        {"id": "typed", "role": "user", "body": f"Send [this]({report})", "created_at": "t4"},
    ]
    return app, peer, context, send, report, outside


def test_a_tapped_link_in_rems_chat_offers_that_file_without_rem(linked):
    app, peer, _, send, report, _ = linked
    rem = app.desktop.rem
    home = send("desktop.read", "rem.home")["value"]
    assert home["file_links"] is True
    assert send("desktop.read", "overview")["value"]["file_links"] is True
    before = _outbox(app)
    result = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "reply-1", "href": str(report)},
    )
    assert result["ok"], result
    value = result["value"]
    channel = app.desktop.files_thread(peer)
    assert (value["name"], value["size"], value["thread_id"]) == (
        "launch plan.html",
        report.stat().st_size,
        channel,
    )
    # No Rem turn ran, and the path stays on the desktop.
    assert rem.calls == []
    assert str(report.parent) not in json.dumps(value)
    offers = _offers(app, peer, before)
    assert [(o["thread_id"], o["payload"]["file_id"]) for o in offers] == [
        (channel, value["file_id"])
    ]
    chunk = app.handle(
        peer, event("file.request", 700, thread_id=channel, payload={"file_id": value["file_id"]})
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == report.read_bytes()[:16384]


def test_link_spellings_are_read_the_way_desktop_opens_them(linked):
    app, _, _, send, report, _ = linked
    rem = app.desktop.rem
    encoded = str(report).replace(" ", "%20")
    spellings = [encoded, f"file://{encoded}", f"{encoded}:12", f"{encoded}#intro"]
    rem.store.messages.append(
        {
            "id": "reply-2",
            "role": "rem",
            "body": " ".join(f"[link]({s})" for s in spellings),
            "created_at": "t5",
        }
    )
    for href in spellings:
        result = send(
            "desktop.action", "files.link", {"source": "rem", "message_id": "reply-2", "href": href}
        )
        assert result["ok"], (href, result)
        assert result["value"]["name"] == "launch plan.html"


def test_links_outside_rems_folders_or_messages_are_refused(linked, tmp_path):
    app, _, _, send, report, outside = linked

    def fetch(**params: Any) -> Any:
        return send("desktop.action", "files.link", {"source": "rem", **params})

    before = _outbox(app)
    # A path the user typed into their own message gives the phone nothing.
    assert fetch(message_id="typed", href=str(report))["code"] == "not_found"
    # A path that isn't in the named message is refused even inside the brain.
    other = report.parent / "other.html"
    other.write_text("other")
    assert fetch(message_id="reply-1", href=str(other))["code"] == "not_found"
    assert fetch(message_id="missing", href=str(report))["code"] == "not_found"
    # Web addresses open in the phone's browser; they never name a desktop file.
    app.desktop.rem.store.messages.append(
        {"id": "web", "role": "rem", "body": "[site](https://example.com/a.txt)"}
    )
    assert fetch(message_id="web", href="https://example.com/a.txt")["code"] == "invalid"
    assert fetch(message_id="reply-1", href="")["code"] == "invalid"
    assert fetch(message_id="reply-1", href="x" * 2049)["code"] == "invalid"
    assert send("desktop.action", "files.link", {"source": "disk", "href": "/"})["code"] == (
        "invalid"
    )
    assert _offers(app, peer=linked[1], before=before) == []


def test_symlinks_folders_and_large_files_are_not_sent(linked):
    app, _, _, send, report, outside = linked
    brain = report.parent
    link = brain / "link.txt"
    os.symlink(outside, link)
    big = brain / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(10 * 1024 * 1024 + 1)
    app.desktop.rem.store.messages.append(
        {"id": "risky", "role": "rem", "body": f"[a]({link}) [b]({brain}) [c]({big})"}
    )

    def fetch(href: Any) -> Any:
        params = {"source": "rem", "message_id": "risky", "href": str(href)}
        return send("desktop.action", "files.link", params)

    assert fetch(link)["code"] == "unauthorized"
    assert fetch(brain)["code"] == "unauthorized"
    assert fetch(big)["code"] == "invalid"
    big.unlink()
    assert fetch(big)["code"] == "not_found"


def test_tapping_again_replaces_the_earlier_offer(linked):
    app, peer, _, send, report, _ = linked
    params = {"source": "rem", "message_id": "reply-1", "href": str(report)}
    first = send("desktop.action", "files.link", params)["value"]["file_id"]
    second = send("desktop.action", "files.link", params)["value"]["file_id"]
    assert first != second
    ids = {
        r[0] for r in app.registry.db.execute("SELECT id FROM device_files WHERE peer=?", (peer,))
    }
    assert second in ids and first not in ids
    # A copy the phone is downloading is kept.
    channel = app.desktop.files_thread(peer)
    app.handle(peer, event("file.request", 800, thread_id=channel, payload={"file_id": second}))
    third = send("desktop.action", "files.link", params)["value"]["file_id"]
    ids = {
        r[0] for r in app.registry.db.execute("SELECT id FROM device_files WHERE peer=?", (peer,))
    }
    assert {second, third} <= ids
    app.remove_peer(peer)
    assert app.registry.db.execute("SELECT COUNT(*) FROM device_link_files").fetchone()[0] == 0


def test_rem_task_thread_links_resolve_against_the_task_project(linked, monkeypatch):
    app, peer, context, send, _, _ = linked
    store = app.desktop.rem.store
    root = context["projects"][0]["root"]
    os.makedirs(os.path.join(root, "src"))
    target = os.path.join(root, "src", "app.py")
    with open(target, "w") as handle:
        handle.write("print('hi')\n")

    def thread(identifier: str) -> dict[str, Any]:
        assert identifier == THREAD
        return {
            "metadata": {"title": "Fix", "projectRoot": root},
            "running": False,
            "messages": [
                {"id": "a1", "role": "assistant", "body": "Changed [app.py](src/app.py:3)."},
                {"id": "u1", "role": "user", "body": "Look at [it](src/app.py)"},
            ],
        }

    monkeypatch.setattr(store, "thread", thread)
    base = {"source": "rem_thread", "thread_id": THREAD}
    result = send(
        "desktop.action", "files.link", {**base, "message_id": "a1", "href": "src/app.py:3"}
    )
    assert result["ok"], result
    assert result["value"]["thread_id"] == app.desktop.files_thread(peer)
    refused = send(
        "desktop.action", "files.link", {**base, "message_id": "u1", "href": "src/app.py"}
    )
    assert refused["code"] == "not_found"
    # A Rem task thread needs its thread ID.
    missing = send(
        "desktop.action", "files.link", {"source": "rem_thread", "message_id": "a1", "href": "x"}
    )
    assert missing["code"] == "invalid"
    # A task thread's link stays inside the open folders, unlike Rem's own chat.
    outside = linked[5]
    store_thread = thread

    def thread_with_outside(identifier: str) -> dict[str, Any]:
        data = store_thread(identifier)
        data["messages"].append(
            {"id": "a2", "role": "assistant", "body": f"See [notes]({outside})"}
        )
        return data

    monkeypatch.setattr(store, "thread", thread_with_outside)
    refused = send(
        "desktop.action", "files.link", {**base, "message_id": "a2", "href": str(outside)}
    )
    assert (refused["code"], "Second Brain" in refused["detail"]) == ("unauthorized", True)
    # Rem's chat has no folder to resolve a relative link against.
    store.messages.append({"id": "rel", "role": "rem", "body": "[app](src/app.py)"})
    relative = send(
        "desktop.action", "files.link", {"source": "rem", "message_id": "rel", "href": "src/app.py"}
    )
    assert relative["code"] == "invalid"


def test_shared_thread_links_use_that_threads_grant_and_folders(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    notes, code = (p["root"] for p in context["projects"])
    with open(os.path.join(code, "README.md"), "w") as handle:
        handle.write("Project documentation")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    long_body = "x" * 4100 + "See [readme](../code/README.md) and [notes](" + str(outside) + ")"
    meta = {
        "id": "desktop-thread",
        "title": "Notes",
        "projectRoot": notes,
        "provider": "codex",
        "model": "model-a",
        "resources": {"shell": True},
    }
    row = app.registry.db.execute("SELECT thread FROM device_grants WHERE peer=?", (peer,))
    thread = row.fetchone()[0]
    revision = app.workspace.value(peer, thread)["revision"]
    app.workspace.exchange(
        peer,
        meta,
        [
            {"id": "old", "role": "assistant", "body": "README is here"},
            {"id": "long", "role": "assistant", "body": long_body},
            {"id": "mine", "role": "user", "body": f"[x]({code}/README.md)"},
        ],
        context,
        revision,
    )
    history = app.handle(
        peer,
        event(
            "thread.history.request",
            900,
            thread_id=thread,
            payload={"before": None, "include_presentation": True},
        ),
    )[-1]["payload"]["messages"]
    # The link sits in the second fragment of a long message.
    fragment = next(m for m in history if m["source_id"] == "long" and m["offset"] == 4000)
    base = {"source": "thread", "thread_id": thread}
    before = _outbox(app)
    result = send(
        "desktop.action",
        "files.link",
        {**base, "message_id": fragment["id"], "href": "../code/README.md"},
    )
    assert result["ok"], result
    assert (result["value"]["name"], result["value"]["thread_id"]) == ("README.md", thread)
    assert [o["thread_id"] for o in _offers(app, peer, before)] == [thread]
    outside_link = {**base, "message_id": "long", "href": str(outside)}
    assert send("desktop.action", "files.link", outside_link)["code"] == "unauthorized"
    typed = {**base, "message_id": "mine", "href": f"{code}/README.md"}
    assert send("desktop.action", "files.link", typed)["code"] == "not_found"
    app.revoke_grant(peer, thread)
    revoked = {**base, "message_id": fragment["id"], "href": "../code/README.md"}
    assert send("desktop.action", "files.link", revoked)["code"] == "unauthorized"


def test_file_links_need_phone_access_like_every_desktop_action(phone, tmp_path):
    app, peer, _, send = phone
    refused = send(
        "desktop.action", "files.link", {"source": "rem", "message_id": "m2", "href": "/etc/hosts"}
    )
    assert refused["code"] == "unauthorized"


@pytest.mark.parametrize(
    "href,expected",
    [
        ("/home/me/a b.html", ["/home/me/a b.html"]),
        ("</home/me/a b.html>", ["/home/me/a b.html"]),
        ("/home/me/a%20b.html", ["/home/me/a%20b.html", "/home/me/a b.html"]),
        ("file:///home/me/a%20b.html", ["/home/me/a b.html"]),
        ("file://localhost/home/me/a.txt", ["/home/me/a.txt"]),
        ("src/app.py:12:4", ["src/app.py"]),
        ("notes.md?plain=1#top", ["notes.md"]),
        ("https://example.com/a.txt", []),
        ("mailto:me@example.com", []),
        ("//example.com/a.txt", []),
        ("#heading", []),
        ("file://server/share/a.txt", []),
    ],
)
def test_link_paths(href: str, expected: list[str]) -> None:
    assert link_paths(href) == expected


def test_a_brain_opened_through_a_symlinked_folder_still_sends_its_files(linked, tmp_path):
    app, _, _, send, report, _ = linked
    alias = tmp_path / "brain-alias"
    os.symlink(report.parent.parent, alias)
    app.desktop.rem.store.config["brain"] = {"root": str(alias)}
    linked_path = alias / "reports" / report.name
    app.desktop.rem.store.messages.append(
        {"id": "alias", "role": "rem", "body": f"[plan](<{linked_path}>)"}
    )
    params = {"source": "rem", "message_id": "alias", "href": str(linked_path)}
    result = send("desktop.action", "files.link", params)
    assert result["ok"], result
    assert result["value"]["name"] == report.name


def test_links_to_secret_files_inside_open_folders_are_refused(linked):
    app, peer, _, send, report, _ = linked
    rem = app.desktop.rem
    brain = report.parent.parent
    # Synthetic credentials an agent might link to by mistake or under a page's influence.
    env = brain / ".env"
    env.write_text("API_KEY=synthetic-not-a-real-key")
    token = brain / "reports" / "github-token.txt"
    token.write_text("synthetic")
    rem.store.messages.append(
        {
            "id": "reply-secret",
            "role": "rem",
            "body": f"See [env]({env}), [token]({token}) and [plan](<{report}>).",
            "created_at": "t6",
        }
    )
    before = _outbox(app)
    for secret in (env, token):
        refused = send(
            "desktop.action",
            "files.link",
            {"source": "rem", "message_id": "reply-secret", "href": str(secret)},
        )
        assert refused["code"] == "unauthorized"
        assert "passwords, keys or sign-ins" in refused["detail"]
    assert _offers(app, peer, before) == []
    # An ordinary file in the same message still opens.
    plan = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "reply-secret", "href": str(report)},
    )
    assert plan["ok"], plan


def test_shared_thread_links_to_secret_files_are_refused(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    notes, code = (p["root"] for p in context["projects"])
    with open(os.path.join(code, ".env"), "w") as handle:
        handle.write("TOKEN=synthetic-not-a-real-token")
    meta = {"id": "desktop-thread", "title": "Notes", "projectRoot": notes, "provider": "codex"}
    thread = app.registry.db.execute(
        "SELECT thread FROM device_grants WHERE peer=?", (peer,)
    ).fetchone()[0]
    revision = app.workspace.value(peer, thread)["revision"]
    body = "The settings are in [.env](../code/.env)."
    app.workspace.exchange(
        peer, meta, [{"id": "env", "role": "assistant", "body": body}], context, revision
    )
    refused = send(
        "desktop.action",
        "files.link",
        {"source": "thread", "thread_id": thread, "message_id": "env", "href": "../code/.env"},
    )
    assert refused["code"] == "unauthorized"
    assert "passwords, keys or sign-ins" in refused["detail"]


def test_links_in_a_replys_details_open_like_links_in_its_text(linked, tmp_path):
    app, peer, _, send, report, _ = linked
    rem = app.desktop.rem
    shot = tmp_path / "screenshots" / "voice synth" / "small-320dp-paused.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fixture")
    rem.store.messages += [
        {
            "id": "reply-details",
            "role": "rem",
            "body": "Here are the screenshots.",
            "details": f"- [Paused](<{shot}>)\n- [Plan](<{report}>)",
            "created_at": "t7",
        },
        # Details on the user's own message never count.
        {"id": "typed-details", "role": "user", "body": "x", "details": f"[p](<{report}>)"},
    ]
    for href, name in ((str(shot), "small-320dp-paused.png"), (str(report), "launch plan.html")):
        result = send(
            "desktop.action",
            "files.link",
            {"source": "rem", "message_id": "reply-details", "href": href},
        )
        assert result["ok"], (href, result)
        assert result["value"]["name"] == name
        assert result["value"]["thread_id"] == app.desktop.files_thread(peer)
        assert str(shot.parent) not in json.dumps(result["value"])
    typed = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "typed-details", "href": str(report)},
    )
    assert typed["code"] == "not_found"


def test_rem_chat_links_reach_files_outside_open_folders_like_its_reply_files(linked, tmp_path):
    app, peer, _, send, _, outside = linked
    rem = app.desktop.rem
    before = _outbox(app)
    # The file is outside the open folders, in a reply Rem wrote: it is read from its own folder.
    result = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "reply-1", "href": str(outside)},
    )
    assert result["ok"], result
    channel = app.desktop.files_thread(peer)
    chunk = app.handle(
        peer,
        event(
            "file.request", 701, thread_id=channel, payload={"file_id": result["value"]["file_id"]}
        ),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == b"private"
    # Secret-looking files and Raticode's own data stay on the desktop wherever they are.
    secret = tmp_path / "auth.json"
    secret.write_text("{}")
    rem.data_dir = tmp_path / "rem-data"
    own = rem.data_dir / "rem-home.sqlite3"
    own.parent.mkdir()
    own.write_bytes(b"synthetic")
    link = tmp_path / "linked.txt"
    link.symlink_to(outside)
    rem.store.messages.append(
        {
            "id": "reply-outside",
            "role": "rem",
            "body": f"[a]({secret}) [b]({own}) [c]({link})",
            "created_at": "t8",
        }
    )
    offered = len(_offers(app, peer, before))
    for path in (secret, own):
        refused = send(
            "desktop.action",
            "files.link",
            {"source": "rem", "message_id": "reply-outside", "href": str(path)},
        )
        assert refused["code"] == "unauthorized"
        assert "passwords, keys or sign-ins" in refused["detail"]
    symlinked = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "reply-outside", "href": str(link)},
    )
    assert symlinked["code"] == "unauthorized"
    assert len(_offers(app, peer, before)) == offered


def test_phones_get_the_full_paths_rem_wrote_and_can_open_them(linked, tmp_path, monkeypatch):
    app, peer, _, send, report, _ = linked
    rem = app.desktop.rem
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = tmp_path / "My Videos"
    folder.mkdir()
    clip = folder / "dock #2 take?.mp4"
    clip.write_bytes(b"clip")
    notes = tmp_path / "home" / "notes" / "plan.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("plan")
    secret = tmp_path / "auth.json"
    secret.write_text("{}")
    body = (
        f"The clip is at {clip}. Open `{report}` or ~/notes/plan.md, "
        f"not {tmp_path}/missing.txt or {secret}."
    )
    rem.store.messages += [
        {"id": "paths", "role": "rem", "body": body, "details": f"- {notes}", "created_at": "t9"},
        {"id": "mine", "role": "user", "body": f"Look at {clip}", "created_at": "t10"},
    ]
    shown = {m["id"]: m for m in send("desktop.read", "rem.home")["value"]["messages"]}
    # Only existing files, as written; the phone gets text to underline, never more.
    assert shown["paths"]["file_mentions"] == [
        str(clip),
        str(report),
        "~/notes/plan.md",
        str(secret),
        str(notes),
    ]
    assert "file_mentions" not in shown["mine"]
    for href, name in (
        (str(clip), "dock #2 take?.mp4"),
        (str(report), "launch plan.html"),
        ("~/notes/plan.md", "plan.md"),
        (str(notes), "plan.md"),
    ):
        result = send(
            "desktop.action", "files.link", {"source": "rem", "message_id": "paths", "href": href}
        )
        assert result["ok"], (href, result)
        assert result["value"]["name"] == name
    # Being linked grants nothing: secret-looking files still stay on the desktop.
    refused = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "paths", "href": str(secret)},
    )
    assert refused["code"] == "unauthorized"
    # The user's own message gives the phone nothing, even for a real file.
    mine = send(
        "desktop.action", "files.link", {"source": "rem", "message_id": "mine", "href": str(clip)}
    )
    assert mine["code"] == "not_found"


def test_a_piece_cut_from_a_longer_link_is_refused(linked):
    """A link must be whole in Rem's message, not any substring of it (security review B-O18)."""
    app, _, _, send, report, _ = linked
    parent = str(report.parent)
    rem = app.desktop.rem
    rem.store.messages.append(
        {"id": "reply-3", "role": "rem", "body": f"See {report} and more.", "created_at": "t6"}
    )
    for piece in (parent, str(report)[:-3], str(report)[1:]):
        refused = send(
            "desktop.action",
            "files.link",
            {"source": "rem", "message_id": "reply-3", "href": piece},
        )
        assert refused["code"] == "not_found", piece
    whole = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "reply-3", "href": str(report)},
    )
    assert whole["ok"], whole
