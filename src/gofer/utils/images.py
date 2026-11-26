"""Image types told apart by a file's first bytes, never by its name or a claimed type."""

from __future__ import annotations

# Signatures at the start of the file. WebP is checked separately: its RIFF header puts
# the format name after a 4-byte size.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(data: bytes) -> str | None:
    """The media type of a JPEG, PNG, GIF or WebP image from its first 12+ bytes, or None.

    Callers decide which of these types they accept and how large an image may be.
    """
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
