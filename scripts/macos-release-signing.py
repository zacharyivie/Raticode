"""Developer ID signing, notarization and stapling for Raticode's macOS release build.

Runs on the disposable GitHub macOS runner of a signed stage candidate:

  prepare  Check the Developer ID Application certificate, import it into a temporary
           keychain, and store notarytool credentials in that keychain. Writes only
           non-secret step outputs: the identity's SHA-1 hash and name, the team ID,
           the keychain path and the notarytool profile name.
  verify   Check the signed app and its nested backend and speech helper, notarize and
           staple the DMG, rebuild the DMG's blockmap and updater entry, and notarize
           the standalone CLI. Writes Apple's notarization receipts.
  cleanup  Delete the temporary keychain and anything else prepare left behind.

Secret values come only from the environment. They are never printed, written to a
log or passed to a command, except the app-specific password, which notarytool can
only take as an argument when it stores the keychain profile.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "frontend/release"
NOTARY_PROFILE = "raticode-notary"
KEYCHAIN_NAME = "raticode-signing.keychain-db"
# Seconds of inactivity before the signing keychain locks itself (was six hours).
KEYCHAIN_IDLE_LOCK = "7200"
ROOT_CERTS_NAME = "raticode-apple-intermediates.keychain"
# electron-builder ships Apple's Developer ID intermediate certificates in this keychain
# and adds it to the search list when it imports a certificate itself. Raticode imports
# the certificate, so it adds the same keychain; an exported .p12 often lacks the chain.
BUNDLED_ROOT_CERTS = ROOT / "frontend/node_modules/app-builder-lib/certs/root_certs.keychain"
IDENTITY_PREFIX = "Developer ID Application: "
TEAM_PATTERN = re.compile(r"[A-Z0-9]{10}")
EXPIRY_WARNING = timedelta(days=30)
NOTARY_TIMEOUT = "45m"

# The names the workflow passes in. The first of each is the documented GitHub secret;
# the workflow also accepts APPLE_CERTIFICATE_P12_BASE64 / APPLE_CERTIFICATE_PASSWORD
# and electron-builder's own names, and passes whichever is set under the first name.
CERTIFICATE = "MACOS_CERTIFICATE"
CERTIFICATE_PASSWORD = "MACOS_CERTIFICATE_PASSWORD"
APPLE_ID = "APPLE_ID"
APP_PASSWORD = "APPLE_APP_SPECIFIC_PASSWORD"
TEAM_ID = "APPLE_TEAM_ID"
IDENTITY = "MACOS_SIGNING_IDENTITY"
REQUIRED = (CERTIFICATE, CERTIFICATE_PASSWORD, APPLE_ID, APP_PASSWORD)
ALTERNATIVES = {
    CERTIFICATE: "APPLE_CERTIFICATE_P12_BASE64 or CSC_LINK",
    CERTIFICATE_PASSWORD: "APPLE_CERTIFICATE_PASSWORD or CSC_KEY_PASSWORD",
}


class SigningError(Exception):
    """A signing problem with a message that is safe to print."""


def fail(message: str) -> NoReturn:
    raise SigningError(message)


def run(
    *args: str | Path,
    check: bool = True,
    capture: bool = False,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a tool without a shell. Failures name the tool, never its arguments."""
    command = [str(arg) for arg in args]
    # Name only the tool in errors: some arguments are passwords.
    tool = " ".join(command[:2]) if command[0].endswith(("security", "xcrun")) else command[0]
    try:
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail(f"{tool} did not finish within {timeout:.0f} seconds.")
    except OSError as error:
        fail(f"Could not start {tool}: {error.strerror}")
    if not capture:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"{tool} exited with status {result.returncode}: {detail}")
    return result


def missing_settings(environ: Mapping[str, str]) -> list[str]:
    """Required settings that are empty, named as the GitHub secrets to create."""
    missing = []
    for name in REQUIRED:
        if not environ.get(name, "").strip():
            alternative = ALTERNATIVES.get(name)
            missing.append(f"{name} (or {alternative})" if alternative else name)
    return missing


@dataclass(frozen=True)
class Certificate:
    """The Developer ID Application identity inside the exported .p12."""

    sha1: str
    name: str
    team_id: str
    expires: datetime
    pkcs12: bytes
    transport_password: str


def decode_base64(value: str) -> bytes:
    # `base64` on Linux wraps lines at 76 characters; GitHub keeps the line breaks.
    compact = "".join(value.split())
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        fail(
            f"{CERTIFICATE} is not base64 text. Encode the .p12 file itself, for example "
            "with `openssl base64 -A -in developer-id.p12`, and save the output as the secret."
        )


def read_certificate(encoded: str, password: str, now: datetime | None = None) -> Certificate:
    """Open the .p12 and re-encode it in a format every macOS version imports.

    OpenSSL 3, which exports a .p12 made on Linux, encrypts it with AES and PBKDF2 by
    default. Older macOS `security import` rejects that as a wrong password, so the
    identity is re-encoded with the legacy algorithms under a fresh random password.
    That also keeps the export password out of every command line.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import PrivateFormat, pkcs12
    from cryptography.x509.oid import NameOID

    data = decode_base64(encoded)
    if not data.startswith(b"\x30"):
        fail(
            f"{CERTIFICATE} does not contain a .p12 file. Export the Developer ID "
            "Application certificate together with its private key as .p12, not .cer or .pem."
        )
    try:
        key, certificate, extras = pkcs12.load_key_and_certificates(data, password.encode("utf-8"))
    except ValueError:
        fail(
            f"{CERTIFICATE_PASSWORD} does not open {CERTIFICATE}. Use the password chosen "
            "when the .p12 was exported."
        )
    if certificate is None or key is None:
        fail(
            f"{CERTIFICATE} must contain the certificate and its private key. Export the "
            "identity from the machine that made the certificate signing request."
        )

    def attribute(oid: x509.ObjectIdentifier) -> str:
        values = certificate.subject.get_attributes_for_oid(oid)
        return str(values[0].value) if values else ""

    name = attribute(NameOID.COMMON_NAME)
    team_id = attribute(NameOID.ORGANIZATIONAL_UNIT_NAME)
    if not name.startswith(IDENTITY_PREFIX):
        fail(
            f"{CERTIFICATE} holds a certificate named '{name}'. Mac releases need a "
            "'Developer ID Application' certificate; Developer ID Installer and Apple "
            "Development certificates can't sign a downloadable app."
        )
    if not TEAM_PATTERN.fullmatch(team_id):
        fail(f"The certificate '{name}' has no Apple team ID in its organizational unit.")
    expires = certificate.not_valid_after_utc
    current = now or datetime.now(UTC)
    if expires <= current:
        fail(f"The certificate '{name}' expired on {expires:%Y-%m-%d}. Create a new one.")
    if certificate.not_valid_before_utc > current:
        fail(f"The certificate '{name}' is not valid until {certificate.not_valid_before_utc}.")
    transport_password = secrets.token_hex(24)
    encryption = (
        PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(50000)
        .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
        .hmac_hash(hashes.SHA1())
        .build(transport_password.encode("ascii"))
    )
    if not isinstance(key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey):
        fail(f"The private key in {CERTIFICATE} is not an RSA or elliptic-curve key.")
    reencoded = pkcs12.serialize_key_and_certificates(
        b"Raticode Developer ID", key, certificate, extras or None, encryption
    )
    return Certificate(
        sha1=certificate.fingerprint(hashes.SHA1()).hex().upper(),
        name=name,
        team_id=team_id,
        expires=expires,
        pkcs12=reencoded,
        transport_password=transport_password,
    )


def check_choices(certificate: Certificate, environ: Mapping[str, str]) -> None:
    """Optional settings must agree with the certificate; they never pick another one."""
    team = environ.get(TEAM_ID, "").strip()
    if team and team != certificate.team_id:
        fail(
            f"{TEAM_ID} is {team}, but the certificate belongs to team {certificate.team_id}. "
            f"Correct {TEAM_ID} or delete it; Raticode reads the team from the certificate."
        )
    wanted = environ.get(IDENTITY, "").strip()
    if (
        wanted
        and wanted.upper() != certificate.sha1
        and wanted
        not in {
            certificate.name,
            certificate.name.removeprefix(IDENTITY_PREFIX),
        }
    ):
        fail(
            f"{IDENTITY} is '{wanted}', but {CERTIFICATE} holds '{certificate.name}'. "
            f"Correct {IDENTITY} or delete it; it is optional."
        )


def find_identity(keychain: Path, sha1: str) -> None:
    """The imported identity is valid for code signing, including its certificate chain."""
    listing = run(
        "/usr/bin/security", "find-identity", "-v", "-p", "codesigning", keychain, capture=True
    ).stdout
    if not any(sha1 in line and f'"{IDENTITY_PREFIX}' in line for line in listing.splitlines()):
        fail(
            "macOS does not accept the imported Developer ID Application identity for code "
            "signing. Check that the certificate was issued by Apple's Developer ID "
            "Certification Authority and has not been revoked. If it was, the runner may "
            "lack Apple's intermediate certificate (electron-builder's root_certs.keychain)."
        )


def write_outputs(path: str | None, values: Mapping[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value:
                fail(f"Unexpected newline in step output {key}")
            stream.write(f"{key}={value}\n")


def prepare(environ: Mapping[str, str]) -> dict[str, str]:
    missing = missing_settings(environ)
    if missing:
        fail(
            "Signed macOS releases need these GitHub secrets: " + ", ".join(missing) + ". "
            "See docs/releasing.md."
        )
    certificate = read_certificate(environ[CERTIFICATE], environ[CERTIFICATE_PASSWORD])
    check_choices(certificate, environ)
    if not environ.get(TEAM_ID, "").strip():
        print(f"{TEAM_ID} is not set; using team {certificate.team_id} from the certificate.")
    if certificate.expires - datetime.now(UTC) < EXPIRY_WARNING:
        print(
            f"::warning title=Developer ID certificate expires soon::"
            f"'{certificate.name}' expires on {certificate.expires:%Y-%m-%d}."
        )
    temporary = Path(environ["RUNNER_TEMP"])
    keychain = temporary / KEYCHAIN_NAME
    roots = temporary / ROOT_CERTS_NAME
    bundle = temporary / "raticode-signing.p12"
    keychain_password = secrets.token_hex(32)
    run("/usr/bin/security", "create-keychain", "-p", keychain_password, keychain)
    # Locks after two idle hours or on sleep. A signed build uses it for about 15 minutes,
    # and the cleanup step deletes it as soon as signing and notarization are done.
    run("/usr/bin/security", "set-keychain-settings", "-lut", KEYCHAIN_IDLE_LOCK, keychain)
    run("/usr/bin/security", "unlock-keychain", "-p", keychain_password, keychain)
    try:
        fd = os.open(bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(certificate.pkcs12)
        run(
            "/usr/bin/security",
            "import",
            bundle,
            "-k",
            keychain,
            "-f",
            "pkcs12",
            "-P",
            certificate.transport_password,
            "-T",
            "/usr/bin/codesign",
            "-T",
            "/usr/bin/security",
            "-T",
            "/usr/bin/productbuild",
        )
    finally:
        bundle.unlink(missing_ok=True)
    run(
        "/usr/bin/security",
        "set-key-partition-list",
        "-S",
        "apple-tool:,apple:,codesign:",
        "-s",
        "-k",
        keychain_password,
        keychain,
        capture=True,
    )
    search = [keychain]
    if BUNDLED_ROOT_CERTS.is_file():
        shutil.copyfile(BUNDLED_ROOT_CERTS, roots)
        search.append(roots)
    current = run("/usr/bin/security", "list-keychains", "-d", "user", capture=True).stdout
    existing = [line.strip().strip('"') for line in current.splitlines() if line.strip()]
    run(
        "/usr/bin/security",
        "list-keychains",
        "-d",
        "user",
        "-s",
        *search,
        *[path for path in existing if path not in {str(p) for p in search}],
    )
    find_identity(keychain, certificate.sha1)
    # Validates the Apple ID, app-specific password and team with Apple before building.
    run(
        "xcrun",
        "notarytool",
        "store-credentials",
        NOTARY_PROFILE,
        "--apple-id",
        environ[APPLE_ID].strip(),
        "--team-id",
        certificate.team_id,
        "--password",
        environ[APP_PASSWORD].strip(),
        "--keychain",
        keychain,
        capture=True,
    )
    outputs = {
        "identity": certificate.sha1,
        "identity-name": certificate.name,
        "team-id": certificate.team_id,
        "keychain": str(keychain),
        "notary-profile": NOTARY_PROFILE,
    }
    print(
        f"Signing with '{certificate.name}' (team {certificate.team_id}), valid until "
        f"{certificate.expires:%Y-%m-%d}. Notarization credentials were accepted by Apple."
    )
    return outputs


def cleanup(environ: Mapping[str, str]) -> None:
    temporary = Path(environ["RUNNER_TEMP"])
    keychain = temporary / KEYCHAIN_NAME
    roots = temporary / ROOT_CERTS_NAME
    for path in (keychain, roots):
        if path.exists():
            run("/usr/bin/security", "delete-keychain", path, check=False)
        path.unlink(missing_ok=True)
    (temporary / "raticode-signing.p12").unlink(missing_ok=True)
    print("Deleted the temporary signing keychain.")


@dataclass(frozen=True)
class Signature:
    authorities: tuple[str, ...]
    team_id: str
    flags: str
    timestamped: bool
    adhoc: bool


def signature(path: Path) -> Signature:
    # codesign writes its description to stderr.
    details = run("/usr/bin/codesign", "-dvvv", path, capture=True).stderr
    authorities = tuple(
        line.removeprefix("Authority=")
        for line in details.splitlines()
        if line.startswith("Authority=")
    )
    team = next(
        (
            line.removeprefix("TeamIdentifier=")
            for line in details.splitlines()
            if line.startswith("TeamIdentifier=")
        ),
        "",
    )
    flags = next((line for line in details.splitlines() if line.startswith("CodeDirectory ")), "")
    return Signature(
        authorities=authorities,
        team_id=team,
        flags=flags,
        timestamped=any(line.startswith("Timestamp=") for line in details.splitlines()),
        adhoc="Signature=adhoc" in details,
    )


def require_developer_id(path: Path, team_id: str, executable: bool = True) -> None:
    """Signed by this team's Developer ID, with a secure timestamp and hardened runtime."""
    found = signature(path)
    label = path.name
    if not found.authorities or not found.authorities[0].startswith(IDENTITY_PREFIX):
        fail(f"{label} is not signed with a Developer ID Application certificate.")
    if found.team_id != team_id:
        fail(f"{label} is signed by team {found.team_id or 'none'}, expected {team_id}.")
    if not found.timestamped:
        fail(f"{label} has no secure timestamp, which notarization requires.")
    if executable and "runtime" not in found.flags:
        fail(f"{label} is signed without the hardened runtime.")


def entitlements(path: Path) -> dict[str, object]:
    output = run(
        "/usr/bin/codesign", "-d", "--entitlements", "-", "--xml", path, capture=True
    ).stdout
    start = output.find("<?xml")
    if start < 0:
        return {}
    loaded = plistlib.loads(output[start:].encode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def nested_code(app: Path) -> list[Path]:
    """The bundled executables Raticode adds to Electron: the backend and speech helper."""
    resources = app / "Contents/Resources"
    return [resources / "backend/Raticode", resources / "speech/raticode-speech"]


def check_app(app: Path, team_id: str) -> None:
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
    require_developer_id(app, team_id)
    granted = entitlements(app)
    if granted.get("com.apple.security.get-task-allow") is True:
        fail(f"{app.name} allows debugging (get-task-allow); notarization rejects it.")
    if granted.get("com.apple.security.device.audio-input") is not True:
        fail(f"{app.name} lacks the audio-input entitlement, so dictation can't record.")
    for code in nested_code(app):
        if not code.is_file():
            fail(f"{app.name} is missing {code.relative_to(app)}.")
        run("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", code)
        require_developer_id(code, team_id)
    run("xcrun", "stapler", "validate", app)
    run("/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=2", app)
    print(f"Verified {app}: Developer ID, team {team_id}, hardened runtime, notarized ticket.")


def notarize(path: Path, receipt: Path, environ: Mapping[str, str]) -> None:
    credentials = (
        "--keychain-profile",
        environ["RATICODE_NOTARY_PROFILE"],
        "--keychain",
        environ["RATICODE_KEYCHAIN"],
    )
    result = run(
        "xcrun",
        "notarytool",
        "submit",
        path,
        *credentials,
        "--wait",
        "--timeout",
        NOTARY_TIMEOUT,
        "--output-format",
        "json",
        check=False,
        capture=True,
        timeout=60 * 60,
    )
    try:
        outcome = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"notarytool gave no result for {path.name}: {result.stderr.strip()}")
    receipt.write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if outcome.get("status") != "Accepted":
        submission = str(outcome.get("id", ""))
        if re.fullmatch(r"[0-9a-fA-F-]{36}", submission):
            # Apple's log lists each rejected file and why.
            run("xcrun", "notarytool", "log", submission, *credentials, check=False)
        status = f"{outcome.get('status')} {outcome.get('message', '')}".strip()
        fail(f"Apple did not accept {path.name}: {status}")
    print(f"Apple accepted {path.name} (submission {outcome.get('id')}).")


def single(paths: Iterable[Path], description: str) -> Path:
    found = sorted(paths)
    if len(found) != 1:
        fail(f"Expected exactly one {description}, found {[p.name for p in found]}.")
    return found[0]


def verify(environ: Mapping[str, str], release: Path = RELEASE) -> None:
    team_id = environ["RATICODE_TEAM_ID"]
    if not TEAM_PATTERN.fullmatch(team_id):
        fail("RATICODE_TEAM_ID must be the team ID from the prepare step.")
    apps = sorted(release.glob("mac*/*.app"))
    if not apps:
        fail("No signed application found in frontend/release.")
    for app in apps:
        check_app(app, team_id)
    # Check every signature before sending anything to Apple.
    cli = single(release.glob("raticode-cli-macos-*"), "macOS CLI")
    run("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", cli)
    require_developer_id(cli, team_id)

    dmg = single(release.glob("*.dmg"), "DMG")
    # electron-builder signs a DMG without a secure timestamp, so Raticode signs it here.
    run(
        "/usr/bin/codesign",
        "--force",
        "--sign",
        environ["RATICODE_IDENTITY"],
        "--timestamp",
        "--keychain",
        environ["RATICODE_KEYCHAIN"],
        dmg,
    )
    run("/usr/bin/codesign", "--verify", "--strict", "--verbose=2", dmg)
    require_developer_id(dmg, team_id, executable=False)
    notarize(dmg, release / "notarization-macos-dmg.json", environ)
    # Stapling adds the ticket to the DMG, so it opens offline on first launch. That
    # changes its bytes: rebuild the blockmap and the latest-mac.yml entry from the
    # final file. verify-update-metadata.cjs then checks them before checksums.
    run("xcrun", "stapler", "staple", dmg)
    run("xcrun", "stapler", "validate", dmg)
    run(
        "/usr/sbin/spctl",
        "--assess",
        "--type",
        "open",
        "--context",
        "context:primary-signature",
        "--verbose=2",
        dmg,
    )
    run("node", ROOT / "scripts/refresh-mac-update-metadata.cjs", release, dmg.name)

    # A bare executable can't hold a stapled ticket; Gatekeeper looks its ticket up online.
    with tempfile.TemporaryDirectory(prefix="raticode-cli-notarization-") as temporary:
        archive = Path(temporary) / f"{cli.name}.zip"
        run("/usr/bin/ditto", "-c", "-k", "--keepParent", cli, archive)
        notarize(archive, release / "notarization-macos-cli.json", environ)
    print("macOS app, DMG and CLI are Developer ID signed and notarized; app and DMG are stapled.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("prepare", "verify", "cleanup"))
    command = parser.parse_args(argv).command
    if sys.platform != "darwin":
        raise SystemExit("Mac release signing runs only on macOS.")
    try:
        if command == "prepare":
            write_outputs(os.environ.get("GITHUB_OUTPUT"), prepare(os.environ))
        elif command == "verify":
            verify(os.environ)
        else:
            cleanup(os.environ)
    except SigningError as error:
        print(f"::error title=macOS signing::{error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
