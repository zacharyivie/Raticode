---
title: Attachments and dictation
description: Share images and files with Rem and transcribe speech into the composer.
pageClass: docs-layout
---

# Attachments and dictation

Add files, screenshots, pasted text or dictated speech to a request.

## Attach files

Use the paperclip, paste a file, or drop it into the composer. Check the attachment labels before sending.

| Limit | Value |
| --- | --- |
| Files per message | 5 |
| Size per file | 20 MB |
| Total per message | 40 MB |
| Pasted text that becomes an attachment | 16 KiB or more |

- Sending uploads files into the thread's attachment storage. The provider gets the content, or a local path, depending on its adapter.
- Deleting a thread removes its attachment files. A configured [archive](../knowledge/archive.md) keeps its own copies.
- Attachments are reference material. A document with instructions in it isn't a request to follow them; say what you want in your own message.

## Images in threads

Codex, Claude Code, Cursor, Copilot, OpenCode and Grok can receive images in threads.

- A Copilot model can say it doesn't accept images.
- Antigravity's command line takes text only, so a thread on Antigravity refuses an image and says why.
- An unsupported image is left out with a warning; other files in the same drop still go.

Check the selected model's abilities before relying on a screenshot alone to describe a bug.

## Images in Rem's chat

Rem's own chat replies run without tools, so a screenshot helps only if it reaches the model. With Claude Code, Codex, Cursor, Copilot or OpenCode as the chat provider in **Settings > Rem**, each PNG, JPEG, GIF or WebP image goes to the model with your message.

- This works with any model the provider offers, for example Sonnet 5.5, Haiku 5.5 and Opus 5.5 in Claude Code.
- Images from earlier messages aren't sent again. Attach the screenshot again if a later question is about it.
- Images sent from the phone work the same way.
- Tasks Rem starts get the attachment's path, and the worker opens it with its own file tools.

When Rem doesn't get an image, its reply says why:

- **Grok** and **Antigravity** can't receive images in Rem's chat. Grok's tool-free mode takes images only inside one command-line argument, which is too small for Rem's request. Grok still sees images in threads.
- The image is larger than 5 MB.
- The file isn't PNG, JPEG, GIF or WebP (for example HEIC or SVG).
- The file is no longer available.

## Web images in replies

A reply can show a picture from the web. Raticode fetches it for you, so the site never sees your computer's address.

- Replies from a turn that searched or read web pages show their pictures right away.
- Anywhere else, including Rem's tool-free chat, a picture shows as **Show image · loads from** *host*. Click it to load the picture.

The click is there because the picture's address can carry text. A reply steered by a hostile page or file could hide your data in an image address and send it out just by being shown. Markdown files you open in the File explorer still show their pictures right away.

## Dictate a message

1. Pick the microphone in **Settings > Devices** and check its signal level.
2. Use the composer's microphone button, or Ctrl+Alt+V (Cmd+Alt+V on macOS).
3. Check names, code and commands in the text before sending.

In **Settings > Devices**:

- **Dictation** chooses **Press to start and stop** or **Hold to talk**.
- **Send after dictation** sends the text as soon as you stop.

**Settings > Rem > Tidy voice messages** (off by default) sends the heard text to a fast model from your chosen provider. It fixes punctuation, filler words and misheard names before you see it. Sending the message uses your AI provider either way.

With tidying on, the desktop tidies while you talk: at each pause, or every 15 words or so. Each round sends only the words said since the last round, and adds its text to the end of the message, so text that was already tidied stays as it is. If a round fails, its words go with the next one. When you stop, any words that still couldn't be tidied are added as heard, with a **Not tidied** note, and aren't sent automatically.

### Speech recognition

Speech is recognized on this computer.

| System | Recognizer |
| --- | --- |
| macOS, Windows | The one built into the system (default), or **Raticode offline model**. Pick in **Settings > Devices > Speech recognition**. |
| Linux | Always the offline model |

The offline model is Vosk English, about 130 MB.

- Download it once with **Download offline model** in **Settings > Devices > Speech recognition**. It shows progress and, if it fails, why.
- Raticode checks the download against a fixed checksum before installing it.
- Dictation never starts the download. Until the model is installed, dictation that needs it says so and points to the button.
- On macOS and Windows the offline model takes over when the built-in recognizer stops partway, so it's worth downloading even if you use the built-in one.

On macOS:

- The download trusts the Mac's keychains first, including a company root certificate an administrator added to the System keychain, then the certificates shipped with Raticode.
- **Test speech recognition** starts macOS speech recognition once without recording. macOS asks for permission before your first dictation, and the button reports whether it works.

## Troubleshoot input

- **Recording won't start.** Check the system's microphone permission and the selected device.
- **"Offline model isn't downloaded".** Use **Download offline model** in **Settings > Devices**. If the download fails, the message names the cause: certificate, connection, timeout, server or disk.
- **macOS: dictation fails although Raticode is allowed** in **System Settings > Privacy & Security > Microphone** and **Speech Recognition**. Use **Test speech recognition**, then look in **Settings > Developer** for lines starting "macOS speech helper", "Dictation: macOS microphone access" and "macOS speech recognition". These lines never include audio or your words.
- **An attachment is rejected.** Check its size and the selected provider and model.
- **Rem needs exact text from an image.** Include the text in your message too.

### macOS permissions after an update

macOS ties microphone and speech permissions to the app's signature.

- Releases up to 0.5.2 are unsigned, so an update can make macOS forget them.
- Signed releases (0.5.3 and later) keep the same Developer ID signature, so permissions should carry over.
- Moving from an unsigned copy to the first signed one, macOS asks again.

If it doesn't ask, or dictation still fails, run these in Terminal, restart Raticode and use **Test speech recognition**:

```bash
tccutil reset Microphone com.goferflow.desktop
tccutil reset SpeechRecognition com.goferflow.desktop
```

See [Chat and threads](./chat.md), [Conversation archive](../knowledge/archive.md), and [Troubleshooting](../reference/troubleshooting.md).
