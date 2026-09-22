# Current live device behavior

September 20 update. Production mobile navigation is live-only. Earlier milestone descriptions below are historical where they refer to missing application handlers or manual thread IDs.

Device networking still requires the explicit environment opt-in. The default listener host is now the OS-selected outgoing private IPv4 address when available; a UDP route lookup obtains the address without transmitting a packet. Public, wildcard and multicast addresses are rejected. An explicit `RATICODE_DEVICE_HOST` remains authoritative. No opt-in means loopback-only and no public relay subscription.

Extended encrypted sync can return the actual LAN endpoint and Rem dispatcher readiness. The updated phone tries LAN first and checks it again while retaining a healthy relay until a pinned TLS replacement succeeds. Legacy sync requests retain their original response shape. Updated mobile and desktop should be installed together.

New phone identity confirmation enables workspace sync by default in the same durable transaction. Sync becomes available only after the phone acknowledges enrollment. Phones use the owner's desktop project catalog and provider approval policies without a separate phone project-access editor. Existing sync choices persist. Legacy scoped grants and desktop-to-desktop grants retain their limits. Thread histories survive provider/model changes.

`DeviceChatBridge` runs accepted requests through `ChatJobs` and `ChatSteering` with validated local permissions, independent of a renderer chat subscription. The local status API exposes dispatcher readiness and a generic error; View conversation reads authorized remote transcripts. Independent security review and physical/public-relay acceptance remain open.

---

The paired-phone list keeps thread sync and its grant count visible. **Project access** contains individual grants; **Device details** contains identity and revocation. **Connection and troubleshooting** shows the LAN IP and port, relay diagnostics and security-review status. Advanced setup is collapsed by default, and all sections use the existing light/dark settings styles. A paired label is saved trust, not evidence of a live connection.

Current implementation: [device messaging and fleet work](device-messaging.md).
Current release scope and acceptance: [release guide](device-release.md).
The enrollment-only scope statements below describe historical milestone evidence,
not the current product capability list. Use the release guide for shipping decisions.

# Desktop pairing service

This milestone implements desktop-owned enrollment and trust, not the complete
mobile product. Experimental encrypted LAN pairing is available with explicit
opt-in. Independent cryptographic review is still required for production
release. No physical phone, second physical desktop or public ntfy traffic was
used in these tests. ntfy transport, application inbox/outbox, Rem dispatch,
fleet jobs, files and activity remain separate dependent milestones.

## What runs

`gofer.devices.control.DeviceControl` owns a dedicated backend event loop and
`DeviceListener`, independent of a renderer or open window. The existing backend
process owns its lifetime. Closing a settings panel does not stop it. A headless
`ui serve` process also hosts it; quitting the backend stops it. Existing Electron
background lifetime behavior is unchanged. No operating-system service is
installed by this change.

The local UI API provides authenticated `/api/devices` administration. The
separate device TCP listener accepts only length-prefixed v2 TLS carrier frames,
then encrypted session control. It cannot call an HTTP route or run a command.
Unexpected application records are rejected until the dispatch milestone.
Desktop-to-desktop initiation is available through the same service and settings.
Discovery is neither implemented nor required for trust; a QR/pasted invitation
supplies an explicit endpoint and public-key pin.

The registry is SQLite, with `BEGIN IMMEDIATE`, WAL and `synchronous=FULL`.
Identity metadata, role, trust revision, pending/confirmed/active/revoked state,
capabilities, authentication time and reconnect endpoints persist. Routing
credentials are encrypted using `cryptography` AES-GCM with independent random
96-bit nonces and peer ID as associated data. The encryption key and P-256
identity private key are stored together in the OS credential store. No TLS
cipher counters or session secrets are persisted. These storage operations are
standard library operations, not a new wire cryptosystem.

## Setup for experimental user testing

Install the `devices` extra in the selected isolated desktop environment with
`uv sync --locked --extra dev --extra devices`. Desktop binary build wrappers
also include the devices extra. Binary packaging itself still requires release
verification; it was not built or installed on user machines in this milestone.
The trusted CLI must resolve to a build containing this revision before these
launch instructions can exercise it. Do not assume an older installed executable
has the new Settings page.

On each test desktop, use an existing unlocked OS credential store. Linux uses
Secret Service through the login D-Bus session, macOS uses Keychain and Windows
uses Credential Manager. The backend explicitly instantiates the appropriate
[keyring backend](https://keyring.readthedocs.io/en/stable/), ignoring configured
file, plaintext, chained and environment backends. A missing or locked store
fails closed. Headless Linux requires access to that user's unlocked session;
there is no password-file fallback. OS-specific real credential-store behavior
has not yet been validated across the host matrix.

Opt in when launching the selected desktop build, or the backend without a window:

```bash
export RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1
export RATICODE_DEVICE_HOST=192.168.1.20
export RATICODE_DEVICE_PORT=18766
/home/doonk/Projects/.gofer-trusted-bin/gof ui serve --host 127.0.0.1 --port 8765 --data-dir /absolute/path/to/disposable-raticode-data
```

Use each machine's actual private interface address. Wildcard, multicast and
public bind addresses are rejected. Do not expose the local UI port to the LAN.
The device port carries only authenticated device protocol traffic. This change
does not edit router or firewall rules. If the network blocks the port, report
that prerequisite rather than automatically changing network policy.

Without opt-in, only loopback endpoints are permitted by the local control API.
Omitting the device port allocates one and persists it for subsequent starts.
Keep the same data directory and OS credential account. If the saved port is
unavailable, startup fails; choose an explicit free port and issue a fresh
invitation. Existing peer endpoints need updating through a new deliberate setup,
not unauthenticated discovery. Stopping the backend and removing the experimental
opt-in returns the local control path to loopback-only mode.

Open Settings > Paired devices and choose **Set up device pairing** if needed.
Choose **Pair a device**, then **Create QR code** for a five-minute invitation. Invitation creation now requires the
experimental network opt-in; a local-only listener cannot pair with a phone.
The QR and paste text stay only in the
current renderer memory; they are never stored in localStorage. Invitation
rendering happens locally; the opted-in relay service subscribes for encrypted
pairing frames after invitation creation. QR generation uses
[Segno](https://pypi.org/project/segno/), not a web image service.

### Development startup and phone authentication timeouts

`electron:dev` uses the source backend and does not require an AppImage rebuild.
It still needs the device-network opt-in. From `frontend`, after selecting the
repository's Node version, stop the old development process and launch:

```sh
RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1 npm run electron:dev
```

This enables the encrypted relay with a loopback-only TCP listener. The invitation
omits that loopback address so the phone does not connect to itself. Add
`RATICODE_DEVICE_HOST=<desktop-private-LAN-IP>` in the same command for direct LAN
connections too. The local HTTP UI server must remain on loopback. Issue a fresh
invitation after every backend restart; an old invitation is no longer valid.

Without the opt-in, earlier builds could generate a QR while both phone routes
were unavailable. The administration API now rejects that action and settings
disables the button with startup instructions. Status distinguishes LAN and relay
configuration and settings displays sanitized relay errors. Enabled means the
service is configured, not proof that a firewall or relay will pass traffic.

For two desktops, paste A's invitation on B, review A's full fingerprint against
A's display, then request pairing. A shows the authenticated candidate fingerprint;
compare it against B's identity and confirm on A. Pairing never grants
`worker.execute` or `remote_approval`. Project/tool/file authorization remains
mandatory in future dispatch. Mobile integration must perform the same pinned
handshake and durable ready/ack sequence; the mobile implementation is a later
milestone, not validated by desktop-to-desktop tests.

## Enrollment and recovery

1. Create a fresh memory-only invitation with a random 256-bit secret and routing
   topics. The invitation expires at five minutes, is invalid after cancellation
   or restart, and permits at most five failed encrypted binding attempts.
2. Complete TLS 1.3, mutually proving certificate possession. Enrollment accepts
   an unknown client certificate as an untrusted candidate only. The client pins
   the QR's responder SPKI. The exact invitation bytes, secret, identities,
   roles and fresh session ID must match the first encrypted binding.
3. Atomically consume the invitation and persist a pending candidate. A competing
   phone/desktop cannot claim it again. Pending candidates receive no trust or
   application capability. Local confirmation checks the exact displayed pin.
4. Confirmation commits trust and encrypted random routing topics before sending
   `session.ready`. The peer commits its trust before sending `session.ready_ack`.
   The responder commits active state only after the exact revision acknowledgment.
5. Lost ready/ack is recovered using a fresh pinned TLS resume. A responder accepts
   only confirmed/active registry pins during TLS, then binds the exact device ID
   and role. A local initiator's explicitly reviewed pending QR pin can recover
   during its enrollment expiry window. Existing ready values must match exactly.
   Settings' Reconnect action uses the persisted endpoint; it never retries a job.
6. Revocation commits a tombstone, removes routing credentials, excludes the pin
   from new TLS sessions and invalidates existing sessions at their next service
   tick or received frame. Tests exercise that bound of about 200 ms. Duplicate
   or late acknowledgments cannot restore trust.

An expired unconfirmed attempt needs a new invitation. Revoked IDs/pins are
retained and cannot be silently restored by a new QR. This first implementation
does not provide an in-place re-enrollment of a revoked identity. A new identity
requires deliberate local setup and revocation of the old identity on its peers.

If the OS credential entry is missing, corrupt, mismatched or locked, startup
does not generate replacement keys. Unlock the store and restart first. If keys
were permanently lost, revoke this desktop on each peer, retain the old data for
inspection and create a fresh disposable data directory/identity. Do not copy a
SQLite registry alone to another path or machine and expect trust restoration.
The UI reset-all-settings button does not reset backend device identity/trust.

## Bounds and shared framing

TCP carries `uint32 big-endian frame length || v2 outer JSON`. The length is
1 through 3072 and is checked before requesting the body. TLS chunks remain at
most 2048 bytes, with the existing bounded order/record parsers. Split and
coalesced TCP reads are handled by `readexactly`; truncated streams, oversized
prefixes, gaps, deadlines and authentication failures close the session.

There are at most 16 concurrent connections. Global admission permits a burst of
32 and refills one per two seconds; each address permits five with one per ten
seconds. The address table is bounded at 256 entries. The first frame has a
10-second deadline, binding/ack phases have 30 seconds, and pending enrollment
cannot exceed invitation expiry. Idle enrolled connections close after 60 seconds
in this milestone; durable application sessions and heartbeats belong to sync.
The peer registry has a 128-entry cap including revoked tombstones. Quota exhaustion
fails closed; no automatic deletion of revocations occurs.

Mobile dev agreed to this TCP framing on shared board
`1cedf8ccc8114c25827538f38f2911d8`. Shared framing-only positive and negative bytes
are in `tests/fixtures/device_pairing/lan.json`. They contain public dummy data,
not a valid TLS exchange. The accepted `protocol/v2` schemas/fixtures are unchanged.
Cross-language application enrollment tests against this actual service remain a
required mobile pairing handoff. Existing cross-language library evidence is
retained under the protocol milestone and is not relabeled as service evidence.

## Verification and limits

`tests/unit/test_device_pairing.py` uses disposable in-memory secret stores and
real TLS/TCP engines. It covers one-use races, secret/hash/identity/role mismatch,
expiry/cancellation/restart, key loss/corruption, protected-backend selection,
process exit after durable confirmation, lost-ready resume, revocation, no mobile
worker grant, resource bounds and TCP fragmentation/truncation/timeout.

The isolated Chromium test uses a fake local settings API to cover focus/clear/type/
blur, QR cancellation, fingerprint consent, two-step revocation and absence of
localStorage secrets. It does not claim real OS credential-store or phone testing.
Run with nvm selected first:

```bash
source "$HOME/.nvm/nvm.sh"
nvm use 22.12.0
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run check:build
xvfb-run -a npm --prefix frontend run test:pairing-browser
```

Use the committed evidence manifest for exact revisions, test counts and hashes.
Independent crypto review, actual phone/two-host LAN testing, public relay and
OS credential-store matrix remain explicitly open. No public traffic, provider
calls, router changes, user workflow execution, publication or deployment was
performed for this milestone.

For managed continuation/integration, `bash scripts/check-swarm-desktop.sh` runs
the full Python suite, Ruff, mypy, frontend tests/lint/build and the dedicated
pairing browser regression. It selects `.nvmrc` before npm and uses Xvfb when
DISPLAY is absent. Prepare the locked `.venv` with devices/dev extras and run
`npm ci` after nvm selection first. The script neither installs dependencies nor
runs a real workflow; managed tooling captures its output and revision.

For enrollment-only acceptance on two disposable desktops, compare fingerprints
on both displays, confirm once, restart each backend, reconnect using the saved
endpoint and revoke the peer. Verify that a fresh reconnect now fails and that
both UIs describe saved trust rather than claiming current reachability. Attempt
a second claim of the same invitation and an expired invitation; neither may add
a trusted peer. A phone follows this sequence only after the mobile pairing
milestone is integrated. Chat/fleet/file acceptance must use the later integrated
system checklist, not this enrollment-only procedure.


## Enrollment review repair

Enrollment persists and returns an empty capability list for controllers and
paired desktops, including reconnect after restart. This service has no
application handlers yet. The initiator rejects every nonempty capability grant.
Before adding chat, files, status or fleet work, both peers must implement an
explicit offer/intersection contract and enforce local authority separately.
The existing session binding and frozen protocol fixtures are unchanged.

An invalid device host, wildcard/public address, or LAN address without the
experimental opt-in disables the device listener. Normal authenticated local UI
startup continues. Settings returns a generic configuration error without
echoing the supplied host; enabling cannot bypass it. Correct the environment
and restart the desktop. No device identity or listener is created in this state.

Repository verification uses `bash scripts/check-swarm-desktop.sh` for all
phases, or the managed phase names `backend-1` through `backend-4`,
`backend-reconcile`, `ruff`, `mypy`, `frontend-test`, `frontend-lint`,
`frontend-build`, and `browser`. Node is selected with nvm before backend tests,
which also exercise JavaScript release scripts. Backend phases collect all pytest
node IDs, select disjoint sorted partitions, and record revision, checkout,
source digest, run identifier, per-test outcomes and JUnit hashes under ignored
`reports-swarm/backend`. Reconciliation rejects missing, duplicate, stale or
failed results and preserves explicit platform skips. Run phase 1 first and do
not modify the checkout between phases.


## Unpairing and clearing revoked devices

**Phone > Unpair** removes the selected pairing, routes, thread grants, shared-history mirror and file transfers. It leaves no revoked-device entry. **Revoke** retains a blocked identity. **Revoked devices > Remove from revoked list** removes that block and the old pairing's access. Neither action restores trust. A fresh QR invitation and desktop confirmation are required, with thread sync enabled by default for new phone pairings. Desktop peers still need project grants. Desktop-owned conversations remain in the renderer's repository.

Accepted/running Rem requests become cancelled locally; the dispatch bridge requests that running turns stop. Already performed work is not undone. Request IDs and terminal work records remain to prevent duplicate execution and stale provider callbacks. All other pairing-scoped journals and routes are removed atomically with trust.

Every pairing now has a local generation ID, assigned on enrollment and persisted across restart. Existing records receive an ID during schema migration. LAN and relay sessions verify that ID as well as the pinned identity, so removing and pairing the same identity cannot revive an old session. The generation ID is local storage metadata; the wire format and TLS pins are unchanged.

On the phone, **Desktop > Forget pairing on this phone** clears its local pairing keys, queued messages and local live history. Use it only when deliberately starting a fresh pairing. A revoked identity cannot reconnect even when its LAN address is correct.

## Relay warning behavior

The desktop LAN listener and ntfy subscriptions run independently. A relay warning does not report a route switch. Relay errors are retained separately for each channel and operation. A successful subscription poll cannot clear a failed publish or an error on another channel. The warning clears when the affected operation succeeds or that channel closes. Retry-After continues to pause relay work without blocking the LAN listener.

Restart the development desktop backend after Python source changes. Vite can update the settings component while the old backend is still running, but the new device actions require the restarted backend.
