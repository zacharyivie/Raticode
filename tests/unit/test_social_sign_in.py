"""Posts sign-in through a loopback listener, against fake platforms only.

The browser is played by a plain HTTP request to the loopback address the sign-in
listens on. Token endpoints are a fake HTTP client; no real platform is reached and every
client ID, secret and token here is made up.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from gofer.core.http import HttpRequest, HttpResponse
from gofer.social import api as social_api
from gofer.social.service import SocialService
from gofer.social.signin import (
    CALLBACK_PATH,
    FINAL,
    OAUTH_PLATFORMS,
    SignInError,
    SignInManager,
    bind_loopback,
    sign_in_info,
)
from gofer.ui.headless import agent_request_refusal


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


class FakeHttp:
    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []
        self.replies: list[HttpResponse] = []

    def reply(self, status: int, body: Any) -> None:
        self.replies.append(HttpResponse(status, {}, json.dumps(body).encode()))

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected request to {request.url}")
        return self.replies.pop(0)


class Browser:
    """Records the pages Raticode asked the system browser to open."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        return True


@pytest.fixture
def secrets() -> FakeSecrets:
    return FakeSecrets()


@pytest.fixture
def service(tmp_path: Path, secrets: FakeSecrets) -> SocialService:
    return SocialService(tmp_path / "data", secrets=secrets)


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def browser() -> Browser:
    return Browser()


@pytest.fixture
def manager(service: SocialService, http: FakeHttp, browser: Browser) -> Iterator[SignInManager]:
    manager = SignInManager(service, http=http, opener=browser)
    service._sign_ins = manager
    yield manager
    manager.cancel_all()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def port_of(session: dict[str, Any]) -> int:
    return int(urlsplit(session["redirect_uri"]).port or 0)


def visit(port: int, path: str) -> tuple[int, str]:
    """What the user's browser does when the platform sends it back."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def callback(session: dict[str, Any], **params: str) -> tuple[int, str]:
    return visit(port_of(session), f"{CALLBACK_PATH}?{urlencode(params)}")


def state_of(session: dict[str, Any]) -> str:
    return str(parse_qs(urlsplit(session["authorize_url"]).query)["state"][0])


def wait(manager: SignInManager, session_id: str, until: frozenset[str] = FINAL) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = manager.get(session_id)
        if current["status"] in until:
            return current
        time.sleep(0.02)
    raise AssertionError(f"sign-in stayed {manager.get(session_id)['status']}")


def port_is_closed(port: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.05)
    return False


def form_of(request: HttpRequest) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs((request.body or b"").decode()).items()}


def test_youtube_sign_in_uses_pkce_state_and_a_random_loopback_port(
    manager: SignInManager, service: SocialService, http: FakeHttp, browser: Browser
) -> None:
    session = manager.start(
        {"platform": "youtube", "client_id": "fake-client.apps", "client_secret": "fake-secret"}
    )
    assert session["status"] == "waiting" and session["opened"] is True
    assert browser.opened == [session["authorize_url"]]
    url = urlsplit(session["authorize_url"])
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["response_type"] == "code" and query["client_id"] == "fake-client.apps"
    assert query["code_challenge_method"] == "S256" and len(query["state"]) >= 32
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["scope"] == "https://www.googleapis.com/auth/youtube.upload"
    assert query["redirect_uri"] == session["redirect_uri"]
    assert session["redirect_uri"].startswith("http://127.0.0.1:")
    assert "fake-secret" not in json.dumps(session)

    # A favicon request doesn't end the wait; only the callback does.
    assert visit(port_of(session), "/favicon.ico")[0] == 404
    assert manager.get(session["id"])["status"] == "waiting"

    http.reply(200, {"access_token": "fake-access", "refresh_token": "fake-refresh"})
    status, page = callback(session, code="fake-code", state=state_of(session))
    assert status == 200 and "Signed in" in page and "fake-code" not in page
    done = wait(manager, session["id"])
    assert done["status"] == "done" and done["account"]["platform"] == "youtube"
    assert done["account"]["signed_in"] is True

    token = form_of(http.requests[0])
    assert http.requests[0].url == "https://oauth2.googleapis.com/token"
    verifier = token["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge.decode() == query["code_challenge"]
    assert token["code"] == "fake-code" and token["redirect_uri"] == session["redirect_uri"]
    assert token["grant_type"] == "authorization_code"

    account_id = done["account"]["id"]
    assert service.accounts.read_secrets(account_id) == {
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "client_id": "fake-client.apps",
        "client_secret": "fake-secret",
    }
    assert "fake-refresh" not in (service.root / "accounts.json").read_text()
    # One callback, then the port is closed.
    assert port_is_closed(port_of(session))


def test_a_reply_with_the_wrong_state_is_turned_away_and_the_real_one_still_signs_in(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    """Any local program or web page can reach the port, so a stray reply can't end the wait."""
    session = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    for stray in ({"code": "stolen-code", "state": "not-the-state"}, {"code": "no-state"}):
        status, page = callback(session, **stray)
        assert status == 400 and "for the sign-in Raticode is waiting for" in page
    assert manager.get(session["id"])["status"] == "waiting"
    assert http.requests == [] and service.accounts.list() == []

    http.reply(200, {"access_token": "fake-access", "refresh_token": "fake-refresh"})
    status, _ = callback(session, code="real-code", state=state_of(session))
    assert status == 200
    assert wait(manager, session["id"])["status"] == "done"
    assert form_of(http.requests[0])["code"] == "real-code"
    assert port_is_closed(port_of(session))


def test_a_sender_that_trickles_bytes_cant_hold_the_listener() -> None:
    """The listener serves one connection at a time, so a request line gets 2 s in all."""
    from gofer.social import signin

    server, client = socket.socketpair()
    stop = threading.Event()

    def trickle() -> None:
        # A byte every 0.5 s would reset a per-read timeout forever.
        while not stop.is_set():
            try:
                client.sendall(b"x")
            except OSError:
                return
            time.sleep(0.5)

    sender = threading.Thread(target=trickle, daemon=True)
    sender.start()
    try:
        started = time.monotonic()
        assert signin._read_target(server, seconds=2.0) is None
        assert time.monotonic() - started < 4
    finally:
        stop.set()
        server.close()
        client.close()
        sender.join(timeout=2)


def test_declining_on_the_consent_page_cancels(
    manager: SignInManager, service: SocialService
) -> None:
    session = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    status, _ = callback(session, error="access_denied", state=state_of(session))
    assert status == 400
    ended = wait(manager, session["id"])
    assert ended["status"] == "cancelled" and "didn't allow" in ended["error"]
    assert service.accounts.list() == []


def test_cancel_and_timeout_close_the_listener(service: SocialService, http: FakeHttp) -> None:
    manager = SignInManager(service, http=http, opener=lambda url: False)
    session = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    assert session["opened"] is False  # the dashboard then offers the link to copy
    cancelled = manager.cancel(session["id"])
    assert cancelled["status"] == "cancelled" and cancelled["authorize_url"] == ""
    assert port_is_closed(port_of(session))

    quick = SignInManager(service, http=http, opener=lambda url: True, timeout_seconds=0.3)
    session = quick.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    ended = wait(quick, session["id"])
    assert ended["status"] == "expired" and "timed out" in ended["error"]
    assert port_is_closed(port_of(session))
    assert http.requests == []


def test_starting_again_cancels_the_earlier_sign_in(manager: SignInManager) -> None:
    first = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    second = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    assert manager.get(first["id"])["status"] == "cancelled"
    assert manager.get(second["id"])["status"] == "waiting"


def test_x_uses_a_fixed_127_port_and_a_taken_port_fails_clearly(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        with pytest.raises(SignInError, match=f"Port {busy}.*in use"):
            manager.start({"platform": "x", "client_id": "x-client", "port": busy})
    assert service.accounts.list() == []

    port = free_port()
    session = manager.start({"platform": "x", "client_id": "x-client", "port": port})
    assert session["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
    query = parse_qs(urlsplit(session["authorize_url"]).query)
    assert urlsplit(session["authorize_url"]).netloc == "x.com"
    assert query["scope"] == ["tweet.read tweet.write users.read offline.access"]
    http.reply(200, {"access_token": "x-access", "refresh_token": "x-refresh"})
    callback(session, code="x-code", state=state_of(session))
    done = wait(manager, session["id"])
    assert done["status"] == "done"
    # A public client (no secret) sends its client ID in the form, with no Basic auth.
    request = http.requests[0]
    assert "Authorization" not in request.headers and form_of(request)["client_id"] == "x-client"
    account = service.accounts.get(done["account"]["id"])
    assert account["auth"] == "oauth2" and account["sign_in_port"] == port
    assert service.accounts.read_secrets(account["id"]) == {
        "access_token": "x-access",
        "refresh_token": "x-refresh",
        "client_id": "x-client",
    }

    # Signing in again reuses the saved client ID and port: one click.
    again = manager.start({"platform": "x", "id": account["id"], "client_secret": "x-secret"})
    assert again["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
    http.reply(200, {"access_token": "x-access-2", "refresh_token": "x-refresh-2"})
    callback(again, code="x-code-2", state=state_of(again))
    assert wait(manager, again["id"])["status"] == "done"
    assert http.requests[1].headers["Authorization"].startswith("Basic ")
    assert "client_secret" not in form_of(http.requests[1])
    assert service.accounts.read_secrets(account["id"])["access_token"] == "x-access-2"
    assert len(service.accounts.list()) == 1


def test_x_sign_in_replaces_api_keys_with_oauth(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    keys = service.accounts.connect(
        {
            "platform": "x",
            "secrets": {
                "api_key": "k",
                "api_secret": "s",
                "access_token": "t",
                "access_token_secret": "ts",
            },
        }
    )
    with pytest.raises(SignInError, match="client ID"):
        manager.start({"platform": "x", "id": keys["id"], "port": free_port()})
    session = manager.start(
        {"platform": "x", "id": keys["id"], "client_id": "x-client", "port": free_port()}
    )
    http.reply(200, {"access_token": "x-access"})
    callback(session, code="c", state=state_of(session))
    assert wait(manager, session["id"])["status"] == "done"
    assert service.accounts.read_secrets(keys["id"]) == {
        "access_token": "x-access",
        "client_id": "x-client",
    }


def test_linkedin_records_its_expiry_and_keeps_the_app_for_next_time(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    with pytest.raises(SignInError, match="client secret"):
        manager.start({"platform": "linkedin", "client_id": "li"})
    session = manager.start(
        {"platform": "linkedin", "client_id": "li", "client_secret": "li-s", "port": free_port()}
    )
    assert urlsplit(session["authorize_url"]).netloc == "www.linkedin.com"
    http.reply(200, {"access_token": "li-token", "expires_in": 5184000})
    callback(session, code="c", state=state_of(session))
    done = wait(manager, session["id"])
    assert done["status"] == "done" and done["account"]["expires_in_days"] in (59, 60)
    assert form_of(http.requests[0])["client_secret"] == "li-s"
    assert service.accounts.read_secrets(done["account"]["id"])["client_id"] == "li"


def test_token_errors_are_shown_without_secrets(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    session = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    http.reply(400, {"error": "invalid_grant", "error_description": "Bad code"})
    status, page = callback(session, code="c", state=state_of(session))
    ended = wait(manager, session["id"])
    assert status == 400 and ended["status"] == "failed"
    assert "Bad code" in ended["error"] and service.accounts.list() == []

    session = manager.start({"platform": "youtube", "client_id": "c", "client_secret": "s"})
    http.reply(200, {"access_token": "only-access"})
    callback(session, code="c", state=state_of(session))
    assert "refresh token" in wait(manager, session["id"])["error"]


def test_facebook_lists_pages_and_saves_the_one_you_choose(
    manager: SignInManager, service: SocialService, http: FakeHttp
) -> None:
    session = manager.start(
        {"platform": "facebook", "client_id": "123", "client_secret": "fb-s", "port": free_port()}
    )
    assert session["redirect_uri"].startswith("http://localhost:")
    query = parse_qs(urlsplit(session["authorize_url"]).query)
    assert query["scope"] == ["pages_show_list,pages_read_engagement,pages_manage_posts"]
    http.reply(200, {"access_token": "short-user"})
    http.reply(200, {"access_token": "long-user"})
    http.reply(
        200,
        {
            "data": [
                {"id": "111", "name": "Raticode", "access_token": "page-111"},
                {"id": "222", "name": "Side project", "access_token": "page-222"},
            ]
        },
    )
    callback(session, code="fb-code", state=state_of(session))
    choosing = wait(manager, session["id"], FINAL | {"choose_page"})
    assert choosing["status"] == "choose_page"
    assert choosing["pages"] == [
        {"id": "111", "name": "Raticode"},
        {"id": "222", "name": "Side project"},
    ]
    assert "page-111" not in json.dumps(choosing)
    extend = parse_qs(urlsplit(http.requests[1].url).query)
    assert extend["grant_type"] == ["fb_exchange_token"] and extend["fb_exchange_token"] == [
        "short-user"
    ]
    assert http.requests[2].headers["Authorization"] == "Bearer long-user"
    with pytest.raises(SignInError):
        manager.choose_page(session["id"], "999")
    done = manager.choose_page(session["id"], "222")
    assert done["status"] == "done"
    assert done["account"]["name"] == "Side project"
    assert done["account"]["options"]["page_id"] == "222"
    assert service.accounts.read_secrets(done["account"]["id"])["page_access_token"] == "page-222"


def test_facebook_with_a_page_id_saves_it_straight_away(
    manager: SignInManager, http: FakeHttp
) -> None:
    session = manager.start(
        {
            "platform": "facebook",
            "client_id": "123",
            "client_secret": "fb-s",
            "page_id": "222",
            "port": free_port(),
        }
    )
    http.reply(200, {"access_token": "short-user"})
    http.reply(200, {"access_token": "long-user"})
    http.reply(
        200,
        {
            "data": [
                {"id": "111", "name": "A", "access_token": "page-111"},
                {"id": "222", "name": "B", "access_token": "page-222"},
            ]
        },
    )
    callback(session, code="c", state=state_of(session))
    assert wait(manager, session["id"])["account"]["options"]["page_id"] == "222"


def test_discord_has_no_sign_in_and_the_snapshot_says_so(service: SocialService) -> None:
    with pytest.raises(SignInError, match="webhook"):
        service.sign_ins.start({"platform": "discord"})
    platforms = {item["id"]: item for item in service.snapshot()["platforms"]}
    assert platforms["discord"]["sign_in"]["available"] is False
    for platform in ("youtube", "x", "linkedin", "facebook"):
        assert platforms[platform]["sign_in"]["available"] is True
    assert platforms["youtube"]["sign_in"]["fixed_port"] is False
    assert platforms["x"]["sign_in"]["redirect_host"] == "127.0.0.1"
    assert sign_in_info("patreon") is None


def test_no_client_id_or_secret_ships_with_raticode() -> None:
    source = Path(__file__).parents[2] / "src/gofer/social/signin.py"
    text = source.read_text()
    for spec in OAUTH_PLATFORMS.values():
        assert "client_id" not in spec.extra_params and "client_secret" not in spec.extra_params
    assert "apps.googleusercontent.com" not in text


def test_the_listener_binds_loopback_only() -> None:
    listener = bind_loopback(0)
    try:
        assert listener.getsockname()[0] == "127.0.0.1"
    finally:
        listener.close()


def _ipv6_loopback_works() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_loopback_works(), reason="No IPv6 loopback here.")
def test_a_localhost_redirect_holds_ipv6_loopback_too_so_no_one_else_can() -> None:
    """Browsers try localhost as ::1 first; another program listening there on the
    sign-in's port would receive the callback instead of Raticode."""
    listener = bind_loopback(0, "localhost")
    try:
        port = listener.getsockname()[1]
        squatter = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        squatter.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        with squatter, pytest.raises(OSError):
            squatter.bind(("::1", port))
        # Raticode itself takes the callback on either address.
        for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.connect((host, port))
                connection, _ = listener.accept()
                connection.close()
    finally:
        listener.close()


@pytest.mark.skipif(not _ipv6_loopback_works(), reason="No IPv6 loopback here.")
def test_a_localhost_port_taken_on_ipv6_loopback_fails_clearly() -> None:
    squatter = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    squatter.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    with squatter:
        squatter.bind(("::1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        with pytest.raises(SignInError, match="already in use"):
            bind_loopback(port, "localhost")
        # The IPv4 side was let go again.
        bind_loopback(port).close()


def test_only_the_user_can_sign_in(service: SocialService, manager: SignInManager) -> None:
    assert agent_request_refusal("POST", "/api/social/accounts/sign-in") is not None
    assert agent_request_refusal("DELETE", "/api/social/accounts/sign-in/abc") is not None
    with pytest.raises(social_api.SocialApiError) as refused:
        social_api.handle(service, "POST", ["accounts", "sign-in"], "rem", {"platform": "youtube"})
    assert refused.value.status == 403
    started = social_api.handle(
        service,
        "POST",
        ["accounts", "sign-in"],
        "user",
        {"platform": "youtube", "client_id": "c", "client_secret": "s"},
    )
    assert (
        social_api.handle(service, "GET", ["accounts", "sign-in", started["id"]], "user", {})[
            "status"
        ]
        == "waiting"
    )
    cancelled = social_api.handle(
        service, "DELETE", ["accounts", "sign-in", started["id"]], "user", {}
    )
    assert cancelled["status"] == "cancelled"
    with pytest.raises(social_api.SocialApiError) as missing:
        social_api.handle(service, "GET", ["accounts", "sign-in", "nope"], "user", {})
    assert missing.value.status == 400
