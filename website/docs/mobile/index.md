---
title: Raticode Mobile
description: Install the Android app, and see what it does, what it needs, and what stays on your computer.
pageClass: docs-layout
---

# Raticode Mobile

Raticode Mobile is an Android app for using Raticode on your computer from your phone: talk to Rem, follow threads and tasks, read files and Git history, work the board and Organizations, and run workflows.

All the work runs in Raticode on your computer. The phone sends requests and shows results. It never runs coding agents, and there's no Raticode cloud service in between.

## Install the app

Raticode Mobile is a signed APK on its own GitHub release. Get it from the [download section](/#download) or here:

<MobileDownload />

1. **Download the APK on the phone.** Open the link above in the phone's browser.
2. **Allow the install.** Open the downloaded file. Android asks you to allow the app you opened it from, such as your browser or Files, to **install unknown apps**. Allow it, go back and tap **Install**. You can turn that permission off again afterwards.
3. **Turn on pairing on the computer.** In Raticode, open **Settings > Developer > Feature Preview** and turn on **Phone and desktop pairing**.
4. **Pair.** In **Settings > Devices**, create a QR code and scan it with the app, then confirm on both sides. [Pair and connect](./pairing.md) has every step.

### Check the download

Optional, on a computer with both files in one folder:

- **Checksum:** `sha256sum -c --ignore-missing checksums-android.txt` prints `OK` beside the APK's name.
- **Signature:** `apksigner verify --print-certs raticode-mobile-X.Y.Z.apk` (from the Android SDK build tools) prints `Signer #1 certificate SHA-256 digest:` followed by the signing certificate above, without the colons.

### Updates

The app doesn't update itself. To update, download the newer APK and install it over the old one; pairing and history stay. Android installs it only when both are signed with the same certificate. A copy you built yourself is signed with a different key: uninstall it first, which deletes its pairing and history.

::: info Status
- **Google Play:** being prepared. Signed releases also include an Android App Bundle, `raticode-mobile-X.Y.Z.aab`, for Play. The app isn't on Google Play yet. The [privacy policy](../privacy.md#raticode-mobile) covers both.
- **Testing so far:** most features are tested on emulators against the real desktop code, not yet on physical phones. An independent security review is still to come.
:::

## Requirements

- **Android 10 or newer.** Older Android only shows a notice. The voice assistant and dictation need **Android 12 or newer** and an offline speech model.
- **Raticode running on your computer.** Closing its window to the tray is fine. When the desktop is too old for a feature, the phone says so and asks you to update it.
- **The same Wi-Fi as the computer**, or your own [Tailscale network](./pairing.md#away-from-home-with-tailscale) when you're away. The phone never uses a relay, and only reaches the computer over mobile data through Tailscale.

## What you can do from the phone

| Area | What it does | Guide |
| --- | --- | --- |
| Pairing | QR pairing, Wi-Fi and Tailscale, waking the computer | [Pair and connect](./pairing.md) |
| Rem and threads | Rem's chat and tasks, threads, attachments, Share to Rem, dictation, the voice assistant, alerts | [Rem on the phone](./rem.md) |
| Projects and work | Files, Source control, Organizations, the global board and self-work, workflows, approvals, swarms, the developer log | [Projects, boards and workflows](./workspace.md) |
| Browser | Raticode (chromium) and Raticode (gecko) tabs in one list, or Firefox, Chrome or Edge | [The phone's browser](./workspace.md#browser) |
| Privacy | App lock and Child lock | [App lock and Child lock](./locks.md) |

## What the computer controls

Your computer decides what each phone may do. Each paired phone has its own switches in **Settings > Devices**; see [Choose what the phone can do](./pairing.md#choose-what-the-phone-can-do). Requests from the phone run under the same project scope, approvals and permissions as work started on the computer.

These never happen from the phone:

- editing, creating, moving or deleting files (Files and Source control are read-only)
- reading files that look like secrets, such as `.env` or private keys; they're listed but never leave the computer
- terminals, credentials and agent permission settings
- Git actions such as staging, committing, pushing and switching branches

## The bottom bar

After pairing, the app opens on the first page of its bottom bar. By default that's **Rem**, **Threads**, **Orgs**, **Swarms** and **More**.

**More › Bottom bar** lets you pick up to four pages and their order from Rem, Threads, Organizations, Swarms, Source control, Files, Workflows, Approvals, Usage, Voice assistant, Desktop settings, Developer log and Browser. **More** stays last and lists everything else.

See also [Permissions and local data](../advanced/security.md) and [Desktop fleet](../advanced/fleet.md).
