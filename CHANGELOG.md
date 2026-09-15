# Changelog

This file records the major user-facing changes in Raticode. Releases through
version 0.1.3 used the Gofer Flow name.

## 0.2.6 - 2026-09-11

### Branding

- Rename the desktop product to Raticode across the UI, CLI messages,
  documentation, Rattish contracts, and release artifacts.
- Write new workflows and bundles with the `.raticode` names while retaining
  reads of previous workflows, bundles, and saved preferences. See the
  [naming and compatibility guide](docs/branding.md).

### Added

- Added persistent desktop records for trusted project folders and a one-time
  migration of existing recent projects, including temporarily offline folders.
- Added release gates for Python lint, type checks, tests, and dependency audits,
  plus native terminal and Electron security smoke tests across platforms.
- Added verified release candidates on main, exact-file publication on version
  tags, artifact verification and provenance attestations. Releases default to
  unsigned; Windows signing and macOS signing/notarization are optional.
- Record signing status in candidate manifests and release notes. Unsigned macOS
  builds use manual update downloads.

### Changed

- Updated Electron to 41.10.7 and refreshed frontend, Python, and packaging
  dependencies to address dependency audit findings.
- Pinned GitHub Actions to commit hashes, added Dependabot updates, and made
  release validation and packaging use the same commit and locked dependencies.
- Reduced Rem change-tracking memory by storing file snapshots on disk and
  reusing unchanged contents. Bounded scans and throttled previews limit work
  on large projects while retaining compatibility with older undo records.
- Moved conversation archive writes to a worker, combined pending updates, and
  skipped unchanged archive writes. Cached message serialization and batched
  streamed updates reduce repeated work when saving long conversations.
- Added a filesystem-watched Second Brain search index with polling fallback,
  incremental updates, and immediate invalidation after saving a note.
- Reduced repeated Git commands, project validation, workflow discovery, and
  Rattish asset loading through caching and shared pending requests. Background
  polling pauses while the window is hidden and resumes on focus.

### Fixed

- Hardened backend authentication and origin checks across API methods and
  streaming responses, and kept backend credentials out of request URLs.
- Restricted folder grant renewal to previously authorized paths and required
  authorization for directory listings. Hardened file writes, workflow bundle
  extraction, chat undo, and Second Brain access against path traversal,
  symbolic links, and filesystem replacement races.
- Added a studio content security policy and navigation restrictions, denied
  protected permissions for remote pages, and kept microphone access scoped
  to the studio and local HTML previews isolated.
- Limited workflow HTTP responses to 16 MiB and enforced request deadlines and
  cancellation. Bounded backend connections, expensive requests, request-body
  sizes, and asynchronous job lifetimes.
- Verified speech-model archive and file hashes, rejected unsafe or oversized
  extraction layouts, and checked cached models for tampering.
- Fixed stale results during rapid project and worktree switching, repeated
  file-explorer refreshes, and late editor callbacks restoring closed-tab state.
- Fixed local Markdown link resolution and link target tooltips, and removed
  the dark backdrop from Rem's avatar sprites.
- Fixed release dry-run failures involving unsigned macOS certificate settings,
  Windows ZIP path normalization, and schema tests without an active project.
- Fixed Linux Electron platform smoke tests aborting on the unconfigured SUID
  sandbox helper by passing `--no-sandbox` when launching the test processes.

## 0.2.5 - 2026-09-10

### Fixed

- Fixed cached desktop folder grants skipping renewal after the backend's
  15-minute expiry, which could prevent new and existing Rem threads from
  sending messages when Second Brain was enabled.
- Fixed folder access renewal failures being silently ignored. Unavailable
  backends, timeouts, network errors, rejected registrations, and invalid
  acknowledgments now stop the pending action with a specific retry message.
  Failed renewal clears the stale cached grant so a later retry can recover.
- Replaced the generic bundle-path error for denied Second Brain access with
  guidance to retry the message, reselect the folder in Settings > Rem, or
  restart Raticode.
- Added folder registration success and failure logs with the folder path,
  HTTP status when available, elapsed time, and failure reason, plus a dedicated
  Second Brain access-denial log. Credentials and grant IDs stay out of these
  log entries.
- Added regression coverage for grant expiry and renewal, registration failures,
  stale-cache removal, credential-safe logging, and blocking chat until folder
  access renewal succeeds.

## 0.2.4 - 2026-09-10

### Added

- Added Rem resource settings for command execution, web search, skill folders,
  and HTTP or local stdio MCP servers, with defaults for new threads and
  per-thread selections that survive provider changes.
- Added a permissions selector beneath Rem's composer, with Codex sandbox modes
  and Claude Code permission modes, plus matching provider-profile options.
- Added conversation archiving to a chosen folder, including searchable thread
  metadata, message revision journals, tool activity, and attachments. Archived
  history remains available after a thread is deleted in Rem.
- Added Second Brain knowledge folders with local search, note reading, and
  report-saving tools for Rem. Reports support Markdown or standalone HTML,
  with selectable HTML design themes.
- Added project-wide search and replace with case, whole-word, and regex
  matching, include and exclude filters, file-level replacement, and navigation
  to matching lines.
- Added editor actions to ask Rem about selected text, request an explanation,
  and resolve Git conflicts with the relevant project and file context.
- Added source-control actions to stage, unstage, revert, and commit changes;
  switch branches; pull with fast-forward checks; push; and publish branches.
  The panel shows staged and unstaged diffs and upstream ahead/behind counts.
- Added stash-and-switch, stash previews and conflict checks, saved-stash
  application, and discard actions for individual stashes or all stashes.
- Added worktree merge and rebase previews, including squash, fast-forward-only,
  and explicit merge-commit strategies, plus source-worktree removal controls.
- Added inline conflict resolution with accept-current, accept-incoming, and
  accept-both actions, alongside merge and rebase continue and abort controls.
- Added commit-history actions for soft and hard resets, detached checkout,
  and creating branches or worktrees at a selected commit.
- Added Conventional Commits message generation from staged changes using
  Rem's selected provider, model, and effort.
- Added Developer settings with runtime diagnostics, data and log folder
  access, recent logs, developer tools, and backend restart. Desktop logs now
  collect backend and renderer errors with rotation and credential redaction.
- Added an animated Rem avatar with visibility and animation settings and
  reduced-motion support.

### Changed

- Established Rem as the coding agent across providers, preserving thread
  identity, conversation history, project context, and resource selections
  when the provider or model changes.
- Separated instructions, context, and requests in Rem and workflow Agent node
  prompts. Skills and tool schemas load on demand, and workflow context now
  includes full graph details only for the selected workflow.
- Paginated Rem's thread list and deferred message loading until a thread opens.
  Large pasted text becomes a file attachment instead of filling the composer.
- Refreshed the application logo and desktop icons, with indigo navigation,
  a warm stone editor, muted sage Rem panels, and a parchment terminal theme.
- Simplified project search controls, tightened Rem's welcome layout, and
  improved thread scrolling and worktree menu keyboard focus.

### Fixed

- Fixed conversation prompt construction silently dropping all but the last
  twelve messages, and made usage estimates reflect the prompt sent.
- Fixed desktop discovery and launch of Codex and Claude Code installed through
  nvm by making the provider's matching Node executable available on PATH.
- Fixed generated commit messages failing on large staged diffs by including
  every changed file with bounded, labeled patch excerpts.
- Fixed merge conflicts opening in the wrong worktree and made unresolved
  index entries and working-file resolutions visible until staged. Branch
  switching now excludes branches checked out in other worktrees and shows
  the branch name in repositories without an initial commit.
- Fixed integrated-browser focus and navigation before the guest is ready,
  stale attachment errors, and browser shortcuts intercepting terminal input.
- Fixed Rattish syntax highlighting for indented declarations and fields.
- Fixed workflow studio browser-test timeouts caused by counting unrelated
  settings tabs, updated stale editor and menu checks and desktop mocks, and
  improved headless execution and failure diagnostics.
- Fixed release lint errors and included macOS ZIP artifacts alongside DMGs
  so desktop updates have the required archive.
- Fixed same-version release-script runs skipping Arch checksum updates.

## 0.2.3 - 2026-09-05

### Added

- Added desktop menus for file, editor, selection, view, terminal, and help
  actions, including configured shortcuts and recent-project access.
- Added terminal Git editor handoff so commit, merge, and rebase messages open
  in the Code workspace and save before the editor request completes.
- Added page titles and favicons to integrated-browser tabs.

### Changed

- Reworked the integrated browser around isolated webview guests so pages stay
  live while tabs move between editor panes, and browser shortcuts and zoom
  follow the application settings.
- Improved split-pane tab dragging and kept overflowing tabs readable with a
  compact scrollbar that appears on interaction.
- Refreshed the Raticode browser home page.

### Fixed

- Refreshed open-editor Git baselines after external branch changes and
  rediscovered workflows whenever a recent project is reopened.
- Restored Claude Code streaming compatibility by enabling verbose output for
  its stream-json mode.

## 0.2.2 - 2026-09-03

### Added

- Added portable `.raticode` bundles for Rattish workflows, with preview,
  import, and export actions in the graph and empty-workspace screens.
- Added bundle validation for ignored files, unsafe archive paths, symbolic
  links, duplicate entries, compression ratios, file counts, and size limits.
- Added app-wide text zoom from 80% to 150%, recent-file cards in the empty IDE,
  tab cycling shortcuts, and save-or-discard prompts for unsaved files when
  autosave is disabled.
- Added a Raticode browser home page, configurable single-word search,
  modified-click tabs, Backspace history navigation, and Markdown file-link
  opening from local browser previews.
- Added a daily TODO implementation workflow that creates tickets, implements
  and reviews them in sequence, commits approved work, and gates the merge to
  `main` on user approval.
- Added a reusable cross-platform release build workflow and a dry run on
  updates to `main`.

### Changed

- Redesigned the empty Graph and Code views, refreshed Raticode branding and
  application icons, and expanded the studio design tokens.
- Discover project workflows before opening or refreshing a project so newly
  created Rattish workflows appear without restarting the studio.
- Keep assistant conversations pinned only when the reader is already at the
  bottom, grow the composer with its draft, and allow the latest user message
  to be edited and resent from that point in the conversation.
- Run tagged releases through the shared build workflow while keeping release
  publication limited to version tags.

### Fixed

- Fixed workflow deletion so source tabs, previews, and recent-file entries are
  cleared while the terminal and Code workspace remain available after the last
  workflow is removed.
- Fixed the file explorer so it reveals the active file through nested folders,
  scrolls it into view, and omits paths deleted from the working tree.
- Fixed integrated-browser focus restoration, overlapping menu and dialog
  detection, stale session events, owner cleanup, failed navigation recovery,
  and shortcut handling when an embedded page is unavailable.
- Fixed deleted-path inspection and update checks so expected errors return
  usable state instead of rejecting desktop requests.

## 0.2.1 - 2026-09-01

### Added

- Added IDE start actions for opening a project, file, or browser without an
  active workflow.
- Added diff controls to rendered HTML, Markdown, and SVG previews, including
  whitespace-only change detection.
- Added a source-control history refresh action with background loading and
  request coalescing.

### Changed

- Remember the last active Git worktree for each recent project and keep the
  main project root as the single recent-project entry.
- Keep editor, browser, and assistant sessions alive while switching projects,
  worktrees, and panes.
- Changed the default integrated-browser shortcut to `Ctrl+J`.
- Limited local Vosk speech transcription to supported non-macOS platforms.

### Fixed

- Fixed recent-project reopening, Windows path grants, missing worktree cleanup,
  active-worktree identification, and stale Git worktree registrations.
- Fixed workflow deletion so it waits for pending saves, removes registered
  workspaces, and handles read-only files on Windows.
- Fixed file opening, tab persistence, editor focus during autosave, sticky
  project-root navigation, and Monaco word deletion.
- Fixed terminal `Ctrl+Shift+V` duplicate pastes and `Ctrl+Backspace` word
  deletion.
- Fixed commit hash copying, source-control refresh behavior, and stale header
  counters.
- Fixed macOS packaging when Vosk is unavailable and updated the Linux browser
  regression test for the current project-actions menu.

## 0.2.0 - 2026-09-01

### Added

- Introduced Rattish as the workflow authoring language, with a formal grammar,
  lexer, parser, compiler, formatter, semantic validation, diagnostics, and a
  versioned JSON intermediate representation.
- Added machine-readable contracts for providers and every supported Rattish
  node, plus conformance fixtures and schemas for ASTs, compiled workflows,
  run records, diagnostics, metadata, and workspace registries.
- Added Rattish runtime support for local bindings, interpolation, structured
  outputs, explicit routing, cycles, joins, retries, timeouts, cancellation,
  public workflow interfaces, and nested workflow execution.
- Added project-based workflow storage under `.raticode`, portable workflow
  bundles, metadata files, ignore rules, project labels, and workflow discovery.
- Added a Monaco code workspace with Rattish diagnostics, graph and code view
  switching, file tabs, project file management, and native file explorer
  actions.
- Brought graph editing, node inspection, workflow settings, approvals, run
  controls, and timeline inspection to Rattish workflows.
- Added an integrated terminal, browser, and problems panel, with project-scoped
  terminal groups, browser previews, and configurable keyboard shortcuts.
- Added Git status decorations, deleted-file visibility, commit history, diff
  previews, file preview tabs, split editors, and rendered previews for Markdown,
  HTML, SVG, images, and PDFs.
- Added persistent application settings for appearance, editor behavior, terminal
  behavior, audio devices, data storage, motion, and command keybindings.
- Added assistant file and image attachments, pasted screenshots, local voice
  transcription, GitHub-flavored Markdown, interactive file links, and code-copy
  controls.
- Added reviewable assistant change summaries with live file diffs, shell and edit
  traces, elapsed time, and guarded undo and redo actions.
- Added crash recovery screens with reload, reset, diagnostic copy, and issue
  reporting actions.
- Added `gof rattish docs` and bundled the Rattish authoring documentation, schemas,
  contracts, and workflow-builder skill in packaged installs.

### Changed

- Renamed the user-facing application from Gofer Flow to Raticode.
- Made Rattish source files the editable workflow definition while compiled IR
  and run artifacts remain internal implementation details.
- Moved workflow organization from a global workspace model to project folders.
- Discover and register existing Rattish workflows when a project opens, including
  projects that do not yet contain a workflow.
- Store compiled artifacts, run logs, and agent memory inside each registered
  workflow directory, with migration from the previous application-data layout.
- Scope assistant threads and file access to their selected project, while keeping
  Code project selection independent from Graph workflow selection.
- Changed the project license from Apache-2.0 to AGPL-3.0-only and updated release
  metadata and repository links.

### Fixed

- Hardened Rattish parsing, lowering, contract validation, activation lineage,
  cyclic execution, output resolution, and interpolation behavior.
- Fixed workflow switching, editor saving, graph refresh, node inspector focus,
  type changes, approval rendering, and runtime error reporting in the studio.
- Prevented cancelled frontend requests from producing noisy backend broken-pipe
  tracebacks.
- Fixed dirty Rattish edits, stale live-analysis responses, duplicate file tab
  labels, Markdown file navigation, and project-aware editor tab persistence.
- Fixed terminal lifecycle, grouping, clipboard shortcuts, and late session cleanup.

## 0.1.3 - 2026-06-30

### Added

- Added typed workflow parameters, webhook triggers, provider profiles, direct
  API providers, revision history, workflow bundles, and queued runners.
- Added workflow validation with diagnostics and suggested fixes across the CLI
  and desktop app.
- Added resume, rerun, checkpoint, cached-output, and run-history controls.
- Added workflow call nodes with nested execution, validation, planner details,
  and child-run status reporting.
- Expanded the graph editor with undo and redo, canvas groups, node and edge
  editing, grouped input selection, and workflow navigation.
- Added browser-level workflow studio tests and dedicated frontend checks.

### Fixed

- Reduced persisted run data by moving or compacting large prompt, thought,
  input, snapshot, and checkpoint payloads.
- Isolated agent memory between loop items and preserved final outputs when log
  and thought content was truncated.
- Fixed Linux and Windows release builds, frontend test configuration, recursive
  tests, and package version synchronization.
- Improved path-grant checks, toolbar behavior, group opacity persistence, and
  workflow target editing.

## 0.1.2 - 2026-06-25

### Added

- Expanded execution with loops, fan-out controls, concurrent branches,
  start/pass/fail behavior, break handling, run limits, retries, and structured
  node outputs.
- Added file and folder operations, HTTP requests, notifications, approval
  gates, local search and vectorization, prompt files, and common LLM tasks.
- Added agent memory, context compaction, thought streaming, workflow assistant
  threads, and persistent assistant context.
- Expanded CLI workflow editing, planning, health checks, triggers, watches,
  approvals, branch configuration, and provider diagnostics.
- Added secure desktop path grants, file selection and editing, backend failure
  handling, update support, and improved workflow run inspection.

### Fixed

- Improved process-tree termination for stopped and timed-out nodes.
- Fixed agent output preservation, thought truncation, loop execution, node I/O,
  invalid workflow display, and packaged provider CLI execution.
- Made notification and approval failures visible instead of silently ignoring
  them.
- Fixed the frontend test command used by the release.

## 0.1.1 - 2026-06-20

### Added

- Added the original TOML workflow engine with conditional routes, recursive
  execution, concurrent nodes, retries, timeouts, and structured fan-out.
- Added named agents and workflows, background scheduling, file watchers, an
  interactive workflow builder, a terminal editor, and graph rendering.
- Added the React workflow studio and Electron desktop application with workflow
  creation, graph editing, run logs, node status, and assistant chat.
- Added Claude Code and Codex integrations, agent memory, thought capture, and
  workflow assistant tooling.
- Added file and folder resources, local file operations, HTTP and LLM utility
  nodes, workflow import controls, and desktop file management.
- Added standalone CLI and desktop packaging for Linux, Windows, and macOS.

### Fixed

- Kept empty or invalid workflow files visible so they could be repaired.
- Fixed workflow deletion cleanup, run stopping, assistant state, prompt-file
  selection, agent input handling, loop outputs, and node status updates.
- Improved desktop backend IPC, update checks, release packaging, and workflow
  data isolation in tests.
