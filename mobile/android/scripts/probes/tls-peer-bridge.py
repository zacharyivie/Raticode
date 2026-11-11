#!/usr/bin/env python3
"""Loopback-only public-fixture bridge to the desktop TLS JSONL probe.

Uses this repository's desktop source unless --desktop-root names another. Use adb reverse tcp:38643 tcp:38643.
The first JSON line chooses only the disposable peer's TLS role. Subsequent
lines go to the existing public-fixture probe. No desktop workflow can run.
"""
import argparse
import json
import os
from pathlib import Path
import socketserver
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--desktop-root", type=Path, default=Path(__file__).resolve().parents[4])
parser.add_argument("--port", type=int, default=38643)
args = parser.parse_args()
root = args.desktop_root.resolve()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        config = json.loads(self.rfile.readline(1024))
        if set(config) - {"role", "alpn"} or config.get("role") not in ("client", "server") or config.get("alpn", "correct") not in ("correct", "wrong", "none"):
            raise ValueError("invalid fixture role")
        environment = dict(os.environ, PYTHONPATH=str(root / "src"))
        with subprocess.Popen(
            [str(root / ".venv/bin/python"), "-u", "scripts/device_tls_probe.py",
             "--role", config["role"], "--identity", "desktop", "--peer", "mobile", "--alpn", config.get("alpn", "correct")],
            cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as peer:
            try:
                for _ in range(100):
                    request = self.rfile.readline(100000)
                    if not request:
                        break
                    if not request.endswith(b"\n"):
                        raise ValueError("probe input bound")
                    peer.stdin.write(request)
                    peer.stdin.flush()
                    response = peer.stdout.readline(100000)
                    if not response.endswith(b"\n"):
                        raise ValueError("probe output bound")
                    self.wfile.write(response)
                    self.wfile.flush()
            finally:
                peer.kill()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with Server(("127.0.0.1", args.port), Handler) as server:
    print("Public-fixture TLS bridge listening on loopback", flush=True)
    server.serve_forever()
