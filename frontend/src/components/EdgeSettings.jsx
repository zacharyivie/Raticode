import { useEffect, useState } from "react";

// Settings > Browser > Edge. Edge's own settings, extensions and Microsoft
// account pages can't run in Raticode's tabs, which Edge draws without a
// window, so each button opens a regular Edge window on Raticode's Edge
// profile. Raticode's Edge tabs pause while that window is open and come back
// when it closes.

const PAGES = [
  ["https://login.microsoftonline.com/", "Sign in to Microsoft", "Your Microsoft work, school or personal account on websites, including sign-ins that need Edge or a device check. Your Edge tabs here come back signed in."],
  ["https://accounts.google.com/", "Sign in to Google", "Google can refuse to sign in while Raticode controls Edge; sign in here instead."],
  ["edge://settings/profiles", "Sign in to Edge and sync", "Your Microsoft account in Edge: favorites, passwords, settings and extensions from Edge on your other devices."],
  ["edge://settings", "Edge settings", "Privacy, search engine, appearance, languages and everything else Edge offers."],
  ["edge://extensions", "Extensions", "See, turn off and remove extensions, or allow extensions from other stores."],
  ["https://microsoftedge.microsoft.com/addons/Microsoft-Edge-Extensions-Home", "Edge Add-ons", "Find and add extensions."],
  ["edge://wallet/passwords", "Passwords", "Passwords Edge saved, protected by Edge."],
  ["edge://settings/profiles/importBrowsingData", "Import from other browsers", "Edge's own import of favorites, passwords and history from Chrome, Firefox or a file."],
];
const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";

function cleanError(cause) {
  return cause instanceof Error ? cause.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "") : String(cause);
}

export default function EdgeSettings() {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(window.goferBrowser?.engineStatus?.())
      .then((result) => { if (!cancelled) setStatus(result?.edge || { available: false }); })
      .catch(() => { if (!cancelled) setStatus({ available: false }); });
    return () => { cancelled = true; };
  }, []);

  async function run(label, action, options, done) {
    const library = window.goferBrowser?.library;
    if (!library) { setError("Edge settings are available in the Raticode desktop app."); return; }
    setBusy(label);
    setError("");
    setNote("");
    try {
      const result = await library(action, options);
      if (result?.edge) setStatus(result.edge);
      setNote(typeof done === "function" ? done(result) : done);
    } catch (cause) {
      setError(cleanError(cause));
    } finally {
      setBusy("");
    }
  }

  const available = status?.available === true;
  return (
    <div className="grid gap-3" data-testid="edge-settings">
      <p className="text-[11px] text-muted">
        {!status ? "Looking for Microsoft Edge…" : available
          ? `Raticode's Edge keeps its own profile in ${status.profile}, separate from your everyday Edge.${status.version ? ` Edge ${status.version} is running.` : ""}`
          : "Microsoft Edge was not found. Install Edge to use it here."}
      </p>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">Open in an Edge window</p>
        <p className="text-[11px] text-muted">
          Edge&apos;s own pages can&apos;t appear in a Raticode tab. These open a regular Edge window that uses Raticode&apos;s Edge profile. Your Edge tabs here pause while it&apos;s open and come back when you close it.
        </p>
        <ul className="grid gap-1.5">
          {PAGES.map(([url, label, detail]) => (
            <li key={url} className="flex items-center gap-2">
              <button
                className={BUTTON}
                disabled={!available || Boolean(busy)}
                type="button"
                onClick={() => void run(`Opening ${label}…`, "edge-sign-in-window", { url }, (result) => (result?.alreadyOpen
                  ? "An Edge window is already open. Close it to come back to your Edge tabs."
                  : `${label} opened in an Edge window. Close the window when you're done.`))}
              >{label}</button>
              <span className="min-w-0 text-[11px] text-muted">{detail}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="grid gap-1.5">
        <p className="text-[11px] font-semibold text-ink">Extensions and Rem</p>
        <p className="text-[11px] text-muted">
          Extensions you add in the Edge window run in your Edge tabs here. Rem&apos;s Edge tabs keep their own sign-ins, which start empty each time Edge starts, and run extensions only where Edge allows them in InPrivate windows. Rem never opens the Edge window.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          className={BUTTON}
          disabled={!available || Boolean(busy)}
          type="button"
          onClick={() => void run("Restarting Edge…", "edge-restart", {}, "Edge restarted.")}
        >Restart Edge</button>
        <span className="text-[11px] text-muted">Reloads your Edge tabs, for example after adding an extension.</span>
      </div>
      {busy ? <p className="text-[11px] text-muted" role="status">{busy}</p> : null}
      {error ? <p className="text-[11px] text-amber-800" role="alert">{error}</p> : null}
      {note && !busy && !error ? <p className="text-[11px] text-muted" role="status">{note}</p> : null}
    </div>
  );
}
