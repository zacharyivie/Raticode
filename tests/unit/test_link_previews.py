from __future__ import annotations

import base64
import socket

import pytest

from gofer.ui import link_previews
from gofer.ui.link_previews import LinkPreviews, PreviewError, page_metadata, youtube_id


def resolver(*addresses: str):
    def resolve(_host, port, type=None):  # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_links_name_their_video(url: str) -> None:
    assert youtube_id(url) == "dQw4w9WgXcQ"


def test_other_links_are_not_videos() -> None:
    assert youtube_id("https://www.youtube.com/channel/abc") is None
    assert youtube_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert youtube_id("https://youtu.be/short") is None


def test_page_metadata_prefers_open_graph_and_resolves_the_image() -> None:
    html = """<html><head><title>Plain title</title>
    <meta property="og:title" content="Card  title">
    <meta name="description" content="Plain summary">
    <meta property="og:site_name" content="Example">
    <meta property="og:image" content="/img/card.png">
    </head><body><meta property="og:title" content="Ignored"></body></html>"""

    assert page_metadata(html, "https://example.com/post/1") == {
        "title": "Card title",
        "description": "Plain summary",
        "siteName": "Example",
        "image": "https://example.com/img/card.png",
        "type": "",
    }


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254"])
def test_previews_never_reach_local_addresses(address: str) -> None:
    with pytest.raises(PreviewError, match="public web addresses"):
        link_previews.fetch("https://example.com/", "text/html", 100, resolver=resolver(address))


def test_previews_reject_other_schemes_and_ports() -> None:
    with pytest.raises(PreviewError):
        link_previews.fetch("file:///etc/passwd", "text/html", 100, resolver=resolver("1.1.1.1"))
    with pytest.raises(PreviewError, match="standard web ports"):
        link_previews.fetch(
            "https://example.com:8443/", "text/html", 100, resolver=resolver("1.1.1.1")
        )


def test_page_preview_inlines_a_small_thumbnail(monkeypatch) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    pages = {
        "https://example.com/a": (
            "https://example.com/a",
            "text/html",
            b'<head><meta property="og:title" content="A"><meta property="og:image" '
            b'content="https://cdn.example.com/a.png"></head>',
            False,
        ),
        "https://cdn.example.com/a.png": ("https://cdn.example.com/a.png", "image/png", png, False),
    }
    calls: list[str] = []

    def fake_fetch(url, _accept, _limit, **_kwargs):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(link_previews, "fetch", fake_fetch)
    previews = LinkPreviews()

    preview = previews.get("https://example.com/a")

    assert preview["title"] == "A"
    assert preview["kind"] == "page"
    assert preview["image"] == "data:image/png;base64," + base64.b64encode(png).decode()
    # A second request is served from memory.
    assert previews.get("https://example.com/a") == preview
    assert calls == ["https://example.com/a", "https://cdn.example.com/a.png"]


def test_oversized_or_non_image_thumbnails_are_left_out(monkeypatch) -> None:
    monkeypatch.setattr(
        link_previews, "fetch", lambda *_a, **_k: ("u", "image/svg+xml", b"<svg/>", False)
    )
    assert link_previews.thumbnail("https://example.com/a.svg") == ""
    monkeypatch.setattr(link_previews, "fetch", lambda *_a, **_k: ("u", "image/png", b"x", True))
    assert link_previews.thumbnail("https://example.com/big.png") == ""


def test_youtube_preview_uses_oembed_and_the_video_thumbnail(monkeypatch) -> None:
    def fake_fetch(url, _accept, _limit, **_kwargs):
        if url.startswith("https://www.youtube.com/oembed"):
            return url, "application/json", b'{"title":"Talk","author_name":"Chan"}', False
        assert url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        return url, "image/jpeg", b"jpg", False

    monkeypatch.setattr(link_previews, "fetch", fake_fetch)

    preview = link_previews.build_preview("https://youtu.be/dQw4w9WgXcQ")

    assert preview == {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "kind": "video",
        "title": "Talk",
        "description": "Chan",
        "siteName": "YouTube",
        "image": "data:image/jpeg;base64," + base64.b64encode(b"jpg").decode(),
    }
