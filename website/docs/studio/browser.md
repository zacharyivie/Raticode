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
| Rem can open and control tabs | Yes | Yes | Yes, after you turn on [Let Rem use Raticode (gecko)](#let-rem-use-raticode-gecko) (Linux) |
| Share one of your tabs with Rem | Yes | Yes | The tab in front of Firefox's window, after you turn on [Let Rem use Raticode (gecko)](#let-rem-use-raticode-gecko) (Linux) |
| Media bar | Yes | Yes | Yes (Linux) |
| Keeps playing when the browser [changes windows](#move-the-browser-to-its-own-window) | Yes | Yes | Yes |

### Choose the default browser

**Settings > Browser > Default browser for new tabs** sets the browser that links, `Ctrl+J` and **Open Browser** use for a new tab.

- Rem's tabs and pages from your phone use it too. When the default is Raticode (gecko), they use Raticode (chromium) instead, unless Rem asks for Raticode (gecko) and you [let it](#let-rem-use-raticode-gecko).
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

## Make Raticode your computer's default browser

*New in Raticode 0.6.0.* Raticode can be the browser your computer uses for links you open in other apps, such as a link in an email or a chat, and for web pages saved as files (`.html`, `.htm`, `.xhtml`).

Installing Raticode only adds it to the list of browsers. It never makes itself the default. You choose it:

1. Open **Settings > Browser > Default web browser**. It says whether Raticode is the default.
2. Choose the button for your system:
   - **Windows**: **Open Windows default apps** opens Settings at Raticode's page. Choose **Set default**, or pick Raticode for `http`, `https` and `.html`. Windows only lets you make this change there.
   - **macOS**: **Open macOS Desktop & Dock settings**, then set **Default web browser** to Raticode. macOS may ask you to confirm.
   - **Linux**: **Open system default apps** opens your desktop's settings (KDE Plasma, GNOME, Cinnamon or Xfce). Or choose **Make Raticode the default browser…**, confirm the dialog, and Raticode runs `xdg-settings set default-web-browser` for you.
3. Choose **Check again** to see the result.

To switch back, pick another browser in the same system settings.

**Settings > Browser > Links from other apps open in** picks the browser for those links: **Raticode (chromium)**, the default, or **Raticode (gecko)**.

- Each link opens in a new tab and brings Raticode's window forward, whether Raticode was closed, minimized or in the background. A link that arrives while Raticode is starting waits until the window has loaded. When Raticode is already running, a second launch hands the link to it and closes.
- With **Raticode (gecko)**, the link opens in Raticode (gecko)'s one tab: when it's already open, the link becomes a new tab inside Firefox's window, and a line under the tab bar says so. See [Raticode (gecko) is one tab](#raticode-gecko-is-one-tab).
- Where Raticode (gecko) can't run, such as on Windows and macOS, without Firefox installed, or before the restart it needs on Linux, the link opens in Raticode (chromium) and a line says why.
- Web pages saved as files always open in Raticode's file preview, which is Raticode (chromium)'s local preview, whichever browser you pick. Rem isn't told about a file opened this way: it doesn't appear in the open files Rem sees. Rem sees a link's tab only if you [share it](#let-rem-use-the-browser), as with any tab.
- A web-page file from another app opens offline with its scripts off, as in the phone's file viewer. It can't load anything from the internet, so it can't tell a website you opened it, and a line above the page says so. Pages that need their scripts or online pictures show without them. A link you click in it still opens in a Browser tab. A link to a file, including one back to the same page or to a file beside it, opens that file offline with its scripts off too, and a link to a folder asks before opening it as a project. It stays offline with its scripts off after Raticode restarts. Opening the file yourself, from recent files or the terminal, shows it as a normal preview again; links in a preview never do.
- Only `http` and `https` links and existing web-page files are accepted. Anything else on the command line is ignored.

On Linux, the system can list Raticode only when it's installed as an app: the `.deb`, `.rpm` or AUR package, or an AppImage added to your apps by your desktop's AppImage integration. A development build or an AppImage run on its own says so in Settings.

## Move the browser to its own window

**Pop out browser** moves the whole Browser plugin, its tab bar and every tab, into a window of its own. Drag it to another screen like any other window.

- To pop it out, click the square-with-an-arrow button at the right end of the tab bar, or right-click a tab and choose **Pop out browser**. With no tabs open, use **Pop out browser into its own window** under the browser buttons.
- Raticode's window then says **The browser is in its own window**, with **Show browser window** and **Bring browser back**.
- To bring it back, click the same tab bar button, choose **Bring browser back** on a tab's menu, or close the browser's window. Every tab comes back to Raticode's window.
- The window opens where you last left it, at the same size. If that screen isn't connected any more, it opens on the screen Raticode is on. If you unplug its screen while it's open, it moves to a screen that's still connected.
- Quitting Raticode closes the browser's window. The next time Raticode starts, the browser is back in Raticode's window.
- If Raticode's window stops responding, closing the browser's window still works after a few seconds. Its pages go back into Raticode's window, and the tab bar follows once Raticode responds again.

Every tab keeps running while the browser changes windows. Pages don't reload, sound and video keep playing, and text you've typed into a page stays.

- **Raticode (chromium)**: the page itself moves to the other window.
- **Raticode (gecko)**: Firefox's window moves with the browser.
- **Firefox**, **Chrome**, **Edge**: the browser keeps running and its tabs show in the new window.

Private tabs stay private, and Rem keeps control of the tabs it opened. In the browser's own window, a site's permission requests show above its page, downloads go to your Downloads folder as usual, and **Developer tools** opens for the tab. While the browser has its own window, the tab showing there counts as on screen for the [media bar](#media-bar): the bar in Raticode's window shows only when the playing tab is behind another tab. Shortcuts for the rest of Raticode only work in Raticode's window.

While a menu, a dialog or a tab you're dragging covers part of a Raticode (chromium) page, the page shows as a still picture. It keeps running underneath, and comes back as soon as the menu closes.

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
- **Share this tab with Rem…** lets Rem work in that tab. For Raticode (gecko), right-click its tab in the tab bar and choose **Share the Gecko tab in front with Rem…** (see [Share a Gecko tab](#share-a-gecko-tab)).
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

### What Rem can do in a tab

In every browser Rem can open, list and close its tabs, go to an address, back, forward and reload, read the page, click, hover, type, press keys, choose from lists, scroll, upload files from the thread's project folders, answer a page's dialogs, wait for text, take a screenshot and run a page script. It can also read and control audio and video: what's playing, the title and artist the page reports, position and volume, and play, pause, seek, volume and mute. Playing or pausing needs no approval; it's the same as pressing the page's play button.

### What Rem reads on a page

When Rem reads a page, it gets what a person looking at the tab could see. Text a page keeps in the page but off the screen is left out:

- text that is fully transparent, faded out with a filter (`filter: opacity(0)`), or inside a part of the page the page has hidden from view (`content-visibility: hidden`)
- text drawn in no colour, or in the same colour as what's behind it, such as white text on a white page
- text covered by an opaque box that moves with it when you scroll
- text in a box squeezed to no width or height, or clipped away completely
- text placed far outside the page, where nobody can scroll to it
- text in a font smaller than 2 pixels

When something is left out, Rem's view of the page says **[hidden text skipped]** once, so Rem knows the page holds more than it shows. Text below the fold or inside a box you can scroll still counts: you can scroll to it. So does text under a header or banner that stays in place while you scroll, light text on a dark box, gradient or outlined text, and text under a see-through tint. When Rem can't tell what's behind some text, such as a picture or a gradient, it reads the text.

Whatever Rem learns from a page comes to Rem marked as content of that page, with the site it came from: the page text, what a page script returns, and also the page's address and title, the message of a dialog the page opens, the titles and artists the page reports for audio and video, a field's value, the names of the options Rem chose and the name of a download Raticode blocked. Rem treats it as information, not as instructions. Only you give Rem instructions, so a page that tells Rem to do something can't direct it. Raticode removes invisible control characters from page text and anything that imitates the marks around it, so a page can't make its text look like it comes from outside the page.

**Ask Rem about this page** (and **Ask Rem about this** on something you right-clicked) works the same way: it leaves out text the page hides, says **[hidden text skipped]** when it did, and puts the page's title, the element's details, your selection and the page text in one block marked as content of that page.

Rem chooses the browser for each tab it opens: your default browser, **Raticode (chromium)**, or **Raticode (gecko)**. A tab stays in the browser it opened in.

Rem's tabs never take your keyboard or come to the front. Rem can take screenshots of its tabs in every browser while they're behind other tabs or the Browser plugin isn't showing.

### Let Rem use Raticode (gecko)

Rem can't use Raticode (gecko) until you turn on **Settings > Browser > Raticode (gecko) > Let Rem use Raticode (gecko)** (Linux). Then:

- Rem's Gecko tabs open in the background in Firefox's own window, in a Firefox container of their own. You see them in Firefox's tab strip, and they also join Raticode's tab bar in the Raticode (gecko) group, where you can watch them, pause Rem and answer its approvals.
- Rem's container starts signed out. It gets only the sign-ins for **Sites where Rem uses your sign-ins**, copied from your Gecko profile. Rem never sees or uses your own Gecko tabs unless you [share one](#share-a-gecko-tab), and your private windows stay out of reach.
- The same approvals, site lists and download setting apply as in other browsers.
- Firefox stops filling in saved passwords as pages load, so a sign-in form in Rem's tabs stays empty. Your own Gecko tabs still offer your saved logins: click the username or password field and pick one from its dropdown. Turning the setting off brings automatic filling back, or whatever you had set yourself before.
- Tabs in the background can play sound once Rem presses play. Firefox normally holds sound in a tab until you've looked at it; this setting turns that hold off. Turning the setting off puts the hold back, or whatever you had set yourself before.
- Turning it on or off restarts Raticode (gecko). Your tabs come back, but anything playing stops.

#### Share a Gecko tab

Raticode (gecko) is Firefox's own window, so its tabs aren't in Raticode's tab bar. To let Rem work in one of them:

1. Turn on **Let Rem use Raticode (gecko)**.
2. Bring the tab you want to the front of Firefox's window.
3. Right-click the Raticode (gecko) tab in the tab bar and choose **Share the Gecko tab in front with Rem…**. A new Rem thread opens with the page attached.

Only regular web pages in Firefox's main window can be shared. Firefox's remote control reports a private window's tabs the same way as regular ones, so Raticode only offers tabs from the window Firefox opened when it started. Private windows never can be shared, and a Gecko profile set to always browse privately can't share at all.

A bar above Firefox's window shows each shared tab. Rem asks there before submitting forms, clicking buttons such as Send or Delete, uploading files or running page scripts, as in any tab you share. Open the bar's Rem button to see what Rem did, pause it, or **Stop sharing**. Sharing also ends when you close the tab or Gecko stops; the tab itself stays open. While shared, the tab keeps your sign-ins, its size, and Firefox's own downloads, file pickers and pop-ups. Approvals show only while the Raticode (gecko) tab is open in the Browser; a request nobody answers is declined after two minutes.

While it's on, Firefox runs with its remote-control port open. Raticode takes that connection as Firefox starts. If another program got there first, the connection fails, or it drops while Firefox keeps running, Raticode closes Firefox so no other program can take control of it, and the Browser says why. Open Raticode (gecko) again to keep browsing. Firefox shows its robot icon in the address bar and tells every website it is remote-controlled. Some sites, including Google's sign-in, refuse to sign in while that's true: turn the setting off to sign in, then turn it back on.

Rem doesn't wait for slow parts of a page, such as an ad frame that never finishes. When Rem opens a page, it carries on once the page's own content has loaded, plus at most about 2 seconds. A click, key press or typing that stays on the same page returns after a fraction of a second. If parts of the page are still loading, Rem is told to read the page again in a moment.

Signing in to a website here doesn't sign in a coding CLI. See [Provider setup](../setup.md) for that.

## Read project reports

Local HTML files open from the File explorer in Browse mode, in the File explorer's own tabs. A previewed page can load files only from the folder it was opened from, so a page can't read other files on your computer, including files in another open preview's folder. A `file://` address that names another computer, such as a network share, never opens. Markdown files have a rendered preview. **Settings > Editor** chooses whether Markdown and HTML open in preview or edit mode; the mode control switches back to source.

*New in Raticode 0.6.0.* A previewed page is offline. It loads nothing from the internet, so it can't send what it read from its folder to a website. That includes connections a page makes without loading anything, such as WebRTC calls and connection hints.

- When a page asks for something online, such as a web font, a chart library or a picture, a bar above it says so. Choose **Load remote content** to reload the page with it. Raticode remembers this for that file, by its full path, and other files stay offline.
- To turn it off again, choose **Block remote content for this file** in the tab's menu.
- Only a link you click opens a website, in a Browser tab. Clicking or typing anywhere else on the page doesn't let the page open a website or a tab, and neither does an automatic redirect.
- A website typed into a preview's address bar opens in a Browser tab.
- Web-page files that another app opened with Raticode never go online and run no scripts, and stay that way when Raticode reopens their tabs after a restart. See [Make Raticode your computer's default browser](#make-raticode-your-computer-s-default-browser).

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
