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
- **Check for updates at startup** (in **Settings > General**, on by default) asks GitHub whether a newer release exists. Turn it off to check by hand.
- Web pages you open in the integrated browser, HTTP request nodes and the tools you connect contact the sites and services you point them at.
- When the app crashes, it shows the crash report on screen. **Open an issue** opens a pre-filled GitHub issue in your browser for you to read and edit, and nothing is sent unless you submit it.

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
| Notifications | Rem's replies, alerts and task progress |
| Biometrics | App lock and child lock ask Android to confirm it's you. Raticode only gets yes or no |
| Network and foreground services | Syncing with your computer while you choose, and keeping browser audio playing |

The app has no location permission, and web pages asking for your location are refused.

### Other services the app can contact

- **Websites you open** in the built-in browsers, over your phone's own connection. The browsers check pages with Google Safe Browsing, and the engines (Android WebView and Mozilla GeckoView) may fetch their own security updates.
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

Ask on [Support](./support.md).
