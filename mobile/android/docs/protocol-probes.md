# Reproduce the mobile protocol probes

The commands below remain useful for disposable protocol checks. Milestone revisions, counts and failure descriptions are historical evidence, not current release status. The integrated app now has an opt-in live peer; use [live setup](live-device-setup.md) and [release readiness](release-readiness.md) for current capabilities and gates.

Use JDK 21, SDK platform 37 and the committed wrapper. All identities are public test
fixtures. These tests demonstrate protocol/library feasibility, not complete
pairing, desktop work, background sync or external cryptographic review.

For ordinary local validation:

```bash
./gradlew assembleDebug testDebugUnitTest lintDebug
.venv-contract/bin/python scripts/check-contract.py
```

The JVM interop tests use the desktop source in this repository and the `.venv` at its
root, with device dependencies. They report skipped when either is missing. A
worktree needs its own `.venv` link; the tests never look above their own checkout. The ordinary TLS, framing, binding, capability and Tink tests
always run. To require actual Python exchanges for evidence, set the path, which turns a
missing environment into a failure, and force the test task to execute if a previous run was cached:

```bash
RATICODE_DESKTOP_PROBE_ROOT=$(git rev-parse --show-toplevel) \
  ./gradlew testDebugUnitTest --rerun-tasks
```

The desktop revision used for this candidate's successful probes is
`d39227d4ae607f57272097399402be9c0417ee58`. It supplies
`scripts/device_tls_probe.py`, `src/gofer/devices/tls.py` and public fixture
identities. Python/JVM exchange JSONL over subprocess pipes. No network is needed.

For actual Android/Python TLS, create a disposable emulator rather than
installing over a user's workspace. This run used `Swarm_Mobile_Protocol_API36`,
Android 16/API 36 x86_64 Google APIs revision 7, serial `emulator-5582`.
Existing emulator `emulator-5580` and the user's original AVD were left alone.

```bash
python scripts/probes/tls-peer-bridge.py
# In another terminal; bridge binds only host 127.0.0.1.
adb -s emulator-5582 reverse tcp:38643 tcp:38643
ANDROID_SERIAL=emulator-5582 ./gradlew connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.interop=true
# Stop the fixture bridge and remove the test forwarding when done.
adb -s emulator-5582 reverse --remove tcp:38643
```

The explicit instrumentation argument enables four fixture-bridge tests. Without
it those tests skip; ordinary local engine/Tink tests still run. The application INTERNET permission enables this test path. The current live UI uses a separate authenticated peer boundary. The bridge only starts the public-fixture Python TLS probe;
it has no workflow or user-data entry point. Its client exchanges plaintext test
labels for assertions, so it must remain loopback-only and never be deployed.

Tests cover TLS 1.3 mutual pinning, byte fragmentation, bidirectional JVM and
Android exchanges with Python, wrong/absent ALPN, wrong pins, pinned expired/P384
certificates, extra certificate chains, altered TLS bytes, replay and prior-session
ciphertext, pre-handshake writes, bounded input, maximum 65536-byte JSON plus its
four-byte length, multiple records, carrier reordering/duplicates/gap deadline,
first encrypted binding including wrong invitation secret/hash/roles, and mobile
worker-grant/request rejection. Tink tests cross-decrypt Python and Java ciphertext,
use multiple segments, and reject wrong AAD/key, modification, truncation and
extra bytes. These are no substitute for subsequent invitation-race, Keystore,
process-death, revocation, dispatch-once, real relay and file-service tests.

`docs/evidence/mobile-protocol.json` records the executed commands, environments,
source/fixture hashes and result counts. Raw developer-run logs and XML reports
are retained in ignored `artifacts/protocol/` for managed evidence hashing, not
committed as build output. The managed verifier also runs the ordinary build and
schema commands on the committed candidate. Exact commit IDs and reviewer verdicts
belong to the shared swarm board and managed submission.

At the historical protocol milestone, the security report was rechecked against source: `LocalVault.importFile`
still performs blocking provider IO without cancellation, and the declared build
versions still match the reported baseline. Advisory revalidation and compatible
patches, plus the stalled-import regression, are assigned to the separate
`m-security` milestone before pairing. No advisory is claimed fixed here.

Remaining evidence gaps include API 29 vendor devices, physical camera/voice and
background delivery, public ntfy/network conditions, a phone plus two desktops,
and independent security review. The local demo remains separate from the integrated live screen.

Final scoped protocol instrumentation passed 13 tests with no skips. The broader
29-test run failed one existing landscape Back/IME test, and an isolated rerun
failed waiting for IME visibility. Those failures are retained in evidence. The
coordinator was asked to carry the UI fix into m-security; full device readiness
is not claimed. Final unit suite passed 44 tests with no skips.

The managed runner did not inherit login-shell JDK/SDK variables. Its initial
verification failed to locate the SDK before compilation. A later attempt failed
because Gradle 9.1 cannot resolve a relative `.jdk21` installation path during
daemon discovery. That property has been removed. The unchanged wrapper and
portable Java 21 daemon criteria remain. Managed check commands must explicitly
supply JAVA_HOME, ANDROID_HOME and ANDROID_SDK_ROOT, with installed absolute paths
kept in managed configuration, never committed source. Integration worktrees need
the same environment. The schema environment remains local to each assignment.

The explicit-environment recovery reran all 44 JVM tests, including nine Python
exchanges, against Desktop `2c5f89392942207080330d46bb0736d5bba33899`.
Build, unit, lint and schema checks passed.
`docs/evidence/mobile-protocol-explicit-env.json` records the recovered input
provenance and fresh artifact hashes. Earlier evidence remains historical.
The emulator suite was not rerun during this recovery.
