# Experimental device messaging and fleet work

> **October 26, 2026:** The ntfy relay was removed. Phones and desktops connect
> only over the local network. Relay, topic and quota material below is historical.
> Invitation `relay` and topic fields remain only for wire compatibility and name the
> unresolvable `https://relay.invalid`. See the mobile repository's
> `docs/mobile-desktop-v2.md` Wi-Fi-only amendment.

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
source port. Each desktop session instead ends with a pinned `fleet.request`
`advertise` record naming the sender's own listener and display name, so the
reverse direction works without typing an address (see Fleet mesh below). A
manual address in Settings remains available and is replaced by the next
announcement.
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

A request the desktop refuses is answered with an `error` event carrying its
request ID, instead of only closing the session. This covers a conflict, an expired
or unknown file, missing thread access, a full quota and a request this desktop
version doesn't support. The phone keeps a request queued until an answer with its
ID arrives and resends the queue on every connection, so a refusal that only ended
the session blocked that phone on every reconnect. Where it can, the desktop records
the refused event's ID first, so a resend gets the same answer and never runs.
Conflicts can't be recorded and are answered again on each resend. Lost trust and a
full outbox still end the session; the phone's next acknowledging sync frees the
outbox. The backend logs only the fixed refusal code, at most every ten minutes per
code for a session that ends.

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
Paired desktops refresh each other every thirty seconds over the LAN; there is no
relay heartbeat.

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

Rem's chat gives each phone with **Use Raticode from this phone** a private file
channel, a UUID derived from both device IDs. File events on it are accepted only
from that phone and only while desktop control is on. Files Rem names in a reply
are offered only to the phone that sent the request, and only from Rem's projects,
the projects shared with that phone, or the Second Brain folder. They use the same
scoped snapshot.

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

## Background parity, October 27, 2026

Phone polling requests use a bounded recent replay window instead of the lifetime inbox, so long background sync cannot exhaust the 100,000-event quota. `src/gofer/devices/remote_control.py` answers phone approval and swarm events only after the per-phone **Allow approvals and swarm controls** opt-in, which is off by default and stored in `device_remote_control`. Workflow decisions reuse `decide_workflow_approval_payload` with `decided_by="phone"`; swarm actions reuse the swarm manager's `control`, `message` and `human_response` paths. Every approval decision must match the current action digest. Thread resource changes only toggle existing resources and need an explicit `set_resources` flag. The provider catalog is read on a worker thread, never on the device event loop. Electron reloads a crashed hidden renderer up to three times an hour and restores a hidden main window if a page-initiated close destroys it, so tray mode keeps phone sync running. See the mobile repository's `docs/mobile-desktop-v2.md` background parity extension.

## Fleet mesh, October 29, 2026

Every desktop session now starts with a `sync.request` whose `after_sequence` is
the highest outbox sequence already received from that peer. Before this, a
desktop never acknowledged anything: the responder replayed its whole outbox at
one record per 200 ms tick on every session and never pruned it. A 100-event
backlog made each exchange take about twenty seconds, and about 1500 events
exceeded the 300-second exchange deadline, so fleet work stopped for good. The
session listener also resumes a phone at its cursor instead of sequence zero.
The same `sync.snapshot` tells the requesting desktop which threads the peer has
granted it (thread, title, project, provider and model). Settings and the Rem
fleet tools use those IDs for `job.submit`; nobody copies UUIDs between machines.

Fleet status probes are read-only and are not journaled as delegated work, so
polling no longer consumes the 100,000-request lifetime quota. The latest fleet
and sync snapshot per peer replace each other in `device_peer_snapshots` and no
longer crowd work answers out of `device_remote_results`. A status request waits
up to five seconds for fresh answers instead of returning the previous snapshot.

After the answer arrives, the session sends a desktop-only `fleet.request`
extension, `advertise: {name, lan_endpoint}`, and waits up to three seconds for
its reply. The receiver accepts it only from an active desktop peer, validates a
private or loopback address with the existing endpoint rules, and ignores a
loopback address unless its own listener is loopback-only. A name typed into an
invitation is kept; an announcement only fills in a missing or default name. An
older desktop rejects the extension after it has already answered, so mixed
versions keep working in one direction until both are updated.

`fleet.snapshot` now lists devices paired with a reporting desktop that this
desktop is not paired with, named `"<name> (via <desktop>)"`, within the
existing frozen device fields. A phone paired with one desktop therefore sees
every machine in the fleet. Freshness still applies at each hop; an indirect
device is reachable only while the reporting desktop's evidence is fresh. Its
local status entries have `state: "indirect"` and a `via` device ID.

Work still goes only to directly paired desktops, under that desktop's own local
grant. A desktop does not forward work to its peers: that would let one pairing
borrow another desktop's grants, which the pairing model rules out. To send work
to a machine listed "via" another desktop, pair with it directly, or ask Rem on
the desktop that is paired with it.

## Run threads on another desktop, October 29, 2026

A thread can run on any directly paired desktop. The composer's **Run on** menu,
next to the permissions menu, lists them; the choice is fixed once the thread has
a message. The receiving desktop must first turn on **Run threads from this
desktop** for that peer in Paired devices. It is off by default, is stored in
`device_thread_hosts`, and turning it off revokes every thread that peer started.

While it is on, the receiving window publishes its open projects and provider
policies as a hosting template grant (`host_template`), built by the local API
from canonical, granted paths with `shared_context`, the same as phone thread
sync. The main desktop reads that desktop's `catalog.snapshot`, picks the project
whose folder name matches the thread's project (otherwise the first open
project), and sends `thread.create` with the template ID. The receiving desktop
applies the requested provider, model, effort and permission mode through the
existing phone `configure_context` checks. The main desktop records the remote
thread in `device_remote_threads`; later turns reuse it, so the receiving
desktop's provider keeps the conversation's context.

Each turn is one pinned session. It sends `chat.submit`, polls
`thread.history.request` with presentation once a second in the same session
(separate connections would exceed the listener's per-address admission rate),
streams settled thoughts and tool steps from the receiving desktop's turn
journal, and finishes with the `chat.message` text after a terminal
`request.status`. Stop and steering send `request.cancel` in that session.
Interactive snapshots are not stored in `device_remote_results`. Phone turns in a
desktop thread with a machine follow the same path from the main desktop.

Limits: messages are capped at the protocol's 4000 characters, attachments are
not sent, Rem cannot start child threads from a remote thread, and the receiving
desktop's window does not list these threads; they run in its backend.


