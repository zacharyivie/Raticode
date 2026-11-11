---
title: Desktop fleet
description: Inspect paired desktops and delegate work using explicit target-local grants.
pageClass: docs-layout
---

# Desktop fleet

Use a paired desktop as an execution destination while keeping its project access and permissions explicit.

::: warning Device rollout
Fleet depends on experimental device networking and compatible desktop versions. Pairing, transport, and release acceptance are separate checks. Do not assume a connected device is ready for unattended execution.
:::

## Pair another desktop

Turn on **Phone and desktop pairing** on both installations (see [Pair and connect](../mobile/pairing.md#turn-on-pairing-on-the-computer)), choose **Pair a device** and pick **Another computer**, then complete mutual confirmation in **Settings > Devices**. Verify the fingerprint on both screens. Desktop fleet targets are desktops, not phones.

Both sides must retain their trusted identity and have a working route: the local network, or your own Tailscale network if you opt in. There is no internet relay. A working connection does not grant project access or execution permission.

**Run threads from this desktop**, on a paired desktop's entry, lets that desktop start Rem threads that run on this computer; they then choose this computer under **Run on**.

## Inspect before dispatch

With Fleet resources available, ask Rem to list paired desktops and obtain a fresh authenticated status snapshot. Check capabilities, the intended project, and the target's availability before asking it to do work.

Status expires after 60 seconds. A missing status is unknown rather than healthy, and an unknown running-job count is not zero. Device summaries are not a public heartbeat or monitoring service.

## Grant a specific job

Local Fleet execution permission and the target's own grants are both required. **Allow this thread to delegate work to paired desktops** is unchecked by default. A remote device cannot bypass its local project and tool restrictions because another desktop asked it to run something.

Specify the target, project, and task in the authorization. For example: "Run the agreed test command on the paired Linux desktop in this project, then return its result." Review the selected command and access before dispatch.

Organizations can use a paired desktop as a managed execution destination. That path also binds an employee allowlist, granted thread, target project, and reservation. It does not make general fleet requests equivalent to organization-managed tasks.

## Results and uncertainty

Keep the returned job receipt. A lost reply can leave the outcome unknown even when the remote side started. Inspect the job and its workspace before retrying; repeating a new request can duplicate side effects.

Remote file access uses scoped operations, size limits, and hashes where supported. Do not use it as a reason to distribute local credential files. **Stop** requests cancellation and needs a confirming result; it does not reverse completed actions.

See [Organizations](./organizations.md), [Pair and connect](../mobile/pairing.md), and [Permissions](./security.md).
