from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from gofer.ui import chat_media


def upload(root: Path, thread: str = "original"):
    return chat_media.store_chat_attachments(
        {
            "threadId": thread,
            "files": [{"name": "note.txt", "data": base64.b64encode(b"safe").decode()}],
        },
        root,
    )["attachments"]


@pytest.mark.parametrize("linked_parent", [False, True])
def test_upload_rejects_linked_attachment_directories(tmp_path, linked_parent):
    outside = tmp_path / "outside"
    outside.mkdir()
    storage = tmp_path / "chat-attachments"
    link = storage if linked_parent else storage / "original"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(chat_media.ChatMediaError):
        upload(tmp_path)
    assert list(outside.iterdir()) == []


def test_fork_rejects_linked_source_thread(tmp_path):
    attachments = upload(tmp_path)
    source = tmp_path / "chat-attachments/original"
    source.rename(tmp_path / "outside")
    source.symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(chat_media.ChatMediaError):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert not (tmp_path / "chat-attachments/fork").exists()


@pytest.mark.parametrize("parent_link", [False, True])
def test_fork_rejects_source_swap_after_resolution(tmp_path, monkeypatch, parent_link):
    attachments = upload(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / attachments[0]["storageName"]).write_bytes(b"private")
    original = chat_media.resolve_chat_attachment

    def swapped(*args, **kwargs):
        source = original(*args, **kwargs)
        if parent_link:
            source.parent.rename(tmp_path / "moved")
            source.parent.symlink_to(outside, target_is_directory=True)
        else:
            source.unlink()
            source.symlink_to(outside / source.name)
        return source

    monkeypatch.setattr(chat_media, "resolve_chat_attachment", swapped)
    with pytest.raises(chat_media.ChatMediaError):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert not list((tmp_path / "chat-attachments/fork").glob("*"))


def test_fork_bounds_actual_content_when_size_metadata_is_stale(tmp_path, monkeypatch):
    attachments = upload(tmp_path)
    source = tmp_path / "chat-attachments/original" / attachments[0]["storageName"]
    source.write_bytes(b"larger than the configured limit")
    monkeypatch.setattr(chat_media, "CHAT_ATTACHMENT_MAX_FILE_BYTES", 8)
    original_stat = Path.stat

    def stale(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        return SimpleNamespace(st_mode=result.st_mode, st_size=4) if path == source else result

    monkeypatch.setattr(Path, "stat", stale)
    with pytest.raises(chat_media.ChatMediaError, match="20 MB"):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert not list((tmp_path / "chat-attachments/fork").glob("*"))


def test_upload_accepts_data_root_alias_and_keeps_private_permissions(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    attachment = upload(alias)[0]
    path = chat_media.resolve_chat_attachment(attachment, data_dir=alias, thread_id="original")
    assert path.read_bytes() == b"safe"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_fork_collision_preserves_existing_file_and_removes_earlier_copies(tmp_path):
    attachments = upload(tmp_path) + upload(tmp_path)
    target = tmp_path / "chat-attachments/fork"
    target.mkdir()
    existing = target / attachments[1]["storageName"]
    existing.write_bytes(b"keep this file")

    with pytest.raises(chat_media.ChatMediaError):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert list(target.iterdir()) == [existing]
    assert existing.read_bytes() == b"keep this file"


def test_fork_rollback_does_not_follow_replaced_destination_parent(tmp_path, monkeypatch):
    attachments = upload(tmp_path) + upload(tmp_path)
    target = tmp_path / "chat-attachments/fork"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / attachments[0]["storageName"]
    sentinel.write_bytes(b"keep outside file")
    original = chat_media.atomic_binary_output
    writes = 0

    @contextmanager
    def fail_second_write(destination, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            target.rename(tmp_path / "moved")
            target.symlink_to(outside, target_is_directory=True)
            raise OSError("Destination replaced")
        with original(destination, **kwargs) as output:
            yield output

    monkeypatch.setattr(chat_media, "atomic_binary_output", fail_second_write)
    with pytest.raises(chat_media.ChatMediaError):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert sentinel.read_bytes() == b"keep outside file"


def test_fork_rejects_linked_destination(tmp_path):
    attachments = upload(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "chat-attachments/fork").symlink_to(outside, target_is_directory=True)
    with pytest.raises(chat_media.ChatMediaError):
        chat_media.copy_chat_attachments(
            {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments},
            tmp_path,
        )
    assert list(outside.iterdir()) == []
