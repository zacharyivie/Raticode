# Integrated browser

Raticode's editor area includes a Chromium browser. Open it with **Open browser**
(Ctrl+J by default) or from a link. Web pages, local HTML previews and Rem's tabs
appear in the same tab strip.

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
  or **Discard**.
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
  (the page, or the selected text, as a new Rem thread).

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

## Known limitations

- Password managers and Chrome extensions are not supported. Use **Open in default
  browser** for sites that need them.
- Electron clears cookies and site storage for all time; time ranges apply to
  history only.
- Some identity providers block embedded browsers regardless of the user agent. Sign
  in with **Open in default browser** when that happens. Google may still ask for a
  CAPTCHA or extra verification in a new profile.
- There is no Safe Browsing phishing and malware check, and no automatic
  certificate revocation list updates. Electron does not include either.
