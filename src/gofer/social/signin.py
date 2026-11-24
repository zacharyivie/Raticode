"""Sign in to a Posts platform in the system browser, with no website of Raticode's.

The loopback pattern from RFC 8252: Raticode listens on ``127.0.0.1`` for a few minutes,
opens the platform's consent page in the user's own browser, and the platform sends the
browser back to ``http://127.0.0.1:<port>/callback`` (or ``localhost`` where a platform
asks for that form) with a one-time code. Raticode checks the ``state`` it made, trades
the code for tokens with the PKCE verifier only it knows, and saves the tokens in the
same keychain entry a pasted login uses (``AccountStore.connect``).

The listener binds the loopback address only, takes one callback and then closes. A
reply with the wrong ``state`` is turned away and the listener keeps waiting, since any
local program or web page can reach the port. Nothing here ships a client ID or
secret: every platform needs the user's own developer app, and its client ID (and secret
where the platform needs one) comes from the user.

Platforms that match the redirect address exactly (X, LinkedIn, Facebook) get a fixed
port the user registers once; Google Desktop clients accept any loopback port, so
YouTube gets a random one. Discord posts through a webhook and has nothing to sign in to.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets as token_source
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import anyio

from gofer.core.http import HttpClient, HttpRequest, HttpResponse
from gofer.social.accounts import SocialAccountError
from gofer.social.providers import facebook as facebook_provider
from gofer.social.providers import x as x_provider
from gofer.social.providers import youtube as youtube_provider
from gofer.social.providers.base import DEFAULT_TIMEOUT, platform_message, redact

if TYPE_CHECKING:
    from gofer.social.service import SocialService

CALLBACK_PATH = "/callback"
DEFAULT_PORT = 47689
SIGN_IN_SECONDS = 300
FINISHED_KEEP_SECONDS = 600
MAX_REQUEST_BYTES = 16 * 1024
NO_SIGN_IN = {
    "discord": "Discord posts through a webhook, so there's nothing to sign in to. Paste the "
    "channel's webhook URL instead.",
}
PAGE_FIELDS = {"fields": "id,name,access_token", "limit": "100"}
FINAL = frozenset({"done", "failed", "cancelled", "expired"})


class SignInError(ValueError):
    """A safe message for the dashboard. Never contains a code, token or secret."""


@dataclass(frozen=True)
class OAuthPlatform:
    platform: str
    label: str  # who the user signs in with
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    scope_separator: str = " "
    redirect_host: str = "127.0.0.1"
    # True when the platform matches the redirect address exactly, so the port is fixed
    # and the user registers http://<host>:<port>/callback in their app once.
    fixed_port: bool = True
    secret_required: bool = True
    extra_params: Mapping[str, str] = field(default_factory=dict)
    # X: a confidential client sends its secret as HTTP Basic auth; a public one doesn't.
    basic_auth: bool = False
    console_url: str = ""

    def redirect_uri(self, port: int) -> str:
        return f"http://{self.redirect_host}:{port}{CALLBACK_PATH}"

    def public(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "fixed_port": self.fixed_port,
            "default_port": DEFAULT_PORT if self.fixed_port else None,
            "redirect_host": self.redirect_host,
            "callback_path": CALLBACK_PATH,
            "secret_required": self.secret_required,
            "scopes": list(self.scopes),
            "console_url": self.console_url,
            "timeout_seconds": SIGN_IN_SECONDS,
        }


OAUTH_PLATFORMS: dict[str, OAuthPlatform] = {
    # Google recommends loopback on any port for desktop apps; copy/paste codes and custom
    # schemes are gone. prompt=consent makes Google send a refresh token every time.
    "youtube": OAuthPlatform(
        platform="youtube",
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url=youtube_provider.TOKEN_URL,
        scopes=("https://www.googleapis.com/auth/youtube.upload",),
        fixed_port=False,
        extra_params={"access_type": "offline", "prompt": "consent"},
        console_url="https://console.cloud.google.com/apis/credentials",
    ),
    # X matches callback URLs exactly and wants 127.0.0.1, not localhost.
    "x": OAuthPlatform(
        platform="x",
        label="X",
        authorize_url="https://x.com/i/oauth2/authorize",
        token_url=x_provider.TOKEN_URL,
        scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
        secret_required=False,
        basic_auth=True,
        console_url="https://console.x.com",
    ),
    # LinkedIn's standard flow: exact redirect match and the client secret at the token
    # step. Tokens last 60 days with no refresh for self-serve apps.
    "linkedin": OAuthPlatform(
        platform="linkedin",
        label="LinkedIn",
        authorize_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        scopes=("openid", "profile", "w_member_social"),
        console_url="https://www.linkedin.com/developers/apps",
    ),
    # Facebook Login's manual flow: the app secret is needed to trade the code and to
    # extend the token, and Meta allows localhost redirects for apps in development mode.
    "facebook": OAuthPlatform(
        platform="facebook",
        label="Facebook",
        authorize_url=f"https://www.facebook.com/{facebook_provider.GRAPH_VERSION}/dialog/oauth",
        token_url=f"{facebook_provider.GRAPH}/oauth/access_token",
        scopes=("pages_show_list", "pages_read_engagement", "pages_manage_posts"),
        scope_separator=",",
        redirect_host="localhost",
        console_url="https://developers.facebook.com/apps",
    ),
}


def sign_in_info(platform: str) -> dict[str, Any] | None:
    """What the Connect flow shows about a platform's sign-in, or None without one."""
    spec = OAUTH_PLATFORMS.get(platform)
    if spec is None:
        return (
            {"available": False, "reason": NO_SIGN_IN[platform]} if platform in NO_SIGN_IN else None
        )
    return {"available": True, **spec.public()}


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier (43-128 characters) and its S256 challenge."""
    verifier = token_source.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def open_in_system_browser(url: str) -> bool:
    """Open the consent page in the user's default browser on this computer."""
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


class LoopbackListener:
    """The sign-in's listening sockets: IPv4 loopback, plus ``[::1]`` on the same port when
    the redirect names ``localhost``.

    Browsers try ``localhost`` as ``::1`` first on most systems, so a ``localhost``
    redirect served on 127.0.0.1 alone would let another program (or another account on
    this computer) listen on ``[::1]:<port>`` and receive the browser's callback.
    """

    def __init__(self, sockets: list[socket.socket]) -> None:
        self.sockets = sockets

    def getsockname(self) -> Any:
        return self.sockets[0].getsockname()

    def accept(self) -> tuple[socket.socket, Any]:
        """The next connection on any socket; TimeoutError after 0.25 s without one."""
        import select

        live = [item for item in self.sockets if item.fileno() >= 0]
        if not live:
            raise OSError("The sign-in listener is closed.")
        ready, _, _ = select.select(live, [], [], 0.25)
        if not ready:
            raise TimeoutError
        connection, address = ready[0].accept()
        connection.setblocking(True)
        return connection, address

    def shutdown(self, how: int) -> None:
        for item in self.sockets:
            try:
                item.shutdown(how)
            except OSError:
                pass

    def close(self) -> None:
        for item in self.sockets:
            item.close()


def _listening_socket(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((address, port))
        listener.listen(4)
    except OSError:
        listener.close()
        raise
    listener.settimeout(0.25)
    return listener


def _ipv6_loopback(port: int) -> socket.socket | None:
    """A listener on ``[::1]:port``; None when this computer has no IPv6 loopback, since
    then no other program can take that address either."""
    import errno

    try:
        return _listening_socket(socket.AF_INET6, "::1", port)
    except OSError as error:
        if error.errno in {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT} or not socket.has_ipv6:
            return None
        raise


def bind_loopback(port: int, host_label: str = "127.0.0.1") -> LoopbackListener:
    """Listening sockets on the loopback address only.

    A port another program already listens on fails clearly. On Windows the port is
    taken exclusively, since SO_REUSEADDR there would let a second program bind it too.
    Elsewhere SO_REUSEADDR only lets a new sign-in reuse the port while the last one's
    connection lingers in TIME_WAIT; it never shares a port that is listening. With
    ``host_label`` ``localhost`` the same port is held on ``[::1]`` as well, and a port
    another program holds there fails the same way.
    """
    attempts = 1 if port or host_label != "localhost" else 5
    for _ in range(attempts):
        try:
            first = _listening_socket(socket.AF_INET, "127.0.0.1", port)
        except OSError:
            break
        if host_label != "localhost":
            return LoopbackListener([first])
        try:
            second = _ipv6_loopback(first.getsockname()[1])
        except OSError:
            first.close()
            # A random port that is free on 127.0.0.1 but taken on ::1: pick another.
            continue
        return LoopbackListener([first] if second is None else [first, second])
    if port:
        raise SignInError(
            f"Port {port} on this computer is already in use by another program, so "
            f"Raticode can't receive the sign-in there. Close that program and try "
            f"again, or choose another port and add http://{host_label}:<that port>"
            f"{CALLBACK_PATH} to your app's redirect URLs."
        ) from None
    raise SignInError("Raticode couldn't open a local port for the sign-in.") from None


@dataclass
class _Session:
    id: str
    spec: OAuthPlatform
    account_id: str
    name: str
    options: dict[str, Any]
    client_id: str
    client_secret: str
    port: int
    redirect_uri: str
    authorize_url: str
    state: str
    verifier: str
    deadline: float
    page_id: str = ""
    status: str = "waiting"
    error: str = ""
    opened: bool = False
    account: dict[str, Any] | None = None
    pages: list[dict[str, str]] = field(default_factory=list)
    page_tokens: dict[str, str] = field(default_factory=dict)
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    listener: LoopbackListener | None = None

    def public(self, now: float) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.spec.platform,
            "status": self.status,
            "error": self.error,
            "authorize_url": self.authorize_url if self.status == "waiting" else "",
            "redirect_uri": self.redirect_uri,
            "opened": self.opened,
            "seconds_left": max(0, int(self.deadline - now)) if self.status not in FINAL else 0,
            "account": self.account,
            "pages": list(self.pages),
        }


class SignInManager:
    """The sign-ins of one data folder. Only one runs at a time."""

    def __init__(
        self,
        service: SocialService,
        *,
        http: HttpClient | None = None,
        opener: Callable[[str], bool] | None = None,
        timeout_seconds: float = SIGN_IN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self._http = http
        self.opener = opener or open_in_system_browser
        self.timeout_seconds = timeout_seconds
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            from gofer.core.http import UrllibHttpClient

            self._http = UrllibHttpClient()
        return self._http

    # Public actions, the user's only (gofer.social.api checks the actor) ---------------

    def start(self, body: Mapping[str, Any]) -> dict[str, Any]:
        platform = str(body.get("platform") or "")
        if platform in NO_SIGN_IN:
            raise SignInError(NO_SIGN_IN[platform])
        spec = OAUTH_PLATFORMS.get(platform)
        if spec is None:
            raise SignInError("Sign in works for YouTube, X, LinkedIn and Facebook Page.")
        account_id = str(body.get("id") or "")
        saved: dict[str, str] = {}
        existing: dict[str, Any] | None = None
        if account_id:
            existing = self.service.accounts.get(account_id)
            if existing["platform"] != platform:
                raise SignInError("An account can't change platform.")
            saved = self.service.accounts.read_secrets(account_id)
        client_id = _text(body.get("client_id")) or saved.get("client_id", "")
        client_secret = _text(body.get("client_secret")) or saved.get("client_secret", "")
        if not client_id:
            raise SignInError(f"Paste the client ID of your {spec.label} developer app.")
        if spec.secret_required and not client_secret:
            raise SignInError(f"Paste the client secret of your {spec.label} developer app.")
        for value in (client_id, client_secret):
            if any(char.isspace() for char in value) or len(value) > 512:
                raise SignInError("The client ID and secret can't contain spaces.")
        port = self._port(spec, body.get("port"), existing)
        options = _options(body.get("options"))
        page_id = _text(body.get("page_id")) or str(options.get("page_id") or "")
        if page_id and not page_id.isdigit():
            raise SignInError("A Page ID is only numbers.")
        name = _text(body.get("name"))[:80]
        with self._lock:
            self._sweep()
            for other in self._sessions.values():
                if other.status not in FINAL:
                    self._finish(other, "cancelled", "Another sign-in started.")
            listener = bind_loopback(port, spec.redirect_host)
            port = listener.getsockname()[1]
            verifier, challenge = pkce_pair()
            state = token_source.token_urlsafe(32)
            redirect_uri = spec.redirect_uri(port)
            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": spec.scope_separator.join(spec.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                **spec.extra_params,
            }
            session = _Session(
                id=uuid4().hex,
                spec=spec,
                account_id=account_id,
                name=name,
                options=options,
                client_id=client_id,
                client_secret=client_secret,
                port=port,
                redirect_uri=redirect_uri,
                authorize_url=f"{spec.authorize_url}?{urlencode(params)}",
                state=state,
                verifier=verifier,
                deadline=self._monotonic() + self.timeout_seconds,
                page_id=page_id,
                listener=listener,
            )
            self._sessions[session.id] = session
        threading.Thread(
            target=self._listen, args=(session,), name="social-sign-in", daemon=True
        ).start()
        if body.get("open_browser", True) is not False:
            session.opened = self.opener(session.authorize_url)
        return self.get(session.id)

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status == "choose_page" and self._monotonic() >= session.deadline:
                self._finish(session, "expired", "The sign-in timed out. Start it again.")
            return session.public(self._monotonic())

    def reopen(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status != "waiting":
                raise SignInError("This sign-in isn't waiting for the browser any more.")
            url = session.authorize_url
        opened = self.opener(url)
        with self._lock:
            session.opened = session.opened or opened
        return self.get(session_id)

    def cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status not in FINAL:
                self._finish(session, "cancelled", "You cancelled the sign-in.")
            return session.public(self._monotonic())

    def choose_page(self, session_id: str, page_id: Any) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status != "choose_page":
                raise SignInError("This sign-in isn't waiting for a Page choice.")
            if self._monotonic() >= session.deadline:
                self._finish(session, "expired", "The sign-in timed out. Start it again.")
                return session.public(self._monotonic())
            token = session.page_tokens.get(str(page_id or ""))
            if not token:
                raise SignInError("Choose one of the Pages listed.")
            name = next(item["name"] for item in session.pages if item["id"] == page_id)
        self._save_facebook(session, str(page_id), name, token)
        return self.get(session_id)

    def cancel_all(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                if session.status not in FINAL:
                    self._finish(session, "cancelled", "Raticode stopped the sign-in.")

    # Internals --------------------------------------------------------------------------

    def _session(self, session_id: str) -> _Session:
        self._sweep()
        session = self._sessions.get(session_id)
        if session is None:
            raise SignInError("That sign-in has ended. Start it again.")
        return session

    def _sweep(self) -> None:
        now = self._monotonic()
        for key, session in list(self._sessions.items()):
            if session.status in FINAL and now - session.finished_at > FINISHED_KEEP_SECONDS:
                del self._sessions[key]

    def _port(self, spec: OAuthPlatform, value: Any, existing: Mapping[str, Any] | None) -> int:
        if not spec.fixed_port:
            return 0
        if value in (None, ""):
            saved = (existing or {}).get("sign_in_port")
            return int(saved) if isinstance(saved, int) else DEFAULT_PORT
        try:
            port = int(value)
        except (TypeError, ValueError):
            port = 0
        if not 1024 <= port <= 65535:
            raise SignInError("Choose a port from 1024 to 65535.")
        return port

    def _finish(self, session: _Session, status: str, error: str = "") -> None:
        with self._lock:
            if session.status in FINAL:
                return
            session.status = status
            session.error = error
            session.finished_at = self._monotonic()
            session.cancel.set()
            # Forget everything that could act for the user.
            session.page_tokens.clear()
            session.verifier = ""
            session.client_secret = ""
            self._close(session)
        if status != "done":
            self.service.log(
                "user",
                "account.sign_in_ended",
                {"platform": session.spec.platform, "result": status},
            )

    @staticmethod
    def _close(session: _Session) -> None:
        listener, session.listener = session.listener, None
        if listener is not None:
            # shutdown wakes the listener thread's accept() so the port closes now.
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                listener.close()
            except OSError:
                pass

    def _listen(self, session: _Session) -> None:
        """Serve the loopback port until one callback arrives, the user cancels or time runs out."""
        while not session.cancel.is_set():
            if self._monotonic() >= session.deadline:
                self._finish(
                    session,
                    "expired",
                    "The sign-in timed out after 5 minutes. Start it again when you're ready.",
                )
                return
            listener = session.listener
            if listener is None:
                return
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(5)
                target = _read_target(connection)
                path = urlsplit(target or "").path
                if target is None or path != CALLBACK_PATH:
                    # Browsers also ask for /favicon.ico. Only the callback ends the wait.
                    _respond(connection, 404, "Not found", "This address only takes a sign-in.")
                    continue
                query = parse_qs(urlsplit(target).query)
                if not _state_matches(query, session.state):
                    # Any local program or web page can reach this port; a reply that isn't
                    # for this sign-in is turned away and the real one is still awaited.
                    _respond(
                        connection,
                        400,
                        "Not this sign-in",
                        "This reply isn't for the sign-in Raticode is waiting for.",
                    )
                    continue
                with self._lock:
                    if session.status != "waiting":
                        _respond(connection, 410, "Sign-in ended", session.error or "")
                        return
                    # One callback only: stop listening before doing anything with it.
                    session.status = "exchanging"
                    self._close(session)
                title, message, ok = self._callback(session, query)
                _respond(connection, 200 if ok else 400, title, message)
            return

    def _callback(self, session: _Session, query: Mapping[str, list[str]]) -> tuple[str, str, bool]:
        def one(key: str) -> str:
            values = query.get(key) or []
            return values[0] if len(values) == 1 else ""

        label = session.spec.label
        if not hmac.compare_digest(one("state").encode(), session.state.encode()):
            self._finish(
                session,
                "failed",
                "The reply didn't come from the sign-in Raticode started (its state didn't "
                "match), so nothing was saved. Start the sign-in again.",
            )
            return "Sign-in refused", session.error, False
        error = one("error")
        if error:
            if error in {"access_denied", "user_denied", "user_cancelled_login"}:
                self._finish(session, "cancelled", f"You didn't allow access on {label}'s page.")
                return "Sign-in cancelled", session.error + " You can close this tab.", False
            detail = redact(one("error_description") or error)[:200]
            self._finish(session, "failed", f"{label} refused the sign-in: {detail}")
            return "Sign-in failed", session.error, False
        code = one("code")
        if not code:
            self._finish(session, "failed", f"{label} didn't send a sign-in code.")
            return "Sign-in failed", session.error, False
        try:
            anyio.run(self._exchange, session, code)
        except SignInError as exc:
            self._finish(session, "failed", str(exc))
            return "Sign-in failed", str(exc), False
        except SocialAccountError as exc:
            self._finish(session, "failed", str(exc))
            return "Sign-in failed", str(exc), False
        except Exception:
            self._finish(session, "failed", f"Raticode couldn't finish signing in to {label}.")
            return "Sign-in failed", session.error, False
        if session.status == "choose_page":
            return (
                "Almost done",
                "Go back to Raticode and choose which Page to post to. You can close this tab.",
                True,
            )
        return (
            "Signed in",
            f"Raticode saved your {label} sign-in. You can close this tab and go back to Raticode.",
            True,
        )

    async def _exchange(self, session: _Session, code: str) -> None:
        spec = session.spec
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": session.redirect_uri,
            "code_verifier": session.verifier,
            "client_id": session.client_id,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if spec.basic_auth and session.client_secret:
            pair = f"{session.client_id}:{session.client_secret}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
        elif session.client_secret:
            form["client_secret"] = session.client_secret
        data = await self._post(spec.token_url, form, headers, spec.label)
        access = _text(data.get("access_token"))
        if not access:
            raise SignInError(f"{spec.label} didn't send an access token.")
        refresh = _text(data.get("refresh_token"))
        if spec.platform == "facebook":
            await self._facebook_pages(session, access)
            return
        secrets: dict[str, str] = {"access_token": access, "client_id": session.client_id}
        if session.client_secret:
            secrets["client_secret"] = session.client_secret
        if refresh:
            secrets["refresh_token"] = refresh
        body: dict[str, Any] = {"platform": spec.platform, "secrets": secrets}
        if spec.platform == "youtube" and not refresh:
            raise SignInError(
                "Google didn't send a refresh token, so Raticode couldn't keep the sign-in. "
                "Remove the app's access at myaccount.google.com/permissions and sign in again."
            )
        if spec.platform == "x":
            body["auth"] = "oauth2"
        if spec.platform == "linkedin":
            seconds = data.get("expires_in")
            if isinstance(seconds, int) and seconds > 0:
                body["token_expires_in_days"] = max(1, min(60, seconds // 86400))
        self._save(session, body)

    async def _post(
        self, url: str, form: Mapping[str, str], headers: dict[str, str], label: str
    ) -> dict[str, Any]:
        return await self._call(
            HttpRequest(
                "POST",
                url,
                headers=headers,
                body=urlencode(form).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            label,
        )

    async def _call(self, request: HttpRequest, label: str) -> dict[str, Any]:
        try:
            response = await self.http.send(request)
        except TimeoutError:
            raise SignInError(f"{label} didn't answer in time. Try again.") from None
        except OSError:
            raise SignInError(
                f"Couldn't reach {label}. Check the connection and try again."
            ) from None
        except ValueError as exc:
            raise SignInError(redact(str(exc))[:200]) from None
        data = _json(response)
        if response.status >= 300:
            described = data.get("error_description")
            detail = (
                redact(described.strip())[:300]
                if isinstance(described, str) and described.strip()
                else platform_message(response, f"{label} answered {response.status}.")
            )
            raise SignInError(f"{label} refused the sign-in: {detail}")
        return data

    async def _facebook_pages(self, session: _Session, user_token: str) -> None:
        """Extend the user token, then list the Pages it manages with their Page tokens.

        A Page token read with a long-lived user token doesn't expire, so the user signs
        in once per Page.
        """
        graph = facebook_provider.GRAPH
        query = urlencode(
            {
                "grant_type": "fb_exchange_token",
                "client_id": session.client_id,
                "client_secret": session.client_secret,
                "fb_exchange_token": user_token,
            }
        )
        extended = await self._call(
            HttpRequest(
                "GET", f"{graph}/oauth/access_token?{query}", timeout_seconds=DEFAULT_TIMEOUT
            ),
            "Facebook",
        )
        long_lived = _text(extended.get("access_token"))
        if not long_lived:
            raise SignInError("Facebook didn't extend the sign-in.")
        listing = await self._call(
            HttpRequest(
                "GET",
                f"{graph}/me/accounts?{urlencode(PAGE_FIELDS)}",
                headers={"Authorization": f"Bearer {long_lived}"},
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            "Facebook",
        )
        pages: list[tuple[str, str, str]] = []
        for item in listing.get("data") or []:
            if not isinstance(item, dict):
                continue
            page_id, name, token = (_text(item.get(key)) for key in ("id", "name", "access_token"))
            if page_id.isdigit() and token:
                pages.append((page_id, name[:120] or page_id, token))
        if not pages:
            raise SignInError(
                "Facebook didn't list a Page you manage. Allow the Pages when Facebook asks, "
                "and check that your account has a role on the Page."
            )
        wanted = session.page_id or (pages[0][0] if len(pages) == 1 else "")
        if wanted:
            for page_id, name, token in pages:
                if page_id == wanted:
                    self._save_facebook(session, page_id, name, token)
                    return
            raise SignInError(f"Facebook didn't list Page {wanted} for this sign-in.")
        with self._lock:
            session.pages = [{"id": page_id, "name": name} for page_id, name, _ in pages]
            session.page_tokens = {page_id: token for page_id, _, token in pages}
            session.status = "choose_page"
            session.deadline = self._monotonic() + self.timeout_seconds

    def _save_facebook(self, session: _Session, page_id: str, name: str, token: str) -> None:
        options = {**session.options, "page_id": page_id}
        secrets = {"page_access_token": token, "client_id": session.client_id}
        if session.client_secret:
            secrets["client_secret"] = session.client_secret
        if not session.name and not session.account_id:
            session.name = name
        try:
            self._save(
                session,
                {"platform": "facebook", "secrets": secrets, "options": options},
            )
        except SocialAccountError as exc:
            self._finish(session, "failed", str(exc))
            raise SignInError(str(exc)) from None

    def _save(self, session: _Session, body: dict[str, Any]) -> None:
        accounts = self.service.accounts
        body = {**body, "options": {**session.options, **(body.get("options") or {})}}
        if session.account_id:
            body["id"] = session.account_id
        if session.name:
            body["name"] = session.name
        fields: dict[str, Any] = {"signed_in": True}
        if session.spec.fixed_port:
            fields["sign_in_port"] = session.port
        with self._lock:
            # Cancelled or timed out while the tokens were on their way: save nothing.
            if session.status in FINAL:
                raise SignInError(session.error or "The sign-in ended before it finished.")
            account = accounts.connect(body)
            accounts.mark(account["id"], **fields)
        saved = next((item for item in accounts.list() if item["id"] == account["id"]), account)
        self.service.log(
            "user",
            "account.connected",
            {"account": account["id"], "method": "sign_in", "platform": session.spec.platform},
        )
        with self._lock:
            session.account = saved
            session.account_id = account["id"]
            self._finish(session, "done")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _options(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json(response: HttpResponse) -> dict[str, Any]:
    import json

    try:
        value = json.loads(response.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _state_matches(query: Mapping[str, list[str]], state: str) -> bool:
    """True when the reply carries exactly one ``state`` and it is this sign-in's."""
    values = query.get("state") or []
    return len(values) == 1 and hmac.compare_digest(values[0].encode(), state.encode())


def _read_target(connection: socket.socket, seconds: float = 5.0) -> str | None:
    """The request target of a GET, or None for anything else.

    The whole request line must arrive within ``seconds``: the listener serves one
    connection at a time, so a sender that trickles bytes must not hold it.
    """
    data = b""
    deadline = time.monotonic() + seconds
    try:
        while b"\r\n" not in data and len(data) < MAX_REQUEST_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            connection.settimeout(remaining)
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        return None
    line = data.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    if len(parts) != 3 or parts[0] != "GET" or not parts[1].startswith("/"):
        return None
    return parts[1]


def _respond(connection: socket.socket, status: int, title: str, message: str) -> None:
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 410: "Gone"}.get(status, "OK")
    page = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Raticode · {html.escape(title)}</title>"
        "<body style='font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:4rem auto;"
        "padding:0 1rem;color:#1d1d1f'>"
        f"<h1 style='font-size:1.4rem'>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></body></html>"
    ).encode()
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(page)}\r\n"
        "Cache-Control: no-store\r\n"
        "Referrer-Policy: no-referrer\r\n"
        "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    try:
        connection.sendall(head + page)
    except OSError:
        pass
