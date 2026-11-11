# Changelog

This file records the major user-facing changes in Raticode. Releases through
version 0.1.3 used an earlier product name.

## 0.5.3 - 2026-11-11

Changes since the `v0.5.2` tag.

### Added

- The Mac app and its DMG are signed with Raticode's Apple Developer ID,
  notarized by Apple and stapled, so they open without a Privacy & Security
  exception. Copies of 0.5.2 or earlier can't update themselves to a signed
  release: install this one by hand once (quit Raticode, open the DMG and drag
  Raticode into Applications). Your projects, settings and conversations stay.
  After that, Mac updates install themselves.
- Workflows can start on their own with triggers: a cron schedule, file
  changes in a folder, or a local webhook that a program on this computer
  calls with its own token. Triggers belong to the computer, not the workflow
  file. Set them in the workflow's **Triggers** section or with
  `Raticode rattish triggers`, and turn on **Start at login** so they keep
  firing after a reboot.
- `Raticode serve --headless` runs the backend without the desktop window, on
  this computer only, with its token in private files that are never printed.
  `Raticode api` and the new resource commands let scripts use Rem's chat,
  tasks and memories, the global board, workflows and their approvals,
  Organizations, swarms, Second Brain, providers and usage, with stable JSON
  results and exit codes.
- Agent tokens: `Raticode tokens create` makes a limited token for a script, a
  CI job or an agent. It can read most things, but it can't act as you: no
  consent decisions, Rem settings, pairing, workflow runs or the owner token.
- **Browser** is its own plugin with one tab bar for every browser. The
  built-in browsers are now called Raticode (chromium) and Raticode (gecko),
  and each tab names its browser.
- **Let this phone wake this computer** in Paired devices shows **Wake
  computer** on the phone, which sends a Wake-on-LAN signal to a Linux
  computer on the same Wi-Fi.
- Rem's chat sends attached screenshots and pictures to the model when it runs
  on Claude Code (any Claude model), Codex, Cursor, Copilot or OpenCode. Before,
  Rem only saw the file's name. When it can't see an image, it says why.
- A Rem task can start after other tasks: say "after the audit finishes, start
  four fix threads", or pick tasks under **Start after**. It waits, then starts
  by itself once they all succeed, told what they found. If one fails, is
  stopped or deleted, it goes on hold with the reason, and you can retry that
  task or start it now. When the follow-up depends on the findings, Rem keeps
  your request and answers it once the first task is done. Waiting tasks show
  on the computer, the phone and the CLI (`rem tasks create --after`).
- Organization workers run like small Rems: blocked tickets first, then To do,
  then proposals in Backlog that fit the current focus, with an hourly check-in
  when idle. A worker you gave projects to stays inside them for everything,
  hands a ticket on only within its organization and with a reason, and can't
  change organizations, swarms, workers, schedules or limits.
- Organizations have a work schedule: named focus blocks that repeat daily,
  weekly, monthly or quarterly, such as feature work by day, hardening in the
  evening and a nightly release. Workers favor tickets that fit the current
  block, and a block change never stops running work. Edit it on the computer
  or the phone; existing organizations have none until you save one.
- Raticode Mobile now lives in this repository under `mobile/android`, with its
  full history. A change to the desktop and phone protocol is one commit, the
  phone's interoperability tests run against the desktop code beside it, and
  each app's checks and releases run only for that app's changes.
- A Kanban board shows Rem's tickets beside every organization's. Rem can add
  and assign tickets for itself or an organization when asked. After 15
  minutes without a prompt Rem fills the threads you allow: to-dos first,
  then, if you turn it on, the highest-impact backlog tickets, leaving out
  ones that no longer make sense. Spare threads research new tickets that wait
  in the backlog until you approve them into To do. The board shows what Rem
  is working on, and a running report of what it added and did waits for you
  on the desktop and phone.
- The Kanban board has People: Rem and every organization worker appear like
  users in ticket software, and you assign each to projects. Working on their
  own, they stay inside those projects; with none assigned they don't work on
  their own. Work you ask Rem for directly isn't limited.
- The phone can browse the projects open in Raticode and open their files:
  code and text with highlighting, Markdown, HTML and SVG previews, PDFs,
  pictures, video and sound. It only reads, files that may hold passwords stay
  on the computer, and files over 10 MB can't be downloaded to the phone. It is
  part of **Use Raticode from this phone** in Paired devices.
- Google Chrome can be the web browser (Settings > Browser). Raticode runs
  Chrome without a window in its own profile and shows its pages in tabs; Rem
  can use it too. Chrome's own settings, extensions, Chrome Sync and Google
  sign-in open in a regular Chrome window on that profile.
- Microsoft Edge can be the web browser too, on the same engine as Chrome, in
  its own Edge profile. Microsoft and Google sign-in, Edge settings,
  extensions, Edge Add-ons, sync and import open in a regular Edge window on
  that profile, and the phone can switch the computer to Edge.

### Changed

- The landing check that holds a Rem task until its branch reaches local `dev`
  now applies only under a worktree skill whose description says its branches
  land on `dev`. A general worktree skill in a repository with a `dev` branch
  no longer turns it on.
- The website deploys to GitHub Pages from `main` once its checks pass, and
  gains privacy and support pages.
- The Arch packages are ready for the AUR as `raticode-bin` (desktop) and
  `raticode-cli-bin` (command line). They carry the real 0.5.2 checksums, a 0BSD
  package license and a real maintainer, and a release workflow can refresh and
  push them after each release once an AUR key is added. They are not published yet.
- A desktop app installed by pacman no longer tries to update itself. Its update
  button says to update with pacman, for example `yay -Syu`.
- Source control opens to History each time you open it, showing the commits
  without waiting for the worktree list. Changes and Branches are a tab away.

### Fixed

- A finished Rem task no longer shows **Plan** or slips back to an earlier
  state, on the desktop or the phone.
- Agents running inside Raticode can no longer act with your authority through
  the CLI or a limited token, and agent tokens can no longer read Rem's tool
  server list, whose addresses can carry keys.
- Webhook trigger tokens no longer have to go on the command line.
- When a webhook or file trigger starts a workflow, its Rem prompt nodes tell
  Rem that the text may come from someone else, so Rem doesn't start work
  because the incoming text asks for it. The log warns when such a prompt may
  start work.
- Rem won't open projects in, or send files from, macOS keychains and
  browser profiles, Windows saved credentials and browser profiles, or Edge,
  Vivaldi and Opera profiles on Linux, as it already didn't for Chrome,
  Firefox and SSH keys.
- When the offline speech model can't download, the log names the error type,
  such as a certificate failure, on every Python version.
- Music playing in a Firefox, Chrome or Edge tab no longer stops when the tab
  sits in the background: those tabs now say when they play sound, so they show
  the speaker (with its mute) and stay loaded like Raticode's own tabs instead
  of pausing after 30 minutes to free memory.
- Rem can no longer click past Firefox's certificate warning ("Accept the Risk
  and Continue") or its other error pages, in its own tabs or tabs you shared,
  matching Chrome, Edge and Raticode's own tabs. You can still decide in your
  own tab.

- Clicking **File explorer** in the activity bar always brings your open files
  and browser tabs back into view, even when it is already selected, so it's
  the quick way back to a page from Rem's chat, Second Brain, a swarm or an
  organization. Tabs stay open as they were, and pages aren't reloaded. Other
  plugins still change only the sidebar.
- Writing in one of Rem's work threads after it ended, stopped or failed, or
  steering it while it runs, no longer adds a "Please continue with the user's
  latest instruction." message from Rem: the worker answers your message as
  written. The thread now labels each instruction **Rem** or **You**, on the
  desktop and in the phone's work-thread view, and Rem can tell them apart
  when it reads the thread. The worker still receives both as user messages.
- Failures that used to vanish now appear in Settings > Developer: unexpected
  errors in the desktop's API (which also now return an error instead of no
  answer), Rem requests and worker turns that fail, phone requests to Rem,
  Second Brain and swarm background work, generation and chat jobs, workflow
  runs, provider CLIs that can't start, updater errors, git commands that fail
  for reasons other than "not a repository", and startup failures shown only
  in the error window.
- Routine events no longer look like problems: `uv`'s startup output, path
  access renewals, retried model replies, user mistakes in requests, terminals
  that already closed, cancelled requests and clean page exits are info or
  debug. Background loops log a lasting failure once and say when it recovers.
- A damaged browser history, bookmarks, sites or downloads file is kept aside
  instead of being overwritten with an empty one.
- Rem's logs no longer quote the model's raw reply.
- Organizations safeguards: workers, Rem and routines can no longer set the
  fields Raticode keeps for itself, such as a ticket's worktree, which could
  have given a run a writable folder outside its project. A rerun needs a new
  review, workers can't lift or reword blocks set by you, Rem or a reviewer,
  and work created during a run keeps that run's review rules and delegation
  depth.
- Rem now asks before changing the work a routine creates, changing who a
  worker reports to, or pointing a tool server at a different command, and a
  request to start an organization names its risky settings.
- Organization runs that hit their time limit say so. A stop that arrives
  just after a run finished no longer reruns it, a result the worker already
  set survives a failing run, and reaching the monthly run limit lets running
  work finish before pausing. Work stopped at closing time goes first when
  hours open, and a worker's pull no longer skips a ticket's start date.
- Routines create their tickets on time while every worker is busy or hours
  are closed. Each worker keeps one check-in ticket instead of adding a new
  one every time.
- The shared repository only merges into `main`, keeps a ticket's checkout
  while its run is still stopping, and keeps the record when finishing fails.
- On the Organizations board, Done work dragged to Backlog reopens straight
  into Backlog, Undo after moving a ticket to In progress works, and Review
  work goes back through Request changes. Editing a ticket, plan item, note,
  worker or working hours no longer overwrites changes made elsewhere, linking
  a note keeps the plan's approval, and a removed worker's tickets can be
  reassigned or cancelled.

## 0.5.2 - 2026-11-06

Changes since the `v0.5.1` tag.

### Changed

- The command line tool is now `Raticode` instead of `gof`. Change scripts,
  scheduled jobs and aliases that call `gof`. The standalone downloads are now
  `raticode-cli-linux-x64`, `raticode-cli-macos-arm64` and
  `raticode-cli-windows-x64.exe`, and the Linux packages are `raticode-cli`,
  which replace the earlier Linux CLI package on upgrade. The Windows installer adds the
  same folder to your PATH, which now holds `Raticode.exe`. Rem's helper copy
  of the CLI is renamed when the app starts, and the old copy is removed.
  Workflows, schedules, settings and other saved data are unchanged.
- The Python package is now `raticode`, so optional spreadsheet support
  installs with `pip install 'raticode[xlsx]'`. The Arch packages are
  `raticode-cli` and `raticode-desktop`.
- Documentation uses the Raticode name throughout.
- Source control History shows each commit's added and removed lines right
  after its ID, on the same line as the author and time, so you no longer open
  a commit to see them. Binary files show as a count, merges count against
  their first parent, and a commit without line counts shows none rather than
  +0. When the panel is too narrow for one line, the ID and its counts move to
  the next line together.

### Added

- Organizations open on five tabs: Organization, Projects, Issues, Workers and
  Knowledge. Write a one-line mission and organization milestones; give each
  project milestones that contain epics; run tickets on a board with keys such
  as `STU-12`; edit each worker's persona and responsibilities on an org chart;
  and keep shared decisions, specs and findings in a knowledge graph that links
  to tickets. Click anything to see its links in a side sheet, or press Ctrl K
  to find it.
- Every ticket shows evidence you can check. Runs post short claims backed by a
  project check, a quote from a note, a commit or a file. Raticode checks quotes,
  commits and files itself. Project checks run when you press Run checks on the
  ticket, or after every run if the project allows it. Raticode flags failed
  checks and changed sources. If a claim doesn't hold up, accepting the ticket
  asks you to confirm.
- Organization knowledge can include notes from the Second Brain and can be
  saved back to it. Raticode records which notes each run read.
- Organization details open in the main area with a back control at the top
  left, and Escape returns to the list. The org chart drags and zooms (Ctrl with
  the wheel or with + and −), the Issues board scrolls sideways, and folders are
  added and removed from the Projects tab. Workers can be removed.
- Workers can be limited to creating, editing, moving or cancelling tickets in
  chosen projects. Raticode enforces this on every ticket action a worker takes.
- Each organization can keep a shared Git repository for its knowledge,
  decisions and notes. Runs change it on their own branch, and accepting the
  ticket merges the changes.
- Waiting Organizations tickets say why they haven't started, such as no worker
  assigned, the organization paused, a cancelled dependency or the worker busy
  with another ticket. Old low-priority tickets no longer jump ahead of critical
  ones, and tickets in a cancelled epic or milestone wait instead of running.
- Only you or Rem can schedule recurring Organizations tickets, at least 15
  minutes apart. Rem needs your approval, as for routines.
- Organizations have working hours. Choose 24/7, or weekly hours in a time
  zone, including overnight hours such as 22:00 to 06:00. Outside them nothing
  starts and check-ins wait. At closing time, running work either stops and
  runs first when hours open, or finishes; you choose. The header shows when
  hours open or close. An organization can also keep running after Raticode
  restarts instead of pausing. Rem needs your approval to widen the hours or
  turn that on.
- Workers can plan ahead and pick what's next. Moving a ticket to In progress,
  on the board, from Start next or by a worker, puts it first in its worker's
  queue, and it starts as soon as the worker is free. Workers can also take an
  unassigned ticket for themselves. Their ticket permissions still apply. The
  board's Running column is now called In progress.
- Worker check-ins (heartbeats) close themselves when they finish, so the next
  one comes on time instead of waiting in Review. A failed check-in is
  replaced, up to three times in a row. After that, check-ins stop and Needs
  you says why, with a Resume check-ins button. Set how often each worker
  checks in from its details.
- Workflows can talk to Rem. A Rem prompt node sends a message to Rem's chat
  and passes Rem's reply to the next steps; the message shows as coming from
  the workflow. Unless you turn on Allow actions, Rem only answers it and won't
  start, change, remember or send anything for the workflow. A Rem task node
  runs one of Rem's existing tasks, as its Run button does, and waits for the
  result, so a workflow can run tasks one after another and hand each task's
  result to the next. A task that fails, is stopped or is deleted fails its
  node with the reason, and stopping the workflow stops the task it started.

## 0.5.1 - 2026-11-05

Changes since the `v0.5.0` tag.

### Added

- Files attached in Rem's chat show on the phone whichever device sent them.
  Pictures and videos attached in the desktop window load on the phone by
  themselves and other files download when tapped; files the phone sent come
  back from the computer after the phone's own copy is gone. A file deleted on
  the computer, or a phone that isn't connected, is shown as such. Needs phone
  version 0.18.0.
- In Source Control, the target branch for a merge or rebase is a list you can
  type in to search. It shows recommended branches first, then local branches,
  then remote branches such as `origin/main`, each in alphabetical order. A
  branch can now be rebased onto a remote branch, so right-clicking a branch
  and choosing Rebase can target `origin/main`. Merges still only go into
  local branches.
- Asking Rem to link a file ("link me the video") gives a link you can click
  instead of trying to send the file, so it no longer refuses with a warning
  about passwords. Asking Rem to send a file works as before.
- Full file paths in Rem's desktop chat are links you can click, including
  paths with spaces. Raticode finds them by checking which files exist; no
  model is involved.
- Files you attach in Rem's chat or a thread show a thumbnail or a file-type
  tile instead of just their name, before and after sending, on the desktop and
  the phone. Click or tap one to preview it: pictures and videos open large,
  text and code show as text, and other files open in their app.
- Reply to any message in Rem's chat, yours or Rem's, on the desktop and the
  phone. The message box shows which message you're replying to, and the sent
  message shows it above your words; click or tap it to scroll back to it. Rem
  reads the whole message you replied to, however far back it is, along with
  its task when it has one, so "continue" on an older task's update continues
  that task. A reply to a message that is no longer in the chat says so. Reply
  replaces **Ask about this**, which pasted a quote into the message box.
- Long messages in Rem's chat fold to a preview about a third of the screen
  tall, with **Show more** to read the rest and **Show less** to fold it again,
  on the desktop and the phone. Only messages at least half again as tall as
  the preview fold. Rem's newest message opens in full; older long replies and
  long messages you sent, such as pasted logs, start folded. Files, cards,
  Reply and Copy stay visible, and copy, find and screen readers still get the
  whole message.
- **Tidy voice messages** (Settings > Rem, off by default). After you dictate in
  the message box or in voice mode on the phone, a fast model adds punctuation,
  drops filler words and fixes misheard names using Rem's chat, and your words
  appear only once that is done, ready to edit. There is no undo; edit the
  words directly. The first choice follows your default provider: Claude Haiku
  5.5 for Claude Code (needs Claude Code 2.1.293 or later; run `claude update`),
  GPT-6 Luna for Codex, Qwen 3.8 Flash for OpenCode, and the fastest listed
  model elsewhere. If tidying is off, unavailable, slower than 15 seconds or
  changes too much, the words appear as heard with a note saying why.
- The desktop microphone works like voice mode on the phone. The same dotted
  line rises with your voice while you talk. Choose how it listens under
  Settings > Devices > Dictation: **Press to start and stop** (the default),
  where you press the mic once to start and again to stop, or **Hold to talk**,
  where it listens only while you hold the mic. Either way, stopping goes
  straight on to your words; there is no Done step. The words wait in the
  message box for you to edit and send. For hands-free use, turn on **Send
  after dictation** in the same place to send them as soon as they're ready;
  words that couldn't be tidied still wait for you. In Press to start and stop,
  Enter also stops, a long silence stops on its own, and clicking the line
  pauses and resumes. Words already in the box and words heard before a pause
  stay. **Discard** drops only what you just said. With **Tidy voice messages**
  on, your words stay hidden until the tidying model has finished, which starts
  as soon as you stop.
- Dictation uses the speech recognizer built into the operating system, on the
  computer: Apple's on-device recognizer on macOS, which asks once for speech
  recognition permission, and the offline recognizer behind Windows speech
  recognition on Windows. Linux has no built-in recognizer, so it keeps
  Raticode's offline model. Settings > Devices > Speech recognition shows
  whether the system recognizer is ready, says why when it isn't, and lets you
  choose Raticode's offline model instead. If the system recognizer refuses
  while you talk, the offline model takes over, if it's installed, and nothing
  you said is lost.
- Press Ctrl+Alt+V (Command+Option+V on a Mac) to dictate while Raticode is
  in front. It starts in the message box you're using, or opens Rem's chat and
  starts there. In Press to start and stop, press it again, or Enter, to stop;
  in Hold to talk, hold it while you speak and let go to stop. While you
  dictate, Space pauses and resumes (Press to start and stop only), and Esc
  discards only what you just said. The mic button's tooltip and the hint under
  the message box show the keys, and **View > Dictate a Message** starts and
  stops dictation in either mode. Change or clear the shortcut under Settings >
  Keybindings. On keyboards where AltGr+V types a character, such as
  Hungarian, AltGr+V still types it.

### Changed

- Update Typer to 0.27.3, APScheduler to 3.11.3, pydantic-settings to 2.16.1,
  cryptography to 50.0.2, bibtexparser to 2.1.0, electron-builder to 26.18.0,
  PostCSS to 8.5.32, `actions/checkout` to 7.0.1 and
  `actions/download-artifact` to 8.0.2. Second Brain's BibTeX import and export
  keep working the same way on bibtexparser 2, including field names in any
  case and repeated citation keys. bibtexparser 2 is MIT-licensed and adds
  pylatexenc (MIT).
- Build the desktop window with Vite 8.3.5 and `@vitejs/plugin-react` 6.1.2,
  which needs Vite 8. Startup still loads only the app's own code and React;
  markdown support and the code editor load when first used, as before.

### Fixed

- Full file paths in Rem's chat on the phone are links, including paths with
  spaces and paths written as code, and tapping one downloads and opens the file.
  The desktop tells the phone which paths name real files; every file check still
  applies when one is tapped.
- Stopping dictation, or letting go of the mic in Hold to talk, no longer cuts
  off the end of the last word: the last fraction of a second of audio still on
  its way is now heard before the microphone closes. With **Tidy voice
  messages** on, a pause in speaking now tidies the words so far as intended;
  before, the steady stream of audio kept postponing it.
- With **Tidy voice messages** on, a long dictation is tidied as you go instead
  of in one block when you stop: once at least 15 new words and 5 seconds since
  the last round have passed, and at every two-second pause. The tidied words
  show in the message box while it keeps listening, and each round re-tidies
  the whole dictation, so a sentence cut in half is mended. When you stop, a
  round that already covers everything is used at once. A round that fails
  changes nothing on screen and isn't retried until you say more.
- Voice tidying keeps swearing as you said it instead of softening or
  masking it, and a tidy that masks words you said is refused, so your words
  appear as heard. When speech recognition itself wrote a swear word as
  "f***", tidying writes it out only when the first letter, length and
  sentence leave one possible word, and otherwise leaves the mask. Masked
  words also no longer lose asterisks when the tidied text is cleaned up.
- An opened Rem task now shows in the Rem sidebar in place of the task list,
  with a back arrow at the top left that returns to every task, like leaving a
  thread. The chat keeps its full width instead of sharing it with the task.
  Opening a task from a card in the chat brings the Rem sidebar forward, and
  **View in conversation** keeps the task open while the chat scrolls to the
  message. Half-edited tasks survive switching to another plugin and back, and
  the form fits the sidebar at its narrowest.
- Rem's chat no longer scrolls sideways in the side pane. Saved-report cards
  shorten long titles and keep their **Open** button in view, and a task's
  progress steps wrap onto two rows in narrow panes.
- Moving Rem's chat between the main area and the side pane no longer freezes
  the window. The chat now moves as it is instead of being rebuilt, so it keeps
  your place, an open task form and the memory panel, and messages scrolled out
  of view skip layout until they come near. With a 700-message history a move
  went from over a second to about 60 ms. While Rem works, a status update
  redraws only the task cards that changed.
- Dictating into a microphone that sends no sound, such as a headset with its
  mic muted or switched off, no longer just shows a flat line. After two
  seconds Raticode names the microphone, says it may be muted or off, and keeps
  listening, so your words come through as soon as it's unmuted. The microphone
  test in Settings > Devices says the same.
- Phones connected through Tailscale no longer drop when the desktop's Tailscale
  briefly restarts, wakes from sleep or answers slowly, and no longer forget the
  desktop's Tailscale address right after the desktop starts. A phone that
  reconnects replaces its own dead sessions, so they can't use up the desktop's
  connection slots.
- Sharing your phone screen with a voice request no longer blocks work you ask
  for in your own words. Rem decides that work again without the screen, so
  anything only the screen asks for still doesn't happen.
- A message you send while Rem is still replying, from the desktop or the
  phone, including a reply typed into a Rem alert or said in voice mode, no
  longer comes back with "Rem is still replying; try again shortly" on the
  phone, and on the desktop it is no longer lost if the window reloads while it
  waits. Rem keeps it, shows it as **Sends when Rem finishes replying** on both,
  and answers it next, in the order it arrived. Up to five can wait.
- Rem's reply as it is being written on the phone no longer comes out garbled
  when Rem has to ask its model again partway through, for example to fix its
  own task changes, even if the phone lost its connection in between. The
  phone now starts over with the new text.
- If clearing Rem's history gives up because a worker won't stop, the message
  Rem was answering is answered again instead of being left without a reply.
- A delegated conversation no longer stops when Rem's own verdict on it comes
  back malformed once; Rem asks again. A worker that answers a conversation
  with nothing is retried, as empty worker turns already were.
- When Rem can't finish a request, the decision that failed is kept with its
  error reply in Rem's own records, so the cause can be traced later. It isn't
  sent to the apps, and nothing is kept when the request carried text from the
  phone's screen.
- Asking Rem to build what an earlier research task proposed no longer fails
  with "Changing projects requires updating the task first". When Rem moves a
  task's work into a project, the work continues in a new thread in that
  project.
- Rem fixes its own mistakes in a reply's task changes, such as a change to a
  task that doesn't exist, two changes to one task or a field the action can't
  change, by asking its model once more. Before, the whole reply failed.
- A dropped connection no longer stops Rem's work for good. Claude Code's
  "API Error: Connection error.", Codex's "error sending request" and similar
  network errors now retry after a short wait, as overloads already did. So
  does a worker turn that ends cleanly with no answer, which used to send the
  provider's raw output to review or block the task with "Worker ended without
  a result".
- Rem's own chat replies on Codex no longer fail when Codex reconnected and
  then finished, and Codex's error text is shown instead of "Provider reported
  a failed response". This also applies to workflow agent nodes.
- A desktop or worker thread whose provider exits without an answer shows
  "The provider ended its turn without a final answer." instead of the
  provider's raw output.
- Closing Raticode while Rem is replying no longer answers your message with
  "Rem stopped". Rem answers it after it starts again.
- A reply that asks to read earlier messages and also proposes tasks now does
  the read and decides again, never acting on that first reply. Before, the
  reply failed.
- Rem reads a reviewer's verdict even when the review quotes a small JSON
  example before it, and never mistakes a provider's own records for its
  answer.
- A task update that keeps failing to post waits longer between tries instead
  of retrying, and possibly calling the chat model, several times a second.
- A Rem research report that couldn't be saved no longer fails with "Swarm tool
  failed. Retry or ask the orchestrator to inspect it.", even with Swarms turned
  off. Rem's report tool shares a tool server with Swarms, and that server hid
  the real reason. Now the worker is told when a report name is already taken
  and which free name to use, when a path is outside the report folder, or when
  a report was written but its receipt couldn't be recorded. Saved reports are
  still never replaced, and a full path inside the report folder now works.
  Rem's follow-up quotes the save error, and Rem stops after one follow-up when
  the save fails the same way again instead of asking for more identical
  saves. Tasks other than research are no longer told to save with a report
  tool they don't have.
- A picture or video in a chat whose file was moved or deleted still says it's
  no longer on this computer, without the desktop logging an error and stack
  trace each time the chat shows it.
- When the phone drops its connection partway through a reply, the desktop ends
  that session quietly instead of logging "socket.send() raised exception." for
  each remaining piece of the reply. The phone resumes where it left off when it
  reconnects, as before.
- A Claude Code turn that hit a dropped connection, retried and then finished
  normally no longer counts as failed. Before, the retry left behind an
  "unknown" error that blocked a finished Rem task after its review had passed.
- **Clean up branches** in Source control now also removes a branch, and its
  worktree, when a branch you always keep (or the default branch) is on the
  same commit, such as a finished branch that `dev` was fast-forwarded to. It
  still keeps branches with their own commits, worktrees with uncommitted or
  untracked files, and locked ones. It now also keeps worktrees a program is
  working in, worktrees whose branch has no commits yet, and Rem's, swarms' and
  organizations' task worktrees. Each branch is checked again right before it
  is deleted, so one that changes during cleanup is kept.

## 0.5.0 - 2026-11-03

Changes since the `v0.4.0` tag.

### Added

- Raticode Mobile can let Rem read the screen you're looking at for one voice
  request, when you turn that on. The text is used for that reply only and isn't
  saved; Rem's answer is kept like any reply. Whatever the AI returns, a reply
  that saw a screen only answers: it never starts or changes tasks, saves
  memories, sends files or makes phone cards.
- Pictures and videos Rem sends show right in its chat, on the desktop and the
  phone. Click or tap a picture to see it large, or a video to play it. Web
  images in chat open large too, with a button for the browser.
- Rem's memory works more like ChatGPT's and Claude's, with any provider. Ask
  Rem to change or forget something it remembers and it edits that memory
  instead of saving a second one. A reply that saves, changes or forgets a
  memory says so, with Undo. What Rem remembers can pause new saves or turn
  memory off, add your own memories and copy them all, on the desktop and the
  phone. Rem won't save anything that looks like a password, key or token. Task
  workers on every provider now get your saved preferences, prompts send the
  most relevant memories once there are many, and Claude Code and Codex no
  longer keep copies of Rem's chat sessions on disk.
- Rem can send you any file this computer's account can read, not only files in
  an open project or the Second Brain, when you ask for it and its path is in
  your conversation. Password and key folders, the coding agents' sign-ins
  (such as `~/.claude` and `~/.codex`), files named like tokens, credentials or
  keys, and Raticode's own data are sent only when your message names the
  exact path.
  Paired phones receive these files too.
- Rem can send files a task listed by folder and name, such as "Screenshots are
  in `/dir/`:" followed by the file names, instead of saying their paths aren't in
  your conversation. When none of the files can be sent, Rem no longer says
  "Here they are" above the reasons.
- When you ask Rem to send something it first has to find or make, such as "Send
  me a video", the file now arrives with the task's update when it finishes,
  instead of waiting for you to ask again. A phone that asked gets it too. It
  goes through the same checks as any file Rem sends, and repeating schedules
  never send files on their own.
- Rem's chat shows richer results on the desktop and the phone. Finished tasks
  get a card with their checks, saved reports and the web pages the work used.
  Running tasks show Plan, Work, Check and Report with their checks and time.
  Replies can open a Details section with tables, highlighted code and simple
  charts, and offer up to three suggested replies. While Rem replies it says
  what it is doing, and Claude Code replies appear as they are written. Each
  reply has Copy, Ask about this and its model and time. Web images in chat now
  load through Raticode, so sites don't see your computer's own request.
- Optional Tailscale access for paired phones away from home. Off by default
  under Paired devices. Raticode also listens on this computer's Tailscale
  address and keeps the Wi-Fi connection, pairing and encryption unchanged.
  Settings explains how to sign in with your own account and limit access to
  Raticode's port. Raticode never signs in to Tailscale or changes its settings.
- Run a Rem thread on another paired computer. Choose it in Run on, next to the
  permissions menu, when you start a thread. The other computer opts in under
  Paired devices, uses its own projects and sign-ins, and streams its progress
  back. Turns sent from your phone in that thread run there too.
- Rem is no longer experimental. Its home is in the activity rail for
  everyone, with no Feature Preview switch. If you hid Rem from the sidebar,
  it stays hidden until you show it again in Settings > Plugins.
- Feature Preview in Developer settings has a switch for every experimental
  feature: Swarms, Organizations, Second Brain, and phone and desktop pairing.
  Features that are off are hidden everywhere else. Organizations and pairing
  no longer need an environment variable at launch.
- Rem's chat can use its own provider, model and effort in Settings > Rem, so
  a fast model answers in chat while tasks and threads keep a stronger one.
- Claude Code replies appear word by word in Rem threads instead of all at once.
- Rem threads that use the web show cards for the pages Rem cited, read and
  found, with titles and thumbnails, plus video cards for YouTube links. Rem is
  told to show real images from pages it read when they help.

### Changed

- Release checks and candidate builds now run on pushes to `stage` instead of
  `main`. When a candidate is ready, squash merge `stage` into `main` and tag the
  squash commit; publishing checks that it has exactly the candidate's files.
  A signing environment set up for `main` needs its branch rule changed to `stage`.

### Fixed

- Tapping a file link in a Rem reply's Details on the phone fetches the file,
  instead of saying the link isn't in the message. A file link in Rem's chat
  also opens files outside open projects, the same files Rem can attach; credential
  stores, secret-looking files and Raticode's own data still stay on the desktop.
- A paired phone no longer gets stuck unable to reconnect until Raticode Mobile
  is reinstalled. When the desktop turned down something the phone had queued,
  such as a download whose file expired while the phone slept, it hung up
  instead of answering, and the phone sent the same request again on every
  reconnect. The desktop now answers, and the phone drops that request and says
  why. A phone that is stuck this way recovers once the desktop is updated, with
  no reinstall or new pairing.
- A workflow run can't be resumed twice at once. The desktop, a paired phone
  and `gof` used to be able to start the same resume together and redo finished
  work. A crash never leaves a run unable to resume.
- Rem's chat saves each reply once. A finished reply used to be written to
  conversation storage a second time.
- Crash reports, Linux packages and Arch packages link to the
  `zacharyivie/Raticode` repository instead of its previous address.
- Phone threads no longer show compaction memory or continuation notes as
  messages, and those hidden rows no longer split a run of thoughts into two
  groups.
- A task's work thread is readable on the phone. The phone used to get the
  newest 120 transcript records one by one, almost all of them tool and
  thinking steps, so the instruction and the report fell out of view. The
  desktop now folds each run of steps into one group, as its own thread pane
  does, and sends replies whole instead of cut at 4,000 characters.
- Source control updates on its own when worktrees are created or removed, by
  Raticode, agents, swarms or `git worktree` in a terminal, and when commits or
  staged changes land. History refreshes while it is open, and the refresh
  button still works.
- Source control History keeps commit text on the left and the branch graph on
  the right, so a wide graph no longer cuts the text off. The text gets about
  two thirds of the panel, and a scrollbar under the graph shows the rest.
- Pairing another computer has its own choice in Pair a device, with clear steps
  to create an invitation link on one computer and paste it on the other.
- Paired desktops work in both directions without typing an address, keep each
  other's status current, and stay fast over time. Every paired machine,
  including ones paired with another desktop, is listed on each desktop and
  phone, and work can target threads the other desktop shared instead of
  copied IDs.

### Security

- Rem no longer sends password and key folders, the coding agents' sign-ins,
  files named like tokens, credentials or keys, or Raticode's own data just
  because they sit in an open project or the Second Brain. Your message has
  to name the exact path, and asking for `id_ed25519.pub` no longer releases
  `id_ed25519`. Tapping a link to such a file on the phone is refused too.
- Packaged desktop builds encrypt the built-in browser's cookies with the
  computer's key store. Existing cookies keep working and are encrypted as
  sites update them.
- Raticode's data folder is private to your account, so other accounts on the
  computer can't read conversations, job logs or databases.
- Claude Code turns read their tool connections from a private file instead of
  the command line, which other accounts on the computer can see.

## 0.4.0 - 2026-10-28

Changes since the `v0.3.9` tag.

### Added

- Rem's home, opened from the rat-face tab: one continuous conversation plus the
  work Rem is responsible for. Rem answers simple questions directly and starts a
  linked worker thread for anything else. Tasks can run once or on a schedule,
  are grouped as Working on, Needs you, Coming up, Paused and Done, and show
  their progress, checks and review threads.
- A Second Brain library of local folders with exact and semantic search,
  collections, saved searches, pinned passages, cited research and reports.
  Brains are independent of projects; closing one never deletes its folder.
- Use Raticode from a paired phone. Per-phone opt-ins, both off by default,
  allow answering workflow and swarm approvals and controlling swarms, or
  talking to Rem, managing its tasks, running organizations, checking usage and
  changing display settings. Work still runs on the desktop under its approval
  policies; files, terminals, the browser and credentials stay there.
- Preview a workflow run before starting it, including its inputs, file access
  and how many items each loop will process.
- Rattish validation warns about resource limits, such as bounded watch queues
  and file-scan limits.
- A public website with installation, workflow, node and feature documentation.
- Rem's Browser tool drives Raticode's own browser tabs in the background, so you
  keep working while several threads test pages in parallel. Settings > Browser can
  switch Rem back to an external Playwright browser, visible or headless.
- The integrated browser behaves like a full browser: links that open new tabs,
  sign-in pop-ups, per-site permission prompts, a downloads list, a security
  indicator with site information, friendly error and certificate pages, history
  with address-bar suggestions, bookmarks, reopen closed tab, per-site zoom,
  printing, Save as PDF, HTTP sign-in, spell checking and audio muting. Restored
  tabs keep their back/forward history, and long-hidden tabs free their memory.
- Share a browser tab with Rem, pause Rem, or take over a tab Rem opened. Rem's
  tabs show what it did, outline the elements it uses, and ask before it submits
  forms, uploads files or runs scripts on signed-in pages. Rem can answer page
  dialogs, read same-origin frames and upload files from its project.
- A redesigned Rem Tools menu with switches, search and add forms, backed by a
  shared library of skills and MCP servers. Agents add entries with `gof rem`;
  the menu also finds Raticode, Agents, Claude and Codex skill folders.
  Skills now include their descriptions and are readable by every provider.
- A Plugins settings section for the left sidebar. Show or hide each plugin,
  drag plugins into order in the sidebar or settings, and lock their positions.
  File explorer is always shown. Rem, Swarms, Organizations and Second Brain
  are experimental: they stay listed but are off until turned on.

### Changed

- Paired phones and desktops connect only over the local network. The internet
  relay has been removed.

### Fixed

- Fill in provider allowances without extra setup. Cursor, Antigravity, Grok
  and OpenCode's Claude or ChatGPT sign-ins report usage from the signed-in
  CLI. Allowances refresh in the background and after provider activity.
  Copilot Free's elapsed reset dates no longer mark fresh readings out of date,
  and Cursor runs record tokens.
- Keep Claude usage current without handling Claude credentials. Claude Code
  reports its five-hour, weekly and model-specific limits through its own
  `/usage` command, with hooks and MCP servers disabled for the check. Each
  Claude turn in Raticode updates the limits at no extra cost, and checks back
  off after failures so Claude's rate-limited usage service is not overloaded.
- Show each workflow's real state in the Workflows sidebar: running, failed,
  stopped or invalid with its first compile error, instead of always "Ready".
  Show the name the workflow file declares, and when it last ran.
- Rename and duplicate Rattish workflows from the sidebar. Duplicates leave
  files excluded by `.raticodeignore` behind.
- List runs newest first with their age and duration, and drop runs the
  backend has deleted. Fit small graphs at normal size when they open.
- Update locked anyio, pydantic, python-dotenv, tzdata, mypy, globals, wait-on,
  autoprefixer and eslint-plugin-react-hooks, and the release workflow's GitHub
  Actions. ESLint stays on 9.x until eslint-plugin-react supports ESLint 10.
- Update release browser tests for the grouped thread list, real workflow
  status, the new phone controls and Electron 45, and let release packaging
  rebuild native modules for Electron 45.

### Security

- Rem's browser access is now granted per turn and bound to its thread, and it
  ends when the turn ends or the Browser switch is off. The long-lived
  `browser-control.json` credential is no longer written and is deleted.
- Rem browses in its own profile. It uses your sign-ins only on sites you list
  in Settings > Browser. Local HTML previews run in a temporary profile.
- Pages can no longer open studio tabs, zoom or navigate by dispatching fake
  input; pop-ups need a real click. Downloads go through a managed list, files
  that can run code wait for review, and Rem's downloads are blocked by default.
- Upgrade Electron from 41.10.7 to 45.1.0 (Chromium 146 to 156). Electron 41
  stopped receiving Chromium security fixes in August 2026, so web pages ran on
  an engine ten releases behind Chrome.
- Integrated browser tabs present a consistent identity. They send the
  `Sec-CH-UA` client hint headers Electron omits, matching what page scripts
  report. Opted-in sites get detailed hints through `Accept-CH` and
  `Critical-CH`. The user agent uses Chrome's reduced format and
  `Accept-Language` matches the page's languages. Google sign-in no longer
  reports "This browser or app may not be secure", and sign-in pop-ups no
  longer announce Electron and Raticode.
- Upgrade locked Pillow to 13.0.0 through fastembed 0.9.1, pypdf to 6.21.3,
  filelock to 4.2.4, msgpack to 1.2.4, certifi to 2026.10.23 and setuptools to
  84.2.1 to address their published advisories.
- Upgrade the frontend's locked undici, Vite 7.3.7, source-map-js,
  follow-redirects, shell-quote, fast-uri and joi to patched releases.
- Review and record licenses for the Second Brain dependencies. Ship upstream
  license texts for loguru, flatbuffers and tokenizers, whose wheels omit them,
  and include onnxruntime's third-party notices.
- Build and test with Node.js 22.23.4, which fixes the October 21, 2026 Node.js
  security release (including a Brotli decoder out-of-bounds write and TLS
  hostname verification bypasses). The desktop app's Electron 45.1.0 already
  bundles the patched Node.js 24.22.1.

## 0.3.9 - 2026-10-03

Changes since the `v0.3.8` tag.

### Added

- Resume provider conversations across turns and restarts, with incremental
  prompts, duplicate-send protection and context-aware provider handoffs.
  Cursor can retry its observed malformed-response error in the same session.
- Git history graphs with branch, remote and tag filters, ancestry search,
  tracking badges and adjustable history limits.
- Animated image, video and audio previews with seeking and playback controls.
  Pause playback when its editor tab becomes inactive.
- Explorer range selection with Shift-click, group dragging, copy/cut across
  projects, native file drops and duplicate-file decisions. Keep both, replace
  or cancel; apply the choice to duplicates within the same paste or drop.
- Recover unsaved editor drafts after restart. Add browser-page Find, shared
  editing commands, language workers and terminal shell selection.
- Shortcut validation, reduced-motion preferences, automatic thought collapsing,
  and report theme import/export.

### Fixed

- Replace pasted and dragged files with their incoming contents, refresh open
  editors, and confirm deletion of multiple selected files with their count.
- Preserve both source and destination when replacement fails. Refuse folder
  replacement when destination files change during copying, including nested
  edits that leave the destination folder's own timestamp unchanged.
- Preserve resource and permission settings across provider changes, validate
  MCP configuration, renew project access and handle missing worktrees safely.
- Improve provider image handling, reasoning/answer separation, bounded protocol
  records and provider error reporting.
- Run file/media browser regressions in release validation, install their FFmpeg
  prerequisite and isolate the fixture's clipboard and saved state. Wait for
  lazy syntax coloring and media playback to become ready on busy release runners.

### Security

- Upgrade locked urllib3 to 2.8.0 to address three streaming and HTTPS proxy
  advisories. Review and retain its MIT license notice.
- Remove the vulnerable braces dependency chain by migrating the CSS build to
  Tailwind 4. Preserve the studio palette, selector precedence and existing
  radius, shadow, ring, placeholder and outline defaults.

### Release limitations

- Native Windows/macOS installer and update acceptance remains part of candidate
  review. Arch checksums must match the actual published candidate artifacts.
  Organizations and network device pairing retain their experimental gates.
  See `docs/releasing.md` and `docs/security/device-release.md`.

## 0.3.8 - 2026-09-30

Changes since the `v0.3.7` tag.

### Added

- Provider usage dashboard with recorded tokens, costs, subscription windows and
  explicit unknown coverage. Optional reporting credentials for Claude, Cursor,
  OpenAI and Anthropic stay in the OS credential store, separate from inference.
- Read-only, side-by-side commit diffs with text search, next/previous match
  navigation, highlighting, and include/exclude file filters.
- Independent report themes with AI-generated previews, reference images,
  configurable generation settings and streamed progress. Reports can use
  Markdown, standalone HTML, slides or desktop-rendered PDF.
- Background commit-message and report-theme generation that survives navigation.
  Optional auto-commit uses the captured staged snapshot, preserves later edits,
  runs Git hooks and refuses to overwrite a branch that moved during generation.
- Rename Rem threads and configure a shared commit-message template for drafts
  and chat commits. Fork user messages or final replies; replay a forked user
  message in its new thread.
- Experimental organizations with persistent Rem employees, reporting charts,
  teams, project ownership, initiatives, tasks, approvals, independent review,
  routines, usage limits, configuration history and operational backups.
  Optional managed execution supports swarms, paired desktops, HTTPS gateways
  and explicitly authorized Rattish workflows.

### Changed

- Organizations is disabled by default. Quit and relaunch Raticode with
  `RATICODE_EXPERIMENTAL_ORGANIZATIONS=1` to opt in. The backend blocks management,
  new execution and routine webhooks while disabled, and omits organization tools
  from Rem. Existing data is preserved; recovery still attempts to stop earlier
  authorized jobs. The sidebar explains experimental access.
- Combine microphone and pairing controls under Settings > Devices. Improve
  select controls and move commit-message preferences into their own editor.
- Use plain bullets in default commit messages. Preserve authored report themes
  and full-page backgrounds in PDF output.

### Fixed

- Update locked `brace-expansion` and `fast-uri` dependencies to fix denial-of-service
  and URI host-normalization advisories. Print failed npm audit reports in release
  validation logs while retaining the audit failure and downloadable evidence.
- Prevent competing runners from claiming and executing the same queued workflow.
  Keep active runner ownership and heartbeat updates through long jobs and
  cancellation, and preserve terminal status when completion races with Stop.
- Pass CLI runner inputs to the executor, validate them before enqueueing, and
  retain absolute workflow paths when the worker uses another working directory.
- Keep revision inputs in new code review templates from executing shell commands
  or injecting Git options, and disable external diff and text conversion commands.
- Preserve all organization template tasks and their recurring intervals when
  task IDs normalize to the same export filename.
- Keep credentials out of malformed HTTP header, method and URL path errors
  recorded in workflow failures and notification diagnostics.
- Keep MCP tools available after requests with deeply nested JSON or escaped
  lone Unicode surrogates, rejecting excessive nesting before invoking tools.
- Keep credentials out of HTTP network-policy failure details and malformed URL
  errors, and handle malformed HTTP authorities without crashing compilation.
- Save provider profiles atomically with private file permissions, preserving
  existing profiles on write failure and preventing writes through file links.
- Keep Stop, timeout and task-cancellation cleanup active when a subprocess parent
  exits while its children still hold output pipes open.
- Reject workflow bundles with case-colliding paths or file/directory conflicts
  before extraction, and prevent exports that would produce those bundles.
- Export files with timestamps outside ZIP's supported range by clamping archive
  timestamps to 1980–2107 without changing the original files.
- Keep Stop and timeout active while sending large prompts to provider processes,
  drain output concurrently, and retain provider errors when input closes early.
- Preserve Unicode characters split across subprocess output reads.
- Stop remaining POSIX child processes even when their parent exits before them.
- Allow concurrent atomic saves to create the same new output folder without
  failing one save, while still rejecting replaced files and symbolic links.
- Honor stop requests in direct OpenAI and Anthropic API calls. Skip requests
  already stopped, close active HTTP connections on stop and return a stopped
  result instead of accepting a response that arrived during cancellation.
- Accept files and folders whose names begin with two dots inside approved paths.
  Reject copying a folder into such a descendant before creating recursive copies.
- Restore selected MCP tools, including Second Brain, in Cursor by nesting the
  temporary plugin folder and matching its directory name to its permission grants.
- Stop acknowledging device file-status acknowledgments, and remove queued chunks
  when a transfer finishes or is cancelled so reconnects do not resend them.
- Reject incomplete device-relay responses and handle broken chunk framing
  through the relay's existing retry and session-recovery path.
- Reject incomplete local MCP request bodies before invoking a tool, even when
  the received fragment is valid JSON.
- Reject incomplete HTTP bodies in usage reporting, remote organization job
  receipts, package downloads and PDF rendering. Truncated PDF responses return
  a recoverable report-tool error without publishing partial files.
- Reject files replaced by named pipes during desktop copying without hanging
  while waiting for a pipe writer.
- Recover conversation archives after an interrupted journal write, retaining
  complete history and removing only the unfinished final record before retrying.
- Keep Second Brain and report tools available after malformed MCP tool calls,
  returning an invalid-parameters response instead of terminating the server.
- Reject truncated HTTP responses instead of treating partial content as a
  successful request. Accept bodyless HEAD and 304 responses whose Content-Length
  describes a representation larger than the response limit.
- Reject Windows drive paths, alternate data streams, device names and ambiguous
  path spellings in workflow bundles before extraction. Export rejects filenames
  that cannot safely round-trip across supported platforms.
- Preserve executable script permissions when importing workflow bundles on
  POSIX systems, without restoring setuid, setgid or sticky bits.
- Refuse workflow export inputs replaced by symbolic links after the folder scan,
  preventing unrelated local files from entering a shared bundle.
- Publish the selected Git branch even when a tag has the same name, and set its
  upstream without pushing the tag.
- Preserve local branch names when a tag has the same name, keeping branch
  selection and merge/rebase previews available.
- Reject organization evidence files replaced by symlinks and bound reads to
  10 MB. Workflow previews compile the same bytes used for their source hash.
- Preserve worktree folder names containing newlines in the worktree list and
  merge/rebase destination lookup.
- Handle concurrent legacy workflow migration when another caller removes the
  old filename during the identity check, preserving the migrated source.
- Ignore malformed background-generation records during startup so a damaged job
  cannot prevent the backend from opening or hide valid saved results.
- Preserve commit diff editors and focus while search and file filters change.
- Stop managed swarms when a launch reply is lost or authorization is revoked
  immediately after launch. Do not submit a remote job already cancelled during
  setup.
- Reconcile interrupted remote organization jobs in the background so unreachable
  gateways cannot block backend startup.
- Include the new Rem actions, commit diff, usage, report theme/output and
  organization browser regressions in release source validation.
- Add the reviewed MIT license entry for locked PyYAML 6.0.3, allowing the
  third-party notice collector to complete.

### Security

- Reject swarm artifact files replaced by symbolic links or named pipes after
  validation, keeping verification inside the workspace and avoiding blocked reads.
- Redact complete URL credentials in desktop logs, including passwords containing
  `@`, uppercase HTTP schemes, and username-only URLs.
- Guard working-tree diff previews and conflict-marker reads against replaced
  symbolic links and named pipes, and enforce their 16 MB limit while reading.
- Redact complete Basic and Bearer credentials and quoted secrets in desktop
  logs, including token punctuation, spaces and escaped quotes.
- Recheck organization workflow source reads at launch without following links,
  enforce the 10 MB limit and verify the preview's source hash before compilation.
- Scope device file acknowledgments and cancellation to the transfer's authorized
  thread before removing pending chunks or stopping a send.
- Apply a fixed five-second request-read deadline to local Rem and swarm tool
  connections. Slow header or body trickles can no longer hold their connection
  slots indefinitely; dispatched tool calls retain their existing execution time.
- Recheck turn-scoped tool authorization after reading a request body. Requests
  delayed until after token revocation or server shutdown can no longer invoke
  Rem, fleet, swarm or organization tools with the expired grant.
- Bound search-and-replace verification reads to 2 MB, including files that grow
  after search or after the replacement size check, before changing any content.
- Reject attachment directory swaps during conversation archiving and bound
  actual attachment reads to 20 MB, including files that grow after their size check.
- Require an explicit HTTP network allowlist entry for non-public shared addresses
  in 100.64.0.0/10, including DNS results and IPv4-mapped IPv6 addresses, preventing
  unapproved requests to services reachable through carrier or VPN routes.
- Reject file and directory links swapped during project search, text editing and
  image previews. Bound actual reads to 2 MB for search/text and 25 MB for images,
  including files that grow after their size check.
- Store uploaded and forked chat attachments atomically without following storage
  directory links. Refuse swapped source links, bound fork reads to 20 MB per file,
  and prevent failure cleanup from following a replaced destination directory.
- Disable Git text converters in editor line, merge, rebase and stash previews.
  Read-only previews now show raw changes without invoking configured converters
  that could execute commands or hide changed lines.
- Validate saved generation-job IDs against their filenames before recovery.
  Read job records without following symlinks and publish private files atomically,
  preventing cached IDs or predictable temporary links from redirecting writes.
- Keep staged-diff reads inside the selected project folder. Background commit
  generation requires opening the repository root before capturing all staged
  changes, preventing a subfolder request from silently expanding its scope.
- Refuse symlink swaps during organization package reads and enforce the byte
  limit on actual content, including files that grow after their size check.
  Write cached organization skills atomically without following destination links.
- Compare the checked swarm configuration again under the launch lock. A concurrent
  change to permissions, resources or other settings now rejects the launch.
- Restrict external browser links to HTTP and HTTPS. Render PDFs in an isolated
  Chromium session with scripts, navigation, permissions and external requests
  disabled; require a private token for the local rendering service.
- Align Claude Code plan-mode tools and commit-diff handling with read-only
  permission constraints.

### Release limitations

- Organizations remains experimental. Live gateway interoperability, provider
  behavior and paired-desktop execution need release-package validation. Budget
  enforcement depends on reported usage and cannot sandbox native provider children.
- Native installer/update checks on Windows and macOS remain part of candidate
  review. Refresh Arch package checksums from the actual 0.3.8 artifacts before
  distributing those package recipes. See `docs/releasing.md`.

## 0.3.7 - 2026-09-24

Changes since the `v0.3.6` tag.

### Added

- Start a Rem thread in the background with Ctrl+Enter from the thread list.
  Stay on the list, clear the composer, and animate the new running thread into
  view. Enter opens the new thread; Shift+Enter inserts a new line.
- Fork Rem threads from messages and thought history. Copy history through the
  selected point, provider and model settings, permissions, resources, project
  scope, and attachments. Fork attachments remain available if the original
  thread is deleted; file-change undo stays with the original thread.
- Add Allowed and Denied model columns for each provider, with arrows to move
  selected models and double arrows to allow or deny all. Save these preferences
  across restarts and hide denied models from model dropdowns.
- Choose a dedicated commit-message provider and model in Settings > Providers,
  independently of the active Rem conversation. Keep the option to use the active
  Rem selection and reject disabled providers or denied models when drafting.
- Support commit-message drafting with Cursor, GitHub Copilot, OpenCode,
  xAI/Grok, and Antigravity alongside Codex and Claude Code.

### Changed

- Replace the commit-message Rem button with a rat-and-sparkles action icon.
- Move threads up the list when the user sends a message or a turn finishes.
  Streamed thoughts and intermediate agent messages no longer reorder the list,
  including updates received from paired devices.
- Show tools, skills, and MCP controls on the thread list so resources can be
  selected before starting a thread. Enable web search in new defaults while
  preserving explicitly saved off settings.
- Put the thread-start keyboard hints in one horizontal row beneath the composer.
- Give every settings category a distinct icon, including Providers, Rem, Memory,
  Paired devices, and Developer.

### Fixed

- Restore the macOS login-shell PATH before provider discovery so Dock and Finder
  launches can find provider CLIs installed through the shell.
- Keep the global Run workflow shortcut from intercepting Ctrl+Enter in the Rem
  composer. Capture thread resources before the first request, including when
  starting in the background.
- Center thread activity spinners and unread completion dots with the archive
  and menu buttons.
- Preserve global scope when reopening a global thread.
- Accept Markdown-wrapped Conventional Commit messages, use Cursor's final
  answer instead of intermediate commentary, and retry invalid formatting once.
  Report provider failures without treating them as formatting errors.
- Capture staged changes once for commit-message generation and reuse the same
  diff for formatting retries. Reject empty or oversized index snapshots before
  launching a provider.

### Security

- Read the Git index with external diff helpers and text conversion disabled.
  Run Codex and Claude commit drafting from a temporary directory containing the
  captured diff instead of the project. Claude can only use its Read tool for
  large diffs; remove its Git shell-command grants. Codex retains its read-only
  sandbox.
- Restrict Cursor, Copilot, and OpenCode commit drafting to read tools, with
  shell and write access disabled. Keep Grok and Antigravity on their native
  CLI-managed permission policies.

### Release limitations

- Dependency advisory scans and the full test suite still need verification in
  an environment with network and local socket access.
- macOS Dock provider discovery and live commit drafting require checks with the
  release package. Refresh the Arch package checksums after building the 0.3.7
  artifacts. See `docs/releasing.md`.

## 0.3.6 - 2026-09-22

Changes since the `v0.3.5` tag.

### Changed

- Show every provider section immediately in Settings. Retain saved preferences
  and cached model catalogs while providers refresh independently in the
  background each time Settings opens.
- List each project's root worktree once in the project selector. Opening a
  linked worktree selects it under its root project, and previously saved
  worktree entries consolidate without losing the active selection.
- Keep branch and worktree switching in Source Control > Branches. Branch rows
  are clickable and mark the current branch with a purple bar. Remove the branch
  dropdowns from Source Control and the global toolbar.
- Move Runs from the toolbar to a bottom-panel tab beside Run Timeline, retaining
  run counts, filters, and controls.
- Offer the matching Mac DMG download and manual installation instructions for
  unsigned macOS updates.

### Fixed

- Open sibling worktrees and other accessible desktop folders without requiring
  prior picker approval. Keep desktop navigation independent of backend grants;
  project selection still registers access for backend project services.
- Handle macOS temporary-folder aliases when capturing Rem file changes and
  loading speech models. Unreadable snapshots no longer fail a completed turn
  and disable undo with an explanation.
- Include the macOS Vosk speech library, microphone usage declaration, and audio
  entitlements in desktop packages.
- Clear the downloading state when an automatic update download fails.
- Audit every locked Python package version when platforms require different
  versions of the same package. Reject incomplete or skipped audit results and
  discard stale evidence before each scan.

### Release limitations

- Paired-device networking remains experimental and opt-in, with the acceptance
  gates documented in `docs/security/device-release.md` still open.
- macOS manual updates and microphone permissions require testing with the exact
  release package. See `docs/releasing.md`.

## 0.3.5 - 2026-09-22

Changes since the `v0.3.4` tag.

### Added

- Experimental pairing with Android phones and other desktops, with locally
  confirmed identities, encrypted LAN and relay messaging, protected credential
  storage, and device revocation and removal.
- Shared desktop and phone conversations with searchable history, grouped tool
  activity, attachment labels, and running-turn status. Phones can create,
  configure, pin, archive, and request deletion of shared threads.
- Scoped file transfers and delegation to paired desktop workers, with local
  authorization, durable request tracking, and replay protection.

### Changed

- Confirming a new phone pairing enables desktop thread and project sync by
  default. Existing phones retain their sync setting. Shared phones act as the
  same user and can change provider permissions; legacy scoped grants retain
  their limits. Desktop must remain open to synchronize archived history.

### Fixed

- Accept Cursor CLI builds without a build-date allowlist. Selected project and
  Global working folders use Cursor's workspace trust flag while retaining
  provider tool permissions.
- Share relay rate-limit cooldowns across subscriptions and pause sessions for
  the server's requested backoff.
- Reject named pipes during device file snapshots without blocking the device
  service while waiting for a writer.
- Include device dependencies and the protocol schema in packaged backends.
- Run paired-device and mobile-conversation browser regressions in the standard
  release validation suite.

### Release limitations

- Device networking remains experimental and requires explicit opt-in.
  Independent security review, physical phone and two-host testing, relay
  endurance, and exact-package acceptance remain open before public device
  support is advertised. See `docs/security/device-release.md`.

## 0.3.4 - 2026-09-19

Changes since the `v0.3.3` tag.

### Added

- Added a swarm inbox for human approvals and restart recovery decisions, with
  retained decision history.
- Swarm agents can work across multiple repositories. Named review worktrees
  are prepared for all projects before tasks are dispatched.
- Added Rem access to swarm workspaces, swarm diagnostics, and run completion
  handling.
- Added global project selection for Rem and thread creation in background
  projects.

### Changed

- Rem's conflict-resolution instructions now include completing the pending
  merge or rebase after resolving conflicts.
- Desktop backend path grants are queued and cached to avoid repeated requests.

### Fixed

- Updated AnyIO to 4.14.2 to address TLS hostname validation and process-pool
  stderr deadlock vulnerabilities, CVE-2026-63374 and CVE-2026-64847.
- Workflow discovery handles filesystem errors and files changing during scans.
- Cancel stale project discovery requests and preserve current results when
  switching projects.

## 0.3.3 - 2026-09-18

### Fixed

- Removed the desktop backend's 15-second startup timeout so slow cold starts
  can finish without the backend being terminated.

## 0.3.2 - 2026-09-18

Changes since the `v0.3.1` tag.

### Changed

- Settings → Providers now saves per-provider model and effort defaults that
  override discovered defaults when switching providers. Reset them to follow
  the provider again.
- Closing the desktop window now hides Raticode and keeps background work running.
  Reopen it from the tray or by launching Raticode again. Use the tray's
  "Quit Raticode and stop background work" action to exit.
- Rem turns now run independently of their HTTP connection, with disk-backed
  events for reconnecting without repeating provider work or delivered messages.
- Idle swarms now ask the coordinator to recover unfinished work rather than
  immediately failing. Coordinators can resolve uncertain attempts after
  inspecting retained effects, and repair attempts have no fixed count limit.
- Removed the global default swarm concurrency ceiling and automatic stall-turn
  pauses. Per-swarm concurrency settings and manual pause/stop controls remain.
- Large chat histories are archived before compaction and summarized in chunks.
- Reduced startup work, file-tree and thread-list loading, Git branch queries,
  and rendering of collapsed tool output. The desktop opens maximized with a
  startup screen while the backend loads.

### Fixed

- Reconnect Rem when the initial response is lost after a turn has started.
  Reconnection uses the existing turn ID and never repeats the launch request.
- Preserve streamed thought batches through reconnection so conversation history
  commits a batch once instead of writing each event separately.
- Improved Rem edit attribution for Codex and Claude Code, and retained chat
  errors in conversation history. Stop requests made during startup are delivered
  once the turn is ready.
- Fixed Windows path comparisons across project, thread, terminal, and editor
  state, including drive-letter case, separators, and UNC paths.
- Preserved editor sessions across file moves and handled Git refreshes without
  discarding unsaved edits. Project selection becomes available before workflow
  discovery finishes.
- Fixed Grok MCP permission handling and preserved streamed ACP thought updates.

## 0.3.1 - 2026-09-17

Changes since the `v0.3.0` tag.

### Compatibility

- Removed Gemini CLI support. Select Antigravity (`agy`) and review its model,
  authentication, executable, and permission settings for existing Gemini CLI
  workflows, profiles, Rem threads, and swarm members. Gemini models remain
  available through Antigravity; provider settings are not migrated automatically.

- Workflow discovery now lists only managed workflows at
  `.raticode/<name>/workflow.rattish`. Move standalone and legacy workflows into
  this layout and explicitly migrate unregistered `.rad` sources. Existing files
  are not deleted, but workflows outside the managed layout no longer appear in
  the project list.
- Grok defaults to CLI-managed permissions. Strict Raticode tool restrictions
  remain unsupported for Grok and block sending when selected. Existing explicit
  thread permission choices are preserved.

### Added

- Added Cursor, GitHub Copilot, OpenCode, Grok, and Antigravity CLI
  provider adapters and their Rattish provider contracts.
- Added provider settings, browser sign-in, and model refresh after authentication.
  Cursor, Copilot, Grok, and Antigravity expose their native model and reasoning
  effort choices.
- Added steering for active Rem turns, including attachments, persistent delivery
  receipts, cancellation, and recovery after interruption. Conversation context
  survives provider changes.
- Added thread pinning, manual archiving, and confirmation before deletion.
- Added swarm Git permission settings for local operations and remote publishing,
  automatic task retries, lifecycle controls, and progress reporting.
- Added local file paths and `file://` navigation in the integrated browser, with
  local links routed through the editor's path authorization.

### Changed

- Removed fixed swarm run and output caps and the fixed terminal session ceiling.
- Reduced worktree discovery overhead and cleaned up unused worktrees. Backend
  access registration now happens when selecting a worktree rather than while
  enumerating every worktree.
- Isolated Electron development profiles from the installed application's profile.
- Updated thread headings and spacing, provider loading states, and permission
  feedback. Failed steering receipts no longer clutter the conversation.

### Fixed

- Default runners now detect Antigravity and Grok using the shared provider
  executable resolver, including configured paths and nvm installations.

- Ctrl+J creates a new browser tab on every press, including when a browser is
  already open or has page focus. Ctrl+T consistently creates a terminal in the
  selected project instead of sometimes creating a browser tab.
- Fixed terminal creation on first use and while the bottom panel is collapsed.
  Removed stale workflow context from project chat and kept terminal working
  directories tied to the selected project.
- Fixed Grok MCP startup injection and readiness checks, including the ACP
  `_x.ai/mcp/list` method and session-only transport placeholders.
- Fixed Cursor compatibility with verified September CLI builds and enabled MCP
  discovery. Provider health checks and planning now detect nvm-installed CLIs.
- Fixed Copilot invocation-directory trust and final-answer delivery. Model
  catalog policy denials are distinguished from authentication failures.
- Added an owned ACP subprocess transport with bounded lifetime, cancellation-safe
  teardown, deep-JSON rejection, and draining of buffered updates before final
  results or terminal errors.
- Fixed portable steering cancellation races and receipt recovery, and persisted
  per-thread permission selections across remounts.
- Protected verified swarm work when dismissing stale deliveries, prevented
  delivery recovery during active verification, and reused integration retries.
  Accepted worktrees are cleaned safely and progress history includes only valid
  objective snapshots.
- Confined swarm publishing to the intended repository and sanitized the packaged
  environment used for verification commands.
- Handled browser navigation failures and views closing during pending operations.
  Bounded backend startup stderr buffering and logged backend exits.
- Fixed release-candidate discovery so ready draft releases can be found
  independently of their tag names. Updated bundled dependency license notices.

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

- Renamed the user-facing application to Raticode.
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
