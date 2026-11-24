"""The publisher: sends approved posts at their time. It runs no model.

It runs inside whichever process owns the Rattish trigger scheduler (the app's backend,
or ``Raticode rattish triggers start`` from the login entry), started and stopped with
that ownership, so there is one publisher per data folder and nothing is sent twice.

Each pass:

- cancels posts the platform was holding when the user edited or cancelled them,
- marks unapproved posts whose time and late window passed as expired,
- hands far-off posts to platforms that schedule natively (Facebook, YouTube),
- turns tap-to-publish variants (LinkedIn) into ``due`` at their time,
- sends what is due, inside the late window, and marks the rest ``missed``,
- never sends a variant whose result is unknown again on its own.

A variant is marked ``publishing`` before its request leaves, so if the process dies
mid-send the next owner finds it and marks it ``outcome_unknown`` instead of resending.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import anyio

from gofer.core.http import HttpClient, UrllibHttpClient
from gofer.social.accounts import AccountStore, SocialAccountError
from gofer.social.models import RETRY_MINUTES
from gofer.social.policy import content_hash
from gofer.social.providers import adapter_for
from gofer.social.providers.base import Media, PublishError, PublishRequest
from gofer.social.service import SocialService, iso, parse_time

log = logging.getLogger(__name__)
ACTOR = "publisher"
POLL_SECONDS = 15.0
ACTIVE_POST_STATES = (
    "proposed",
    "changes_requested",
    "draft",
    "approved",
    "scheduled",
    "due",
    "publishing",
    "partly_published",
    "needs_attention",
    "failed",
    "cancelled",  # only to cancel copies a platform still holds
)
Notify = Callable[[str, Mapping[str, Any]], None]


def request_for(
    service: SocialService,
    post: Mapping[str, Any],
    variant: Mapping[str, Any],
    account: Mapping[str, Any],
    publish_at: datetime | None = None,
) -> PublishRequest:
    media = []
    for item in variant.get("media") or []:
        path = service.media_path(item["sha256"])
        if path is None:
            raise PublishError("permanent", f"The file {item['name']} is missing.")
        media.append(Media(path=path, mime=item["mime"], name=item["name"], size=item["size"]))
    return PublishRequest(
        text=variant["text"],
        link=variant.get("link") or "",
        media=tuple(media),
        options=dict(variant.get("options") or {}),
        account_options=dict(account.get("options") or {}),
        publish_at_unix=int(publish_at.timestamp()) if publish_at else None,
    )


class Publisher:
    def __init__(
        self,
        service: SocialService,
        *,
        http: HttpClient | None = None,
        notify: Notify | None = None,
    ) -> None:
        self.service = service
        self.http = http or UrllibHttpClient()
        self.notify = notify or (lambda kind, detail: None)
        self._in_flight: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()

    # Thread control ----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="social-publisher", daemon=True)
        self._thread.start()

    def stop(self, wait_seconds: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=wait_seconds)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.service.db_path.exists():
                    self.tick()
            except Exception:  # noqa: BLE001 - one bad pass must not stop publishing
                log.exception("Social publisher pass failed")
            self._stop.wait(POLL_SECONDS)

    # One pass -----------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> None:
        with self._tick_lock:
            moment = now or self.service.clock()
            paused = self.service.settings()["paused"]
            for post in self.service.posts_in(ACTIVE_POST_STATES):
                self._cancel_held(post)
                if post["state"] != "cancelled":
                    self._pass(post["id"], moment, paused)

    def _late(self, post: Mapping[str, Any], now: datetime) -> bool:
        when = parse_time(post.get("scheduled_at"))
        return when is not None and now > when + timedelta(minutes=post["late_window_minutes"])

    def _pass(self, post_id: str, now: datetime, paused: bool) -> None:
        post = self.service.get(post_id)
        if post["review_state"] in {"proposed", "changes_requested", "draft"}:
            if self._late(post, now):

                def expire(p: dict[str, Any]) -> None:
                    p["review_state"] = "expired"

                self.service.update_post(post_id, expire, ACTOR, "expired", {})
            return
        approval = post.get("approval") or {}
        if approval.get("content_hash") != content_hash(post):
            return  # never send anything but the approved bytes
        when = parse_time(post.get("scheduled_at"))
        for variant in post["variants"]:
            self._variant(post, variant, when, now, paused)

    def _set(self, post_id: str, variant_id: str, kind: str, **fields: Any) -> None:
        def change(post: dict[str, Any]) -> None:
            for item in post["variants"]:
                if item["id"] == variant_id:
                    item.update(fields)

        detail = {"variant": variant_id, **{k: v for k, v in fields.items() if k != "url"}}
        self.service.update_post(post_id, change, ACTOR, kind, detail)

    def _variant(
        self,
        post: Mapping[str, Any],
        variant: Mapping[str, Any],
        when: datetime | None,
        now: datetime,
        paused: bool,
    ) -> None:
        state = variant["state"]
        post_id, variant_id = post["id"], variant["id"]
        late = self._late(post, now)
        if state == "publishing" and variant_id not in self._in_flight:
            self._set(
                post_id,
                variant_id,
                "outcome_unknown",
                state="outcome_unknown",
                message="Raticode stopped while sending this, so it may have posted. "
                "Check the platform, then choose It posted or Send again.",
            )
            self.notify("problem", {"post": post_id})
            return
        if state == "held_by_platform":
            if when is not None and now >= when:
                self._set(post_id, variant_id, "published", state="published", message="")
            return
        tapped = bool(variant.get("publish_tap"))
        if state not in {"approved", "scheduled", "due"}:
            return
        due_now = when is None or now >= when
        if paused and not tapped:
            if due_now:
                self._set(
                    post_id,
                    variant_id,
                    "missed",
                    state="missed",
                    message="Publishing was paused at this time, so it was skipped.",
                )
            return
        retry_at = parse_time(variant.get("next_retry_at"))
        if retry_at is not None and now < retry_at:
            return
        if state == "scheduled" and not due_now and self._can_hold(variant, when, now):
            # Facebook and YouTube publish it themselves, even with this computer off.
            self._send(post, variant, now, publish_at=when)
            return
        if not due_now and not tapped:
            return
        if late and not tapped:
            message = (
                "Nobody tapped Publish before the late window ended."
                if state == "due"
                else "Its time passed while Raticode wasn't running."
                if variant.get("attempts", 0) == 0
                else "Retries ran past the late window."
            )
            final = "failed" if variant.get("attempts", 0) and state != "due" else "missed"
            self._set(post_id, variant_id, final, state=final, message=message)
            self.notify("problem", {"post": post_id})
            return
        if variant["delivery"] == "tap_to_publish" and not tapped:
            if state != "due":
                self._set(post_id, variant_id, "due", state="due", message="")
                self.notify("due", {"post": post_id, "variant": variant_id})
            return
        self._send(post, variant, now)

    def _claim(self, post: Mapping[str, Any], variant: Mapping[str, Any]) -> bool:
        """Mark the variant publishing, but only if the post is still what was approved.

        The request was built from ``post`` as read at the start of the pass. An edit,
        cancel or decline can land in between, so under the service's lock this checks
        that the saved post still carries an approval for exactly those bytes and that
        the variant hasn't moved on. Otherwise nothing is sent.
        """
        sent_hash = content_hash(post)
        claimed = False

        def change(fresh: dict[str, Any]) -> bool:
            nonlocal claimed
            approval = fresh.get("approval") or {}
            if approval.get("content_hash") != sent_hash or content_hash(fresh) != sent_hash:
                return False
            if fresh.get("state") == "cancelled" or fresh.get("state") not in ACTIVE_POST_STATES:
                return False
            for item in fresh["variants"]:
                if item["id"] == variant["id"]:
                    if item["state"] != variant["state"]:
                        return False
                    item.update(state="publishing", attempts=int(item.get("attempts", 0)) + 1)
                    claimed = True
                    return True
            return False

        detail = {"variant": variant["id"], "state": "publishing"}
        self.service.update_post(post["id"], change, ACTOR, "publishing", detail)
        if not claimed:
            log.info("Social post %s changed before sending; not sent", post["id"])
        return claimed

    def _can_hold(self, variant: Mapping[str, Any], when: datetime | None, now: datetime) -> bool:
        if when is None or variant.get("remote_id"):
            return False
        adapter = adapter_for(variant["platform"])
        if adapter is None or not adapter.capabilities.native_scheduling:
            return False
        lead = (when - now).total_seconds()
        caps = adapter.capabilities
        return caps.min_native_lead_seconds <= lead <= caps.max_native_lead_seconds

    def _account(self, variant: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            return self.service.accounts.get(variant["account_id"])
        except (SocialAccountError, TypeError):
            return None

    def _send(
        self,
        post: Mapping[str, Any],
        variant: Mapping[str, Any],
        now: datetime,
        *,
        publish_at: datetime | None = None,
    ) -> None:
        post_id, variant_id = post["id"], variant["id"]
        adapter = adapter_for(variant["platform"])
        account = self._account(variant)
        if adapter is None or account is None:
            self._set(
                post_id, variant_id, "failed", state="failed", message="The account was removed."
            )
            return
        status = AccountStore.status(account, now)
        if status == "disabled":
            self._set(
                post_id, variant_id, "held", state="held", message=f"{account['name']} is off."
            )
            return
        if status == "needs_reconnect":
            self._set(
                post_id,
                variant_id,
                "needs_reconnect",
                state="needs_reconnect",
                message=f"Reconnect {account['name']} to send this.",
            )
            return
        if publish_at is None:
            day = self.service.sends_since(account["id"], now - timedelta(days=1))
            month = self.service.sends_since(account["id"], now - timedelta(days=30))
            if day >= account["daily_cap"] or month >= account["monthly_cap"]:
                self._set(
                    post_id,
                    variant_id,
                    "held",
                    state="held",
                    message=f"{account['name']} reached its daily or monthly post limit.",
                )
                self.notify("problem", {"post": post_id})
                return
        try:
            secrets = self.service.accounts.read_secrets(account["id"])
            request = request_for(self.service, post, variant, account, publish_at)
        except (SocialAccountError, PublishError) as exc:
            self._set(post_id, variant_id, "failed", state="failed", message=str(exc))
            return
        trigger = "user_tap" if variant.get("publish_tap") else "schedule"
        self._in_flight.add(variant_id)
        try:
            if not self._claim(post, variant):
                return
            attempt = self.service.record_attempt_start(post_id, variant, trigger)

            def save_secrets(values: dict[str, str]) -> None:
                self.service.accounts.write_secrets(account["id"], values)

            async def run() -> Any:
                return await adapter.publish(request, secrets, self.http, save_secrets)

            try:
                result = anyio.run(run)
            except PublishError as exc:
                self.service.record_attempt_end(
                    attempt, status=exc.status, error_class=exc.kind, message=exc.message
                )
                self._failed(post, variant, account, exc, now)
                return
            except Exception as exc:  # noqa: BLE001 - an adapter bug: don't guess, don't resend
                log.exception("Social adapter %s failed", variant["platform"])
                self.service.record_attempt_end(
                    attempt, status=None, error_class="outcome_unknown", message=type(exc).__name__
                )
                self._set(
                    post_id,
                    variant_id,
                    "outcome_unknown",
                    state="outcome_unknown",
                    message="Something went wrong while sending, so it may have posted.",
                )
                return
            self.service.record_attempt_end(attempt, status=200, error_class=None, message="")
            held = publish_at is not None
            self._set(
                post_id,
                variant_id,
                "held_by_platform" if held else "published",
                state="held_by_platform" if held else "published",
                remote_id=result.remote_id,
                url=result.url,
                message="",
                next_retry_at=None,
                publish_tap=None,
            )
            if not held:
                self.service.accounts.mark(account["id"], last_posted_at=iso(now))
        finally:
            self._in_flight.discard(variant_id)

    def _failed(
        self,
        post: Mapping[str, Any],
        variant: Mapping[str, Any],
        account: Mapping[str, Any],
        error: PublishError,
        now: datetime,
    ) -> None:
        post_id, variant_id = post["id"], variant["id"]
        if error.kind == "outcome_unknown":
            self._set(
                post_id,
                variant_id,
                "outcome_unknown",
                state="outcome_unknown",
                message=error.message,
            )
        elif error.kind == "auth":
            if error.disable_account:
                self.service.accounts.mark(account["id"], enabled=False)
            else:
                self.service.accounts.mark(account["id"], needs_reconnect=True)
            self._set(
                post_id,
                variant_id,
                "needs_reconnect",
                state="needs_reconnect",
                message=error.message,
            )
        elif error.kind == "cap":
            self._set(post_id, variant_id, "held", state="held", message=error.message)
        elif error.kind == "transient":
            attempts = int(variant.get("attempts", 0)) + 1
            minutes = RETRY_MINUTES[min(attempts - 1, len(RETRY_MINUTES) - 1)]
            wait = timedelta(minutes=minutes)
            if error.retry_after_seconds is not None:
                wait = max(wait, timedelta(seconds=error.retry_after_seconds))
            retry_at = now + wait
            when = parse_time(post.get("scheduled_at")) or now
            limit = when + timedelta(minutes=post["late_window_minutes"])
            if variant.get("publish_tap") or retry_at <= limit:
                self._set(
                    post_id,
                    variant_id,
                    "retry_scheduled",
                    state="scheduled" if not variant.get("publish_tap") else "approved",
                    next_retry_at=iso(retry_at),
                    message=f"{error.message} Trying again at {iso(retry_at)}.",
                )
                return
            self._set(post_id, variant_id, "failed", state="failed", message=error.message)
        else:
            self._set(post_id, variant_id, "failed", state="failed", message=error.message)
        self.notify("problem", {"post": post_id})

    def _cancel_held(self, post: Mapping[str, Any]) -> None:
        pending = list(post.get("_cancel_native") or [])
        if not pending:
            return
        remaining = []
        for item in pending:
            adapter = adapter_for(item["platform"])
            account = self._account(item)
            if adapter is None or account is None:
                continue
            try:
                secrets = self.service.accounts.read_secrets(account["id"])

                def save_secrets(values: dict[str, str], account_id: str = account["id"]) -> None:
                    self.service.accounts.write_secrets(account_id, values)

                async def run(remote_id: str = item["remote_id"]) -> None:
                    await adapter.cancel_native(remote_id, secrets, self.http, save_secrets)

                anyio.run(run)
            except (PublishError, SocialAccountError):
                remaining.append(item)

        def change(p: dict[str, Any]) -> None:
            p["_cancel_native"] = remaining

        self.service.update_post(
            post["id"],
            change,
            ACTOR,
            "platform_copy_cancelled",
            {"cancelled": len(pending) - len(remaining), "left": len(remaining)},
        )
