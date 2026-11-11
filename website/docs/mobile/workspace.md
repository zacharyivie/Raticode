---
title: Projects, boards and workflows on the phone
description: Read files and Git history, work the global board and Organizations, run workflows and browse the web from Raticode Mobile.
pageClass: docs-layout
---

# Projects, boards and workflows on the phone

These pages need **Use Raticode from this phone** in the computer's [device settings](./pairing.md#choose-what-the-phone-can-do), except the browser, which works on its own. Each one is in **More**, or on the bottom bar if you put it there. When the computer is too old for a page, the page says so.

## Files

**Files** lists the projects open in Raticode on your computer. Browse a folder at a time, with a breadcrumb trail and a name filter. Tapping a file opens it full screen: code and text with line numbers, highlighting, wrapping and find; previews for Markdown, HTML (no scripts, nothing loaded from the internet) and SVG; PDFs page by page; zoomable pictures; and video and sound. Other formats offer **Open in another app** and **Save to phone**.

Files is read-only. Large text files show their first 256 KB, and **Show all** fetches the rest up to 10 MB; other files over 10 MB can't come to the phone. Files that look like they hold secrets, such as `.env`, show a lock and never leave the computer.

## Source control

**Source control** lists your open projects with their branch, commits to push or pull, changed files and any merge or rebase in progress. A project opens on the desktop's three views:

- **History**: the branch graph with branch and tag labels, and each commit's author, time, ID and added and removed lines. Filter by branch, message, author or ID, and tap a commit for its files and diffs.
- **Changes**: conflicts, staged and other changes; tap a file to read its diff.
- **Branches**: local branches with how they track their upstream, then worktrees.

It is read-only: stage, commit, push, pull and switch branches on the computer. See [Source control](../studio/source-control.md) for the desktop side.

## Organizations

The **Orgs** tab shows each [organization](../advanced/organizations.md) with its **Organization**, **Projects**, **Issues**, **Workers** and **Knowledge** sections.

The **Organization** section shows the [work schedule](../advanced/organizations.md#work-schedule): the block on now and its focus, the next block and when it starts, warnings about gaps, and which block each running worker started under. Tap the card to change blocks, their cadence (daily, weekly, monthly or quarterly), times, keywords and the time zone. With no schedule yet, **Use suggested schedule** fills in the suggestion for you to edit. Nothing is saved until you tap **Save**, and the computer checks the schedule again before saving it. Offline, the section shows the schedule you last loaded and editing is off. A computer running an older Raticode says to update it instead.

## Global board and self-work

**More › Global board** shows every organization's tickets and Rem's own, a column at a time, with filters for whose tickets, who they're assigned to and which project. Like the computer's board it has a **Blocked** column for work that is waiting, stopped or failed. You can add a ticket for Rem or an organization, assign it, hand one of Rem's tickets to an organization, and move tickets with a long press.

Open a ticket to see **History and comments** under its details: each status change with its reason, oldest first, and comments from you, Rem and workers. Long results stay folded until you tap **Show the full text**, and you can add a comment at the bottom. Offline, the page shows the history you last loaded. A computer running an older Raticode says to update it instead of showing the history.

- **Proposed** lists Rem's ideas with **Approve** and **Decline**. Nothing there runs until you approve it.
- **People** lists Rem and every organization's agents. Tap someone to choose the projects they may work in during self-work.
- **Self-work** shows what Rem is doing on its own, where it can work, and the report of tickets added and work done. **Self-work settings** opens its settings. Rem's chat shows what happened while you were away, with a task card for each job.
- Tapping a ticket pill in Rem's chat opens that ticket here. A ticket no longer on the board says so.

Self-work's settings are in **Desktop settings › Rem self-work**, so they work even when the board is off on the computer. There you can turn self-work on or off, set the maximum threads and whether Rem pulls from Backlog when To do is empty, and see the report. **Where Rem can work** opens the board's **People** to choose Rem's projects. With the board off on the computer, the page says so, backlog pulling is unavailable, and Rem's research ideas appear in the report instead of as proposals. The research jobs still show in Rem's chat with their task cards. With the board off, tapping **While you were away** opens **Rem self-work**. An older computer without the board shows an update note instead.

See [Global board and self-work](../rem/board.md) for how these work on the computer.

## Workflows

**More › Workflows** lists the workflows in your open projects. Open one to see its graph, steps and run history. **Run**, **Stop** and **Resume** ask for confirmation and run on the computer with its usual checks. Workflows can't be edited from the phone, a workflow that needs inputs is started on the computer, and Rattish runs can't be resumed yet.

## Approvals and swarms

With **Allow approvals and swarm controls** on, **Approvals** answers workflow and swarm approvals, and **Swarms** shows each [swarm's](../advanced/swarms.md) status as a colored dot with its progress, each agent's state, and the board, with pause, resume, stop and messages. Long board posts and approval summaries keep to a few lines until you tap **Show more**. With **Use Raticode from this phone** you can also start swarms.

## Desktop settings, Usage and the Developer log

- **Desktop settings** changes the computer's appearance, Rem defaults, editor, terminal and web browser settings.
- **Usage** shows provider usage, as in [Usage and allowances](../rem/usage.md).
- **Developer log** follows the computer's developer log. Long-press an entry to copy it or to **Ask Rem** about it.

## Browser

**Browser** is a phone browser that runs over the phone's own internet connection, not through your computer. Raticode has two built-in browsers, **Raticode (chromium)** (built on Android's WebView) and **Raticode (gecko)** (built on Mozilla's engine). Both run inside Raticode, with tabs, bookmarks, history, find in page and Raticode's bar still in reach. Raticode (gecko) adds profiles, tracking protection and Firefox extensions under **Extensions**, and **Import from Firefox** brings over extensions, settings and bookmarks (not passwords or your Mozilla account).

The **Tabs** button shows the tabs of both built-in browsers in one list, each marked **chromium** or **gecko**. Tap any tab to switch to it. The other browser's tabs stay open, and music playing in one keeps playing while you use the other. At the top of the list, **New tabs open in** chooses which browser a new tab opens in, including a private tab, a reopened tab or one of your computer's tabs. It starts at your default browser.

**Browser settings and privacy › Default browser** sets that default. Raticode (gecko)'s settings have the same choice. A choice saved by an earlier version is kept: **Raticode's browser** is now **Raticode (chromium)** and **Gecko** is now **Raticode (gecko)**.

- **Firefox**, **Chrome** or **Edge** as the default open pages in their own apps, which keep their own sign-ins and data; Raticode never sees them. Android doesn't let one app draw inside another, so these always use their own screen, and Raticode can't list or switch their tabs. The tab list offers **Open this page in** to send the page you're on to one of them. **Use Raticode (chromium) instead** and **Use Raticode (gecko), with Firefox extensions** switch back.

Both built-in browsers offer **New private tab**: private tabs keep no history, and their cookies and site data are removed when the last private tab closes. Only secure (https) pages open, certificate errors can't be skipped, and sites ask each time before using the camera or microphone. Downloads ask first, including files a page builds itself, such as a chat or artifact you export from Open WebUI (up to 25 MB in Raticode (chromium)).

### Media controls

While a page in Raticode (chromium) or Raticode (gecko) plays sound, a media bar sits above Raticode's pages with the title, a picture when the page has one, and **Previous**, **Play/Pause** and **Next**. The sound keeps playing after you leave Raticode, and the same controls appear on the lock screen, in the notification shade and for headsets, cars and watches, with **Stop** to end every page's sound.

- The media bar hides while the **Browser** page is showing the tab that's playing, since the page already shows its player; the lock-screen and notification controls stay. Go to another tab or Raticode page and it comes back. If another tab is also playing, the bar shows that one instead.
- Only the buttons the page offers right now appear. Pages that don't name their track show the page title, or the site's name.
- After a pause, the controls stay for 30 seconds so **Play** still works from the lock screen (5 seconds when the page can't resume), then go with the media bar. Closing the tab removes them at once.
- A private tab shows only **Private tab**, with no title, site or picture, on the lock screen and in the shade.
- **Show what's playing on the lock screen**, in either browser's settings, is on unless you turn it off. Off, the lock screen shows only **Playing in Raticode's browser**, and the title comes back when you unlock. With [App lock](locks.md#app-lock) on, the lock screen always hides it.
- While the [Child lock](locks.md#child-lock) is on, the controls outside Raticode do nothing and say so; the sound keeps playing.
- On Android 13 and newer the controls appear even if Raticode's notifications are turned off, as Android allows for media. Android only lets Raticode start them while it's on screen, so a page that starts playing after you've left Raticode plays without them.

With **Use Raticode from this phone**, a new tab also lists your computer's open browser tabs, each named with the browser it runs in there (Raticode (chromium), Raticode (gecko), Firefox, Chrome or Edge), and **Open on your computer** opens the page in a background tab of the computer's [Browser plugin](../studio/browser.md).
