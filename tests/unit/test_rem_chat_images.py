"""Images attached in Rem's chat reach the chat model, or the reply says why not."""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import zlib
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import AgentResult
from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.ui import rem_home_provider as provider_module
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_media import attachment_thread_dir, store_chat_attachments
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.rem_home import IMAGE_RULES, RemHome, attachment_context, attachment_images
from gofer.ui.rem_home_provider import (
    CHAT_IMAGE_LIMIT,
    ChatImage,
    chat_image_refusal,
    claude_user_message,
    image_kind,
)


def png(colour: tuple[int, int, int] = (20, 40, 230), size: int = 8) -> bytes:
    raw = b"".join(b"\x00" + bytes(colour) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


IMAGE = ChatImage("screenshot.png", "image/png", ".png", png())


@pytest.fixture(autouse=True)
def no_cli_probes(monkeypatch: pytest.MonkeyPatch):
    """Keep these tests from running the real provider CLIs' --help."""
    monkeypatch.setattr(provider_module, "no_saved_session", lambda name: [])


def run_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    model: str = "cli-default",
    on_reply: Any = None,
) -> dict[str, Any]:
    """Run one chat call with a fake Agent; return what the CLI would have been given."""
    observed: dict[str, Any] = {}

    class Agent:
        def __init__(self, config, subscription):
            observed["config"] = config
            observed["subscription"] = subscription

        async def run(self, **kwargs):
            observed.update(kwargs)
            subscription = observed["subscription"]
            settings = kwargs["provider_settings"]
            # The files a CLI attaches must exist while it runs.
            observed["files"] = {
                p.name: p.read_bytes() for p in observed["config"].working_dir.iterdir()
            }
            if provider != "antigravity":
                observed["command"] = subscription._build_command(
                    "-" if subscription.prompt_via_stdin else kwargs["prompt_override"],
                    [],
                    [],
                    [],
                    settings,
                )
            if subscription.prompt_via_stdin:
                observed["stdin"] = subscription._stdin_bytes(kwargs["prompt_override"])
            return AgentResult(
                agent_id="Rem",
                success=True,
                output="",
                message='{"reply":"It is blue."}',
                exit_code=0,
                duration_seconds=0,
            )

    monkeypatch.setattr(provider_module, "Agent", Agent)
    executable = tmp_path / "fake-cli"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    from gofer.subscriptions import claude_code, cli_providers, codex

    for module in (claude_code, cli_providers, codex):
        monkeypatch.setattr(module, "resolve_provider_executable", lambda p: str(executable))
    observed["result"] = provider_module.generate_decision(
        "What does the screenshot show?",
        {"provider": provider, "model": model},
        threading.Event(),
        tmp_path,
        on_reply,
        images=[IMAGE],
    )
    return observed


@pytest.mark.parametrize(
    "model", ["sonnet[1m]", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
)
@pytest.mark.parametrize("streaming", [False, True])
def test_claude_code_sends_images_as_image_blocks_for_every_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, streaming: bool
):
    """The Sonnet 5.5 bug: the image was only a path in text, and the model had no tools."""
    observed = run_chat(
        monkeypatch, tmp_path, "claude_code", model, (lambda text: None) if streaming else None
    )
    assert observed["result"] == {"reply": "It is blue."}
    command = observed["command"]
    assert command[command.index("--model") + 1] == model
    assert command[command.index("--input-format") + 1] == "stream-json"
    # Still no tools, no MCP servers and no folder grants.
    assert command[command.index("--tools") + 1] == ""
    assert "--add-dir" not in command and "--allowedTools" not in command
    message = json.loads(observed["stdin"])
    assert message["type"] == "user" and message["message"]["role"] == "user"
    content = message["message"]["content"]
    assert content[0] == {"type": "text", "text": "Image 1: screenshot.png"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"
    assert base64.b64decode(content[1]["source"]["data"]) == IMAGE.data
    assert content[-1] == {"type": "text", "text": "What does the screenshot show?"}
    # Nothing is written to disk for Claude Code; the image travels on stdin.
    assert observed["files"] == {}


def test_claude_code_without_images_keeps_the_plain_text_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    observed: dict[str, Any] = {}

    class Agent:
        def __init__(self, config, subscription):
            observed["subscription"] = subscription

        async def run(self, **kwargs):
            subscription = observed["subscription"]
            observed["stdin"] = subscription._stdin_bytes(kwargs["prompt_override"])
            observed["extra"] = kwargs["provider_settings"].extra_args
            return AgentResult(
                agent_id="Rem",
                success=True,
                output="",
                message='{"reply":"Hi"}',
                exit_code=0,
                duration_seconds=0,
            )

    monkeypatch.setattr(provider_module, "Agent", Agent)
    provider_module.generate_decision(
        "Say hi", {"provider": "claude_code"}, threading.Event(), tmp_path
    )
    assert observed["stdin"] == b"Say hi"
    assert "--input-format" not in observed["extra"]


@pytest.mark.parametrize(
    ("provider", "flag"),
    [
        ("codex", "--image"),
        ("cursor", "--image"),
        ("copilot", "--attachment"),
        ("opencode", "--file"),
    ],
)
def test_file_option_providers_attach_a_private_copy_of_each_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, flag: str
):
    observed = run_chat(monkeypatch, tmp_path, provider)
    assert observed["result"] == {"reply": "It is blue."}
    # The CLI reads a copy inside Rem's empty, temporary folder, never the upload itself.
    assert observed["files"] == {"image-1.png": IMAGE.data}
    root = observed["config"].working_dir
    command = observed["command"]
    assert f"{flag}={root / 'image-1.png'}" in command
    assert not root.exists()
    settings = observed["provider_settings"]
    if provider == "codex":
        # --image takes several values, so an option must follow it before the prompt.
        assert command[-1] == "-"
        position = command.index(f"--image={root / 'image-1.png'}")
        assert command[position + 1].startswith("-") and command[position + 1] != "-"
        # The model gets the picture without a file-viewing tool or a writable sandbox.
        assert "tools.view_image=false" in settings.extra_args
        assert "features.shell_tool=false" in settings.extra_args
        assert settings.sandbox_mode == "read-only"
    else:
        subscription = observed["subscription"]
        assert subscription.tool_free is True and subscription.read_only is True


@pytest.mark.parametrize(("provider", "words"), [("grok", "command-line"), ("antigravity", "text")])
def test_providers_whose_chat_cant_take_images_say_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, words: str
):
    reason = chat_image_refusal(provider)
    assert reason and words in reason
    with pytest.raises(ValueError, match=reason.replace("(", r"\(").replace(")", r"\)")):
        run_chat(monkeypatch, tmp_path, provider)


def test_every_provider_has_an_image_answer():
    for provider in CLI_PROVIDERS:
        reason = chat_image_refusal(provider)
        assert reason is None or "Raticode" in reason or "command-line" in reason


def test_images_are_recognised_by_their_bytes():
    assert image_kind(png()) == ("image/png", ".png")
    assert image_kind(b"\xff\xd8\xff\xe0rest") == ("image/jpeg", ".jpg")
    assert image_kind(b"GIF89a....") == ("image/gif", ".gif")
    assert image_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", ".webp")
    assert image_kind(b"<svg xmlns=") is None
    assert image_kind(b"PK\x03\x04") is None


def test_claude_message_numbers_several_images_in_order():
    second = ChatImage("b.jpg", "image/jpeg", ".jpg", b"\xff\xd8\xffdata")
    content = json.loads(claude_user_message("Compare them", [IMAGE, second]))["message"]["content"]
    assert [c.get("text") for c in content if c["type"] == "text"] == [
        "Image 1: screenshot.png",
        "Image 2: b.jpg",
        "Compare them",
    ]
    assert content[3]["source"]["media_type"] == "image/jpeg"


def upload(data_dir: Path, name: str, content: bytes, kind: str) -> dict[str, Any]:
    stored = store_chat_attachments(
        {
            "threadId": "rem-home",
            "files": [{"name": name, "type": kind, "data": base64.b64encode(content).decode()}],
        },
        data_dir,
    )["attachments"][0]
    return {
        **stored,
        "path": str(attachment_thread_dir(data_dir, "rem-home") / stored["storageName"]),
    }


def describe(attachments: list[dict[str, Any]], data_dir: Path, provider: str = "claude_code"):
    described = attachment_context(attachments)
    return described, attachment_images(attachments, described, data_dir, provider)


def test_attached_images_are_read_from_rem_uploads_and_marked_shown(tmp_path: Path):
    attachments = [
        upload(tmp_path, "screenshot.png", png(), "image/png"),
        upload(tmp_path, "notes.txt", b"plain notes", "text/plain"),
    ]
    described, images = describe(attachments, tmp_path)
    assert images == [ChatImage("screenshot.png", "image/png", ".png", png())]
    assert described[0]["image"] == "shown" and described[0]["image_number"] == 1
    # A text file stays text: its contents go in the prompt, no image note.
    assert "image" not in described[1] and described[1]["text"] == "plain notes"


def test_an_image_that_cant_be_shown_carries_the_real_reason(tmp_path: Path):
    fake = upload(tmp_path, "fake.png", b"not really a picture", "image/png")
    heic = upload(tmp_path, "photo.heic", b"\x00\x00\x00\x18ftypheic", "image/heic")
    big = upload(tmp_path, "huge.png", png() + b"\x00" * CHAT_IMAGE_LIMIT, "image/png")
    gone = upload(tmp_path, "gone.png", png(), "image/png")
    Path(gone["path"]).unlink()
    described, images = describe([fake, heic, big, gone], tmp_path)
    assert images == []
    reasons = [d["not_shown_because"] for d in described]
    assert all(d["image"] == "not shown" for d in described)
    assert "PNG, JPEG, GIF or WebP" in reasons[0] and "PNG, JPEG, GIF or WebP" in reasons[1]
    assert "larger than 5 MB" in reasons[2]
    assert reasons[3] == "the file is no longer available"


@pytest.mark.parametrize("provider", ["grok", "antigravity"])
def test_providers_without_chat_images_explain_on_each_image(tmp_path: Path, provider: str):
    described, images = describe(
        [upload(tmp_path, "s.png", png(), "image/png")], tmp_path, provider
    )
    assert images == []
    assert described[0]["image"] == "not shown"
    assert described[0]["not_shown_because"] == chat_image_refusal(provider)


def test_only_rem_uploads_are_read_never_links_or_other_paths(tmp_path: Path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(png())
    linked = upload(tmp_path, "linked.png", png(), "image/png")
    Path(linked["path"]).unlink()
    os.symlink(outside, linked["path"])
    # A record naming another file can't redirect the read: the stored name decides.
    forged = {**upload(tmp_path, "real.png", png((1, 2, 3)), "image/png"), "path": str(outside)}
    traversal = {"storageName": "0" * 32 + "-../../outside.png", "name": "x", "type": "image/png"}
    described, images = describe([linked, forged, traversal], tmp_path)
    assert described[0]["not_shown_because"] == "the file is no longer available"
    assert images == [ChatImage("real.png", "image/png", ".png", png((1, 2, 3)))]
    assert described[2]["not_shown_because"] == "the file is no longer available"


def home_for(data: Path, provider: str, monkeypatch: pytest.MonkeyPatch) -> RemHome:
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "memory_available_bytes", lambda: 8 * 1024**3)
    monkeypatch.setattr(rem_home_module, "swap_blocked", lambda: None)

    async def source(**kwargs: Any):
        yield {"type": "final", "message": {"body": "done"}}

    home = RemHome(
        data,
        source=source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        start=False,
    )
    home.configure({"provider": "codex", "model": "cli-default", "chat_provider": provider})
    return home


@pytest.mark.parametrize("provider", ["claude_code", "codex", "grok"])
def test_rem_reply_sends_the_screenshot_to_its_chat_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
):
    import gofer.ui.rem_home as rem_home_module

    calls: list[dict[str, Any]] = []

    def generate_decision(prompt, config, cancel, data_dir, on_reply=None, images=()):
        calls.append({"prompt": prompt, "config": config, "images": list(images)})
        return {"reply": "That's the certificate list."}

    monkeypatch.setattr(rem_home_module, "generate_decision", generate_decision)
    data = tmp_path / "data"
    data.mkdir()
    home = home_for(data, provider, monkeypatch)
    try:
        shot = upload(data, "image.png", png(), "image/png")
        home.submit_message(
            {"request_id": "shot", "body": "What are these options?", "attachments": [shot]}
        )
        home._reply()
        assert len(calls) == 1
        call = calls[0]
        assert call["config"]["provider"] == provider
        assert IMAGE_RULES in call["prompt"]
        if provider == "grok":
            assert call["images"] == []
            assert chat_image_refusal("grok") in call["prompt"]
        else:
            assert call["images"] == [ChatImage("image.png", "image/png", ".png", png())]
            assert '"image":"shown"' in call["prompt"].replace(" ", "")
        # The images belong to that reply only.
        assert home._drafting.images == ()
        home.submit_message({"request_id": "next", "body": "Thanks"})
        home._reply()
        assert calls[1]["images"] == [] and IMAGE_RULES not in calls[1]["prompt"]
    finally:
        home.close()
        home.jobs.close()
        home.steering.close()
