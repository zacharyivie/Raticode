"""Real library protocol probes; no network, user keys, or provider execution."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tink
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from tink import json_proto_keyset_format, secret_key_access, streaming_aead

from gofer.devices.binding import SessionBinding
from gofer.devices.framing import (
    MAX_CHUNK,
    MAX_FRAME,
    MAX_REORDER,
    Frame,
    FrameError,
    OrderedFrames,
    Records,
    encode_record,
    strict_json,
)
from gofer.devices.tls import MAX_FLIGHT, MAX_PLAINTEXT, DeviceTLS, DeviceTLSError, certificate_pin

FIXTURES = Path(__file__).parents[1] / "fixtures/device_protocol"
PINS = json.loads((FIXTURES / "pins.json").read_text())
CONTRACT = Path(__file__).parents[2] / "src/gofer/devices/protocol/v2"


def peer(name: str, other: str | None, *, server: bool, enrollment: bool = False) -> DeviceTLS:
    return DeviceTLS(
        certificate_pem=(FIXTURES / f"{name}-test-only.cert.pem").read_bytes(),
        private_key_pem=(FIXTURES / f"{name}-test-only.key.pem").read_bytes(),
        peer_pin=None if other is None else bytes.fromhex(PINS[other]),
        server=server,
        enrollment=enrollment,
    )


def handshake(client: DeviceTLS, server: DeviceTLS) -> None:
    for _ in range(20):
        client.feed(server.drain())
        server.feed(client.drain())
        if client.handshake_complete and server.handshake_complete:
            # Drain post-handshake records before tamper/replay cases.
            client.feed(server.drain())
            client.receive()
            return
    pytest.fail("TLS handshake did not finish")


def pair() -> tuple[DeviceTLS, DeviceTLS]:
    client = peer("mobile", "desktop", server=False)
    server = peer("desktop", "mobile", server=True)
    handshake(client, server)
    return client, server


def test_mutual_pins_and_bidirectional_encryption() -> None:
    client, server = pair()
    assert client.peer_pin == bytes.fromhex(PINS["desktop"])
    assert server.peer_pin == bytes.fromhex(PINS["mobile"])
    for sender, receiver in ((client, server), (server, client)):
        message = b"public test: hello Rem " * 1000
        sender.send(message)
        ciphertext = sender.drain()
        assert b"hello Rem" not in ciphertext
        # Simulate transport fragmentation without assuming TLS record boundaries.
        for offset in range(0, len(ciphertext), 1024):
            receiver.feed(ciphertext[offset : offset + 1024])
        assert b"".join(receiver.receive()) == message


@pytest.mark.parametrize("wrong_side", ["client", "server"])
def test_wrong_pinned_identity_fails(wrong_side: str) -> None:
    client = peer("mobile", "other" if wrong_side == "client" else "desktop", server=False)
    server = peer("desktop", "other" if wrong_side == "server" else "mobile", server=True)
    with pytest.raises(DeviceTLSError, match="tls_authentication_failed"):
        handshake(client, server)


@pytest.mark.parametrize("identity", ["expired", "p384"])
@pytest.mark.parametrize("server_role", [False, True])
def test_pinned_but_invalid_certificate_fails(identity: str, server_role: bool) -> None:
    client = peer(
        "mobile" if server_role else identity, identity if server_role else "desktop", server=False
    )
    server = peer(
        identity if server_role else "desktop", "mobile" if server_role else identity, server=True
    )
    with pytest.raises(DeviceTLSError):
        handshake(client, server)


@pytest.mark.parametrize("offered", [[], [b"wrong-protocol"]])
def test_missing_or_wrong_alpn_fails(monkeypatch, offered: list[bytes]) -> None:
    from OpenSSL import SSL

    original = SSL.Context.set_alpn_protos
    monkeypatch.setattr(
        SSL.Context,
        "set_alpn_protos",
        lambda ctx, protocols: original(ctx, offered) if offered else None,
    )
    client = peer("mobile", "desktop", server=False)
    server = peer("desktop", "mobile", server=True)
    with pytest.raises(DeviceTLSError):
        handshake(client, server)


@pytest.mark.parametrize("alpn", ["correct", "wrong", "none"])
def test_jsonl_probe_preserves_openssl_context_class(alpn: str) -> None:
    result = subprocess.run(
        [sys.executable, str(FIXTURES.parents[2] / "scripts/device_tls_probe.py"), "--alpn", alpn],
        input='{"feed":""}\n',
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"ciphertext": "", "plaintext": [], "handshake": False}


@pytest.mark.parametrize("operation", ["tamper", "replay", "fresh_session"])
def test_tls_rejects_modified_replayed_and_cross_session_records(operation: str) -> None:
    client, server = pair()
    client.send(b"immutable test event")
    frame = client.drain()
    if operation == "replay":
        server.feed(frame)
        assert server.receive() == [b"immutable test event"]
    elif operation == "tamper":
        frame = frame[:-1] + bytes([frame[-1] ^ 1])
    else:
        _, server = pair()
    with pytest.raises(DeviceTLSError, match="tls_authentication_failed"):
        server.feed(frame)
        server.receive()
    with pytest.raises(DeviceTLSError, match="session_closed"):
        server.feed(b"")


def test_enrollment_possession_does_not_claim_trust() -> None:
    client = peer("mobile", "desktop", server=False)
    server = peer("desktop", None, server=True, enrollment=True)
    handshake(client, server)
    assert server.enrollment
    assert server.peer_pin == bytes.fromhex(PINS["mobile"])
    # Registry, invitation consumption, confirmation, and dispatch belong to a
    # separate authorization layer. A TLS handshake cannot call any of them.


def test_unpinned_client_and_implicit_enrollment_are_rejected() -> None:
    with pytest.raises(DeviceTLSError, match="invalid_authentication_mode"):
        peer("mobile", None, server=False)
    with pytest.raises(DeviceTLSError, match="invalid_authentication_mode"):
        peer("mobile", None, server=False, enrollment=True)
    with pytest.raises(DeviceTLSError, match="invalid_authentication_mode"):
        peer("desktop", None, server=True)


def test_no_application_write_before_handshake() -> None:
    client = peer("mobile", "desktop", server=False)
    with pytest.raises(DeviceTLSError, match="handshake_incomplete"):
        client.send(b"work")


def test_bounded_input_and_output() -> None:
    client, server = pair()
    with pytest.raises(DeviceTLSError, match="plaintext_limit"):
        client.send(b"x" * (MAX_PLAINTEXT + 1))
    with pytest.raises(DeviceTLSError, match="ciphertext_limit"):
        server.feed(b"x" * (MAX_FLIGHT + 1))


def test_cumulative_input_bound_without_pumping() -> None:
    client, server = pair()
    # Feed distinct valid ciphertext records without consuming them. Each
    # individual feed fits the limit, but the cumulative queue must not grow.
    with pytest.raises(DeviceTLSError, match="ciphertext_limit"):
        for _ in range(10):
            client.send(b"x" * 65536)
            server.feed(client.drain())


@pytest.mark.parametrize("fragment_size", [1, 2048, 200000])
def test_tls_and_records_preserve_maximum_and_coalesced_records(fragment_size: int) -> None:
    client, server = pair()
    maximum = {"x": "y" * (65536 - 8)}
    small = {"type": "small"}
    payload = encode_record(maximum) + encode_record(small) + encode_record(small)
    assert len(encode_record(maximum)) == 65540
    client.send(payload)
    wire = client.drain()
    decoder = Records()
    received = []
    for offset in range(0, len(wire), fragment_size):
        server.feed(wire[offset : offset + fragment_size])
        while chunks := server.receive():
            for chunk in chunks:
                received.extend(decoder.feed(chunk))
    assert received == [maximum, small, small]


def test_shared_fixture_pins_are_der_spki() -> None:
    for name, expected in PINS.items():
        cert = x509.load_pem_x509_certificate(
            (FIXTURES / f"{name}-test-only.cert.pem").read_bytes()
        )
        assert certificate_pin(cert).hex() == expected
        key = serialization.load_pem_private_key(
            (FIXTURES / f"{name}-test-only.key.pem").read_bytes(), password=None
        )
        assert key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) == cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )


def streaming_primitive() -> streaming_aead.StreamingAead:
    streaming_aead.register()
    handle = json_proto_keyset_format.parse(
        (FIXTURES / "streaming-test-only.keyset.json").read_text(), secret_key_access.TOKEN
    )
    return handle.primitive(streaming_aead.StreamingAead)


def test_shared_python_streaming_fixture() -> None:
    metadata = json.loads((FIXTURES / "streaming.json").read_text())
    ciphertext = (FIXTURES / "python-streaming.ciphertext").read_bytes()
    assert hashlib.sha256(ciphertext).hexdigest() == metadata["ciphertext_sha256"]
    with streaming_primitive().new_decrypting_stream(
        io.BytesIO(ciphertext), metadata["aad_utf8"].encode()
    ) as stream:
        assert stream.read() == bytes(range(256)) * 128


def test_shared_java_streaming_fixture() -> None:
    metadata = json.loads((FIXTURES / "streaming.json").read_text())
    with streaming_primitive().new_decrypting_stream(
        (FIXTURES / "java-streaming.ciphertext").open("rb"), metadata["aad_utf8"].encode()
    ) as stream:
        assert stream.read() == bytes(range(256)) * 128


@pytest.mark.parametrize("invalid", ["future", "not_self_signed"])
def test_invalid_certificate_rejected_even_with_exact_pin(invalid: str) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "public disposable test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(
            now + timedelta(days=1) if invalid == "future" else now - timedelta(days=1)
        )
        .not_valid_after(now + timedelta(days=10))
        .sign(
            key if invalid == "future" else ec.generate_private_key(ec.SECP256R1()), hashes.SHA256()
        )
    )
    client = DeviceTLS(
        certificate_pem=certificate.public_bytes(serialization.Encoding.PEM),
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        peer_pin=bytes.fromhex(PINS["desktop"]),
        server=False,
    )
    server = DeviceTLS(
        certificate_pem=(FIXTURES / "desktop-test-only.cert.pem").read_bytes(),
        private_key_pem=(FIXTURES / "desktop-test-only.key.pem").read_bytes(),
        peer_pin=certificate_pin(certificate),
        server=True,
    )
    with pytest.raises(DeviceTLSError):
        handshake(client, server)


def test_extra_certificate_chain_is_rejected(monkeypatch) -> None:
    from OpenSSL import SSL

    original = SSL.Context.use_certificate

    def add_extra(ctx, cert):
        original(ctx, cert)
        ctx.add_extra_chain_cert(cert)

    monkeypatch.setattr(SSL.Context, "use_certificate", add_extra)
    client = peer("mobile", "desktop", server=False)
    server = peer("desktop", "mobile", server=True)
    with pytest.raises(DeviceTLSError):
        handshake(client, server)


def test_missing_client_certificate_is_rejected(monkeypatch) -> None:
    from OpenSSL import SSL

    server = peer("desktop", "mobile", server=True)
    monkeypatch.setattr(SSL.Context, "use_certificate", lambda ctx, cert: None)
    monkeypatch.setattr(SSL.Context, "use_privatekey", lambda ctx, key: None)
    monkeypatch.setattr(SSL.Context, "check_privatekey", lambda ctx: None)
    client = peer("mobile", "desktop", server=False)
    with pytest.raises(DeviceTLSError):
        handshake(client, server)


def test_attempted_tls_resumption_still_requires_fresh_certificate_validation() -> None:
    client, _ = pair()
    prior_session = client._connection.get_session()
    assert prior_session is not None
    resumed_client = peer("mobile", "desktop", server=False)
    with pytest.raises(ValueError, match="same Context"):
        resumed_client._connection.set_session(prior_session)
    resumed_server = peer("desktop", "mobile", server=True)
    handshake(resumed_client, resumed_server)
    # These fields are populated only by each new certificate verify callback.
    assert resumed_client.peer_pin == bytes.fromhex(PINS["desktop"])
    assert resumed_server.peer_pin == bytes.fromhex(PINS["mobile"])
    rejected_client = peer("mobile", "other", server=False)
    with pytest.raises(ValueError, match="same Context"):
        rejected_client._connection.set_session(prior_session)
    with pytest.raises(DeviceTLSError):
        handshake(rejected_client, peer("desktop", "mobile", server=True))


@pytest.mark.parametrize("corruption", ["aad", "key", "truncate", "tamper", "append"])
def test_streaming_rejects_corruption(corruption: str) -> None:
    metadata = json.loads((FIXTURES / "streaming.json").read_text())
    ciphertext = (FIXTURES / "python-streaming.ciphertext").read_bytes()
    aad = metadata["aad_utf8"].encode()
    primitive = streaming_primitive()
    if corruption == "aad":
        aad += b"other offer"
    elif corruption == "key":
        primitive = tink.new_keyset_handle(
            streaming_aead.streaming_aead_key_templates.AES128_GCM_HKDF_1MB
        ).primitive(streaming_aead.StreamingAead)
    elif corruption == "truncate":
        ciphertext = ciphertext[:-1]
    elif corruption == "tamper":
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    else:
        ciphertext += b"extra"
    with pytest.raises(tink.TinkError):
        with primitive.new_decrypting_stream(io.BytesIO(ciphertext), aad) as stream:
            stream.read()


def test_streaming_crosses_segment_boundary_and_rejects_incomplete_final_segment(tmp_path) -> None:
    primitive = streaming_primitive()
    source = bytes(range(256)) * 8193
    path = tmp_path / "encrypted.bin"
    with primitive.new_encrypting_stream(path.open("wb"), b"public multi-segment test") as stream:
        for offset in range(0, len(source), 8192):
            stream.write(source[offset : offset + 8192])
    with primitive.new_decrypting_stream(path.open("rb"), b"public multi-segment test") as stream:
        assert stream.read() == source
    # A valid first segment does not establish whole-file completion.
    with pytest.raises(tink.TinkError):
        with primitive.new_decrypting_stream(
            io.BytesIO(path.read_bytes()[:1048576]), b"public multi-segment test"
        ) as stream:
            stream.read()


SESSION = "00000000-0000-4000-8000-000000000001"
DIRECTION = "initiator_to_responder"


def frame(index: int, body: bytes = b"test bytes") -> bytes:
    return Frame(SESSION, "resume", DIRECTION, index, body).encode()


def test_frame_maximum_fits_ntfy_profile() -> None:
    raw = frame(2**53 - 1, b"x" * MAX_CHUNK)
    assert len(raw) <= MAX_FRAME
    assert len(Frame.decode(raw).body) == MAX_CHUNK
    with pytest.raises(FrameError):
        frame(0, b"x" * (MAX_CHUNK + 1))


def test_reorder_boundary_and_duplicate_do_not_reach_tls_twice() -> None:
    receiver = OrderedFrames(SESSION, "resume", DIRECTION)
    for index in range(1, MAX_REORDER + 1):
        assert receiver.accept(frame(index, b"x" * MAX_CHUNK), now=0) == b""
    assert receiver.accept(frame(1, b"x" * MAX_CHUNK), now=1) == b""
    assert receiver.accept(frame(0, b"x" * MAX_CHUNK), now=2) == b"x" * (
        MAX_CHUNK * (MAX_REORDER + 1)
    )
    assert receiver.accept(frame(0), now=3) == b""


@pytest.mark.parametrize("failure", ["window", "conflict", "deadline", "binding"])
def test_reorder_failure_closes_session(failure: str) -> None:
    receiver = OrderedFrames(SESSION, "resume", DIRECTION)
    receiver.accept(frame(1), now=0)
    with pytest.raises(FrameError):
        if failure == "window":
            receiver.accept(frame(MAX_REORDER + 1), now=1)
        elif failure == "conflict":
            receiver.accept(frame(1, b"changed"), now=1)
        elif failure == "deadline":
            receiver.accept(frame(0), now=30)
        else:
            receiver.accept(Frame(SESSION, "pair", DIRECTION, 0, b"changed").encode(), now=1)
    with pytest.raises(FrameError, match="session_closed"):
        receiver.accept(frame(0), now=1)


def test_partial_record_and_multiple_records() -> None:
    record = {"type": "public-test", "text": "Rem \u00e9"}
    wire = encode_record(record)
    decoder = Records()
    for octet in wire[:-1]:
        assert decoder.feed(bytes([octet])) == []
    assert decoder.feed(wire[-1:] + wire) == [record, record]


@pytest.mark.parametrize("length", [0, 65537, 2**32 - 1])
def test_record_rejects_length_before_buffering_body(length: int) -> None:
    with pytest.raises(FrameError, match="record_size_limit"):
        Records().feed(length.to_bytes(4, "big"))


@pytest.mark.parametrize(
    "data",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":1e999}',
        b'{"a":"\\ud800"}',
        b'{"a":"\xff"}',
        b"{}{}",
        b"[]",
        b'{"a":' + b"[" * 17 + b"0" + b"]" * 17 + b"}",
    ],
)
def test_strict_json_rejects_ambiguous_or_unbounded_input(data: bytes) -> None:
    with pytest.raises(FrameError):
        strict_json(data)


@pytest.mark.parametrize(
    "changed",
    [
        "secret",
        "invitation_sha256",
        "session_id",
        "mode",
        "initiator_id",
        "responder_id",
        "initiator_role",
        "responder_role",
        "profile",
        "extra",
    ],
)
def test_encrypted_pair_binding_rejects_wrong_secret_or_transcript_context(changed: str) -> None:
    binding = SessionBinding(SESSION, "pair", SESSION, SESSION, "controller", "a" * 64, "A" * 43)
    record = binding.record()
    binding.verify(record)
    record[changed] = "wrong"
    client, server = pair()
    client.send(encode_record(record))
    server.feed(client.drain())
    records = Records()
    for chunk in server.receive():
        for received in records.feed(chunk):
            with pytest.raises(FrameError, match="session_binding"):
                binding.verify(received)


def test_enrollment_and_resume_binding_cannot_mix() -> None:
    with pytest.raises(FrameError):
        SessionBinding(SESSION, "resume", SESSION, SESSION, "controller", "a" * 64, "A" * 43)
    with pytest.raises(FrameError):
        SessionBinding(SESSION, "pair", SESSION, SESSION, "controller")


@pytest.mark.parametrize("collection", [[0] * 1025, {str(i): i for i in range(1025)}])
def test_collection_limit_matches_mobile_contract(collection) -> None:
    with pytest.raises(FrameError, match="json_collection_limit"):
        strict_json(json.dumps({"items": collection}).encode())


@pytest.mark.parametrize("collection", [[0] * 1024, {str(i): i for i in range(1024)}])
def test_collection_exact_limit_is_accepted(collection) -> None:
    assert strict_json(json.dumps({"items": collection}).encode()) == {"items": collection}


def test_canonical_mobile_contract_manifest() -> None:
    manifest = (CONTRACT / "SHA256SUMS").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == (
        "2265d6a218dfb844cc273bfbd951c8b554eeffa39ad474dea432a477e24da4b8"
    )
    for line in manifest.decode().splitlines():
        digest, relative = line.split("  ", 1)
        assert hashlib.sha256((CONTRACT / relative).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "path", sorted((CONTRACT / "fixtures").glob("*.json")), ids=lambda p: p.stem
)
def test_canonical_mobile_schema_fixtures(path: Path) -> None:
    name = path.stem if path.stem in {"invitation", "frame", "session"} else "event"
    schema = json.loads((CONTRACT / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(strict_json(path.read_bytes()))


@pytest.mark.parametrize(
    "kind,fixture,field,value",
    [
        ("frame", "frame", "index", -1),
        ("frame", "frame", "body", "AQ=="),
        ("frame", "frame", "direction", "phone_to_desktop"),
        ("frame", "frame", "extra", True),
        ("session", "session", "secret", "A" * 43),
        ("session", "session", "mode", "pair"),
        ("session", "session", "responder_role", "controller"),
        ("event", "job.submit", "thread_id", None),
        ("event", "job.submit", "type", "shell.exec"),
        ("event", "job.submit", "request_id", None),
    ],
)
def test_canonical_mobile_negative_shapes(kind: str, fixture: str, field: str, value) -> None:
    schema = json.loads((CONTRACT / f"{kind}.schema.json").read_text())
    record = strict_json((CONTRACT / f"fixtures/{fixture}.json").read_bytes())
    record[field] = value
    assert not Draft202012Validator(schema).is_valid(record)


def test_canonical_mobile_worker_grant_is_forbidden() -> None:
    schema = json.loads((CONTRACT / "session.schema.json").read_text())
    record = {
        "type": "session.ready",
        "device_id": SESSION,
        "role": "controller",
        "capabilities": ["worker.execute"],
        "initiator_topic": "A" * 43,
        "responder_topic": "B" * 43,
        "trust_revision": 0,
    }
    assert not Draft202012Validator(schema).is_valid(record)
