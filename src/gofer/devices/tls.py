"""TLS 1.3 memory-BIO feasibility adapter for the version 2 device protocol.

TLS proves possession of a certificate key. This module grants no device trust,
capability, enrollment, or execution authority. The caller must enforce those
before processing application records. Cipher state is never serialized.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from OpenSSL import SSL, crypto

MAX_FLIGHT = 256 * 1024
# TLS stream pumping budgets are independent of the 65536-byte JSON record
# limit. A framed record includes four additional bytes and may span drains.
MAX_PLAINTEXT = 128 * 1024
READ_BUDGET = 64 * 1024
ALPN = b"raticode/2"


class DeviceTLSError(ValueError):
    """Sanitized protocol failure; discard the session after this exception."""


def certificate_pin(certificate: x509.Certificate) -> bytes:
    """Return the SHA-256 of DER SubjectPublicKeyInfo, never a display label."""
    return hashlib.sha256(
        certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).digest()


def _valid_identity(certificate: x509.Certificate) -> bool:
    public_key = certificate.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        return False
    if not isinstance(public_key.curve, ec.SECP256R1):
        return False
    now = datetime.now(UTC)
    if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
        return False
    try:
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


class DeviceTLS:
    """Fresh, mutually authenticated TLS engine with explicit enrollment mode.

    A responder in enrollment mode accepts an unknown certificate solely to
    verify key possession. Only the higher-level one-time invitation and local
    desktop confirmation may authorize that identity. Clients always pin.
    """

    def __init__(
        self,
        *,
        certificate_pem: bytes,
        private_key_pem: bytes,
        peer_pin: bytes | None,
        server: bool,
        enrollment: bool = False,
        trusted_pins: frozenset[bytes] | None = None,
    ) -> None:
        registry_mode = server and not enrollment and trusted_pins is not None
        if trusted_pins is not None and (not registry_mode or peer_pin is not None):
            raise DeviceTLSError("invalid_authentication_mode")
        if trusted_pins is not None and any(len(pin) != 32 for pin in trusted_pins):
            raise DeviceTLSError("invalid_identity_pin")
        if (enrollment and (not server or peer_pin is not None)) or (
            peer_pin is None and not (server and enrollment) and not registry_mode
        ):
            raise DeviceTLSError("invalid_authentication_mode")
        if peer_pin is not None and len(peer_pin) != 32:
            raise DeviceTLSError("invalid_identity_pin")
        self._failed = False
        self._input_since_read = 0
        self._output_since_drain = 0
        self.handshake_complete = False
        self.peer_pin: bytes | None = None
        self.enrollment = enrollment
        self._server = server
        context = SSL.Context(SSL.TLS_METHOD)
        context.set_min_proto_version(SSL.TLS1_3_VERSION)
        context.set_max_proto_version(SSL.TLS1_3_VERSION)
        context.set_session_cache_mode(SSL.SESS_CACHE_OFF)
        context.set_options(SSL.OP_NO_TICKET | SSL.OP_NO_COMPRESSION)
        if server:

            def select_alpn(connection: SSL.Connection, protocols: list[bytes]) -> bytes:
                del connection
                if ALPN not in protocols:
                    raise SSL.Error("unsupported_application_protocol")
                return ALPN

            context.set_alpn_select_callback(select_alpn)
        else:
            context.set_alpn_protos([ALPN])
        # A fresh context per connection prevents resumption even on a peer that
        # emits TLS 1.3 tickets. No early-data APIs or TLS secrets logger is used.
        context.use_certificate(x509.load_pem_x509_certificate(certificate_pem))
        key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise DeviceTLSError("invalid_identity_key")
        context.use_privatekey(key)
        context.check_privatekey()

        def verify(
            connection: SSL.Connection,
            cert: crypto.X509,
            error_number: int,
            depth: int,
            preverified: int,
        ) -> bool:
            del connection, preverified
            # 18 means self-signed leaf. No other X.509 verification error is
            # waived; no public CA chain can stand in for a pinned identity.
            if depth != 0 or error_number not in (0, 18):
                return False
            identity = cert.to_cryptography()
            if not _valid_identity(identity):
                return False
            pin = certificate_pin(identity)
            if registry_mode:
                if pin not in (trusted_pins or frozenset()):
                    return False
            elif not enrollment and (peer_pin is None or not hmac.compare_digest(pin, peer_pin)):
                return False
            self.peer_pin = pin
            return True

        context.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, verify)
        context.set_verify_depth(0)
        self._connection = SSL.Connection(context, None)
        if server:
            self._connection.set_accept_state()
        else:
            self._connection.set_connect_state()

    def _check(self) -> None:
        if self._failed:
            raise DeviceTLSError("session_closed")

    def feed(self, ciphertext: bytes) -> None:
        self._check()
        self._input_since_read += len(ciphertext)
        if self._input_since_read > MAX_FLIGHT:
            self._failed = True
            raise DeviceTLSError("ciphertext_limit")
        try:
            if ciphertext:
                self._connection.bio_write(ciphertext)
            if not self.handshake_complete:
                try:
                    self._connection.do_handshake()
                except SSL.WantReadError:
                    return
                if self._connection.get_protocol_version_name() != "TLSv1.3":
                    raise DeviceTLSError("unsupported_tls_version")
                if self._connection.get_alpn_proto_negotiated() != ALPN:
                    self._failed = True
                    raise DeviceTLSError("unsupported_application_protocol")
                if self.peer_pin is None:
                    raise DeviceTLSError("peer_identity_missing")
                chain = self._connection.get_peer_cert_chain()
                # OpenSSL excludes the leaf from a server's peer-chain result,
                # but includes it on a client. The verified leaf is separate.
                if chain is not None and len(chain) != (0 if self._server else 1):
                    self._failed = True
                    raise DeviceTLSError("unsupported_certificate_chain")
                self.handshake_complete = True
        except SSL.Error:
            self._failed = True
            raise DeviceTLSError("tls_authentication_failed") from None

    def drain(self) -> bytes:
        self._check()
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = self._connection.bio_read(16384)
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_FLIGHT:
                    self._failed = True
                    raise DeviceTLSError("ciphertext_limit")
        except SSL.WantReadError:
            self._output_since_drain = 0
            return b"".join(chunks)

    def send(self, plaintext: bytes) -> None:
        self._check()
        if not self.handshake_complete:
            raise DeviceTLSError("handshake_incomplete")
        if not plaintext or len(plaintext) > MAX_PLAINTEXT:
            raise DeviceTLSError("plaintext_limit")
        self._output_since_drain += len(plaintext)
        if self._output_since_drain > MAX_PLAINTEXT:
            self._failed = True
            raise DeviceTLSError("plaintext_limit")
        try:
            offset = 0
            while offset < len(plaintext):
                sent = self._connection.send(plaintext[offset:])
                if sent <= 0:
                    self._failed = True
                    raise DeviceTLSError("partial_write")
                offset += sent
        except SSL.Error:
            self._failed = True
            raise DeviceTLSError("tls_write_failed") from None

    def receive(self) -> list[bytes]:
        """Read at most READ_BUDGET; callers pump until [] before more input.

        Feed each returned chunk to the incremental Records parser. Never join
        an unlimited stream or assume a TLS read ends an application record.
        """
        self._check()
        if not self.handshake_complete:
            return []
        chunks: list[bytes] = []
        total = 0
        try:
            while total < READ_BUDGET:
                chunk = self._connection.recv(min(16384, READ_BUDGET - total))
                if not chunk:
                    self._failed = True
                    raise DeviceTLSError("session_closed")
                total += len(chunk)
                chunks.append(chunk)
            return chunks
        except SSL.WantReadError:
            self._input_since_read = 0
            return chunks
        except SSL.Error:
            self._failed = True
            raise DeviceTLSError("tls_authentication_failed") from None
