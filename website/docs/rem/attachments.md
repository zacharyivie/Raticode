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

## Show Rem your screen

::: info New in 0.5.5
Showing Rem your screen is new in Raticode 0.5.5.
:::

Ask Rem about what you're doing in another app, or on another monitor, by giving it one screenshot with your message. It's off until you turn on **Settings > Devices > Show Rem your screen**.

1. In Rem's message box, click the screen button next to the paperclip (**Show Rem your screen**).
2. Pick a screen or a window from the list. On Linux with Wayland, your desktop's own screen-sharing dialog asks instead.
3. The screenshot appears in the message box. Click it to see it full size, or remove it with **×** to send without it. Press **Esc** to close the list without taking anything.
4. Type or dictate your question and send. A screenshot on its own can be sent too.

To ask without switching to Raticode, also turn on [Dictate to Rem from any app](#dictate-from-any-app), then press **Ctrl+Alt+Shift+Space** (Control+Option+Shift+Space on macOS) in any app. Raticode takes a screenshot of the screen your pointer is on (on Wayland, the desktop asks which screen or window), puts it in Rem's message box and starts listening. The listening panel names the screenshot. Change the key under **Ask Rem about your screen** in **Settings > Keybindings**.

What happens to the screenshot:

- It's taken only when you click the button or press the shortcut, for that one message. Raticode never captures your screen in the background, and Rem can't take a screenshot on its own or ask a worker to.
- Until you send, it exists only in Raticode's memory. Removing it, or closing Raticode, discards it.
- A screenshot always waits in the message box for you to check, even with **Send after dictation** on.
- Rem's chat model sees it for that reply only, the same way as an [attached image](#images-in-rem-s-chat). It isn't saved: the message keeps just the screen's or window's name, shown as **Showed Rem** *name*. Later messages, and tasks Rem starts, don't see it. To ask about it again, take another one.
- Raticode doesn't write it to disk or to its logs. With Codex, Cursor, Copilot or OpenCode, the CLI receives it as a private temporary file that's deleted after the reply; Claude Code receives it directly.
- Anything in the screenshot is treated as content, not as your request. Rem answers questions about it, but work it starts, memories it saves and files it sends come only from your own words: if only the screenshot asks for something, Rem says it didn't do it.
- It goes to the AI provider you set for Rem's chat, like any image you attach. Check what's on the screen before sending, especially passwords or private messages.

How each system handles it:

| System | What happens |
| --- | --- |
| Windows, Linux on X11 | Raticode lists your screens and windows. No permission is needed. |
| macOS | The first screenshot makes macOS ask for **Screen & System Audio Recording** permission. Allow Raticode in **System Settings > Privacy & Security > Screen & System Audio Recording**, then quit and reopen Raticode. Until then, Raticode says so and takes nothing. |
| Linux on Wayland | Apps can't see other windows, so Raticode asks the desktop's screen-sharing service (xdg-desktop-portal with PipeWire). Its dialog asks which screen or window every time. Closing it takes nothing. |
| Linux on Wayland, with Raticode running as an X11 app | The Raticode (gecko) browser needs this mode. Raticode can only see other X11 apps, and screens usually come out black. Settings warns about this. |

Screenshots come at most 2560 pixels on the long side, and as JPEG when a PNG would be over Rem's 5 MB image limit. Rem's chat providers that [can't receive images](#images-in-rem-s-chat) (Grok, Antigravity) can't see screenshots either.

## Web images in replies

A reply can show a picture from the web. Raticode fetches it for you, so the site never sees your computer's address.

- Replies from a turn that searched or read web pages show their pictures right away.
- Anywhere else, including Rem's tool-free chat, a picture shows as **Show image · loads from** *host*. Click it to load the picture.

The click is there because the picture's address can carry text. A reply steered by a hostile page or file could hide your data in an image address and send it out just by being shown. Markdown files you open in the File explorer still show their pictures right away.

## Dictate a message

1. Pick the microphone in **Settings > Devices** and check its signal level.
2. Use the composer's microphone button, or Ctrl+Alt+V (Cmd+Alt+V on macOS). To dictate to Rem while you're in another app, see [Dictate from any app](#dictate-from-any-app).
3. Check names, code and commands in the text before sending.

In **Settings > Devices**:

- **Dictation** chooses **Press to start and stop** or **Hold to talk**.
- **Send after dictation** sends the text as soon as you stop.

**Settings > Rem > Tidy voice messages** (off by default) sends the heard text to a fast model from your chosen provider. It fixes punctuation, filler words and misheard names before you see it. Sending the message uses your AI provider either way.

With tidying on, the desktop tidies while you talk: at each pause, or every 15 words or so. Each round sends only the words said since the last round, and adds its text to the end of the message, so text that was already tidied stays as it is. A round often ends partway through a sentence, so its closing full stop waits for the next round: if your next words carry the sentence on, they join it, and if they start a new sentence, the full stop goes in and they start with a capital. When you stop, the last sentence gets its full stop.

If a round fails, its words go with the next one, and a **Not tidied yet** note says why while you keep talking. When you stop, any words that still couldn't be tidied are added as heard, with basic capitals (the start of each new part and the word "I") and a closing full stop, plus a **Not tidied** note. They aren't sent automatically. Raticode's log records how many words each round tidied, which model did it and how long it took, never the words.

### Dictate from any app

::: info New in 0.5.5
Dictating from any app is new in Raticode 0.5.5.
:::

Talk to Rem without switching to Raticode, like the assistant button on a phone. It's off until you turn on **Settings > Devices > Dictate to Rem from any app**.

1. Press **Ctrl+Alt+Space** (Control+Option+Space on macOS) in any app, even with Raticode minimized or closed to the tray. Change the key under **Dictate to Rem from any app** in **Settings > Keybindings**. On a Mac with more than one keyboard input source, Control+Option+Space also switches input source, so pick another key there.
2. A small panel near the bottom of the screen shows **Listening for Rem**. Raticode stays where it was, and the app you're in keeps the keyboard.
3. Press the shortcut again, click **Stop**, or stop talking for about 6 seconds to finish. If you say nothing, it stops after 15 seconds.

Your words go to Rem's message box, never to a thread's. Rem's chat doesn't need to be on screen. **Tidy voice messages** and **Send after dictation** apply as usual: with **Send after dictation** on, the message goes to Rem straight away. Otherwise the panel says your words are waiting in Rem's message box, and **Open** brings Raticode forward.

- The microphone opens only when you press the shortcut, and closes when the dictation ends. The panel shows the whole time it's open.
- The panel and the shortcut never receive your words or audio, and nothing about the dictation is logged.
- The shortcut always works as press to start and stop. A key pressed in another app can't report being let go, so **Hold to talk** applies only inside Raticode.
- If another app already uses the key, Settings says so; pick another one. Keys without Ctrl, Alt or the system key aren't allowed, except F1 to F24, so the shortcut never takes a key you type with.

How each system handles it:

| System | What happens |
| --- | --- |
| Windows, macOS | Raticode registers the key itself. No permission is needed. |
| Linux on X11 | Raticode registers the key itself. |
| Linux on Wayland | Apps can't watch keys, so Raticode asks the desktop's shortcut service (the GlobalShortcuts portal). KDE Plasma and recent GNOME may ask you to confirm the key the first time, and let you change it in their own shortcut settings. Desktops without the portal ignore the request. |
| Linux on Wayland, with Raticode running as an X11 app | The Raticode (gecko) browser needs this mode. Most Wayland desktops pass the key to X11 apps only while one of them is focused. Settings warns about this. |

If pressing the key does nothing, Settings shows when it was last pressed and, on Linux, a command to run from a custom shortcut in your desktop's settings, such as **System Settings > Keyboard > Shortcuts > Add New > Command** on KDE Plasma. The command reaches the running Raticode and only starts or stops dictation. It does nothing while **Dictate to Rem from any app** is off.

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
- **The screenshot is black or empty.** The screen may be locked or the window minimized. On macOS, check **Screen & System Audio Recording** permission. On Wayland with Raticode running as an X11 app, only X11 apps show up.

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
