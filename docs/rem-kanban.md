# Global Kanban and Rem's self-work

> User documentation for this feature is on the website: [Global board and self-work](../website/docs/rem/board.md). The website is the single source of truth for how Raticode works; this file is developer and design detail.

The Kanban plugin shows Rem's tickets beside every organization's. Self-work is what Rem does with its own tickets once the user has gone quiet. Code: `src/gofer/ui/rem_kanban.py` (board and ticket changes) and `src/gofer/ui/rem_self_work.py` (the engine and report).

## Tickets and ownership

- **Rem's tickets** live in `rem-home.sqlite3` (`kanban_tickets`). Their keys are `REM-n`. Statuses are `backlog`, `todo`, `in_progress`, `blocked`, `done` and `cancelled`. Every change checks `revision`.
- **Organization tickets** stay in their organization. The board only reads them, and heartbeat check-ins and archived organizations are left out. Changes go through `OrganizationManager.call`, so approvals, reviews, revisions and consent still apply. A change from Rem's chat uses a `Rem:` actor, a change from a board or phone uses `user`.
- A ticket Rem hands to an organization is created there first and only then closed on Rem's side. An organization's ticket can't be reassigned to Rem: an organization owns its tickets, so make a new one for Rem instead.
- Reading the board never starts an organization, and creating a ticket doesn't either.

## Participants and project boundaries

The board's **People** view lists Rem and every worker in every configured organization, like users in ticket software, and the avatars above the board filter it by person. Each person has the projects they may work in on their own. Only the user can change those, on the desktop or a phone: Rem's chat and workers can't.

No assignment means no self-work. Assignments are stored per participant with a revision, so two devices can't overwrite each other. A phone can only assign projects in its open-project catalog: an organization's project that isn't open on the desktop is counted as hidden there and has to be assigned on the desktop. A person can only be given projects they could use: Rem gets projects open in Raticode, and a worker gets its own organization's projects within its workspace access. An organization still owns its projects, and per-project ticket permissions apply unchanged, so an assignment never widens what a worker may do. Clearing Rem's history keeps assignments.

| | Inside an assigned project | Outside |
|---|---|---|
| Rem's To do and Backlog tickets | Run or pulled in self-work | Left where they are, marked on the card, and hold nothing else back |
| Rem's backlog ranking, duplicate review and research | Each run covers one assigned project and sees only that project's tickets | Not ranked, reviewed or researched |
| An organization worker's check-ins (heartbeats) | Run in the first assigned project | Not created; a waiting one closes |
| Tickets a check-in creates, takes unassigned, or sends back to To do from any other state (including Done and Review, or by asking for changes in a review) | Allowed, and marked as self-work | Refused if the check-in's own worker lacks the project; blocked when it would run if the assignee lacks it |
| A check-in handing a ticket to another worker, or moving a worker's ticket to another project | Allowed if that worker is assigned to the project | Refused |
| A self-work ticket whose assignee lacks the project, such as one a check-in created for that worker | Runs only once the assignee has the project | Blocked with the reason |

A ticket with no project is outside every boundary. A project that is assigned but closed in Raticode or gone from disk is skipped and shown as unavailable. Taking a project away stops self-started work in it, and research that finishes afterwards is discarded.

Work someone asks for isn't limited: moving a ticket to In progress, chat requests, tickets assigned to a worker, routines and scheduled tasks all work as before. A ticket a check-in made stops being self-work once you reopen, move or reassign it. A worker's project is matched with all its Git worktrees, as organization ownership is; Rem's is matched exactly.

## Rem's access

Rem's decision JSON has a `tickets` list (`create`, `edit`, `move`, `assign`). Its prompt gets the open tickets and every configured organization and worker. Tickets are applied one at a time, keyed by the chat request so a replay after a crash adds nothing, and the reply says what happened to each. Workflow prompts that may not act and replies that saw phone screen text can't change tickets. Rem can't approve or decline a proposal, or mark a ticket done.

## Self-work

Self-work's settings (`self_work` in Rem's store, unchanged since they moved) are shown in **Settings > Rem** on the desktop (`SelfWorkSettings.jsx`, which also edits Rem's projects through the participant endpoint) and in **Desktop settings › Rem self-work** on the phone. The board's sidebar keeps the status and report and links there.

Self-work doesn't need the board. The window sends `kanban_board` (whether the Kanban plugin is shown) in Rem's context; a window that doesn't send it counts as on (`rem_kanban.board_enabled`). With the board off, each tick skips the duplicate merge, To do and backlog pulls and ranking, and fills free threads with research only. Those research runs get a prompt without the board that asks for ideas (`IDEAS_FORMAT`), and `_ingest_ideas` writes them to the report as a `researched` row instead of making proposals. Since ideas can't pile up for approval, each project and angle is researched at most once every `IDEAS_AGAIN_AFTER` (24 hours, `ideas_researched` in `self_work_state`), instead of the 12-proposal stop. Toggling is safe: claimed tickets keep counting toward the limit and are still reconciled when their runs finish; a research pass records `board` when it starts, and its results become tickets only if it started with the board and the board is still on; a ranking that finishes with the board off isn't applied; the merge waits until the board is on, even mid-session. `selfwork.read` reports `state.board_enabled`.

Self-work needs at least one assigned project. It starts 15 minutes after the last user prompt: a message in Rem's chat from any device, or an instruction typed in a worker thread. Workflow prompts and Rem's own follow-ups don't count. A restart restarts the wait. A new prompt ends the session; running work finishes, and nothing new starts.

In a session, in order:

Duplicate backlog tickets are merged within each assigned project first. Then every free thread, up to the limit, is filled at once, in this order. The same order applies each time a thread comes free.

1. Every `todo` ticket in an assigned project, by priority then number.
2. **Auto-pull on only:** backlog tickets, best first. A proposal never runs until it is approved. Before a project's backlog is pulled, one ranking run scores each ticket for impact and relevance (1 to 5) and can mark one as no longer making sense. Tickets are pulled by impact, relevance and priority added together. A ticket Rem leaves out stays in Backlog with its reason on the card and in the report. Moving it yourself, or editing it, clears that. Rankings last 12 hours and end sooner if the ticket changes. Only a ticket with a current ranking that doesn't leave it out is pulled, so an expired, failed or missing ranking keeps it in Backlog. New tickets, tickets past the 40 a run can look at, and changed tickets get their own ranking run. A ticket a run saw but didn't judge is tried again after 12 hours, or sooner if it changes.
3. Research with whatever threads remain, whether or not auto-pull is on. Each run looks at one assigned project from one angle: new features, reliability and security, or speed and upkeep. Every project gets its first angle before any gets a second. Each run saves a report and ends with a JSON block of proposals, which become `backlog` tickets waiting for approval. Approving one moves it to To do, and it runs next.

The sidebar's self-work card lists what is running now, with links to each thread, and the report records when a session started and what it took up first.

Each piece of work is an ordinary Rem task and run, so it has the normal authorization, queueing, independent review and recovery. A ticket is `done` only after a run is accepted. A failed or stopped run blocks the ticket with the reason, and it isn't retried on its own. Continuing the run's thread moves the ticket along when that run finishes; only a run that finishes after the block can clear it. Moving a ticket to another column detaches its last run, and a ticket blocked before any run starts (for example, its project is no longer allowed) stays blocked until you move it.

**Limits.** The thread limit is capped by Settings > Rem > Rem task workers. Memory checks still apply. Research is at most 6 runs a session and stops at 12 ideas waiting for approval. It can run again in a later quiet period. A session then says it is out of work until something new arrives.

**Claims.** A ticket's claim and its run are written in one transaction, so only one caller can start a ticket. The engine runs only on the backend holding Rem's lease. After a restart, tickets reconcile with their saved runs. A ticket whose run record is gone is blocked, not rerun.

**Approval boundaries.** Unattended runs are told not to push, publish, deploy, message outside the computer, spend money, change credentials, or start organizations or workflows. `git push` is blocked for them, and they get no browser, subagents, swarms or configured MCP servers. Research keeps Rem's own report-saving server. Reviews still run. Duplicates found by matching titles are closed and can be restored; a restored proposal goes back to Backlog and still needs approval. A restore is permanent: later reviews and research runs neither close that ticket again nor use it to close another. Closed proposals don't count toward the 12 waiting. Once you decline a proposal, Rem can't move it. A worker's own duplicate judgment can only close proposals Rem wrote; on anything else it leaves a note in the report.

**History.** Clearing Rem's history removes the report and keeps tickets and the self-work settings. Running tickets become blocked because their runs are gone.

## The running report

`self_work_report` holds what was added and done (the newest 2,000 entries). Self-work doesn't post chat updates; this report replaces them. It is shown on the Kanban board and on Rem's home. Viewing it on any device marks it seen everywhere; it stays visible until newer entries arrive.

## HTTP and phone interfaces

The desktop calls `GET /api/rem/kanban` (versioned like other Rem polls), `POST /api/rem/kanban/ticket`, `POST /api/rem/kanban/participant`, `PUT /api/rem/kanban/settings` and `POST /api/rem/kanban/report/seen`, on the UI server's token.

Phones use the same service over `desktop.read` and `desktop.action`, gated by **Use Raticode from this phone**. `overview` reports `kanban: true`, and a phone sends these ops only then.

- `kanban.board`: `{tickets, organizations, earlier, counts}`. Tickets carry `ticket_id`, `scope`, `organization_id`, `organization_name`, `key`, `title`, `description` (at most 4,000 characters), `status`, `priority` (`urgent` on the phone for the board's `critical`), `assignee_id`, `assignee`, `proposed`, `proposal_reason`, `duplicate_of`, `created_by`, `created_at`, `updated_at`, `revision`, `running`, `block_reason`, `summary`, `type`, `project`, `run_id` and `thread_id`. No folder paths. Organizations carry their workers for the assignee picker. Open tickets come first; `earlier` counts what was left out for size.
- `kanban.ticket` returns the refreshed board. `action` is one of:
  - `create`: `scope`, `organization_id?`, `title`, `description?`, `priority?`, `status?`, `assignee_id?`, `project_id?`, `type?`, `done_when?`.
  - `edit`: `ticket_id`, `scope`, `organization_id?`, `expected_revision`, then any of `title`, `description`, `priority`, `project_id`, `type`, `done_when`.
  - `move`: `ticket_id`, `scope`, `organization_id?`, `expected_revision`, `column` (`backlog`, `todo`, `running`, or for Rem's tickets `cancelled` and `done`). `running` starts the ticket now.
  - `assign`: `ticket_id`, `scope`, `organization_id?`, `expected_revision`, `assignee_id`. On Rem's tickets, `to_organization_id` hands the ticket to that organization.
  - `approve` or `decline` (`reason?`): `ticket_id`, `scope`, `expected_revision`. Rem's proposals only.
- `kanban.board` also has `participants` and `projects` on desktops whose `overview` reports `kanban_participants`. A participant has `participant_id` (`rem`, or `agent:<organization>:<worker>`), `kind` (`rem` or `agent`), `name`, `title`, `organization_id`, `organization_name`, `employee_id`, `paused`, `projects`, `assignable`, `unavailable`, `hidden_projects` and `revision`. On a phone the project lists hold catalog `project_id`s, and `hidden_projects` counts assigned projects outside that phone's catalog. `projects[]` has `project_id`, `name`, `available` and `organization_name`. Tickets add `project_id` and, for Rem's, `self_work_scope` (`assigned`, `unassigned`, `unavailable` or `no_project`).
- `kanban.participant`: `{participant_id, projects, expected_revision}` replaces the projects this phone can see and keeps the hidden ones. Returns the refreshed board. A project outside `assignable` is `invalid`.
- `selfwork.read`: `{settings, limits, state, report}`. `state.mode` is `off`, `waiting`, `working` or `paused`; `state.thread_limit` is the cap in effect, `state.assigned_projects` counts projects Rem can use now, and `state.outside_projects` counts open tickets it is leaving alone. `report` carries `since`, `updated_at`, `unseen`, `summary`, `tickets_added`, `work_done`, `duplicates_merged` and `notes`.
- `selfwork.settings`: `{enabled?, max_threads?, auto_pull_backlog?, expected_revision}`, returns `selfwork.read`'s answer.
- `selfwork.report.seen`: `{updated_at?}`, the user has seen the report, up to that time when given.

Errors use the usual codes: `conflict` for a stale revision, `invalid` for a value that isn't allowed, `not_found` and `unauthorized`.

Protocol files are the desktop's `src/gofer/devices/protocol/v2` (`event.schema.json`, six fixtures and `SHA256SUMS`). The mobile copy must match them byte for byte.
