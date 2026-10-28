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

Enable device networking on both installations using the [pairing guide](./mobile.md), then complete mutual confirmation in **Settings > Devices**. Verify the fingerprint on both screens. Desktop fleet targets are desktops, not phones.

Both sides must retain their trusted identity and have a working route. A relay can carry encrypted device traffic, but its presence does not grant project access or execution permission.

## Inspect before dispatch

With Fleet resources available, ask Rem to list paired desktops and obtain a fresh authenticated status snapshot. Check capabilities, the intended project, and the target's availability before asking it to do work.

Status expires after 60 seconds. A missing status is unknown rather than healthy, and an unknown running-job count is not zero. Device summaries are not a public heartbeat or monitoring service.

## Grant a specific job

Local Fleet execution permission and the target's own grants are both required. The `fleet_execute` permission is unchecked by default. A remote device cannot bypass its local project and tool restrictions because another desktop asked it to run something.

Specify the target, project, and task in the authorization. For example: "Run the agreed test command on the paired Linux desktop in this project, then return its result." Review the selected command and access before dispatch.

Organizations can use a paired desktop as a managed execution destination. That path also binds an employee allowlist, granted thread, target project, and reservation. It does not make general fleet requests equivalent to organization-managed tasks.

## Results and uncertainty

Keep the returned job receipt. A lost reply can leave the outcome unknown even when the remote side started. Inspect the job and its workspace before retrying; repeating a new request can duplicate side effects.

Remote file access uses scoped operations, size limits, and hashes where supported. Do not use it as a reason to distribute local credential files. **Stop** requests cancellation and needs a confirming result; it does not reverse completed actions.

See [Organizations](./organizations.md), [Mobile and pairing](./mobile.md), and [Permissions](./security.md).
