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

Paste, drop, or select images for Codex, Claude Code, Cursor, Copilot, OpenCode,
and Grok. Copilot models that advertise no vision input reject images in the
composer. Antigravity's headless protocol accepts text only. Unsupported images
are left out, with a warning in the attachment area that disappears after four
seconds. Other files in the same paste or drop are kept.

Consecutive thought messages share one collapsible group, including messages
from different provider turns. Settings > Rem > Auto-hide thoughts is on by
default. Thoughts stay open while Rem works and collapse when a reply arrives.
You can reopen completed groups, or turn off this setting to keep them open.

## Resources

Settings > Rem sets defaults copied into new threads. In a thread, the **Tools** button
above the composer summarizes the selection (for example "Commands · Web · 2 skills")
and opens a menu of switches: built-in tools, Second Brains, skills and MCP servers.
Invalid entries from older threads must be fixed or removed before sending. These
selections survive provider changes. Command execution, web search and subagents are
enabled by default for new threads; the browser is off. Explicitly disabled resources
stay disabled.

### Built-in tools

| Switch | What it allows | Provider mapping |
|---|---|---|
| Run commands | Shell commands, scripts and tests | Codex `features.shell_tool`, Claude `Bash`, Cursor/Copilot/OpenCode shell |
| Search the web | Web search and page reading | Codex `web_search`, Claude `WebSearch`/`WebFetch`, Cursor/Copilot/OpenCode web tools |
| Subagents | Handing parts of a task to helper agents | Codex `features.multi_agent`, Claude `Agent`, Cursor `task_tool_call`, Copilot `task`, OpenCode `task` |
| Browser | Opening, reading and testing pages in the browser chosen in Settings > Browser | An MCP server named `browser` for every provider: `gof ui browser` for Raticode tabs, or a pinned `@playwright/mcp` server started with `npx` |

File reading and editing are always available, subject to the thread's permissions.
They include image viewing (Codex `tools.view_image`), notebook edits (Claude
`NotebookEdit`) and language-server queries (Claude `LSP`, Cursor lints, OpenCode
`lsp`). Codex features the menu does not offer (`apps`, `browser_use`,
`computer_use` and `image_generation`) are turned off for every Rem turn. A selected
MCP server already named `browser` is used instead of either browser.

### Rem's browser

Settings > Browser > **Rem's browser** chooses where the Browser switch opens pages:

- **Raticode browser tabs** (default). Rem opens tabs in the editor's tab strip without
  selecting them, so the current view and keyboard focus stay where they are. Each
  thread gets its own tabs, kept across its turns, so several threads can work in
  parallel. Rem may keep up to 8 tabs per thread; tabs idle for 5 minutes stop using
  CPU in the background, and tabs idle for an hour close. Rem's tabs are not restored
  after a restart. This requires the desktop app to be open.
- **External browser window** or **External browser, hidden**. Rem uses Playwright with
  a private profile, in a visible window or headless. These need Node.js and a
  Chromium browser: Google Chrome if installed, then Microsoft Edge, then Chromium or
  Brave, then a Chromium that Playwright downloaded earlier. Without any of these,
  Rem reports Playwright's install command.

Rem reads pages through an outline with element references (`browser_snapshot`),
including the contents of same-origin frames, and acts with `browser_click`,
`browser_type`, `browser_press_key`, `browser_select_option`, `browser_hover`,
`browser_file_upload`, `browser_handle_dialog`, `browser_evaluate` and
`browser_wait_for`. It can open `http` and `https` pages only. Input is dispatched as
page events instead of native input, which would move keyboard focus into the tab; a
few sites that require native input may ignore it. Hidden tabs lay pages out at
1280×800. `browser_take_screenshot` works while the editor area is showing, including
for tabs behind the selected one; otherwise Rem uses the outline.

#### Profiles and sign-ins

Rem's tabs use a separate browser profile, so Rem is signed out everywhere by
default. **Sites where Rem uses your sign-ins** lists sites, such as `github.com`,
whose cookies are copied from your profile into Rem's and kept in sync; removing a
site removes them again. **Sites Rem may open** and **Sites Rem may not open** limit
where Rem can navigate. Subdomains are included.

You can also share one of your own tabs with Rem from the browser menu. This starts
a Rem thread that controls that tab, with your sign-ins, until you choose **Stop
sharing**. For a tab Rem opened, **Take over this tab** ends Rem's access and leaves the
page open for you.

#### Approvals and visibility

Each Rem tab shows a **Controlled by Rem** chip. It lists Rem's recent actions and can
pause Rem in that tab. DevTools' overlay outlines each element Rem acts on; pages
cannot see it. **Approve Rem's browser actions** decides when Rem must ask before it
submits a form, presses a likely-consequential button such as Send, Pay or Delete,
uploads files, or runs a page script. By default it asks on sites with shared sign-ins
and in shared tabs. **Ask on every site** asks everywhere. **Full access, never ask**
lets Rem act without approval prompts, including in tabs you share. The request
appears in the tab and as a banner when the tab is in the background. If you decline,
or do not answer within two minutes, Rem is told not to retry. **Let Rem run page
scripts** turns off `browser_evaluate` entirely.

When a thread's Browser switch is off, Rem has no browser tools. It asks you to turn
Browser on in the thread's Tools menu instead of opening pages in your system
browser. To give new threads Browser by default, turn it on under Settings > Rem >
Rem resources.

Rem cannot use your HTTP sign-in credentials, client certificates, site permissions
or certificate-error exceptions. Downloads Rem starts are blocked and reported to it
unless **Downloads started by Rem** is set to save them. Rem can upload only files
inside the thread's project and trusted folders.

#### Access control

Access to the desktop browser is granted per Rem turn. When a turn starts with the
Browser switch on, the backend asks the desktop app for a random capability bound to
that thread. It writes the capability to a file readable only by the current user,
under `<data dir>/browser-grants/`, and passes the file to the turn's `gof ui
browser` MCP server. The desktop identifies the thread from the capability, never
from the request. When the turn ends, the backend revokes the capability and deletes
the file. Capabilities expire after 12 hours at most, and none survive a desktop
restart. The backend reaches the desktop through a loopback service with an
admin token. Only the backend receives that token, and it is removed from the
environment of providers, terminals and workflow commands. Earlier versions wrote a
long-lived `browser-control.json`; it is deleted at startup.

Processes running as your user can still read another process's memory or
environment, so this is not a boundary against local malware. It ensures that
turning Browser off, or ending a turn, removes access, and that threads cannot
reach each other's tabs.

See [the integrated browser](browser.md) for the browser itself.

### Shared library

Skills and MCP servers come from one Raticode-wide library, and each thread turns
entries on or off. The menu lists:

- skill folders in Raticode's skills folder (`gof rem skill path`), `~/.agents/skills`,
  `~/.claude/skills` and `$CODEX_HOME/skills`, plus folders registered anywhere else;
- MCP servers saved in `<data dir>/rem-resources.json`.

New library entries start off in every thread. Adding a skill or server from the menu
saves it to the library and turns it on for the current selection. Agents and scripts
use the CLI, and the open menu picks changes up within a few seconds:

```bash
gof rem skill new release-notes -d "Write release notes from git history"
gof rem skill add /path/to/skill-folder
gof rem mcp add docs --url https://example.com/mcp -d "Product docs"
gof rem mcp add github -- npx -y @modelcontextprotocol/server-github
gof rem resources --format json
```

Threads store a link to a library server, not a copy. Each turn resolves the link
against the current library, so edits reach every provider on the next message, and a
removed server is dropped from the turn and marked "Removed" in the menu. The names
`fleet`, `organizations`, `rem_artifacts`, `rem_threads`, `reports`, `second_brain` and
`swarm` are reserved for built-in connections.

Global threads retain selected web search, skill references and MCP servers, so
research does not require selecting a project. Until project selection, Codex
uses Read Only while retaining the selected command setting to read files such
as skill instructions. Claude Code, Cursor, Copilot and OpenCode
retain read/web/MCP tools but disable native commands and file edits. The composer
explains these effective restrictions; the chosen project permissions take effect
after selection. Grok and Antigravity still follow CLI-managed permissions.
Selected MCP tools keep their own permissions, including Second Brain report saving.

Skills are indexed by name, SKILL.md description and path, and read on demand by every
provider. Cursor, Copilot and OpenCode also receive read access to each enabled skill
folder. Existing provider-installed skills keep their native defaults; this list is not
a filesystem access boundary. Codex disables
inherited configured MCP entries before adding the selection, and Claude Code uses
strict MCP configuration. Cursor, Copilot and OpenCode add private configuration
with tool grants; provider-native integrations can remain discoverable. Grok and
Antigravity follow their native CLI policies. Authentication remains with the
provider CLI. Local programs receive an executable and separate arguments;
Raticode does not interpret them as shell commands.
Never put credentials in endpoint URLs.

## Prompt structure

Rem and Agent nodes share an envelope separating instructions, context, and the request.
Rem includes a short resource index and installed Rattish paths, instead of the full
workflow-builder skill. Only the selected workflow includes graph details; other workflows
have source references and status. Ordinary messages resume a saved provider-native
conversation and send only new requests plus changed instructions/context. The provider
owns compaction for these conversations. Each provider keeps its own session for the
thread. Returning to it restores that session and adds the intervening providers'
turns. The UI and conversation archive retain the complete history.
See [native provider sessions](rem-provider-sessions.md)
for the continuation mechanisms, reset rules and billing limits.

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

In Settings → Providers → Commit messages, edit the message template used by the
generate button and by Rem when writing commits in chat. The setting applies across
providers and models. It saves when you leave the field; clear it or choose Reset commit
template to restore the default. Provider and model choices are saved separately.

The default uses one of `fix`, `feat`, `test`, or `chore` for the subject, followed by
a blank line and one to eight plain, one-line change bullets ordered by importance:

```text
feat: add configurable commit messages

 - Save the message template in settings
 - Apply the template to Rem commit drafts and chat
```

Custom templates can change the subject and bullet format. Keep at most eight change
bullets, combining related changes when necessary. Generated drafts are checked before
they fill the commit box; Rem receives the same format instructions for chat commits.

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

The **Second Brain** activity-rail item opens the multi-brain workspace. Open or create
local folders, search and read their documents, collect pinned evidence, generate cited
reports, and manage imports, exports and research jobs. When Settings > Memory enables
Second Brain, Thread tools has a **Second Brains** checklist of registered brains. Every
brain starts on, including brains registered later; turn one off to keep Rem out of it in
that thread, or turn them all off to remove Second Brain from the thread. Brains without
cloud permission stay off, except the Settings > Memory folder, which Rem reaches by path
while no other brain is on; the checklist marks it skipped otherwise.
Cloud permission is checked on every source brain; changing it can revoke an active turn's
access. The switches remove Second Brain tools and instructions, not file access: Rem can
still read a brain folder with shell or file tools when it is an open project. Notes saved by
Rem appear in Reports, with any declared source evidence retained as reviewable citations.
See [Second Brain workspace](second-brain-workspace.md) for storage, runtime requirements
and verification.

### Legacy single-folder configuration

Settings > Memory also configures Second Brain independently of the conversation archive.
Choose its knowledge folder and enable it. Report output is configured separately below it.
The folder is added to recent projects. This setting turns Second Brain on for Rem;
disabling it removes the tools from subsequent turns in every thread and hides the Thread
tools checklist. A turn already in progress keeps its original configuration. Without any
registered brains, Rem uses this folder directly.

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

### Report themes

Settings > Memory has separate **Second Brain** and **Report themes** sections.
Report themes work with Second Brain off, and Second Brain works with theme guidance off.
The horizontally scrolling gallery shows labeled document examples for System, Light,
Dark, Sepia, Vaporwave, Steam, Carbon, Botanical, Blueprint, Arcade, Sakura, Deep Sea,
Solarpunk, Noir, Candy Lab, and Cosmic. Existing theme selections migrate automatically.
System asks Rem to design coordinated light and dark palettes that follow device appearance.
Theme changes apply to subsequent HTML reports, slides, and PDFs wherever they are saved, without changing
an explicitly requested output format.

To share a company theme, select a saved custom theme and choose **Export theme** below
the gallery. Send the downloaded `.raticode-theme.json` file to your teammates. They can
choose **Import theme** in the same section to add and select it. The file includes the
theme name, design instructions, and HTML preview. Provider choices and other settings
stay local. Imports create a new custom theme, even when its name matches an existing
theme. Invalid or unsupported files leave saved themes unchanged. Up to 24 custom themes
can be saved; previews remain isolated with scripts and network access disabled.

The **Report output** selector offers Markdown, HTML, Slides, and PDF. Existing Markdown
and HTML preferences migrate automatically. Output works with Second Brain disabled and
with theme guidance disabled. Settings apply to the next Rem turn, including paired-device
turns; an explicit format request in chat overrides the default.

Slides are a single HTML file with embedded styling and assets. Rem writes one
`section.slide` per slide. Raticode adds a 16:9 landscape presentation layout, Previous
and Next buttons, arrow-key and Home/End navigation, a slide count, and fullscreen.
Printing includes every slide, one per landscape page.

For PDF, Rem supplies standalone HTML with `section.page` elements for deliberate page
breaks. The desktop app renders it to PDF using its bundled Chromium, with A4 portrait
defaults, print backgrounds, and CSS pagination. Headings stay with their following
content, while figures and table rows avoid page splits. Long sections can flow onto
additional pages. Rendering disables scripts, network access, and local resource loading;
images and fonts must be embedded. No separate PDF package is required.

The native `reports.save_report` tool saves files relative to the active project, even
without shell access. It accepts an optional `format` override. Second Brain's `save_note`
uses the same writer inside its knowledge folder. Both keep existing files. PDF saves
return the actual `.pdf` link and also retain a `.pdf.html` source for reading and knowledge
search. PDF rendering requires a running desktop app; failure does not create a fake PDF.

Choose **New theme** to open the composer directly below the button. Describe a design,
use dictation, or attach reference files with the paperclip, paste, or drag and drop.
Pasted text of 16 KB or more becomes a text attachment. Attach up to five files,
20 MB per file and 40 MB total, using the same limits as Rem chat. Dictation uses the
microphone selected in Settings > Devices. Press Enter or the **Generate preview**
button to request a standalone demo and reusable design instructions. Rename it and choose **Save theme** to add it to the gallery and select it.
Unaccepted previews are not saved. Custom themes persist with desktop Memory settings.
Preview documents run in sandboxed frames with scripts and remote assets disabled.

Generation uses the current Rem provider, model, and reasoning effort. Expand
**Theme generation settings** to select a dedicated provider, model, and effort.
Screenshot references currently require Codex or Claude Code, matching the supported
image adapters. Text descriptions work with all supported Rem providers.
The selected theme's design instructions are supplied separately from knowledge tools.

Agents author standalone reports with their own embedded CSS. Guidance encourages
expressive typography, deliberate composition, and diagrams or visual evidence
suited to the findings. Themes set palette and mood without prescribing a template.
`save_note` preserves authored content, and the desktop reader adds no report CSS.
Existing files are not restyled or rewritten. Reports saved by the earlier shared
stylesheet implementation retain that embedded CSS until explicitly revised.

### Second Brain tool access

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

Swarm settings include **Local Git operations**, enabled by default, and **Remote
branches and pull requests**, disabled by default. The saved configuration uses
`gitPermissions: {"local": true, "remote": false}`. Changes apply to the next run;
resuming retains the current run's permissions. Remote permission requires local
permission.

Members use `swarm_action` with `action=git` to inspect status/diffs, stage paths,
and commit in their assigned worktree. Raticode performs these operations, so a
provider sandbox does not need write access to the repository's shared Git metadata.
The tool preserves the user's index and branch and disables commit hooks. When
remote permission is enabled, `push` publishes only the assignment branch without
force or submodule publication, and `create_pr` creates a GitHub pull request through
the installed `gh` CLI. Managed PR creation requires one matching fetch/push URL;
split fork URLs and multiple push destinations are rejected rather than guessing a head.
Remote operations use existing credentials; these settings do not install tools or
authenticate accounts. Local verification and swarm integration remain separate.

These permissions are enforced by the managed Git tool and included in each member's
instructions. They are not an OS-level restriction on arbitrary shell commands or
network requests; provider sandbox and resource settings continue to govern those.

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

Runs default to three concurrent agents and a 60-second orchestrator check interval.
There is no cumulative turn or elapsed-time limit. Each agent can choose a workspace
project. Git repositories get a named review worktree before agents start, with isolated
assignment worktrees feeding their changes into it. Cleanup retains each repository's
review worktree. Non-Git projects serialize writers within each project.
The timer reconciles new board activity; it does not start repeated empty provider turns.
Pause prevents new turns while current turns finish. Stop requests cancellation. Reopening
the application leaves interrupted runs paused, with uncertain deliveries visible for
review. Provider failures retry with bounded backoff in their preserved workspace.
No run starts merely by opening a project or selecting a swarm.

### Rem access

Settings > Rem > Swarm access enables the built-in swarm MCP connection. It is on
by default and applies to the next message in any thread, including existing threads.
The connection uses that thread's project and works through the provider MCP adapter
even when Rem's shell access is off. Read-only and plan modes allow inspection only.

Ask Rem to create or edit a team, start a requested run, pause/resume/stop work,
message members, update objectives, or inspect progress, agent activity, and previous
runs. Rem can also verify and integrate attempts, resolve the human inbox, repair or
replan work, retry cleanup, and request completion through the runtime's normal checks.
Creating a team does not start it. Members still use their own configured
providers and resources.

Only a compact tool description is offered initially. The tool's `help` action loads
its instructions; team and run details are fetched separately. Board messages, events,
and agent activity are paginated. Rem can hand off selected conversation context as
text or JSON with a task or message. The app does not copy the transcript automatically.
Large handoffs should use project file references and a summary.

The on-demand instructions live in `src/gofer/ui/rem_swarms.py` as `SWARM_HELP`;
there is no separate swarm SKILL.md to install. Help includes the resource schema and
workspace paths supplied by the desktop. Set each agent's `workspacePath` to one of
these paths, or omit it to use the swarm project. Read configuration before updating
the roster and preserve agent IDs. Agent permission modes, resources, provider, model,
effort, steering, roles, and coordinator selection are configurable. Saved changes
apply to the next run.

The desktop forwards existing grants for the projects offered by the workspace selector
privately with the chat request. Rem does not need to see or copy grant tokens. Start,
resume, verify, integrate, create, and update still validate the required workspace grants;
an expired grant requires selecting the project again and starting a new chat turn.
This does not grant access to arbitrary directories.

Read `workspaces` for each repository's review destination and `diagnostics` for
integration, cleanup, recovery, coordinator digest, and usage. Read `attempts` for
attempt workspaces and evidence, including archived runs. Board participation uses
`message`: target a member or everyone, use `actionable=false` for an informational
post, and reuse `requestId` when retrying a post. Read the board to inspect delivery
receipts. Rem monitors when called; its turn-scoped connection is not a background
subscription. Unknown provider usage remains unknown.

The connection reuses the running application's swarm manager and expires when the
Rem turn ends. It cannot switch to another project. Disabling Swarm access removes
this built-in connection from subsequent turns; it does not stop an existing run.

Swarm definitions and run history live in `swarms.sqlite3` under the application's data
directory. They are separate from Rattish workflows and do not create or execute a
`workflow.rattish` file. Starting or resuming a run uses the existing desktop project grants.
