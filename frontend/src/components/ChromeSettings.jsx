import { useEffect, useState } from "react";

// Settings > Browser > Chrome. Chrome's own settings, extensions and account
// pages can't run in Raticode's tabs, which Chrome draws without a window, so
// each button opens a regular Chrome window on Raticode's Chrome profile.
// Raticode's Chrome tabs pause while that window is open and come back when
// it closes.

const PAGES = [
  ["https://accounts.google.com/", "Sign in to Google", "Your Google account on websites. Your Chrome tabs here come back signed in."],
  ["chrome://settings/syncSetup", "Sign in to Chrome and sync", "Brings bookmarks, passwords, settings and extensions from Chrome on your other devices."],
  ["chrome://settings", "Chrome settings", "Privacy, search engine, appearance, languages and everything else Chrome offers."],
  ["chrome://extensions", "Extensions", "See, turn off and remove extensions."],
  ["https://chromewebstore.google.com/", "Chrome Web Store", "Find and add extensions."],
  ["chrome://password-manager", "Passwords", "Passwords Chrome saved, protected by Chrome."],
  ["chrome://settings/importData", "Import bookmarks and settings", "Chrome's own import from Firefox, Edge, Safari or a bookmarks file."],
];
const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";

function cleanError(cause) {
  return cause instanceof Error ? cause.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "") : String(cause);
}

export default function ChromeSettings() {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(window.goferBrowser?.engineStatus?.())
      .then((result) => { if (!cancelled) setStatus(result?.chrome || { available: false }); })
      .catch(() => { if (!cancelled) setStatus({ available: false }); });
    return () => { cancelled = true; };
  }, []);

  async function run(label, action, options, done) {
    const library = window.goferBrowser?.library;
    if (!library) { setError("Chrome settings are available in the Raticode desktop app."); return; }
    setBusy(label);
    setError("");
    setNote("");
    try {
      const result = await library(action, options);
      if (result?.chrome) setStatus(result.chrome);
      setNote(typeof done === "function" ? done(result) : done);
    } catch (cause) {
      setError(cleanError(cause));
    } finally {
      setBusy("");
    }
  }

  const available = status?.available === true;
  return (
    <div className="grid gap-3" data-testid="chrome-settings">
      <p className="text-[11px] text-muted">
        {!status ? "Looking for Chrome…" : available
          ? `Raticode's Chrome keeps its own profile in ${status.profile}, separate from your everyday Chrome.${status.version ? ` Chrome ${status.version} is running.` : ""}`
          : "Google Chrome was not found. Install Chrome to use it here."}
      </p>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">Open in a Chrome window</p>
        <p className="text-[11px] text-muted">
          Chrome&apos;s own pages can&apos;t appear in a Raticode tab. These open a regular Chrome window that uses Raticode&apos;s Chrome profile. Your Chrome tabs here pause while it&apos;s open and come back when you close it.
        </p>
        <ul className="grid gap-1.5">
          {PAGES.map(([url, label, detail]) => (
            <li key={url} className="flex items-center gap-2">
              <button
                className={BUTTON}
                disabled={!available || Boolean(busy)}
                type="button"
                onClick={() => void run(`Opening ${label}…`, "chrome-sign-in-window", { url }, (result) => (result?.alreadyOpen
                  ? "A Chrome window is already open. Close it to come back to your Chrome tabs."
                  : `${label} opened in a Chrome window. Close the window when you're done.`))}
              >{label}</button>
              <span className="min-w-0 text-[11px] text-muted">{detail}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">Extensions and Rem</p>
        <p className="text-[11px] text-muted">
          Extensions you add in the Chrome window run in your Chrome tabs here. Rem&apos;s Chrome tabs keep their own sign-ins, which start empty each time Chrome starts, and run extensions only where Chrome allows them in private windows. Rem never opens the Chrome window.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          className={BUTTON}
          disabled={!available || Boolean(busy)}
          type="button"
          onClick={() => void run("Restarting Chrome…", "chrome-restart", {}, "Chrome restarted.")}
        >Restart Chrome</button>
        <span className="text-[11px] text-muted">Reloads your Chrome tabs, for example after adding an extension.</span>
      </div>
      {busy ? <p className="text-[11px] text-muted" role="status">{busy}</p> : null}
      {error ? <p className="text-[11px] text-amber-800" role="alert">{error}</p> : null}
      {note && !busy && !error ? <p className="text-[11px] text-muted" role="status">{note}</p> : null}
    </div>
  );
}
