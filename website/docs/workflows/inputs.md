---
title: Inputs, outputs and bindings
description: Pass typed values between nodes and expose a reusable workflow interface.
pageClass: docs-layout
---

# Inputs, outputs and bindings

Give a workflow a typed interface instead of pasting values into prompts by hand.

## Declare an input

Each workflow input has a schema and can be required. Each public output names its source and schema:

```rattish
Rattish: 1

Workflow:
  name: Save a message
  interface-version: 1
  inputs:
    message:
      schema: {"type": "string"}
      required: true
  outputs:
    saved-path:
      from: node.save.output.path
      schema: {"type": "string"}

Node save:
  type: write-file
  path: reports/message.txt
  with:
    stdin: input.message
  finish: pass
```

The `stdin` port replaces the writer's authored content. Pass the input as JSON when you run it:

```bash
Raticode rattish run WORKFLOW_PATH --input 'message="Hello from an input"' --format json
```

- The value after `=` is JSON, so a string needs JSON quotes inside the shell argument.
- Repeat `--input` for more values.
- Invalid inputs fail before anything runs.
- Each run saves its input values in its run folder so you can [resume it or run it again](../studio/runs.md#saved-inputs-and-trigger-events) (from 0.5.5). An input whose schema says `"format": "password"` is never saved; you enter it again when you resume or run again.

## File inputs

*New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*

An input whose schema is `{"type": "string", "format": "file"}` takes a file. Two optional fields set what it takes:

```rattish
Workflow:
  name: Summarize a document
  inputs:
    document:
      schema: {"type": "string", "format": "file", "title": "Document"}
      required: true
      accept: [".pdf", ".docx", "text/*"]
      max-size: 5 MB

Node summarize:
  type: bash-command
  command: wc -w "$DOCUMENT"
  with:
    document: input.document
  finish: pass
```

- `accept` lists file name extensions, such as `.pdf`, and MIME patterns, such as `image/png` or `image/*`. A file fits when it matches any one of them. Leave it out to take any file.
- `max-size` is a number of bytes or a size such as `500 KB` or `5 MB`, up to `1 GB`. It defaults to 20 MB, the same limit as a file you [attach to Rem's chat](../rem/attachments.md).
- A file input can't have a `default`, and `format: "file"` works only on the input's own schema, not inside a list or object. Make the input optional (`required: false`) when the workflow can run without one.

When a run starts, Raticode checks that the file exists and fits, then copies it into the run's folder, beside the run's record, as `RUN_ID.files/INPUT/FILE_NAME`. Only your user account can read the copy and its folders. The input's value is the copy's absolute path, so a step reads it as `$DOCUMENT` in a command, or `{{document}}` in a prompt. Later changes to the original file can't change the run, and agent steps in the [sandbox](../nodes/agent.md) can read the copy.

- **Resume**, **Run again** and **Run from here** use the earlier run's copy, not the original. Each run gets its own copy, so deleting one run never removes another's file. If the copy is gone, they say so; start a new run and choose the file again.
- The copy is deleted with its run: by the automatic clean-up of old runs, by **Prune**, and when you delete the workflow.
- A [trigger](triggers.md) has nobody to choose a file, so it can't fill a file input. A workflow whose file input is required can't have a trigger, and preflight for a triggered run stops with `RATTISH_PREFLIGHT_FILE_INPUT_UNFILLED`. Make the input optional to add one.
- A [child workflow](../nodes/workflow.md) gets the path its caller passes, and doesn't make its own copy.

To give the file:

- **In the Studio:** the [Run dialog](../studio/runs.md#file-inputs) shows **Choose file** and a drop zone for each file input. In the file tree, right-click a file and choose **Run workflow with this file**.
- **From the CLI:** pass the path as a JSON string, relative to the folder you run the command from. The CLI checks that the file exists and fits before anything starts:

  ```bash
  Raticode rattish run WORKFLOW_PATH --input 'document="reports/q3.pdf"' --format json
  Raticode rattish start WORKFLOW_ID --input 'document=reports/q3.pdf'
  ```

- **On the phone:** share one file to **Run a workflow** in any app's share sheet. See [Share to a workflow](../mobile/workspace.md#share-to-a-workflow).

## Fill an input from a shortcut

*New in Raticode 0.6.0.*

A [workflow shortcut](../reference/shortcuts.md#run-a-workflow-from-any-app) runs a workflow from any app. When the workflow has a plain text input, the shortcut opens a small Run sheet filled from the clipboard instead of running at once:

- A plain text input has `"type": "string"` with no `enum` and no `"format": "password"`.
- The clipboard goes to the first required plain text input, or to the first one when none is required. When there are several, **Add shortcut…** asks which one gets it.
- Every other input uses its default. A workflow with another required input that has no default, such as a number, a choice or a password, can't have a shortcut.
- The sheet warns that copied text can contain instructions you didn't write, as the phone's [Share to a workflow](../mobile/workspace.md#share-to-a-workflow) does. Read and edit it before you press Enter.
- The text is checked against the input's schema like any run's input, and the run saves it with its other inputs. A run takes up to 100,000 characters; when the clipboard holds more, the sheet keeps the start and says so.

## Bind another node's result

A `with` block binds a reference or a literal to a local name:

```text
with:
  report: node.collect.output.stdout
```

- Use the name in a prompt as `{{report}}`.
- <span v-pre>The value goes into the text as it is, even when it contains `{{` or `}}`, such as an HTML page or another template. Raticode never fills placeholders inside a value, so a value of `{{other}}` stays that text. To write literal braces in your own prompt, type `{{{{` and `}}}}`.</span>
- If the name matches one of the node's input ports, the value also goes to that port.
- Command and script nodes get other bindings as uppercase environment variables: `build-mode` becomes `BUILD_MODE`. Structured values arrive as compact JSON.
- Output selectors use the schema's exact field names. Those can contain underscores, even though Rattish fields use hyphens.

## Missing values and defaults

A branch can reach a consumer before the producer succeeds, and an allowed failure produces no output. For an optional reference, give a compatible default in an expanded binding, or change the readiness conditions:

```text
with:
  report:
    from: node.collect.output.stdout
    default: No report was available.
```

The compiler checks the binding's type and whether its source can be available. At run time, the consumer gets the latest successful value from its own activation lineage at the moment it starts.

## Secrets

Reference a secret by name with exact brackets, such as `secret["SERVICE_TOKEN"]`, in a compatible port.

::: info New in 0.5.5
Saving workflow secrets in the system keychain, and reading them from a headless secrets folder, are new in Raticode 0.5.5. In 0.5.4 and earlier, `secret["NAME"]` reads only the environment variable with that name.
:::

At run time a reference checks three places, in order:

1. A value saved in Raticode, which lives in your system keychain.
2. On a [headless backend](../reference/headless.md#secrets-without-a-keychain) with a secrets folder, the read-only file `workflow-secret-NAME` in that folder. The backend reads it when the keychain has no saved value or can't give it, such as on a server with no keychain.
3. The environment variable with the same name, so workflows that already read secrets from the environment keep working.

A saved value wins over the folder file, and both win over an environment variable with the same name.

### Save a secret

- **In the Studio:** open the workflow, choose **Workflow settings** from the graph's **More** menu, and scroll to **Secrets**. **This workflow uses** lists each name the workflow references as **Saved**, **Secrets folder**, **From environment** or **Missing**; **Save value** next to one stores it. **Add secret** saves any other name, and each saved name has **Replace** and **Remove**. On a headless backend, **From the secrets folder** lists the folder's workflow secrets; they're read-only there.
- **From the CLI:** `Raticode rattish secrets set SERVICE_TOKEN` asks for the value without showing it. `--stdin` reads it from standard input instead, which also keeps line breaks, such as in a private key. `Raticode rattish secrets list` shows saved names and `Raticode rattish secrets remove SERVICE_TOKEN` deletes one.

Saved secrets belong to this computer and its Raticode data folder, and every workflow you run there can read them. Names start with a letter or underscore and use letters, digits, `_`, `.` and `-`. A value can be up to 16 KiB.

Raticode never shows a saved value again: not in the Studio, the CLI or the phone. To change one, save it again. Saving and removing only work from your own terminal or the app; inside a Rem or worker turn the CLI refuses.

### On a server without a keychain

Saving needs the system keychain, the same as the [Jev](./jev.md#on-a-server-without-a-keychain) key. On a server or in a container without one, **Save secret** and `Raticode rattish secrets set` fail with a message that names `--secrets-dir`. Give the [headless backend](../reference/headless.md#secrets-without-a-keychain) a secrets folder instead and put each value in its own file:

- Name the file `workflow-secret-` followed by the secret's name, exactly as the workflow writes it: `secret["SERVICE_TOKEN"]` reads `workflow-secret-SERVICE_TOKEN`.
- The file must belong to the account that runs the backend and be mode 600 or 400. A symlink, a file other accounts can read, an empty file or one over 64 KiB is refused: preflight stops the run with a message that names the file, never the value, and the environment variable isn't used in its place.
- Raticode only reads the file, and reads it again at each run, so replacing it takes effect without a restart. Spaces and line breaks at the start and end of the file are dropped; line breaks inside the value, such as in a private key, are kept.
- Only the backend reads the folder. `Raticode rattish run` runs inside the CLI process, which doesn't; start the run with `Raticode rattish start`.
- `Raticode doctor` and the headless log say whether workflow secrets come from the secrets folder.

With systemd, encrypt the value for your user and load it in a user service. systemd decrypts it into `$CREDENTIALS_DIRECTORY`, which the backend reads with no option (systemd 256 or later):

```bash
mkdir -p ~/.config/raticode/credentials
systemd-ask-password -n "SERVICE_TOKEN:" \
  | systemd-creds --user encrypt --name=workflow-secret-SERVICE_TOKEN - \
      ~/.config/raticode/credentials/workflow-secret-SERVICE_TOKEN.cred
```

```ini
# ~/.config/systemd/user/raticode.service
[Service]
ExecStart=/usr/local/bin/Raticode serve --headless
LoadCredentialEncrypted=workflow-secret-SERVICE_TOKEN:%h/.config/raticode/credentials/workflow-secret-SERVICE_TOKEN.cred
```

With Docker Compose, mount it as a secret. Compose keeps the host file's owner and mode, so make the file belong to the container's user (usually ID 1000) with mode 600:

```yaml
services:
  raticode:
    image: raticode-headless
    command: ["Raticode", "serve", "--headless", "--secrets-dir", "/run/secrets"]
    secrets:
      - workflow-secret-SERVICE_TOKEN
secrets:
  workflow-secret-SERVICE_TOKEN:
    file: ./secrets/workflow-secret-SERVICE_TOKEN
```

The [headless backend page](../reference/headless.md#with-systemd-credentials) has both recipes in full, including how to create the file and why a system service with `User=` doesn't work.

### When a secret is missing

Preflight stops the run with `RATTISH_PREFLIGHT_SECRET_UNAVAILABLE`. The message names the secret and says how to add it; on a headless backend it also names the `workflow-secret-NAME` file. When a source can't be read, the diagnostic's `details.source` says which one: `keychain` or `secrets folder`. If the secret is saved but the keychain is locked, the message says that instead; unlock the keychain (on Linux, the Secret Service such as GNOME Keyring or KWallet) and run again. A binding with a `default` uses the default when the secret is neither saved nor set, but not when a saved secret can't be read.

### What Raticode keeps out of records

- Compilation records the reference, never the credential.
- Secrets can't be route predicates or public workflow outputs.
- A node's output and errors show `[REDACTED]` wherever a secret's value appears, and run records keep only the redacted copy.
- The keychain holds the values. A small `workflow-secrets.json` in the data folder lists only the saved names and when they were saved. Raticode never writes a value to the secrets folder. See [Permissions and local data](../advanced/security.md#credentials).

## Reuse a child workflow

A `workflow` node calls another workflow through its public inputs and outputs. The child's internal node results stay private. The compiler reads the child's interface and freezes the selected version. See [Workflow nodes](../nodes/workflow.md) for the fields.
