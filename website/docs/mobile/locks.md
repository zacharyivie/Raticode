---
title: App lock and Child lock
description: Require your fingerprint, face or screen lock to open Raticode Mobile, and lock the screen before handing your phone to a child.
pageClass: docs-layout
---

# App lock and Child lock

Both locks are off until you turn them on, both need Android 10 or newer, and both need the phone to have a screen lock. Android does the checking; Raticode only gets the result and never sees your PIN, pattern or password.

## App lock

**More › App lock** makes Raticode ask for your fingerprint, face or the phone's screen lock when it opens. Choose how long Raticode may be away from the screen before it asks again: **Immediately**, or after 1, 5 or 30 minutes. Turning it on or off, or allowing a longer time, asks first. Restarting the phone or a crash always asks.

While Raticode is locked, background sync, alerts, replies typed on alerts and messages waiting to send keep going. A notification tapped while locked opens where it points once you unlock. The share sheet asks too. On Android 13 and newer the recent apps preview is hidden.

## Child lock

The child lock lives on a small handle tucked against the right edge of every Raticode page, halfway down. It stays out of the way: only a thin mark shows on the edge. Tap that mark (or pull it out from the edge) and the handle slides in with an open padlock. Swipe it back to the right to put it away; it also slides away by itself after a few seconds untouched.

Before handing the phone to a child, bring the handle out, tap the padlock, then **Tap to lock** (or use **More › Child lock**). The padlock closes, and that same handle is now the way to unlock. Nothing else in Raticode responds, and video or sound in the browser keeps playing. Its [media controls](workspace.md#media-controls) on the lock screen and in the notification shade stop working too.

To unlock, tap the mark on the right edge to bring the handle out, then press and hold the closed padlock until its ring fills and enter the phone's PIN, pattern or password in Android's prompt. A tap or a short press does nothing. On Android 11 and newer a fingerprint or face doesn't count. A child who finds the handle still can't get past Android's prompt. With TalkBack, the edge mark offers **Show the lock** and **Unlock** directly, and the handle stays out longer if you've set a longer **Time to take action** in Android's accessibility settings. The handle slides in and out without animation when Android's animations are turned off.

The lock stays on if you leave the app, close it, rotate the phone or restart it. It only covers Raticode: the volume keys still work, and Android doesn't let an app block Home, Recents, the notification shade or other apps. Use **App pinning** in Android's security settings to keep the phone on one app. Clearing Raticode's storage in Android settings also removes the lock.
