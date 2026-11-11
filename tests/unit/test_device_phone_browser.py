"""The desktop browser as a paired phone's browser sees it: its tabs, and pages sent to it."""

from typing import Any

import pytest

from gofer.devices import phone_browser
from gofer.devices.phone_browser import PhoneBrowser, web_url
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone

WINDOW_TABS: list[dict[str, Any]] = [
    {
        "id": "browser:1",
        "title": "  Docs \n home ",
        "url": "https://example.com/docs",
        "active": True,
    },
    {"id": "browser:2", "title": "Preview", "url": "file:///home/me/report.html"},
    {"id": "browser:3", "title": "", "url": "about:blank"},
    {"id": "browser:4", "title": "Home", "url": "raticode://home"},
    {
        "id": "browser:5",
        "title": "Issue",
        "url": "https://github.com/acme/app/issues/1",
        "owner": "rem",
        "label": "Rem",
    },
]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://example.com/a?b=c#d", True),
        ("http://192.168.1.20:5173/", True),
        (" https://example.com ", True),
        ("javascript:alert(1)", False),
        ("file:///etc/passwd", False),
        ("intent://scan#Intent;scheme=zxing;end", False),
        ("https://user:pw@example.com/", False),
        ("https://", False),
        ("https://example.com/\nx", False),
        ("https://example.com:99999/", False),
        ("https://" + "a" * 2050, False),
        (None, False),
    ],
)
def test_only_plain_web_addresses_cross(value: Any, expected: bool) -> None:
    assert (web_url(value) is not None) is expected


def test_phone_reads_web_tabs_only_once_the_window_publishes_them(phone):
    app, peer, _, send = phone
    assert send("desktop.read", "browser.tabs")["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["browser"] is True
    gone = send("desktop.read", "browser.tabs")["value"]
    assert gone["available"] is False and gone["tabs"] == []
    assert app.desktop.exchange_browser(WINDOW_TABS, "https://duckduckgo.com/?q={query}", []) == {
        "opens": []
    }
    view = send("desktop.read", "browser.tabs")["value"]
    assert view["available"] is True and view["stale"] is False
    assert view["search_url"] == "https://duckduckgo.com/?q={query}"
    # Previews, local files, blank and home tabs stay on the computer.
    assert [(t["tab_id"], t["owner"]) for t in view["tabs"]] == [
        ("browser:1", "you"),
        ("browser:5", "rem"),
    ]
    assert view["tabs"][0]["title"] == "Docs home" and view["tabs"][0]["active"] is True
    assert view["tabs"][1]["label"] == "Rem"


def test_unchanged_tabs_answer_with_their_digest(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.exchange_browser(WINDOW_TABS, "", [])
    first = send("desktop.read", "browser.tabs")["value"]
    app.desktop.exchange_browser(WINDOW_TABS, "", [])
    again = send("desktop.read", "browser.tabs", {"known": first["digest"]})["value"]
    assert again == {"unchanged": True, "digest": first["digest"]}


def test_a_sent_page_opens_once_in_the_window(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    assert send("desktop.action", "browser.open", {"url": "https://example.com/"})["code"] == (
        "unavailable"
    )
    app.desktop.exchange_browser([], "", [])
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://u:p@example.com/", 7):
        assert send("desktop.action", "browser.open", {"url": bad})["code"] == "invalid"
    sent = send("desktop.action", "browser.open", {"url": "https://example.com/a"})
    assert sent["ok"] and sent["value"]["queued"] is True
    opens = app.desktop.exchange_browser([], "", [])["opens"]
    assert [o["url"] for o in opens] == ["https://example.com/a"]
    # Offered again until the window says it opened it.
    assert app.desktop.exchange_browser([], "", [])["opens"] == opens
    assert send("desktop.read", "browser.tabs")["value"]["pending"] == 1
    assert app.desktop.exchange_browser([], "", [opens[0]["id"]]) == {"opens": []}


def test_pages_wait_for_a_window_that_stopped_publishing() -> None:
    now = [1000.0]
    browser = PhoneBrowser(clock=lambda: now[0])
    browser.exchange(WINDOW_TABS, "", [])
    now[0] += phone_browser.STALE_AFTER + 1
    view = browser.view()
    assert view["available"] and view["stale"]
    now[0] += phone_browser.GONE_AFTER
    assert browser.view()["available"] is False
    with pytest.raises(phone_browser.BrowserRefused) as refused:
        browser.open({"url": "https://example.com/"}, "r1")
    assert refused.value.code == "unavailable"


def test_queue_is_bounded_and_repeats_are_one_page() -> None:
    browser = PhoneBrowser()
    browser.exchange([], "", [])
    browser.open({"url": "https://example.com/"}, "same")
    browser.open({"url": "https://example.com/"}, "same")
    assert len(browser.exchange([], "", [])["opens"]) == 1
    for index in range(phone_browser.MAX_PENDING - 1):
        browser.open({"url": f"https://example.com/{index}"}, f"r{index}")
    with pytest.raises(phone_browser.BrowserRefused) as refused:
        browser.open({"url": "https://example.com/full"}, "full")
    assert refused.value.code == "conflict"


def test_tab_list_and_search_template_are_bounded() -> None:
    browser = PhoneBrowser()
    many = [{"id": f"t{i}", "title": "x" * 500, "url": f"https://e.com/{i}"} for i in range(90)]
    browser.exchange(many, "javascript:{query}", [])
    view = browser.view()
    assert len(view["tabs"]) == phone_browser.MAX_TABS
    assert len(view["tabs"][0]["title"]) == phone_browser.MAX_TITLE
    assert view["search_url"] == ""
    browser.exchange("not a list", None, None)
    assert browser.view()["tabs"] == []


def test_local_api_offers_no_pages_while_no_phone_uses_the_desktop(tmp_path):
    from gofer.devices.control import DeviceControl
    from tests.unit.test_device_pairing import TestSecrets

    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        assert control.application is not None
        control.application.desktop.browser.pending.append({"id": "r", "url": "https://e.com/"})
        # Without "Use Raticode from this phone" on any phone, the window is told nothing.
        assert control.action(
            {"action": "browser_exchange", "tabs": WINDOW_TABS, "search_url": "", "opened": []}
        ) == {"opens": []}
        assert control.application.desktop.browser.tabs == []
    finally:
        control.close()


def test_tab_list_says_which_engine_runs_the_window_tabs(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.exchange_browser(WINDOW_TABS, "", [], "firefox")
    assert send("desktop.read", "browser.tabs")["value"]["engine"] == "firefox"
    app.desktop.exchange_browser(WINDOW_TABS, "", [], "chrome")
    assert send("desktop.read", "browser.tabs")["value"]["engine"] == "chrome"
    app.desktop.exchange_browser(WINDOW_TABS, "", [], "edge")
    assert send("desktop.read", "browser.tabs")["value"]["engine"] == "edge"
    # Anything else, including an older window that sends nothing, is Raticode's engine.
    for engine in (None, "edge-of-nowhere", "msedge", ["chrome"], 1):
        app.desktop.exchange_browser(WINDOW_TABS, "", [], engine)
        assert send("desktop.read", "browser.tabs")["value"]["engine"] == "raticode"


def test_each_tab_names_its_own_browser_and_older_windows_send_none(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    tabs = [
        {"id": "browser:1", "url": "https://music.example/", "engine": "gecko"},
        {"id": "browser:2", "url": "http://localhost:5173/", "engine": "edge"},
        {"id": "browser:3", "url": "https://example.com/", "engine": "raticode"},
        {"id": "browser:4", "url": "https://example.org/", "engine": "netscape"},
        {"id": "browser:5", "url": "https://example.net/", "engine": ["chrome"]},
        {"id": "browser:6", "url": "https://example.edu/"},
    ]
    app.desktop.exchange_browser(tabs, "", [], "chrome")
    view = send("desktop.read", "browser.tabs")["value"]
    # The view's engine stays the default browser, for phones that read only it.
    assert view["engine"] == "chrome"
    assert [t.get("engine") for t in view["tabs"]] == [
        "gecko",
        "edge",
        "raticode",
        None,
        None,
        None,
    ]


def test_phone_can_switch_the_computers_web_browser_and_nothing_else_there(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    window: dict[str, Any] = {"browser": {"engine": "raticode"}, "appearance": {"theme": "light"}}
    app.desktop.exchange_settings(window, [])
    view = send("desktop.read", "settings.get")["value"]
    assert view["values"]["browser.engine"] == "raticode"
    engine = next(s for s in view["settings"] if s["key"] == "browser.engine")
    assert engine["group"] == "Browser"
    # The values stay as before; only the names changed, so older phones keep working.
    assert engine["label"] == "Default browser for new tabs"
    assert engine["options"] == [
        ["raticode", "Raticode (chromium)"],
        ["firefox", "Firefox"],
        ["chrome", "Chrome"],
        ["edge", "Edge"],
    ]
    for bad in ("netscape", "", True):
        result = send("desktop.action", "settings.update", {"changes": {"browser.engine": bad}})
        assert result["code"] == "invalid"
    for other in ("browser.remConfirm", "browser.remSites", "browser.homepage"):
        result = send("desktop.action", "settings.update", {"changes": {other: "x"}})
        assert result["code"] == "invalid"
    sent = send("desktop.action", "settings.update", {"changes": {"browser.engine": "firefox"}})
    assert sent["ok"]
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[0]["values"] == {"browser.engine": "firefox"}
    sent = send("desktop.action", "settings.update", {"changes": {"browser.engine": "chrome"}})
    assert sent["ok"]
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[-1]["values"] == {"browser.engine": "chrome"}
    sent = send("desktop.action", "settings.update", {"changes": {"browser.engine": "edge"}})
    assert sent["ok"]
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[-1]["values"] == {"browser.engine": "edge"}
