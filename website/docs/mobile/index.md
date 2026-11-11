---
title: Raticode Mobile
description: What the Android app does, what it needs, and what stays on your computer.
pageClass: docs-layout
---

# Raticode Mobile

Raticode Mobile is an Android app for using Raticode on your computer from your phone. You can talk to Rem, follow threads and tasks, read project files and Git history, work the global board and Organizations, and run workflows. All the work runs in Raticode on your computer. The phone sends requests and shows results; it never runs coding agents itself, and there is no Raticode cloud service in between.

::: info Not published yet
The Android app is built and in testing, but has no public download yet. Every release candidate is built and checked, but so far without the release signing key, and Android won't install an unsigned APK, so none can be published. When it ships, each release is a signed APK, `raticode-mobile-X.Y.Z.apk`, on GitHub under a `mobile-vX.Y.Z` tag, with a `checksums-android.txt` file beside it, and the download section on this site's home page links to it. You install it by hand; the app has no automatic updates. Most phone features have been tested on emulators against the real desktop code, but not yet on physical phones, and an independent security review is still outstanding. Install the [desktop app](../installation.md) today.
:::

## Requirements

- **Android 10 or newer.** On older Android the app only shows a notice. The voice assistant and dictation need **Android 12 or newer** and an offline speech model.
- **Raticode on your computer, running.** Closing its window to the tray keeps the phone working. Many phone features need a desktop that is up to date; when the desktop is too old for a feature, the phone says so and asks you to update Raticode on your computer.
- **The same Wi-Fi network as the computer**, or your own [Tailscale network](./pairing.md#away-from-home-with-tailscale) when you're away. The phone never uses a relay and never sends desktop traffic over mobile data except through Tailscale.

## What you can do from the phone

| Area | What it does | Guide |
| --- | --- | --- |
| Pairing and connection | QR pairing, Wi-Fi and Tailscale, waking the computer | [Pair and connect](./pairing.md) |
| Rem and threads | Rem's chat and tasks, threads, attachments, Share to Rem, dictation, the voice assistant, alerts | [Rem on the phone](./rem.md) |
| Projects and work | Files, Source control, Organizations, the global board and self-work, workflows, approvals, swarms, the developer log | [Projects, boards and workflows](./workspace.md) |
| Browser | Raticode (chromium) and Raticode (gecko) tabs in one list, or Firefox, Chrome or Edge | [Projects, boards and workflows](./workspace.md#browser) |
| Privacy | App lock and Child lock | [App lock and Child lock](./locks.md) |

## What the desktop controls

Your computer decides what the phone may do. In **Settings > Devices** each paired phone has its own switches (see [Pair and connect](./pairing.md#choose-what-the-phone-can-do)). Everything the phone asks for runs on the computer under the same project scope, approval policies and permissions as work started there.

These stay on the computer: editing, creating, moving and deleting files; files that look like they hold secrets (such as `.env` or private keys), which are listed on the phone but never leave the computer; terminals; credentials; agent permission settings; and Git actions such as staging, committing, pushing and switching branches. Files and Source control on the phone are read-only.

## The bottom bar

After pairing, the app opens to the first page on its bottom bar. By default the bar holds **Rem**, **Threads**, **Orgs**, **Swarms** and **More**. **More › Bottom bar** lets you choose up to four pages and their order, from Rem, Threads, Organizations, Swarms, Source control, Files, Workflows, Approvals, Usage, Voice assistant, Desktop settings, Developer log and Browser. **More** always stays last and lists everything else.

See also [Permissions and local data](../advanced/security.md) and [Desktop fleet](../advanced/fleet.md).
