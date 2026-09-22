"""Public-fixture-only Python peer for Android/JVM TLS interoperability tests.

Run with the assignment venv. Input and output use JSON lines with standard
base64. This utility never listens on a network or runs desktop work.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from OpenSSL import SSL

from gofer.devices.tls import DeviceTLS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("client", "server"), default="server")
    identities = ("desktop", "mobile", "other", "expired", "p384")
    parser.add_argument("--identity", choices=identities, default="desktop")
    parser.add_argument("--peer", choices=identities, default="mobile")
    parser.add_argument("--alpn", choices=("correct", "wrong", "none"), default="correct")
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures/device_protocol"
    pins = json.loads((fixtures / "pins.json").read_text())
    # Patch methods only, preserving SSL.Context's type checks inside OpenSSL.
    with ExitStack() as stack:
        if args.alpn == "none":
            stack.enter_context(patch.object(SSL.Context, "set_alpn_protos", lambda ctx, ps: None))
            stack.enter_context(
                patch.object(SSL.Context, "set_alpn_select_callback", lambda ctx, cb: None)
            )
        elif args.alpn == "wrong":
            original_set = SSL.Context.set_alpn_protos
            original_select = SSL.Context.set_alpn_select_callback
            stack.enter_context(
                patch.object(
                    SSL.Context,
                    "set_alpn_protos",
                    lambda ctx, ps: original_set(ctx, [b"wrong-protocol"]),
                )
            )
            stack.enter_context(
                patch.object(
                    SSL.Context,
                    "set_alpn_select_callback",
                    lambda ctx, cb: original_select(
                        ctx, lambda connection, protocols: b"wrong-protocol"
                    ),
                )
            )
        engine = DeviceTLS(
            certificate_pem=(fixtures / f"{args.identity}-test-only.cert.pem").read_bytes(),
            private_key_pem=(fixtures / f"{args.identity}-test-only.key.pem").read_bytes(),
            peer_pin=bytes.fromhex(pins[args.peer]),
            server=args.role == "server",
        )
    for line in sys.stdin:
        try:
            record = json.loads(line)
            engine.feed(base64.b64decode(record.get("feed", ""), validate=True))
            if record.get("send"):
                engine.send(base64.b64decode(record["send"], validate=True))
            plaintext = []
            while chunks := engine.receive():
                plaintext.extend(chunks)
            response = {
                "ciphertext": base64.b64encode(engine.drain()).decode("ascii"),
                "plaintext": [base64.b64encode(chunk).decode("ascii") for chunk in plaintext],
                "handshake": engine.handshake_complete,
            }
            print(json.dumps(response), flush=True)
        except Exception:
            print(json.dumps({"error": "probe_failed"}), flush=True)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
