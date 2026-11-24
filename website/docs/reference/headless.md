---
title: Headless backend
description: Run the Raticode backend on a server or in a container with no desktop window, and give scripts and agents limited tokens.
pageClass: docs-layout
---

# Headless backend

`Raticode serve --headless` runs the Raticode backend with no desktop window, on servers, remote boxes and containers. Rem, tickets, Organizations, workflows and triggers keep working. Scripts on the same computer reach the backend's API with a token file.

::: info New in 0.5.3
Headless mode arrived in Raticode 0.5.3. Earlier versions have only `Raticode ui serve`, which needs the desktop window for Rem.
:::

## Start it

```bash
Raticode serve --headless
```

- It listens on `127.0.0.1:8765`. `--port 0` lets the system pick a free port.
- `--data-dir PATH` (or the `RATICODE_DATA_DIR` or `GOFER_DATA_DIR` environment variable) uses a different data folder. Every part of the backend uses it, and `Raticode tokens` and the CLI's backend commands look in the same folder.
- It listens only on this computer, over TCP. It refuses `--host 0.0.0.0` or any other non-loopback address, and it can't listen on a Unix socket.
- To reach it from another machine, forward the port over SSH, for example `ssh -L 8765:127.0.0.1:8765 box`. Raticode has no remote listener or TLS yet.
- `Raticode serve` without `--headless` is the same as `Raticode ui serve`, the backend the desktop app starts.

## Find the endpoint and token

At startup, headless mode writes two files in `<data>/headless/`. On Linux and macOS only your account can read the folder (mode 700) and the files (mode 600):

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

- The backend removes both files when it stops.
- The token never goes on a command line, where other accounts could see it.
- To use your own token, such as a mounted container secret, pass `--token-file PATH`. The file must belong to you and be mode 600. The `GOFER_UI_API_TOKEN` environment variable also works. Either way, Raticode doesn't write `owner-token`.

::: warning Windows
On Windows, Raticode doesn't set or check permissions on these files, on token files you pass with `--token-file`, or on agent token files. They get whatever access their folder allows. The default data folder is in your user profile, which other standard accounts can't normally read but an administrator can. Keep custom data folders and token files in your profile too.
:::

## Give Rem its settings

The desktop window normally tells Rem which projects, provider and resources to use. Until it does, Rem's tasks wait with "Waiting for Raticode to connect". In headless mode Rem reads them from `<data>/headless/rem-settings.json`, or from the file named with `--rem-settings PATH`:

```json
{
  "provider": "codex",
  "model": "cli-default",
  "projects": ["/srv/repos/api", { "root": "/srv/repos/web", "name": "Web" }],
  "brain": { "enabled": true, "root": "/srv/second-brain" },
  "swarm_access": false
}
```

- The file takes the same fields the desktop sends: `provider`, `model`, `effort`, `chat_provider`, `chat_model`, `chat_effort`, `projects`, `resources`, `brain`, `report_theme`, `conversation_archive_root`, `browser_target`, `swarm_access`, `command_slots` and `kanban_board`.
- Project and Second Brain folders must be existing absolute paths.
- Rem gets access to every folder the file lists, so the file must belong to you and must not be writable by other accounts.
- Without the file, the backend still starts and queued Rem tasks say they're waiting for it.
- Restart the backend after changing the file.

[`Raticode rem settings export --file rem.json`](./cli-rem.md#rem-s-settings) writes this format from a running backend. `Raticode rem settings apply` changes the settings without a restart, but only for folders the backend can already reach. To add a project folder, add it to the settings file and restart.

## Secrets without a keychain

::: info New in 0.5.5
The secrets folder is new in Raticode 0.5.5.
:::

Some features keep their secrets in the system keychain: the [Jev](../workflows/jev.md) key, Organizations secrets and webhook signing secrets, usage reporting credentials, and the [workflow secrets](../workflows/inputs.md#secrets) `secret["NAME"]` reads. Servers and containers often have no keychain. A headless backend can read these secrets from a folder of files instead:

```bash
Raticode serve --headless --secrets-dir ~/.config/raticode/secrets
```

- Without `--secrets-dir`, the backend uses `$CREDENTIALS_DIRECTORY` when systemd sets it. That's where `LoadCredentialEncrypted=` puts decrypted credentials, so a [systemd service](#with-systemd-credentials) needs no option.
- Each file holds one secret. It must belong to the account that runs the backend and be mode 600 (or 400), the same rule as `--token-file`. A symlink, or a file other accounts can read, is refused with a message that names the file and never the value.
- The folder must belong to that account or to root, and other accounts must not be able to add files to it. Otherwise the backend doesn't start.
- The keychain comes first. A file is used only when the keychain can't give that secret.
- Raticode only reads the folder. It never writes a secret there, or anywhere else, in plain text. It never reads a secret from an environment variable or a command-line argument: `$CREDENTIALS_DIRECTORY` names only the folder, and the backend removes it from the environment of the providers, workers and commands it starts.
- Saving still needs the keychain. **Save** in Settings, `Raticode provider jev set-key`, **Save secret** in Workflow settings, `Raticode rattish secrets set` and storing an Organizations credential fail with a message that names `--secrets-dir`. To change a secret, replace its file. Raticode reads the file again each time it needs the secret, so no restart is needed.

| File | Secret |
| --- | --- |
| `jev-key` | The Jev API key |
| `org-secret-ACCOUNT` | An Organizations secret or webhook signing secret. `ACCOUNT` is the secret reference's account, with each `/` written as `.` |
| `usage-PROVIDER-FIELD` | A usage reporting credential, such as `usage-openai_api-admin_api_key`. For a provider profile, `usage-PROVIDER-PROFILE-FIELD` |
| `workflow-secret-NAME` | The value `secret["NAME"]` reads in a workflow, such as `workflow-secret-SERVICE_TOKEN`. `NAME` is the secret's name exactly as the workflow writes it |

Usage reporting reads these fields: `claude_code` `oauth_token`; `cursor` `admin_api_key` and `email`; `openai_api` `admin_api_key` and `project_id`; `anthropic_api` `admin_api_key` and `workspace_id`.

An Organizations secret reference names an account. On a server, add a reference whose account is your file's name without `org-secret-`:

```bash
Raticode api POST /organizations -f action=secret_reference -f organizationId=ORG_ID \
  -f params.id=github -f params.account=github-token
```

The backend then reads `org-secret-github-token` wherever an employee's grant uses `secret://github`, or a webhook uses the `github` reference.

A workflow secret needs no setup beyond the file. `secret["SERVICE_TOKEN"]` checks a value saved in the keychain first, then `workflow-secret-SERVICE_TOKEN` in the folder, then the `SERVICE_TOKEN` environment variable. **Workflow settings > Secrets** marks a name the folder provides as **Secrets folder** and lists the folder's workflow secrets, read-only. A file that breaks the rules above stops the run at preflight with a message naming the file; the environment variable isn't used in its place. See [Secrets](../workflows/inputs.md#secrets).

### What's unavailable

At startup, the headless log says where each secret-backed feature gets its secrets and warns about each one that can't work, such as `Unavailable: Usage reporting credentials. No system keychain. Add usage-PROVIDER-FIELD to the secrets folder ...`. `Raticode doctor` lists the same, without network calls:

```bash
Raticode doctor
Raticode doctor --secrets-dir ~/.config/raticode/secrets
```

- `doctor` checks `--secrets-dir`, then `$CREDENTIALS_DIRECTORY`, then the folder of the running headless backend.
- systemd mounts a service's credentials privately, so `doctor` in your shell may not be able to check them. It says so, and `Raticode api GET /doctor` shows the running backend's own report.
- Phone pairing has no secrets folder fallback. It needs the keychain for the computer's device identity.
- The folder belongs to the backend. `Raticode rattish run` runs a workflow inside the CLI process, which doesn't read it, so a jev node there can't use the folder's key and `secret["NAME"]` there can't read `workflow-secret-NAME`. Start runs through the backend with `Raticode rattish start`.

::: warning Same account, same access
Self-work and reviews that use Claude Code run in [Claude Code's sandbox](../rem/board.md#claude-code-s-sandbox), which hides the secrets folder and `$CREDENTIALS_DIRECTORY` from their shell commands and file tools. From 0.6.0 every agent run hides it too: Claude Code's file tools in any turn, Codex through a permission profile that denies the folder, and, through bubblewrap on Linux, Claude Code turns you watch, Codex with full access, and Cursor, Copilot, OpenCode, Antigravity and Grok. See [Credential folders no agent can reach](../advanced/security.md#credential-folders-no-agent-can-reach).

What still runs as the backend's account and can read these files: workflow `bash` and `python` steps, and, when bubblewrap isn't installed or the server isn't Linux, Claude Code turns you watch, Claude Code steps with `sandbox: off`, Codex with full access or with `sandbox_mode` in its config, and the providers without a sandbox. Removing `$CREDENTIALS_DIRECTORY` from their environment doesn't stop them, because the folder's usual places, such as `/run/credentials/<unit>/`, are easy to guess. Running the backend under its own account, as with `owner-token`, keeps other programs of yours away from the files, but not these: the backend starts them, so they run as its account. Install bubblewrap, and put in the folder only secrets your own workflow commands may read.
:::

### With systemd credentials

This recipe runs the backend as a systemd user service, so the decrypted files belong to your account. It needs systemd 256 or later, and hasn't been tried with a published build yet.

Encrypt each secret for your user. `systemd-ask-password` asks without showing the value, and the value never goes on a command line:

```bash
mkdir -p ~/.config/raticode/credentials
systemd-ask-password -n "Jev key:" \
  | systemd-creds --user encrypt --name=jev-key - ~/.config/raticode/credentials/jev-key.cred
systemd-ask-password -n "GitHub token:" \
  | systemd-creds --user encrypt --name=org-secret-github-token - \
      ~/.config/raticode/credentials/org-secret-github-token.cred
systemd-ask-password -n "SERVICE_TOKEN:" \
  | systemd-creds --user encrypt --name=workflow-secret-SERVICE_TOKEN - \
      ~/.config/raticode/credentials/workflow-secret-SERVICE_TOKEN.cred
```

Write `~/.config/systemd/user/raticode.service`:

```ini
[Unit]
Description=Raticode headless backend

[Service]
ExecStart=/usr/local/bin/Raticode serve --headless
LoadCredentialEncrypted=jev-key:%h/.config/raticode/credentials/jev-key.cred
LoadCredentialEncrypted=org-secret-github-token:%h/.config/raticode/credentials/org-secret-github-token.cred
LoadCredentialEncrypted=workflow-secret-SERVICE_TOKEN:%h/.config/raticode/credentials/workflow-secret-SERVICE_TOKEN.cred
Restart=on-failure

[Install]
WantedBy=default.target
```

Start it, keep it running after you log out, and read the startup lines:

```bash
systemctl --user daemon-reload
systemctl --user enable --now raticode
loginctl enable-linger "$USER"
journalctl --user -u raticode -b
```

- systemd decrypts each credential into `$CREDENTIALS_DIRECTORY` only for this service, mode 400. The backend finds the folder there with no option.
- Use a user service. A system service with `User=` gets credential files that belong to root, with an access list that lets the service's account read them. They don't belong to the backend's account, so Raticode refuses them.
- After replacing a `.cred` file, restart the service so systemd decrypts it again.

### With Docker secrets

Docker Compose mounts each file secret into `/run/secrets/` as it is on the host. It ignores a file secret's `uid`, `gid` and `mode` settings, so set the owner and mode on the host file. The owner is the numeric ID of the container's user: `docker run --rm raticode-headless id -u` shows it, usually 1000 for the `raticode` user the [container recipe](#in-a-container) creates.

```bash
mkdir -m 700 secrets
(umask 077; read -rsp "Jev key: " key && printf '%s\n' "$key" > secrets/jev-key)
(umask 077; read -rsp "SERVICE_TOKEN: " value && printf '%s\n' "$value" > secrets/workflow-secret-SERVICE_TOKEN)
sudo chown -R 1000:1000 secrets
```

`compose.yaml`:

```yaml
services:
  raticode:
    image: raticode-headless
    init: true
    command: ["Raticode", "serve", "--headless", "--secrets-dir", "/run/secrets"]
    volumes:
      - raticode-data:/data
      - raticode-home:/home/raticode
    secrets:
      - jev-key
      - workflow-secret-SERVICE_TOKEN
secrets:
  jev-key:
    file: ./secrets/jev-key
  workflow-secret-SERVICE_TOKEN:
    file: ./secrets/workflow-secret-SERVICE_TOKEN
volumes:
  raticode-data:
  raticode-home:
```

- With plain `docker run`, mount the folder read-only instead: `-v "$PWD/secrets:/run/secrets:ro"`, and add `--secrets-dir /run/secrets` to the command.
- In a Swarm stack (`docker stack deploy`), Docker does apply `uid`, `gid` and `mode`. Set `uid: "1000"`, `gid: "1000"` and `mode: 0400` on each secret. The default mode, 0444, lets other accounts read the file, so Raticode refuses it.
- `docker exec raticode Raticode doctor` lists what the backend can and can't use.

## Turn on Organizations

Organizations are a Feature Preview. A headless backend doesn't read the desktop app's Settings choice, so start it with the environment variable:

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

- `create` writes the token to a new file only you can read, and never prints it. Raticode stores only a hash.
- Revoking takes effect at once, even on a running backend.
- Agent tokens work with the desktop's backend too.

An agent token acts with Rem's authority, not yours:

- Starting an organization, granting it full access or lifting holds records a consent request that you approve in the Organizations tab. An agent token can't approve it, approve Kanban proposals, store credentials or approve workflow approval gates. It also can't give an organization a folder inside Raticode's data folder.
- It can't send Rem messages, create or change Kanban tickets, or control Rem's tasks and memories, because Rem treats those as your requests. Rem's self-work, for example, starts To-do tickets on its own.
- It can't change Rem's settings, self-work settings, provider settings or sign-ins, Rem's skills and MCP servers, or retention. It can't pair or control devices, manage folder grants, start chat threads, or read the owner token.
- It can't create, edit, run, queue, resume, stop or delete workflows, change their triggers, approve a changed workflow version for a trigger, or create, start or control swarms. Editing a workflow and then running it would let it run any command it wrote, with your account.
- It can't change Second Brain libraries.
- It can't read a Second Brain with **Allow cloud processing** off. Searching one, or listing its projects, topics or report and job sources, gets HTTP 403 with the code `brain_private`, and so does opening, relating or connecting a document stored in one. In `Raticode brain list` such a brain shows its name and the state `private`, with no folder, documents, reports or tasks.

A local script that reads your private brains and keeps what it reads on this computer can get a token that reads them:

```bash
Raticode tokens create notes-index --output ~/notes-index.token --private-brains
```

The choice is stored with the token's hash, and `Raticode tokens list` shows it. Tokens without `--private-brains` stay closed to private brains. The **Settings > Memory** folder counts as private here too while its brain has **Allow cloud processing** off.

It can read workflows, runs, triggers, swarms, tickets, organizations and other state. The exceptions are Rem's context, Rem's MCP server list (`/api/rem-resources`), whose addresses and arguments can hold keys, your provider accounts with their emails, plans and any phone sign-in's link and code (`/api/provider/accounts`), and usage-reporting credentials (`/api/usage/credentials`). Beyond reads, an agent token can only:

- Check a workflow without saving it: validate a draft, analyze a document, preview a bundle import or a trigger.
- Read a ticket's timeline and search or read Second Brains that allow cloud processing.
- Use Organizations as Rem would. Changes that need your consent become consent requests you approve in the Organizations tab.

Any other change, including endpoints added in later versions, is refused with HTTP 403 (`agent_token_forbidden`) until Raticode allows it for agent tokens on purpose.

## What Rem's workers can reach

Raticode never gives either kind of token to Rem's workers, providers or workflow commands. Everything the backend starts runs with `RATICODE_AGENT_SESSION=1` and without `RATICODE_TOKEN`, `RATICODE_TOKEN_FILE` or `CREDENTIALS_DIRECTORY`, even if they were set when Raticode started.

Inside such a session:

- The CLI doesn't pick up the owner token from the data folder, and refuses any token that isn't an agent token.
- `Raticode api` refuses requests an agent token couldn't make.
- `Raticode tokens` refuses to create or revoke agent tokens.
- A worker you deliberately give an agent token file can use it with `--token-file`.

::: warning This isn't a sandbox
A worker runs as your account, so it could read any file your account can, including `owner-token`. Keep agent token files out of the projects Rem works in. To keep workers away from the owner token completely, run the backend under its own account.
:::

## In a container

The backend only listens on loopback inside the container, so publishing its port with `-p` doesn't work. Run your scripts in the same container with `docker exec`. The CLI there finds the address and token the backend writes into the data folder.

This recipe uses the standalone Linux CLI from a release. It needs Raticode 0.5.3 or later, which adds `serve --headless`, and hasn't been tried with a published build yet. Put `raticode-cli-linux-x64` next to this `Dockerfile`:

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
- A container has no keychain. Give the backend the Jev key and other keychain secrets with [Docker secrets](#with-docker-secrets).
- For scripts that shouldn't act as you, create an [agent token](#agent-tokens) with `docker exec raticode Raticode tokens create ci --output /data/ci.token` and pass it with `--token-file`.

Self-work and running organizations spend provider quota with nobody watching, so leave them off unless you mean to use them.
