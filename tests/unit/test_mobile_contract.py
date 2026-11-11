"""Checks that span the desktop and the phone app in mobile/android."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PHONE_IDENTITIES = ROOT / "mobile/android/protocol/probes/public-test-identities"
DESKTOP_IDENTITIES = ROOT / "tests/fixtures/device_protocol"


def desktop_name(phone_name: str) -> str:
    # The two apps name the same dummy files differently.
    for peer in ("desktop", "mobile", "expired", "other", "p384"):
        if phone_name == f"{peer}.cert.pem":
            return f"{peer}-test-only.cert.pem"
        if phone_name == f"{peer}.test-key.pem":
            return f"{peer}-test-only.key.pem"
    return phone_name


def test_the_protocol_has_one_copy() -> None:
    assert (ROOT / "src/gofer/devices/protocol/v2/SHA256SUMS").is_file()
    assert not (ROOT / "mobile/android/protocol/v2").exists()


@pytest.mark.parametrize(
    "phone_file",
    [p for p in sorted(PHONE_IDENTITIES.iterdir()) if p.name != "SHA256SUMS"],
    ids=lambda p: p.name,
)
def test_both_apps_share_the_same_dummy_identities(phone_file: Path) -> None:
    assert (
        phone_file.read_bytes() == (DESKTOP_IDENTITIES / desktop_name(phone_file.name)).read_bytes()
    )


def test_contract_script_reads_the_desktop_copy() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mobile/android/scripts/check-contract.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "v2 fixtures" in result.stdout
