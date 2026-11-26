import { useEffect, useState } from "react";
import { ipcErrorMessage } from "../lib/ipcError.js";

// Settings > Browser > Chrome and > Edge. The browser's own settings,
// extensions and account pages can't run in Raticode's tabs, which the
// browser draws without a window, so each button opens a regular browser
// window on Raticode's profile for that browser. Raticode's tabs in that
// browser pause while the window is open and come back when it closes.

const CHROME_PAGES = [
  ["https://accounts.google.com/", "Sign in to Google", "Your Google account on websites. Your Chrome tabs here come back signed in."],
  ["chrome://settings/syncSetup", "Sign in to Chrome and sync", "Brings bookmarks, passwords, settings and extensions from Chrome on your other devices."],
  ["chrome://settings", "Chrome settings", "Privacy, search engine, appearance, languages and everything else Chrome offers."],
  ["chrome://extensions", "Extensions", "See, turn off and remove extensions."],
  ["https://chromewebstore.google.com/", "Chrome Web Store", "Find and add extensions."],
  ["chrome://password-manager", "Passwords", "Passwords Chrome saved, protected by Chrome."],
  ["chrome://settings/importData", "Import bookmarks and settings", "Chrome's own import from Firefox, Edge, Safari or a bookmarks file."],
];

const EDGE_PAGES = [
  ["https://login.microsoftonline.com/", "Sign in to Microsoft", "Your Microsoft work, school or personal account on websites, including sign-ins that need Edge or a device check. Your Edge tabs here come back signed in."],
  ["https://accounts.google.com/", "Sign in to Google", "Google can refuse to sign in while Raticode controls Edge; sign in here instead."],
  ["edge://settings/profiles", "Sign in to Edge and sync", "Your Microsoft account in Edge: favorites, passwords, settings and extensions from Edge on your other devices."],
  ["edge://settings", "Edge settings", "Privacy, search engine, appearance, languages and everything else Edge offers."],
  ["edge://extensions", "Extensions", "See, turn off and remove extensions, or allow extensions from other stores."],
  ["https://microsoftedge.microsoft.com/addons/Microsoft-Edge-Extensions-Home", "Edge Add-ons", "Find and add extensions."],
  ["edge://wallet/passwords", "Passwords", "Passwords Edge saved, protected by Edge."],
  ["edge://settings/profiles/importBrowsingData", "Import from other browsers", "Edge's own import of favorites, passwords and history from Chrome, Firefox or a file."],
];

// key is the engine's key in engineStatus() and the prefix of its library
// actions; a window is the name with its article, as in "Open in a Chrome window".
export const CHROMIUM_BROWSERS = {
  chrome: { key: "chrome", name: "Chrome", fullName: "Google Chrome", window: "a Chrome window", privateWindows: "private windows", pages: CHROME_PAGES },
  edge: { key: "edge", name: "Edge", fullName: "Microsoft Edge", window: "an Edge window", privateWindows: "InPrivate windows", pages: EDGE_PAGES },
};

const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";

function capitalized(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export default function ChromiumBrowserSettings({ browser }) {
  const { key, name, fullName, window: browserWindow, privateWindows, pages } = browser;
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(window.goferBrowser?.engineStatus?.())
      .then((result) => { if (!cancelled) setStatus(result?.[key] || { available: false }); })
      .catch(() => { if (!cancelled) setStatus({ available: false }); });
    return () => { cancelled = true; };
  }, [key]);

  async function run(label, action, options, done) {
    const library = window.goferBrowser?.library;
    if (!library) { setError(`${name} settings are available in the Raticode desktop app.`); return; }
    setBusy(label);
    setError("");
    setNote("");
    try {
      const result = await library(action, options);
      if (result?.[key]) setStatus(result[key]);
      setNote(typeof done === "function" ? done(result) : done);
    } catch (cause) {
      setError(ipcErrorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  const available = status?.available === true;
  return (
    <div className="grid gap-3" data-testid={`${key}-settings`}>
      <p className="text-[11px] text-muted">
        {!status ? `Looking for ${fullName}…` : available
          ? `Raticode's ${name} keeps its own profile in ${status.profile}, separate from your everyday ${name}.${status.version ? ` ${name} ${status.version} is running.` : ""}`
          : `${fullName} was not found. Install ${name} to use it here.`}
      </p>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">{`Open in ${browserWindow}`}</p>
        <p className="text-[11px] text-muted">
          {`${name}'s own pages can't appear in a Raticode tab. These open a regular ${name} window that uses Raticode's ${name} profile. Your ${name} tabs here pause while it's open and come back when you close it.`}
        </p>
        <ul className="grid gap-1.5">
          {pages.map(([url, label, detail]) => (
            <li key={url} className="flex items-center gap-2">
              <button
                className={BUTTON}
                disabled={!available || Boolean(busy)}
                type="button"
                onClick={() => void run(`Opening ${label}…`, `${key}-sign-in-window`, { url }, (result) => (result?.alreadyOpen
                  ? `${capitalized(browserWindow)} is already open. Close it to come back to your ${name} tabs.`
                  : `${label} opened in ${browserWindow}. Close the window when you're done.`))}
              >{label}</button>
              <span className="min-w-0 text-[11px] text-muted">{detail}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">Extensions and Rem</p>
        <p className="text-[11px] text-muted">
          {`Extensions you add in the ${name} window run in your ${name} tabs here. Rem's ${name} tabs keep their own sign-ins, which start empty each time ${name} starts, and run extensions only where ${name} allows them in ${privateWindows}. Rem never opens the ${name} window.`}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          className={BUTTON}
          disabled={!available || Boolean(busy)}
          type="button"
          onClick={() => void run(`Restarting ${name}…`, `${key}-restart`, {}, `${name} restarted.`)}
        >{`Restart ${name}`}</button>
        <span className="text-[11px] text-muted">{`Reloads your ${name} tabs, for example after adding an extension.`}</span>
      </div>
      {busy ? <p className="text-[11px] text-muted" role="status">{busy}</p> : null}
      {error ? <p className="text-[11px] text-amber-800" role="alert">{error}</p> : null}
      {note && !busy && !error ? <p className="text-[11px] text-muted" role="status">{note}</p> : null}
    </div>
  );
}
