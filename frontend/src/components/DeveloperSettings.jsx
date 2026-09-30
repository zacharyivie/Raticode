import { useEffect, useState } from "react";
import ReportThemeSettings from "./ReportThemeSettings.jsx";

export default function DeveloperSettings() {
  const [info, setInfo] = useState(null);
  const [log, setLog] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const bridge = window.goferDesktop?.developer;
  useEffect(() => { if (bridge) void bridge.info().then(setInfo).catch((cause) => setError(cause.message)); }, [bridge]);
  async function act(action) {
    setError(""); setBusy(true);
    try {
      if (action === "copy") await navigator.clipboard.writeText(JSON.stringify(await bridge.info(), null, 2));
      else { const result = await bridge.action(action); if (action === "read-log") setLog(result); else if (typeof result === "string" && result) throw new Error(result); }
      setInfo(await bridge.info());
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }
  if (!bridge) return <p className="py-3 text-xs text-muted">Developer diagnostics are available in the desktop app.</p>;
  return <div className="space-y-4 py-3">
    <p className="text-xs text-muted">The app log records desktop, backend, and renderer errors. It keeps up to four 5 MB files. Conversation content belongs in Rem&apos;s archive.</p>
    {info ? <dl className="space-y-2 text-xs">{[["Version", info.version], ["Platform", info.platform], ["Electron / Node", `${info.electron} / ${info.node}`], ["Backend", info.backend], ["Application data", info.dataDir], ["Desktop settings and chat history", info.userData], ["App log", info.appLog], ["Conversation archive", info.archiveFolder || "Not configured"]].map(([label, value]) => <div key={label}><dt className="font-semibold">{label}</dt><dd className="select-text break-all text-muted">{value}</dd></div>)}</dl> : <p className="text-xs text-muted">Loading diagnostics…</p>}
    <div className="flex flex-wrap gap-2">{[["logs", "Open logs folder"], ["read-log", "View recent log"], ["data", "Open app data"], ["user-data", "Open desktop data"], ["copy", "Copy diagnostics"], ["devtools", "Open developer tools"], ["restart", "Restart backend"]].map(([action, label]) => <button key={action} type="button" disabled={busy} className="rounded border border-line px-2 py-1 text-xs hover:bg-slate-50 focus-visible:outline disabled:opacity-40" onClick={() => { if (action !== "restart" || window.confirm("Restart the backend? Active requests will be interrupted.")) void act(action); }}>{label}</button>)}</div>
    {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
    {log !== null ? <pre aria-label="Recent app log" className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-[10px]">{log || "No log entries yet."}</pre> : null}
  </div>;
}

export function RemMemorySettings({ value, onChange, providerState, audioInputDeviceId }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const bridge = window.goferDesktop?.rem;
  async function commit(key, next) {
    setBusy(true); setError("");
    try {
      const config = await bridge.configure(key, next);
      for (const [field, content] of Object.entries(config)) onChange(`memory.${field}`, content);
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }
  async function choose(key) {
    setError("");
    try {
      const folder = await window.goferDesktop.workspace.selectPath({ directoryOnly: true, currentPath: value[key] || "" });
      if (folder) await commit(key, folder);
    } catch (cause) { setError(cause.message); }
  }
  const buttonClass = "rounded border border-line px-2 py-1 text-xs hover:bg-slate-50 focus-visible:outline disabled:opacity-40";
  return <div className="space-y-4 py-3">
    <div><h3 className="text-xs font-semibold">Conversation archive</h3><p className="mt-1 text-[11px] text-muted">Archive existing and future conversations, messages, tool activity, and attachments. Files remain available after a thread is deleted in Rem.</p>
      <p className="my-2 break-all text-xs text-muted">{value.archiveFolder || "Not configured"}</p>
      <button type="button" className={buttonClass} disabled={!bridge || busy} onClick={() => void choose("archiveFolder")}>Choose archive folder</button>
      {value.archiveFolder ? <button type="button" className={`${buttonClass} ml-2`} disabled={busy} onClick={() => void commit("archiveFolder", "")}>Stop archiving</button> : null}
    </div>
    <div className="border-t border-line pt-3"><h3 className="text-xs font-semibold">Second Brain</h3><p className="mt-1 text-[11px] text-muted">Give Rem tools to search your knowledge, read notes, and save reports in this folder. The tools work independently of shell access.</p>
      <p className="my-2 break-all text-xs text-muted">{value.secondBrainRoot || "Choose a knowledge folder"}</p>
      <button type="button" className={buttonClass} disabled={!bridge || busy} onClick={() => void choose("secondBrainRoot")}>Choose Second Brain folder</button>
      <label className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={value.secondBrainEnabled} disabled={!bridge || busy || !value.secondBrainRoot} onChange={(event) => void commit("secondBrainEnabled", event.target.checked)} />Enable Second Brain</label>
    </div>
    <ReportThemeSettings audioInputDeviceId={audioInputDeviceId} value={value} onChange={onChange} providerState={providerState} />
    {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
  </div>;
}
