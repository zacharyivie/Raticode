# Integrated browser

> User documentation for this feature is on the website: [Browser](../website/docs/studio/browser.md). The website is the single source of truth for how Raticode works; this file is developer and design detail.

Web tabs live in the **Browser** plugin (`plugins.js`, `pane: false`), a second
`CodeWorkspace` with `variant="browser"` that App mounts once and hides behind other
areas, so pages keep running. Open it with **Open browser** (Ctrl+J by default), from
the plugin rail or from a link. Your web tabs and Rem's tabs share its tab bar; local
HTML previews stay in the File explorer's editor.

Each tab records its browser in `browserTabs[path].engine`: `raticode` (Raticode
(chromium), a webview), `gecko` (Raticode (gecko), Firefox's window placed over the
tab), or `firefox`, `chrome`, `edge` (streamed). The renderer passes it to
`goferBrowser.create({ engine })`; the main process starts that streamed browser for
the tab, and a request without `engine` gets `policy.engine`, as before. Tabs saved
before per-tab browsers get the saved default (`withBrowserTabEngines` in
`lib/browserTabs.js`), and the editor session lists web tabs in `browserPaths` and,
for older builds, in `paths`.

## Profiles

| Content | Profile | Kept after restart |
| --- | --- | --- |
| Your browser tabs | `persist:raticode-browser` | Yes: cookies, storage, cache |
| Rem's tabs | `persist:raticode-rem` | Yes, but signed out except on shared sites |
| Local HTML and PDF previews | `raticode-preview` | No, held in memory |

Typing a local path in the address bar opens the file as a preview tab instead of
loading it in a web tab. Every guest runs sandboxed, with context isolation, no
Node.js and a fixed preload. The desktop refuses to attach a view whose profile does
not match the tab it was created for.

## Private tabs

**New private tab** in the browser menu, or Ctrl+Shift+N with focus in a web tab, opens a tab
marked **Private** in Raticode's own Chromium engine, whichever web browser is chosen in Settings.

- Cookies, storage and cache live in the in-memory `raticode-private` profile, shared by your
  private tabs and separate from your regular tabs, Rem and previews. They are removed when the
  last private tab closes, and don't outlast Raticode.
- The tab isn't restored after a restart or added to **Reopen closed tab**, and its pages aren't
  added to History. Links it opens stay private.
- Site permissions, pop-up and zoom choices made there last only for the private session.
  Your saved ones don't apply there.
- Rem can't be given the tab, **Ask Rem** isn't offered in the menu or on right-click, and the
  phone doesn't see it.
- **What stays:** files you download and bookmarks you add. A download is listed only until the
  last private tab closes. Closing it also stops an unfinished private download and deletes a
  dangerous one still waiting for review. Websites, your network, and the account you sign in to can still see
  what you do. While something plays, the top media bar shows its title, except while the playing tab itself is on screen.

With **Gecko** as the web browser, use Firefox's own **New private window** (Ctrl+Shift+P) in the
Gecko tab. It opens as its own window, outside Raticode.

## Browsing

- **Address bar**: suggestions from history, bookmarks and open tabs, inline
  completion of hosts you visit, and searches with the engine in Settings > Browser.
  The icon at the left shows the connection: a lock for HTTPS, **Not secure** for
  plain HTTP or an accepted certificate error, and an information icon for local
  servers and files. Click it for site information: the certificate, site
  permissions, pop-up and zoom settings, and **Clear cookies and site data**.
- **New tabs and pop-ups**: links with `target=_blank`, middle-clicks and
  Ctrl/Cmd-clicks open tabs. Ctrl/Cmd-click and middle-click open them in the
  background. A sized `window.open`, such as a sign-in pop-up, opens a small window
  that keeps its connection to the page. The window shows the site's address in its
  title and cannot open further windows. Pop-ups that are not the result of a click
  or key press within the last five seconds are blocked. An info bar can open them or
  allow pop-ups for the site. A page can open at most ten tabs in ten seconds.
- **Permissions**: camera, microphone, location, notifications, clipboard reading,
  MIDI and links to apps (such as `mailto:`) are requested in a bar under the toolbar
  with **Allow**, **Allow this time** and **Block**. Decisions are remembered per site
  and can be changed in site information. Copy buttons, full-screen video and pointer
  lock work without asking. USB, serial, HID and Bluetooth devices are always denied.
- **Downloads**: files go to your Downloads folder without overwriting, unless
  **Ask where to save each download** is on. The Downloads button and panel show
  progress, pause, resume, cancel, retry, open and show in folder. Executables,
  scripts, installers and similar types are held for review: choose **Keep anyway**
  or **Discard**. Files a page builds itself, such as a chat exported from Open WebUI
  or ChatGPT (`blob:` and `data:` links), download the same way in every web browser
  choice. In Raticode's own tabs, right-click a link and choose **Save link as…** to
  pick where it goes;
  for a `blob:` or `data:` link that is the only choice, since the file exists only
  in that page.
- **Errors**: offline, DNS, timeout, refused-connection and blocked pages explain the
  problem and offer **Retry**. Certificate errors show a warning page with **Back to
  safety**. You can continue to that site for the rest of the session; the
  exception covers only that certificate. Crashed pages offer **Reload**. Pages that
  stop responding offer **Wait** or **Reload**. Leaving a page with unsaved changes
  asks first.
- **History and bookmarks**: the browser menu opens History, Bookmarks, Downloads and
  Site data. History records your web tabs, not Rem's or previews, keeps up to 10,000
  pages and can be cleared for the last hour, day, week, 4 weeks or all time.
  **Clear browsing data** also clears cookies and site data (always for all time),
  cached files, the downloads list and site settings. You can clear either your
  profile or Rem's. Press and hold, or right-click, Back or Forward to jump through
  the tab's history.
- **Tabs**: Ctrl+Shift+T reopens the last closed tab from anywhere in the studio,
  as does **Reopen closed tab** in the browser menu. Restored tabs keep their
  back/forward list and scroll positions where Chromium allows restoring them;
  otherwise they reopen at the page they last showed. A tab playing audio shows a
  speaker that mutes it. Tabs you have not viewed for 30 minutes pause to free memory
  and reload when you return. Tabs playing audio, waiting on a prompt or controlled
  by Rem are kept. Change the delay in Settings > Browser; 0 turns this off.
  Firefox, Chrome and Edge tabs check every few seconds whether their page plays an
  audio or video element with its volume up, so they show the speaker and stay loaded
  too. A muted tab that is playing counts. Sound from a player a page keeps outside
  its document, from Web Audio or from inside a frame isn't seen there, and such a
  tab can still be paused after the delay.
- **Page tools**: per-site zoom (Ctrl+= / Ctrl+- / Ctrl+0 or Ctrl+wheel), separate
  from the studio's own text size, with a reset badge in the address bar. Also Find in
  Page, Print, Save as PDF, Save page as, DevTools (F12) and Hard reload. If the
  computer has no printers (for example, no CUPS on Linux), Print offers Save as PDF
  instead of opening the print dialog. Save as PDF shows the file in its folder when
  it finishes and explains any failure. HTTP sign-in
  prompts appear in the tab; credentials are never saved. Spell checking and its
  suggestions are available in text fields.
- **Context menu**: open links in a new tab or your default browser, copy link
  addresses, save or copy images, search for selected text, and **Ask Rem about this**
  (the page, or the selected text, as a new Rem thread). Ask Rem also works on local
  HTML and PDF previews; as for a web page, Rem gets the open file's address and title,
  plus the selection and page text where the page provides them, and no other access
  to your files.

### Keyboard shortcuts

These apply while focus is in a page or the browser toolbar. On macOS, use Cmd
instead of Ctrl.

| Keys | Action |
| --- | --- |
| Ctrl+L, Alt+D | Focus the address bar |
| F5, Ctrl+R | Reload |
| Ctrl+Shift+R, Shift+F5 | Reload, bypassing the cache |
| Esc | Stop loading |
| Alt+Left / Alt+Right | Back / Forward |
| Alt+Home | Home page |
| Ctrl+F | Find in page |
| Ctrl+D | Bookmark the page |
| Ctrl+H (Cmd+Y on macOS) | History |
| Ctrl+P | Print |
| F12, Ctrl+Shift+I | DevTools |
| Ctrl+Shift+T | Reopen closed tab |
| Ctrl+W, Ctrl+Tab | Close, cycle tabs |

Ctrl+L toggles the Rem pane everywhere else in the studio. Ctrl+1–9 keep their studio
meaning (graph and code views) instead of selecting tabs.

## Security notes

- Only real user input counts. The page preload ignores events a script dispatches
  itself, so pages cannot fake clicks to open tabs, zoom or navigate back.
- Pages can only navigate to web, `about:` and `blob:` URLs. Previews can also reach
  local files, which open through the studio's own path checks.
- The browser engine is the Chromium build in Raticode's Electron version. Keep
  Raticode updated: Electron supports only its three newest major versions, and
  older ones stop receiving Chromium security fixes.
- Tabs identify as the Chromium they run on, using Chrome's reduced user agent
  without Electron or Raticode tokens. Electron does not send `Sec-CH-UA` client
  hint headers itself, so Raticode adds them with the values page scripts see.
  Sites that ask with `Accept-CH` get detailed hints such as the full version and
  CPU architecture, and only while they are the top-level site. `Critical-CH`
  replays a page load once with the hints it needs. Clearing a site's data also
  forgets its hint requests.
- Google's sign-in (`accounts.google.*` and `accounts.youtube.com`) rejects a
  Chrome user agent from a browser that lacks Chrome's internals, with "This
  browser or app may not be secure". Requests to those hosts carry Electron's own
  user agent instead, which Google accepts. WhatsApp Web and Microsoft Teams reject
  that user agent, so other sites never see it.
- Rem's access is described in [Rem's browser](rem.md#rems-browser).

## Firefox

Settings > Browser > **Web browser** can be Raticode or Firefox. With Firefox, web tabs and
Rem's tabs run in a Firefox that Raticode starts without a window, in its own profile
(`firefox-profile` in Raticode's data folder). The tab shows the page as a stream of
pictures and passes your clicks, scrolling and typing back. Local files and previews stay
on Raticode's engine. Your everyday Firefox and its profiles are never opened or changed.

Firefox's own pages (`about:preferences`, `about:addons`, `about:config`) and extension
pages can't be shown in a tab: Firefox offers them to remote control only when that control may
also run programs as you, which Raticode doesn't allow. They can be opened in a Firefox
window (see below). **Settings > Browser > Firefox** covers the common cases in Raticode:

- **Settings**: common Firefox settings (HTTPS-Only Mode, tracking protection, cookies,
  DNS over HTTPS, proxy, autoplay, fonts, languages, website appearance) and any other
  preference by name. They apply when Firefox restarts; **Restart Firefox** reloads its tabs.
  Settings Raticode needs while Firefox has no window, such as declining permission
  prompts it can't show, stay fixed and are listed with the reason.
- **Extensions**: search Firefox Add-ons, add from a `.xpi` file, and remove. Each install
  shows the extension's permissions in a Raticode dialog first. Files come only from
  Mozilla's servers and must match Mozilla's published hash; Firefox itself refuses
  extensions Mozilla hasn't signed. On an addons.mozilla.org page, a bar offers the
  install, because Firefox's own Add button can't open its prompt. Extensions that work
  on pages, such as ad blockers, run normally, in Rem's tabs too. Their toolbar buttons,
  pop-ups and options pages can't appear.
- **Import**: copy settings, bookmarks and history, site permissions and zoom, extensions
  with their settings, and optionally website sign-ins from one of your Firefox profiles.
  Raticode only reads the profile you pick. Its databases are copied only while that
  Firefox is closed: on Linux Raticode checks Firefox's lock; elsewhere you confirm it.
  Anything an import replaces is kept in `firefox-profile-backups`. Saved passwords and
  your Firefox account aren't imported. Sign-ins and site permissions come only from that
  Firefox's normal tabs, never from its other containers. Imported site permissions and
  zoom levels apply in every Firefox tab in Raticode, Rem's included. **Reset Raticode's Firefox** moves the profile to
  the same backup folder.

### Signing in where a site refuses remote control

While Raticode controls Firefox, Firefox tells every page it is automated
(`navigator.webdriver` is true), and no setting turns that off. Google's sign-in then
stops with "Couldn't sign you in · This browser or app may not be secure". Other sites
may refuse in a similar way.

When Google refuses, the tab shows a bar with **Sign in in a Firefox window**. The tab
menu has the same item for any of your Firefox tabs. It opens a regular Firefox window on
Raticode's own Firefox profile with no remote control, at the page you were signing in to.
Sign in there and close the window. Your Firefox tabs in Raticode pause while the window is
open, because a profile can be used by one Firefox at a time. They reload when the window
closes, and the sign-in carries over to them. Rem's tabs keep their separate container
and stay signed out. Rem can't open this window.

**Settings > Browser > Firefox > Firefox in a window** opens the same window at a sign-in
page or at Firefox's own pages: settings, add-ons, and Firefox account and Sync. Changes
made there are kept, except that Raticode's fixed settings apply again the next time it
starts Firefox. The window is a normal desktop window, so it appears on your screen and
takes focus.

Google may still check a session again later, for example when a sign-in needs to be
confirmed. If a tab is refused again, sign in from the window again.

Rem's Firefox tabs use a separate container that starts signed out each time Firefox
starts, except on sites you share with Rem: Raticode clears its cookies when Firefox
creates it, and new containers are numbered above any an import brought in. Rem can't install extensions or change Firefox
settings. Firefox fills saved logins in every container, so automatic login filling is off.
Known gaps in Firefox tabs that Chrome and Edge tabs don't have: Rem's snapshot doesn't
reach into frames from the same site, and right-clicking shows no Raticode menu.

While Rem is using a Firefox tab, its own or one you shared, Rem's tools refuse Firefox's own error and warning pages (`about:certerror`, `about:neterror`, `about:blocked`, `about:httpsonlyerror`). Before this, Rem could click **Advanced** and then **Accept the Risk and Continue** on a certificate warning. Rem can still go back or open another page, and you can still decide in your own tab.

## Chrome

Settings > Browser > **Web browser** can also be Chrome. With Chrome, web tabs and Rem's
tabs run in a Google Chrome that Raticode starts without a window (headless), in its own
profile folder in Raticode's data folder (`chrome-profile`, readable only by you). Tabs work
as they do with Firefox: the page streams into the tab, your clicks, scrolling and typing go
back to Chrome, and Rem's browser tools, site rules, approvals and pause apply unchanged.
Previews and local files stay on Raticode's engine. Your everyday Chrome and its profiles are
never opened or changed.

Raticode finds Chrome in the usual install folders (`/opt/google/chrome`, `google-chrome` on
the `PATH`, `/Applications/Google Chrome.app`, `Program Files\Google\Chrome` or
`%LOCALAPPDATA%\Google\Chrome`). `RATICODE_CHROME_PATH` names another binary. Chromium, and
Chrome from Flatpak or Snap, are not looked for.

Raticode controls Chrome through a private pipe (`--remote-debugging-pipe`), not a network
port, so no other program on the computer can connect to it. Chrome closes when that pipe
closes, including when Raticode itself is killed. Chrome keeps its own protections, such
as Safe Browsing and its certificate checks; a certificate error shows Chrome's own warning
page in the tab. Pages that ask for your location, camera, microphone or notifications are
refused, because a windowless Chrome can't ask you.

On Linux both the windowless Chrome and the Chrome window below keep the key that protects
saved cookies in the profile folder (`--password-store=basic`) rather than the desktop's
keyring, which a windowless Chrome can't reach. Otherwise a sign-in made in the window
couldn't be read by Raticode's tabs.

Chrome's own pages (`chrome://settings`, `chrome://extensions` and the rest) can't be opened in
a Raticode tab, because they can change how Chrome runs. **Settings > Browser > Chrome** opens
them in a regular Chrome window on Raticode's Chrome profile: Sign in to Google, Sign in to
Chrome and sync, Chrome settings, Extensions, the Chrome Web Store, Passwords, and Chrome's own
Import bookmarks and settings. Raticode has no extension manager or profile import of its
own for Chrome; Chrome Sync and Chrome's importer bring bookmarks, passwords, settings and
extensions instead. **Restart Chrome** reloads your Chrome tabs.

### Signing in where a site refuses remote control

A windowless Chrome under Raticode's control tells every page it is automated
(`navigator.webdriver` is true), and Google's sign-in then refuses, as it does for Firefox.
The tab shows **Sign in in a Chrome window**, and the tab menu has the same item. It stops
Raticode's Chrome and opens a regular Chrome window on the same profile with no remote
control, where pages see an ordinary Chrome. Sign in there and close the window; your Chrome
tabs pause while it's open (Chrome allows one browser per profile) and reload signed in
when it closes. Rem can't open the window. The window is a normal desktop window, so it
appears on your screen and takes focus.

Rem's Chrome tabs run in a separate browser context that keeps nothing on disk and starts
signed out each time Chrome starts, except on sites you share with Rem, whose cookies are
copied in. Chrome runs extensions there only where you allowed them in private windows.
Rem can't open Chrome's own pages or change Chrome settings. While Rem is using a Chrome or Edge tab, Rem's tools refuse Chrome's own error and warning pages, so Rem can't click past a certificate warning ("Proceed to … (unsafe)"); it can go back or open another page. You can still decide in your own tab.

Right-clicking a Chrome or Edge tab opens Raticode's menu, as in Raticode's own tabs, unless the
page shows a menu of its own: open or copy a link, open or copy an image's address, Undo, Redo,
Cut, Copy, Paste and Select all in text fields (they act in the page and use the system
clipboard), search for the selected text, Ask Rem, Back, Forward, Reload, bookmarks, Save page
as and Save as PDF. **Save page as** saves one `.mhtml` file with the page's pictures and styles.
Save image as, Copy image, Inspect and printing on paper aren't offered there; Save as PDF
replaces printing. Redo after Paste may not put the pasted text back.

Limits of Chrome tabs: Chrome's toolbar, extension pop-ups, its built-in password filling
prompts and other parts of Chrome's own window don't exist without a window; date and
color pickers don't open; pages that open a new window get a new Raticode tab instead, so
sign-in pop-ups that talk back to the page that opened them may not finish (sign in in the
Chrome window instead); prompts that ask you to type an answer send the page's suggested
answer, as with Firefox.

## Microsoft Edge

Settings > Browser > **Web browser** can also be Edge. Edge is built on Chromium, so it runs
on the same windowless engine as Chrome mode (`chromium-engine.cjs`, `chromium-contents.cjs`):
web tabs and Rem's tabs run in a Microsoft Edge that Raticode starts without a window, in its
own profile (`edge-profile` in Raticode's data folder), and everything under [Chrome](#chrome)
about streaming, input, Rem's tools, site rules, approvals, downloads and the private DevTools
pipe applies to Edge as well. Your everyday Edge and its profiles are never opened or changed.

Raticode finds Edge in the usual install folders, release first and then Beta, Dev and Canary:
`/opt/microsoft/msedge`, `microsoft-edge` or `microsoft-edge-stable` on the `PATH`,
`/Applications/Microsoft Edge.app`, or `Microsoft\Edge\Application\msedge.exe` under
`Program Files (x86)`, `Program Files` or `%LOCALAPPDATA%`. `RATICODE_EDGE_PATH` names another
binary. Edge from Flatpak is not looked for.

Edge's own pages (`edge://settings`, `edge://extensions` and the rest, including the
`chrome://` names Edge also answers to) can't be opened in a Raticode tab. **Settings > Browser >
Edge** opens them in a regular Edge window on Raticode's Edge profile, with no remote control:

- **Sign in to Microsoft**: your Microsoft work, school or personal account on websites. Use it
  for sign-ins that need a device check, such as company sign-ins that ask for Okta Verify or
  Microsoft Authenticator on this computer, which a windowless Edge can't ask you to allow.
- **Sign in to Google**, for Google, which can refuse while Raticode controls Edge.
- **Sign in to Edge and sync**: your Microsoft account in Edge, which brings favorites,
  passwords, settings and extensions from Edge on your other devices.
- **Edge settings**, **Extensions**, **Edge Add-ons**, **Passwords**, and **Import from other
  browsers** (Edge's own import from Chrome, Firefox or a file).

Your Edge tabs pause while that window is open (Edge allows one browser per profile) and reload
signed in when you close it. **Restart Edge** reloads your Edge tabs, for example after adding an
extension. Like Chrome, a windowless Edge under remote control tells pages it is automated, and a
tab Google refuses shows **Sign in in an Edge window**. Rem can't open the Edge window.

Rem's Edge tabs run in a separate browser context that keeps nothing on disk and starts signed
out each time Edge starts, except on sites you share with Rem, whose cookies are copied in. Rem
can't open Edge's own pages or change Edge settings.

Limits of Edge tabs are those of Chrome tabs. Edge's sidebar, Copilot, Collections, vertical tabs,
extension pop-ups, developer tools and other parts of Edge's own window don't exist without a
window. On Linux the profile keeps its cookie key in the profile folder (`--password-store=basic`)
rather than the desktop keyring, as with Chrome.

What was checked against a real Microsoft Edge (157.0.4341.42 for Linux, unpacked from
Microsoft's package without installing it) on November 7, 2026: Edge starts without a window over
the private pipe with Raticode's own profile, every Rem and worker browser tool action
(`stream-agent-actions.test.cjs`, `stream-agent-guards.test.cjs`), Rem's site rules, refused
downloads and separate signed-out context, Edge's certificate warning, shared tabs, the right-click
menu and its editing commands, Save page as, sign-ins kept across restarts, and Edge stopping when
Raticode dies. To repeat that, set `RATICODE_EDGE_PATH` to an Edge binary; without one the tests
use a Chrome for Testing build. Real Microsoft, Google and Okta sign-ins, the Edge window on a
desktop, and Edge on macOS and Windows are unchecked.

## Paired phone

With **Use Raticode from this phone** on in Paired devices, the phone's own browser lists this window's
web tabs, yours and Rem's, and can open one of them on the phone. It gets each tab's title and address,
never its page, cookies or sign-ins; previews and local files are left out. The list says whether
Raticode's engine, Firefox, Chrome or Edge runs them, and the phone's More › Desktop settings can switch **Web browser**
between them; no other browser setting is shared with the phone. **Open on your computer** on
the phone opens a page here in a new background tab of your profile, without switching to it. The phone
can't drive tabs, use Rem's browser tools or change browser settings. See
[the phone browser](mobile-desktop-parity.md#the-browser-on-the-phone-november-6-2026).

## Known limitations

- Password managers and Chrome extensions are not supported in Raticode's engine. Use
  **Open in default browser** for sites that need them, or choose Firefox, Chrome or Edge as
  the web browser for their extensions.
- Electron clears cookies and site storage for all time; time ranges apply to
  history only.
- Some identity providers block embedded browsers regardless of the user agent. Sign
  in with **Open in default browser** when that happens. Google may still ask for a
  CAPTCHA or extra verification in a new profile.
- There is no Safe Browsing phishing and malware check, and no automatic
  certificate revocation list updates. Electron does not include either.

## Gecko

**Raticode (gecko)** (stored as `gecko`) is a second built-in browser on Linux, chosen per tab from the Browser plugin's **New tab in** menu or as **Settings > Browser > Default browser for new tabs**. The Browser plugin keeps at most one Gecko tab, because every Gecko tab would show the same window; further Gecko pages open as Firefox tabs inside it (`pendingUrls` on the tab). Firefox's real window sits inside the Raticode tab. It isn't streamed. Tabs, the address bar, settings, bookmarks, extensions and the Mozilla account are Firefox's own, in a profile under Raticode's data folder (`gecko/profiles/`). Raticode opens no control port and can't read pages or passwords.

- **Requirements:** Linux with an X11 display and Firefox installed. Gecko can't be used on macOS (no supported way to place another program's window) and hasn't been built for Windows.
- **Wayland desktops:** the window can only be placed over X11, so Raticode restarts through XWayland while Gecko is chosen. Text can look softer under fractional scaling, and other X11 programs on the desktop can observe X11 windows.
- **Tabs:** every Gecko tab in Raticode shows the one Firefox window. Links from Raticode open as new Firefox tabs. A second Firefox window (Ctrl+N) opens as an ordinary window.
- **Rem and previews:** Rem's tabs, local previews and the phone's view of this computer stay on Raticode's own browser.
- **Menus over the tab:** Firefox's window hides while a Raticode menu or dialog covers the tab, because a native window always draws above Raticode's page.
- **Importing:** Settings > Browser > Gecko imports settings, bookmarks and history, cookies, site permissions, extensions and extension data from a local Firefox profile, which is only read. For passwords, sign in to the Mozilla account inside Gecko and turn on Sync.

The code is in `frontend/electron/gecko-window.cjs`, `gecko-display.cjs` and `x11-client.cjs`.
