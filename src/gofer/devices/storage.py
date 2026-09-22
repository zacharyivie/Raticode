"""OS-protected device identity. No file, chained, or environment keyring fallback."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from gofer.devices.tls import certificate_pin


class StorageError(ValueError):
    """Safe local error without credential material."""


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...
    def put(self, account: str, value: str) -> None: ...


class OSSecretStore:
    def __init__(self) -> None:
        try:
            # Select explicitly, ignoring user-configured plaintext/chainer backends.
            if sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
            elif sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring as Keyring
            elif sys.platform.startswith("linux"):
                from keyring.backends.SecretService import Keyring
            else:
                raise StorageError("protected_store_unsupported")
            backend: Any = Keyring
            self._backend: Any = backend()
            if self._backend.priority <= 0:
                raise StorageError("protected_store_unavailable")
        except Exception:
            raise StorageError("protected_store_unavailable") from None

    def get(self, account: str) -> str | None:
        try:
            result = self._backend.get_password("Raticode devices v2", account)
            if result is not None and not isinstance(result, str):
                raise StorageError("protected_store_invalid")
            return result
        except Exception:
            raise StorageError("protected_store_locked_or_unavailable") from None

    def put(self, account: str, value: str) -> None:
        try:
            self._backend.set_password("Raticode devices v2", account, value)
            if self.get(account) != value:
                raise StorageError("protected_store_write_failed")
        except Exception:
            raise StorageError("protected_store_write_failed") from None


@dataclass(frozen=True)
class Identity:
    device_id: str
    certificate: bytes = field(repr=False)
    private_key: bytes = field(repr=False)
    storage_key: bytes = field(repr=False)

    @property
    def pin(self) -> bytes:
        return certificate_pin(x509.load_pem_x509_certificate(self.certificate))

    @classmethod
    def create(cls) -> Identity:
        device_id = str(uuid4())
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        return cls(
            device_id,
            cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            AESGCM.generate_key(bit_length=256),
        )

    def encode(self) -> str:
        return json.dumps(
            {
                "device_id": self.device_id,
                "certificate": self.certificate.decode(),
                "private_key": self.private_key.decode(),
                "storage_key": base64.b64encode(self.storage_key).decode(),
            }
        )

    @classmethod
    def decode(cls, value: str) -> Identity:
        try:
            data = json.loads(value)
            identity = cls(
                data["device_id"],
                data["certificate"].encode(),
                data["private_key"].encode(),
                base64.b64decode(data["storage_key"], validate=True),
            )
            key = serialization.load_pem_private_key(identity.private_key, None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise ValueError()
            cert = x509.load_pem_x509_certificate(identity.certificate)
            if cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ) != key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ):
                raise ValueError()
            cert.verify_directly_issued_by(cert)
            AESGCM(identity.storage_key)
            return identity
        except Exception:
            raise StorageError("protected_identity_invalid") from None


def account_for(directory: Path) -> str:
    return hashlib.sha256(str(directory.resolve()).encode()).hexdigest()
