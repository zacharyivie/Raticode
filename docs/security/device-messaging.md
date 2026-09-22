# Experimental device messaging and fleet work

The paired-device backend now accepts application records after a mutually
pinned TLS 1.3 session, exact binding echo and durable ready acknowledgment.
The same TLS carrier is used for local TCP and HTTPS ntfy topics. ntfy is an
untrusted carrier, not a Raticode account service. There is no paid API or hosted
Raticode backend dependency.

Independent security review, physical phone/two-machine testing, and public
internet reliability remain release gates. Agent tests do not replace those.
The setup opt-in and OS credential requirements in [device-pairing.md](device-pairing.md)
still apply. The service is tied to the desktop backend lifetime, not a renderer.

## Local authority

Pairing still grants no execution capability. In Settings > Paired devices,
share an explicit thread UUID, provider/model, local project folder and permission
mode. The desktop folder picker grants local path access before this operation.
The protected application store holds that context separately from the peer's
wire messages. Provider, permissions and filesystem paths cannot be selected by
a remote message. Replacing or removing a thread grant cancels its queued/running
requests. Peer revocation invalidates active TLS sessions and queued dispatch.

Rem dispatch uses the existing ChatJobs, ChatSteering and provider subscription
paths. Codex read-only/workspace-write and Claude plan/default modes are supported;
unattended bypass modes are rejected. Model defaults can use the user's existing
CLI subscription. No provider command is executed by tests. A local grant is
required before mobile chat or desktop work can dispatch. Remote turn fleet tools
are read-only unless the target's local grant explicitly sets `fleet_execute`.
Settings exposes an unchecked-by-default fleet delegation option and the saved
grant state. Revoke thread access to remove this authority and cancel current
requests, then grant again without fleet delegation for chat-only access.

Desktop-to-desktop initiation uses the invitation's persisted LAN endpoint. The
responder does not infer an initiator's listening address from a connection's
source port. Set that desktop's private listening host/port explicitly in Settings
before initiating in the reverse direction. Endpoint changes remain local actions.
Mobile devices cannot receive worker requests. Jobs check the receiving desktop's
ID, exact granted project UUID and policy revision. Both request IDs and job IDs
have immutable content checks and lifetime deduplication.

## Persistence and recovery

SQLite uses the existing WAL/full-sync transaction boundary. Inbox acceptance,
request identity and acknowledgment outbox commit before an acknowledgment is sent.
Outbox sequences are per peer. New acknowledgments cannot overtake older pending
responses; the TLS pump takes one bounded application record at a time. Application
IDs remain unchanged across a fresh TLS session. Repeated events with changed
content and reused sequence numbers fail closed. Repeated operation IDs cannot
start a second provider job. Dispatch claims serialize by thread.

The dispatch boundary is persisted before entering ChatJobs. Restart marks an
uncertain running request `outcome_unknown` instead of launching it again.
Outbound desktop requests retain exact event bytes and retry on a fresh pinned
LAN session. Results and application data use AES-GCM at rest with the OS-keystore
protected storage key; no wire TLS keys or counters are persisted. Existing local
provider job logs retain the desktop's established storage behavior.

Every sync request receives a correlated snapshot, including an empty backlog.
The session drains all older events in sequence before that snapshot. The snapshot
contains only explicitly shared threads. Draft/model selection never changes the
thread's identity. Provider context includes the latest twenty dispatched/completed turns in that
peer/thread. Encrypted reply transcripts are stored separately from transport
outbox payloads, so file-transfer cleanup cannot erase recent conversation context.
Per-peer transcript data is capped at 64 MiB. Authenticated sync cursors permit
outbox cleanup while retaining 400 recent events; download chunk acknowledgments
remove their delivered chunk payloads immediately. A separate persistent sequence
counter prevents number reuse after cleanup. Outbox and remote-result payloads
are bounded to 10000 events/64 MiB per peer. Remote-result cleanup retains the most
recent 2000 events; terminal request states remain. Lifetime event/operation
records cap new requests at 100000 per peer; reaching a quota fails closed.

## Transport and fleet status

Mobile tries the invitation's private LAN endpoint and proves the stored SPKI pin
before treating it as connected. An unreachable LAN endpoint may start a fresh
TLS session over the invitation/paired ntfy topics. No application plaintext,
filename, invitation secret, action, or notification preview appears in relay
headers. The responder subscribes to the raw responder topic and publishes to the
raw initiator topic. Topic strings are not prefixed. Relay endpoints require HTTPS,
reject redirects, bound poll bodies and honor Retry-After. Untrusted topic traffic
still has to pass the same TLS certificate, binding and registry checks.

Fleet queries refresh desktop peers over authenticated LAN sessions. Up status
requires a fresh authenticated snapshot and expires after sixty seconds. Missing
or stale evidence is unknown; it is not proof of downtime. Running jobs are
reported from actual device work plus the backend's active ChatJobs snapshot.
The local status API uses `running_jobs: null` when no fresh inventory exists.
There is no idle fleet heartbeat through the public relay.

## File transport revision

The earlier Tink StreamingAead profile remains an interoperability fixture, not
this live transport's file format. Live transfers use standard TLS records for
all file bytes. There is no custom encryption or plaintext fallback.

An authenticated immutable offer binds file UUID, size, SHA-256, expiry and name.
`file.chunk` contains a canonical unpadded base64url encoding of at most 16 KiB,
an exact byte offset and an EOF flag. `file.status` acknowledges received size;
download sends one chunk per acknowledgment. A repeated chunk must contain the
same bytes. Files remain unavailable until the complete declared byte count and
SHA-256 match through EOF. Each file is at most 10 MiB, retained offers reserve a
50 MiB desktop quota, and offers expire within twenty-four hours. File cancellation
and expiry remove protected contents. Imported chunks and completed contents are
encrypted at rest. Sharing a local file snapshots a regular file beneath the
thread's granted project using descriptor-relative, no-follow path traversal;
remote IDs never accept arbitrary paths or symlinks. Scoped snapshot creation
requires operating-system support for descriptor-relative no-follow opens. On
unsupported platforms it fails closed, rather than using unsafe path traversal.
Cross-platform snapshot support is therefore a remaining verification gate.

Public ntfy's free daily message allowance makes large file transfer slow or
impractical. A 16 KiB application chunk spans several TLS carrier frames. Prefer
LAN or a user-operated ntfy server for large files; neither HTTP publication nor
a first chunk is a delivered-file receipt. Background Android reliability and
free-relay throughput must be measured on real devices before release.

## Verification

Disposable loopback tests cover enrolled desktop TLS exchange, stable retry
without duplicate dispatch, schema rejection, encrypted persistence, revocation,
uncertain restart, thread scope, file truncation/digest failure and symlink denial.
Relay tests use a local HTTP fixture with an explicit test-only opt-in. Provider
tests use fakes; no paid provider call or public ntfy publication is needed.
The mobile agent additionally exercises Android Keystore TLS against a disposable
Python listener on the API 36 emulator. See the after-action report for the actual
commands and final results rather than inferring verification from this guide.

A separate disposable API 36 emulator test completed pairing, pinned resume and
chat exchange through public ntfy on 2026-09-19. This is one internet smoke test,
not evidence of sustained reliability, physical-device delivery or relay capacity.
OS credential-store matrix checks still use disposable test stores; no claim is
made that this host’s real Secret Service or other OS stores were exercised.

For current release scope, capacity recovery and exact package acceptance, see
[paired-device release and acceptance](device-release.md).


## Renderer conversation sharing

`DeviceWorkspace` bridges renderer-owned conversations through the authenticated loopback device API after an explicit per-phone sharing choice. It stores per-phone metadata and text history using the existing registry encryption. Canonical project path grants are checked on import; ungranted/global threads have no executable project context. Provider credentials and executable paths never enter the mobile catalog. Codex and Claude use the conservative remote permission allowlist. Cursor, Copilot and OpenCode use their existing default desktop permission/resource adapters. Antigravity and Grok remain catalog-only. A deterministic wire UUID maps back to the native renderer thread ID used by ChatJobs and ChatSteering.

`deviceWorkspaceSync.js` imports phone-created threads and replies into the renderer repository. Provider/model updates use expected revisions; a stale mirror cannot replace a newer phone change, and concurrent desktop edits win during reconciliation. Individual revocation leaves a tombstone. Desktop thread removal revokes the mirror. Shared text mirrors are capped to 10000 messages/8 MiB per thread and 64 MiB per phone; dispatch uses the latest 200 shared text messages. No thread, message or selected resource is keyed by provider/model.

The updated wire extension pages thread snapshots at 50 entries or 45 KB and history at 40 fragments or 45 KB. History access always checks an active thread grant. Catalogs/history remain inside pinned TLS over both LAN and relay. Mobile polls every five seconds on LAN and 30 seconds on relay; renderer reconciliation runs every five seconds while its window is open. Full Markdown/tool streams, global execution and additional remote provider permission policies are not implemented by this extension.


### Desktop display of phone turns

The authenticated loopback workspace export now joins each accepted request to its existing native ChatJobs journal. It projects provider thought events, structured tool traces, final text, failures, file-change summaries and running state into the renderer's normal conversation message shapes. Stable request/event IDs let deltas replace their earlier text and let a final reply replace the existing text-only mirror without duplicate messages. User messages retain `origin: phone`, including after desktop edits. A local journal revision changes the renderer sync token as work progresses. Reconciliation remains on its five-second interval.

The renderer can stop or steer an active phone turn using the same existing local ChatSteering endpoints. It never reposts a phone prompt to start another job. Unpairing, revocation and desktop history removal continue to control export. Diagnostic projection is limited to the local API, and diagnostics are excluded from outgoing workspace text mirrors and mobile dispatch context. TLS, receipt semantics and mobile capabilities are unchanged. Existing journals permit recovery after the renderer or backend restarts; missing historical journals cannot reconstruct traces that were never saved.
