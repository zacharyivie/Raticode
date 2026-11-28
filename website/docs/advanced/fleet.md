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
- When a computer moves to another network, it tells its paired desktops its new address on its next exchange with them, usually within half a minute (see [Staying connected](../mobile/pairing.md#staying-connected)). A desktop that can't reach the other at all can't learn the new address until one of them makes contact.
- A working connection grants no project access and no permission to run anything.
- **Run threads from this desktop**, on a paired desktop's entry, lets that desktop start Rem threads that run on this computer. They pick this computer under **Run on**. Turning this off, revoking that desktop or unpairing it stops any of its tasks still running here.
- What a paired desktop sends back stays a suggestion here. Proposals from a self-work pass that ran on a paired desktop (through **Run there**) always wait for your review, even with self-work's [auto-approval](../rem/board.md#let-rem-approve-its-own-tickets) on, and name the desktop they came from. New in 0.6.1.

## Tasks another desktop hands over

A Rem task pinned to this computer from a paired desktop runs here as one of Rem's own tasks, under this computer's worker limit. Only research and conversation tasks are handed over. The queue order, the limit, the check labels and the run limits below are new in 0.6.1.

- It waits behind your own work here, as self-work and organization workers do. When one worker is free, a task you queue on this computer starts first, even if the handed-over task was queued earlier. While it waits, the sending desktop shows the reason on its run.
- One desktop can have at most 10 tasks queued or running here at once. This computer refuses the 11th, and the sending desktop shows why on that task. Run it again once one of the 10 ends.
- The checks sent with the task are marked **Paired desktop** in the task's checks, not as yours. In Auto mode, a check from another desktop that asks for your sign-off doesn't hold the task for you. Edit a check here and it becomes yours.
- Nobody here watches it, so it runs under [self-work's limits](../rem/board.md#limits-and-safety). With Claude Code, its shell commands run in [Claude Code's sandbox](../rem/board.md#claude-code-s-sandbox); with Codex, they can write Raticode's data folder only where that sandbox could. It can't push (`git push` is blocked), publish, or send messages outside this computer, and it gets no browser, subagents, swarms, fleet tools, Organizations tools or configured MCP servers.
- It doesn't get this computer's Second Brain, even when it's on, because its answer goes back to the other desktop. It keeps Rem's report saving.

## Work another desktop sends

An organization on a paired desktop can send a ticket's work here through a fleet execution target you granted it (see [Organizations](./organizations.md)). It runs in that target's project with that target's provider and model, and counts against this computer's worker limit.

Work the other desktop's Rem sends with its fleet tools, and messages it sends to a project you granted it under **Desktop work**, run the same way in that grant's project. Nobody here watches any of this work, so it runs under the same limits as a [task another desktop hands over](#tasks-another-desktop-hands-over), whether or not an organization sent it. These limits are new in 0.6.1 for organization work and in 0.6.2 for work the other desktop's Rem sends.

- With Claude Code, its shell commands run in [Claude Code's sandbox](../rem/board.md#claude-code-s-sandbox). If the sandbox can't start on this computer, the work is refused before it runs and the sending desktop sees it fail. With Codex, it can write Raticode's data folder only where that sandbox could.
- It can't push (`git push` is blocked), publish, or send messages outside this computer.
- It gets no browser, subagents, swarms, fleet tools, thread tools or configured MCP servers, even when the target allows them.
- It doesn't get this computer's Second Brain, because its answer goes back to the other desktop.
- Its instructions say the work came from another desktop (its organization or its Rem) and that nobody here is watching it.

Phone turns, and threads a paired desktop runs here through **Run threads from this desktop**, keep the tools and Second Brain their grant gives them.

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

Organizations can use a paired desktop as a managed run destination. That path also binds a worker allowlist, a granted thread, the target project and a reservation. General fleet requests don't get those guarantees.

## Results and uncertainty

- Keep the returned job receipt.
- A lost reply can leave the outcome unknown even when the remote side started. Inspect the job and its workspace before retrying; sending a new request can repeat side effects.
- **Stop** asks for cancellation and needs a confirming result. It doesn't undo finished actions.
- Remote file access uses scoped operations, size limits and, where supported, hashes. Don't use it to copy credential files around.

See [Organizations](./organizations.md), [Pair and connect](../mobile/pairing.md), and [Permissions](./security.md).
