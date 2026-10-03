# Rem steering API

Rem steering belongs to the Raticode conversation. It does not depend on a provider's native session ID or selected model. Existing Codex native Swarm steering remains unchanged.

Identified conversations use [native provider continuation](rem-provider-sessions.md). An explicit message edit/resend sets `resetSession: true` and invalidates all provider references for the old branch. Model changes keep the native session. Provider changes restore that provider's saved session with intervening turns, or create one with the full handoff on its first use. Terminal events include `sessionResumed` when native continuation applies.

Start `POST /api/chat/stream` with `conversationId` and a fresh `turnId`, alongside the existing provider, model, messages, workflow and permission fields. IDs contain 1-128 ASCII letters, digits, underscores or hyphens. Requests omitting both identifiers retain the legacy stream behavior. Supplying only one identifier is invalid. Stream admission failures, including reused or active turn IDs, arrive as NDJSON `error` events after the HTTP stream has opened. Reusing a completed turn ID is rejected, including after server restart.

The NDJSON stream emits `type: "turn"` with the conversation ID, turn ID, provider, model and integer `generation`. Generation starts at zero and increases for each interrupted-process continuation. Provider events carry their turn ID and generation. Render only events for the active turn/generation.

## Submit steering

`POST /api/chat/steer` accepts:

```json
{
  "conversationId": "conversation-uuid",
  "turnId": "turn-uuid",
  "requestId": "instruction-uuid",
  "text": "Keep the existing public API."
}
```

A successful response wraps the receipt in `receipt`. The receipt includes these request fields, the original provider and model, generation, `mode: "restart"`, and a status:

- `interrupting`: persisted before cancellation was requested.
- `delivered`: the successor emitted a `thought` or `final` event after receiving the steering request. This is a handoff, not proof of model compliance or successful task completion.
- `failed`: delivery was not confirmed. Text remains available for review. The provider may have performed tool actions before failing; do not automatically replay.
- `cancelled`: explicit Stop prevented an unconfirmed delivery from continuing.

The request may include `resources` with the same `shell`, `web`, `skills` and `mcpServers` fields as `workflow.remResources`. Raticode validates and persists this snapshot before interrupting. Invalid resources return 400 without cancelling the running process.

The same request ID, text, attachments and resources return the existing receipt even after completion. Reusing an ID with changed content or a different turn returns HTTP 409. A stale or stopped turn also returns 409. Invalid input returns 400; persistence failure returns 500 without accepting new text. Retain the draft on these failures. Steering needs nonblank text or attachments, with text limited to 100000 characters.

Raticode waits for the interrupted stream to close before starting another provider process. Instructions accepted during shutdown retain their order. The successor restores the saved native conversation and receives new steering instructions. Raticode preserves the visible original conversation and partial assistant output for review. Legacy calls without a conversation identity use the transcript handoff.

Provider, model, permission settings and workflow context stay with the original request. The latest accepted resource snapshot configures the successor; omitting resources preserves the previous selection. Raticode retains its trusted managed Swarm connection and rebuilds built-in thread tools for the successor. The successor uses the current provider's saved native ID. It never resumes an ID belonging to a different provider. Completed external tool effects cannot be rolled back by steering.

The stream emits `type: "interrupted"` with the old generation, partial terminal metadata and the updated `messages`. Persist that transcript in the conversation so subsequent ordinary turns retain steering. Preserve partial change metadata for review. Then a new `turn` event identifies the successor generation. Receipt transitions are emitted as `type: "steering", receipt: {...}`. A final event from the interrupted generation cannot complete the successor.

## Stop and recovery

`POST /api/chat/stop` with the conversation and turn IDs prevents further restarts and cancels the active process. Stop remains separate from steering. It returns `stopped: true`; the stream then emits `type: "stopped"` after shutdown. A stale turn returns 409. Stop and steering endpoints remain available when expensive chat request slots are occupied.

`GET /api/chat/steering?conversationId=...` returns `receipts`. Use it after reconnecting or an interrupted response. Receipts are persisted under the server data directory in `chat-steering/`. On server restart, an unconfirmed receipt becomes failed with an explicit review message. No provider work starts automatically. The same authentication and origin checks as other Rem endpoints apply.

The UI should clear only the draft accepted by a receipt, preserve later edits, show receipt status and keep provider switching separate from an active turn. A provider change starts a new turn with the existing Raticode persona, conversation and resource selections. The backend does not transfer provider-native sessions.

These APIs implement the backend transport. Provider availability, frontend interaction and final integrated acceptance are checked separately; the API does not claim that every requested provider adapter is already installed or supported.

## Failure and disconnect details

Steer and Stop success responses use HTTP 200. Errors use `{ "error": "description" }`. Receipt lookup returns 400 for an invalid conversation ID and 500 if receipt storage cannot be read or reconciled. All endpoints require the existing Rem authentication; unauthenticated requests return 401.

If persisting a new instruction fails, it is not accepted and does not trigger interruption. A disk failure during delivery ends the turn without claiming delivery. After storage recovers, receipt lookup reconciles it to `failed`, retaining its text. Stop still signals cancellation when its receipt write fails and returns 500; lookup reconciles unconfirmed text to `cancelled` after storage recovers. A server restart conservatively reports unconfirmed text as `failed`, since it cannot prove whether Stop or delivery completed before the write failed.

Disconnecting the HTTP stream cancels and closes the provider, prevents a successor, and retains accepted text in a terminal receipt. A disconnect observed by the HTTP handler uses `cancelled`; an abandoned coordinator stream uses `failed`. Neither triggers automatic replay. Cancellation also reaches the context summarization subprocess, and the interrupted generation cannot launch its answer after summarization returns.
