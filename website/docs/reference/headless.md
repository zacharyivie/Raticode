---
title: Headless backend
description: Run the Raticode backend on a server or in a container with no desktop window, and give scripts and agents limited tokens.
pageClass: docs-layout
---

# Headless backend

`Raticode serve --headless` runs the Raticode backend with no desktop window, for servers, remote boxes and containers. Rem, tickets, Organizations, workflows and triggers keep working, and scripts on the same computer reach the backend's API with a token file.

::: info New in 0.5.3
Headless mode arrived in Raticode 0.5.3. Earlier versions have only `Raticode ui serve`, which needs the desktop window for Rem.
:::

## Start it

```bash
Raticode serve --headless
```

The backend listens on `127.0.0.1:8765`. Use `--port 0` to let the system choose a free port, and `--data-dir PATH` (or the `GOFER_DATA_DIR` environment variable) for a different data folder.

Headless mode listens only on this computer, over TCP. It refuses `--host 0.0.0.0` or any other non-loopback address, and it can't listen on a Unix socket. To reach it from another machine, forward the port over SSH, for example `ssh -L 8765:127.0.0.1:8765 box`. Raticode has no remote listener or TLS yet.

`Raticode serve` without `--headless` is the same as `Raticode ui serve`: the backend the desktop app starts.

## Find the endpoint and token

At startup, headless mode writes two files in `<data>/headless/`. On Linux and macOS the folder is readable only by your account (mode 700), and so are the files (mode 600):

| File | Contents |
| --- | --- |
| `endpoint.json` | The API URL, process ID, data folder and token file path |
| `owner-token` | The full-access API token, replaced at every start |

[`Raticode api`](./cli.md#talk-to-a-running-backend) finds both files on its own:

```bash
Raticode api GET /workflows
```

Other clients send the token as `Authorization: Bearer TOKEN`:

```bash
curl -s -H "Authorization: Bearer $(cat ~/.local/share/gofer/headless/owner-token)" \
  http://127.0.0.1:8765/api/workflows
```

Both files are removed when the backend stops. The token is never put on a command line, where other accounts on the computer could see it.

To supply your own token instead, such as a mounted container secret, pass `--token-file PATH`. The file must belong to you and be mode 600. The `GOFER_UI_API_TOKEN` environment variable also works. In both cases Raticode doesn't write `owner-token`.

::: warning Windows
On Windows, Raticode doesn't set or check permissions on these files, on token files you pass with `--token-file`, or on agent token files. They get whatever access the folder they're in allows. The default data folder is inside your user profile, which other standard accounts can't normally read, but an administrator can. Keep custom data folders and token files inside your profile too.
:::

## Give Rem its settings

The desktop window normally tells Rem which projects, provider and resources to use, and Rem's tasks wait with "Waiting for Raticode to connect" until it does. In headless mode Rem reads them from `<data>/headless/rem-settings.json`, or from the file named with `--rem-settings PATH`:

```json
{
  "provider": "codex",
  "model": "cli-default",
  "projects": ["/srv/repos/api", { "root": "/srv/repos/web", "name": "Web" }],
  "brain": { "enabled": true, "root": "/srv/second-brain" },
  "swarm_access": false
}
```

The file takes the same fields the desktop sends: `provider`, `model`, `effort`, `chat_provider`, `chat_model`, `chat_effort`, `projects`, `resources`, `brain`, `report_theme`, `conversation_archive_root`, `browser_target`, `swarm_access`, `command_slots` and `kanban_board`. Project and Second Brain folders must be existing absolute paths. Rem gets access to every folder the file lists, so the file must belong to you and must not be writable by other accounts.

If the file doesn't exist, the backend still starts, and queued Rem tasks say they are waiting for it. Restart the backend after changing the file.

[`Raticode rem settings export --file rem.json`](./cli-rem.md#rem-s-settings) writes this format from a running backend. `Raticode rem settings apply` changes the settings without a restart, but only for folders the backend already has access to. To add a project folder, add it to the settings file and restart.

## Turn on Organizations

Organizations are a Feature Preview. The desktop app turns them on from Settings, but a headless backend doesn't read that choice. Start it with the environment variable instead:

```bash
RATICODE_EXPERIMENTAL_ORGANIZATIONS=1 Raticode serve --headless
```

Without it, `Raticode org` commands exit 5. `Raticode doctor` shows whether the variable is set in the environment you run it from.

## Agent tokens

The owner token can do anything you can do in the desktop app. For scripts, CI jobs and agents, create a limited agent token instead:

```bash
Raticode tokens create ci --output ~/ci.token --expires-days 30
Raticode tokens list --format json
Raticode tokens revoke ci
```

`create` writes the token to a new file that only you can read and never prints it. Raticode stores only a hash. Revoking takes effect immediately, even on a running backend, and the tokens work with the desktop's backend too.

An agent token acts with Rem's authority, not yours:

- Starting an organization, granting it full access or lifting holds records a consent request that you approve in the Organizations tab. An agent token can't approve it, approve Kanban proposals, store credentials or approve workflow approval gates.
- It can't send Rem messages, create or change Kanban tickets, or control Rem's tasks and memories, because Rem treats those as your requests. Rem's self-work, for example, starts To-do tickets on its own.
- It can't change Rem's settings, self-work settings, provider settings or sign-ins, Rem's skills and MCP servers, or retention. It can't pair or control devices, manage folder grants, start chat threads, or read the owner token.
- It can't create, edit, run, queue, resume, stop or delete workflows, change their triggers, or create, start or control swarms. Editing a workflow and then running it would let it run any command it wrote, with your account.
- It can't change Second Brain libraries.

Reading workflows, runs, triggers, swarms, tickets, organizations and other state is allowed, except Rem's context and Rem's MCP server list (`/api/rem-resources`), whose addresses and arguments can hold keys. Beyond reads, an agent token can only:

- Check a workflow without saving it: validate a draft, analyze a document, preview a bundle import or a trigger.
- Read a ticket's timeline and search or read Second Brain.
- Use Organizations as Rem would. Changes that need your consent become consent requests you approve in the Organizations tab.

Any other change, including endpoints added in later versions, is refused with HTTP 403 (`agent_token_forbidden`) until Raticode allows it for agent tokens on purpose.

## What Rem's workers can reach

Raticode never gives either kind of token to Rem's workers, providers or workflow commands. Everything the backend starts runs with `RATICODE_AGENT_SESSION=1`, and without `RATICODE_TOKEN` or `RATICODE_TOKEN_FILE`, even if you exported them before starting Raticode. Inside such a session the CLI doesn't pick up the owner token from the data folder and refuses any token that isn't an agent token, `Raticode api` refuses requests an agent token couldn't make, and `Raticode tokens` refuses to create or revoke agent tokens. A worker you deliberately give an agent token file can use it with `--token-file`.

This isn't a sandbox. A worker runs as your account, so it could read any file your account can, including `owner-token`. Keep agent token files out of the projects Rem works in. To keep workers away from the owner token completely, run the backend under its own account.

## In a container

The backend only listens on loopback, inside the container. Publishing its port with `-p` doesn't work, so run your scripts in the same container with `docker exec`. The backend writes its address and token into the data folder, and the CLI in the same container finds them there.

This recipe uses the standalone Linux CLI from a release. It needs Raticode 0.5.3 or later, which adds `serve --headless`; it hasn't been tried with a published build yet. Put `raticode-cli-linux-x64` next to this `Dockerfile`:

```dockerfile
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
# Install the provider CLIs your workflows and Rem use here, such as Codex or Claude Code.
ADD raticode-cli-linux-x64 /usr/local/bin/Raticode
RUN chmod 755 /usr/local/bin/Raticode \
 && useradd --create-home raticode \
 && mkdir /data && chown raticode /data
USER raticode
ENV GOFER_DATA_DIR=/data
VOLUME ["/data", "/home/raticode"]
HEALTHCHECK --interval=30s --timeout=10s \
  CMD Raticode api GET /health --timeout 5s > /dev/null || exit 1
CMD ["Raticode", "serve", "--headless"]
```

Build it, start it, and use it:

```bash
docker build -t raticode-headless .
docker run -d --init --name raticode \
  -v raticode-data:/data -v raticode-home:/home/raticode \
  -v /srv/repos:/srv/repos \
  raticode-headless
docker exec raticode Raticode api GET /health
docker exec raticode Raticode doctor
```

- `docker stop` sends the backend SIGTERM, and it shuts down and removes its token files. `--init` adds a small init process that also cleans up the provider processes the backend starts.
- `/data` holds the data folder: workflows, runs, Rem, tickets and the `headless/` files. `/home/raticode` keeps provider CLI sign-ins across container rebuilds.
- Sign each provider CLI in once from a shell in the container, `docker exec -it raticode bash`, following that CLI's own instructions.
- Write [Rem's settings](#give-rem-its-settings) to `/data/headless/rem-settings.json`, listing the project folders you mounted, such as `/srv/repos/api`. Then restart the container.
- To turn on [Organizations](#turn-on-organizations), add `-e RATICODE_EXPERIMENTAL_ORGANIZATIONS=1` to `docker run`.
- To use your own owner token, mount a file owned by the container's `raticode` user with mode 600 and add `--token-file /run/secrets/raticode-token` to the command. Scripts in the container then need the same `--token-file`.
- For scripts that shouldn't act as you, create an [agent token](#agent-tokens) with `docker exec raticode Raticode tokens create ci --output /data/ci.token` and pass it with `--token-file`.

Self-work and running organizations spend provider quota with nobody watching, so leave them off unless you mean to use them.
