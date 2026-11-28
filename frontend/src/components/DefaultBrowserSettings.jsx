import { useCallback, useEffect, useState } from "react";
import { ipcErrorMessage } from "../lib/ipcError.js";

// Settings > Browser > Default web browser. Shows whether the computer opens
// links in Raticode and takes the user to the system's own default-apps
// settings. Windows and macOS only allow the change there. On Linux, Make
// Raticode the default browser asks again in a dialog from the main process
// before it runs xdg-settings. Nothing here changes a default by itself.

const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";

const SETTINGS_LABELS = {
  win32: "Open Windows default apps",
  darwin: "Open macOS Desktop & Dock settings",
  linux: "Open system default apps",
};

const HOW_TO = {
  win32: "In Windows Settings, choose Raticode under Default apps, then Set default.",
  darwin: "In System Settings, set Default web browser to Raticode. macOS may ask you to confirm.",
  linux: "Choose Raticode as the web browser in your desktop's default-apps settings, or use Make Raticode the default browser.",
};

function defaultBrowserSummary(status) {
  if (!status) return "Checking…";
  if (status.problem) return status.problem;
  if (status.isDefault === true) return "Raticode is the default web browser on this computer.";
  if (status.isDefault === false) return "Raticode isn't the default web browser on this computer.";
  return "Raticode can't tell which browser is the default on this computer.";
}

export default function DefaultBrowserSettings() {
  const bridge = window.goferBrowser?.defaultBrowser;
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!bridge) return;
    try { setStatus(await bridge("status")); } catch (cause) { setError(ipcErrorMessage(cause)); }
  }, [bridge]);

  useEffect(() => { void refresh(); }, [refresh]);

  if (!bridge) return <p className="text-[11px] text-muted">Only Raticode&apos;s desktop app can be the computer&apos;s web browser.</p>;

  async function run(work) {
    setBusy(true);
    setError("");
    setNote("");
    try { await work(); } catch (cause) { setError(ipcErrorMessage(cause)); } finally { setBusy(false); }
  }

  const openSettings = () => run(async () => {
    const result = await bridge("open-settings");
    if (result?.opened) setNote(`${HOW_TO[status?.platform] || ""} Then come back and choose Check again.`.trim());
    else setError(result?.problem || "Couldn't open the system's settings.");
  });

  const makeDefault = () => run(async () => {
    const result = await bridge("make-default");
    if (result?.changed) {
      setStatus(result.status || null);
      setNote("Done. Links from other apps now open in Raticode.");
    } else if (!result?.cancelled) setError(result?.problem || "The default browser didn't change.");
  });

  return (
    <div className="grid gap-2" data-testid="default-browser-settings">
      <p role="status" className={`text-[11px] ${status?.problem ? "text-amber-700" : "text-muted"}`}>{defaultBrowserSummary(status)}</p>
      <div className="flex flex-wrap gap-2">
        {status?.canOpenSettings ? (
          <button type="button" className={BUTTON} disabled={busy} onClick={() => void openSettings()}>{SETTINGS_LABELS[status.platform] || "Open system default apps"}</button>
        ) : null}
        {status?.canMakeDefault && status.isDefault !== true ? (
          <button type="button" className={BUTTON} disabled={busy} onClick={() => void makeDefault()}>Make Raticode the default browser…</button>
        ) : null}
        <button type="button" className={BUTTON} disabled={busy} onClick={() => void run(refresh)}>Check again</button>
      </div>
      {note ? <p className="text-[11px] text-muted">{note}</p> : null}
      {error ? <p role="alert" className="text-[11px] text-red-700">{error}</p> : null}
    </div>
  );
}
