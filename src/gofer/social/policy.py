"""Content checks shown on each card, and the hash an approval is pinned to.

Nothing here decides to publish. The flags tell the user what to look at before they
approve: links outside their allowlist, punycode or look-alike domains, @mentions,
hashtags they haven't used before, and invisible or direction-changing characters,
which are removed from the text before it is stored.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

# Zero-width, bidi override/isolate and other invisible format characters. Ordinary
# newlines and tabs stay.
_INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁯ㅤ︀-️﻿ﾠ\U000e0000-\U000e007f]")
_URL = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.IGNORECASE)
_BARE_DOMAIN = re.compile(
    r"(?<![@\w.])((?:[a-z0-9-]+\.)+(?:com|dev|io|net|org|app|ai|co|xyz|gg|me|ly|link|info))"
    r"(?:/[^\s]*)?\b",
    re.IGNORECASE,
)
_MENTION = re.compile(r"(?<![\w@])@[\w.-]{1,64}")
_HASHTAG = re.compile(r"(?<![\w#])#[^\s#.,;:!?()\[\]{}]{1,100}")


def clean_text(text: str) -> tuple[str, bool]:
    """The text with invisible and direction-changing characters removed, and whether any were."""
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = _INVISIBLE.sub("", normalized)
    return cleaned, cleaned != normalized


def links_in(text: str) -> list[str]:
    seen: list[str] = []
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _allowed(host: str, allowlist: Iterable[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in allowlist)


def _skeleton(host: str) -> str:
    """A rough confusable skeleton, so raticode.dev and rat1code.dev compare equal."""
    table = str.maketrans({"0": "o", "1": "l", "i": "l", "3": "e", "5": "s"})
    folded = unicodedata.normalize("NFKD", host)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return folded.replace("rn", "m").translate(table)


def flags_for(
    texts: Iterable[str],
    links: Iterable[str],
    *,
    allowlist: Iterable[str],
    known_hashtags: Iterable[str] = (),
    stripped_invisible: bool = False,
    external_sources: bool = False,
) -> list[dict[str, str]]:
    """Warnings for a card. Each has a ``kind`` and a plain ``message``."""
    allow = [item.lower().strip(".") for item in allowlist if item]
    known = {tag.lower() for tag in known_hashtags}
    flags: list[dict[str, str]] = []
    all_text = "\n".join(texts)
    urls = list(dict.fromkeys([*links, *links_in(all_text)]))
    for url in urls:
        host = _host(url)
        if not host:
            flags.append({"kind": "link", "message": f"This link can't be read: {url}"})
            continue
        if host.startswith("xn--") or ".xn--" in host or not host.isascii():
            flags.append({"kind": "lookalike", "message": f"{host} uses international characters."})
        if _allowed(host, allow):
            continue
        lookalike = next(
            (item for item in allow if _skeleton(host) == _skeleton(item) and host != item),
            None,
        )
        if lookalike:
            flags.append(
                {"kind": "lookalike", "message": f"{host} looks like {lookalike} but isn't."}
            )
        flags.append({"kind": "link", "message": f"Links to {host}, which isn't on your list."})
    for match in _BARE_DOMAIN.finditer(all_text):
        host = match.group(1).lower()
        if any(_host(url) == host for url in urls) or _allowed(host, allow):
            continue
        flags.append({"kind": "link", "message": f"Mentions {host}, which isn't on your list."})
    mentions = sorted({match.group(0) for match in _MENTION.finditer(all_text)})
    if mentions:
        flags.append({"kind": "mention", "message": "Mentions " + ", ".join(mentions[:10])})
    new_tags = sorted(
        {
            match.group(0)
            for match in _HASHTAG.finditer(all_text)
            if match.group(0).lower() not in known
        }
    )
    if new_tags:
        flags.append({"kind": "hashtag", "message": "New hashtags: " + ", ".join(new_tags[:10])})
    if stripped_invisible:
        flags.append(
            {
                "kind": "invisible",
                "message": "Invisible or direction-changing characters were removed.",
            }
        )
    if external_sources:
        flags.append(
            {
                "kind": "external",
                "message": "Drafted from outside pages; check it says what you mean.",
            }
        )
    unique: list[dict[str, str]] = []
    for flag in flags:
        if flag not in unique:
            unique.append(flag)
    return unique


def content_hash(post: Mapping[str, Any]) -> str:
    """SHA-256 of everything an approval covers: time, late window and every variant."""
    variants = sorted(
        (
            {
                "account_id": variant.get("account_id"),
                "platform": variant.get("platform"),
                "text": variant.get("text", ""),
                "link": variant.get("link") or "",
                "media": [item.get("sha256") for item in variant.get("media") or []],
                "options": variant.get("options") or {},
                "delivery": variant.get("delivery"),
            }
            for variant in post.get("variants") or []
        ),
        key=lambda item: (str(item["account_id"]), str(item["platform"])),
    )
    payload = {
        "scheduled_at": post.get("scheduled_at"),
        "late_window_minutes": post.get("late_window_minutes"),
        "variants": variants,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
