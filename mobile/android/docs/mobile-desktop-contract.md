# Raticode mobile protocol v1

V2 update: [the jointly selected TLS candidate](mobile-desktop-v2.md) supersedes the proposed Noise profile for new integration. The user authorized implementation and local network probes; independent security review remains outstanding. Existing demo behavior below remains unchanged.
Status: application contract draft implemented as schemas, public fixtures, a bounded Android parser, and local simulation. There is no live wire implementation. E2EE below is a proposed standard protocol profile, not a claim of a reviewed cryptographic system. Do not deploy by treating this document as a cryptography review.

## Ownership

Desktop owns Rem, provider credentials, thread routing, project/resource authorization, files, tools, agents, and approvals. Mobile submits user intent into a stable thread. Pairing authorizes communication with a device, not all desktop actions. Neither QR contents nor a relay notification may invoke a CLI command.

`protocol/v1/event.schema.json` defines the decrypted application envelope. Twelve fixtures show each event type. IDs are canonical lowercase UUIDs. Integers are nonnegative and at most 2^53-1 for Python/Kotlin/JavaScript interoperability. UTF-8 encoding is mandatory. Unknown versions, fields, event types, oversized data, invalid IDs, and invalid payloads are rejected. JSON must have unique member names. Validate before any dispatch.

## Pairing invitation

Desktop generates `raticode://pair?v=1&data=<unpadded-base64url-utf8-json>`. Decoded JSON follows `invitation.schema.json`:

| Field | Meaning |
| --- | --- |
| `version` | Integer 1 |
| `invitation_id` | Fresh UUID, one attempt at a time |
| `desktop_id` | Stable installation identity; not a filesystem path |
| `desktop_name` | Display label, 1–80 characters, no controls or directional formatting |
| `relay` | HTTPS origin without credentials, query, fragment, path, or non-443 port |
| `expires_at` | UTC epoch seconds, at most 300 seconds ahead of the phone's clock |
| `desktop_key` | Canonical unpadded base64url, 32-byte Noise static X25519 public key |
| `secret` | Canonical unpadded base64url, 32 random bytes, one-time PSK |

Phone caps the whole URI at 4,096 characters. A mismatched clock must produce an actionable expiry error. Parsing shows the label, relay origin, and full SHA-256 public-key fingerprint. That preview does not authenticate the desktop. The current parser drops the invitation secret and never contacts the supplied host. Copy/paste is supported; the app does not register exported pairing deep links.

Desktop shows the invitation only after an explicit user action. Keep it in memory, expire it after five minutes, and invalidate it after a completed handshake or user cancellation. Serialize competing handshakes so two phones cannot consume one invitation. Limit failed attempts. QR payloads, PSKs, full fingerprints, and raw protocol bodies must not enter analytics, logs, crash reports, or notification previews. Public checked-in fixtures contain fixed dummy material and must never be accepted as production keys.

## Proposed E2EE profile and release gate

Use the existing Noise specification, `Noise_XXpsk3_25519_ChaChaPoly_SHA256`, with the phone as initiator and the desktop as responder. Use the QR PSK as the protocol's `psk3` input. Pin the responder's static public key to `desktop_key`; never accept a different identity because a handshake otherwise succeeded. Bind the exact versioned invitation bytes to the handshake prologue. A fresh session uses fresh ephemeral keys. Every reconnect needs a fresh authenticated handshake, and cached application records must be re-encrypted for that session.

This profile is a proposal to verify against maintained Kotlin/Android and Python implementations. **No Noise implementation or custom cryptographic substitute is included.** Library maintenance, published vectors, correct PSK/prologue APIs, transport counters, crash recovery, and an independent protocol review must pass before enabling `INTERNET`. Do not silently substitute static public-key encryption, raw AES, TLS, or topic secrecy for this E2EE requirement. Android Keystore AES-GCM in this build protects local storage only.

After the first authenticated handshake, display the phone and desktop identities on both devices and require desktop confirmation. Exchange permanent device IDs, allowed capabilities, and two fresh random 256-bit routing topics inside the encrypted handshake/session setup. Persist device trust and key material atomically only after confirmation. Session establishment and resumption control records are outside the application envelope. Their proposed encoding is defined below and requires cross-language vectors before implementation. A session is not ready merely because the relay returned HTTP 200.

Subsequent handshakes authenticate pinned device identities and check the desktop revocation registry. Reinstallation or lost local keys requires pairing again. Revocation closes active sessions, rejects future handshakes, removes routing credentials, and prevents replayed queued work from executing. Never restore trust from Android backups.

## Application envelope and events

Every envelope has exactly `version`, `id`, `thread_id`, `request_id`, `sequence`, `type`, and `payload`. `id` identifies an immutable event. `request_id` correlates one user operation and all its outcomes; it can be null for unsolicited events. `thread_id` stays stable across provider/model changes. `sequence` is a per-sender sequence within one paired device stream. It persists across reconnects. Cryptographic transport counters are separate and managed by Noise.

| Type | Direction | Purpose |
| --- | --- | --- |
| `chat.submit` | Phone to desktop | Text up to 4,000 characters and zero or one authorized uploaded attachment ID |
| `chat.accepted` | Desktop to phone | Durable acceptance, with the canonical message ID |
| `chat.message` | Desktop to phone | An immutable reply chunk or final reply; unique message ID; `final` ends the response |
| `request.cancel` | Phone to desktop | Best-effort cancellation of the correlated request |
| `request.status` | Desktop to phone | accepted, running, awaiting_approval, completed, failed, cancelled, or cancel_requested |
| `thread.snapshot` | Desktop to phone | Authoritative title, project/resource IDs, provider/model, and monotonically increasing revision |
| `swarm.snapshot` | Desktop to phone | Versioned list of swarm IDs, titles, states, and summaries |
| `approval.required` | Desktop to phone | Bound action digest, approval ID, summary, expiry, and whether policy permits remote approval |
| `approval.resolve` | Phone to desktop | Approve/deny intent for that exact approval ID and action digest |
| `file.offer` | Either after authorization | File ID, safe display name, MIME, size, SHA-256, and expiry, all inside E2EE |
| `file.request` | Either | Request an offered file by ID, never a filesystem path |
| `error` | Either | Stable error code, retryability, and a bounded sanitized detail |

A chat submit with blank text and no attachment is invalid. Phone-side project/resource strings in the local demo are labels only. The live UI must use authorized IDs from desktop snapshots; never interpret a label as a grant or path. Thread creation, resource discovery, context editing, and sync control records must be added during desktop integration, with server-assigned IDs and revision preconditions. The current v1 events intentionally do not allow arbitrary context mutation.

## Delivery, replay, and recovery

Phone saves a request and attachment references before scheduling a send. A relay publish response means only "submitted to relay". Display "received by desktop" only after decrypting and durably applying `chat.accepted` for the same request. "Completed" requires a final desktop status. Do not call any of those states "delivered" for a local demo.

Desktop keeps a transactional inbox keyed by paired device ID plus request ID. Persist the request, input hash, authorization context, and a durable work item together before acknowledging. A replay with the same ID and same content returns the existing outcome. The same ID with different content is a conflict. Workers claim the stored work item; their execution identity is stable across restarts. Duplicate transport events must not create duplicate agent jobs, merges, or PRs. Exactly-once publication still depends on the underlying desktop operation's idempotency.

Persist outgoing events before publish. Retry the same application IDs after timeouts with exponential backoff and full jitter, starting at 2 seconds, capped at 5 minutes. Respect Retry-After. Stop automatic retry for revoked, unauthorized, expired, malformed, or permanently oversized inputs. A user retry must not allocate a new request ID. Cancellation after acceptance is a separate request to stop; never show cancelled until desktop confirms. The local simulator cancels only its own not-yet-processed queue.

On sequence gaps, suspend application of dependent state and ask the desktop sync layer for missing events. Deduplicate IDs, reject sequence reuse with different content, and reject stream identity changes. Replayed encrypted frames must be rejected by the chosen protocol library/session layer. Retain desktop request tombstones for the life of the pairing; event retention can be bounded with explicit snapshot/resync responses. Resume sends last committed receive sequence per direction; cursor persistence and event application must be one transaction. Mobile must never execute a command to recover a gap.

## Relay profile

ntfy remains a candidate untrusted store-and-forward carrier. Use two directions and HTTPS. Publish only opaque encrypted frame data with a generic fixed title if required. No plaintext filenames, bodies, project names, tool commands, desktop paths, preview text, or blob URLs in headers, tags, actions, topics, or click targets. An ntfy subscription URL must never itself become an Android intent.

Do not assume a free-tier payload limit or delivery SLA. Verify the chosen deployment's current terms, retention, rate limits, binary encoding overhead, authentication, background behavior, and attachment retention before implementing its adapter. Cap decrypted application envelopes at 64 KiB. Fragmentation, reconnect handshakes, routing, and frame counters require the reviewed session profile; do not split plaintext to satisfy relay limits. Desktop operation continues independently of relay availability. Lost relay history triggers explicit resync.

The relay can still observe IP addresses, timing, approximate sizes, and routing correlations. E2EE cannot hide that metadata. Select a hosting policy after measuring Android background reliability and battery use. FCM, if later used, carries only a wake-up hint; no content or secrets.

## Files and notifications

The mobile limit is 10 MiB per file and 50 MiB of retained local attachments. Obtain bytes using scoped document access. Validate actual byte counts while streaming, distrust provider-reported sizes and MIME, sanitize display names, hash the bytes, and encrypt before any upload. Outgoing file IDs refer only to completed verified uploads. Incoming offers do not trigger downloads or filesystem access automatically.

File transport is not implemented. It must use a maintained authenticated streaming encryption format and encrypted metadata. Bind file ID, byte count, and digest to the authenticated offer. Use opaque object identifiers and short retention; keep keys and retrieval credentials inside E2EE. Do not use ntfy's public attachment feature for plaintext. Authenticate the complete file before presenting it as available. Export through Android's document creator, explicitly warning that the exported copy leaves the local vault. Never auto-open received executables or resolve remote paths on the phone.

Local notifications default to generic text with hidden lock-screen content. The sample notification uses no remote delivery. Real notifications follow verified, durably applied desktop events, route to the matching thread, deduplicate event IDs, respect Android permission denial, and never bypass app identity/approval checks.

## Validation

Run `python3 scripts/check-contract.py` after installing `protocol/requirements.txt` into a virtual environment. Run Gradle unit tests for Android invitation/envelope parsing, queue state, retries, cancellation, and persistence. JSON Schema covers event shapes; application code must additionally check authorization, byte budgets, expiry, canonical base64, clocks, revisions, digest bindings, and duplicates. Neither schema validation nor local tests establish interoperability or cryptographic review.

References for the next implementer: https://noiseprotocol.org/noise.html, https://docs.ntfy.sh/, https://developer.android.com/privacy-and-security/keystore, and https://developer.android.com/reference/android/speech/SpeechRecognizer. Current ntfy plan limits were not measured in this pass.

## Proposed session and frame encoding

This section fixes the intended wire design for the desktop implementation. It is subject to the same library/interoperability/review gate as the Noise profile. Android currently parses only invitations and decrypted application events; it does not consume these frames or session records.

The relay body is UTF-8 JSON following `frame.schema.json`. Fields are `version: 1`, random UUID `session_id`, `mode: pair|resume`, `direction: phone_to_desktop|desktop_to_phone`, `kind: handshake|transport`, nonnegative `index`, and canonical unpadded-base64url `body`. Outer fields carry no device label or stable device identity. Cap an entire frame at 3,072 UTF-8 bytes. Relay deployments that cannot accept this bound are unsupported by this profile. No plaintext escape hatch is permitted.

Initial pairing uses Noise XXpsk3 as above. Handshake body is exactly one raw Noise handshake message. Handshake indexes are globally 0, 1, 2: phone index 0, desktop index 1, phone index 2. Reject other direction/index combinations and second competing handshakes for an invitation. The bootstrap topics are `raticode-pair-<invitation_id>-p2d` and `raticode-pair-<invitation_id>-d2p`. Knowledge of a topic is not an authentication credential. Expire and stop listening to both after invitation completion/cancellation/expiry.

The prologue is UTF-8 `raticode-mobile-v1\n<mode>\n<session_id>\n<binding>`. For pairing, binding is the exact QR URI bytes, including the secret. Never log it. For resume, binding is `<desktop_id>\n<phone_device_id>` from the pinned registry. Resume uses `Noise_XX_25519_ChaChaPoly_SHA256` without a PSK and must authenticate both previously pinned static keys. Reject unpaired/revoked identities. Pair and resume modes cannot fall back to one another. Ephemeral keys are fresh for every handshake.

After Noise Split, each direction has its own library-managed transport CipherState. Start transport indexes at 0 in each direction. Use the Noise library's authenticated-data operation with UTF-8 `raticode-mobile-v1|<session_id>|<direction>|<index>` as associated data. `mode` is fixed by the authenticated handshake. Sender increments only after producing a frame and caches that exact frame for same-session transport retry. Never encrypt different bytes at the same library nonce. A new session may re-encrypt the same stable application event with fresh keys.

Receiver processes only its expected transport index, with a bounded reorder buffer of 64 frames/192 KiB and a 30-second gap timeout. Drop already-processed indexes. Reject changed data for a buffered index. On gap timeout or authentication failure, discard the session, rate-limit, and require a fresh handshake. Never skip a Noise nonce, restore volatile cipher state after process death, or display a relay-provided error as a trusted desktop message. Noise keys and counters are not persisted in app snapshots.

Each decrypted transport plaintext is a strict session record from `session.schema.json`:

| Record | Fields and behavior |
| --- | --- |
| `session.identify` | `device_id`, `device_name`, `capabilities`; each side sends its authenticated identity and supported capabilities |
| `session.ready` | Desktop sends `phone_device_id`, the allowed capability intersection, and random canonical 32-byte `phone_to_desktop_topic` / `desktop_to_phone_topic` tokens after desktop confirmation. Prefix relay topics with `raticode-`. These tokens are routing secrets, not cryptographic keys. Persist in protected storage. |
| `session.ready_ack` | Phone acknowledges `phone_device_id` after saving pinned identity and routing state. Desktop marks enrollment active. If interrupted, pending enrollment can only resume with the same pinned identity. |
| `session.resume` | Each side supplies `last_received_sequence`, or null before receiving an application event. Desktop replays durable outgoing application events after that cursor. |
| `session.resync_required` | `reason` equals `history_expired` or `cursor_invalid`; suspend queued dependent operations until an authoritative snapshot arrives. |
| `event.chunk` | `event_id`, `part`, `total`, `sha256`, and `data`. The fields and data are all encrypted. |

Allowed capabilities are `chat`, `status`, `files`, and `remote_approval`. The last is never granted without explicit desktop policy. Both identity records must match the authenticated handshake and registry. Pairing is not usable for application events until the ready acknowledgment. A resume session reuses the existing topic pair, sends identity/resume records, and has no fresh approval grant. Only a fresh confirmed pairing may alter capabilities or routing topics in this v1 profile.

Serialize application events as UTF-8 JSON and split the bytes into at most 64 chunks of at most 1,024 bytes each. A chunk's `event_id` equals the decoded event's ID, `part` starts at 0, and `total` is 1..64. Include the SHA-256 of the complete serialized event in every chunk; this digest is authenticated inside Noise, not exposed to the relay. Do not decode partial UTF-8. Reassemble only after authenticated chunks agree on event ID, total, and digest; verify the digest and full byte limit before strict application parsing. Reject duplicate parts with different content. Bound reassembly to 8 events/512 KiB, expire incomplete events after 60 seconds, and recover through replay/resync. This is application framing; all encryption and nonce management remain in the reviewed Noise library.

No binary file contents ride in event chunks. The encrypted blob format and storage service remain separate integration choices; `file.offer` and `file.request` define the application boundary. Keep file capability absent until both peers implement and negotiate the reviewed transfer format. Keep remote_approval absent until desktop policy and digest/expiry checks exist.
