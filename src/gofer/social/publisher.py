"""The publisher: sends approved posts at their time. It runs no model.

It runs inside whichever process owns the Rattish trigger scheduler (the app's backend,
or ``Raticode rattish triggers start`` from the login entry), started and stopped with
that ownership, so there is one publisher per data folder and nothing is sent twice.

Each pass:

- deletes copies a platform was holding when the user edited or cancelled the post,
  backing off on errors and stopping for the user on a refusal or a login problem,
- marks unapproved posts whose time and late window passed as expired,
- hands far-off posts to platforms that schedule natively (Facebook, YouTube),
- turns tap-to-publish variants (LinkedIn) into ``due`` at their time,
- sends what is due, inside the late window, and marks the rest ``missed``,
- holds back a variant while an older copy on its account still waits to be deleted,
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
from gofer.social.models import PLATFORMS, RETRY_MINUTES
from gofer.social.policy import content_hash
from gofer.social.providers import adapter_for
from gofer.social.providers.base import Media, PublishError, PublishRequest
from gofer.social.service import SocialService, copy_key, iso, parse_time
from gofer.utils.logging import RepeatingFailure

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
# The only variants an unexpected error shows "will try again" on: the ones the next pass
# sends. A variant being sent, a finished one, or one waiting for the user (failed,
# missed, held, needs_reconnect, outcome_unknown) keeps its own message.
RETRIED_VARIANT_STATES = frozenset({"approved", "scheduled", "due"})
POST_ERROR_PREFIX = "Raticode hit an error with this post ("
Notify = Callable[[str, Mapping[str, Any]], None]
OLD_COPY_MESSAGE = (
    "Waiting for the earlier copy on {platform} to be deleted, so two copies can't go out."
)


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
        # Keyed "pass:<post id>" or "cancel:<post id>", so each logs once while it repeats.
        self._failures: dict[str, RepeatingFailure] = {}

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
            seen: set[str] = set()
            for post in self.service.posts_in(ACTIVE_POST_STATES):
                seen.add(post["id"])
                try:
                    self._cancel_held(post, moment)
                    if post["state"] != "cancelled":
                        self._pass(post["id"], moment, paused)
                except Exception as exc:  # noqa: BLE001 - one post must not stop the others
                    self._post_failed(post["id"], exc)
                else:
                    self._post_recovered(post["id"])
            # Copies left on posts the loop above skips (declined, expired, missed).
            for post in self.service.posts_with_platform_copies(ACTIVE_POST_STATES):
                seen.add(post["id"])
                try:
                    self._cancel_held(post, moment)
                except Exception as exc:  # noqa: BLE001 - one post must not stop the others
                    self._post_failed(post["id"], exc)
                else:
                    self._post_recovered(post["id"])
            for key in [k for k in self._failures if k.split(":", 1)[1] not in seen]:
                del self._failures[key]

    def _failure(self, key: str, what: str) -> RepeatingFailure:
        failure = self._failures.get(key)
        if failure is None:
            failure = self._failures[key] = RepeatingFailure(log, what)
        return failure

    def _post_failed(self, post_id: str, exc: Exception) -> None:
        """Log an unexpected error once while it repeats, and tell the user once.

        Only variants the next pass tries again get the message. Variants in
        ``publishing`` are left alone: a send may be on its way, and the next pass's
        outcome-unknown rule must still find them as they are.
        """
        key = f"pass:{post_id}"
        first = key not in self._failures
        failure = self._failure(key, f"Social post {post_id}")
        before = failure.current
        failure.failed(exc)
        if failure.current == before:
            return  # the same error again: already logged, shown and notified
        message = f"{POST_ERROR_PREFIX}{type(exc).__name__}) and will try again."

        def show(post: dict[str, Any]) -> bool:
            changed = False
            for item in post["variants"]:
                if item["state"] not in RETRIED_VARIANT_STATES or item.get("message") == message:
                    continue
                item["message"] = message
                changed = True
            return changed

        try:
            self.service.update_post(post_id, show, ACTOR, "error", {"error": type(exc).__name__})
        except Exception:  # noqa: BLE001 - the error is logged; showing it is best effort
            log.debug("Couldn't show the error on social post %s", post_id, exc_info=True)
        if first:
            self.notify("problem", {"post": post_id})

    def _post_recovered(self, post_id: str) -> None:
        failure = self._failures.pop(f"pass:{post_id}", None)
        if failure is None:
            return
        failure.succeeded()

        def clear(post: dict[str, Any]) -> bool:
            changed = False
            for item in post["variants"]:
                if str(item.get("message") or "").startswith(POST_ERROR_PREFIX):
                    item["message"] = ""
                    changed = True
            return changed

        try:
            self.service.update_post(post_id, clear, ACTOR, "error_cleared", {})
        except Exception:  # noqa: BLE001 - a stale message must not stop the other posts
            log.debug("Couldn't clear the error on social post %s", post_id, exc_info=True)

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
            if not self._waits_for_old_copy(post, variant):
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
        if not self._waits_for_old_copy(post, variant):
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

    def _cancel_held(self, post: Mapping[str, Any], now: datetime) -> None:
        """Delete copies a platform holds for an edited or cancelled post.

        Each copy keeps its attempts, next try and last error. Network, server and rate
        limit errors back off on ``RETRY_MINUTES``; a refusal, a login problem or a removed
        account stops and waits for the user's Try again or I deleted it. The post is
        saved and logged only when a copy was deleted or its record changed.
        """
        pending = list(post.get("_cancel_native") or [])
        deleted: set[str] = set()
        changed: dict[str, dict[str, Any]] = {}
        stopped = False
        bug: Exception | None = None
        for item in pending:
            key = copy_key(item)
            retry_at = parse_time(item.get("next_try_at"))
            if item.get("waiting") or (retry_at is not None and now < retry_at):
                continue
            try:
                outcome = self._delete_copy(item)
            except Exception as exc:  # noqa: BLE001 - an adapter bug: don't guess, keep it
                bug = exc
                continue
            if outcome is None:
                deleted.add(key)
                continue
            attempts = int(item.get("attempts") or 0) + 1
            update: dict[str, Any] = {"id": key, "attempts": attempts, "error": outcome.message}
            if outcome.kind in {"transient", "outcome_unknown"}:
                wait = timedelta(minutes=RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1])
                if outcome.retry_after_seconds is not None:
                    wait = max(wait, timedelta(seconds=outcome.retry_after_seconds))
                update.update(next_try_at=iso(now + wait), waiting=False)
            else:
                update.update(next_try_at=None, waiting=True)
                stopped = True
            if any(item.get(name) != value for name, value in update.items()):
                changed[key] = update
        failure_key = f"cancel:{post['id']}"
        if bug is not None:
            what = f"Cancelling social post {post['id']} on its platform"
            self._failure(failure_key, what).failed(bug)
        elif (fixed := self._failures.pop(failure_key, None)) is not None:
            fixed.succeeded()
        if not deleted and not changed:
            return  # nothing changed, so nothing to save or log

        def change(p: dict[str, Any]) -> bool:
            kept, touched = [], False
            for item in p.get("_cancel_native") or []:
                key = copy_key(item)
                touched = touched or key in deleted or key in changed
                if key in deleted:
                    continue
                item.update(changed.get(key, {}))
                kept.append(item)
            if not touched:
                return False  # the user dealt with these copies in the meantime
            p["_cancel_native"] = kept
            return True

        detail = {"cancelled": len(deleted), "failed": len(changed)}
        kind = "platform_copy_cancelled" if deleted else "platform_copy_not_deleted"
        self.service.update_post(post["id"], change, ACTOR, kind, detail)
        if stopped:
            self.notify("problem", {"post": post["id"]})

    def _delete_copy(self, item: Mapping[str, Any]) -> PublishError | None:
        """Ask the platform to delete one held copy: None when it's gone, else why not."""
        adapter = adapter_for(item["platform"])
        account = self._account(item)
        label = PLATFORMS[item["platform"]].label if item["platform"] in PLATFORMS else "it"
        if adapter is None or account is None:
            return PublishError(
                "permanent",
                f"The account was removed, so Raticode can't delete this copy. Delete it on "
                f"{label} yourself, then choose I deleted it.",
            )
        try:
            secrets = self.service.accounts.read_secrets(account["id"])

            def save_secrets(values: dict[str, str]) -> None:
                self.service.accounts.write_secrets(account["id"], values)

            async def run() -> None:
                await adapter.cancel_native(item["remote_id"], secrets, self.http, save_secrets)

            anyio.run(run)
        except PublishError as exc:
            if exc.kind == "auth":
                self.service.accounts.mark(account["id"], needs_reconnect=True)
            return exc
        except SocialAccountError as exc:
            return PublishError("auth", str(exc))
        return None

    def _waits_for_old_copy(self, post: Mapping[str, Any], variant: Mapping[str, Any]) -> bool:
        """Whether an older copy of this post on the same account still waits for deletion.

        Sending now could put two copies out, so the variant waits until that copy is
        deleted or the user marks it deleted. The variant's message says so, set once.
        """
        waiting = any(
            item.get("account_id") == variant.get("account_id")
            and item.get("platform") == variant["platform"]
            for item in post.get("platform_copies") or []
        )
        message = OLD_COPY_MESSAGE.format(platform=PLATFORMS[variant["platform"]].label)
        if waiting and variant.get("message") != message:
            self._set(post["id"], variant["id"], "waiting_for_old_copy", message=message)
        elif not waiting and variant.get("message") == message:
            self._set(post["id"], variant["id"], "old_copy_gone", message="")
        return waiting
