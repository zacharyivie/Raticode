---
title: Attachments and dictation
description: Share images and files with Rem and transcribe speech into the composer.
pageClass: docs-layout
---

# Attachments and dictation

Add evidence to a request with files, screenshots, pasted text, or locally transcribed speech.

## Attach files

Use the paperclip, paste a file, or drop it into the composer. Review the attachment labels before sending. Large pasted text of at least 16 KiB becomes a text attachment instead of filling the message field.

Chat attachments allow up to five files, 20 MB per file, and 40 MB total. Sending uploads them into the thread's attachment storage. The provider receives supported attachment content or a local reference appropriate to its adapter. Deleting a thread removes its working attachment files; a configured archive keeps its copied evidence.

## Images depend on the provider

Codex, Claude Code, Cursor, Copilot, OpenCode, and Grok have image paths in the current chat adapters. A Copilot model can advertise that it does not accept vision input. Antigravity's command line accepts only text, so a thread on Antigravity refuses an image and says why.

Unsupported images are excluded with a warning. Other supported files in the same drop remain. Check the actual selected model's capabilities before using an image as the only description of a bug.

## Images in Rem's chat

Rem's own replies in the Rem chat run without tools, so a screenshot helps only when it reaches the model itself. When **Settings > Rem** uses Claude Code, Codex, Cursor, Copilot or OpenCode for the chat, each PNG, JPEG, GIF or WebP image in your message goes to the model with that message. This works with any model the provider offers, for example Sonnet 5.5, Haiku 5.5 and Opus 5.5 in Claude Code. Images from earlier messages aren't sent again, so attach the screenshot again if a later question is about it. This applies to images sent from the phone too.

When Rem doesn't get an image, its reply says why:

- **Grok** and **Antigravity** can't receive images in Rem's chat. Grok's tool-free mode takes images only inside one command-line argument, which is too small for Rem's request. Antigravity's command line accepts only text. Grok still sees images in threads.
- The image is larger than 5 MB.
- The file isn't a PNG, JPEG, GIF or WebP image, for example HEIC or SVG.
- The file is no longer available.

Tasks Rem starts get the attachment's path, and the worker opens it with its own file tools.

Attachment contents are reference material. A document containing instructions is not automatically a request to follow them. State the intended action in your own message.

## Dictate a message

Select the microphone in **Settings > Devices** and check its signal level. Use the composer's microphone control, or Ctrl+Alt+V (Cmd+Alt+V on macOS), to transcribe speech into editable text. **Dictation** in **Settings > Devices** chooses **Press to start and stop** or **Hold to talk**, and **Send after dictation** sends the text as soon as you stop. Review names, code, and commands before sending the result.

Speech is recognized on this computer. On macOS and Windows, **Settings > Devices > Speech recognition** chooses the recognizer built into the operating system (the default) or **Raticode offline model**; Linux always uses the offline model.

The offline Vosk English model (about 130 MB) is downloaded once with **Download offline model** in **Settings > Devices > Speech recognition**, which shows the download's progress and, if it fails, why. Raticode checks the download against a fixed checksum before installing it. Dictation doesn't start the download itself: until the model is installed, dictation that needs it says so and points to that button. On macOS and Windows the offline model also takes over when the built-in recognizer stops partway through a dictation, so downloading it is worthwhile even if you use the built-in one.

On macOS, the download trusts the certificates in the Mac's keychains first, including a company root certificate that an administrator installed in the System keychain, and then the certificates shipped with Raticode. **Test speech recognition** starts macOS speech recognition once without recording, so macOS asks for its permission before your first dictation, and reports whether it works.

**Settings > Rem > Tidy voice messages** is off by default. When on, the heard text goes to a fast model from your chosen provider, which fixes punctuation, filler words and misheard names before you see it. Sending the result uses the chosen AI provider either way.

## Troubleshoot input

- If recording cannot start, check OS microphone permission and the selected device.
- If dictation says the offline model isn't downloaded, use **Download offline model** in **Settings > Devices**. If the download fails, the message says whether the certificate, the connection, a timeout, the server or the disk was the cause.
- On macOS, if Raticode is allowed in **System Settings > Privacy & Security > Microphone** and **Speech Recognition** but dictation still fails, use **Test speech recognition**, then check **Settings > Developer** for lines starting "macOS speech helper", "Dictation: macOS microphone access" and "macOS speech recognition". These lines never include audio or your words.
- macOS ties microphone and speech recognition permission to the app's signature. Releases up to 0.5.2 are unsigned, so an update can make macOS forget them. Signed releases (0.5.3 and later) keep the same Developer ID signature, so the permissions should carry over to later updates. Moving from an unsigned copy to the first signed one, macOS asks again. If it doesn't ask, or the switches are on but dictation still fails, run `tccutil reset Microphone com.goferflow.desktop` and `tccutil reset SpeechRecognition com.goferflow.desktop` in Terminal, restart Raticode, and use **Test speech recognition**.
- If an attachment is rejected, check its size and the selected provider/model.
- If the request needs exact text from an image, include a text description as well.

See [Chat and threads](./chat.md), [Conversation archive](../knowledge/archive.md), and [Troubleshooting](../reference/troubleshooting.md).

