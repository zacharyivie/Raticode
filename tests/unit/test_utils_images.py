from __future__ import annotations

import pytest

from gofer.core import approvals, health
from gofer.devices import desktop_control
from gofer.social import service as social_service
from gofer.ui import link_previews, organization_recovery, organizations, rem_home_provider
from gofer.utils.images import sniff_image_type

SAMPLES = [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
    (b"GIF87a\x01\x00\x01\x00", "image/gif"),
    (b"GIF89a\x01\x00\x01\x00", "image/gif"),
    (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
    (b"<svg xmlns=", None),
    (b"PK\x03\x04", None),
    (b"", None),
]


@pytest.mark.parametrize(("head", "expected"), SAMPLES)
def test_every_image_reader_names_the_same_type_from_first_bytes(
    head: bytes, expected: str | None
) -> None:
    assert sniff_image_type(head) == expected
    assert link_previews.image_mime(head) == expected
    assert desktop_control.image_type(head) == expected
    assert social_service._sniff(head) == expected
    kind = rem_home_provider.image_kind(head)
    assert (kind[0] if kind else None) == expected


def test_social_media_still_recognises_its_videos() -> None:
    assert social_service._sniff(b"\x1aE\xdf\xa3\x01\x00\x00\x00") == "video/webm"
    assert social_service._sniff(b"\x00\x00\x00\x18ftypmp42") == "video/mp4"
    assert social_service._sniff(b"\x00\x00\x00\x14ftypqt  ") == "video/quicktime"


def test_powershell_lookup_and_blocked_spell_have_one_copy() -> None:
    # Health reports the PowerShell that desktop notifications actually run.
    assert health._windows_powershell_path is approvals._windows_powershell_path
    # Organizations stores blocked spells that recovery and Auto mode compare against.
    assert organizations.blocked_spell is organization_recovery.blocked_spell
