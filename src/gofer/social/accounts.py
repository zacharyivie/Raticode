"""Connected social accounts: a non-secret index file and one keychain entry per account.

Logins (webhook URLs, access and refresh tokens, client secrets) live in the OS keychain
under the service "Raticode social accounts", which is separate from workflow secrets
("Raticode workflow secrets"), so no ``secret["NAME"]`` binding in a workflow can read
them. The entry's account name starts with a hash of the data folder, so a development
data folder never reads the installed app's logins. The index file lists names,
platforms, switches, caps and expiry dates, never values.

Only the publisher and the user's own connect and check actions read an entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from gofer.social.models import PLATFORMS
from gofer.utils.instants import stored_instant

KEYCHAIN_SERVICE = "Raticode social accounts"
INDEX_FILE = "accounts.json"
MAX_SECRET_BYTES = 16 * 1024
EXPIRY_WARNING_DAYS = (14, 3)
STORE_ERROR = (
    "Raticode couldn't use the system keychain. Unlock it (on Linux, the Secret Service "
    "such as GNOME Keyring or KWallet) and try again."
)
# Which login fields each platform takes. Everything listed is kept in the keychain.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "discord": ("webhook_url",),
    "x": (
        "api_key",
        "api_secret",
        "access_token",
        "access_token_secret",
        "refresh_token",
        "client_id",
        "client_secret",
    ),
    # A sign-in also keeps the user's own app client ID and secret, so signing in again
    # is one click. Pasted logins don't need them.
    "linkedin": ("access_token", "client_id", "client_secret"),
    "facebook": ("page_access_token", "client_id", "client_secret"),
    "youtube": ("access_token", "refresh_token", "client_id", "client_secret"),
}
REQUIRED_SECRET: dict[str, str] = {
    "discord": "webhook_url",
    "x": "access_token",
    "linkedin": "access_token",
    "facebook": "page_access_token",
    "youtube": "refresh_token",
}
# X takes either the console's API keys (OAuth 1.0a, the default) or an OAuth 2.0 user
# token. Each account keeps only one method's fields, so the publisher can tell them apart.
X_AUTH_FIELDS: dict[str, tuple[str, ...]] = {
    "api_keys": ("api_key", "api_secret", "access_token", "access_token_secret"),
    "oauth2": ("access_token", "refresh_token", "client_id", "client_secret"),
}
X_REQUIRED: dict[str, tuple[str, ...]] = {
    "api_keys": X_AUTH_FIELDS["api_keys"],
    "oauth2": ("access_token",),
}
_X_REQUIRED_MESSAGE = {
    "api_keys": "X needs the API key, API secret, access token and access token secret.",
    "oauth2": "X needs access token.",
}
# Non-secret settings each platform reads from the account.
OPTION_FIELDS: dict[str, tuple[str, ...]] = {
    "discord": ("allowed_mentions", "thread_id"),
    "x": ("monthly_budget_usd",),
    "linkedin": ("author_urn", "api_version", "send_without_tap"),
    "facebook": ("page_id",),
    "youtube": ("privacy",),
}
_DISCORD_WEBHOOK = re.compile(
    r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+"
)
_lock = threading.RLock()


class SocialAccountError(ValueError):
    """A safe error for the dashboard and CLI. Never contains a login."""


def _x_auth(value: Any, secrets: Mapping[str, str], existing: Mapping[str, Any] | None) -> str:
    """Which X method a connect uses: the one asked for, else the one its fields imply."""
    if value is not None:
        if not isinstance(value, str) or value not in X_AUTH_FIELDS:
            raise SocialAccountError("Connect X with API keys or an OAuth 2.0 token.")
        return str(value)
    if any(key in secrets for key in ("api_key", "api_secret", "access_token_secret")):
        return "api_keys"
    if any(key in secrets for key in ("refresh_token", "client_id", "client_secret")):
        return "oauth2"
    # Only an access token (or nothing, when replacing): keep the account's method.
    # Accounts saved before API keys existed are OAuth 2.0.
    return str(existing.get("auth") or "oauth2") if existing else "oauth2"


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...
    def put(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


def os_secret_store() -> SecretStore:
    from gofer.devices.storage import OSSecretStore

    return OSSecretStore(service=KEYCHAIN_SERVICE)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class AccountStore:
    """The account index for one data folder. Inject ``secrets`` in tests."""

    def __init__(self, root: Path, *, secrets: SecretStore | None = None) -> None:
        self.root = root
        self._secrets = secrets
        self._prefix = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:32]

    @property
    def path(self) -> Path:
        return self.root / INDEX_FILE

    def _store(self) -> SecretStore:
        if self._secrets is None:
            try:
                self._secrets = os_secret_store()
            except Exception:
                raise SocialAccountError(STORE_ERROR) from None
        return self._secrets

    def _entry(self, account_id: str) -> str:
        return f"{self._prefix}:{account_id}"

    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        items = raw.get("accounts") if isinstance(raw, dict) else None
        return [item for item in items or [] if isinstance(item, dict) and item.get("id")]

    def _save(self, accounts: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({"accounts": accounts}, indent=2) + "\n", "utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def list(self) -> list[dict[str, Any]]:
        with _lock:
            return [self._public(item) for item in self._load()]

    def get(self, account_id: str) -> dict[str, Any]:
        with _lock:
            for item in self._load():
                if item["id"] == account_id:
                    return dict(item)
        raise SocialAccountError("No such social account.")

    def _public(self, item: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        moment = now or _now()
        result = {key: value for key, value in item.items() if key != "secret_fields"}
        result["status"] = self.status(item, moment)
        expires = stored_instant(item.get("token_expires_at"))
        result["expires_in_days"] = max(0, (expires - moment).days) if expires is not None else None
        result["platform_label"] = PLATFORMS[item["platform"]].label
        return result

    @staticmethod
    def status(item: Mapping[str, Any], now: datetime | None = None) -> str:
        """connected, reconnect_soon, needs_reconnect or disabled."""
        if not item.get("enabled", True):
            return "disabled"
        if item.get("needs_reconnect"):
            return "needs_reconnect"
        expires = stored_instant(item.get("token_expires_at"))
        moment = now or _now()
        if expires is not None:
            if expires <= moment:
                return "needs_reconnect"
            if expires - moment <= timedelta(days=EXPIRY_WARNING_DAYS[0]):
                return "reconnect_soon"
        return "connected"

    def connect(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Add an account or replace its login. The user's action only."""
        platform = body.get("platform")
        if platform not in SECRET_FIELDS:
            raise SocialAccountError("Choose Discord, X, LinkedIn, Facebook Page or YouTube.")
        name = str(body.get("name") or "").strip()[:80] or PLATFORMS[platform].label
        secrets_in = body.get("secrets")
        if not isinstance(secrets_in, Mapping):
            raise SocialAccountError("Enter the account's login details.")
        secrets: dict[str, str] = {}
        for key in SECRET_FIELDS[platform]:
            value = secrets_in.get(key)
            if isinstance(value, str) and value.strip():
                secrets[key] = value.strip()
        unknown = set(secrets_in) - set(SECRET_FIELDS[platform])
        if unknown:
            raise SocialAccountError("Unknown login fields for this platform.")
        account_id = str(body.get("id") or "")
        existing = None
        if account_id:
            existing = self.get(account_id)
            if existing["platform"] != platform:
                raise SocialAccountError("An account can't change platform.")
        auth = _x_auth(body.get("auth"), secrets, existing) if platform == "x" else None
        if auth is not None and set(secrets) - set(X_AUTH_FIELDS[auth]):
            raise SocialAccountError(
                "Those fields belong to the other way of connecting X. Send one method's fields."
            )
        if existing is not None:
            # Replacing a login keeps saved values for fields left empty, unless the
            # account switches to the other X method, which starts from nothing.
            same_method = auth is None or auth == (existing.get("auth") or "oauth2")
            if same_method:
                secrets = {**self.read_secrets(account_id), **secrets}
        if auth is not None:
            if any(not secrets.get(key) for key in X_REQUIRED[auth]):
                raise SocialAccountError(_X_REQUIRED_MESSAGE[auth])
        elif not secrets.get(REQUIRED_SECRET[platform]):
            raise SocialAccountError(
                f"{PLATFORMS[platform].label} needs {REQUIRED_SECRET[platform].replace('_', ' ')}."
            )
        if platform == "discord" and not _DISCORD_WEBHOOK.fullmatch(secrets["webhook_url"]):
            raise SocialAccountError("That isn't a Discord webhook address.")
        encoded = json.dumps(secrets, separators=(",", ":"))
        if len(encoded.encode()) > MAX_SECRET_BYTES:
            raise SocialAccountError("The login details are too long.")
        options = self._options(platform, body.get("options"), existing)
        now = _now()
        expires_days = body.get("token_expires_in_days")
        expires_at = None
        if platform == "linkedin":
            # Self-serve LinkedIn tokens last 60 days and have no refresh token.
            days = expires_days if isinstance(expires_days, int) and 0 < expires_days <= 60 else 60
            expires_at = _iso(now + timedelta(days=days))
        elif isinstance(expires_days, int) and expires_days > 0:
            expires_at = _iso(now + timedelta(days=min(expires_days, 3650)))
        with _lock:
            accounts = self._load()
            record = {
                "id": account_id or f"{platform}-{uuid4().hex[:10]}",
                "platform": platform,
                "name": name,
                "enabled": existing.get("enabled", True) if existing else True,
                "quiet": bool(body.get("quiet", existing.get("quiet") if existing else False)),
                "daily_cap": self._cap(body.get("daily_cap"), existing, "daily_cap", 10),
                "monthly_cap": self._cap(body.get("monthly_cap"), existing, "monthly_cap", 100),
                "options": options,
                "token_expires_at": expires_at,
                "needs_reconnect": False,
                "connected_at": _iso(now),
                "last_posted_at": existing.get("last_posted_at") if existing else None,
                "secret_fields": sorted(secrets),
            }
            if auth is not None:
                record["auth"] = auth
            try:
                self._store().put(self._entry(record["id"]), encoded)
            except SocialAccountError:
                raise
            except Exception:
                raise SocialAccountError(STORE_ERROR) from None
            accounts = [item for item in accounts if item["id"] != record["id"]] + [record]
            self._save(accounts)
        return self._public(record)

    @staticmethod
    def _cap(value: Any, existing: Mapping[str, Any] | None, key: str, default: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
            return value
        if existing and isinstance(existing.get(key), int):
            return int(existing[key])
        return default

    @staticmethod
    def _options(platform: str, value: Any, existing: Mapping[str, Any] | None) -> dict[str, Any]:
        options = dict(existing.get("options") or {}) if existing else {}
        if isinstance(value, Mapping):
            for key in OPTION_FIELDS[platform]:
                if key in value:
                    options[key] = value[key]
        if platform == "discord":
            mentions = options.get("allowed_mentions")
            # None by default, so a draft can't ping @everyone, a role or anyone.
            allowed = [item for item in mentions or [] if item in {"users", "roles", "everyone"}]
            options["allowed_mentions"] = allowed
        if platform == "linkedin":
            options["send_without_tap"] = options.get("send_without_tap") is True
        if platform == "youtube" and options.get("privacy") not in {"public", "unlisted"}:
            options["privacy"] = "public"
        return options

    def update(self, account_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Switches and caps: enabled, quiet, daily_cap, monthly_cap, options."""
        with _lock:
            accounts = self._load()
            for item in accounts:
                if item["id"] != account_id:
                    continue
                if isinstance(body.get("enabled"), bool):
                    item["enabled"] = body["enabled"]
                if isinstance(body.get("quiet"), bool):
                    item["quiet"] = body["quiet"]
                item["daily_cap"] = self._cap(body.get("daily_cap"), item, "daily_cap", 10)
                item["monthly_cap"] = self._cap(body.get("monthly_cap"), item, "monthly_cap", 100)
                if "name" in body and str(body["name"]).strip():
                    item["name"] = str(body["name"]).strip()[:80]
                if "options" in body:
                    item["options"] = self._options(item["platform"], body["options"], item)
                self._save(accounts)
                return self._public(item)
        raise SocialAccountError("No such social account.")

    def mark(self, account_id: str, **fields: Any) -> None:
        """Publisher bookkeeping: needs_reconnect, last_posted_at, token_expires_at."""
        with _lock:
            accounts = self._load()
            for item in accounts:
                if item["id"] == account_id:
                    item.update(fields)
            self._save(accounts)

    def disconnect(self, account_id: str) -> None:
        with _lock:
            accounts = self._load()
            if not any(item["id"] == account_id for item in accounts):
                raise SocialAccountError("No such social account.")
            try:
                self._store().delete(self._entry(account_id))
            except SocialAccountError:
                raise
            except Exception:
                raise SocialAccountError(STORE_ERROR) from None
            self._save([item for item in accounts if item["id"] != account_id])

    def read_secrets(self, account_id: str) -> dict[str, str]:
        """The login for one account. Only the publisher and the user's actions call this."""
        try:
            raw = self._store().get(self._entry(account_id))
        except SocialAccountError:
            raise
        except Exception:
            raise SocialAccountError(STORE_ERROR) from None
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return (
            {k: v for k, v in value.items() if isinstance(v, str)}
            if isinstance(value, dict)
            else {}
        )

    def write_secrets(self, account_id: str, secrets: Mapping[str, str]) -> None:
        """Save refreshed tokens after the publisher renews them."""
        try:
            self._store().put(self._entry(account_id), json.dumps(dict(secrets)))
        except Exception:
            raise SocialAccountError(STORE_ERROR) from None
