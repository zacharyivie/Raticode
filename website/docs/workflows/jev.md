---
title: Jev classifier
description: Save a TypeSafe key for Jev, check it, and see what each jev node call sends, costs and records.
pageClass: docs-layout
---

# Jev classifier

::: info New in 0.5.5
Jev is new in Raticode 0.5.5.
:::

Jev is TypeSafe's hosted classifier. It reads some text and answers one question about it: yes or no, or which of several options fits best. In Raticode it powers the [jev node](../nodes/jev.md), so a workflow can branch on what a message, file or command output says.

To try it, copy the [Jev demo](../starters/jev-demo.md) starter with `Raticode rattish create --from jev-demo --project .`. It asks one yes/no and one multiple-choice question about a message and routes on each label and score. Starters are new in 0.5.5.

Jev is a classification provider, not a coding provider. It doesn't write text or run tools, so Rem's model picker, Organizations workers and [agent](../nodes/agent.md) nodes don't list it.

## Save your key

You need a TypeSafe API key. TypeSafe's [API reference](https://docs.typesafe.ai/api) explains how keys work.

1. Open **Settings > Providers** and scroll to **Classification models**.
2. Paste the key into **API key** under **Jev (TypeSafe)** and choose **Save**. The field empties again: Raticode never shows the key after saving it.
3. Choose **Check key**. Raticode makes one call to Jev's model list. **Jev accepted the key** means it works, and the card lists the models Jev names, such as `jev-latest`.

From a terminal, run `Raticode provider jev set-key`. It asks for the key without showing what you type. See [the Jev CLI commands](../reference/cli-headless.md#jev-classifier).

The key is kept in your system keychain: Keychain on macOS, Credential Manager on Windows, and the Secret Service (GNOME Keyring or KWallet) on Linux. On a computer without one, such as a headless Linux server with no keyring, saving fails with a message that names the secrets folder option. See [On a server without a keychain](#on-a-server-without-a-keychain).

- Raticode never reads the key from an environment variable or a command-line argument. A `JEV_API_KEY` you exported for another tool is ignored, and Raticode removes it from the environment of every agent and workflow command it starts.
- Only the part of Raticode that calls Jev reads the key. It isn't passed to agents, scripts or commands, and it never appears in workflow source, compiled workflows, run records, logs, node output or usage records.
- Each Raticode data folder has its own key, so a test data folder doesn't use your main one.

**Disabled** in the card's status menu stops every jev node from calling Jev and keeps the key. **Remove key** deletes it from the keychain.

### On a server without a keychain

::: info New in 0.5.5
Reading the key from a secrets folder is new in Raticode 0.5.5.
:::

A [headless backend](../reference/headless.md#secrets-without-a-keychain) can read the key from a file named `jev-key` in a secrets folder. Raticode only reads that file: it never writes the key there, and saving still needs the keychain.

- The file must belong to the account that runs the backend and be mode 600 or 400, the same rule as `--token-file`.
- A key saved in the keychain is used first. The file is read again at each use, so replacing it takes effect without a restart.
- `Raticode provider jev status` says `Key: read from the secrets folder (jev-key)`. **Disabled** still turns Jev off; **Remove key** can't delete the file.
- The headless log and `Raticode doctor` say whether the key was found.
- Only the backend reads the folder. A jev node in `Raticode rattish run`, which runs inside the CLI process, can't use it; start the run with `Raticode rattish start`.

With systemd, encrypt the key for your user, then load it in a user service. systemd decrypts it into `$CREDENTIALS_DIRECTORY`, which the backend reads with no option (systemd 256 or later):

```bash
mkdir -p ~/.config/raticode/credentials
systemd-ask-password -n "Jev key:" \
  | systemd-creds --user encrypt --name=jev-key - ~/.config/raticode/credentials/jev-key.cred
```

```ini
# ~/.config/systemd/user/raticode.service
[Service]
ExecStart=/usr/local/bin/Raticode serve --headless
LoadCredentialEncrypted=jev-key:%h/.config/raticode/credentials/jev-key.cred
```

With Docker Compose, mount it as a secret. Compose keeps the host file's owner and mode, so make the file belong to the container's user (usually ID 1000) with mode 600:

```yaml
services:
  raticode:
    image: raticode-headless
    command: ["Raticode", "serve", "--headless", "--secrets-dir", "/run/secrets"]
    secrets:
      - jev-key
secrets:
  jev-key:
    file: ./secrets/jev-key
```

The [headless backend page](../reference/headless.md#with-systemd-credentials) has both recipes in full, including why a system service with `User=` doesn't work.

## What leaves your computer

A jev node sends its question, its criteria or options, and its `state` text to `https://api.typesafe.ai/v1/systemone`. Nothing else is sent. The address is fixed: workflow source can't change it, so a workflow can't send your key anywhere else, and Raticode refuses redirects.

TypeSafe says it doesn't train on requests or responses. Its terms and data-retention rules are TypeSafe's, not Raticode's.

Jev reads the state as content, but TypeSafe notes that instructions hidden in that text can still sway an answer. Put your rules in the node's criteria, and don't route anything you can't undo on one Jev answer without an [approval gate](../nodes/approval-gate.md).

## Cost and usage

TypeSafe bills input tokens; output tokens are free. Every jev call, including failed ones, adds a row under **Settings > Usage** in [Raticode activity](../rem/usage.md#raticode-activity) as **Jev (TypeSafe)**, with the input tokens Jev reported. Raticode doesn't show a dollar amount, because Jev reports tokens, not cost.

## Limits Raticode applies

- **One question per call**, with a 15-second limit by default. A node can set `request-timeout` up to 60 seconds.
- **Four calls at a time.** Raticode lets at most four Jev calls run at once in the app, or in one CLI run. Extra calls, such as from a [loop](../nodes/loop.md) with high concurrency, wait their turn.
- **A shared pause after a rate limit.** When Jev says the key is rate limited, every jev call waits out the time Jev names, between 1 and 5 minutes, instead of piling on. Calls during the pause fail right away with the wait time.
- **State size.** A node sends at most 512 KB of state.

## Errors

| What happened | Error code | Retried with `retry-count`? |
| --- | --- | --- |
| No key saved, or Jev is off | `RATTISH_JEV_NOT_CONFIGURED` | No |
| Jev rejected the key | `RATTISH_JEV_AUTH` | No |
| Jev rejected the request (400 or 422) | `RATTISH_JEV_REJECTED` | No |
| The model doesn't exist | `RATTISH_JEV_MODEL_UNAVAILABLE` | No |
| Rate limited (429 or 529) | `RATTISH_JEV_RATE_LIMITED` | Yes, after Jev's wait |
| Jev is unavailable (5xx) | `RATTISH_JEV_UNAVAILABLE` | Yes |
| Jev couldn't be reached | `RATTISH_JEV_NETWORK` | Yes |
| No answer in time | `RATTISH_JEV_TIMEOUT` | Yes |
| The answer was malformed | `RATTISH_JEV_INVALID_RESPONSE` | No |
| You stopped the run | `RATTISH_JEV_CANCELLED` | No |

- A malformed answer, such as a score outside 0 to 1 or an option the node doesn't list, is always an error. It's never treated as "no".
- Error messages never include the key or the text you sent. For a rejected request, they name only the fields Jev complained about.
- `retry-count` skips errors another attempt can't fix, so a wrong key fails once instead of three times.

## Not checked yet

Raticode's tests use a stand-in for Jev. As of this release no test has called the live service, so TypeSafe's real rate limits and response times for your account aren't known to Raticode.
