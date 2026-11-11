# Historical architecture before live-only navigation

This describes the removed production demo and earlier live behavior. See [current architecture](architecture.md). Simulator code is now test-only.

# Mobile architecture

V2 update: [the jointly selected TLS candidate](mobile-desktop-v2.md) supersedes the proposed Noise profile for new integration. The user authorized implementation and local network probes; independent security review remains outstanding. The local demo remains isolated from the opt-in live screen.
## Implemented boundaries

`MainActivity` hosts Compose and protects its window from screenshots/recent-app previews. `RaticodeApp` presents Threads, Chat, Activity, and Desktop. `DocumentImport` bounds provider work and discards cancelled or expired reads before vault access. `RemViewModel` dispatches disk work to IO and owns observable immutable `Workspace` state. `LocalVault` serializes storage operations across all instances in this process. Each ViewModel acquires an owner token and closes it on destruction; replacement invalidates older tokens. Ownership is checked after acquiring the shared lock, so queued stale work cannot write or clean up a replacement workspace. A synchronous operation already holding the lock completes before a replacement load or reset can proceed. Load plus orphan cleanup and import plus snapshot save hold the lock for their whole transaction. Owner closure never waits for IO on the main thread. This app uses one process; this lock is not an interprocess storage protocol. No composable implements transport or cryptography.

`LocalSession` is a deterministic local peer. It accepts up to 20 independent threads and reserves room for replies within 500 messages per thread. UUIDs survive retries and process restarts. Offline requests remain queued, failed simulations can retry, and cancellation stops only local unprocessed work. No state represents a real remote acknowledgment.

`LocalVault` stores versioned JSON snapshots and file blobs under `noBackupFilesDir`. Android Keystore generates a 256-bit AES key; platform AES-GCM uses a fresh random IV and authenticated purpose/file ID. AtomicFile protects snapshot replacement. Missing keys, corrupt storage, and unsupported versions fail closed without silently replacing saved data. Reset requires confirmation. The app does not provide biometric locking, hardware-backed-key guarantees, or protection from a rooted/unlocked compromised device.

Messages, draft text, attachment metadata, and imported bytes are encrypted at rest. Draft saves debounce by 350 ms; an immediate process kill inside that interval may lose the last keystrokes. Mutating actions save the current draft with their transaction. Backgrounding also requests an immediate draft save. The view model skips a draft write when the workspace still equals its last successfully persisted state; failed writes and newer drafts remain eligible for saving. An interrupted import may leave an encrypted orphan; the next successful load removes unreferenced blobs. Snapshot size is bounded at 48 MiB. The first failed import can leave an unused Keystore key but no plaintext file. Local files have a 10 MiB actual-byte limit and a 50 MiB workspace quota. Export authenticates and hashes bytes before writing via the user's document destination. Exported copies leave the vault.

`PairingInvitation`, `StrictJson`, `PayloadValidation`, and `Protocol` retain historical contract validation. Welcome-screen scan and paste route into `LiveScreen` and `LiveClient.preview`, using `DeviceInvitation` for current v2 enrollment. No visible pairing action calls the historical v1 preview. Pending scanner launch is consumed once and invitation bytes are never stored in saved UI state. Specific static errors explain old versions, expiry and clock mismatch without exposing the scanned payload. Gson's strict tokenizer rejects malformed JSON, duplicate names, trailing input, invalid UTF-8, and excessive depth. Previewing a QR never connects, executes, or trusts a device. Legacy `DesktopTransport` fails closed. Live transport is implemented separately under `live/`; the app requests INTERNET and data-sync foreground-service permissions. See [v2](mobile-desktop-v2.md) for the current TLS profile and release gates. The [v1 contract](mobile-desktop-contract.md) and [desktop handoff](desktop-handoff.md) preserve historical design context.

Short landscape windows below 480 dp use a compact workspace header and scrollable side navigation. Chat shares the header with its thread title; Threads places New conversation alongside its heading. Composer actions share the input row, leaving more height for messages and the keyboard. Portrait retains bottom navigation. Both layouts keep the local-demo warning visible.

While typing in landscape Chat, the main header collapses and the local-demo warning moves into the side navigation. This leaves room for messages above tall software keyboards.

Chat has a top-right Back to threads button and handles Android Back by selecting Threads without clearing the active conversation. Both routes request a draft save. Android dismisses an open keyboard or dialog first. During landscape typing, the arrow sits beside the message viewport so it remains accessible without expanding the header.

## Android facilities

- ZXing Embedded handles QR capture through a non-exported screenshot-protected activity. Camera access is requested only when scanning. Paste is available without camera access.
- SpeechRecognizer uses the on-device API on Android 12 or newer. It requires microphone permission and an installed offline recognizer/language model. There is no online fallback. Recognition stops when the screen is left or the app backgrounds. The user reviews text before sending.
- OpenDocument supplies scoped access for import. The app copies/encrypts bytes and does not retain a broad or persistent document grant. CreateDocument exports an explicitly chosen file. No broad storage permissions are requested.
- Local demo notifications remain distinct. Live notifications follow committed authenticated final replies and contain generic text only. A user-started foreground data-sync service maintains subscriptions; Android timeouts, process death and battery restrictions can interrupt it.
- Android backups are disabled. Keys and snapshots are excluded from device transfer. Reinstall/reset clears local trust and history; revoke the phone separately in desktop Device settings.

## Verification and limitations

Unit tests exercise the local state machine, codec, shared event fixtures, strict parser, and invitation validation. Emulator tests cover navigation, context changes, drafts, offline cancellation/retry, pairing rejection, reset, encryption, tampering, import/export, file limits, and storage recovery.

Camera decode and actual dictation need physical-device validation. The emulator tests only cover the relevant consent/validation paths. The local demo remains visibly labeled. Live TLS, relay, durable message and file integration are separate from the demo. Physical-device, public relay and multi-host acceptance still require testing. Resource and project selections are local labels until desktop supplies authorized IDs. English strings remain inline in this prototype; localization requires extraction.

The live screen uses its own durable authenticated peer boundary and platform TLS 1.3. The local simulator remains separate. Cross-language tests cover platform TLS interoperability. The phone never becomes an authority for arbitrary desktop files, merges, publication, or workflow execution.

Provider import cancellation, resource bounds, draft-save behavior, and dependency audit scope are described in [the security repair notes](mobile-security-fixes.md).


## Live device boundary

`LiveClient` serializes the protected journal and network session on one worker. `DeviceStore` shares an ownership lock across instances and stores identity keys in Android Keystore; loss of an existing key fails closed. Trust, outbox, cursor, thread snapshots, drafts and file metadata commit atomically under AES-GCM. Stable immutable events remain queued until a correlated desktop response. Transport writes never produce a delivery receipt.

`DeviceConnection` creates a fresh pinned TLS engine for every LAN attempt, fallback and reconnect, verifies the first encrypted binding echo, persists confirmed trust before ready acknowledgment and rejects changed resume trust. `NtfyCarrier` transports opaque TLS fragments only, bounds byte parsing and respects Retry-After. No body or filename appears in relay headers. Relay quotas can stall transfers.

Connection diagnostics track authentication, binding, local confirmation and ready phases. Only fixed route/stage guidance reaches the UI; raw transport exception messages remain private. Rate-limit and storage-limit failures retain their separate handling. A failed attempt publishes the protected pending-pair state so the user can retry without resetting identity or history.

`FileTransfers` writes encrypted bounded chunks before committing receive offsets. It verifies the offered size and SHA-256 of the complete authenticated stream before export. Export provider IO occurs outside vault/journal locks. Imports and files are capped at 10 MiB, with a 50 MiB plaintext transfer quota. Temporary ciphertext staging can coexist with the final encrypted copy during commit. The current native TLS chunk transport replaces the earlier proposed independent Tink blob service, using TLS's reviewed encryption instead of a custom cipher.

The foreground service requires an explicit user action, displays a generic ongoing notification and stops on Android's data-sync time limit. It cannot promise always-on delivery. A killed process recovers journaled work when the user reconnects or Android restarts the opted-in service. Notification denial does not authorize previews or a paid wake-up service.

## Release usability and operation

The shared theme mirrors desktop light/dark colors, typography hierarchy, compact corner radii and logo. Light mode separates indigo navigation, stone workspace and sage conversation regions; dark mode uses neutral charcoal. Chat uses indigo outgoing bubbles with explicit white text and neutral Rem replies. Both modes follow Android system appearance. Live and demo retain safe-area and keyboard insets, labeled touch targets and wrapping controls for narrow displays. Response notifications require a separate live opt-in, respect both application and channel permission, expose Android settings when blocked, and never include message or file content. Background sync remains a separate user action.

Route selection occurs at connect/reconnect. A healthy ntfy session stays on ntfy until reconnection; there is no claim of proactive Wi-Fi migration or always-on background delivery. Fleet snapshots expose freshness and identifiers, not comprehensive work descriptions. The release scope, signing preparation and external acceptance gates are in [release readiness](release-readiness.md).

Live history has a 2,000-message cap and an explicit completed-history cleanup action. The 128-event/8 MiB outbox keeps stable bytes until correlated authenticated responses. The 32 MiB encrypted journal retains received-event hashes and removed-file tombstones up to 100,000 each for the life of pairing; history cleanup never removes them or rewinds the cursor. Reaching a lifetime safety cap requires resolving outstanding work on desktop, revoking the old identity and deliberately pairing again. This is a bounded recovery policy, not unlimited retention.

Draft writes coalesce outside the bounded command queue. Send clears the composer only after the message commits durably, and a newer draft survives that callback. Quota failures preserve the draft and explain the next recovery action. Cleanup commands can run even when a draft save fails. File removal commits its tombstone before deleting ciphertext; startup cleanup retries only explicitly recorded removals and completed staging.

Live imports commit a bounded reservation before writing ciphertext. Metadata/outbox admission removes the reservation in the same journal commit; restart recovery reclaims only recorded uncommitted import IDs and preserves any committed file or unrelated blob. Files left unrecorded by older builds are not deleted speculatively. Document-picker requests retain their originating thread/file IDs across activity recreation, and a returning import checks that its thread remains in the saved grant snapshot.
