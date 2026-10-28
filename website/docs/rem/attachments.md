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

Codex, Claude Code, Cursor, Copilot, OpenCode, and Grok have image paths in the current chat adapters. A Copilot model can advertise that it does not accept vision input. Antigravity's headless chat path accepts text only.

Unsupported images are excluded with a warning. Other supported files in the same drop remain. Check the actual selected model's capabilities before using an image as the only description of a bug.

Attachment contents are reference material. A document containing instructions is not automatically a request to follow them. State the intended action in your own message.

## Dictate a message

Select the microphone in **Settings > Devices** and check its signal level. Use the composer's microphone control to transcribe speech into editable text. Review names, code, and commands before sending the result.

Transcription uses a local Vosk recognizer. Its English model is downloaded and verified on first preparation, so first use can require network access and disk space. Once the model is available, speech recognition runs locally. Sending the resulting text still uses the chosen AI provider.

## Troubleshoot input

- If recording cannot start, check OS microphone permission and the selected device.
- If first-time transcription fails, inspect connectivity, storage, and the application log.
- If an attachment is rejected, check its size and the selected provider/model.
- If the request needs exact text from an image, include a text description as well.

See [Chat and threads](./chat.md), [Conversation archive](../knowledge/archive.md), and [Troubleshooting](../reference/troubleshooting.md).

