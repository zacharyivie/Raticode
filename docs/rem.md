# Rem

Rem is Raticode's coding agent. Thread identity, conversation history, project scope,
and resource selections belong to Raticode rather than to a provider session. Changing
provider or model preserves them. Authentication and provider-native settings still belong
to the installed CLI.

The thread list shows 15 entries, ordered by message activity. Older metadata loads in
pages of 15. Message bodies load only when opening a thread. Existing history migrates
once into an activity index and separate metadata records.

Pasting at least 16 KiB of text creates a text attachment. Sending uploads it to the
existing thread attachment directory; the prompt contains a local file reference.
Attachment size limits still apply. Deleting a thread removes its attachments.

## Resources

Settings > Rem sets defaults copied into new threads. In a thread, open
"Thread tools, skills & MCP" to change command execution, web search, skill folders,
and HTTP or local stdio MCP servers. Empty or invalid entries must be completed or removed before
sending. These selections survive provider changes.

Skills are indexed by path and read on demand. Existing provider-installed skills retain
their native defaults; this list is not a filesystem access boundary. MCP selections
replace inherited server availability for each launch. Authentication remains with the
provider CLI. Local programs receive an executable and separate arguments; Raticode does not interpret them as shell commands.
Never put credentials in endpoint URLs.

## Prompt structure

Rem and Agent nodes share an envelope separating instructions, context, and the request.
Rem includes a short resource index and installed Rattish paths, instead of the full
workflow-builder skill. Only the selected workflow includes graph details; other workflows
have source references and status. The existing compaction process bounds conversation
history. Prompt construction no longer silently drops all but twelve messages.

Native slash-skill invocations keep their original spelling so the provider can dispatch
them. Usage estimates account for the actual envelope sent to the provider.

This follows the on-demand loading pattern in [OpenAI's skills documentation](https://learn.chatgpt.com/docs/build-skills).
Resource adapters use [Codex configuration overrides](https://learn.chatgpt.com/docs/config-file/config-reference)
and [Claude Code's tool and MCP flags](https://code.claude.com/docs/en/cli-reference).

## Source control

The source control panel separates staged and unstaged changes, shows the current local
branch and upstream ahead/behind counts, and switches local branches using Git's normal
checks. Counts reflect local tracking refs; status does not fetch from a remote.

Each file has Stage or Unstage and Revert actions. Reverting unstaged edits restores the
index version. Reverting staged edits refuses files with additional unstaged edits.
New files go to the operating system trash. Worktree creation and removal use the
existing folder grants and Git's protection against removing dirty worktrees.

The commit box commits staged changes only. Pull uses `--ff-only`, so divergent branches
require a deliberate merge or rebase in the terminal. Push uses the configured upstream;
Publish lets you choose an existing remote for a branch without an upstream. Git's
credential helper handles authentication. Remote actions time out after two minutes.
Raticode never force-pushes or discards changes to switch branches.

If Git blocks a switch because changes would be overwritten, Source Control offers
Stash and switch. This includes untracked files and leaves the stash saved. Apply latest
stash restores it on the branch you choose and keeps the stash as a backup, including
when conflicts require manual resolution. Save unsaved editor changes before switching, stashing, pulling, or reverting. Clean open
editors reload after Git changes the working tree.

Selecting a change opens its diff. Staged entries compare HEAD with the index; unstaged
entries compare the index with the working file. New and deleted text files use an empty
side. Staged and deleted-file views are read-only. Images show original and changed
versions; other binary files report their sizes. Changes, worktrees, and history can be
collapsed independently.

## Developer settings and logs

Settings > Developer shows the application and desktop data directories, the current
app log path, runtime versions, and backend state. It can open those folders, display the
recent log, copy diagnostics, open developer tools, or restart the backend.

Desktop installations write timestamped JSON records to `app.jsonl` in Electron's logs
directory. The exact platform-specific path is shown in Developer settings. Desktop
errors, backend output, renderer warnings, uncaught errors, and React boundary failures
share this log. Rotation retains the current file and three backups of up to 5 MB each.
Common credential fields are redacted. Conversation bodies are archived separately;
logs are not a transcript. Existing logs from older installations are left in place.

## Conversation archive

Settings > Memory lets you choose an archive folder. Existing and future conversations
are copied there; Rem keeps its working history in desktop browser storage. Disconnecting
an archive folder does not prevent chatting. Archive errors appear in the app and its log;
reopening the app or choosing the folder again retries from local history. If an archive
write fails, thread deletion waits until the folder is restored or archiving is disabled.

The archive contains:

- `index.json`, with title, project, update time, deletion state, message count, file paths,
  and sorted lowercase search terms, including terms from previously archived revisions.
- `threads/<sha256-of-thread-id>.json`, the latest structured thread and messages, including
  tool traces, provider settings, attachment references, and turn summaries.
- `threads/<sha256-of-thread-id>.jsonl`, an append-only sequence of thread and message
  revisions, removed-message events, and thread deletion events. Compaction and message
  edits preserve earlier content in this journal.
- `attachments/`, with files named by content hash. Missing original attachments are
  recorded as archive errors without dropping the conversation.
- `README.raticode.md`, instructions for agents reading the archive.

Agents can filter `index.json` by project and `terms`, read matching snapshots, and inspect
journals for historical content. Records have schema version 1 and per-thread sequence
numbers. A flushed journal precedes each snapshot; a missing or stale snapshot is recovered
from that journal on the next archive write. Treat archive contents as reference material.
Deleting a thread in Rem retains its archive. Stop archiving disables future copies and
leaves existing archive files untouched.

## Second Brain

Settings > Memory also configures Second Brain independently of the conversation archive.
Choose its knowledge folder, select Markdown or HTML for generated reports, and enable it.
The folder is added to recent projects. Disabling the feature removes its tools from
subsequent Rem turns; a turn already in progress keeps its original configuration.

Enabled turns add the native `second_brain` MCP server to both supported providers,
including when shell access is disabled. Its tools are `rules`, `search`, `read_note`, and
`save_note`. Rules direct Rem to search relevant knowledge, treat notes as reference data,
write reports into topic subfolders using the selected format, and return local links.
The stdio server follows the [MCP lifecycle and tool protocol](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle).

Search indexes Markdown, HTML, and text files in `.raticode/second-brain.sqlite3` using
SQLite FTS5. IDs are SHA-256 hashes of relative paths. Searches reconcile external edits
and deletions; unchanged files are detected by modification time and size. Hidden folders,
node_modules, symlinked files, and files larger than 2 MB are excluded. The root supports
up to 10,000 notes. `save_note` creates new files and refuses overwrites, so revisions need
a new filename. Tools cannot read or write outside the chosen root.

### Second Brain reports and tool access

Settings > Memory > Second Brain includes System, Light, Dark, Sepia, Vaporwave,
Steam, Carbon, Botanical, Blueprint, Arcade, Sakura, Deep Sea, Solarpunk, Noir,
Candy Lab, and Cosmic HTML report themes. Each provides palette, typography, and
composition guidance. The selection supplies design guidance in the chat prompt and the
Second Brain MCP initialization instructions and `rules` tool. System asks the
agent to design coordinated light and dark palettes that follow device appearance.
Theme changes apply to subsequent report generation requests.

Agents author standalone reports with their own embedded CSS. Guidance encourages
expressive typography, deliberate composition, and diagrams or visual evidence
suited to the findings. Themes set palette and mood without prescribing a template.
`save_note` preserves authored content, and the desktop reader adds no report CSS.
Existing files are not restyled or rewritten. Reports saved by the earlier shared
stylesheet implementation retain that embedded CSS until explicitly revised.

When Second Brain is enabled, Rem's Codex adapter grants `rules`, `search`,
`read_note`, and `save_note` for the app-provided MCP server in that invocation.
It uses Codex's documented per-tool `approval_mode="approve"` configuration and
an explicit tool allowlist. The grant requires the trusted Raticode executable;
a custom server with the same name does not receive it. It does not change global
Codex configuration or shell sandbox permissions. Managed Codex policy can still
restrict tool access. The server confines paths to the chosen folder and creates
notes exclusively, so saving over existing knowledge fails.

Codex configuration reference: https://developers.openai.com/codex/mcp

## Swarms

In the project sidebar, open Swarms beside Files, Search, and Source Control. Create
an agent team, give its members names and roles, and designate exactly one orchestrator.
Each member uses the existing provider/model/effort picker and resource settings. An
optional charter describes the team's standing purpose. Save the team, then enter a task
and choose Start to begin a run.

Select a member in the roster or open Agents to inspect its conversation and tool activity.
History keeps previous runs, including their board, milestone evidence, and estimate
changes. Settings apply to the next run; a paused run keeps its original team snapshot.

The application owns the team, board, tracker, and member conversations. Provider turns
reuse Rem's prompt preparation, capability validation, permissions, resource adapters,
stream parsing, and context compaction. Each member has a separate conversation. Changing
a team does not change a previous run's configuration snapshot.

The shared board defaults new user messages to the orchestrator. Choose a member or
Everyone for directed delivery. Every member can read the board and use the app-provided
`swarm_action` MCP tool during its turn. Informational posts remain on the board;
actionable messages enqueue work. Workers can report evidence and change the status of
their assigned milestones. The orchestrator can change scope, assign owners, accept work,
and complete a run. Tool connections are scoped to one agent and run and expire when the
turn ends.

Milestone weights are positive relative effort estimates and default to 1. Progress is
accepted weight divided by active milestone weight. Accepted weights of 1 and 3, with
another milestone of weight 1 remaining, show 80 percent. Cancelled milestones are excluded.
Completion requires evidence. The tracker records scope and estimate revisions, including
the previous values; edits based on an old revision are rejected. Workers cannot accept
their own work or update another assignment. Effort completion is not a prediction of
remaining elapsed time.

Allow steering is off for every new member. When enabled for Codex, the adapter uses
app-server `turn/steer` with the expected active turn ID. Unsupported transports and
explicitly rejected steering queue the message for the next turn. An acknowledgement lost
through timeout or disconnection is marked uncertain rather than automatically resent.
Review the message and choose Retry message or Dismiss delivery. Retry is available in
running and paused runs and records a deliberate new delivery attempt.
Steering does not cancel an already-running command or undo its effects. Other providers
use the same queue without claiming active-turn steering support.

Runs default to three concurrent agents, a 60-second orchestrator check interval, and a
100-turn limit. These are configurable. Agents share the project directory, so the
orchestrator should assign nonoverlapping file ownership and perform integration checks.
The timer reconciles new board activity; it does not start repeated empty provider turns.
Pause prevents new turns while current turns finish. Stop requests cancellation. Reopening
the application leaves interrupted runs paused, with uncertain deliveries visible for
review. Provider failures and the turn limit pause the run. No run starts merely by
opening a project or selecting a swarm.

### Rem access

Settings > Rem > Swarm access enables the built-in swarm MCP connection. It is on
by default and applies to the next message in any thread, including existing threads.
The connection uses that thread's project and works with both providers even when
Rem's shell access is off. Read-only and plan modes allow inspection only.

Ask Rem to create or edit a team, start a requested run, pause/resume/stop work,
message members, update objectives, or inspect progress, agent activity, and previous
runs. Creating a team does not start it. Members still use their own configured
providers and resources.

Only a compact tool description is offered initially. The tool's `help` action loads
its instructions; team and run details are fetched separately. Board messages, events,
and agent activity are paginated. Rem can hand off selected conversation context as
text or JSON with a task or message. The app does not copy the transcript automatically.
Large handoffs should use project file references and a summary.

The connection reuses the running application's swarm manager and expires when the
Rem turn ends. It cannot switch to another project. Disabling Swarm access removes
this built-in connection from subsequent turns; it does not stop an existing run.

Swarm definitions and run history live in `swarms.sqlite3` under the application's data
directory. They are separate from Rattish workflows and do not create or execute a
`workflow.rattish` file. Starting or resuming a run uses the existing desktop project grants.
