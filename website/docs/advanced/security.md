---
title: Permissions and local data
description: Understand project scope, provider permissions, secret references, and data stored on your machine.
pageClass: docs-layout
---

# Permissions and local data

Raticode keeps its working state on your machine and shows which tools each turn can use. The provider you choose still decides where its requests go and how its own permissions work.

## Permission layers

Each layer is separate. Turning one off doesn't turn off the others.

| Layer | What it controls |
| --- | --- |
| Project selection | The folder the studio and project tools work in |
| Rem resources | Shell, web, skills, MCP servers and app tools offered to a turn |
| Provider permissions | The provider's own approval mode and execution policy |
| Workflow preflight | Whether declared executables, paths, credentials and contracts are ready |
| Agent step sandbox | Where a workflow agent step's shell can write, what it can read and which hosts it can reach (from 0.6.0) |
| Organization or device grants | What one organization worker or one paired device may do |

- Turning off Rem's shell doesn't limit an MCP server you added separately. Provider-native tools can have their own policy.
- Choosing a project isn't an operating-system sandbox. Rem's unattended work and workflow agent steps add one; see below.
- A read-only session can look at supported state but can't change it through Raticode's management tools.
- Selecting a tool isn't an instruction. Rem still needs you to ask before it runs a workflow.

## Credential folders no agent can reach

*New in Raticode 0.6.0.*

Raticode keeps credentials in a few folders of its own: each Claude Code, Codex and Grok [account's folder](../rem/providers.md#several-accounts) (`<data>/claude-accounts`, `<data>/codex-accounts`, `<data>/grok-accounts`), paired-device keys (`<data>/devices`), the sites you shared with Rem's browser tabs (`<data>/browser-grants`) and a headless backend's [secrets folder](../reference/headless.md#secrets-without-a-keychain). A Claude Code account's `settings.json` is that account's own settings, so a hook written there would run outside every sandbox, and a Codex or Grok account's `config.toml` can name programs the provider starts. On Linux with bubblewrap, no agent run can read or change these folders, apart from the limits listed below:

| Run | How |
| --- | --- |
| Codex: threads, Rem tasks, self-work, reviews, organization workers, swarms, workflow steps, commit messages | Raticode runs Codex with a permission profile that gives it the same reach as before (read the disk, write the project, temp and the folders it adds) and denies each folder for reading and writing, even inside Raticode's data folder, which threads may otherwise write. Self-work, reviews and organization workers write the data folder only where their run needs to; see [What Codex self-work, reviews and organization workers can write](#what-codex-self-work-reviews-and-organization-workers-can-write). The steerable app-server turns get the same profile. |
| Claude Code, any turn | Read, Edit and Write tool deny rules for each folder. Unattended turns and workflow steps also hide them, and the rest of the data folder, from the shell through Claude Code's sandbox. Turns you watch, which have no Claude Code sandbox, and steps with `sandbox: off` start inside bubblewrap like the providers below, so their shell can't reach the folders either. |
| Cursor, GitHub Copilot, OpenCode, Antigravity, Grok, and Codex with full access or its older settings | On Linux, Raticode starts the CLI with bubblewrap, where each folder is empty and read-only and `/proc` shows only the CLI's own processes. The CLI's own sign-in, the project and the network are unchanged. |
| Workflow agent steps, any provider | An `extra-paths` entry that is, holds or sits in one of these folders, or in a home credential folder such as `~/.ssh`, `~/.aws` or `~` itself, stops the run at preflight (`RATTISH_PREFLIGHT_CREDENTIAL_PATH`) and again when the step starts. |

Account folders that don't exist yet are created empty and owner-only before a Codex or bubblewrap run starts, so a run can't create one first and leave a settings file in it. A turn that runs as one of your accounts keeps that one account's folder, because the provider's app keeps its sign-in and sessions there. Inside bubblewrap, the files in it that name programs the provider's app starts are read-only: Claude Code's `settings.json` (created empty if it's missing), `settings.local.json` and `plugins`, Codex's `config.toml`, `hooks.json` and `rules` folder (rules can let later runs skip asking), and Grok's `config.toml` (created empty if it's missing), hooks, policy and sandbox files, `trusted_folders.toml` (folder trust turns on a project's hooks), plugin folders, `workflows`, `lsp.json`, `pager.toml`, `statusline.sh` and `bin`. See [What Grok's shell can reach](../rem/providers.md#what-grok-s-shell-can-reach). Claude Code's `.claude.json` can't be read-only, because Claude Code rewrites it on every run, and `claude mcp add` from a turn's shell writes an MCP server there. So every Claude Code run on one of your accounts starts only the MCP servers Raticode passes (`--strict-mcp-config`), never servers from the account's `.claude.json` or settings, or a project's `.mcp.json`. Checked with Claude Code 2.1.319: a server written into an account's `.claude.json` started without the flag and didn't with it. A workflow step's Codex profile can't pass `-c sandbox_mode`, `default_permissions` or `permissions.*` to replace the profile; preflight refuses it unless the step sets `sandbox: off`.

Limits:

- macOS and Windows have no bubblewrap. There, and on Linux without it, Claude Code turns you watch, Claude Code steps with `sandbox: off`, Codex with full access or its older settings, and Cursor, Copilot, OpenCode, Antigravity and Grok still run with your full access. Claude Code's file tools keep their deny rules everywhere.
- Codex uses its older settings when your Codex `config.toml` (or a trusted project's `.codex/config.toml`) sets `sandbox_mode`, or on native Windows, whose Codex sandbox can't keep a folder out of a writable one. Raticode logs which; on Linux the run then starts inside bubblewrap instead.
- A turn's own account folder stays readable and writable to it, apart from the files above, as `~/.claude`, `~/.codex` and `~/.grok` are to a turn on the default sign-in. A turn can still change that account's instructions, such as `CLAUDE.md`, `AGENTS.md`, skills, commands and agents, which later turns of the account read, and its `.claude.json`. These can change what a later turn is told, not start a program by themselves.
- MCP servers you add to a Claude Code account with `claude mcp add` aren't started in Raticode's runs of that account, including workflow agent steps. Rem's threads and tasks already used only Rem's MCP server list, so add servers for them there. Runs on the default sign-in (`~/.claude`) still start yours.
- Codex skills kept in a Codex account's own `skills` folder can't run their scripts from a Codex shell command under Raticode's profile, because the account's folder is denied. Skills in `~/.codex` are unaffected.
- Workflow `bash` and `python` steps run your own commands and aren't covered.

### What Codex self-work, reviews and organization workers can write

*New in Raticode 0.6.0.*

Self-work and [reviews](../rem/home.md#how-work-is-checked) run with nobody watching. On Codex, their shell commands can write only these parts of Raticode's data folder, the same parts a [sandboxed Claude Code turn](../rem/board.md#claude-code-s-sandbox) can write:

- The turn's own scratch folder, such as a review's `<data>/rem-work/review-scratch/<turn>`.
- `<data>/command-slots/activity` and `<data>/command-slots/queue`, where queued tests and builds report progress and wait for a slot.
- The task's own folder when it has no project (`<data>/rem-work/<task>`).

The rest of the data folder is read-only to them: databases such as the usage ledger (`<data>/usage.sqlite3`), settings, the command-slot programs, other turns' scratch folders and any folder a task's tools add inside it. The credential folders above stay denied. Unlike Claude Code's sandbox, which hides the data folder, Codex's commands can still read it. The project, temp and folders outside the data folder keep their usual access.

From 0.6.1, [organization](organizations.md) workers on Codex are limited the same way, both the workers this desktop starts and the organization work it runs for a paired desktop. Inside the data folder their commands can write the command-slot folders, their working folder (such as their ticket's worktree under `<data>/organization-worktrees`) and each of their workspaces inside the data folder, such as their ticket's checkout of the organization's shared repository under `<data>/organization-shared-worktrees`. The rest is read-only to them, including the organization database (`<data>/organizations.sqlite3`), the usage ledger, report themes, saved run inputs (`<data>/organization-runs`), copied skills (`<data>/organization-skills`) and a desktop worker's own runtime folder. Organization workers on Claude Code are unchanged.

Unlike self-work, reviews and organization workers, Codex threads you watch and the worker of a task you ask Rem for can still write the whole data folder apart from the credential folders, as Claude Code turns you watch can. So can Codex on native Windows, with its older settings (a `sandbox_mode` in a loaded config) or with full access, where Raticode's permission profile doesn't apply.

## Sandbox for workflow agent steps

::: info New in 0.6.0
The sandbox for workflow agent steps is new in Raticode 0.6.0.
:::

Workflow agent steps on Claude Code run in Claude Code's operating-system sandbox (bubblewrap on Linux and WSL2, Seatbelt on macOS), with the same settings Rem uses for self-work and reviews:

- **Writes.** Shell commands can write only to the step's `working-dir`, its `extra-paths` and a scratch folder for the run, removed when the run ends. In a git worktree they can also write the repository's shared `.git` folder, so commits work, except its hooks, `config` and `info` folder, the worktree's own `commondir`, `gitdir` and `config.worktree`, and other worktrees' folders there: git would run what those name, outside the sandbox, the next time anyone runs git in the repository. The same parts are read-only to Rem's sandboxed Claude Code turns in a worktree. *The hooks and config rule is new in 0.6.2.* In a run with `workspace: worktree` only the parts the run's own branch needs are writable, so a step can't move the project's other branches or tags.
- **Credentials.** `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.config/gcloud`, `~/.kube`, `~/.docker/config.json`, `~/.netrc`, git and GitHub CLI credentials, keyrings, `~/.password-store`, `~/Library/Keychains`, `~/.npmrc`, `~/.pypirc`, Claude Code's and Codex's sign-ins, Grok's sign-in and MCP tokens (`~/.grok/auth.json`, `~/.grok/mcp_credentials.json`, from 0.6.0), a headless backend's secrets folder and Raticode's data folder are hidden from the shell. The Read and Edit tools, which the sandbox doesn't cover, get deny rules for the same credential paths and for paired-device keys and browser grants.
- **Network.** The shell reaches only the hosts in the step's `network` field: none by default, a list of hosts, or `any`.
- **No escape.** A command the sandbox blocks can't ask to run again outside it, and the step fails rather than running unsandboxed when the sandbox can't start.
- **No project hooks.** Unattended Claude Code runs (self-work, reviews and workflow agent steps) don't load a project's own `.claude/settings.json` or `settings.local.json`, because Claude Code runs the hooks they name outside its sandbox. The project's `CLAUDE.md` files are passed as instructions instead. See [Claude Code's sandbox](../rem/board.md#claude-code-s-sandbox). Raticode's own git commands also ignore hooks that a repository's settings name.

Codex steps keep Codex's own sandbox with the reach of `workspace-write`, with the network only for `network: any`, and, from 0.6.0, the same home credential folders and Raticode's [credential folders](#credential-folders-no-agent-can-reach) denied. A run started by a trigger refuses a profile that gives Codex full access. Codex 0.156's own sandbox can deny only one file, and a second one stops the run before it starts ("bwrap: Can't write data to file ...: Bad file descriptor"). So on Linux Codex's profile denies the folders and Codex's own `auth.json`, which Codex reads to sign in, and the other credential files, such as `~/.claude.json`, `~/.grok/auth.json`, `~/.docker/config.json` and `~/.netrc`, are covered for the whole Codex process with bubblewrap. Without bubblewrap those other files stay readable to the step's commands. Rem's own replies on Codex get the same protection.

A run started by a trigger that needs approval also checks each approved script, prompt file and child workflow right before using it, and fails that step if the file changed after you approved it. Shell scripts run from a checked copy in a folder for the run under Raticode's data folder. The sandboxed Claude Code shell can't see that folder and Codex's can't write it, so a step can't swap a later script out from under the check. See [Checked again during the run](../workflows/triggers.md#checked-again-during-the-run).

Cursor, Copilot, OpenCode, Antigravity and Grok have no sandbox Raticode can turn on, and on native Windows no agent step counts as sandboxed. Preflight shows these steps under **Runs unsandboxed**, the workflow's Triggers section lists them, and adding a trigger asks you to confirm. A step can opt out with `sandbox: off`, which the Studio shows as a warning.

The workflow file also decides how far a sandboxed step reaches: its `extra-paths` are writable and `network: any` opens every host. From 0.6.0, an extra path outside the project or `network: any` shows as **Widens its sandbox** in preflight (`RATTISH_PREFLIGHT_SANDBOX_WIDENED`) and in the Triggers section, and adding a trigger asks you to confirm, so a cloned or edited workflow can't widen its own sandbox without you seeing it.

The sandbox limits what a step's shell can do, not whether the provider runs tools at all. MCP servers an agent starts and the WebFetch tool run outside it. See [Sandbox and network](../nodes/agent.md#sandbox-and-network) on the agent node page for the fields and preflight messages.

An [`mcp-tool`](../nodes/mcp-tool.md#sandbox-and-network) step starts its local MCP server in Raticode's own sandbox with the same limits: writes only to its `working-dir` and a scratch folder, the same credential folders and the rest of the data folder hidden (also when its `working-dir` contains them), on Linux the sockets in your session's runtime folder (message bus, keyring, SSH agent) and the system message bus hidden too, no local name lookups (systemd-resolved, Avahi or nscd) unless `network` is `any` (the proxy looks names up), and only the hosts in its `network` field, through a proxy that refuses the rest (bubblewrap and socat on Linux, `sandbox-exec` on macOS). It reads the server's own [package cache](../nodes/mcp-tool.md#package-cache) but can't change it: only starts that list the server's tools (preflight, the Studio's **Tool** picker and `Raticode rem mcp tools`) write there, because they carry no workflow data. A tool call's writes to the cache go to a throwaway layer (a bubblewrap overlay on Linux, a copy-on-write clone on macOS) or, where neither works, to an empty cache in the scratch folder, so one call can't plant files that a later run's server loads. It gets only a base set of environment variables (such as `HOME`, `PATH` and the locale) and the ones its entry in Rem's MCP server list names, not all of Raticode's. A trigger's approval covers each server's command, arguments and variable names, so a changed command, argument list or variable name doesn't run unreviewed. It doesn't cover the code a package runner downloads: a server started with `npx`, `bunx`, `pnpm dlx`, `uvx` or `pipx run` and no exact version fetches the newest release each time it starts, so the code can change while the approved command stays the same. **Review changes** marks such a server with **Package version not pinned: each run fetches the newest release**, approving it asks a second time, and preflight and the Studio warn about it. Pin an exact version (`pkg@1.2.3`, or `pkg==1.2.3` for a Python package), or install the server and run the installed program. Pinning fixes the top-level package only: the packages it depends on still resolve within the version ranges it declares, so they can change between runs too. See [Pin the server's package](../nodes/mcp-tool.md#pin-the-server-s-package). New in 0.6.0.

## Paths and side effects

- Registered Rattish workflows resolve relative paths from their project root. A standalone file resolves them from its own folder.
- Absolute paths, `~` and `..` are allowed, subject to each node's and the operating system's checks. Review paths before you run.
- Commands and agents can change files, call services and start processes. Preflight doesn't simulate those effects, and **Stop** doesn't undo finished changes.
- Review imported workflows, and keep a copy of work you care about, before running them.

## Credentials

- Sign in to coding-provider CLIs through their own login. API-backed providers need their configured credentials.
- In workflows, refer to secrets by name, such as `secret["SERVICE_TOKEN"]`. Don't paste the value into source, reports or screenshots. From 0.5.5, the reference reads a value saved in the system keychain first, then, on a headless backend, a read-only `workflow-secret-NAME` file in its secrets folder, then the environment variable with the same name. Save one in **Workflow settings > Secrets** or with `Raticode rattish secrets set NAME`. See [Secrets](../workflows/inputs.md#secrets).
- Saved workflow secrets belong to the computer and its data folder, and any workflow you run there can read them, so save only what you'd hand to those workflows. Raticode never shows a saved value again, keeps it out of run records, and lets only you save or remove one: agent tokens and the CLI inside a Rem or worker turn can list names but not change them. On Linux, other programs running as you can read the Secret Service too, so the keychain protects values at rest, not from your own processes.
- Device identities, organization secrets, workflow secrets and, from 0.5.5, the [Jev](../workflows/jev.md) key use the platform's credential store. Raticode reads the Jev key only from there, never from an environment variable, and never passes it to agents or workflow commands. On a server with no keychain, a headless backend can read these secrets, except device identities, from a read-only [secrets folder](../reference/headless.md#secrets-without-a-keychain) of owner-only files (from 0.5.5). It never writes a secret there in plain text. Organization tools can grant a secret reference without putting the value in task text.
- [Social post](./posts.md) account logins (webhook URLs, access and refresh tokens, client secrets) have their own keychain service, **Raticode social accounts**, separate from workflow secrets, so no `secret["NAME"]` can read them. Only the post publisher and your own connect and check actions read them, and Rem, workflows and agent tokens can only propose posts, never approve or publish them.
- Workflow environment variables aren't an encrypted vault, and projects and the chat archive aren't encrypted at rest.

## Stored data

Workflow source and editor metadata live in each project's `.raticode/` folder. Everything else is in Raticode's data folder, which keeps its original `gofer` name so existing data stays in place:

| Platform | Default data folder |
| --- | --- |
| Linux | `$XDG_DATA_HOME/gofer`, or `~/.local/share/gofer` |
| macOS | `~/Library/Application Support/gofer` |
| Windows | `%APPDATA%\gofer` |

- Some CLI commands accept a different data folder.
- The desktop app's own profile, which holds working chat state, is stored apart from the backend files.
- Swarms and organizations keep their own saved state.
- Rem's chat, tasks and board are in `rem-home.sqlite3`. Raticode keeps verified copies of it in the private `rem-db-backups` folder beside it ([Rem's database backups](../knowledge/second-brain.md#rem-s-database-backups)).
- An enabled [conversation archive](../knowledge/archive.md) writes a separate copy to the folder you choose. [Second Brain](../knowledge/second-brain.md) indexes your knowledge folder: a registered brain in the data folder, or, for a folder that isn't registered, `.raticode/second-brain.sqlite3` inside it.

Back up every location you rely on. Deleting one copy doesn't always delete the others.

## Network use

Raticode needs no Raticode account or cloud service. These still use the network:

- requests to your AI providers
- web search, when a thread has it on
- remote MCP servers
- model downloads
- Tailscale, if you turn it on for the phone
- HTTP request nodes in workflows
- agent steps' shell commands, only to the hosts in their `network` field when sandboxed
- jev nodes in workflows, which call TypeSafe's Jev service (from 0.5.5)

See [Provider setup](../setup.md), [Tools and resources](../rem/resources.md) and [Troubleshooting](../reference/troubleshooting.md).
