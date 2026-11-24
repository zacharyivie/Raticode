"""Social posts on a paired phone: the review list and the user's decisions.

``posts.list`` sends the posts the Posts dashboard shows, newest decisions first, with
each platform's text, its "see more" fold, the flags and timing notes, and which
decisions each post takes now. ``posts.decide`` applies one decision through
``SocialService`` as the user, the same calls the dashboard makes, with ``via: phone`` in
the audit log. A phone with Use Raticode from this phone on has the user's trust, so its
tap is the user's approval; like every phone request it passes the phone guard first.

Approval stays pinned to what the phone was shown: the phone sends the post's
``content_hash`` from ``posts.list`` and a post that changed since is refused. Text longer
than ``TEXT_LIMIT`` is cut for the phone, and the phone can't show media or platform options
(a video or link-card title, LinkedIn visibility, a Discord thread), so such a post is
``unseen``: approving it needs ``unseen_confirmed``, sent after the phone asked a second
time. Rem, tasks and workflows can't reach any of this; they only propose.

The phone can't compose, edit, connect accounts or change settings. Account logins,
webhook addresses and media paths never leave the desktop.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gofer.devices.desktop_control import Refused

READS = frozenset({"posts.list"})
ACTIONS = frozenset({"posts.decide"})
DECISIONS = frozenset(
    {
        "approve",
        "approve_now",
        "decline",
        "request_changes",
        "cancel",
        "publish",
        "mark_posted",
        "pause",
        "resume",
    }
)
TEXT_LIMIT = 4000
MAX_POSTS = 60
REVIEWABLE = frozenset({"proposed", "changes_requested", "draft", "expired"})
PUBLISHABLE = frozenset({"due", "held", "failed", "missed", "outcome_unknown"})
MARKABLE = frozenset({"ready_to_post", "outcome_unknown", "missed", "failed", "due"})
FINISHED = frozenset({"published", "cancelled", "declined"})
# Lanes in the dashboard's order; the phone groups posts by these.
LANES = (
    ("review", {"proposed", "changes_requested", "draft", "expired"}),
    ("due", {"due"}),
    ("problems", {"failed", "missed", "needs_attention", "partly_published"}),
    ("scheduled", {"approved", "scheduled", "publishing"}),
    ("done", {"published", "cancelled", "declined"}),
)
_CODES = {"not_found": "not_found", "conflict": "conflict", "forbidden": "unauthorized"}


def _lane(state: str) -> str:
    return next((name for name, states in LANES if state in states), "done")


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


class PhonePosts:
    def __init__(self) -> None:
        self.service: Any = None

    def available(self) -> bool:
        return self.service is not None

    def _need(self) -> Any:
        if self.service is None:
            raise Refused("unavailable", "Posts isn't available on this computer.")
        return self.service

    def gather(self) -> dict[str, Any]:
        """The dashboard's data, read outside the device lock."""
        return {"snapshot": self._need().snapshot()}

    def read(self, data: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = data["snapshot"]
        posts = [post for post in snapshot["posts"] if post["state"] != "declined"]
        order = {name: index for index, (name, _) in enumerate(LANES)}
        posts.sort(key=lambda post: order[_lane(post["state"])])
        accounts = snapshot.get("accounts") or []
        return {
            "paused": bool(snapshot.get("paused")),
            "accounts": [
                {
                    "name": _text(account.get("name"), 120),
                    "platform": account.get("platform"),
                    "needs_reconnect": bool(account.get("needs_reconnect")),
                }
                for account in accounts[:40]
            ],
            "posts": [self._post(post) for post in posts[:MAX_POSTS]],
            "more": max(0, len(posts) - MAX_POSTS),
        }

    def _post(self, post: Mapping[str, Any]) -> dict[str, Any]:
        state = post["state"]
        review = post.get("review_state")
        approved = post.get("approval") is not None
        variants = []
        unseen = False
        for variant in post["variants"]:
            text = variant.get("text") or ""
            cut = len(text) > TEXT_LIMIT
            media = len(variant.get("media") or [])
            options = variant.get("options") or {}
            hidden = any(value not in (None, "") for value in options.values())
            unseen = unseen or cut or media > 0 or hidden
            vstate = variant["state"]
            actions = []
            if approved and variant.get("delivery") != "manual" and vstate in PUBLISHABLE:
                actions.append("publish")
            if approved and vstate in MARKABLE:
                actions.append("mark_posted")
            variants.append(
                {
                    "id": variant["id"],
                    "platform": variant["platform"],
                    "platform_label": variant.get("platform_label") or variant["platform"],
                    "account_name": _text(variant.get("account_name"), 120),
                    "state": vstate,
                    "delivery": variant.get("delivery"),
                    "text": text[:TEXT_LIMIT],
                    "text_cut": cut,
                    "link": _text(variant.get("link"), 2000),
                    "media_count": media,
                    "chars": variant.get("chars"),
                    "limit": variant.get("limit"),
                    "fold_at": variant.get("fold_at"),
                    "cost_usd": variant.get("cost_usd"),
                    "message": _text(variant.get("message"), 500),
                    "url": _text(variant.get("url"), 2000),
                    "open_url": variant.get("open_url") or "",
                    "actions": actions,
                }
            )
        actions = []
        if review in REVIEWABLE:
            actions += ["approve", "approve_now"]
        if review in {"proposed", "changes_requested", "draft"}:
            actions += ["request_changes", "decline"]
        if state not in FINISHED and (approved or review not in REVIEWABLE):
            actions.append("cancel")
        notes = post.get("notes") or []
        return {
            "id": post["id"],
            "title": _text(post.get("title"), 200),
            "state": state,
            "lane": _lane(state),
            "created_by": _text(post.get("created_by"), 100),
            "filled_in": bool(post.get("filled_in")),
            "scheduled_at": post.get("scheduled_at"),
            "timezone": post.get("timezone") or "UTC",
            "content_hash": post["content_hash"],
            "rationale": _text(post.get("rationale"), 1200),
            "sources": [
                {"title": _text(s.get("title"), 200), "url": _text(s.get("url"), 2000)}
                for s in (post.get("sources") or [])[:5]
            ],
            "flags": [_text(flag.get("message"), 300) for flag in post.get("flags") or []][:10],
            "timing": [_text(hint.get("message"), 300) for hint in post.get("timing") or []][:5],
            "notes": [
                {"by": _text(n.get("by"), 60), "text": _text(n.get("text"), 600)}
                for n in notes[-3:]
            ],
            "unseen": unseen,
            "variants": variants,
            "actions": actions,
        }

    def act(self, params: Mapping[str, Any]) -> dict[str, Any]:
        from gofer.social.service import SocialError

        service = self._need()
        decision = params.get("decision")
        if decision not in DECISIONS:
            raise Refused("invalid", "Unknown decision.")
        try:
            if decision in {"pause", "resume"}:
                service.set_paused("user", decision == "pause", via="phone")
                return self.read(self.gather())
            post_id = _text(params.get("post_id"), 64)
            shown = _text(params.get("content_hash"), 80)
            post = service.get(post_id)
            if decision in {"approve", "approve_now"}:
                view = self._post(post)
                if view["unseen"] and params.get("unseen_confirmed") is not True:
                    raise Refused(
                        "conflict",
                        "Part of this post isn't shown on the phone (long text, media or options). "
                        "Confirm to approve it anyway, or review it on the computer.",
                    )
                service.approve(
                    "user", post_id, shown, publish_now=decision == "approve_now", via="phone"
                )
            elif decision in {"decline", "request_changes", "cancel"}:
                if shown != post["content_hash"]:
                    raise Refused("conflict", "This post changed. Look at it again.")
                service.decide("user", post_id, decision, params.get("note") or "", via="phone")
            elif decision == "publish":
                service.publish_variant(
                    "user", post_id, _text(params.get("variant_id"), 64), shown, via="phone"
                )
            else:
                service.mark_posted(
                    "user",
                    post_id,
                    _text(params.get("variant_id"), 64),
                    params.get("url"),
                    via="phone",
                )
        except SocialError as exc:
            raise Refused(_CODES.get(exc.code, "invalid"), str(exc)) from None
        return self.read(self.gather())
