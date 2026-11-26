import { useEffect, useState } from "react";
import AppLogViewer from "./AppLogViewer.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import ReportThemeSettings from "./ReportThemeSettings.jsx";
import { ARCHIVE_GIT_LIMIT, ARCHIVE_PURGE_OPTIONS } from "../lib/threadActivity.js";

export default function DeveloperSettings() {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, confirmDialog] = useConfirm();
  const bridge = window.goferDesktop?.developer;
  useEffect(() => { if (bridge) void bridge.info().then(setInfo).catch((cause) => setError(cause.message)); }, [bridge]);
  async function act(action) {
    setError(""); setBusy(true);
    try {
      if (action === "copy") await navigator.clipboard.writeText(JSON.stringify(await bridge.info(), null, 2));
      else { const result = await bridge.action(action); if (typeof result === "string" && result) throw new Error(result); }
      setInfo(await bridge.info());
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }
  if (!bridge) return <p className="py-3 text-xs text-muted">Developer diagnostics are available in the desktop app.</p>;
  return <div className="space-y-4 py-3">
    <AppLogViewer bridge={bridge} logFile={info?.appLog} version={info?.version} platform={info?.platform} />
    <div className="space-y-3 border-t border-line pt-3">
      <h3 className="text-xs font-semibold">Diagnostics</h3>
      {info ? <dl className="space-y-2 text-xs">{[["Version", info.version], ["Platform", info.platform], ["Electron / Node", `${info.electron} / ${info.node}`], ["Backend", info.backend], ["Data folder", info.dataDir], ["Desktop settings and chat history", info.userData], ["App log", info.appLog], ["Conversation archive", info.archiveFolder || "Not configured"]].map(([label, value]) => <div key={label}><dt className="font-semibold">{label}</dt><dd className="select-text break-all text-muted">{value}</dd></div>)}</dl> : <p className="text-xs text-muted">Loading diagnostics…</p>}
      <div className="flex flex-wrap gap-2">{[["logs", "Open logs folder"], ["data", "Open data folder"], ["user-data", "Open desktop data"], ["copy", "Copy diagnostics"], ["devtools", "Open developer tools"], ["restart", "Restart backend"]].map(([action, label]) => <button key={action} type="button" disabled={busy} className="rounded border border-line px-2 py-1 text-xs hover:bg-slate-50 focus-visible:outline disabled:opacity-40" onClick={async () => { if (action !== "restart" || await confirm({ confirmLabel: "Restart backend", message: "Restart the backend? Active requests will be interrupted. You will be asked to save or preserve unsaved file drafts." })) void act(action); }}>{label}</button>)}</div>
      {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
    </div>
    {confirmDialog}
  </div>;
}

export function RemMemorySettings({ value, onChange, providerState, audioInputDeviceId }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, confirmDialog] = useConfirm();
  const bridge = window.goferDesktop?.rem;
  async function commit(key, next) {
    setBusy(true); setError("");
    try {
      const config = await bridge.configure(key, next);
      for (const [field, content] of Object.entries(config)) onChange(`memory.${field}`, content);
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }
  async function choosePurge(next) {
    if (next !== "never" && !await confirm({ confirmLabel: "Purge archive copies", danger: true, message: `Purge archive copies of threads deleted more than ${next} days ago? Their snapshots, journals and unshared attachments are removed from the archive folder now and from then on. ${ARCHIVE_GIT_LIMIT}` })) return;
    await commit("archivePurgeDays", next);
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
    <div><h3 className="text-xs font-semibold">Conversation archive</h3><p className="mt-1 text-[11px] text-muted">Archive existing and future conversations, messages, tool activity, and attachments. Files remain available after a thread is deleted in Rem, unless you choose Delete permanently or turn on purging below.</p>
      <p className="my-2 break-all text-xs text-muted">{value.archiveFolder || "Not configured"}</p>
      <button type="button" className={buttonClass} disabled={!bridge || busy} onClick={() => void choose("archiveFolder")}>Choose archive folder</button>
      {value.archiveFolder ? <button type="button" className={`${buttonClass} ml-2`} disabled={busy} onClick={() => void commit("archiveFolder", "")}>Stop archiving</button> : null}
      <label className="mt-3 block text-xs font-semibold" htmlFor="rem-archive-purge">Purge archive copies</label>
      <p id="rem-archive-purge-help" className="mt-1 text-[11px] text-muted">Remove the snapshot, journal and unshared attachments of a deleted thread from the archive this long after it was deleted. Never keeps everything. {ARCHIVE_GIT_LIMIT}</p>
      <select id="rem-archive-purge" aria-describedby="rem-archive-purge-help" className="mt-2 rounded border border-line bg-white px-2 py-1 text-xs disabled:opacity-40"
        value={value.archivePurgeDays || "never"} disabled={!bridge || busy || !value.archiveFolder} onChange={(event) => void choosePurge(event.target.value)}>
        {ARCHIVE_PURGE_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </div>
    <div className="border-t border-line pt-3"><h3 className="text-xs font-semibold">Second Brain</h3><p className="mt-1 text-[11px] text-muted">Give Rem tools to search your knowledge, read notes, and save reports in this folder. The tools work independently of shell access.</p>
      <p className="my-2 break-all text-xs text-muted">{value.secondBrainRoot || "Choose a knowledge folder"}</p>
      <button type="button" className={buttonClass} disabled={!bridge || busy} onClick={() => void choose("secondBrainRoot")}>Choose Second Brain folder</button>
      <label className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={value.secondBrainEnabled} disabled={!bridge || busy || !value.secondBrainRoot} onChange={(event) => void commit("secondBrainEnabled", event.target.checked)} />Enable Second Brain</label>
    </div>
    <ReportThemeSettings audioInputDeviceId={audioInputDeviceId} value={value} onChange={onChange} providerState={providerState} />
    {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
    {confirmDialog}
  </div>;
}
