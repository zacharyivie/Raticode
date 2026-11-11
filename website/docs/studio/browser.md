---
title: Browser
description: The Browser plugin keeps tabs from Raticode (chromium), Raticode (gecko), Firefox, Chrome and Edge in one tab bar, with private tabs, downloads, a media bar and controls for what Rem can do.
pageClass: docs-layout
---

# Browser

**Browser** is its own plugin in the plugin rail, between **File explorer** and **Search** unless you move it. Choosing it fills the main area with your web tabs. It has no side pane: only the plugin rail stays on the left, and Rem and threads stay on the right as they do with every plugin. **File explorer** shows your file tabs only.

Web tabs keep running while another plugin is in front, so music keeps playing and pages keep their place and scroll position when you come back. Tabs you had open before the Browser plugin move into it the first time you start this version, in the same order and with their back and forward history.

## Open a browser tab

Click **Browser** in the plugin rail, or press `Ctrl+J` from anywhere. Links that open a page, Rem's tabs and pages sent from your phone also land here. Enter a URL to navigate, or words to search with the configured search URL.

In **Settings > Browser**, set the **Home page** and **Search URL**. Include `{query}` where the encoded search terms should appear.

The browser menu has **New private tab**, **Reopen closed tab**, History, Bookmarks, Downloads, site data and clearing, zoom, Print, Save as PDF, **Save page as…**, **Hard reload**, **Developer tools**, and Mute. Browser [keyboard shortcuts](../reference/shortcuts.md#integrated-browser) cover the same actions. Sites ask before using permissions, pop-ups are blocked until you allow them, and sites that ask for a client certificate or sign-in show a prompt.

## One tab bar for every browser

The tab bar holds tabs from every browser Raticode can run, side by side. Each tab carries a small badge naming its browser: **chromium**, **gecko**, **Firefox**, **Chrome** or **Edge**. Click a tab to switch to it, whatever browser it runs in; the other tabs keep running, so music in a Raticode (gecko) tab keeps playing while you work in a Chrome or Edge tab.

- **New tab** (the `+` after the last tab) opens a tab in your default browser.
- The arrow beside it opens **New tab in**, which lists every browser and marks the default. Browsers that aren't installed are greyed out with the reason. **New private tab** is at the bottom.
- `Ctrl+T` inside a tab opens the next tab in the same browser. A link a page opens in a new tab also stays in that page's browser.
- **Reopen closed tab** brings a tab back in the browser it ran in.
- Drag tabs to reorder them, or onto the left or right edge, or use **Split left** / **Split right** on a tab's context menu, to see two tabs side by side, from the same browser or different ones.

| Browser | How it works |
| --- | --- |
| **Raticode (chromium)** | Raticode's own Chromium-based browser. The default. |
| **Raticode (gecko)** | Real Firefox, with its own tabs, settings and extensions, placed directly inside one tab. **Linux only**; it needs an X11 display (XWayland on Wayland desktops), and the first time you open it Raticode may ask to restart in X11 mode. It isn't available on macOS and hasn't been built for Windows. |
| **Firefox**, **Chrome**, **Edge** | The installed browser runs without a window, in its own profile separate from your everyday one. Raticode shows its pages in tabs and passes your clicks and typing through. Each has a settings section for sign-in, extensions and importing; your everyday browser isn't changed. |

**Settings > Browser > Settings for each browser** shows the profiles, sign-in, extensions and importing of one browser at a time, starting with your default; **Show settings for** picks another. A Firefox, Chrome or Edge tab's menu item for its settings opens that browser's section directly.

**Settings > Browser > Default browser for new tabs** chooses the browser that **New tab** uses. Rem's tabs and pages sent from your phone also use it, except that they use Raticode (chromium) when the default is Raticode (gecko). Changing the default doesn't move tabs that are already open. A choice saved by an earlier version, when this setting was called **Web browser**, is kept: **Raticode** is now **Raticode (chromium)** and **Gecko** is now **Raticode (gecko)**.

Local files and previews always use Raticode (chromium) and open in the File explorer, not the Browser plugin.

### Raticode (gecko) is one tab

Raticode (gecko) is Firefox's own window with its own tab strip inside, so the tab bar holds at most one Raticode (gecko) tab. Opening another page in Raticode (gecko) switches to that tab and opens the page as a new tab inside Firefox. Tabs saved by an earlier version that ran in Gecko become that one tab, and their pages open inside it. Use Firefox's own tab strip to move between Gecko pages. Raticode can't show Firefox's inner tabs in its own tab bar or split two of them side by side, because it has no control over the pages in Firefox's window. For private browsing in Raticode (gecko), use Firefox's own **New private window** (`Ctrl+Shift+P`), which opens outside Raticode.

### What each browser supports

| | Raticode (chromium) | Firefox, Chrome, Edge | Raticode (gecko) |
| --- | --- | --- | --- |
| Address bar, back, forward, reload | Yes | Yes | Firefox's own |
| Private tabs | Yes | Opens in Raticode (chromium) | Firefox's own private window |
| Developer tools | Yes | No | Firefox's own |
| Rem can open and control tabs | Yes | Yes | No |
| Media bar | Yes | Yes | Yes (Linux) |

## Private tabs

**New private tab** (Ctrl+Shift+N, or Cmd+Shift+N on macOS, while the browser has focus) opens a tab marked private. It keeps its cookies and site data apart from your other tabs, adds nothing to history, doesn't save site permissions, and its data is removed shortly after you close the last private tab. Downloads and bookmarks you make stay. Websites and your network can still see what you do.

## Downloads

Files go to your Downloads folder unless you turn on **Ask where to save each download**. Files that can run code always wait for you to keep them. Files a page builds itself, such as a chat or artifact you export from Open WebUI or ChatGPT, download the same way with every web browser choice. In Raticode (chromium) tabs, right-click a link and choose **Save link as…** to pick where a file goes. **Free memory from background tabs** pauses tabs you haven't viewed for a while; tabs playing audio or controlled by Rem stay loaded.

## Media bar

While a browser tab plays sound, a thin bar at the top of the main content shows the title, the artist or site, a picture when the page has one, and **Previous**, **Play/Pause** and **Next**. It stays above every area of Raticode, so you can pause or skip without going back to the tab.

- The bar stays out of the way while you're looking at the tab that's playing, since the page already shows its player. Switch to another tab or area and it appears; in split view, both visible tabs count as in view. If another tab is also playing, the bar shows that one instead.
- Buttons the page doesn't offer stay greyed out. Pages that don't name their track show the page title or the site.
- After a pause, the bar stays for 30 seconds so **Play** can resume, then goes. Closing the tab or leaving the page removes it at once.
- For **Raticode (gecko)**, Raticode follows Firefox's player (Linux, through the desktop's media controls). The bar hides Gecko's media while the Raticode (gecko) tab is on screen, since Firefox's window shows its player, and shows it while you're on any other tab or area. Raticode can't tell which of Firefox's own tabs is playing.

## Let Rem use the browser

Your own tabs aren't shared with Rem automatically. You can share deliberately:

- **Ask Rem about this page** (browser menu) puts the page's title and address into Rem's message box without sending.
- **Share this tab with Rem…** lets Rem work in that tab.
- The **Browser** entry in a thread's [tools menu](../rem/resources.md) (off by default) lets Rem open and control its own browser tabs. They join the Browser plugin's tab bar in the background, so you keep your place. When Rem needs your approval in a tab and the Browser plugin isn't in front, a bar at the top of the main area names the tab, with **Review** to go to it.

**Settings > Browser** controls what Rem may do:

| Setting | What it does |
| --- | --- |
| **Rem's browser** | Where Rem opens pages: Browser tabs using the default browser above, or an external browser through Playwright (needs Node.js) |
| **Sites where Rem uses your sign-ins** | Rem browses in its own signed-out profile; listed sites may use your sign-ins, and Rem asks before submitting or running scripts on them |
| **Sites Rem may open** / **Sites Rem may not open** | Allow and block lists; subdomains are included and the block list always wins |
| **Approve Rem's browser actions** | Whether Rem asks before submitting forms, pressing buttons such as Send or Delete, uploading files or running page scripts |
| **Let Rem run page scripts** | Turn off to limit Rem to reading, clicking and typing |
| **Downloads started by Rem** | **Block** (reported to Rem instead) or **Save to Downloads** |

Using the browser to sign in to a website does not sign in a coding CLI. Follow [Provider setup](../setup.md) for that CLI's authentication flow.

## Read project reports

Local HTML files open from the File explorer in Browse mode, in the File explorer's own tabs. Markdown files have a separate rendered preview. **Settings > Editor** chooses whether Markdown and HTML files open in preview or edit mode, and the mode control switches back to source for edits.

Reports authored by Rem keep their own embedded styling. Reading a report should not rewrite its CSS or change its theme. [Reports, slides and PDF](../knowledge/reports.md) explains how those documents are produced.

## A page does not load

Check the URL and try it in the system browser to separate a website problem from an embedded-browser problem. For a local report, confirm the file still exists and belongs to a selected folder. With Raticode (gecko), check that the desktop session has an X11 display. Use **Settings > Developer** for application diagnostics when the page or preview fails inside Raticode.

On the phone, see [the phone's browser](../mobile/workspace.md#browser).
