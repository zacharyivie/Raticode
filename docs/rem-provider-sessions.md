# Native provider conversations in Rem

Ordinary Rem messages resume the provider's own conversation. Raticode sends the
retained transcript once when starting that conversation, then sends new requests.
The native conversation retains provider tool history. CLI processes still exit
between turns, and the next process restores the explicit saved session ID.

A continuously running process is also possible with Codex app-server, Claude's
streaming input, Antigravity streaming input, and ACP. Rem currently uses native
restoration so every turn can reconnect its current private MCP endpoints and
permission configuration through the existing subprocess lifetime.

## Provider mechanisms

| Provider | Native continuation | Reference |
| --- | --- | --- |
| Codex | `codex exec ... resume SESSION_ID PROMPT` | [Non-interactive sessions](https://developers.openai.com/codex/noninteractive) |
| Claude Code | `claude --print ... --resume SESSION_ID -p PROMPT` | [Programmatic conversations](https://code.claude.com/docs/en/headless) |
| Cursor | `cursor-agent --print ... --resume SESSION_ID -p PROMPT` | [CLI parameters](https://cursor.com/docs/cli/reference/parameters) |
| GitHub Copilot | `copilot --session-id UUID ... -p PROMPT`; Raticode creates the exact UUID on the first turn | [Session selection](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) |
| OpenCode | `opencode run --format json --session SESSION_ID PROMPT` | [CLI run flags](https://opencode.ai/docs/cli/) |
| Grok Build | ACP `session/resume` when advertised; otherwise advertised `session/load` | [ACP session setup](https://agentclientprotocol.com/protocol/v1/session-setup) |
| Google Antigravity | `agy --conversation CONVERSATION_ID --input-format stream-json ...` | [Headless conversations](https://antigravity.google/docs/cli/headless) |

No adapter chooses the most recent session or an interactive session picker.
The installed Grok CLI advertised both resume and load capabilities during a
no-model handshake on 2026-09-30. Its resume method returned a missing-path error
for a deliberately nonexistent session. Providers without an advertised ACP
restore method fail before sending the next prompt.

## Conversation lifetime

The HTTP `conversationId`, or `workflow.chatThreadId` for direct callers, identifies
the Raticode conversation. Server data under `chat-provider-sessions/` stores a
separate native ID, random configuration generation, and hashes of consumed user
messages, instructions and context for each provider in the current project.
It does not store another copy of message bodies or tool outputs. The provider
owns its native transcript. Existing single-provider references migrate without
losing their saved ID or configuration.

- A new user message resumes the saved native ID and omits consumed messages,
  prior assistant output, old attachments and unchanged Rem instructions/context.
- A model or effort change within the same provider retains the native ID. Changed
  prompt instructions and editor context are sent when needed.
- Selected resources and permissions are applied on every launch. Cursor's private
  configuration, Grok's injected plugin directory and other generated MCP aliases
  remain stable within that conversation generation. Endpoint values and tool
  grants are rebuilt for the current turn.
- The first turn with another provider starts its native session with the full
  Raticode handoff. Switching back restores that provider's saved session and
  sends the intervening user messages, visible tool traces and assistant replies,
  followed by the new request. Its own earlier messages and tool history are not
  replayed. Each provider tracks its progress independently. For example, three
  Codex turns, two Cursor turns, then Codex resumes its original ID with the two
  Cursor turns and the new question. Further Codex turns send only new requests.
  Project changes start a new session because ACP
  restore requires the same working directory and project grants must remain scoped.
- Editing and resending a message sets `resetSession: true` and starts fresh, even
  if the text is identical. All provider references for the old branch are
  invalidated. The original native session is left intact; the revised history
  seeds a new session rather than appending a correction or deleting native turns.
  The reset applies once; steering successors resume it. Workspace changes from
  the stopped turn remain in place.
- Forking creates a different Raticode conversation ID and starts an independent
  native session from the selected visible history. It never restores the parent's
  native ID or copies its hidden tool state. The parent can continue its own session.
- Duplicate requests without new user messages fail before provider execution.
  A nonblocking SQLite lease prevents overlapping native turns across backend
  threads, event loops and processes. Cleanup and process crashes release it.

Native IDs observed during a turn are saved before terminal completion, allowing
interrupted work to continue without replaying partial assistant output. Steering
still cancels and drains the current process, then resumes with new instructions.
Completed external effects remain in the workspace.

Rem still displays and saves the complete conversation through its existing
renderer repository and Electron archive, including user messages, visible tool
traces, final replies and turn summaries. Only the provider-bound prompt is
trimmed. Hidden native tool state remains private to the provider that created it;
another provider receives the visible Rem transcript, not the native session file.

An unavailable native session returns an error. Raticode never silently retries
with a full transcript. If a provider exits without a native ID, delivery is
uncertain and further continuation requires an explicit edit and resend. Corrupt
session metadata also fails until explicitly reset. Native sessions do not restart
work automatically after a backend restart; they resume when the user sends again.

## Interrupted responses

Cursor's provider-reported `Provider emitted malformed JSON content` error can
trigger up to two automatic continuations, after one and three seconds. Recovery
requires a saved native session ID and undamaged protocol records. The failed
process is closed first. Each continuation uses the same provider, model, project,
permissions, resources, and private configuration generation. Its instruction
asks the agent to inspect completed work before finishing the original request.

The backend owns recovery inside the existing turn and session lease. Reconnecting
replays the journal instead of launching another attempt. Stop and steering cancel
the backoff. Recovery status and partial answers remain in Rem chat, and exhausted
recovery offers a manual **Resume task** action. Interrupted file-change summaries
remain reviewable. Continuation instructions reduce repeated work but cannot
guarantee that a provider will never repeat an external action.

Authentication failures, malformed stdout, output limits, changed session IDs,
unavailable sessions, and missing native IDs do not trigger automatic continuation.
Other provider failures use manual continuation. Automatic recovery is limited to
the observed Cursor error. No provider/model switch or transcript reset happens
automatically.

Structured stdout is parsed before content retention limits. Cursor, OpenCode,
Antigravity, and ordinary Claude/Codex execution preserve terminal answers and
session IDs even after tool output exceeds the old 2 MB cap. Individual wire records
have a separate 16 Mi-character framing limit with an explicit error. Retained
answers have a 2,000,000-character cap and tool trace strings have smaller display
limits. Copilot uses plain text; capped output is reported as an output-limit error.
Grok ACP and Codex app-server steering already report explicit transport-limit
failures and keep their existing limits. Single-record framing overflows now also
produce a distinct limit error instead of a generic malformed-output message.

CLI terminal events retain the actual process exit code separately from the turn's
success status, local parse error, failure kind, record count, truncation flag, and
Cursor request ID when supplied. CLI version discovery is unchanged. These events
avoid copying raw failed tool payloads into diagnostics.

## Long threads and usage

Same-provider native turns delegate compaction to the provider, including model
changes. Initial sessions, forks and edit-and-resend resets also skip Raticode
summarization.

On a provider switch, Raticode summarizes imported history only when the estimated
incoming text exceeds 80% of the destination's context baseline. Exactly 80% does
not trigger a summary. The estimate includes imported history, the latest request,
and visible instructions and project context. Switching back checks only the
history that provider missed, plus the new prompt. The latest user request and its
attachments remain unchanged. Full imported text is archived under `chat-context/`,
and the summary includes the archive path.

The editable baselines and 80% setting live in
`src/gofer/core/provider_context.py`, based on the 2026-10-02 context-window report:

| Destination | Context baseline in tokens | Handoff threshold in tokens |
| --- | ---: | ---: |
| Codex | 272,000 | 217,600 |
| Claude Code | 1,000,000 | 800,000 |
| Cursor Sonnet / Gemini / unknown | 200,000 | 160,000 |
| Cursor Opus | 300,000 | 240,000 |
| Cursor GPT | 272,000 | 217,600 |
| Cursor Grok | 256,000 | 204,800 |
| OpenCode GPT | 1,050,000 | 840,000 |
| OpenCode Claude | 1,000,000 | 800,000 |
| OpenCode Gemini / Antigravity Gemini or default | 1,048,576 | 838,860 |
| Antigravity Claude | 1,000,000 | 800,000 |
| Grok / OpenCode Grok | 500,000 | 400,000 |
| Copilot / unknown OpenCode model | 200,000 fallback | 160,000 |

OpenCode models qualified with `github-copilot/` or `copilot/` use the Copilot
fallback. Copilot's default window was unverified in the report. Its optional 1M tier and
Cursor's optional maximum windows are not assumed active. These fixed baselines
do not query live provider settings or detect account-specific overrides.

Token counting uses a rough estimate of four UTF-8 bytes per token, rounded up.
It is not a model tokenizer. Images, hidden tool schemas and retained native
session state are not counted. Native compaction remains responsible for retained
state. Summary calls chunk source text at half the destination's handoff budget;
recent imported history uses at most half the budget remaining after the new
prompt. A new prompt that exceeds the budget by itself remains verbatim.

Handoff summaries belong only to the destination native session. The `compaction`
event has `scope: "provider-handoff"` and omits `messages`, so the renderer displays
a status without saving a thread-wide checkpoint. Rem keeps the full history and
all providers' original user-message hashes and native IDs. The saved `lastProvider`
is updated when a native ID is observed; older references infer it from consumed
user hashes.

Legacy callers without a conversation identity retain the existing Raticode
compaction and fresh-invocation behavior. Existing checkpoints can bootstrap a
native conversation once.

Grok exposes cumulative session usage. Resumed turns subtract the pre-prompt
snapshot, including reported cost, so the ledger records only the new turn.
Missing or decreasing baseline counters remain unknown and mark usage partial.

Native continuation cannot guarantee free historical tokens. OpenAI explicitly
[bills prior input in response chains](https://developers.openai.com/api/docs/guides/conversation-state).
[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) can
reduce repeated input costs. Subscription plans and other providers use their own
accounting. Keeping a process open also does not establish zero-cost context.
This change removes Raticode transcript replay and preserves native read evidence;
the provider can still choose to read files again.

## Validation

`tests/unit/test_chat_sessions.py` covers explicit IDs, follow-up prompt contents,
model changes, provider changes and switching back, project/resource configuration,
attachment reuse, interrupted turns, restart recovery, duplicate/concurrent requests,
failed restore, corrupt metadata and intentional resend. ACP subprocess doubles
cover resume/load negotiation, suppression of history replay and per-turn usage.
Frontend coverage checks the explicit reset request flag and an eight-turn
Codex/Cursor/Codex conversation in the UI, local storage and archive snapshot and
journal. Provider model calls and account billing were not exercised.
