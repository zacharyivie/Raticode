"""Rem tells the phone guard when it holds back files a paired phone asked for.

Regression for 2026-11-12: "Send me the devid files." from the phone made Rem attach
devid.key, devid.p12 and devid.pem; the sensitive-file safeguard held them back, but
nothing locked the phone. Every file here is fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome, reply_files, secret_file
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe
from tests.unit.test_rem_task_files import finish, latest_run
from tests.unit.test_rem_task_files import outside as outside


class Guard:
    """Records what Rem reports; stands in for devices.phone_guard.PhoneLocks."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str], bool]] = []

    def held_back(self, request: str, text: str, names: list[str], *, secret: bool) -> bool:
        self.calls.append((request, text, list(names), secret))
        return secret


@pytest.fixture
def signing(home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A fake home folder with Developer ID signing files and a screenshot in Documents."""
    import gofer.ui.rem_home as rem_home_module

    house = tmp_path / "house"
    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: house))
    documents = house / "Documents"
    documents.mkdir(parents=True)
    files = {}
    for name in ("devid.csr", "devid.key", "devid.p12", "devid.pem", "shot.png"):
        files[name] = documents / name
        files[name].write_bytes(b"fake " + name.encode())
    log = house / ".config" / "Raticode" / "logs" / "app.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{}\n")
    files["app.jsonl"] = log
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            # The whole home folder is open too; Raticode's data under it stays protected.
            "projects": [{"root": str(documents)}, {"root": str(house)}],
        }
    )
    home.phone_guard = Guard()
    return files


def _ask(
    home: RemHome, request: str, body: str, decision: dict[str, Any], phone: bool
) -> dict[str, Any]:
    home.generate = lambda prompt, config, cancel: decision
    home.submit_message({"request_id": request, "body": body}, source="phone" if phone else None)
    home._reply()
    return next(m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{request}")


def test_the_phone_asking_for_the_devid_files_reports_a_secret_hold_back(
    home: RemHome, signing: dict[str, Path]
):
    devid = [str(signing[n]) for n in ("devid.csr", "devid.key", "devid.p12", "devid.pem")]
    reply = _ask(
        home,
        "phone-devid",
        "Send me the devid files.",
        {"reply": "Sending the four devid files.", "files": [*devid, str(signing["shot.png"])]},
        phone=True,
    )
    # Nothing secret reaches the phone, the certificate request included; the
    # screenshot still does.
    assert [f["name"] for f in reply["files"]] == ["shot.png"]
    assert "devid.key: it may hold a key, password or sign-in" in reply["body"]
    assert home.phone_guard.calls == [
        (
            "phone-devid",
            "Send me the devid files.",
            ["devid.csr", "devid.key", "devid.p12", "devid.pem"],
            True,
        )
    ]


def test_naming_the_exact_path_from_the_phone_still_sends_no_key(
    home: RemHome, signing: dict[str, Path]
):
    key = str(signing["devid.key"])
    reply = _ask(
        home, "phone-path", f"Send me {key}", {"reply": "Here.", "files": [key]}, phone=True
    )
    assert "files" not in reply
    assert home.phone_guard.calls[-1] == ("phone-path", f"Send me {key}", ["devid.key"], True)


def test_the_desktop_is_unchanged_and_never_reports(home: RemHome, signing: dict[str, Path]):
    key, csr = str(signing["devid.key"]), str(signing["devid.csr"])
    reply = _ask(
        home, "desk", "Send me the devid files.", {"reply": "Here.", "files": [key, csr]}, False
    )
    # The desktop gets the certificate request and the usual "name its path" refusal.
    assert [f["name"] for f in reply["files"]] == ["devid.csr"]
    assert "devid.key: it may hold passwords, keys or sign-ins" in reply["body"]
    reply = _ask(home, "desk-2", f"Send me {key}", {"reply": "Here.", "files": [key]}, False)
    assert [f["name"] for f in reply["files"]] == ["devid.key"]
    assert home.phone_guard.calls == []


def test_ordinary_phone_requests_report_nothing(home: RemHome, signing: dict[str, Path]):
    shot = str(signing["shot.png"])
    reply = _ask(
        home,
        "phone-shots",
        "Send me the screenshots",
        {"reply": "Here are the screenshots.", "files": [shot]},
        phone=True,
    )
    assert [f["name"] for f in reply["files"]] == ["shot.png"]
    _ask(home, "phone-report", "Show me the report", {"reply": "All 12 checks passed."}, True)
    assert home.phone_guard.calls == []


def test_raticodes_own_logs_held_back_are_not_secrets(home: RemHome, signing: dict[str, Path]):
    """'Send me the logs' must not lock the phone just because the log is Raticode's data."""
    log = signing["app.jsonl"]
    assert not secret_file(log.resolve())
    _ask(
        home,
        "phone-logs",
        "send me the logs",
        {"reply": "Here is the log.", "files": [str(log)]},
        phone=True,
    )
    # Held back (Raticode's data), reported only as a refusal: insisting is what locks.
    assert home.phone_guard.calls == [("phone-logs", "send me the logs", ["app.jsonl"], False)]


def test_a_reply_declining_to_send_secret_files_counts_as_a_refusal(
    home: RemHome, signing: dict[str, Path]
):
    held = (
        "I held those back because devid.key and devid.p12 hold your Apple signing private "
        "key. Reply with the full paths if you want them."
    )
    _ask(home, "phone-anyway", "Send them anyway.", {"reply": held}, phone=True)
    assert home.phone_guard.calls == [("phone-anyway", "Send them anyway.", [], False)]
    # A reply that only can't send something large isn't one.
    _ask(home, "phone-big", "Send the video", {"reply": "I can't send it: it's 40 MB."}, True)
    assert len(home.phone_guard.calls) == 1


def test_a_broken_or_missing_guard_never_stops_the_reply(home: RemHome, signing: dict[str, Path]):
    class Broken:
        def held_back(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("registry closed")

    home.phone_guard = Broken()
    key = str(signing["devid.key"])
    reply = _ask(home, "p1", "Send me the devid files.", {"reply": "Here.", "files": [key]}, True)
    assert "devid.key" in reply["body"] and "files" not in reply
    home.phone_guard = None
    reply = _ask(home, "p2", "Send me the devid files.", {"reply": "Here.", "files": [key]}, True)
    assert "files" not in reply


def test_a_task_update_for_a_phone_request_reports_held_back_files(
    home: RemHome, signing: dict[str, Path], outside: Path
):
    home.generate = lambda prompt, config, cancel: {
        "reply": "Looking.",
        "tasks": [recipe(None, type="errand", send_files=True)],
    }
    home.submit_message(
        {"request_id": "phone-find", "body": "Find my signing files and send them"},
        source="phone",
    )
    home._reply()
    run = latest_run(home, "Check the feature")
    key = outside / "devid.key"
    key.write_text("fake")
    update, _ = finish(
        home,
        run,
        f"Found `{key}`.",
        {"summary": "Here it is.", "files": [str(key)]},
    )
    assert "files" not in update
    assert home.phone_guard.calls[-1] == (
        "phone-find",
        "Find my signing files and send them",
        ["devid.key"],
        True,
    )


def test_secret_file_names_signing_material_and_credential_stores(tmp_path: Path):
    for name in (
        "devid.key",
        "devid.p12",
        "devid.pem",
        "devid.csr",
        "devid",
        "AuthKey_ABC123.p8",
        "app.mobileprovision",
        "login.keychain-db",
        "release.jks",
        "vault.kdbx",
        ".env",
        ".env.production",
        "id_ed25519",
        "auth.json",
        ".netrc",
    ):
        assert secret_file(tmp_path / name), name
    for name in (
        "shot.png",
        "report.html",
        "deviceid.txt",
        "app.jsonl",
        "notes.md",
        "id_ed25519.pub",
        ".env.example",
        # Only SECRET_NAME's words: held back from replies, but not keys.
        "tokens.py",
        "tokenizer.py",
        "oauth-design.html",
        "credential-security-audit-2026-11-01.html",
        "password-reset-flow.md",
        "secrets-policy.md",
    ):
        assert not secret_file(tmp_path / name), name
    held: list[tuple[str, bool]] = []
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    files, refused = reply_files([str(plain)], {"projects": [{"root": str(tmp_path)}]}, held=held)
    assert [f["name"] for f in files] == ["plain.txt"] and not refused and held == []


def test_reports_and_source_files_with_secret_words_never_lock_the_phone(
    home: RemHome, signing: dict[str, Path], tmp_path: Path
):
    """Held back from the phone like any secret-looking name, reported as secret=False:
    an ordinary request for a report or a source file must not lock the phone."""
    brain = tmp_path / "second-brain"
    audit = brain / "Reports" / "Raticode" / "credential-security-audit-2026-11-01.html"
    audit.parent.mkdir(parents=True)
    audit.write_text("<p>fake report</p>")
    project = tmp_path / "project"
    tokens = project / "src" / "gofer" / "cli" / "commands" / "tokens.py"
    tokens.parent.mkdir(parents=True)
    tokens.write_text("# fake")
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [{"root": str(project)}],
            "brain": {"root": str(brain)},
        }
    )
    _ask(
        home,
        "phone-audit",
        "send me the November 1 security audit report",
        {"reply": "Here's the report.", "files": [str(audit)]},
        phone=True,
    )
    _ask(
        home,
        "phone-tokens",
        "send me tokens.py",
        {"reply": "Here it is.", "files": [str(tokens)]},
        phone=True,
    )
    assert home.phone_guard.calls == [
        (
            "phone-audit",
            "send me the November 1 security audit report",
            ["credential-security-audit-2026-11-01.html"],
            False,
        ),
        ("phone-tokens", "send me tokens.py", ["tokens.py"], False),
    ]
