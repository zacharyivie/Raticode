"""Post states, platforms and limits shared by the store, the publisher and the callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# A post's own states while it is reviewed. After approval a post's state comes from its
# variants (``derive_post_state``).
REVIEW_STATES = frozenset({"draft", "proposed", "changes_requested", "declined", "expired"})

# A variant is one post on one account.
VariantState = Literal[
    "pending",  # not approved yet
    "approved",  # approved for now; the publisher sends it on its next pass
    "scheduled",  # approved for later
    "held_by_platform",  # handed to the platform's own scheduler
    "due",  # tap to publish: waiting for the user's Publish tap
    "publishing",
    "published",
    "failed",
    "missed",
    "needs_reconnect",
    "outcome_unknown",
    "held",  # a cap was reached; waits for the user
    "ready_to_post",  # manual platforms: copy, paste, then mark posted
    "posted_by_you",
    "cancelled",
]
DONE_VARIANT_STATES = frozenset({"published", "posted_by_you", "cancelled"})
PROBLEM_VARIANT_STATES = frozenset(
    {"failed", "missed", "needs_reconnect", "outcome_unknown", "held"}
)

DeliveryMode = Literal["api", "platform_held", "tap_to_publish", "manual"]

DEFAULT_LATE_WINDOW_MINUTES = 120
MAX_LATE_WINDOW_MINUTES = 7 * 24 * 60
MAX_TITLE = 200
MAX_TEXT = 70_000
MAX_VARIANTS = 12
MAX_SOURCES = 20
MAX_TEMPLATES = 100
MAX_TEMPLATE_NAME = 80
MAX_RATIONALE = 4000
RETRY_MINUTES = (1, 5, 15, 60)

# Limits on what one proposer can put in front of the user.
MAX_OPEN_PROPOSALS_PER_SOURCE = 10
MAX_PROPOSALS_PER_SOURCE_PER_DAY = 20

DEFAULT_LINK_ALLOWLIST = ("raticode.dev", "github.com")


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    api: bool
    text_limit: int
    default_delivery: str
    native_scheduling: bool = False
    needs_video: bool = False
    title_limit: int = 0
    open_url: str = ""
    note: str = ""
    # Where the feed cuts the text with "see more": about this many characters or lines,
    # whichever comes first. 0 shows the whole text. Approximate and shown as "about".
    fold_chars: int = 0
    fold_lines: int = 0
    # The platform turns a link into a preview card.
    link_card: bool = False


PLATFORMS: dict[str, Platform] = {
    item.id: item
    for item in (
        Platform(
            "discord",
            "Discord",
            True,
            2000,
            "api",
            note="Posts through a channel webhook. Mentions are off unless you allow them.",
            link_card=True,
        ),
        Platform("x", "X", True, 280, "api", note="Pay per post: links cost more.", link_card=True),
        Platform(
            "linkedin",
            "LinkedIn",
            True,
            3000,
            "tap_to_publish",
            note="At its time you tap Publish. LinkedIn's API Terms bar automated posting.",
            fold_chars=210,
            fold_lines=3,
            link_card=True,
        ),
        Platform(
            "facebook",
            "Facebook Page",
            True,
            63206,
            "api",
            native_scheduling=True,
            note="Posts to a Page you manage. Can be handed to Facebook to publish on time.",
            fold_chars=480,
            fold_lines=5,
            link_card=True,
        ),
        Platform(
            "youtube",
            "YouTube",
            True,
            5000,
            "api",
            native_scheduling=True,
            needs_video=True,
            title_limit=100,
            note="Video uploads only. The post text is the video description.",
            fold_chars=150,
            fold_lines=3,
        ),
        Platform(
            "patreon",
            "Patreon",
            False,
            70_000,
            "manual",
            open_url="https://www.patreon.com/posts/new",
            note="No posting API: copy the draft and paste it.",
        ),
        Platform(
            "buymeacoffee",
            "Buy Me a Coffee",
            False,
            70_000,
            "manual",
            open_url="https://www.buymeacoffee.com/dashboard",
            note="No posting API: copy the draft and paste it.",
        ),
        Platform(
            "tiktok",
            "TikTok",
            False,
            2200,
            "manual",
            open_url="https://www.tiktok.com/upload",
            note="Public posts need TikTok's audit, so post it yourself.",
        ),
    )
}


def derive_post_state(review_state: str, variant_states: list[VariantState]) -> str:
    """A post's state from its variants once approved; its review state before that."""
    if review_state in REVIEW_STATES or not variant_states:
        return review_state
    states = set(variant_states)
    if states <= {"cancelled"}:
        return "cancelled"
    live = states - {"cancelled"}
    if "publishing" in live:
        return "publishing"
    if live & PROBLEM_VARIANT_STATES:
        if live & {"published", "posted_by_you"}:
            return "partly_published"
        if live <= {"missed"}:
            return "missed"
        if live <= {"failed"}:
            return "failed"
        return "needs_attention"
    if "due" in live:
        return "due"
    if live <= {"published", "posted_by_you"}:
        return "published"
    if live & {"approved"}:
        return "approved"
    if live & {"scheduled", "held_by_platform", "ready_to_post"}:
        if live & {"published", "posted_by_you"}:
            return "partly_published"
        return "scheduled"
    return "approved"


def fold_at(platform: str, text: str) -> int | None:
    """Where the feed cuts ``text`` with "see more", or None when it shows all of it."""
    item = PLATFORMS.get(platform)
    if item is None or not item.fold_chars:
        return None
    cuts = []
    if len(text) > item.fold_chars:
        cuts.append(item.fold_chars)
    if item.fold_lines:
        lines = text.rstrip("\n").split("\n")
        if len(lines) > item.fold_lines:
            cuts.append(len("\n".join(lines[: item.fold_lines])))
    return min(cuts) if cuts else None
