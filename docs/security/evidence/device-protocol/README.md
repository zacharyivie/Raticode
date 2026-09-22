# Desktop protocol probe evidence

Base revision: `dc109fa58fe412f5ab3d05f1fad837b9b28ef0e7`.
The candidate revision is recorded by the managed swarm Git receipt and board
handoff so this file does not contain a circular self-referential commit hash.
`checks.json` hashes each tested source file and records command exit codes.
`fixture-sha256.json` hashes all public interoperability fixtures.

Setup in the isolated assignment worktree:

```sh
uv sync --locked --extra dev --extra devices
.venv/bin/python -m pytest tests/unit/test_device_protocol.py -q
.venv/bin/ruff check src tests --fix
.venv/bin/mypy src tests
```

The final local run passed 109 tests, Ruff and mypy. Logs are alongside this file.
No UI/CLI/executor behavior changes in this milestone, so no frontend or full
workflow suite was required. No workflow or paid provider was invoked. The
system Python lacks the test environment; use the assignment-local venv.
The coordinator must change managed checks after reconciling this active
attempt, because the manager rejected changes to commands mid-assignment.
This is not a managed verification receipt yet.

Coverage includes mutual P-256 pinning; wrong pin both ways; absent/wrong ALPN;
expired, future, P-384, invalid self-signature, extra-chain and absent client
certificates; explicit enrollment mode; fresh-context rejection of old TLS
sessions; tamper, replay and cross-session ciphertext; cumulative memory BIO
bounds; maximum 65536-byte JSON plus prefix and coalesced messages under one-byte,
2048-byte and whole-flight delivery; exact first encrypted binding including
wrong invitation secret/hash/role; bounded carrier reordering and gap timeout;
strict parser negatives; Tink Java-to-Python fixture decryption; multisegment
streaming and key/AAD/tamper/truncation/appended-data failures.

Python crypto versions are pinned in `pyproject.toml` and `uv.lock`.
The tested pyOpenSSL wheel uses OpenSSL 4.0.2. The Python stdlib's separately
linked OpenSSL is not the TLS engine used by this adapter. Mypy 2.1.0 and
Ruff 0.15.17 match the repository lockfile. An initial unpinned mypy 2.3.1 run
found three pre-existing unrelated type errors; no baseline source was changed
to work around them. The locked run passes.

Mobile dev owns Android/JVM test artifacts and exact mobile revision evidence.
Its tests invoke `scripts/device_tls_probe.py` with newline JSON and standard
base64 flights, in both endpoint roles. Tests use the same public fixture bytes.
The Java-generated Tink ciphertext was copied from Mobile dev's build artifact
and is committed as `tests/fixtures/device_protocol/java-streaming.ciphertext`;
the Python decryption assertion checks the entire deterministic 32768-byte file.
The converse Python fixture is in the same directory for mobile tests.

Canonical mobile schemas and fixtures are copied without alteration under
`src/gofer/devices/protocol/v2`. The manifest SHA-256 is
`f3faf4bb173b6fe4238530cf271c9fbe647025ce7587195cb1e7401ac9ba8e60`.
Tests verify this manifest, every listed file, all 27 positive fixtures and
negative shapes including controller worker grants. `mobile-interop.json`
records hashes/counts for Mobile dev's actual JVM and emulator artifacts, read
from its isolated protocol assignment. Those tests were run by Mobile dev;
the coordinator must attach the final exact mobile revision and verdict.

Protocol probes do not implement the subsequent device registry, authorization,
network service, transactional dispatch, UI, or file service milestones.
Independent cryptographic review, physical-device validation, real internet
relay testing and host key-store coverage remain open. See
[protocol decisions](../../device-protocol-v2.md) for limits and integration plans.
