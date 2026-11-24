"""One adapter per platform. Manual platforms (Patreon, Buy Me a Coffee, TikTok) have none."""

from __future__ import annotations

from gofer.social.providers.base import Adapter
from gofer.social.providers.discord import DiscordAdapter
from gofer.social.providers.facebook import FacebookAdapter
from gofer.social.providers.linkedin import LinkedInAdapter
from gofer.social.providers.x import XAdapter
from gofer.social.providers.youtube import YouTubeAdapter

ADAPTERS: dict[str, Adapter] = {
    "discord": DiscordAdapter(),
    "x": XAdapter(),
    "linkedin": LinkedInAdapter(),
    "facebook": FacebookAdapter(),
    "youtube": YouTubeAdapter(),
}


def adapter_for(platform: str) -> Adapter | None:
    return ADAPTERS.get(platform)
