"""Posts: proposals, edits, the user's approval and the hash-linked audit log.

Every caller (HTTP routes, the CLI, Rem's ``social`` tools, the publisher) goes through
``SocialService``. Actor strings decide what a caller may do:

- ``"user"``: the desktop window's own session. Only this actor approves, taps Publish,
  sends a post again, marks one posted or cancels it.
- ``"rem"``, ``"task:<id>"``, ``"workflow:<id>@<rev>"``, ``"cli"``: proposers. They can
  propose, revise their own proposals before approval, and read.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.social.accounts import AccountStore, SecretStore
from gofer.social.models import (
    DEFAULT_LATE_WINDOW_MINUTES,
    DEFAULT_LINK_ALLOWLIST,
    DONE_VARIANT_STATES,
    MAX_LATE_WINDOW_MINUTES,
    MAX_OPEN_PROPOSALS_PER_SOURCE,
    MAX_PROPOSALS_PER_SOURCE_PER_DAY,
    MAX_RATIONALE,
    MAX_SOURCES,
    MAX_TEMPLATES,
    MAX_TEXT,
    MAX_TITLE,
    MAX_VARIANTS,
    PLATFORMS,
    PROBLEM_VARIANT_STATES,
    REVIEW_STATES,
    derive_post_state,
    fold_at,
)
from gofer.social.policy import clean_text, content_hash, flags_for
from gofer.social.templates import STARTERS, TemplateError
from gofer.utils.paths import get_data_dir

SOCIAL_FOLDER = "social"
DB_FILE = "posts.db"
MEDIA_FOLDER = "media"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 256 * 1024 * 1024
MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x1aE\xdf\xa3", "video/webm"),
)
USER = "user"


class SocialError(ValueError):
    """A safe error for callers. ``code`` lets the API pick a status."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SocialError("Times are ISO 8601 strings, such as 2026-11-18T17:00:00Z.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SocialError("Times are ISO 8601 strings, such as 2026-11-18T17:00:00Z.") from None
    if parsed.tzinfo is None:
        raise SocialError("Give the time with its offset or Z for UTC.")
    return parsed.astimezone(UTC)


def is_proposer(actor: str) -> bool:
    return actor != USER


def social_root(data_dir: Path | None = None) -> Path:
    return (data_dir or get_data_dir()).expanduser().resolve() / SOCIAL_FOLDER


class SocialService:
    """The posts of one data folder."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        secrets: SecretStore | None = None,
        clock: Any = utc_now,
        media_roots: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        self.root = social_root(data_dir)
        # Folders a proposer (Rem, a task, a workflow, the CLI) may attach media from:
        # the projects open in Raticode. Without it only the user attaches files.
        self.media_roots = media_roots
        self.accounts = AccountStore(self.root, secrets=secrets)
        self._sign_ins: Any = None
        self.clock = clock
        self._lock = threading.RLock()
        self._prepared = False

    @property
    def sign_ins(self) -> Any:
        """Browser sign-ins through a loopback listener (gofer.social.signin)."""
        if self._sign_ins is None:
            from gofer.social.signin import SignInManager

            self._sign_ins = SignInManager(self)
        return self._sign_ins

    # Storage -------------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.root / DB_FILE

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self._prepare()
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _prepare(self) -> None:
        if self._prepared:
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, at TEXT NOT NULL,
                    actor TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
                    prev_hash TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL, account_id TEXT, started_at TEXT NOT NULL,
                    ended_at TEXT, status INTEGER, error_class TEXT, message TEXT,
                    trigger TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                """
            )
        finally:
            db.close()
        self.db_path.chmod(0o600)
        self._prepared = True

    def _event(
        self,
        db: sqlite3.Connection,
        post_id: str | None,
        actor: str,
        kind: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        row = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row["hash"] if row else "0" * 64
        at = iso(self.clock())
        encoded = json.dumps(dict(detail or {}), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(
            "\x1f".join([previous, post_id or "", at, actor, kind, encoded]).encode()
        ).hexdigest()
        db.execute(
            "INSERT INTO events (post_id, at, actor, kind, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, at, actor, kind, encoded, previous, digest),
        )

    def verify_log(self) -> bool:
        """Whether the audit log's hash chain is intact."""
        with self.connect() as db:
            previous = "0" * 64
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                digest = hashlib.sha256(
                    "\x1f".join(
                        [
                            previous,
                            row["post_id"] or "",
                            row["at"],
                            row["actor"],
                            row["kind"],
                            row["detail"],
                        ]
                    ).encode()
                ).hexdigest()
                if row["prev_hash"] != previous or row["hash"] != digest:
                    return False
                previous = digest
        return True

    def _load(self, db: sqlite3.Connection, post_id: str) -> dict[str, Any]:
        row = db.execute("SELECT body FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise SocialError("No such post.", "not_found")
        post: dict[str, Any] = json.loads(row["body"])
        return post

    def _save(self, db: sqlite3.Connection, post: dict[str, Any]) -> None:
        post["state"] = derive_post_state(
            post["review_state"], [variant["state"] for variant in post["variants"]]
        )
        post["updated_at"] = iso(self.clock())
        db.execute(
            "INSERT INTO posts (id, state, created_by, created_at, updated_at, body) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
            "updated_at=excluded.updated_at, body=excluded.body",
            (
                post["id"],
                post["state"],
                post["created_by"],
                post["created_at"],
                post["updated_at"],
                json.dumps(post, ensure_ascii=False),
            ),
        )

    # Settings ------------------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        with self.connect() as db:
            return self._settings(db)

    def _settings(self, db: sqlite3.Connection) -> dict[str, Any]:
        values = {
            row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")
        }
        allow = values.get("link_allowlist")
        late = values.get("late_window_minutes")
        return {
            "link_allowlist": allow if isinstance(allow, list) else list(DEFAULT_LINK_ALLOWLIST),
            "late_window_minutes": late
            if isinstance(late, int) and 0 < late <= MAX_LATE_WINDOW_MINUTES
            else DEFAULT_LATE_WINDOW_MINUTES,
            "paused": values.get("paused") is True,
            "paused_at": values.get("paused_at"),
            "known_hashtags": values.get("known_hashtags")
            if isinstance(values.get("known_hashtags"), list)
            else [],
        }

    def _set(self, db: sqlite3.Connection, key: str, value: Any) -> None:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def update_settings(self, actor: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._require_user(actor, "change Posts settings")
        with self.connect() as db:
            if "link_allowlist" in body:
                items = body["link_allowlist"]
                if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                    raise SocialError("The link list is a list of domains.")
                cleaned = sorted({i.strip().lower().strip(".") for i in items if i.strip()})
                self._set(db, "link_allowlist", cleaned[:100])
            if "late_window_minutes" in body:
                value = body["late_window_minutes"]
                if not isinstance(value, int) or not 1 <= value <= MAX_LATE_WINDOW_MINUTES:
                    raise SocialError("The late window is 1 minute to 7 days.")
                self._set(db, "late_window_minutes", value)
            self._event(db, None, actor, "settings.changed", {"fields": sorted(body)})
            return self._settings(db)

    def set_paused(self, actor: str, paused: bool, *, via: str = "") -> dict[str, Any]:
        """Pause all publishing. Times that pass while paused are skipped, never caught up."""
        self._require_user(actor, "pause or resume publishing")
        with self.connect() as db:
            self._set(db, "paused", paused)
            self._set(db, "paused_at", iso(self.clock()) if paused else None)
            self._event(
                db,
                None,
                actor,
                "publishing.paused" if paused else "publishing.resumed",
                _via({}, via),
            )
            return self._settings(db)

    # Reading -------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        from gofer.social.signin import sign_in_info
        from gofer.social.timing import SOURCE_NOTE, windows_view

        with self.connect() as db:
            rows = db.execute("SELECT body FROM posts ORDER BY updated_at DESC LIMIT 500")
            posts = [self._view(json.loads(row["body"])) for row in rows]
            settings = self._settings(db)
            templates = self._templates(db)
        _add_timing_hints(posts)
        return {
            "posts": posts,
            "templates": templates,
            "best_times": {"windows": windows_view(), "note": SOURCE_NOTE},
            "accounts": self.accounts.list(),
            "platforms": [
                {
                    "id": item.id,
                    "label": item.label,
                    "api": item.api,
                    "text_limit": item.text_limit,
                    "default_delivery": item.default_delivery,
                    "native_scheduling": item.native_scheduling,
                    "needs_video": item.needs_video,
                    "title_limit": item.title_limit,
                    "open_url": item.open_url,
                    "note": item.note,
                    "fold_chars": item.fold_chars,
                    "fold_lines": item.fold_lines,
                    "link_card": item.link_card,
                    "sign_in": sign_in_info(item.id),
                }
                for item in PLATFORMS.values()
            ],
            "paused": settings["paused"],
            "settings": {
                "link_allowlist": settings["link_allowlist"],
                "late_window_minutes": settings["late_window_minutes"],
            },
        }

    def list_posts(self, *, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            if state:
                rows = db.execute(
                    "SELECT body FROM posts WHERE state = ? ORDER BY updated_at DESC LIMIT ?",
                    (state, limit),
                )
            else:
                rows = db.execute(
                    "SELECT body FROM posts ORDER BY updated_at DESC LIMIT ?", (limit,)
                )
            posts = [self._view(json.loads(row["body"])) for row in rows]
        _add_timing_hints(posts)
        return posts

    def get(self, post_id: str, *, events: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            post = self._view(self._load(db, post_id))
            if events:
                post["events"] = [
                    {
                        "at": row["at"],
                        "actor": row["actor"],
                        "kind": row["kind"],
                        "detail": json.loads(row["detail"]),
                    }
                    for row in db.execute(
                        "SELECT * FROM events WHERE post_id = ? ORDER BY seq", (post_id,)
                    )
                ]
                post["attempts"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM attempts WHERE post_id = ? ORDER BY id", (post_id,)
                    )
                ]
        return post

    def _view(self, post: dict[str, Any]) -> dict[str, Any]:
        from gofer.social.providers.x import post_cost, weighted_length

        result = {key: value for key, value in post.items() if not key.startswith("_")}
        result["content_hash"] = content_hash(post)
        result["filled_in"] = post["created_by"] != USER
        variants = []
        for variant in post["variants"]:
            view = dict(variant)
            platform = PLATFORMS[variant["platform"]]
            view["platform_label"] = platform.label
            view["limit"] = platform.text_limit
            text = variant["text"]
            if variant["platform"] == "x":
                full = f"{text}\n{variant['link']}" if variant.get("link") else text
                view["chars"] = weighted_length(full)
                view["cost_usd"] = post_cost(text, variant.get("link") or "")
            else:
                view["chars"] = len(text)
                view["cost_usd"] = None
            view["open_url"] = platform.open_url
            view["fold_at"] = fold_at(variant["platform"], text)
            variants.append(view)
        result["variants"] = variants
        return result

    # Proposing and editing ------------------------------------------------------------

    def propose(self, actor: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create a post waiting for the user's review. Anyone may call this."""
        now = self.clock()
        with self._lock, self.connect() as db:
            settings = self._settings(db)
            if body.get("template") and not body.get("variants"):
                body = self._from_template(db, body, actor)
            post: dict[str, Any] = {
                "id": f"post-{uuid4().hex[:12]}",
                "revision": 1,
                "review_state": "proposed",
                "created_by": actor,
                "created_at": iso(now),
                "approval": None,
                "previous": None,
                "notes": [],
            }
            self._apply_fields(post, body, actor, settings, now, creating=True)
            if is_proposer(actor):
                self._check_proposer_limits(db, actor, post, now)
            self._save(db, post)
            self._event(
                db,
                post["id"],
                actor,
                "proposed",
                {"content_hash": content_hash(post), "accounts": self._targets(post)},
            )
            return self._view(post)

    def _check_proposer_limits(
        self, db: sqlite3.Connection, actor: str, post: Mapping[str, Any], now: datetime
    ) -> None:
        open_rows = [
            json.loads(row["body"])
            for row in db.execute(
                "SELECT body FROM posts WHERE created_by = ? AND state IN "
                "('draft', 'proposed', 'changes_requested')",
                (actor,),
            )
        ]
        if len(open_rows) >= MAX_OPEN_PROPOSALS_PER_SOURCE:
            raise SocialError(
                f"{actor} already has {MAX_OPEN_PROPOSALS_PER_SOURCE} posts waiting for review.",
                "limit",
            )
        since = iso(now - timedelta(days=1))
        count = db.execute(
            "SELECT COUNT(*) FROM events WHERE actor = ? AND kind = 'proposed' AND at >= ?",
            (actor, since),
        ).fetchone()[0]
        if count >= MAX_PROPOSALS_PER_SOURCE_PER_DAY:
            raise SocialError(
                f"{actor} proposed {MAX_PROPOSALS_PER_SOURCE_PER_DAY} posts today. "
                "Try again tomorrow.",
                "limit",
            )
        mine = {_same_text(variant["text"]) for variant in post["variants"]}
        for other in open_rows:
            if mine & {_same_text(variant["text"]) for variant in other["variants"]}:
                raise SocialError(
                    f"The same post is already waiting for review ({other['id']}).", "duplicate"
                )

    def edit(self, actor: str, post_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Change a post. Any change clears the approval and returns it to review."""
        now = self.clock()
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            if is_proposer(actor):
                if post["created_by"] != actor:
                    raise SocialError("Only the post's author or you can revise it.", "forbidden")
                if post["review_state"] not in {"draft", "proposed", "changes_requested"}:
                    raise SocialError(
                        "This post was already decided. Propose a new one instead.", "forbidden"
                    )
            if post["state"] in {"publishing", "published", "partly_published"} or any(
                variant["state"] in {"publishing", "published", "posted_by_you"}
                for variant in post["variants"]
            ):
                raise SocialError("Part of this post already went out, so it can't change.")
            revision = body.get("revision")
            if revision is not None and revision != post["revision"]:
                raise SocialError("This post changed since you opened it. Reload it.", "conflict")
            before = content_hash(post)
            snapshot = {"variants": [_seen(variant) for variant in post["variants"]]}
            held = [
                variant
                for variant in post["variants"]
                if variant["state"] == "held_by_platform" and variant.get("remote_id")
            ]
            settings = self._settings(db)
            self._apply_fields(post, body, actor, settings, now, creating=False)
            if content_hash(post) == before:
                return self._view(post)
            post["revision"] += 1
            post["previous"] = snapshot
            if post["approval"] is not None:
                self._event(db, post_id, actor, "approval.cleared", {"reason": "edited"})
            post["approval"] = None
            post["review_state"] = "proposed"
            for variant in post["variants"]:
                variant.update(state="pending", message="", next_retry_at=None)
            post.setdefault("_cancel_native", []).extend(
                {
                    "account_id": v["account_id"],
                    "platform": v["platform"],
                    "remote_id": v["remote_id"],
                }
                for v in held
            )
            self._save(db, post)
            self._event(
                db, post_id, actor, "edited", {"content_hash": content_hash(post), "from": before}
            )
            return self._view(post)

    def _apply_fields(
        self,
        post: dict[str, Any],
        body: Mapping[str, Any],
        actor: str,
        settings: Mapping[str, Any],
        now: datetime,
        *,
        creating: bool,
    ) -> None:
        if creating or "title" in body:
            post["title"] = _short(body.get("title"), MAX_TITLE) or "Untitled post"
        if creating or "scheduled_at" in body:
            when = parse_time(body.get("scheduled_at"))
            if when is not None and when < now - timedelta(minutes=1) and creating:
                raise SocialError("That time has already passed.")
            post["scheduled_at"] = iso(when) if when else None
        if creating or "timezone" in body:
            post["timezone"] = _short(body.get("timezone"), 64) or "UTC"
        if creating or "late_window_minutes" in body:
            late = body.get("late_window_minutes")
            if late is None:
                late = settings["late_window_minutes"]
            if not isinstance(late, int) or not 1 <= late <= MAX_LATE_WINDOW_MINUTES:
                raise SocialError("The late window is 1 minute to 7 days.")
            post["late_window_minutes"] = late
        if creating or "rationale" in body:
            post["rationale"] = _short(body.get("rationale"), MAX_RATIONALE)
        if creating or "sources" in body:
            post["sources"] = _sources(body.get("sources"))
        if creating or "variants" in body:
            post["variants"] = self._variants(body.get("variants"), actor, post.get("variants"))
        stripped = any(variant.pop("_stripped", False) for variant in post["variants"])
        post["flags"] = flags_for(
            [variant["text"] for variant in post["variants"]],
            [variant["link"] for variant in post["variants"] if variant.get("link")],
            allowlist=settings["link_allowlist"],
            known_hashtags=settings["known_hashtags"],
            stripped_invisible=stripped or bool(post.get("_stripped_before")),
            external_sources=bool(post["sources"]) and is_proposer(post["created_by"]),
        )
        post["_stripped_before"] = stripped or bool(post.get("_stripped_before"))

    def _variants(
        self, items: Any, actor: str, existing: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not isinstance(items, list) or not items:
            raise SocialError("A post needs at least one platform.")
        if len(items) > MAX_VARIANTS:
            raise SocialError(f"A post can have at most {MAX_VARIANTS} platforms.")
        accounts = {account["id"]: account for account in self.accounts.list()}
        old = {variant["id"]: variant for variant in existing or []}
        result: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                raise SocialError("Each platform entry is an object.")
            account_id = item.get("account_id")
            platform = item.get("platform")
            if account_id:
                account = accounts.get(str(account_id))
                if account is None:
                    raise SocialError(f"No connected account {account_id}.")
                platform = account["platform"]
                if is_proposer(actor) and (not account["enabled"] or account["quiet"]):
                    raise SocialError(
                        f"{account['name']} is off or quiet, so only you can post to it.",
                        "forbidden",
                    )
            elif platform in PLATFORMS and not PLATFORMS[platform].api:
                account = None
            elif platform in PLATFORMS:
                matches = [
                    a
                    for a in accounts.values()
                    if a["platform"] == platform
                    and a["enabled"]
                    and (not a["quiet"] or not is_proposer(actor))
                ]
                if len(matches) != 1:
                    raise SocialError(
                        f"Choose which {PLATFORMS[platform].label} account to use."
                        if matches
                        else f"No {PLATFORMS[platform].label} account is available to post to."
                    )
                account = matches[0]
                account_id = account["id"]
            else:
                raise SocialError("Unknown platform. Bluesky and Mastodon aren't supported.")
            target = str(account_id or platform)
            if target in seen_targets:
                raise SocialError("Each account appears once per post.")
            seen_targets.add(target)
            text, stripped = clean_text(str(item.get("text") or ""))
            if len(text) > MAX_TEXT:
                raise SocialError("The text is too long.")
            link = str(item.get("link") or "").strip()
            if link and not link.lower().startswith(("https://", "http://")):
                raise SocialError("Links start with https://.")
            options = _options(platform, item.get("options"))
            delivery = PLATFORMS[platform].default_delivery
            if platform == "linkedin" and account and account["options"].get("send_without_tap"):
                delivery = "api"
            variant_id = str(item.get("id") or "")
            previous = old.get(variant_id)
            media = self._media(item.get("media"), previous, actor)
            result.append(
                {
                    "id": variant_id if previous else f"v-{uuid4().hex[:10]}",
                    "account_id": account_id or None,
                    "account_name": account["name"] if account else PLATFORMS[platform].label,
                    "platform": platform,
                    "delivery": delivery,
                    "text": text,
                    "link": link,
                    "media": media,
                    "options": options,
                    "state": "pending",
                    "remote_id": "",
                    "url": "",
                    "message": "",
                    "next_retry_at": None,
                    "attempts": 0,
                    "_stripped": stripped,
                }
            )
        return result

    def _media(
        self, items: Any, previous: Mapping[str, Any] | None, actor: str = USER
    ) -> list[dict[str, Any]]:
        if items is None:
            return list(previous.get("media") or []) if previous else []
        if not isinstance(items, list) or len(items) > 10:
            raise SocialError("Media is a list of up to 10 files.")
        known = {item["sha256"]: item for item in (previous or {}).get("media") or []}
        result = []
        for item in items:
            digest = item.get("sha256") if isinstance(item, Mapping) else None
            if isinstance(digest, str) and digest in known:
                result.append(known[digest])
                continue
            if isinstance(digest, str):
                stored = self.media_path(digest)
                if stored is not None:
                    # A proposer can't rename a stored file, so the name the user
                    # reviews is never one a proposer chose for someone else's file.
                    name = stored.name if is_proposer(actor) else str(item.get("name") or "")
                    result.append(_media_record(stored, name or stored.name))
                    continue
            path = item.get("path") if isinstance(item, Mapping) else None
            if not isinstance(path, str):
                raise SocialError("Each media item needs a file path.")
            if is_proposer(actor):
                self._check_proposer_media(Path(path))
            result.append(self.import_media(Path(path)))
        return result

    def _check_proposer_media(self, path: Path) -> None:
        """Refuse a proposer's file outside the projects open in Raticode.

        The review card shows a file's name, type and size, not the picture, so a
        proposer must not reach the user's other files, such as a photo in ~/Pictures.
        Every refusal reads the same, so it can't tell whether a file exists.
        """
        refused = SocialError(
            "Rem, tasks and workflows can attach files only from a project open in Raticode.",
            "forbidden",
        )
        roots = list(self.media_roots()) if self.media_roots is not None else []
        try:
            source = path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            raise refused from None
        for root in roots:
            try:
                base = Path(root).resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if source.is_relative_to(base) and not any(
                part.startswith(".") for part in source.relative_to(base).parts
            ):
                return
        raise refused

    def media_path(self, sha256: str) -> Path | None:
        if len(sha256) != 64 or not all(char in "0123456789abcdef" for char in sha256):
            return None
        folder = self.root / MEDIA_FOLDER
        for candidate in folder.glob(f"{sha256}.*"):
            return candidate
        return None

    def import_media(self, path: Path) -> dict[str, Any]:
        """Copy a file into the data folder by its SHA-256 so it can't change after review."""
        try:
            source = path.expanduser().resolve(strict=True)
        except OSError:
            raise SocialError(f"Can't read {path.name}.") from None
        if not source.is_file():
            raise SocialError(f"{path.name} isn't a file.")
        with source.open("rb") as handle:
            head = handle.read(16)
        # Only the file's own bytes decide its type, never its name.
        mime = _sniff(head) or ""
        if mime not in MEDIA_TYPES:
            raise SocialError("Media can be JPEG, PNG, GIF, WebP, MP4, MOV or WebM.")
        size = source.stat().st_size
        limit = MAX_VIDEO_BYTES if mime.startswith("video/") else MAX_IMAGE_BYTES
        if size > limit:
            raise SocialError(f"{source.name} is larger than {limit // (1024 * 1024)} MB.")
        folder = self.root / MEDIA_FOLDER
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = folder / f".{uuid4().hex}.tmp"
        try:
            shutil.copyfile(source, temporary)
            if mime in {"image/jpeg", "image/png", "image/webp"}:
                _strip_exif(temporary, mime)
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            extension = mimetypes.guess_extension(mime) or ".bin"
            target = folder / f"{digest}{extension}"
            if not target.exists():
                temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return _media_record(target, source.name)

    def _targets(self, post: Mapping[str, Any]) -> list[str]:
        return [str(v["account_id"] or v["platform"]) for v in post["variants"]]

    # The user's decisions -------------------------------------------------------------

    @staticmethod
    def _require_user(actor: str, action: str) -> None:
        if actor != USER:
            raise SocialError(f"Only you can {action}.", "forbidden")

    def approve(
        self,
        actor: str,
        post_id: str,
        shown_hash: Any,
        *,
        publish_now: bool = False,
        via: str = "",
    ) -> dict[str, Any]:
        """Approve the exact version the user saw. A different hash is refused."""
        self._require_user(actor, "approve a post")
        now = self.clock()
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            if post["review_state"] not in {"proposed", "changes_requested", "draft", "expired"}:
                raise SocialError("This post isn't waiting for review.", "conflict")
            if publish_now:
                # Publishing now changes the time, so it is part of what gets approved.
                if shown_hash != content_hash(post):
                    raise SocialError(
                        "The post changed since you looked at it. Review it again.", "conflict"
                    )
                post["scheduled_at"] = iso(now)
            elif shown_hash != content_hash(post):
                raise SocialError(
                    "The post changed since you looked at it. Review it again.", "conflict"
                )
            when = parse_time(post["scheduled_at"])
            if when is not None and when + timedelta(minutes=post["late_window_minutes"]) < now:
                raise SocialError("This post's time has passed. Pick a new time first.")
            problems = self._validate(post)
            if problems:
                raise SocialError(" ".join(problems))
            approved_hash = content_hash(post)
            post["approval"] = {"by": USER, "at": iso(now), "content_hash": approved_hash}
            post["review_state"] = "approved"
            later = when is not None and when > now + timedelta(seconds=30)
            for variant in post["variants"]:
                if variant["delivery"] == "manual":
                    variant["state"] = "ready_to_post"
                else:
                    variant["state"] = "scheduled" if later else "approved"
            self._remember_hashtags(db, post)
            self._save(db, post)
            self._event(
                db,
                post_id,
                actor,
                "approved",
                _via({"content_hash": approved_hash, "publish_now": publish_now}, via),
            )
            return self._view(post)

    def _validate(self, post: Mapping[str, Any]) -> list[str]:
        from gofer.social.publisher import request_for

        problems = []
        for variant in post["variants"]:
            platform = PLATFORMS[variant["platform"]]
            if not platform.api:
                continue
            from gofer.social.providers import adapter_for

            adapter = adapter_for(variant["platform"])
            if adapter is None:
                continue
            try:
                account = self.accounts.get(variant["account_id"])
            except ValueError:
                problems.append(f"{variant['account_name']} was disconnected.")
                continue
            request = request_for(self, post, variant, account)
            problems += [f"{platform.label}: {item}" for item in adapter.validate(request)]
        return problems

    def _remember_hashtags(self, db: sqlite3.Connection, post: Mapping[str, Any]) -> None:
        import re

        tags = set(self._settings(db)["known_hashtags"])
        for variant in post["variants"]:
            tags |= {
                m.lower() for m in re.findall(r"(?<![\w#])#[^\s#.,;:!?()]{1,100}", variant["text"])
            }
        self._set(db, "known_hashtags", sorted(tags)[:1000])

    def decide(
        self, actor: str, post_id: str, decision: str, note: Any = "", *, via: str = ""
    ) -> dict[str, Any]:
        """decline, request_changes or cancel."""
        names = {
            "decline": "decline a post",
            "request_changes": "ask for changes",
            "cancel": "cancel a post",
        }
        if decision not in names:
            raise SocialError("Unknown decision.")
        self._require_user(actor, names[decision])
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            note_text = _short(note, 2000)
            if decision == "cancel":
                for variant in post["variants"]:
                    if variant["state"] == "publishing":
                        raise SocialError("A send is in progress. Try again in a moment.")
                    if variant["state"] == "held_by_platform" and variant.get("remote_id"):
                        post.setdefault("_cancel_native", []).append(
                            {
                                "account_id": variant["account_id"],
                                "platform": variant["platform"],
                                "remote_id": variant["remote_id"],
                            }
                        )
                    if variant["state"] not in DONE_VARIANT_STATES:
                        variant["state"] = "cancelled"
                if post["review_state"] in REVIEW_STATES:
                    post["review_state"] = "approved"  # so the state comes from the variants
            else:
                if post["review_state"] not in {"proposed", "changes_requested", "draft"}:
                    raise SocialError("This post isn't waiting for review.", "conflict")
                post["review_state"] = "declined" if decision == "decline" else "changes_requested"
            if note_text:
                post.setdefault("notes", []).append(
                    {"by": actor, "at": iso(self.clock()), "text": note_text}
                )
            self._save(db, post)
            self._event(db, post_id, actor, decision, _via({"note": note_text}, via))
            return self._view(post)

    def publish_variant(
        self, actor: str, post_id: str, variant_id: str, shown_hash: Any, *, via: str = ""
    ) -> dict[str, Any]:
        """The user's Publish tap: a due variant, or sending a stuck one again now."""
        self._require_user(actor, "publish a post")
        now = self.clock()
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            approval = post.get("approval") or {}
            current = content_hash(post)
            if approval.get("content_hash") != current or shown_hash != current:
                raise SocialError(
                    "This post isn't the version you approved. Review it again.", "conflict"
                )
            variant = _variant(post, variant_id)
            allowed = {"due", "scheduled", "approved", "held"} | (
                PROBLEM_VARIANT_STATES - {"needs_reconnect"}
            )
            if variant["delivery"] == "manual" or variant["state"] not in allowed:
                raise SocialError(
                    "This platform can't be published from here right now.", "conflict"
                )
            variant.update(state="approved", next_retry_at=None, message="", publish_tap=iso(now))
            self._save(db, post)
            self._event(
                db,
                post_id,
                actor,
                "publish_tapped",
                _via({"variant": variant_id, "content_hash": current}, via),
            )
            return self._view(post)

    def mark_posted(
        self, actor: str, post_id: str, variant_id: str, url: Any, *, via: str = ""
    ) -> dict[str, Any]:
        self._require_user(actor, "mark a post as posted")
        link = _short(url, 2000)
        if link and not link.startswith(("https://", "http://")):
            raise SocialError("Paste the post's link, starting with https://.")
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            variant = _variant(post, variant_id)
            if variant["state"] not in {
                "ready_to_post",
                "outcome_unknown",
                "missed",
                "failed",
                "due",
            }:
                raise SocialError("This platform isn't waiting for you.", "conflict")
            if post.get("approval") is None:
                raise SocialError("Approve the post first.", "conflict")
            variant.update(state="posted_by_you", url=link, message="")
            self._save(db, post)
            self._event(
                db, post_id, actor, "marked_posted", _via({"variant": variant_id, "url": link}, via)
            )
            return self._view(post)

    # Templates and timing -------------------------------------------------------------

    def _templates(self, db: sqlite3.Connection) -> list[dict[str, Any]]:
        from gofer.social.templates import view

        own = [
            view({**json.loads(row["body"]), "id": row["id"], "updated_at": row["updated_at"]})
            for row in db.execute("SELECT * FROM templates ORDER BY updated_at DESC")
        ]
        return own + [view(item, builtin=True) for item in STARTERS]

    def templates(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._templates(db)

    def save_template(
        self, actor: str, body: Mapping[str, Any], template_id: str | None = None
    ) -> dict[str, Any]:
        """Save a new template, or change one of the user's. Starters can't change."""
        from gofer.social.templates import clean, view

        self._require_user(actor, "save a post template")
        try:
            template = clean(body)
        except TemplateError as exc:
            raise SocialError(str(exc)) from None
        now = iso(self.clock())
        with self._lock, self.connect() as db:
            if template_id is None:
                count = db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
                if count >= MAX_TEMPLATES:
                    raise SocialError(f"You can keep up to {MAX_TEMPLATES} templates.", "limit")
                template_id = f"template-{uuid4().hex[:10]}"
                db.execute(
                    "INSERT INTO templates (id, created_at, updated_at, body) VALUES (?, ?, ?, ?)",
                    (template_id, now, now, json.dumps(template, ensure_ascii=False)),
                )
                kind = "template.saved"
            else:
                changed = db.execute(
                    "UPDATE templates SET updated_at = ?, body = ? WHERE id = ?",
                    (now, json.dumps(template, ensure_ascii=False), template_id),
                ).rowcount
                if not changed:
                    raise SocialError("No such template.", "not_found")
                kind = "template.changed"
            self._event(db, None, actor, kind, {"template": template_id, "name": template["name"]})
        return view({**template, "id": template_id, "updated_at": now})

    def delete_template(self, actor: str, template_id: str) -> dict[str, Any]:
        self._require_user(actor, "delete a post template")
        with self._lock, self.connect() as db:
            if not db.execute("DELETE FROM templates WHERE id = ?", (template_id,)).rowcount:
                raise SocialError("No such template.", "not_found")
            self._event(db, None, actor, "template.deleted", {"template": template_id})
        return {"ok": True}

    def _from_template(
        self, db: sqlite3.Connection, body: Mapping[str, Any], actor: str
    ) -> dict[str, Any]:
        """A proposal's fields from ``template`` and ``fields``; given fields win."""
        from gofer.social.templates import to_proposal

        wanted = str(body.get("template") or "")
        template = next((t for t in self._templates(db) if t["id"] == wanted), None)
        if template is None:
            raise SocialError("No such template.", "not_found")
        values = body.get("fields")
        try:
            title, variants = to_proposal(
                template,
                values if isinstance(values, Mapping) else {},
                self.accounts.list(),
                proposer=is_proposer(actor),
            )
        except TemplateError as exc:
            raise SocialError(str(exc)) from None
        result = {k: v for k, v in body.items() if k not in {"template", "fields"}}
        result["variants"] = variants
        if not result.get("title"):
            result["title"] = title
        return result

    def suggest_times(self, targets: list[str], timezone: Any, *, count: int = 3) -> dict[str, Any]:
        """The next good times to post to these accounts or platforms (general guidance)."""
        from gofer.social.timing import SOURCE_NOTE
        from gofer.social.timing import suggest_times as suggest

        accounts = {account["id"]: account for account in self.accounts.list()}
        platforms = []
        for target in targets[:MAX_VARIANTS]:
            if target in accounts:
                platforms.append(accounts[target]["platform"])
            elif target in PLATFORMS:
                platforms.append(target)
        keys = set(targets)
        taken = []
        for post in self.posts_in(("scheduled", "approved", "proposed", "changes_requested")):
            when = parse_time(post.get("scheduled_at"))
            if when is not None and any(
                str(v.get("account_id") or v["platform"]) in keys
                or (not v.get("account_id") and v["platform"] in platforms)
                for v in post["variants"]
            ):
                taken.append(when)
        return {
            "slots": suggest(platforms, timezone, self.clock(), taken, count=max(1, min(count, 7))),
            "note": SOURCE_NOTE,
        }

    # Publisher bookkeeping ------------------------------------------------------------

    def record_attempt_start(self, post_id: str, variant: Mapping[str, Any], trigger: str) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO attempts (post_id, variant_id, account_id, started_at, trigger) "
                "VALUES (?, ?, ?, ?, ?)",
                (post_id, variant["id"], variant.get("account_id"), iso(self.clock()), trigger),
            )
            return int(cursor.lastrowid or 0)

    def record_attempt_end(
        self, attempt_id: int, *, status: int | None, error_class: str | None, message: str
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE attempts SET ended_at = ?, status = ?, error_class = ?, message = ? "
                "WHERE id = ?",
                (iso(self.clock()), status, error_class, message[:500], attempt_id),
            )

    def update_post(
        self, post_id: str, change: Any, actor: str, kind: str, detail: Any
    ) -> dict[str, Any]:
        """Apply ``change(post)`` under the lock and log it. Used by the publisher."""
        with self._lock, self.connect() as db:
            post = self._load(db, post_id)
            result = change(post)
            if result is False:
                return post
            self._save(db, post)
            self._event(db, post_id, actor, kind, detail)
            return post

    def posts_in(self, states: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in states)
        with self.connect() as db:
            return [
                json.loads(row["body"])
                for row in db.execute(f"SELECT body FROM posts WHERE state IN ({marks})", states)
            ]

    def sends_since(self, account_id: str, since: datetime) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE account_id = ? AND started_at >= ? "
                    "AND (error_class IS NULL OR error_class = 'outcome_unknown')",
                    (account_id, iso(since)),
                ).fetchone()[0]
            )

    def log(
        self, actor: str, kind: str, detail: Mapping[str, Any], post_id: str | None = None
    ) -> None:
        with self.connect() as db:
            self._event(db, post_id, actor, kind, detail)


def _via(detail: dict[str, Any], via: str) -> dict[str, Any]:
    """An event's detail, saying where the user acted when it wasn't the window."""
    if via:
        detail["via"] = via
    return detail


def _add_timing_hints(posts: list[dict[str, Any]]) -> None:
    """Spacing and quiet-hour notes on posts still waiting to go out."""
    from gofer.social.timing import timing_hints

    waiting = {"proposed", "changes_requested", "draft", "approved", "scheduled"}
    planned = [post for post in posts if post.get("state") in waiting]
    for post in posts:
        post["timing"] = timing_hints(post, planned) if post.get("state") in waiting else []


def _variant(post: Mapping[str, Any], variant_id: str) -> dict[str, Any]:
    for variant in post["variants"]:
        if variant["id"] == variant_id:
            found: dict[str, Any] = variant  # edited in place
            return found
    raise SocialError("No such platform on this post.", "not_found")


def _short(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _same_text(text: str) -> str:
    return " ".join(text.lower().split())


def _seen(variant: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": variant["id"],
        "account_id": variant["account_id"],
        "platform": variant["platform"],
        "text": variant["text"],
        "link": variant.get("link", ""),
    }


def _sources(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SocialError("Sources are a list of links.")
    result = []
    for item in value[:MAX_SOURCES]:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, Mapping):
            continue
        url = _short(item.get("url"), 2000)
        if url.startswith(("https://", "http://")):
            result.append({"url": url, "title": _short(item.get("title"), 200)})
    return result


def _options(platform: str, value: Any) -> dict[str, Any]:
    # Every option kept here is shown on the review card, so nothing is approved unseen.
    # LinkedIn's link-card description was dropped for that reason: no view showed it.
    allowed = {
        "discord": {"thread_id"},
        "x": set(),
        "linkedin": {"visibility", "article_title"},
        "facebook": set(),
        "youtube": {"title"},
        "patreon": {"title"},
        "buymeacoffee": {"title"},
        "tiktok": set(),
    }[platform]
    if not isinstance(value, Mapping):
        return {}
    options = {key: str(value[key])[:5000] for key in allowed if value.get(key) not in (None, "")}
    if platform == "linkedin" and options.get("visibility", "PUBLIC") not in {
        "PUBLIC",
        "CONNECTIONS",
    }:
        raise SocialError("LinkedIn visibility is PUBLIC or CONNECTIONS.")
    return options


def _sniff(head: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:10] == b"qt" else "video/mp4"
    return None


def _strip_exif(path: Path, mime: str = "image/jpeg") -> None:
    """Re-save a JPEG, PNG or WebP without its EXIF and text blocks.

    They can hold the camera's location. PNG keeps them in eXIf and text chunks, which
    Pillow writes back only when asked to.
    """
    try:
        from PIL import Image

        with Image.open(path) as image:
            if mime != "image/jpeg" and getattr(image, "is_animated", False):
                return  # copying would keep only the first frame
            image.load()
            clean = image.copy()
        for key in ("exif", "xmp", "XML:com.adobe.xmp"):
            clean.info.pop(key, None)
        if mime == "image/png":
            clean.save(path, format="PNG")
        elif mime == "image/webp":
            clean.save(path, format="WEBP", quality=95, exif=b"", xmp=b"")
        else:
            clean.save(path, format="JPEG", quality=95, exif=b"")
    except Exception as exc:  # noqa: BLE001 - refuse rather than keep a location tag
        raise SocialError("Couldn't remove the photo's location data.") from exc


def _media_record(path: Path, name: str) -> dict[str, Any]:
    head = path.read_bytes()[:16]
    mime = _sniff(head) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "sha256": path.name.split(".")[0],
        "name": Path(name).name[:200],
        "mime": mime,
        "size": path.stat().st_size,
    }
