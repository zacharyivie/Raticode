---
title: Desktop fleet
description: Inspect paired desktops and delegate work using explicit target-local grants.
pageClass: docs-layout
---

# Desktop fleet

Use a paired desktop to run work, while its project access and permissions stay explicit.

::: warning Device rollout
Fleet depends on experimental device networking and compatible desktop versions. Pairing, connection and release checks are separate. A connected device isn't necessarily ready for unattended work.
:::

## Pair another desktop

1. Turn on **Phone and desktop pairing** on both computers (see [Pair and connect](../mobile/pairing.md#turn-on-pairing-on-the-computer)).
2. Choose **Pair a device**, then **Another computer**.
3. Confirm on both sides in **Settings > Devices**, checking that the fingerprints match.

Fleet targets are desktops, not phones.

- Both sides must keep their trusted identity and have a route: the local network, or your own Tailscale network if you opt in. There's no internet relay.
- A working connection grants no project access and no permission to run anything.
- **Run threads from this desktop**, on a paired desktop's entry, lets that desktop start Rem threads that run on this computer. They pick this computer under **Run on**.

## Inspect before sending work

With Fleet tools on in a thread, ask Rem to list paired desktops and fetch a fresh authenticated status. Check capabilities, the intended project and whether the target is available.

- Status expires after 60 seconds.
- A missing status means unknown, not healthy. An unknown running-job count isn't zero.
- Device summaries aren't a public heartbeat or monitoring service.

## Allow a specific job

Both are required:

- **Allow this thread to delegate work to paired desktops** in this thread. It's off by default.
- The target's own grants. A remote device never bypasses its local project and tool restrictions because another desktop asked.

Name the target, project and task when you authorize, for example: "Run the agreed test command on the paired Linux desktop in this project, then return its result." Check the command and access before Rem sends it.

Organizations can use a paired desktop as a managed execution destination. That path also binds an employee allowlist, a granted thread, the target project and a reservation. General fleet requests don't get those guarantees.

## Results and uncertainty

- Keep the returned job receipt.
- A lost reply can leave the outcome unknown even when the remote side started. Inspect the job and its workspace before retrying; sending a new request can repeat side effects.
- **Stop** asks for cancellation and needs a confirming result. It doesn't undo finished actions.
- Remote file access uses scoped operations, size limits and, where supported, hashes. Don't use it to copy credential files around.

See [Organizations](./organizations.md), [Pair and connect](../mobile/pairing.md), and [Permissions](./security.md).
