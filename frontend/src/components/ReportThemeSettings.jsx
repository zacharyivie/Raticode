import { dismissGenerationJob, latestGenerationJob, jobIsRunning } from "../lib/generationJobs.js";
import { startPolling } from "../lib/refresh.js";
import { useEffect, useRef, useState } from "react";
import { REPORT_THEMES } from "../lib/settings.js";
import { exportReportTheme, importReportTheme, MAX_REPORT_THEME_FILE_BYTES, normalizeReportThemes, reportOutputFormat, reportPreviewDocument, requestReportTheme } from "../lib/reportThemes.js";
import ChatComposer from "./ChatComposer.jsx";
import { clipboardAttachmentFiles, encodeChatAttachments, largePasteFile, readChatAttachments, transferContainsFiles } from "../lib/chatAttachments.js";
import "./ReportThemeSettings.css";

const palettes = {
  auto: ["#f6f7fb", "#273047", "#6268b3"], light: ["#ffffff", "#243040", "#3769a5"], dark: ["#181e2c", "#eff2fa", "#ada8ff"],
  sepia: ["#eee0c4", "#403221", "#966447"], vaporwave: ["#221236", "#f6efff", "#ff83c7"], steam: ["#e7d8b6", "#342e29", "#34726c"],
  carbon: ["#23262a", "#f1f2f3", "#ff9e57"], botanical: ["#f4f0df", "#234c36", "#b77b61"], blueprint: ["#102e50", "#f1f5f2", "#6fe1f5"],
  arcade: ["#1b1025", "#fff5dd", "#e8f76b"], sakura: ["#fff6e7", "#452637", "#ba567c"], "deep-sea": ["#071f32", "#ecf5f3", "#63ddca"],
  solarpunk: ["#fff4d7", "#284e33", "#b07810"], noir: ["#191718", "#f3efe4", "#ee737d"], "candy-lab": ["#fff6eb", "#4c2440", "#b24180"], cosmic: ["#111a38", "#f0f1ff", "#b7a0ff"],
};

function ThemeDocument({ theme }) {
  if (theme.html) return <span className="report-theme-paper custom-paper" aria-hidden="true"><iframe title={`${theme.label} miniature`} tabIndex={-1} sandbox="" srcDoc={reportPreviewDocument(theme.html)} /></span>;
  const [paper, ink, accent] = palettes[theme.id];
  return <span aria-hidden="true" className={`report-theme-paper theme-${theme.id}`} style={{ "--paper": paper, "--ink": ink, "--accent": accent }}>
    <span className="mini-rule" /><span className="mini-heading">Project<br />review</span><span className="mini-copy">Findings &amp; next steps</span>
    <span className="mini-chart"><i /><i /><i /><i /></span><span className="mini-lines"><i /><i /><i /></span><span className="mini-footer">Raticode / Report</span>
  </span>;
}

export default function ReportThemeSettings({ value, onChange, providerState = {}, audioInputDeviceId = "default" }) {
  const config = normalizeReportThemes(value.reportThemes, value.secondBrainTheme);
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [draft, setDraft] = useState(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [busy, setBusy] = useState(false);
  const [savingConfig, setSaving] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [transferStatus, setTransferStatus] = useState("");
  const saving = savingConfig || transferring;
  const importInput = useRef(null);
  const jobRef = useRef(null);
  const [loadingJob, setLoadingJob] = useState(true);
  const gallery = useRef(null);
  const editor = useRef(null);
  useEffect(() => {
    if (editing) editor.current?.scrollIntoView?.({ block: "nearest" });
  }, [editing]);
  useEffect(() => {
    let active = true;
    const stop = startPolling(async () => {
      try {
        const job = await latestGenerationJob("theme");
        if (!active) return;
        if (job && (job.id !== jobRef.current?.id || job.updatedAt !== jobRef.current?.updatedAt || job.status !== jobRef.current?.status)) {
          const newJob = job.id !== jobRef.current?.id;
          jobRef.current = job;
          if (newJob) setEditing(true);
          setBusy(jobIsRunning(job)); setProgress(job.progress || ""); setError(job.error || "");
          if (newJob) setDescription(job.description);
          if (job.result) { setDraft(job.result); setName(job.result.label); }
        }
      } catch (cause) { if (active) setError(cause.message); }
      finally { if (active) setLoadingJob(false); }
    }, { immediate: true });
    return () => { active = false; stop(); };
  }, []);
  useEffect(() => {
    const selected = gallery.current?.querySelector('[aria-pressed="true"]');
    if (selected) gallery.current.scrollTo?.({ left: Math.max(0, selected.offsetLeft - (gallery.current.clientWidth - selected.offsetWidth) / 2) });
  }, [config.selected]);
  const themes = [...REPORT_THEMES, ...config.custom];
  const providers = (providerState.capabilities || []).filter(item => item.available && item.enabled !== false);
  const provider = providers.find(item => item.id === config.generation.provider);
  const model = provider?.models?.find(item => item.id === config.generation.model);
  const selected = themes.find(theme => theme.id === config.selected);
  const selectedCustom = config.custom.find(theme => theme.id === config.selected);
  const format = reportOutputFormat(value);
  async function changeFormat(next) {
    setSaving(true); setError("");
    try {
      if (window.goferDesktop?.rem) await window.goferDesktop.rem.configure("reportFormat", next);
      onChange("memory.reportFormat", next);
    } catch (cause) { setError(cause.message); }
    finally { setSaving(false); }
  }

  async function commit(next) {
    setSaving(true); setError("");
    try {
      if (window.goferDesktop?.rem) await window.goferDesktop.rem.configure("reportThemes", next);
      onChange("memory.reportThemes", next);
      return true;
    } catch (cause) { setError(cause.message); return false; }
    finally { setSaving(false); }
  }
  async function importTheme(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || saving) return;
    setTransferring(true); setError(""); setTransferStatus("");
    try {
      if (file.size > MAX_REPORT_THEME_FILE_BYTES) throw new Error("Theme files must be 2 MB or smaller.");
      const next = importReportTheme(await file.text(), config);
      if (await commit(next)) setTransferStatus(`Imported "${next.custom.at(-1).label}" and selected it.`);
    } catch (cause) { setError(cause.message); }
    finally { setTransferring(false); }
  }
  function exportTheme() {
    setError(""); setTransferStatus("");
    try {
      const { filename, content } = exportReportTheme(selectedCustom);
      const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url; link.download = filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setTransferStatus(`Theme file ready to share: ${filename}`);
    } catch (cause) { setError(cause.message); }
  }
  function generation(patch) { void commit({ ...config, generation: { ...config.generation, ...patch } }); }
  function addAttachments(files) {
    const result = readChatAttachments(files, attachments);
    setAttachments(result.attachments); setAttachmentError(result.error); setDraft(null);
  }
  function paste(event) {
    const files = clipboardAttachmentFiles(event.clipboardData);
    const text = largePasteFile(event.clipboardData?.getData?.("text/plain") || "");
    if (!files.length && !text) return;
    event.preventDefault(); event.stopPropagation();
    if (!busy) addAttachments(text ? [...files, text] : files);
  }
  async function generate() {
    if (busy || saving || loadingJob) return;
    setBusy(true); setProgress(""); setError(""); setDraft(null);
    try {
      const files = await encodeChatAttachments(attachments);
      const job = await requestReportTheme({ background: true, description, attachments: files, generation: config.generation });
      jobRef.current = job;
      setBusy(jobIsRunning(job)); setDescription(job.description); setProgress(job.progress || ""); setError(job.error || "");
      if (job.result) { setDraft(job.result); setName(job.result.label); }
    } catch (cause) { setError(cause.message); setBusy(false); }
  }
  function close() { setEditing(false); }
  async function save() {
    const theme = { ...draft, label: name.trim(), id: `custom-${crypto.randomUUID()}` };
    if (await commit({ ...config, selected: theme.id, custom: [...config.custom, theme] })) {
      try { if (jobRef.current) await dismissGenerationJob(jobRef.current.id); }
      catch (cause) { setError(cause.message); return; }
      jobRef.current = null; setDraft(null); setDescription(""); setAttachments([]); close();
    }
  }
  return <section className="report-themes" aria-labelledby="report-themes-heading">
    <div className="report-theme-heading"><h3 id="report-themes-heading">Report themes</h3><button type="button" aria-expanded={editing} aria-controls="report-theme-editor" disabled={loadingJob || saving || config.custom.length >= 24} onClick={() => { setEditing(true); setError(""); }}>{busy ? "View generation" : draft ? "View generated theme" : "+ New theme"}</button></div>
    {editing ? <div ref={editor} id="report-theme-editor" className="report-theme-editor" aria-label="New report theme">
      <h4>Create a theme with Rem</h4>
      <p className="report-theme-help">Describe the look you want, or attach a report or screenshot as a reference.</p>
      <div className="report-theme-composer" onPaste={paste}
        onDragOver={event => { if (transferContainsFiles(event.dataTransfer)) { event.preventDefault(); event.stopPropagation(); } }}
        onDrop={event => { if (transferContainsFiles(event.dataTransfer)) { event.preventDefault(); event.stopPropagation(); if (!busy) addAttachments(event.dataTransfer.files); } }}>
        <fieldset disabled={busy || saving}>
          <ChatComposer inputLabel="Theme description" placeholder="For example, a marine field journal with navy ink and sea-glass accents."
            draft={description} onDraftChange={text => { setDescription(text); setDraft(null); }}
            attachments={attachments} onAddAttachments={addAttachments}
            onAttachmentsChange={next => { setAttachments(next); setDraft(null); }}
            attachmentError={attachmentError} onAttachmentErrorChange={setAttachmentError}
            audioInputDeviceId={audioInputDeviceId} contextKey="report-theme" focusRequest={1}
            permissionOptions={[]} onSend={() => void generate()} sendDisabled={busy || saving}
            sendLabel={draft ? "Generate another preview" : "Generate preview"}
            shortcutHint="Enter to generate preview · Shift+Enter for a new line" />
        </fieldset>
      </div>
      <div className="report-theme-actions"><button type="button" disabled={saving} onClick={close}>{busy ? "Continue in background" : "Close"}</button></div>
      {busy ? <div role="status" className="report-theme-progress"><span className="report-theme-spinner" aria-hidden="true" /><p>{progress || "Rem is composing your theme…"}</p></div> : null}
      {draft ? <div className="report-theme-draft"><p role="status">Theme generated successfully.</p><h4>Preview</h4><iframe title="New report theme preview" sandbox="" srcDoc={reportPreviewDocument(draft.html)} /><label>Theme name<input aria-label="Theme name" value={name} maxLength={80} onChange={event => setName(event.target.value)} /></label><button type="button" disabled={saving || !name.trim() || config.custom.length >= 24} onClick={() => void save()}>{saving ? "Saving…" : "Save theme"}</button></div> : null}
      {error ? <p role="alert" className="report-theme-error">{error}</p> : null}
    </div> : null}
    <label className="report-output-format">Report output<select aria-label="Report output format" value={format} disabled={saving} onChange={event => void changeFormat(event.target.value)}>
      <option value="md">Markdown</option><option value="html">HTML</option><option value="slides">Slides</option><option value="pdf">PDF</option>
    </select></label>
    <p className="report-theme-help">{{ md: "Markdown documents. Theme styling applies to HTML, Slides, and PDF.", html: "Standalone HTML reports with embedded styling.", slides: "Landscape slides in a single HTML file, with presentation controls.", pdf: "PDF reports with clean page breaks and readable print layouts." }[format]} Applies wherever reports are saved. You can request a different format in Rem.</p>
    <label className="report-theme-toggle"><input type="checkbox" checked={config.enabled} disabled={saving} onChange={event => void commit({ ...config, enabled: event.target.checked })} />Use the selected report theme</label>
    <div className="report-gallery-controls"><span>{selected?.label || "System"}{config.enabled ? " selected" : " · Theme guidance off"}</span><div><button type="button" aria-label="Previous report themes" onClick={() => gallery.current?.scrollBy({ left: -260 })}>‹</button><button type="button" aria-label="Next report themes" onClick={() => gallery.current?.scrollBy({ left: 260 })}>›</button></div></div>
    <div ref={gallery} className="report-theme-gallery" role="group" aria-label="Report theme gallery">
      {themes.map(theme => <button key={theme.id} type="button" className="report-theme-choice" aria-label={theme.label} aria-pressed={config.selected === theme.id} disabled={saving} onClick={() => void commit({ ...config, selected: theme.id })}>
        <ThemeDocument theme={theme} /><span className="report-theme-label">{theme.label}{config.selected === theme.id ? <span aria-hidden="true"> ✓</span> : null}</span>
      </button>)}
    </div>
    <div className="report-theme-sharing">
      <div className="report-theme-actions">
        <button type="button" disabled={saving || config.custom.length >= 24} onClick={() => importInput.current?.click()}>{transferring ? "Importing…" : "Import theme"}</button>
        {selectedCustom ? <button type="button" disabled={saving} onClick={exportTheme}>Export theme</button> : null}
      </div>
      <input ref={importInput} type="file" accept=".json,application/json" aria-label="Import report theme file" hidden onChange={event => void importTheme(event)} />
      <p className="report-theme-help">Share a custom theme with your team by exporting its theme file. Import a shared file to add and select the theme.</p>
      {transferStatus ? <p role="status" className="report-theme-transfer-status">{transferStatus}</p> : null}
    </div>
    <details className="report-generation-settings"><summary>Theme generation settings</summary>
      <p className="report-theme-help">Uses the current Rem provider, model, and effort unless you choose an override.</p>
      <label>Provider<select aria-label="Theme generation provider" value={config.generation.provider} disabled={saving || busy || providerState.loading} onChange={event => generation({ provider: event.target.value, model: "", effort: "" })}>
        <option value="">Current Rem selection</option>{providers.map(item => <option key={item.id} value={item.id}>{item.displayName || item.id}</option>)}
        {config.generation.provider && !provider ? <option value={config.generation.provider}>{config.generation.provider} · unavailable</option> : null}
      </select></label>
      {config.generation.provider ? <><label>Model<select aria-label="Theme generation model" disabled={saving || busy} value={config.generation.model} onChange={event => generation({ model: event.target.value, effort: "" })}><option value="">Provider default</option>{(provider?.models || []).filter(item => item.allowed !== false).map(item => <option key={item.id} value={item.id}>{item.displayName || item.id}</option>)}</select></label>
        <label>Effort<select aria-label="Theme generation effort" value={config.generation.effort} disabled={saving || busy || !model?.efforts?.length} onChange={event => generation({ effort: event.target.value })}><option value="">Model default</option>{(model?.efforts || []).map(item => <option key={item.id} value={item.id}>{item.displayName || item.id}</option>)}</select></label></> : null}
    </details>
    {error && !editing ? <p role="alert" className="report-theme-error">{error}</p> : null}
  </section>;
}
