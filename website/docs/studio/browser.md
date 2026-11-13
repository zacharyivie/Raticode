---
title: Browser
description: Keep Raticode (chromium), Raticode (gecko), Firefox, Chrome and Edge tabs in one tab bar, with private tabs, downloads, a media bar and limits on Rem.
pageClass: docs-layout
---

# Browser

**Browser** is its own plugin in the plugin rail. It fills the main area with your web tabs, from any browser Raticode can run, in one tab bar.

- It sits between **File explorer** and **Search** unless you move it. It has no side pane; Rem and threads stay on the right.
- Web tabs keep running while another plugin is in front. Music keeps playing and pages keep their scroll position.
- The first time you start this version, web tabs you had open move into the Browser plugin, in the same order and with their history. **File explorer** then shows only file tabs.

## Open a tab

- Click **Browser** in the plugin rail, or press `Ctrl+J` from anywhere.
- Links that open a page, Rem's tabs and pages sent from your phone also land here.
- Type a URL, or words to search with your search URL.

Set the **Home page** and **Search URL** in **Settings > Browser**. Put `{query}` where the search terms go.

The browser menu has **New private tab**, **Reopen closed tab**, History, Bookmarks, Downloads, site data and clearing, zoom, Print, Save as PDF, **Save page as…**, **Hard reload**, **Developer tools** and Mute. [Keyboard shortcuts](../reference/shortcuts.md#browser) cover the same actions.

Sites ask before using permissions. Pop-ups are blocked until you allow them. Sites that want a client certificate or a sign-in show a prompt.

## One tab bar for every browser

Each tab has a small badge naming its browser: **chromium**, **gecko**, **Firefox**, **Chrome** or **Edge**. Each browser's tabs sit together in the tab bar, in the order you first opened that browser. Private tabs form their own group. Click any tab to switch to it. The other tabs keep running, so music in a Raticode (gecko) tab keeps playing while you work in a Chrome tab.

<AppScreenshot name="browser-tabs" caption="One tab bar for every browser. Each badge names the browser the tab runs in." />

- **`+` after a browser's tabs** opens another tab in that browser. After the private tabs it opens another private tab. Raticode (gecko) has no `+` of its own here, because Firefox's window already has one (see [Raticode (gecko) is one tab](#raticode-gecko-is-one-tab)).
- **`+ Browser`**, right after the last tab, starts a tab in a browser that has no tabs yet, or a private tab. It also always lists Raticode (gecko), which opens a new tab inside its Firefox window. It opens in the tab bar itself, so it never covers the page. `Escape` or a click elsewhere closes it.
- A browser that isn't installed is greyed out. Choosing it says why under the tab bar instead of doing nothing.
- `Ctrl+T` inside a tab opens another tab in the same browser. Links a page opens in a new tab also stay in its browser.
- `Ctrl+Tab` and `Ctrl+Shift+Tab` move through the tabs in the order the tab bar shows them.
- **Reopen closed tab** reopens a tab in the browser it ran in.
- To see two tabs side by side, drag one onto the left or right edge, or use **Split left** / **Split right** on its context menu. The two can use different browsers.
- Drag tabs to reorder them within their browser's group.
- With no tabs open, the Browser plugin shows one button per browser.

<AppScreenshot name="browser-tab-groups" caption="Each browser's tabs sit together with a + after them. + Browser starts a browser that has no tabs yet." />

### The browsers

| Browser | What it is |
| --- | --- |
| **Raticode (chromium)** | Raticode's own Chromium-based browser. The default. |
| **Raticode (gecko)** | Real Firefox, with its own tabs, settings and extensions, placed inside one tab. **Linux only**, on an X11 display (XWayland on Wayland desktops). The first time, Raticode may ask to restart in X11 mode. Not available on macOS, and not built for Windows. |
| **Firefox**, **Chrome**, **Edge** | Your installed browser, running without a window in its own profile, apart from your everyday one. Raticode shows its pages in tabs and passes your clicks and typing through. |

If another program connects to Firefox's control connection before Raticode does as Firefox starts, Raticode closes that Firefox instead of using it and says so. Try again. If it keeps happening, check what else runs on your computer.

### What each browser supports

| | Raticode (chromium) | Firefox, Chrome, Edge | Raticode (gecko) |
| --- | --- | --- | --- |
| Address bar, back, forward, reload | Yes | Yes | Firefox's own |
| Private tabs | Yes | Opens in Raticode (chromium) | Firefox's own private window |
| Developer tools | Yes | No | Firefox's own |
| Rem can open and control tabs | Yes | Yes | No |
| Media bar | Yes | Yes | Yes (Linux) |

### Choose the default browser

**Settings > Browser > Default browser for new tabs** sets the browser that links, `Ctrl+J` and **Open Browser** use for a new tab.

- Rem's tabs and pages from your phone use it too. When the default is Raticode (gecko), they use Raticode (chromium) instead.
- Changing it doesn't move tabs that are already open.
- A choice saved by an earlier version, when the setting was called **Web browser**, carries over: **Raticode** is now **Raticode (chromium)** and **Gecko** is now **Raticode (gecko)**.

**Settings > Browser > Settings for each browser** shows one browser's profiles, sign-in, extensions and importing at a time, starting with your default. **Show settings for** picks another. Your everyday Firefox, Chrome or Edge isn't changed. A Firefox, Chrome or Edge tab's menu also opens that browser's section.

Local files and previews always open in Raticode (chromium), in the File explorer rather than the Browser plugin.

### Raticode (gecko) is one tab

Raticode (gecko) is Firefox's own window, with its own tab strip, so the tab bar holds at most one Raticode (gecko) tab.

- To open another Raticode (gecko) tab, use the `+` in Firefox's own tab strip, or `Ctrl+T` in Firefox. Choosing **Raticode (gecko)** from **`+ Browser`** does the same, and a line under the tab bar says the tab opened inside Firefox's window. Use Firefox's tab strip to move between them.
- Opening another page in Raticode (gecko) switches to that tab and opens the page as a new tab inside Firefox.
- A menu Raticode opens over the page, such as a tab's right-click menu, cuts a hole in Firefox's window just for itself. The rest of the page stays on screen, and clicking the page closes the menu. Something that covers most of the page, like a dialog, still hides Firefox's window until it closes.
- Gecko tabs saved by an earlier version become that one tab, with their pages inside it.
- Raticode can't list Firefox's inner tabs or split two of them side by side, because it doesn't control the pages in Firefox's window.
- For private browsing, use Firefox's **New private window** (`Ctrl+Shift+P`). It opens outside Raticode.

## Private tabs

**New private tab** (`Ctrl+Shift+N`, or `Cmd+Shift+N` on macOS, while the browser has focus) opens a tab marked private.

- It keeps cookies and site data apart from your other tabs.
- It adds nothing to history and saves no site permissions.
- Its data is removed shortly after you close the last private tab. Downloads and bookmarks you make stay.

Websites and your network can still see what you do.

## Downloads

- Files go to your Downloads folder, unless you turn on **Ask where to save each download**.
- Files that can run code always wait for you to keep them. That includes disk images (`.iso`, `.img`, `.vhd`, `.vhdx`) and app packages (`.msix`, `.appx`, `.snap`), which install or mount code when opened.
- Files a page builds itself, such as a chat you export from Open WebUI or ChatGPT, download the same way in every browser.
- In Raticode (chromium), right-click a link and choose **Save link as…** to pick the folder.

**Free memory from background tabs** pauses tabs you haven't viewed for a while. Tabs playing audio or controlled by Rem stay loaded.

## Media bar

While a tab plays sound, a thin bar at the top of the main area shows the title, the artist or site, a picture when the page has one, and **Previous**, **Play/Pause** and **Next**. It stays above every area of Raticode.

- It hides while you're looking at the playing tab, since the page shows its own player. In split view, both visible tabs count. If another tab is also playing, the bar shows that one.
- Buttons the page doesn't offer are greyed out. Pages that don't name their track show the page title or the site.
- After a pause the bar stays for 30 seconds, so **Play** can resume. Closing the tab or leaving the page removes it at once.
- **Raticode (gecko)** (Linux): Raticode follows Firefox's player through the desktop's media controls. The bar hides while the Raticode (gecko) tab is on screen and shows on any other tab or area. Raticode can't tell which of Firefox's own tabs is playing.

## Let Rem use the browser

Rem doesn't see your tabs unless you share them:

- **Ask Rem about this page** (browser menu) puts the page's title and address into Rem's message box, without sending.
- **Share this tab with Rem…** lets Rem work in that tab.
- **Browser** in a thread's [tools menu](../rem/resources.md) (off by default) lets Rem open and control its own tabs. They join the tab bar in the background, so you keep your place. When Rem needs your approval in a tab and the Browser plugin isn't in front, a bar at the top names the tab, with **Review** to go to it.

**Settings > Browser** controls what Rem may do:

| Setting | What it does |
| --- | --- |
| **Rem's browser** | Where Rem opens pages: **Raticode browser tabs** (in the background, using your default browser), or an external browser window, shown or hidden, through Playwright (needs Node.js) |
| **Sites where Rem uses your sign-ins** | Rem browses signed out, in its own profile. Listed sites may use your sign-ins, and Rem asks before clicking submit or Send-style buttons or running scripts on them. A site's sign-in often lives on its parent domain, so listing `mail.example.com` also makes Rem ask on `example.com` and its other subdomains. |
| **Sites Rem may open** / **Sites Rem may not open** | Allow and block lists. Subdomains are included, and the block list wins. |
| **Approve Rem's browser actions** | Whether Rem asks before clicking a form's submit button or a button such as Send or Delete, uploading files or running page scripts. Rem never asks before pressing Enter or another key, so Enter in a search box, a message field or a form goes through without a prompt. |
| **Let Rem run page scripts** | Turn off to limit Rem to reading, clicking and typing |
| **Downloads started by Rem** | **Block** (Rem is told instead) or **Save to Downloads** |

<AppScreenshot name="settings-browser-rem" caption="What Rem may do in the browser, in Settings > Browser." />

Pages in Rem's own tabs can't write to your clipboard, and a page can't use a pop-up to reach a site Rem may not open. Tabs you share with Rem keep their usual permissions.

Signing in to a website here doesn't sign in a coding CLI. See [Provider setup](../setup.md) for that.

## Read project reports

Local HTML files open from the File explorer in Browse mode, in the File explorer's own tabs. A previewed page can load files only from the folder it was opened from, so a page can't read other files on your computer. Markdown files have a rendered preview. **Settings > Editor** chooses whether Markdown and HTML open in preview or edit mode; the mode control switches back to source.

Reports Rem writes keep their own styling. Raticode doesn't change their CSS or theme. See [Reports, slides and PDF](../knowledge/reports.md).

## A page stops or gets stuck

A page can stop while you're away, for example after the computer sleeps, or get stuck running a script. In Raticode (chromium) you don't get an error or a dialog for it:

- A tab whose page stopped reloads by itself when you come back to it. It keeps its address and its Back and Forward history. Nothing reloads while the tab is out of sight or the Raticode window is minimized.
- A stuck page you're looking at gets about five seconds to recover, then reloads in place. A stuck tab in the background is left alone until you come back. A tab playing audio is never reloaded for being slow.
- Downloads keep going, since they don't run inside the page. Anything the page held only in memory, such as text you typed and didn't send, is lost when its page stops, as in any browser.
- A page that stops again right after reloading gets three automatic reloads within two minutes, each a little later than the last. After that the tab shows **This page keeps stopping** with a **Reload** button.

Rem's tabs keep showing **This page crashed**, so Rem can see it. Firefox, Chrome and Edge tabs keep their own messages, such as the one shown while you sign in in a separate window. Raticode (gecko) shows Firefox's own crash and slow-page notices.

## A page doesn't load

1. Check the URL, and try it in your system browser to tell a site problem from a Raticode problem.
2. For a local report, check the file still exists and is inside a selected folder.
3. For Raticode (gecko), check the desktop session has an X11 display.
4. **Settings > Developer** has the application log and diagnostics.

On the phone, see [the phone's browser](../mobile/workspace.md#browser).
