---
title: App lock and Child lock
description: Require your fingerprint, face or screen lock to open Raticode Mobile, and lock the screen before handing your phone to a child.
pageClass: docs-layout
---

# App lock and Child lock

| Lock | Use it to |
| --- | --- |
| **App lock** | Ask for your fingerprint, face or screen lock whenever Raticode opens |
| **Child lock** | Freeze Raticode's screen before handing the phone to a child |

Both are off until you turn them on. Both need Android 10 or newer and a screen lock on the phone. Android does the checking: Raticode only gets the result and never sees your PIN, pattern or password.

## App lock

Turn it on in **More › App lock**, and choose how long Raticode may be off screen before it asks again: **Immediately**, or after 1, 5 or 30 minutes.

- Turning it on or off, or allowing a longer time, asks first.
- After a phone restart or a crash, it always asks.
- The share sheet asks too.
- On Android 13 and newer, the recent apps preview is hidden.

While Raticode is locked, background sync, alerts, replies typed on alerts and messages waiting to send keep going. A notification you tap while locked opens where it points once you unlock.

App lock covers Raticode's own screens. It doesn't cover [voice mode](rem.md#voice) or **Reply** on Rem's alerts, on purpose: those follow the phone's own lock screen, so you can talk to Rem hands-free. Anyone using your unlocked phone can talk to Rem through them too. The computer still [checks every request from the phone](pairing.md#suspicious-requests-lock-the-phone) and locks the phone if one asks for your credentials or carries something to run.

On Android 10 to 12, the recent apps preview still shows Raticode's screen. Hiding it there would also block your screenshots of Raticode, so it isn't hidden.

## Child lock

The child lock lives on a small handle on the right edge of every Raticode page, halfway down. Only a thin mark shows until you need it.

### Lock

1. Tap the mark on the right edge, or pull it out. The handle slides in with an open padlock.
2. Tap the padlock, then **Tap to lock**. (Or use **More › Child lock**.)

The padlock closes. Nothing else in Raticode responds, and video or sound in the browser keeps playing. The browser's [media controls](workspace.md#media-controls) on the lock screen and in the notification shade stop working too.

To put the handle away without locking, swipe it back to the right. It also slides away by itself after a few seconds.

### Unlock

1. Tap the mark on the right edge to bring the handle out.
2. Press and hold the closed padlock until its ring fills.
3. Enter the phone's PIN, pattern or password in Android's prompt.

A tap or a short press does nothing. On Android 11 and newer, a fingerprint or face doesn't count, so a child who finds the handle still can't get past the prompt.

### Accessibility

- With TalkBack, the edge mark offers **Show the lock** and **Unlock** directly.
- The handle stays out longer if you've set a longer **Time to take action** in Android's accessibility settings.
- With Android's animations off, the handle moves without animation.

### What it doesn't cover

The lock stays on if you leave the app, close it, rotate the phone or restart it. But it only covers Raticode:

- The volume keys still work.
- Android doesn't let an app block Home, Recents, the notification shade or other apps. Use **App pinning** in Android's security settings to keep the phone on one app.
- Clearing Raticode's storage in Android settings removes the lock.
