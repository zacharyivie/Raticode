# Shared mobile and desktop conversations

September 20, 2026. The matching Android 0.5.0 app treats a shared phone as the same user controlling desktop Rem. Desktop confirmation enables thread sync by default for new phone pairings; per-thread mobile permission caps are removed for shared conversations. Legacy individually scoped grants retain their limits.

The renderer sends validated open projects and the same thread provider permissions, resources, memory settings and swarm configuration used for local turns. Phone changes use expected revisions and the desktop provider validators. Native thread identity, resources and message history survive scope/model changes. Antigravity and Grok can select CLI-managed permissions from the phone. Global uses the existing Rem project-selection tool, then rebuilds the invocation in the selected working directory. Explicit child-thread actions inherit those settings and are deduplicated in the desktop journal.

Routine workspace refreshes no longer call grant revocation when shared metadata/resources change. Explicit revocation, unpairing or disabling sharing still cancels work. The file tool now supplies the wire thread ID instead of the native renderer ID; shared file offers can read another validated open project while retaining file size, canonical-directory identity and no-symlink checks.

The renderer exports its normal Pinned/Active/Archived classification. Mobile sync includes desktop-native and phone-originated running jobs. Search uses NFKC, case-insensitive substring matching over titles and all saved message text in the encrypted mirror, with snippets, pagination and deterministic fragment anchors. Diagnostic text is searchable but excluded from provider dispatch context. Existing mirror quotas still apply. The renderer must remain open to synchronize its IndexedDB archive.

Protocol extensions are in `src/gofer/devices/protocol/v2/event.schema.json` and the matching mobile repository: optional thread details and permission/effort catalogs, context effort/permission fields, `thread.search.request/snapshot`, and `thread.history.request.message_id`. Phone events never provide paths or credentials. TLS pinning, durable acceptance receipts, replay protection and desktop execution ownership are unchanged.

Restart the updated desktop backend and frontend. Install the matching same-key APK over the existing phone app; no re-pairing is required. Packaged desktop binaries need rebuilding. Native mobile history includes grouped thoughts and attachment labels; dedicated remote-approval and swarm-console UI remain separate work. Do not infer public release readiness or a Play Protect verdict from local tests.

## Grouped mobile history and attachment messages, September 21, 2026

Renderer exchange preserves group IDs, structured thought traces, turn timing and attachment metadata. Rich history requests use `include_presentation` to receive the same projected phone turns used by desktop, alongside renderer-originated messages. Stable fragment IDs remain compatible with search. Mobile groups thoughts under Show thoughts, combines tool updates by trace ID and renders summary timing separately. Large trace payloads are bounded and explicitly marked as shortened. Older history requests keep their original schema.

Accepted uploads retain verified name, MIME and size metadata in an encrypted `device_message_attachments` table, separate from expiring transfer bytes. This commit shares the accepted-work transaction and is capped at 32 MiB per peer. Workspace export adds attachment metadata to the originating user message. Renderer reconciliation repairs missing attachments on already imported phone messages while preserving desktop text edits. Unpairing removes attachment metadata. Legacy uploads can recover labels while their offers still exist; already expired offers may produce a generic label.

Update the mobile APK and restart this backend and frontend together. No re-pairing is required. The phone stays on its thread list after sync unless the user explicitly opens or creates a thread or follows a notification.

## Phone pairing defaults, September 21, 2026

Local confirmation of a new controller phone now inserts its workspace-sync setting atomically with confirmed trust. The setting only exposes active peers after the authenticated ready acknowledgment. This includes pending-enrollment recovery after restart. Desktop peers do not inherit phone sync. Existing paired phones retain their current choice; reconnects never re-enable a disabled setting. Unpair/re-pair creates a new default.

The Phone section removes the legacy project-access editor. Shared conversations use the validated desktop project catalog and existing provider approval policies. Fingerprint, last authenticated connection and Unpair/Revoke controls are visible directly under Phone. Desktop-to-desktop grants remain under Desktop work. Mobile 0.5.2 aligns thought markers and list bullets to text baselines, including tool disclosures and font scaling.

## Compact mobile thread home, 0.5.3

The phone shows the desktop's pinned/active rows, archive history, search, project scope and composer controls without the desktop introduction. Creation starts from a message or a small plus action. Shared creation validates project/model/effort/permission choices through `DeviceWorkspace.configure_context`, retains granted resources and assigns the native thread ID on desktop. The first accepted message supplies the same eight-word title used by the renderer, capped at the wire's 160 characters.

`thread.manage` uses current revisions for pin/unpin/archive/restore/delete. Organization overlays preserve base metadata for concurrent-edit reconciliation. A delete request stays pending and rejects new submissions until the renderer imports current history, saves its configured archive, removes the conversation and acknowledges `workspace_remove`. Archive failures report through desktop sync status and retry. Routine organization updates do not revoke work. Mobile remains a controller. Completion now sends a current thread snapshot before its final reply so immediate row actions have the correct revision.

Install Android 0.5.3 over the same-key app and restart the matching desktop backend and frontend. Pairing is unchanged. Disposable TLS tests exercise phone controls and queued creation; renderer tests cover archive failure and removal acknowledgment. Physical-phone/provider/public-relay acceptance remains separate.
