# Device protocol v2 feasibility and desktop integration decisions

This is the retained protocol-milestone report. The subsequent
[desktop enrollment implementation and setup](device-pairing.md) has separate
verification evidence and does not satisfy independent review.

Status on 2026-09-19: protocol/library probes, not an enrolled device service.
No desktop trust registry, networking daemon, remote dispatch, QR settings UI,
or file service is enabled by this milestone. Independent cryptographic review
has not been supplied. Passing agent review and interoperability tests does not
satisfy that gate. Physical-device and public-network reliability remain untested.

## Joint choice and rejected Noise profile

Mobile owns the canonical `protocol/v2` application schemas. The shared board
records both developers' agreement to an explicit TLS 1.3 v2 profile. V1's
proposed Noise semantics remain unchanged and are not a fallback. Signal's
legacy Java NoisePSK handling does not implement the proposed XXpsk3 modifier.
Python noiseprotocol's high-level transport API supplies empty associated data;
its README lists peer review as future work. Primitive-library provenance is
not proof that a wrapper's protocol state machine has been reviewed.
Sources: [Python Noise implementation](https://github.com/plizonczyk/noiseprotocol),
[Java Noise handshake](https://github.com/signalapp/noise-java/blob/master/src/main/java/com/southernstorm/noise/protocol/HandshakeState.java).

Desktop uses `pyOpenSSL==26.4.0` with `cryptography==50.0.1`; the installed wheel
reports OpenSSL 4.0.2. Its public memory-BIO and certificate verification APIs
allow the TLS records to travel over an arbitrary bounded byte carrier.
Python stdlib MemoryBIO also exists, but its certificate callback limitations
make initial unknown-client enrollment awkward. We did not weaken verification
to force that API. Mobile uses its platform SSLEngine; actual provider/API
coverage belongs to the mobile evidence. These are maintained standard TLS
engines, not a new cipher or key agreement. The integration itself still needs
independent review. [pyOpenSSL API](https://www.pyopenssl.org/en/stable/api/ssl.html),
[Python SSL API](https://docs.python.org/3/library/ssl.html).

## Exact transport profile

- TLS 1.3 only, ALPN `raticode/2`, no SNI, no early data. Fresh context on every
  connection, session caches and tickets disabled. No serialized TLS keys or counters.
- Single self-signed P-256 identity certificate, valid at the receiving clock.
  Pin SHA-256 of DER SubjectPublicKeyInfo. Require certificate possession on
  both ends. Public CA validation is not a replacement for the registry pin.
- During pairing only, desktop may verify possession of a previously unknown
  client certificate. That is an untrusted enrollment candidate. The invitation
  secret, exact invitation hash, expiry, one-time consumption, and explicit
  desktop confirmation must succeed before trust. Resume must pin both peers.
- First encrypted binding must match profile, session UUID, pair/resume mode,
  both peer IDs and roles. Pairing adds the SHA-256 of the exact decoded
  invitation UTF-8 JSON bytes and secret. A
  completed TLS handshake alone grants no application or execution permission.
- Outer UTF-8 JSON has exactly `version:2`, `session_id`, `mode`, `direction`,
  `index`, `body`. Direction is `initiator_to_responder` or its reverse. Body is
  canonical unpadded base64url of at most 2048 TLS bytes; whole frame <=3072 bytes.
  Index is <=2^53-1 and orders carrier chunks across the entire session. It is
  not a TLS nonce or durable application sequence. Outer values are untrusted.
- Gap buffer <=32 frames/65536 bytes, fixed 30-second deadline. Conflicting
  buffered duplicates, wrong session/mode/direction, timeout, and authentication
  failure close the session. Already-consumed indexes never reach TLS again.
- Decrypted records are uint32 big-endian length plus <=65536 strict UTF-8 JSON
  bytes. Reject duplicate keys, nonfinite numbers, invalid Unicode, depth >16
  and collections larger than 1024 elements.
  TLS reads are stream chunks, independent of record boundaries. Pump `receive`
  until empty and feed each chunk to `Records`; do not concatenate an unlimited
  stream. TLS input and output have separate cumulative pump budgets.
- LAN preference requires a completed pinned handshake. Discovery is a hint,
  never trust. Migration creates a fresh session and resends identical durable
  application IDs. Concurrent carriers share one durable inbox/outbox.

## ntfy constraints, checked 2026-09-19

The official publishing documentation states these public/default limits.
No account was purchased and no application payload was sent to ntfy.sh.

| Limit | Documented value |
| --- | --- |
| ntfy.sh daily messages | 250 |
| Message body | 4096 bytes; larger bodies become attachments |
| Default request bucket | 60, replenishes one request per five seconds |
| Default simultaneous subscriptions | 30 per visitor |
| ntfy.sh attachment size/visitor total | 2 MB / 20 MB |
| Default attachment expiry | Three hours |

[Official publishing limits](https://docs.ntfy.sh/publish/#limitations).
The default message cache is 12 hours, configurable by deployment; do not treat
that as a guaranteed delivery window. The relay can see IPs, topics, timings and
sizes. [ntfy FAQ](https://docs.ntfy.sh/faq/).

Design consequences are our decisions: coalesce chat updates, no idle fleet
heartbeats through the public relay, persist outbox records locally, honor 429
and Retry-After with bounded backoff. Use HTTPS for relay connections and do
not follow untrusted redirects to leak routing tokens. Self-hosted ntfy is
configurable but does not bypass protocol authentication. Tokens stay in
protected storage. Fixed generic headers only; no names, paths, plaintext
bodies, credentials, invitation material, or click actions. Poll/stream cache
loss triggers authoritative resync, not an invented receipt. Android background
delivery and battery behavior need device evidence, not an ntfy HTTP 200.

## File format and quotas

Selected candidate: Tink StreamingAead `AES128_GCM_HKDF_1MB`, RAW output prefix,
Python 1.16.1 and mobile's pinned Java/Android version. Tink owns segment keys,
nonces, authentication and final-segment handling. No custom encrypted chunk
format. Test-only JSON keysets are public fixtures; production file keys travel
only inside the authenticated session and remain protected at rest.
[Tink Streaming AEAD](https://developers.google.com/tink/streaming-aead).

Bind immutable file ID, actual byte length and SHA-256 to the authenticated
offer and streaming AAD. Initial file cap is 10 MiB; retained mobile quota is
50 MiB. Public ntfy attachments cap individual encrypted objects below the
documented 2 MB including encryption overhead, so larger files need opaque
segments of the same Tink stream, with their manifest inside E2EE. Reassemble
bounded ciphertext in exact order and authenticate through EOF before exposing
a file. An authenticated first segment is not complete-file evidence. Target
desktop file authorization, snapshotting, quota reservations, expiry and safe
symlink handling belong to the file milestone. No plaintext public attachment.

## Durable desktop authority and recovery plan

Device state belongs to a backend service independent of an open window.
Use one transactional SQLite inbox/work/outbox store with full synchronous
commits. The durable key is paired device plus event ID, immutable content hash
and sequence. Operation/request IDs have a separate deduplication table, since
cancel and status events legitimately reference an earlier request. Retain
operation tombstones for the pairing lifetime; bound event history to seven
days/10000 events per peer initially and return authoritative snapshots after
expiry. Bound outbox to 128 events/8 MiB, history to 64 MiB and lifetime
tombstones to 100000 per peer. Deny new work when quotas prevent durable acceptance.
Separately cap pending work requests at 100 per peer and 20 per thread. Work
requests and response/status events are distinct quota domains.

Persist authorized acceptance, stable desktop turn/job mapping and response
outbox in one transaction before an acknowledgment. Recheck revocation and
current target authority when claiming queued work. Mark the dispatch boundary
before calling existing `ChatJobs`/`ChatSteering` services. Recover an uncertain
start as `outcome_unknown`; never automatically restart non-idempotent work.
Durable result and outbox commit precede terminal claims. Preserve existing
exclusive turn creation and conservative steering recovery. Sanitize errors
instead of forwarding raw provider exceptions or paths.

Reuse `chat.py` for provider flow, `chat_jobs.py` for stable turn event retrieval,
`chat_steering.py` for cancellation, `chat_media.py` for authorized attachments,
and existing swarm/approval services for policy decisions. Do not remotely
expose loopback routes. Catalog, thread creation and context update need
authorized opaque IDs and revision preconditions. Provider/model changes keep
the original thread, persona, history, project and resource selections.

Roles are persistent registry data. Mobile is a controller only and may never
advertise or receive worker assignments. Desktop worker capability requires a
separate local grant scoped to projects/resources/tools. Pairing grants only
communication. Remote execution approval stays disabled by default. Approval
intents bind device, thread, digest, expiry and current policy; never widen it.
Fleet reachability uses authenticated status with a monotonic revision and
freshness of at most 60 seconds. Missing/stale evidence is unknown, not reachable. Discovery
alone supplies neither a heartbeat nor last-seen evidence.
Use a 20-second LAN heartbeat target, with no idle relay heartbeat.

## Protected storage implementation plan

Long-lived identity and routing secrets must use OS credential storage: Linux
Secret Service, macOS Keychain, Windows Credential Manager through an explicit
allowlist of protected keyring backends. Do not accept plaintext file backends
or silently create replacement keys after loss. A locked/unavailable store
disables pairing/service startup with an actionable local message. Headless
Linux requires the user's existing unlocked Secret Service session; installing
or unlocking a desktop key service is a setup action, not an insecure fallback.
The pyOpenSSL in-memory key API avoids writing a decrypted PEM temporary file.
Android uses its Keystore. Lost keys require local recovery/re-pair and peer
revocation, never trust restoration from an unauthenticated backup.

## Scope and external gates

This milestone proves the bounded carrier, TLS library calls, exact encrypted
binding rejection for wrong secret/hash/roles and file format interoperability.
Enrollment races, invitation consumption/expiry,
revocation, registry key loss, inbox/outbox crashes, actual LAN/relay switching,
Rem dispatch, files and approvals require their subsequent implementation
milestones and tests. Do not infer those behaviors from these probes. Independent
cryptographic review, physical phone/two-desktop acceptance, OS keyring matrix,
and actual internet relay reliability remain explicit verification gaps.
