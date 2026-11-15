---
title: Projects, boards and workflows on the phone
description: Read files and Git history, work the global board and Organizations, run workflows and browse the web from Raticode Mobile.
pageClass: docs-layout
---

# Projects, boards and workflows on the phone

These pages need **Use Raticode from this phone** in the computer's [device settings](./pairing.md#choose-what-the-phone-can-do). The browser is the exception: it works on its own.

Find each page in **More**, or put it on the bottom bar. When the computer is too old for a page, the page says so.

## Files

**Files** lists the projects open in Raticode on your computer. Browse one folder at a time, with a breadcrumb trail and a name filter.

Tap a file to open it full screen:

- code and text with line numbers, highlighting, wrapping and find
- previews of Markdown, HTML (no scripts, nothing loaded from the internet) and SVG
- PDFs page by page, zoomable pictures, video and sound
- other formats offer **Open in another app** and **Save to phone**

Files is read-only.

- Large text files show their first 256 KB. **Show all** fetches the rest, up to 10 MB.
- Other files over 10 MB can't come to the phone.
- Files that look like secrets, such as `.env`, show a lock and never leave the computer.

## Source control

**Source control** lists your open projects with their branch, commits to push or pull, changed files and any merge or rebase in progress. Each project has the desktop's three views:

| View | Shows |
| --- | --- |
| **History** | The branch graph with branch and tag labels, and each commit's author, time, ID and added and removed lines. Filter by branch, message, author or ID; tap a commit for its files and diffs. |
| **Changes** | Conflicts, staged and other changes. Tap a file to read its diff. |
| **Branches** | Local branches and how they track their upstream, then worktrees |

It's read-only: stage, commit, push, pull and switch branches on the computer. See [Source control](../studio/source-control.md).

## Organizations

The **Orgs** tab shows each [organization](../advanced/organizations.md) with its **Organization**, **Projects**, **Issues**, **Workers** and **Knowledge** sections.

- The list marks each organization that is waiting on you with **N need you** (Rem's requests, results to review, blocked tickets and plans to approve), shows why a paused one stopped, such as a reached limit, and puts archived ones last.
- A new organization's **Organization** page shows **Get started** until it has run once. Add workers and project folders in Raticode on your computer; **Add** opens a new ticket here.
- **Start** asks you to confirm and says when nothing will run yet: no workers, every worker held, no project folder or no open tickets.
- You create organizations in Raticode on your computer. It only needs a name.
- **Issues** keeps each organization's project, worker and **Needs me** filters, and the column you were on, after you leave it or close the app. A filter that's on is highlighted, and **Clear filters** turns them off. A removed project or worker falls back to all.
- An open organization shows **Offline** beside its state while the phone isn't connected. A strip under the header says when the page shows older information: offline, or after a refresh failed. After a failed refresh, **Try again** asks your computer again.

**Organization** shows the [work schedule](../advanced/organizations.md#work-schedule): the block on now and its focus, the next block and when it starts, warnings about gaps, and which block each running worker started under.

- Tap the card to change blocks, their cadence (daily, weekly, monthly or quarterly), times, keywords and time zone.
- With no schedule yet, **Use suggested schedule** fills one in for you to edit.
- Nothing is saved until you tap **Save**, and the computer checks the schedule again before saving.
- Offline, you see the schedule you last loaded and can't edit it. An older computer asks you to update it.

**Needs you** on the same page shows Rem's requests with **Approve** and **Decline**. Approve asks you to confirm first. With two or more waiting, **Approve all** lists exactly what it will approve and approves only those, in the [same safe order as the computer](../advanced/organizations.md#approve-several-requests-at-once); a request that arrives after the list opens keeps waiting. A card then shows each request as **Approved**, **Failed** or **Left waiting**. The phone approves exactly what you could approve at the computer, including requests that let workers run without a sandbox or add secrets. An older computer that can't take decisions from the phone asks you to update it, and an older computer may leave unsandboxed requests waiting for you there.

The **Auto mode** card turns [Auto mode](../advanced/organizations.md#auto-mode) on or off. Turning it on asks you to confirm what it allows. With it on, the organization shows **Auto**; requests Rem is deciding say **Rem is reviewing** and aren't in Approve all; the card lists Rem's recent decisions with their reasons; and if Auto mode stopped the organization, a card says why and what to do. An older computer shows an update note instead of the switch. You can also [tell Rem to approve](../advanced/organizations.md#tell-rem-to-approve) in its chat on the phone.

**Backlog pulls** on the same page turns [workers pulling accepted Backlog tickets into To do](../advanced/organizations.md#pull-from-backlog) on or off. A Backlog ticket's details say whether a worker will pull it or what it waits for. Offline the switch is disabled, and an older computer that doesn't have the setting asks you to update it.

## Global board and self-work

**More › Global board** shows every organization's tickets and Rem's own, one column at a time. Filter by whose tickets, who they're assigned to and which project. Like the computer's board, it has a **Blocked** column for work that is waiting, paused, stopped or failed. A paused ticket shows **Paused** or **Paused for your work**; moving it to **In progress** resumes it.

- Add a ticket for Rem or an organization, assign it, or hand one of Rem's tickets to an organization.
- Long-press a ticket to move it.
- The filters, the column you were on and the open section (**Board**, **Proposed**, **People** or **Self-work**) stay after you leave the board or close the app. A filter that's on is highlighted, and **Clear filters** turns them off. If a saved organization, person or project is gone, that filter shows everything again and the others stay. The phone keeps its own filters, separate from your computer's.
- Open a ticket for **History and comments**: each status change with its reason, oldest first, and comments from you, Rem and workers. Long results stay folded until you tap **Show the full text**. Add a comment at the bottom.
- Offline, a ticket shows the history you last loaded. An older computer asks you to update it instead.
- Tapping a ticket pill in Rem's chat opens that ticket here. A ticket no longer on the board says so.
- A ticket that [waits for other tickets](../rem/board.md#tickets-that-wait-for-other-tickets) shows **Waits for:** on its card. Its page lists them in **Waits for**, where you add one with **Wait for a ticket…** or remove one with its **×**. If one was cancelled, the card and page show a notice instead. An older computer sends no list, so nothing shows.

The board also has:

| Section | What it does |
| --- | --- |
| **Proposed** | Rem's ideas, with **Approve** and **Decline**. Nothing runs until you approve it. |
| **People** | Rem and every organization's agents. Tap someone to choose the projects they may work in during self-work. |
| **Self-work** | What Rem is doing on its own, where it can work, and the report of tickets added and work done. **Self-work settings** opens its settings. |

### Self-work settings

**Desktop settings › Rem self-work** holds them, so they work even when the board is off on the computer.

- Turn self-work on or off, set the maximum threads, and choose whether Rem pulls from Backlog when To do is empty. Telling Rem "turn on self-work" or "stop self-work" changes the same switch.
- **Approve its own backlog tickets** lets self-work's new ideas go to Backlog already approved until self-work ends, instead of waiting for you. Turning it on shows what it allows and asks you to confirm; turning it off doesn't. Those tickets carry an **Auto-approved** badge on the board and on their page, and the report says how many it approved. A computer without this setting shows no switch. See [Let Rem approve its own tickets](../rem/board.md#let-rem-approve-its-own-tickets).
- The status line says how many self-work tasks are paused to make room for your work.
- See the report. **Where Rem can work** opens the board's **People**.
- Rem's chat shows what happened while you were away, with a task card for each job.

With the board off on the computer:

- the page says so, and backlog pulling is unavailable
- Rem's research ideas appear in the report instead of as proposals; the research jobs still show in Rem's chat
- tapping **While you were away** opens **Rem self-work**

An older computer without the board shows an update note instead. See [Global board and self-work](../rem/board.md) for the computer side.

## Workflows

**More › Workflows** lists the workflows in your open projects. Open one to see its graph, steps, run history and [triggers](#triggers).

- **Run**, **Stop**, **Resume** and **Run again with the same inputs** ask first, then run on the computer with its usual checks.
- **Stop** reaches a run however it started, including one the computer's background trigger scheduler started. The run shows **Stop requested** until it ends.
- You can't edit workflows from the phone.
- When a workflow is missing a [secret](../workflows/inputs.md#secrets), the reason under **Run** names it and says where to add it on the computer. Secret values never travel to the phone, and the phone can't save them.

### All runs

::: info New in 0.35.0
The **All runs** list needs Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the tab doesn't appear.
:::

**All runs** at the top of **More › Workflows** lists the runs of every workflow in your open projects, newest first, like the computer's [Workflow runs](../studio/runs.md#every-workflow-s-runs) view. Each run shows its workflow, status, when it started, how long it took, whether it was started by hand, a trigger, the phone or Rem, and its tokens and estimated cost. Chips filter by time (24 hours, 7 days, 30 days or all time), status and workflow, and the line above the list totals what matches. Tap a run to open it in its workflow. The phone shows the newest 60 runs; older ones are on the computer. A run whose model step moved to a [fallback provider](../nodes/agent.md#fallback-providers) reads **switched provider**, and in the run's steps that step says which provider it left, why, and which one answered. That needs Raticode 0.5.5 on the computer; the phone app needs no update.

### Run with inputs

::: info New in 0.35.0
Filling in a workflow's inputs on the phone is new and needs Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, start workflows that need inputs on the computer.
:::

When a workflow has [inputs](../workflows/inputs.md), **Run** opens a sheet with a field for each one. The fields come from the same input schema as the computer's Run dialog:

- text, whole numbers and numbers
- a yes/no switch
- a row of choices, or a menu when there are more than six
- a JSON box for lists, objects and anything else

Fields start at the workflow's defaults. Leave an optional field blank to use its default. An input whose schema says `"format": "password"` is masked, and its default stays on the computer.

**Run** marks any field that needs fixing, such as a missing required value, a number out of range or JSON that doesn't parse, and sends nothing until they're fixed. The computer then checks the values against the schema again before anything starts. They also go through the same [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone) as everything else the phone sends.

### Resume and run again

When a run failed, **Resume from failed step** starts a new run on the computer. It reuses the steps that finished and runs the failed step again, then the steps after it. A stopped or interrupted run shows **Resume** and continues from where it stopped. The confirmation lists the steps that run again.

::: info New in 0.35.0
**Run again with the same inputs**, and resuming runs started with inputs or by a trigger, need Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the **Run again** button doesn't appear and runs started with inputs can't be resumed.
:::

**Run again with the same inputs** starts a new run of the workflow as it is now, with the inputs and trigger events the shown run used. The computer keeps those values in the run's folder; they never travel to the phone. See [Saved inputs and trigger events](../studio/runs.md#saved-inputs-and-trigger-events).

The computer decides whether a run can be resumed or run again. When it can't, the reason shows under the buttons. The computer doesn't save password inputs (`"format": "password"`), and the phone can't fill them in when it resumes or runs again, so for such a run the reason names those inputs: use **Resume** or **Run again** on the computer, where the app asks for them.

### Triggers

::: info New in 0.35.0
Triggers on the phone need Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the **Triggers** tab and the pause switch don't appear.
:::

A workflow's **Triggers** tab lists its [triggers](../workflows/triggers.md): each one's kind (schedule, file change or webhook) and what it watches, when it next runs, and how its last start went, such as **Succeeded**, **Failed** or **Skipped**, with the computer's reason. A webhook's address and token stay on the computer.

- Each trigger has its own switch. Turning one off happens at once.
- Turning one on asks first, because it then starts the workflow on your computer without asking each time it fires. Like everything the phone sends, it goes through the [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone), so a locked phone can't turn triggers back on. The computer refuses an unconfirmed request.
- Adding, changing and deleting triggers stays on the computer.

**Pause all triggers** at the top of **More › Workflows** pauses every trigger on the computer, the same switch as in its Workflow runs view. Pausing happens at once; resuming asks first and says how many triggers come back on. Each trigger keeps its own switch, so resuming restores exactly the ones that were on. While paused, the computer records scheduled times as skipped and doesn't catch them up. See [Pause all triggers](../workflows/triggers.md#pause-all-triggers).

## Approvals and swarms

With **Allow approvals and swarm controls** on:

- **Approvals** answers workflow and swarm approvals.
- **Swarms** shows each [swarm's](../advanced/swarms.md) status as a colored dot with its progress, each agent's state and the board, with pause, resume, stop and messages.
- Long board posts and approval summaries show a few lines until you tap **Show more**.

With **Use Raticode from this phone** you can also start swarms, including ones whose agents have full access, and start organizations or release a held worker that runs without a sandbox. Starting there counts as your confirmation, as it does at the computer.

## Desktop settings, Usage and the Developer log

- **Desktop settings** changes the computer's appearance, Rem defaults, editor, terminal and web browser settings.
- **Usage** shows provider usage, as in [Usage and allowances](../rem/usage.md).
- **Developer log** follows the computer's application log. Long-press an entry to copy it or **Ask Rem** about it.

## Browser

**Browser** runs over the phone's own internet connection, not through your computer. It has two built-in browsers, both inside Raticode, with tabs, bookmarks, history, find in page, and Raticode's bar in reach:

| Browser | Built on | Extras |
| --- | --- | --- |
| **Raticode (chromium)** | Android's WebView | — |
| **Raticode (gecko)** | Mozilla's engine | Profiles, tracking protection and Firefox extensions under **Extensions**. **Import from Firefox** brings over extensions, settings and bookmarks (not passwords or your Mozilla account). |

### Tabs and the default browser

<AppScreenshot name="phone-browser-tabs" phone caption="One tab list for both built-in browsers, with your computer's open tabs below." />

- The **Tabs** button lists the tabs of both built-in browsers together, each marked **chromium** or **gecko**. Tap any tab to switch. The other browser's tabs stay open, and music in one keeps playing while you use the other.
- **New tabs open in**, at the top of the list, picks the browser for a new tab, a private tab, a reopened tab or one of your computer's tabs. It starts at your default.
- **Browser settings and privacy › Default browser** sets the default. Raticode (gecko)'s settings have the same choice. A choice saved by an earlier version carries over: **Raticode's browser** is now **Raticode (chromium)** and **Gecko** is now **Raticode (gecko)**.

**Firefox**, **Chrome** or **Edge** as the default open pages in their own apps, with their own sign-ins and data, which Raticode never sees. Android doesn't let one app draw inside another, so Raticode can't list or switch their tabs. **Open this page in**, in the tab list, sends the current page to one of them. **Use Raticode (chromium) instead** and **Use Raticode (gecko), with Firefox extensions** switch back.

### Firefox extensions

In Raticode (gecko), **Extensions** in the menu searches Firefox Add-ons and lists what you've installed. Each extension shows what it can access before it's added, and only extensions Mozilla has signed install.

- **Open** shows the extension's popup over the page you're viewing, the same window its toolbar button opens in Firefox. On a new tab's start page the popup opens too, with no page behind it.
- An extension with no popup but with a settings page opens that page in a new tab. If it has both, **Settings** opens the settings page.
- An extension whose button has no window of its own does its work on the page you're viewing, and Raticode says so.
- **Nothing to open** marks an extension that has neither, such as one that works on every page by itself.
- Extensions don't run in private tabs. **Open** in a private tab says so and opens nothing. Switch to a regular tab first.
- The switch turns an extension off without removing it, and **Remove** uninstalls it. Profiles share the same extensions.

### Privacy and downloads

- **New private tab** is in both built-in browsers. Private tabs keep no history, and their cookies and site data are removed when the last private tab closes.
- Only secure (https) pages open, and certificate errors can't be skipped.
- Sites ask each time before using the camera or microphone.
- Downloads ask first, including files a page builds itself, such as a chat you export from Open WebUI (up to 25 MB in Raticode (chromium)).
- Only the tab on screen can ask a question, such as a download, a sign-in or another app. A new question never replaces one that's open; Raticode says **Answer the open question first, then try again**.
- In Raticode (gecko), at most three questions wait at once; more are answered **No**.
- Download names lose invisible characters, so a name can't hide its real extension.

### Your computer's tabs

With **Use Raticode from this phone**, a new tab also lists your computer's open browser tabs, each named with the browser it runs in there (Raticode (chromium), Raticode (gecko), Firefox, Chrome or Edge). **Open on your computer** opens the page in a background tab of the computer's [Browser plugin](../studio/browser.md).

### Tabs that stop while you're away

Android ends a browser's page processes to free memory while you use other apps or the screen is off. A page can also crash or get stuck. In Raticode (chromium) and Raticode (gecko) you don't get a "stopped working" message for this:

- The tab reloads by itself when you come back to it, at the same address. Tabs you aren't looking at reload when you open them.
- Raticode (chromium) tabs keep their Back and Forward history. Raticode (gecko) tabs keep it too, except private tabs, which reopen at their page.
- A stuck Raticode (chromium) page you're looking at gets about ten seconds, then reloads. Raticode (gecko) stops stuck scripts on its own.
- A page that was playing stops when its process ends; press play again after it reloads. Downloads keep going, since Android's download manager runs them.
- A page that stops again right after reloading gets three automatic reloads within two minutes, each a little later than the last. After that the tab shows **This page keeps stopping** with a **Reload** button.
- Known limit: a private Raticode (gecko) tab sometimes stays blank after Android ends its page. It stays private and keeps its address; close it and open the page again.

### Full-screen video

Tap a video's full-screen button in Raticode (chromium) or Raticode (gecko) and the video takes the whole screen. Raticode's bottom bar, the browser controls, the media bar and Android's status and navigation bars go away, and the screen turns to landscape even with auto-rotate off.

- With auto-rotate on, turn the phone over to flip the picture. A portrait video, such as a clip filmed on a phone, stays upright when the page reports its size.
- Swipe in from an edge to see Android's bars for a moment.
- **Back**, or the player's own exit button, leaves full screen. The page doesn't reload, the video keeps playing, and the screen follows how you hold the phone again.
- Switching to another tab or Raticode page, for example from a link or a notification, also leaves full screen.
- If you leave Raticode during full screen, it's still full screen when you come back.
- With [App lock](locks.md#app-lock) on, the lock screen shows upright with Android's bars, and the video returns to full screen once you unlock. The [Child lock](locks.md#child-lock) keeps the video full screen, with its handle on top.
- Firefox, Chrome and Edge handle full-screen video in their own apps.

### Media controls

While a page in Raticode (chromium) or Raticode (gecko) plays sound, a media bar above Raticode's pages shows the title, a picture when the page has one, and **Previous**, **Play/Pause** and **Next**. Sound keeps playing after you leave Raticode. The same controls appear on the lock screen, in the notification shade and for headsets, cars and watches, with **Stop** to end every page's sound.

- The media bar hides while the **Browser** page shows the playing tab; the lock-screen and notification controls stay. On another tab or page it comes back. If another tab is also playing, it shows that one.
- Only the buttons the page offers right now appear. Pages that don't name their track show the page title or the site's name.
- After a pause the controls stay for 30 seconds, so **Play** still works from the lock screen (5 seconds when the page can't resume). Closing the tab removes them at once.
- A private tab shows only **Private tab** on the lock screen and in the shade, with no title, site or picture.
- **Show what's playing on the lock screen**, in either browser's settings, is on by default. Off, the lock screen shows only **Playing in Raticode's browser** until you unlock. With [App lock](locks.md#app-lock) on, the lock screen always hides it.
- While the [Child lock](locks.md#child-lock) is on, controls outside Raticode do nothing and say so; the sound keeps playing.
- On Android 13 and newer the controls appear even with Raticode's notifications off, as Android allows for media. Android only lets Raticode start them while it's on screen, so a page that starts playing after you've left plays without them.
