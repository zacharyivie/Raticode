"""Mac release signing, checked on any OS with a test certificate and fake Apple tools."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    pkcs12,
)
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
TEAM = "ABCDE12345"
NAME = f"Developer ID Application: Example Maker ({TEAM})"
EXPORT_PASSWORD = "export-password-do-not-print"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up while the module is being executed.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def signing() -> Any:
    return _load("macos_release_signing", "macos-release-signing.py")


@pytest.fixture
def packages() -> Any:
    return _load("test_release_packages", "test-release-packages.py")


def make_p12(
    common_name: str = NAME,
    team: str = TEAM,
    *,
    expires_in: timedelta = timedelta(days=365),
    with_key: bool = True,
    password: str = EXPORT_PASSWORD,
) -> tuple[bytes, x509.Certificate]:
    """A self-signed stand-in for Apple's certificate, exported like OpenSSL 3 does."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, team),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Example Maker"),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + expires_in)
        .sign(key, hashes.SHA256())
    )
    # AES-256 with PBKDF2: OpenSSL 3's default, which older macOS can't import.
    data = pkcs12.serialize_key_and_certificates(
        b"Developer ID",
        key if with_key else None,
        certificate,
        None,
        BestAvailableEncryption(password.encode()),
    )
    return data, certificate


def wrapped_base64(data: bytes) -> str:
    # Like `base64 developer-id.p12` on Linux: 76-character lines.
    return base64.encodebytes(data).decode()


def test_certificate_from_linux_is_read_and_reencoded_for_macos(signing: Any) -> None:
    data, certificate = make_p12()
    found = signing.read_certificate(wrapped_base64(data), EXPORT_PASSWORD)
    assert found.name == NAME
    assert found.team_id == TEAM
    assert found.sha1 == certificate.fingerprint(hashes.SHA1()).hex().upper()
    assert EXPORT_PASSWORD not in found.transport_password
    key, reencoded, _ = pkcs12.load_key_and_certificates(
        found.pkcs12, found.transport_password.encode()
    )
    assert key is not None
    assert reencoded is not None
    assert reencoded.public_bytes(Encoding.DER) == certificate.public_bytes(Encoding.DER)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong-password", "MACOS_CERTIFICATE_PASSWORD does not open"),
        ("not-base64", "is not base64"),
        ("pem", "does not contain a .p12"),
        ("no-key", "certificate and its private key"),
        ("installer", "Developer ID Application"),
        ("development", "Developer ID Application"),
        ("no-team", "no Apple team ID"),
        ("expired", "expired on"),
    ],
)
def test_unusable_certificates_are_explained_without_secrets(
    signing: Any, case: str, message: str
) -> None:
    password = EXPORT_PASSWORD
    if case == "installer":
        data, _ = make_p12(f"Developer ID Installer: Example Maker ({TEAM})")
    elif case == "development":
        data, _ = make_p12(f"Apple Development: Example Maker ({TEAM})")
    elif case == "no-team":
        data, _ = make_p12(team="")
    elif case == "expired":
        data, _ = make_p12(expires_in=timedelta(days=-1))
    elif case == "no-key":
        data, _ = make_p12(with_key=False)
    else:
        data, certificate = make_p12()
    encoded = wrapped_base64(data)
    if case == "wrong-password":
        password = "not-the-export-password"
    elif case == "not-base64":
        encoded = "this is not base64!"
    elif case == "pem":
        encoded = base64.b64encode(certificate.public_bytes(Encoding.PEM)).decode()
    with pytest.raises(signing.SigningError, match=message) as error:
        signing.read_certificate(encoded, password)
    assert EXPORT_PASSWORD not in str(error.value)
    assert encoded[:40] not in str(error.value)


def test_optional_team_and_identity_must_match_the_certificate(signing: Any) -> None:
    data, _ = make_p12()
    found = signing.read_certificate(wrapped_base64(data), EXPORT_PASSWORD)
    for agreeing in (
        {},
        {"APPLE_TEAM_ID": TEAM},
        {"MACOS_SIGNING_IDENTITY": NAME},
        {"MACOS_SIGNING_IDENTITY": NAME.removeprefix("Developer ID Application: ")},
        {"MACOS_SIGNING_IDENTITY": found.sha1.lower()},
    ):
        signing.check_choices(found, agreeing)
    with pytest.raises(signing.SigningError, match="belongs to team ABCDE12345"):
        signing.check_choices(found, {"APPLE_TEAM_ID": "ZZZZZ99999"})
    with pytest.raises(signing.SigningError, match="MACOS_SIGNING_IDENTITY is"):
        signing.check_choices(found, {"MACOS_SIGNING_IDENTITY": "Someone Else (ZZZZZ99999)"})


def test_missing_settings_name_the_secrets_to_create(signing: Any) -> None:
    assert signing.missing_settings({}) == [
        "MACOS_CERTIFICATE (or APPLE_CERTIFICATE_P12_BASE64 or CSC_LINK)",
        "MACOS_CERTIFICATE_PASSWORD (or APPLE_CERTIFICATE_PASSWORD or CSC_KEY_PASSWORD)",
        "APPLE_ID",
        "APPLE_APP_SPECIFIC_PASSWORD",
    ]
    complete = {
        "MACOS_CERTIFICATE": "x",
        "MACOS_CERTIFICATE_PASSWORD": "x",
        "APPLE_ID": "x",
        "APPLE_APP_SPECIFIC_PASSWORD": "x",
    }
    # The team ID and identity are optional: both come from the certificate.
    assert signing.missing_settings(complete) == []


def test_tool_failures_never_show_arguments(signing: Any) -> None:
    with pytest.raises(signing.SigningError) as error:
        signing.run(sys.executable, "-c", "import sys; sys.exit(3)", APP_PASSWORD)
    assert "status 3" in str(error.value)
    assert APP_PASSWORD not in str(error.value)
    with pytest.raises(signing.SigningError) as error:
        signing.run(sys.executable, "-c", "import time; time.sleep(5)", APP_PASSWORD, timeout=0.2)
    assert "did not finish" in str(error.value)
    assert APP_PASSWORD not in str(error.value)


class FakeTools:
    """Records commands and answers like macOS's security, codesign and xcrun."""

    def __init__(self, signing: Any) -> None:
        self.signing = signing
        self.sha1 = ""
        self.commands: list[list[str]] = []
        self.imported: bytes | None = None
        self.signatures: dict[str, str] = {}
        self.entitlements: dict[str, dict[str, object]] = {}
        self.notary_status = "Accepted"
        self.failures: set[str] = set()

    def details(self, *, runtime: bool = True, timestamp: bool = True, team: str = TEAM) -> str:
        flags = "0x10000(runtime)" if runtime else "0x0(none)"
        return "\n".join(
            [
                "Executable=/somewhere",
                f"CodeDirectory v=20500 size=1 flags={flags} hashes=1+7 location=embedded",
                f"Authority=Developer ID Application: Example Maker ({team})",
                "Authority=Developer ID Certification Authority",
                "Authority=Apple Root CA",
                *(["Timestamp=10 Nov 2026 at 12:00:00"] if timestamp else []),
                f"TeamIdentifier={team}",
            ]
        )

    def __call__(
        self, *args: str | Path, check: bool = True, capture: bool = False, timeout: float = 0
    ) -> subprocess.CompletedProcess[str]:
        command = [str(a) for a in args]
        self.commands.append(command)
        stdout, stderr, code = "", "", 0
        tool = Path(command[0]).name
        if tool == "security" and command[1] == "import":
            self.imported = Path(command[2]).read_bytes()
        elif tool == "security" and command[1] == "find-identity":
            stdout = f'  1) {self.sha1} "{NAME}"\n     1 valid identities found\n'
        elif tool == "security" and command[1] == "list-keychains" and len(command) == 4:
            stdout = '    "/Users/runner/Library/Keychains/login.keychain-db"\n'
        elif tool == "codesign" and command[1] == "-dvvv":
            stderr = self.signatures.get(Path(command[2]).name, self.details())
        elif tool == "codesign" and "--entitlements" in command:
            granted = self.entitlements.get(
                Path(command[-1]).name, {"com.apple.security.device.audio-input": True}
            )
            stdout = plistlib.dumps(granted).decode()
        elif command[:3] == ["xcrun", "notarytool", "submit"]:
            stdout = json.dumps(
                {"id": "0f0e0d0c-0b0a-0908-0706-050403020100", "status": self.notary_status}
            )
        key = " ".join(command[:3])
        if any(failure in key for failure in self.failures):
            code = 1
            stderr = "simulated failure"
        result = subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)
        if check and code:
            raise self.signing.SigningError(f"{command[0]} exited with status {code}")
        return result


@pytest.fixture
def tools(signing: Any, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    fake = FakeTools(signing)
    monkeypatch.setattr(signing, "run", fake)
    return fake


def prepare_env(tmp_path: Path, data: bytes, **extra: str) -> dict[str, str]:
    return {
        "RUNNER_TEMP": str(tmp_path),
        "MACOS_CERTIFICATE": wrapped_base64(data),
        "MACOS_CERTIFICATE_PASSWORD": EXPORT_PASSWORD,
        "APPLE_ID": "maker@example.com",
        "APPLE_APP_SPECIFIC_PASSWORD": APP_PASSWORD,
        **extra,
    }


def test_prepare_imports_into_a_temporary_keychain_and_validates_notarization(
    signing: Any,
    tools: FakeTools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data, certificate = make_p12()
    tools.sha1 = certificate.fingerprint(hashes.SHA1()).hex().upper()
    roots = tmp_path / "bundled-roots.keychain"
    roots.write_bytes(b"apple intermediates")
    monkeypatch.setattr(signing, "BUNDLED_ROOT_CERTS", roots)

    outputs = signing.prepare(prepare_env(tmp_path, data))

    keychain = str(tmp_path / "raticode-signing.keychain-db")
    assert outputs == {
        "identity": tools.sha1,
        "identity-name": NAME,
        "team-id": TEAM,
        "keychain": keychain,
        "notary-profile": "raticode-notary",
    }
    # The export password never reaches a command; the app password only notarytool.
    for command in tools.commands:
        assert EXPORT_PASSWORD not in command
        if command[:3] != ["xcrun", "notarytool", "store-credentials"]:
            assert APP_PASSWORD not in command
    store = next(c for c in tools.commands if c[:3] == ["xcrun", "notarytool", "store-credentials"])
    assert store[store.index("--team-id") + 1] == TEAM
    assert store[store.index("--keychain") + 1] == keychain
    assert "--no-validate" not in store
    # The keychain locks itself after two idle hours, not six, and on sleep.
    settings = next(c for c in tools.commands if c[1:2] == ["set-keychain-settings"])
    assert settings[2:] == ["-lut", "7200", keychain]
    # The re-encoded identity was imported, then its temporary file deleted.
    assert tools.imported is not None
    assert not (tmp_path / "raticode-signing.p12").exists()
    search = next(c for c in tools.commands if c[1] == "list-keychains" and "-s" in c)
    assert search[search.index("-s") + 1 :] == [
        keychain,
        str(tmp_path / "raticode-apple-intermediates.keychain"),
        "/Users/runner/Library/Keychains/login.keychain-db",
    ]
    printed = capsys.readouterr()
    assert "APPLE_TEAM_ID is not set; using team ABCDE12345" in printed.out
    for secret in (EXPORT_PASSWORD, APP_PASSWORD, "maker@example.com"):
        assert secret not in printed.out + printed.err


def test_prepare_refuses_an_identity_macos_does_not_trust(
    signing: Any, tools: FakeTools, tmp_path: Path
) -> None:
    data, _ = make_p12()
    tools.sha1 = "0" * 40
    with pytest.raises(signing.SigningError, match="does not accept the imported"):
        signing.prepare(prepare_env(tmp_path, data))
    assert not any(c[:2] == ["xcrun", "notarytool"] for c in tools.commands)


def test_prepare_lists_every_missing_secret_before_touching_the_keychain(
    signing: Any, tools: FakeTools, tmp_path: Path
) -> None:
    with pytest.raises(signing.SigningError, match="APPLE_APP_SPECIFIC_PASSWORD"):
        signing.prepare({"RUNNER_TEMP": str(tmp_path), "APPLE_ID": "maker@example.com"})
    assert tools.commands == []


def test_step_outputs_are_written_for_github(signing: Any, tmp_path: Path) -> None:
    output = tmp_path / "output"
    signing.write_outputs(str(output), {"identity": "ABC", "team-id": TEAM})
    assert output.read_text() == f"identity=ABC\nteam-id={TEAM}\n"
    with pytest.raises(signing.SigningError):
        signing.write_outputs(str(output), {"identity": "A\nB"})


def make_release(root: Path) -> Path:
    release = root / "release"
    app = release / "mac-arm64/Raticode.app"
    for nested in ("backend/Raticode", "speech/raticode-speech"):
        path = app / "Contents/Resources" / nested
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xcf\xfa\xed\xfe")
    (release / "Raticode-0.5.3-arm64.dmg").write_bytes(b"dmg")
    (release / "raticode-cli-macos-arm64").write_bytes(b"cli")
    return release


VERIFY_ENV = {
    "RATICODE_IDENTITY": "0123456789ABCDEF0123456789ABCDEF01234567",
    "RATICODE_TEAM_ID": TEAM,
    "RATICODE_KEYCHAIN": "/runner/temp/raticode-signing.keychain-db",
    "RATICODE_NOTARY_PROFILE": "raticode-notary",
}


def test_verify_checks_nested_code_then_notarizes_and_staples_the_dmg(
    signing: Any, tools: FakeTools, tmp_path: Path
) -> None:
    release = make_release(tmp_path)
    signing.verify(VERIFY_ENV, release)

    def position(match: Callable[[list[str]], bool]) -> int:
        return next(i for i, c in enumerate(tools.commands) if match(c))

    dmg = str(release / "Raticode-0.5.3-arm64.dmg")
    for nested in ("backend/Raticode", "speech/raticode-speech"):
        path = str(release / "mac-arm64/Raticode.app/Contents/Resources" / nested)
        assert ["/usr/bin/codesign", "-dvvv", path] in tools.commands
    sign = position(lambda c: c[:3] == ["/usr/bin/codesign", "--force", "--sign"] and c[-1] == dmg)
    assert "--timestamp" in tools.commands[sign]
    notarize = position(lambda c: c[:3] == ["xcrun", "notarytool", "submit"] and c[3] == dmg)
    staple = position(lambda c: c == ["xcrun", "stapler", "staple", dmg])
    refresh = position(lambda c: c[0] == "node")
    assert sign < notarize < staple < refresh
    assert tools.commands[refresh][-1] == "Raticode-0.5.3-arm64.dmg"
    submitted = tools.commands[notarize]
    assert submitted[submitted.index("--keychain-profile") + 1] == "raticode-notary"
    for receipt in ("notarization-macos-dmg.json", "notarization-macos-cli.json"):
        assert json.loads((release / receipt).read_text())["status"] == "Accepted"
    app = str(release / "mac-arm64/Raticode.app")
    assert ["xcrun", "stapler", "validate", app] in tools.commands


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("helper-without-runtime", "without the hardened runtime"),
        ("backend-other-team", "signed by team ZZZZZ99999"),
        ("cli-without-timestamp", "no secure timestamp"),
        ("app-debuggable", "get-task-allow"),
        ("app-without-microphone", "audio-input"),
        ("helper-missing", "missing Contents/Resources/speech"),
        ("rejected", "Apple did not accept"),
        ("no-app", "No signed application"),
    ],
)
def test_verify_stops_on_any_signing_problem(
    signing: Any, tools: FakeTools, tmp_path: Path, problem: str, message: str
) -> None:
    release = make_release(tmp_path)
    app = release / "mac-arm64/Raticode.app"
    if problem == "helper-without-runtime":
        tools.signatures["raticode-speech"] = tools.details(runtime=False)
    elif problem == "backend-other-team":
        tools.signatures["Raticode"] = tools.details(team="ZZZZZ99999")
    elif problem == "cli-without-timestamp":
        tools.signatures["raticode-cli-macos-arm64"] = tools.details(timestamp=False)
    elif problem == "app-debuggable":
        tools.entitlements["Raticode.app"] = {
            "com.apple.security.get-task-allow": True,
            "com.apple.security.device.audio-input": True,
        }
    elif problem == "app-without-microphone":
        tools.entitlements["Raticode.app"] = {}
    elif problem == "helper-missing":
        (app / "Contents/Resources/speech/raticode-speech").unlink()
    elif problem == "rejected":
        tools.notary_status = "Invalid"
    else:
        shutil.rmtree(release / "mac-arm64")
    with pytest.raises(signing.SigningError, match=message):
        signing.verify(VERIFY_ENV, release)
    if problem == "rejected":
        assert any(c[:3] == ["xcrun", "notarytool", "log"] for c in tools.commands)
        assert not any(c[:3] == ["xcrun", "stapler", "staple"] for c in tools.commands)
    else:
        assert not any(c[:3] == ["xcrun", "notarytool", "submit"] for c in tools.commands)


def test_cleanup_deletes_the_keychain_even_after_a_failed_build(
    signing: Any, tools: FakeTools, tmp_path: Path
) -> None:
    keychain = tmp_path / "raticode-signing.keychain-db"
    keychain.write_bytes(b"keychain")
    (tmp_path / "raticode-signing.p12").write_bytes(b"left over")
    tools.failures.add("delete-keychain")
    signing.cleanup({"RUNNER_TEMP": str(tmp_path)})
    assert ["/usr/bin/security", "delete-keychain", str(keychain)] in tools.commands
    assert not keychain.exists()
    assert not (tmp_path / "raticode-signing.p12").exists()


def test_signing_commands_run_only_on_macos(signing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signing.sys, "platform", "linux")
    with pytest.raises(SystemExit, match="only on macOS"):
        signing.main(["prepare"])


def write_asar(path: Path, files: dict[str, bytes]) -> None:
    """The asar layout electron-builder writes: sizes, a pickled JSON index, then data."""
    index: dict[str, Any] = {"files": {}}
    offset = 0
    for name, data in files.items():
        index["files"][name] = {"size": len(data), "offset": str(offset)}
        offset += len(data)
    text = json.dumps(index).encode()
    padded = text + b"\0" * (-len(text) % 4)
    pickle = struct.pack("<II", len(padded) + 4, len(text)) + padded
    path.write_bytes(struct.pack("<II", 4, len(pickle)) + pickle + b"".join(files.values()))


def test_package_check_reads_the_updater_label_from_the_app_archive(
    packages: Any, tmp_path: Path
) -> None:
    archive = tmp_path / "app.asar"
    label = json.dumps({"name": "raticode", "raticodeReleaseSigning": "signed"}).encode()
    write_asar(archive, {"index.html": b"<html>", "package.json": label})
    assert packages.read_asar_file(archive, "package.json") == label
    with pytest.raises(RuntimeError, match="no packed"):
        packages.read_asar_file(archive, "missing.json")


def test_package_check_reads_a_real_electron_archive(packages: Any, tmp_path: Path) -> None:
    node = shutil.which("node")
    asar = ROOT / "frontend/node_modules/@electron/asar"
    if node is None or not asar.is_dir():
        pytest.skip("Needs Node and the frontend's installed packages")
    source = tmp_path / "app"
    source.mkdir()
    (source / "package.json").write_text('{"raticodeReleaseSigning": "signed"}')
    (source / "main.js").write_text("console.log('hi')")
    archive = tmp_path / "app.asar"
    # @electron/asar 4 is an ES module, the same code electron-builder packs apps with.
    script = (
        "import(process.argv[1])"
        ".then((asar) => asar.createPackage(process.argv[2], process.argv[3]))"
        ".catch((e) => { console.error(e); process.exit(1); })"
    )
    entry = (asar / "lib/asar.js").as_uri()
    subprocess.run([node, "-e", script, entry, str(source), str(archive)], check=True)
    data = packages.read_asar_file(archive, "package.json")
    assert json.loads(data) == {"raticodeReleaseSigning": "signed"}


@pytest.mark.parametrize(
    ("mode", "label", "adhoc", "error"),
    [
        ("developer-id", "signed", False, None),
        ("developer-id", "unsigned", False, "labelled 'unsigned'"),
        ("unsigned", "unsigned", True, None),
        ("unsigned", "signed", True, "labelled 'signed'"),
        ("unsigned", "unsigned", False, "only the unsigned build's ad-hoc signature"),
    ],
)
def test_package_check_asserts_how_each_mac_app_is_signed(
    packages: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    label: str,
    adhoc: bool,
    error: str | None,
) -> None:
    app = tmp_path / "Raticode.app"
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True)
    write_asar(
        resources / "app.asar",
        {"package.json": json.dumps({"raticodeReleaseSigning": label}).encode()},
    )
    checked: list[str] = []

    class Signing:
        @staticmethod
        def check_app(path: Path, team_id: str) -> None:
            checked.append(f"developer-id {team_id}")

        @staticmethod
        def signature(path: Path) -> Any:
            return type("Signature", (), {"adhoc": adhoc})()

    monkeypatch.setattr(packages, "run", lambda *args, **kwargs: "")
    if error:
        with pytest.raises(RuntimeError, match=error):
            packages.check_mac_app_signing(app, mode, TEAM, Signing)
    else:
        packages.check_mac_app_signing(app, mode, TEAM, Signing)
        assert checked == ([f"developer-id {TEAM}"] if mode == "developer-id" else [])


@pytest.mark.parametrize(
    ("config", "error"),
    [
        (
            "owner: zacharyivie\nrepo: Raticode\nprovider: github\n"
            "updaterCacheDirName: gofer-flow-ui-updater\n",
            None,
        ),
        (
            "owner: zacharyivie\nrepo: gofer-flow\nprovider: github\n",
            "looks for updates at github:zacharyivie/gofer-flow",
        ),
        (None, "has no app-update.yml"),
    ],
)
def test_package_check_reads_the_update_feed_baked_into_the_app(
    packages: Any, tmp_path: Path, config: str | None, error: str | None
) -> None:
    app = tmp_path / "Raticode.app"
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True)
    if config is not None:
        # The first case is the app-update.yml of the published 0.5.3 zip.
        (resources / "app-update.yml").write_text(config)
    if error:
        with pytest.raises(RuntimeError, match=error):
            packages.check_mac_update_feed(app)
    else:
        assert packages.check_mac_update_feed(app) == "github:zacharyivie/Raticode"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (
            {"RATICODE_MACOS_SIGNING": "developer-id", "RATICODE_MACOS_TEAM_ID": TEAM},
            ("developer-id", TEAM),
        ),
        ({"RATICODE_MACOS_SIGNING": "unsigned"}, ("unsigned", "")),
        ({"RATICODE_MACOS_SIGNING": "developer-id"}, "needs RATICODE_MACOS_TEAM_ID"),
        ({}, "must be developer-id or unsigned"),
    ],
)
def test_package_check_requires_an_explicit_mac_signing_mode(
    packages: Any, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], expected: Any
) -> None:
    for name in ("RATICODE_MACOS_SIGNING", "RATICODE_MACOS_TEAM_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    if isinstance(expected, str):
        with pytest.raises(RuntimeError, match=expected):
            packages.mac_signing_mode()
    else:
        assert packages.mac_signing_mode() == expected


def _node_with_packages() -> str:
    node = shutil.which("node")
    needed = ("js-yaml", "app-builder-lib/out/targets/blockmap/blockmap.js")
    if node is None or not all((ROOT / "frontend/node_modules" / n).exists() for n in needed):
        pytest.skip("Needs Node and the frontend's installed packages")
    return node


def test_stapled_dmg_gets_a_new_blockmap_and_updater_entry(tmp_path: Path) -> None:
    node = _node_with_packages()
    release = tmp_path / "release"
    release.mkdir()
    version = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    zip_name, dmg_name = f"Raticode-{version}-arm64.zip", f"Raticode-{version}-arm64.dmg"
    (release / zip_name).write_bytes(os.urandom(150_000))
    (release / dmg_name).write_bytes(os.urandom(200_000))
    refresh = ROOT / "scripts/refresh-mac-update-metadata.cjs"
    verify = (
        "const { verifyMetadata } = require(process.argv[1]);"
        "try { console.log(verifyMetadata(process.argv[2], process.argv[3], 'darwin')); }"
        "catch (e) { console.error(e.message); process.exit(1); }"
    )

    def check() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                node,
                "-e",
                verify,
                str(ROOT / "scripts/verify-update-metadata.cjs"),
                str(release),
                version,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    # electron-builder's output before stapling: blockmaps plus latest-mac.yml.
    build = (
        "const path = require('node:path');"
        "const { buildBlockMap } = require(path.join(process.argv[1],"
        " 'app-builder-lib/out/targets/blockmap/blockmap'));"
        "const yaml = require(path.join(process.argv[1], 'js-yaml'));"
        "(async () => { const files = [];"
        " for (const name of process.argv.slice(3)) {"
        "  const file = path.join(process.argv[2], name);"
        "  const info = await buildBlockMap(file, 'gzip', file + '.blockmap');"
        "  files.push({ url: name, sha512: info.sha512, size: info.size }); }"
        " require('node:fs').writeFileSync(path.join(process.argv[2], 'latest-mac.yml'),"
        "  yaml.dump({ version: " + json.dumps(version) + ", files,"
        "  path: files[0].url, sha512: files[0].sha512,"
        "  releaseDate: '2026-11-10T12:00:00.000Z' }));"
        "})();"
    )
    subprocess.run(
        [node, "-e", build, str(ROOT / "frontend/node_modules"), str(release), zip_name, dmg_name],
        check=True,
    )
    assert check().returncode == 0
    old_blockmap = (release / f"{dmg_name}.blockmap").read_bytes()

    with (release / dmg_name).open("ab") as dmg:
        dmg.write(b"stapled notarization ticket")
    stale = check()
    assert stale.returncode == 1
    assert "Updater hash does not match final file" in stale.stderr

    subprocess.run([node, str(refresh), str(release), dmg_name], check=True)
    assert check().returncode == 0, check().stderr
    metadata = (release / "latest-mac.yml").read_text()
    assert f"size: {200_000 + len(b'stapled notarization ticket')}" in metadata
    assert "releaseDate: '2026-11-10T12:00:00.000Z'" in metadata

    # A blockmap left over from before stapling is caught even when the hashes match.
    (release / f"{dmg_name}.blockmap").write_bytes(old_blockmap)
    stale = check()
    assert stale.returncode == 1
    assert "Blockmap does not match final file" in stale.stderr

    bad = subprocess.run(
        [node, str(refresh), str(release), "../escape.dmg"], capture_output=True, text=True
    )
    assert bad.returncode == 1


def test_mac_build_config_signs_everything_with_the_hardened_runtime() -> None:
    mac = json.loads((ROOT / "frontend/package.json").read_text())["build"]["mac"]
    assert mac["hardenedRuntime"] is True
    # Nested code (backend, speech helper, Electron helpers) gets the same entitlements.
    assert mac["entitlementsInherit"] == mac["entitlements"]
    # The workflow chooses the identity and notarization; the config never disables them.
    assert "identity" not in mac and "notarize" not in mac
    granted = plistlib.loads((ROOT / "frontend" / mac["entitlements"]).read_bytes())
    assert "com.apple.security.get-task-allow" not in granted
    spec = (ROOT / "raticode.spec").read_text()
    assert 'os.environ.get("GOFER_CODESIGN_IDENTITY")' in spec
    assert 'os.environ["GOFER_CODESIGN_ENTITLEMENTS"]' in spec
    # macos-release-signing.py replaced the old shell scripts.
    assert not (ROOT / "scripts/prepare-macos-signing.sh").exists()
    assert not (ROOT / "scripts/verify-macos-release.sh").exists()
