import { useEffect, useState } from "react";
import { AlertTriangle, ArchiveRestore, FolderOpen, ShieldCheck } from "lucide-react";
import { formatBytes, formatDay, formatTime } from "../lib/secondBrain.js";
import { WEEKDAYS, archiveLabel, backupSettings, packageFit, scheduleLabel } from "../lib/secondBrainBackups.js";
import { isSampleLibrary, startTask, updateBrain, useSecondBrain } from "../lib/secondBrainStore.js";
import { brainRequest } from "../lib/secondBrainApi.js";
import { DraftInput } from "./SecondBrainParts.jsx";

/** What a complete package of this brain holds, and how much of it is Rem's conversation archive. */
export function useBackupEstimate(brainId) {
  const [estimate, setEstimate] = useState(null);
  useEffect(() => {
    if (!brainId || isSampleLibrary()) return undefined;
    let live = true;
    brainRequest("backup_estimate", { brainId }).then(result => { if (live) setEstimate(result); }).catch(() => {});
    return () => { live = false; };
  }, [brainId]);
  return estimate;
}

function TimeField({ value, onCommit }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <input type="time" aria-label="Backup time" value={draft} onChange={event => setDraft(event.target.value)} onBlur={() => { if (/^\d\d:\d\d$/.test(draft) && draft !== value) onCommit(draft); else setDraft(value); }} />;
}

export function BackupSettings({ brain }) {
  const store = useSecondBrain();
  const settings = backupSettings(brain);
  const status = brain.backupStatus || {};
  const verified = status.lastVerified;
  const attempt = status.lastAttempt;
  const estimate = useBackupEstimate(brain.id);
  const fit = packageFit(estimate, settings.conversations);
  const running = store.tasks.some(task => task.kind === "backup" && task.brainId === brain.id && task.state === "running");
  const set = patch => updateBrain(brain.id, current => ({ backup: { ...backupSettings(current), ...patch } }));
  const chooseFolder = async () => {
    try {
      const path = await window.goferDesktop?.workspace?.selectPath?.({ directoryOnly: true, currentPath: settings.folder || store.capabilities?.home || "" });
      if (path) set({ folder: path });
    } catch { /* The picker was closed. */ }
  };
  return <section aria-labelledby="sb-backups-title"><h4 id="sb-backups-title">Backups</h4>
    <div className="sb-backup-status" data-tone={brain.backupWarning ? "warning" : verified ? "success" : undefined}>
      {brain.backupWarning ? <AlertTriangle size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}
      <div>
        <strong>Last verified backup: {verified ? formatDay(verified.at, { relative: true }) : "none yet"}</strong>
        {verified ? <small>{formatTime(verified.at)} · {formatBytes(verified.size)} · {verified.files.toLocaleString()} files{verified.conversations ? "" : " · without the conversation archive"}</small> : null}
        {brain.backupWarning ? <small role="status">{brain.backupWarning} Check the backup folder, or back up now.</small> : null}
        {attempt?.state === "failed" ? <small role="alert">Last attempt {formatTime(attempt.at)} failed: {attempt.error}</small> : null}
      </div>
    </div>
    <label className="sb-check"><input type="checkbox" checked={settings.enabled} disabled={!settings.folder} onChange={event => set({ enabled: event.target.checked })} />Back up on a schedule</label>
    <div className="sb-field"><span>Backup folder</span>
      <div className="sb-path-input"><DraftInput label="Backup folder" className="sb-mono" placeholder="/media/backup-drive/Raticode brains" value={settings.folder} onCommit={folder => set({ folder: folder.trim(), enabled: folder.trim() ? settings.enabled : false })} /><button type="button" className="sb-button" onClick={chooseFolder}><FolderOpen size={14} />Browse…</button></div>
      <small>Must be outside the brain. A different drive also protects against a failed disk.</small>
    </div>
    <div className="sb-backup-grid">
      <label className="sb-field"><span>How often</span><select value={settings.frequency} onChange={event => set({ frequency: event.target.value })}><option value="daily">Every day</option><option value="weekly">Every week</option></select></label>
      {settings.frequency === "weekly" ? <label className="sb-field"><span>Day</span><select value={settings.weekday} onChange={event => set({ weekday: Number(event.target.value) })}>{WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label> : null}
      <label className="sb-field"><span>Time</span><TimeField value={settings.time} onCommit={time => set({ time })} /></label>
      <label className="sb-field"><span>Copies to keep</span><DraftInput label="Copies to keep" type="number" min="1" max="60" value={String(settings.keep)} onCommit={value => { const keep = Number.parseInt(value, 10); if (keep >= 1 && keep <= 60) set({ keep }); }} /></label>
    </div>
    <label className="sb-check"><input type="checkbox" checked={settings.conversations} onChange={event => set({ conversations: event.target.checked })} />{archiveLabel(estimate)}</label>
    {fit && !fit.fits ? <p className="sb-inline-note" data-tone="warning"><AlertTriangle size={14} aria-hidden="true" /><span>A backup would be {formatBytes(fit.size)}, over the {formatBytes(fit.limit)} package limit, so it would fail.{settings.conversations && estimate.conversations ? " Leave out the conversation archive." : ""}</span></p> : null}
    <div className="sb-actions">
      <button type="button" className="sb-button" disabled={!settings.folder || running} onClick={() => startTask({ kind: "backup", label: `Backing up ${brain.name}`, brainId: brain.id })}><ArchiveRestore size={14} />{running ? "Backing up…" : "Back up now"}</button>
      {settings.folder ? <button type="button" className="sb-button" onClick={() => window.goferDesktop?.workspace?.revealPath?.(verified?.path || settings.folder)}><FolderOpen size={14} />Show backups</button> : null}
    </div>
    <p className="sb-hint">{scheduleLabel(settings)}. Each backup is the complete-brain package, checked by the package verifier after it is saved. Only then are older copies past the last {settings.keep} removed. A copy that fails the check is deleted and never counts. Restore one with Import brain › Restore this brain.</p>
  </section>;
}
