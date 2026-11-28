# Glossary

Shared words for Raticode. Use the term in the first column in anything users read (the app, the phone app, the website, the CLI's help) and the code name in new code. Don't use a word from the last column for the same thing, and don't add a second word for a term listed here. Older names stay until someone changes them on purpose.

| Term | Meaning | Name in code | Words to avoid |
| --- | --- | --- | --- |
| Raticode | The product: the desktop app, the `Raticode` CLI, the phone app and the website. | `gofer` (Python package, `GOFER_*` variables, `gofer:` IPC channels) | gofer, Gofer Flow, Taskurotta, Murina Code in anything users read |
| Rem | Raticode's coordinator agent. It chats with the user, makes tasks and starts workers. | `rem_*` modules, `RemHome` | assistant, bot, the AI |
| task | A piece of work Rem hands to a worker: a goal, its checks and when it runs. A task can run many times. | `task`, `recipe` (the task's saved goal and settings) | job, errand for a task in general (an errand is one task type), ticket |
| run | One attempt at a task, from start to its review result. A repair continues the same run. | `run`, `runs` table, `epoch` (which edit of the run) | execution, session |
| ticket | A card on a board, with a key such as `REM-12` or `ENG-18`. A task can create or work on tickets. | `ticket`, `kanban_*` tables, `rem_kanban` | card, issue, task, item |
| worker | An agent thread that does a task's work, separate from Rem. In an organization, each person on the chart is a worker. | `worker`; organization members are `employees` in config and `employee_id` | employee, agent, bot, sub-agent in anything users read |
| worker limit | How many workers run on one computer at once: Rem tasks, self-work, phone turns, organization workers, swarm turns and work from other desktops. 0 is Auto, which picks a number from the computer's cores and memory. Set in Settings › Computers on that computer. | `maxWorkers` (setting), `max_threads` (API and phone protocol) | Rem task workers, max workers in anything users read |
| workers at once | The organization's own cap on how many of its workers run together, 1 to 16. It is separate from each computer's worker limit, and every start needs room under both. Set on the organization's settings page. | `maxConcurrency` (organization config) | maximum concurrent employees, concurrent employees |
| reviewer | The separate read-only agent that checks a worker's result against the task's checks and the project's standards. | `verify` role, `review_*` | verifier, checker, grader |
| finding | A problem the reviewer reports, on the `spec` or `standards` axis, with severity blocker, should fix or note. | `findings`, `rem_review_findings` | issue, nit, comment |
| decision | An answer Rem reuses instead of asking again, such as the branch work lands on. Some have a record file in `.raticode/decisions`. | `decision`, `rem_decisions`, `rem_decision_records` | rule, policy, setting |
| self-work | Work Rem starts on its own toward the user's objectives when the user is away. | `self_work`, `rem_self_work` | autopilot, background work, idle work |
| level of effort | One of Rem's projects' share of self-work's threads, in percent; the enabled projects total 100. Threads held at once count, not tasks finished. | `project_effort` (setting), `effort_percent`, `rem_self_work_effort` | share, weight, allocation, quota in anything users read |
| data folder | Where Raticode keeps databases, logs, threads and settings on this computer. A path may still contain an old product name, such as `taskurotta-dev`. | `get_data_dir()`, `GOFER_DATA_DIR`, `data_dir` | taskurotta folder, gofer folder, state folder, app data |
| workflow | A Rattish program in `workflow.rattish` that Raticode runs node by node. | `workflow` | pipeline, flow, automation, recipe |
| project | A folder the user opened in Raticode. Tasks and threads can be scoped to one. | `project`, `project_dir` | workspace, repo (unless it means the git repository itself) |
| worktree | A git worktree under `.worktrees/<slug>` where one change is made before it lands on `dev`. | `worktree` | branch folder, checkout, sandbox |
