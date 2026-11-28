---
title: Privacy
description: What this website and the Raticode apps collect, where your data stays, and which outside services they contact.
pageClass: docs-layout
---

# Privacy

Raticode has no account, no sign-up and no Raticode server. Neither this website nor the apps send usage analytics.

## This website

- The site has no analytics, tracking scripts, cookies, ads or forms. Fonts and images are served with the site, not loaded from other services.
- Your browser keeps the light or dark appearance you choose in its own local storage. Search runs in your browser against an index that ships with the site.
- The site is hosted on GitHub Pages. GitHub receives your IP address and request details when you load a page, as described in the [GitHub General Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
- Download links and **Edit this page on GitHub** go to GitHub, and the links to issues and releases do too.

## The desktop app and CLI

- Projects, workflows, threads, run history and settings stay in your project folders and Raticode's data folder on your computer. [Permissions and local data](./advanced/security.md) lists where.
- Rem and agent nodes send your prompts and the files you give them to the AI provider you set up, under that provider's account and terms. See [Provider setup](./setup.md).
- **Check for updates** (in **Settings > General**, on by default) asks GitHub whether a newer release exists when Raticode starts and, from 0.5.5, every 10 minutes while it runs. GitHub sees your IP address and Raticode's version. Turn it off to check by hand.
- **Download offline model** in **Settings > Devices** downloads the offline speech model from the sherpa-onnx project's releases on GitHub, only when you click it. GitHub sees your IP address. Dictation itself never goes online.
- **Enable related-meaning search** in a brain's settings downloads the `BAAI/bge-small-en-v1.5` model from Hugging Face (huggingface.co) the first time a search or index needs it. Hugging Face sees your IP address. After that the model runs on your computer, so your notes and searches stay there unless you choose cloud embeddings. Raticode turns off the usage telemetry of ONNX Runtime, which runs the model, and of Hugging Face's download library.
- Web pages you open in the integrated browser, HTTP request nodes and the tools you connect contact the sites and services you point them at.
- [jev nodes](./nodes/jev.md) (from 0.5.5) send their question and the text they classify to TypeSafe's Jev service, under your TypeSafe account and terms. Nothing is sent until you save a Jev key and run such a node.
- [Social posts](./advanced/posts.md) send a post to Discord, X, LinkedIn, Facebook or YouTube only after you approve it, under your account on that platform and its terms. Account logins stay in your system keychain.
- Dictation records only after you press the microphone button or a dictation shortcut, including **Dictate to Rem from any app** (off by default), and a listening panel or the message box shows while it records. Speech is recognized on your computer; audio and the words heard are never logged. [Dictation](./rem/attachments.md#dictate-a-message) explains where the words go.
- **Show Rem your screen** (off by default) takes one screenshot only when you click the screen button in Rem's message box or press its shortcut, and shows it there so you can remove it before sending. It goes to Rem's chat provider for that one reply, isn't saved or logged, and the chat keeps only the screen's or window's name. Raticode never captures your screen in the background. See [Show Rem your screen](./rem/attachments.md#show-rem-your-screen).
- When the app crashes, it shows the crash report on screen. **Open an issue** opens a pre-filled GitHub issue in your browser for you to read and edit, and nothing is sent unless you submit it. The issue shows your home folder as `~` and the app's own files from `app.asar/`, so it doesn't name your account; **Copy error details** keeps the full paths. **Help › Report a Problem** also opens a new issue for you to edit and submit, filled in with only the Raticode version, operating system, architecture and Electron version.

## Raticode Mobile

This section is the privacy policy for the Android app (`dev.raticode.mobile`), including copies installed from Google Play. In the app, it opens from **More › Privacy policy**.

The app has no account, ads, analytics, crash reporting or Raticode server. Its own messages go only to computers you pair it with, over your Wi-Fi or, if you turn it on, your own Tailscale network, encrypted end to end. [Pair and connect](./mobile/pairing.md) explains what each paired computer can see and do.

### What goes to your paired computer

Only what you send, and only after you tap send:

- Chat messages, and files you attach or share to Rem (pictures, videos, PDFs, text; up to 10 MB each).
- The words voice mode heard. Speech is recognized on the phone; audio is never stored or sent.
- With **Let Rem read your screen** turned on (off by default), the text of the app on screen, only for the message where you tap the screen button. Password fields and the lock screen are skipped.
- A page's title, address and selected text when you use **Ask Rem** in the browser.
- A random ID for the phone, made at pairing. No name, number, location, contacts or device model.

What your computer does next, such as passing your words to an AI provider, follows the desktop sections above.

### What stays on the phone

- Pairing keys, chat history (up to 32 MB), the send queue and browser tabs, history and bookmarks are encrypted with keys held in Android's Keystore.
- **People Rem can reach** keeps the name and number of each contact you pick, encrypted, and never syncs them.
- Settings, and website cookies and storage, stay in the app's private storage.
- The app is excluded from Android's cloud backup and device-to-device transfer.

### Permissions

| Permission | Used for |
| --- | --- |
| Camera | Scanning the pairing QR code. Chat photos come from your camera app |
| Microphone | Voice mode and dictation, recognized on the phone; web pages ask each time |
| Phone and SMS | Calls and texts you dictate to people you saved, after a countdown you can cancel. Texts go directly only while Raticode is your digital assistant; otherwise a draft opens. Raticode never reads texts, call logs or contacts |
| Notifications | Rem's replies, alerts and task progress, and at most one update reminder a day |
| Install unknown apps (from Raticode Mobile 0.35.0) | **Update Raticode** hands the verified update to Android's installer, which asks you to confirm. Android asks you to allow it first. The Google Play build doesn't have it |
| Biometrics | App lock and child lock ask Android to confirm it's you. Raticode only gets yes or no |
| Network and foreground services | Syncing with your computer while you choose, and keeping browser audio playing |

The app has no location permission, and web pages asking for your location are refused.

### Other services the app can contact

- **Websites you open** in the built-in browsers, over your phone's own connection. The browsers check pages with Google Safe Browsing, and the engines (Android WebView and Mozilla GeckoView) may fetch their own security updates.
- **GitHub**, from Raticode Mobile 0.35.0, while Raticode is on screen: every 10 minutes it reads the list of phone release tags (usually an unchanged answer), and the release details when one is newer. **Update Raticode** downloads the APK and its release checks from GitHub. GitHub sees your IP address and the app's version.
- **Mozilla's add-ons site** (addons.mozilla.org), when you search for or import Firefox add-ons. Your search words or the collection name are sent.
- **Your phone's speech service**, only if you tap **Download offline speech**, to fetch its language model.
- **Other apps** you open from a card or command, such as your dialer, messaging, maps, calendar or clock. They handle what you do there.

### Deleting your data

- **Forget this desktop** deletes that computer's pairing keys, history, queue and cached files from the phone.
- Remove people under **More › People Rem can reach**, and browsing data with **Clear browsing data** in the browser menu.
- Uninstalling the app deletes everything it stored. Copies on your computer follow the desktop's settings.

### Children

Raticode is a developer tool for adults. It isn't directed at children.

## Changes

Every change to this policy is public in [this page's history on GitHub](https://github.com/zacharyivie/Raticode/commits/main/website/docs/privacy.md). The app links here, so it always opens the current version.

## Questions

Email [support@raticode.dev](mailto:support@raticode.dev), or see [Support](./support.md).
