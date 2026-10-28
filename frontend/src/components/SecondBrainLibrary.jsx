import SecondBrainProvider, { ResearchProviderContext, useResearchSelection } from "./SecondBrainProvider.jsx";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { CircleCheck, ClipboardPaste, CopyCheck, FileWarning, FolderOpen, Globe, Inbox, Library, Link2, Pencil, Play, Plus, Quote, RefreshCw, ScanText, Tags, Trash2, Unlink, FilePlus2, Repeat, CalendarClock, HardDrive } from "lucide-react";
import { formatDay, formatTime, extractionLabel } from "../lib/secondBrain.js";
import { HEALTH_KINDS, RECIPES } from "../lib/secondBrain.js";
import { useSecondBrain, openDocument, openDialog, updateDocument, addToCollection, evidenceItem, resolveHealth, startTask, saveJob, removeJob, removeDocument, updateBrain, notify, navigate, uid } from "../lib/secondBrainStore.js";
import { DraftInput, Empty, Menu, Pill, ProgressBar, TypeIcon } from "./SecondBrainParts.jsx";
import JobSourceRules from "./SecondBrainJobSources.jsx";
import { defaultJobSourceRules, describeJobSources, jobScopeLabel, jobSourceRules, jobSourceRulesError } from "../lib/secondBrainJobSources.js";

export function InboxView({ brain }) {
  const store = useSecondBrain();
  const items = store.documents.filter(doc => doc.brainId === brain.id && doc.inbox).sort((a, b) => (b.dates.captured || "").localeCompare(a.dates.captured || ""));
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const [selected, setSelected] = useState([]);
  const [confirm, setConfirm] = useState("");
  const review = ids => { for (const id of ids) updateDocument(id, { inbox: false }); setSelected([]); notify(ids.length === 1 ? "Moved out of Inbox." : `${ids.length} sources moved out of Inbox.`); };
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Inbox</h3><p>New captures and imports wait here until you review them. Reviewing keeps the file where it is; it just becomes part of your regular library.</p></div>
      <div className="sb-actions"><button type="button" className="sb-button sb-primary" onClick={() => openDialog("capture", { mode: "url" })}><Globe size={14} />Capture web page</button><button type="button" className="sb-button" onClick={() => openDialog("capture", { mode: "excerpt" })}><ClipboardPaste size={14} />Save excerpt</button><button type="button" className="sb-button" onClick={async () => { const file = await window.goferDesktop?.workspace?.selectPath?.({ currentPath: brain.root }); if (file) void startTask({ kind: "add-files", label: "Adding files to Inbox", paths: [file] }); }}><FilePlus2 size={14} />Add files</button></div></div>
    {selected.length ? <div className="sb-bulk"><strong>{selected.length} selected</strong><button type="button" className="sb-mini-button" onClick={() => review(selected)}><CircleCheck size={13} />Mark reviewed</button><button type="button" className="sb-mini-button" onClick={() => setSelected([])}>Clear</button></div> : null}
    {items.length ? <ul className="sb-rows">{items.map(doc => { const missing = [!doc.author && "author", !doc.dates.published && "publication date", !doc.tags.length && "tags"].filter(Boolean); return <li key={doc.id} className="sb-row">
      <input type="checkbox" aria-label={`Select ${doc.title}`} checked={selected.includes(doc.id)} onChange={() => setSelected(selected.includes(doc.id) ? selected.filter(id => id !== doc.id) : [...selected, doc.id])} />
      <TypeIcon type={doc.type} size={16} />
      <div className="sb-row-main">
        <button type="button" className="sb-row-title" onClick={() => openDocument(doc.id, { preview: false })}>{doc.title}</button>
        <p>{doc.source?.url ? <span className="sb-mono">{doc.source.url}</span> : <span>{doc.path}</span>}<span>Captured {doc.dates.captured ? formatTime(doc.dates.captured) : formatDay(doc.dates.added)}</span>{doc.extraction !== "ready" ? <Pill tone="warning">{extractionLabel(doc.extraction)}</Pill> : null}</p>
        {missing.length ? <p className="sb-hint">Missing {missing.join(", ")}. <button type="button" className="sb-link" onClick={() => openDialog("edit-metadata", { docId: doc.id })}>Add metadata</button></p> : null}
        <div className="sb-row-tags"><Tags size={13} aria-hidden="true" /><DraftInput label={`Tags for ${doc.title}`} className="sb-tags-input" placeholder="Add tags, separated by commas" value={doc.tags.join(", ")} onCommit={value => updateDocument(doc.id, { tags: value.split(",").map(tag => tag.trim().toLowerCase()).filter(Boolean) })} /></div>
      </div>
      <div className="sb-row-actions">
        <Menu label={`Add ${doc.title} to a collection`} triggerClassName="sb-mini-button" trigger={<><Library size={13} />Collect</>} items={[...collections.map(item => ({ label: item.name, icon: Library, onSelect: () => addToCollection(item.id, evidenceItem(doc.id)) })), { separator: true, key: "s" }, { label: "New collection…", icon: Plus, onSelect: () => openDialog("new-collection", { items: [evidenceItem(doc.id)] }) }]} />
        <button type="button" className="sb-mini-button" onClick={() => review([doc.id])}><CircleCheck size={13} />Reviewed</button>
        {confirm === doc.id ? <button type="button" className="sb-mini-button" data-danger onClick={() => { removeDocument(doc.id); setConfirm(""); notify("Moved the capture to .raticode/trash in this brain."); }}>Confirm discard</button> : <button type="button" className="sb-icon-button" aria-label={`Discard ${doc.title}`} title="Discard" onClick={() => setConfirm(doc.id)}><Trash2 size={14} /></button>}
      </div>
    </li>; })}</ul> : <Empty icon={Inbox} title="Inbox is clear">Captured pages, excerpts and imported files appear here for review.</Empty>}
  </div>;
}

const FIX_ICONS = { link: Unlink, metadata: Pencil, extraction: ScanText, duplicate: CopyCheck, citation: Quote };
const HEALTH_PAGE_SIZE = 15;

function HealthProblemList({ issues, documents, selected, onSelect, onFix }) {
  const [limit, setLimit] = useState(HEALTH_PAGE_SIZE);
  const list = useRef(null);
  const sentinel = useRef(null);
  const hasMore = limit < issues.length;
  useEffect(() => { list.current?.closest(".sb-page")?.scrollTo({ top: 0 }); }, []);
  useEffect(() => {
    const target = sentinel.current;
    if (!hasMore || !target || typeof IntersectionObserver === "undefined") return;
    let active = true;
    const observer = new IntersectionObserver(entries => {
      if (!active || !entries.some(entry => entry.isIntersecting)) return;
      active = false;
      observer.disconnect();
      setLimit(current => Math.max(current, limit + HEALTH_PAGE_SIZE));
    }, { root: target.closest(".sb-page"), threshold: 0 });
    observer.observe(target);
    return () => { active = false; observer.disconnect(); };
  }, [hasMore, limit, issues.length]);
  return <>
    <ul ref={list} className="sb-rows sb-health-rows">{issues.slice(0, limit).map(issue => { const doc = documents.get(issue.docId); const Icon = FIX_ICONS[issue.kind]; return <li key={issue.id} className="sb-row">
      <input type="checkbox" aria-label={`Select problem in ${doc?.title}`} checked={selected.has(issue.id)} onChange={() => onSelect(issue.id)} />
      <Icon size={16} aria-hidden="true" className="sb-row-icon" />
      <div className="sb-row-main"><button type="button" className="sb-row-title" onClick={() => openDocument(issue.docId)}>{doc?.title}</button><p><span className="sb-mono">{doc?.path}</span></p><p>{issue.detail}</p></div>
      <div className="sb-row-actions"><span className="sb-row-kind">{HEALTH_KINDS.find(([id]) => id === issue.kind)?.[1]}</span><button type="button" className="sb-button" onClick={() => onFix(issue)}>{issue.fix}</button></div>
    </li>; })}</ul>
    <div className="sb-health-pagination">
      <p className="sb-hint" role="status">Showing {Math.min(limit, issues.length)} of {issues.length} problems</p>
      {hasMore ? <button ref={sentinel} type="button" className="sb-button" onClick={() => setLimit(current => Math.max(current, limit + HEALTH_PAGE_SIZE))}>Show {Math.min(HEALTH_PAGE_SIZE, issues.length - limit)} more problems</button> : null}
    </div>
  </>;
}

export function HealthView({ brain }) {
  const store = useSecondBrain();
  const documents = useMemo(() => new Map(store.documents.map(doc => [doc.id, doc])), [store.documents]);
  const issues = useMemo(() => store.health.filter(issue => documents.get(issue.docId)?.brainId === brain.id), [store.health, documents, brain.id]);
  const counts = useMemo(() => { const result = {}; for (const issue of issues) result[issue.kind] = (result[issue.kind] || 0) + 1; return result; }, [issues]);
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState([]);
  const selectedIds = useMemo(() => new Set(selected), [selected]);
  const visible = useMemo(() => kind ? issues.filter(issue => issue.kind === kind) : issues, [issues, kind]);
  const fix = issue => {
    if (issue.kind === "extraction" && documents.get(issue.docId)?.extraction === "ocr") openDialog("ocr", { docIds: [issue.docId], issueIds: [issue.id] });
    else if (issue.kind === "extraction") openDialog("encoding", { docId: issue.docId, issueId: issue.id });
    else if (issue.kind === "metadata") openDialog("edit-metadata", { docId: issue.docId });
    else if (issue.kind === "duplicate") openDialog("duplicate", { issue });
    else if (issue.kind === "link") openDialog("locate-link", { issue });
    else if (issue.kind === "citation") { const report = store.reports.find(item => item.citations.some(citation => citation.state === "relocate" && citation.docId === issue.docId)); const citation = report?.citations.find(item => item.state === "relocate" && item.docId === issue.docId); if (report && citation) { navigate("reports", { reportId: report.id }); openDialog("relocate", { citation, reportId: report.id, issueId: issue.id }); } }
  };
  const selectedIssues = issues.filter(issue => selectedIds.has(issue.id));
  const ocrBatch = selectedIssues.filter(issue => issue.kind === "extraction" && documents.get(issue.docId)?.extraction === "ocr");
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Library health</h3><p>Problems that keep documents out of search or make citations hard to trust. Fix them in batches; your folder structure stays as it is.</p></div>
      <button type="button" className="sb-button" onClick={() => startTask({ kind: "index", label: "Checking library health", steps: ["Scanning links", "Checking metadata", "Comparing content hashes", "Resolving citations"],  })}><RefreshCw size={14} />Check again</button></div>
    <div className="sb-kpis" role="group" aria-label="Problem counts">
      <button type="button" aria-pressed={!kind} onClick={() => setKind("")}><strong>{issues.length}</strong><span>All problems</span></button>
      {HEALTH_KINDS.map(([id, label]) => { const Icon = FIX_ICONS[id]; return <button key={id} type="button" aria-pressed={kind === id} onClick={() => setKind(kind === id ? "" : id)}><strong>{counts[id] || 0}</strong><span><Icon size={13} aria-hidden="true" />{label}</span></button>; })}
    </div>
    {selected.length ? <div className="sb-bulk"><strong>{selected.length} selected</strong>{ocrBatch.length ? <button type="button" className="sb-mini-button" onClick={() => openDialog("ocr", { docIds: ocrBatch.map(issue => issue.docId), issueIds: ocrBatch.map(issue => issue.id) })}><ScanText size={13} />Run OCR on {ocrBatch.length}</button> : null}<button type="button" className="sb-mini-button" onClick={() => { resolveHealth(selected); setSelected([]); notify("Ignored. These problems stay hidden until the documents change."); }}>Ignore</button></div> : null}
    {visible.length ? <HealthProblemList key={`${brain.id}:${kind}`} issues={visible} documents={documents} selected={selectedIds} onSelect={id => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])} onFix={fix} /> : <Empty icon={CircleCheck} title={kind ? "Nothing to fix here" : "Your library looks healthy"}>Links resolve, documents are searchable and citations point to one passage.</Empty>}
    <section className="sb-index-panel"><h4>Search index</h4><dl className="sb-facts">
      {[["State", brain.index.state === "updating" ? `Updating · ${brain.index.progress}%` : "Up to date"], ["Documents", brain.index.documents.toLocaleString()], ["Passages", brain.index.passages.toLocaleString()], ["Waiting for extraction", brain.index.pending], ["Extraction failed", brain.index.failed], ["Generation", `#${brain.index.generation} · as of ${new Date(brain.index.asOf).toLocaleTimeString()}`]].map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
    </dl>{brain.index.state === "updating" ? <ProgressBar value={brain.index.progress} label="Index progress" /> : null}<p className="sb-hint">Search keeps answering from the last published generation while new documents are processed.</p></section>
  </div>;
}

function NumberDraft({ label, value, onCommit }) {
  return <label className="sb-field"><span>{label}</span><DraftInput label={label} inputMode="numeric" value={String(value)} onCommit={text => { const number = Number.parseInt(text, 10); if (Number.isFinite(number) && number > 0) onCommit(number); }} /></label>;
}

function JobEditor({ job, brain, store, onDone }) {
  const [provider, setProvider] = useResearchSelection(job?.provider);
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const searches = store.savedSearches.filter(item => item.brainId === brain.id);
  const [draft, setDraft] = useState(() => job ? { ...structuredClone(job), sourceRules: jobSourceRules(job, collections, searches) } : { id: uid("job"), brainId: brain.id, name: "", recipe: "summarize-new", enabled: false, scope: "All documents", sourceRules: defaultJobSourceRules(), trigger: { kind: "manual", label: "Only when run manually" }, destination: "Summaries", limits: { documents: 10, turns: 3 }, review: true, lastRun: null });
  const set = patch => setDraft(current => ({ ...current, ...patch }));
  const sourceError = jobSourceRulesError(draft.sourceRules, collections, searches) || (draft.recipe === "refresh-comparison" && draft.sourceRules.location.kind !== "collection" ? "Comparison jobs need a collection as their source location." : "");
  const triggers = [["manual", "Only when run manually"], ["schedule", "On a schedule"], ["added", "When a document is added"], ["changed", "When a cited source changes"]];
  const [day, setDay] = useState(job?.trigger.day || "Mondays");
  const [time, setTime] = useState(job?.trigger.time || "09:00");
  return <form className="sb-job-editor" onSubmit={event => { event.preventDefault(); if (sourceError) return; const trigger = draft.trigger.kind === "schedule" ? { kind: "schedule", day, time, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, label: `${day} at ${time}` } : draft.trigger; saveJob({ ...draft, scope: jobScopeLabel(draft.sourceRules, collections, searches), provider, name: draft.name.trim() || RECIPES.find(([id]) => id === draft.recipe)[1], trigger }); notify(job ? "Job saved." : "Job created."); onDone(); }}>
    <h4>{job ? "Edit research job" : "New research job"}</h4>
    <div className="sb-form-grid">
      <label className="sb-field"><span>Name</span><input value={draft.name} placeholder={RECIPES.find(([id]) => id === draft.recipe)[1]} onChange={event => set({ name: event.target.value })} /></label>
      <label className="sb-field"><span>Recipe</span><select value={draft.recipe} onChange={event => set({ recipe: event.target.value, ...(event.target.value === "refresh-comparison" ? { sourceRules: { ...draft.sourceRules, time: { ...draft.sourceRules.time, mode: "all" }, onlyUnprocessed: false } } : {}) })}>{RECIPES.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><small>{RECIPES.find(([id]) => id === draft.recipe)[2]}</small></label>
      <label className="sb-field"><span>Trigger</span><select value={draft.trigger.kind} onChange={event => set({ trigger: { kind: event.target.value, label: triggers.find(([id]) => id === event.target.value)[1] } })}>{triggers.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select></label>
      {draft.trigger.kind === "schedule" ? <div className="sb-form-pair"><label className="sb-field"><span>Day</span><select value={day} onChange={event => setDay(event.target.value)}>{["Every day", "Weekdays", "Mondays", "Fridays"].map(item => <option key={item}>{item}</option>)}</select></label><label className="sb-field"><span>Time</span><input type="time" value={time} onChange={event => setTime(event.target.value)} /></label></div> : null}
      <label className="sb-field"><span>Save results to</span><DraftInput label="Destination folder" value={draft.destination} onCommit={destination => set({ destination })} /></label>
      <NumberDraft label="Most documents per run" value={draft.limits.documents} onCommit={documents => set({ limits: { ...draft.limits, documents } })} />
      <NumberDraft label="Most Rem turns per run" value={draft.limits.turns} onCommit={turns => set({ limits: { ...draft.limits, turns } })} />
    </div>
    <JobSourceRules rules={draft.sourceRules} onChange={sourceRules => set({ sourceRules })} job={draft} collections={collections} searches={searches} />
    {sourceError ? <p role="alert" className="sb-hint">{sourceError}</p> : null}
    <SecondBrainProvider value={provider} onChange={setProvider} />
    <label className="sb-check"><input type="checkbox" checked={draft.review !== false} onChange={event => set({ review: event.target.checked })} />Hold results as drafts until I review them</label>
    <label className="sb-check"><input type="checkbox" checked={draft.enabled} onChange={event => set({ enabled: event.target.checked })} />Enable the trigger now</label>
    <p className="sb-hint">Jobs keep the selected provider and model and this brain’s cloud-processing rule. Creating a job never runs an unrelated workflow.</p>
    <div className="sb-actions"><button type="submit" className="sb-button sb-primary" disabled={Boolean(sourceError)}>{job ? "Save job" : "Create job"}</button><button type="button" className="sb-button" onClick={onDone}>Cancel</button></div>
  </form>;
}

export function JobsView({ brain }) {
  const store = useSecondBrain();
  const jobs = store.jobs.filter(job => job.brainId === brain.id);
  const [editing, setEditing] = useState(null);
  const runNow = job => startTask({ kind: "job", label: `Running “${job.name}”`, jobId: job.id, provider: job.provider });
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Research jobs</h3><p>Reusable recipes for recurring research. Each shows exactly what it reads, when it runs, where it writes and how much it may do.</p></div><button type="button" className="sb-button sb-primary" onClick={() => setEditing("new")}><Plus size={14} />New job</button></div>
    {editing ? <JobEditor key={editing} job={editing === "new" ? null : jobs.find(job => job.id === editing)} brain={brain} store={store} onDone={() => setEditing(null)} /> : null}
    {jobs.length ? <ul className="sb-job-list">{jobs.map(job => <li key={job.id} className="sb-job">
      <div className="sb-job-head"><Repeat size={16} aria-hidden="true" /><strong>{job.name}</strong>
        <label className="sb-switch"><input type="checkbox" role="switch" checked={job.enabled} onChange={() => saveJob({ ...job, enabled: !job.enabled })} /><span>{job.enabled ? "On" : "Off"}</span></label></div>
      <dl className="sb-job-facts"><div><dt>Reads</dt><dd>{describeJobSources(jobSourceRules(job, store.collections.filter(item => item.brainId === brain.id), store.savedSearches.filter(item => item.brainId === brain.id)), store.collections, store.savedSearches)}</dd></div><div><dt>Runs</dt><dd><CalendarClock size={12} aria-hidden="true" />{job.trigger.label}</dd></div><div><dt>Writes to</dt><dd className="sb-mono">{job.destination}</dd></div><div><dt>Limits</dt><dd>{job.limits.documents} documents · {job.limits.turns} Rem turns</dd></div></dl>
      <div className="sb-job-foot">{job.lastRun ? <span>Last run {formatDay(job.lastRun.at, { relative: true }).toLowerCase()} · <Pill tone={job.lastRun.status === "done" ? "success" : "warning"}>{job.lastRun.status === "done" ? "Completed" : "Needs review"}</Pill> {job.lastRun.detail}</span> : <span className="sb-muted">Never run</span>}
        {job.lastRun?.remainingCount ? <span className="sb-muted">{job.lastRun.remainingCount} sources waiting for a later run.</span> : null}
        <div className="sb-actions"><small>{job.provider?.profile || job.provider?.id || "Choose a provider before enabling"}</small><button type="button" className="sb-mini-button" onClick={() => runNow(job)}><Play size={12} />Run now</button><button type="button" className="sb-mini-button" onClick={() => setEditing(job.id)}><Pencil size={12} />Edit</button><button type="button" className="sb-icon-button" aria-label={`Delete ${job.name}`} title="Delete job" onClick={() => removeJob(job.id)}><Trash2 size={14} /></button></div></div>
    </li>)}</ul> : !editing ? <Empty icon={Repeat} title="No research jobs" actions={<button type="button" className="sb-button sb-primary" onClick={() => setEditing("new")}>New job</button>}>Summarize new documents, prepare a weekly digest or refresh a comparison when its sources change.</Empty> : null}
  </div>;
}

export function BrainSettingsView({ brain }) {
  const { provider } = useContext(ResearchProviderContext);
  const store = useSecondBrain();
  const settings = brain.settings;
  const set = patch => updateBrain(brain.id, current => ({ settings: { ...current.settings, ...patch } }));
  const formats = [["markdown", "Markdown"], ["html", "HTML"], ["text", "Plain text"], ["pdf", "PDF"], ["ocr", "Scanned pages (OCR)"]];
  const collections = useMemo(() => store.collections.filter(item => item.brainId === brain.id).length, [store.collections, brain.id]);
  return <div className="sb-page sb-settings">
    <div className="sb-page-head"><div><h3>Brain settings</h3><p>Settings for {brain.name}. Original files are never rewritten by these options.</p></div></div>
    <section><h4>General</h4>
      <label className="sb-field"><span>Name</span><DraftInput label="Brain name" value={brain.name} onCommit={name => name.trim() && updateBrain(brain.id, { name: name.trim() })} /></label>
      <div className="sb-field"><span>Folder</span><div className="sb-path-row"><code className="sb-mono">{brain.root}</code><button type="button" className="sb-mini-button" onClick={() => window.goferDesktop?.workspace?.revealPath?.(brain.root)}><FolderOpen size={12} />Reveal</button><button type="button" className="sb-mini-button" onClick={() => openDialog("locate", { brainId: brain.id })}>Change…</button></div><small>Moving the folder keeps the brain’s identity. Choose its new location here.</small></div>
    </section>
    <section><h4>Library metadata</h4>
      <fieldset className="sb-checklist"><legend className="sb-sr-only">Where library metadata is stored</legend>
        <label><input type="radio" name="sb-metadata" checked={settings.metadata === "portable"} onChange={() => set({ metadata: "portable" })} /><span>Store in the brain folder<small>Collections, citations and saved searches live in <code className="sb-mono">.raticode/</code> and travel with the folder.</small></span></label>
        <label><input type="radio" name="sb-metadata" checked={settings.metadata === "app"} onChange={() => set({ metadata: "app" })} /><span>Store in Raticode only<small>Use for read-only or shared folders. Metadata stays on this computer and is included in brain exports.</small></span></label>
      </fieldset>
      <p className="sb-hint">Search indexes are always kept on this computer and can be rebuilt at any time. {collections} collection{collections === 1 ? "" : "s"} currently use this setting.</p>
    </section>
    <section><h4>Search</h4>
      <label className="sb-check"><input type="checkbox" checked={settings.semantic} onChange={event => set({ semantic: event.target.checked })} />Enable related-meaning search</label>
      {settings.semantic ? <fieldset className="sb-checklist sb-indent"><legend>Embedding processing</legend>
        <label><input type="radio" name="sb-embeddings" checked={settings.embeddings === "local"} onChange={() => set({ embeddings: "local" })} /><span>On this computer<small>Downloads a public BGE model once, then processes document text on this computer.</small></span></label>
        <label data-disabled={!settings.allowCloud || undefined}><input type="radio" name="sb-embeddings" disabled={!settings.allowCloud} checked={settings.embeddings === "cloud"} onChange={() => set({ embeddings: "cloud" })} /><span>Cloud embedding provider<small>Uses the API provider profile selected below. Document passages are sent to that provider.</small></span></label>
        {settings.embeddings === "cloud" ? <>
          <label className="sb-field"><span>API provider profile</span><select value={settings.embeddingProfile || ""} onChange={event => set({ embeddingProfile: event.target.value })}><option value="">Default API credentials</option>{(store.capabilities?.embeddingProfiles || []).map(name => <option key={name}>{name}</option>)}</select><small>Configure credentials in Raticode provider profiles. Keys stay outside the brain.</small></label>
          <label className="sb-field"><span>Embedding model</span><DraftInput label="Embedding model" value={settings.embeddingModel || "text-embedding-3-small"} onCommit={embeddingModel => set({ embeddingModel })} /></label>
        </> : null}
      </fieldset> : null}
      <p className="sb-hint">Exact search always works offline, without a model. Changing Rem’s chat model never rebuilds this index.</p>
    </section>
    <section><h4>Privacy</h4>
      <label className="sb-check"><input type="checkbox" checked={!settings.researchProvider && !settings.researchProfile} onChange={event => set({ researchProvider: event.target.checked ? null : { id: provider.id, model: provider.model, profile: provider.profile || null, effort: provider.effort || "" }, researchProfile: "" })} />Use the current Rem provider by default</label>
      <SecondBrainProvider value={provider} onChange={value => set({ researchProvider: { id: value.id, model: value.model || "cli-default", profile: value.profile || null, effort: value.effort || "" }, researchProfile: "" })} />
      <label className="sb-check"><input type="checkbox" checked={settings.allowCloud} onChange={event => set({ allowCloud: event.target.checked, embeddings: event.target.checked ? settings.embeddings : "local" })} />Allow cloud processing for background jobs, embeddings and Rem chat</label>
      <p className="sb-hint">When off, reports and jobs require a local API profile, and cloud embedding is disabled. Attaching this brain to Rem’s chat pane requires cloud access.</p>
    </section>
    <section><h4>Formats</h4>
      <div className="sb-format-grid">{formats.map(([id, label]) => <label key={id} className="sb-check"><input type="checkbox" checked={settings.formats[id]} disabled={id === "ocr" && !settings.formats.pdf} onChange={event => set({ formats: { ...settings.formats, [id]: event.target.checked } })} />{label}</label>)}</div>
      {settings.formats.ocr ? <label className="sb-field sb-narrow"><span>OCR language</span><select value={settings.ocrLanguage} onChange={event => set({ ocrLanguage: event.target.value })}>{[["eng", "English"], ["deu", "German"], ["fra", "French"], ["spa", "Spanish"], ["jpn", "Japanese"]].map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label> : null}
      <p className="sb-hint">OCR text is labeled as recognized text and never replaces the original page.</p>
    </section>
    <section><h4>Interoperability</h4>
      <label className="sb-check"><input type="checkbox" checked={settings.obsidian} onChange={event => set({ obsidian: event.target.checked })} />Read Obsidian frontmatter, tags, wiki links and block references</label>
      <p className="sb-hint">The <code className="sb-mono">.obsidian</code> folder is left untouched and never indexed. Plugins are not run.</p>
      <div className="sb-actions"><button type="button" className="sb-button" onClick={() => openDialog("bibliography")}><Link2 size={14} />Import bibliography…</button><button type="button" className="sb-button" onClick={() => openDialog("bibliography", { exporting: true })}>Export bibliography…</button></div>
    </section>
    <section><h4>Index</h4>
      <dl className="sb-facts">{[["Documents", brain.index.documents.toLocaleString()], ["Passages", brain.index.passages.toLocaleString()], ["Generation", `#${brain.index.generation}`], ["As of", formatTime(brain.index.asOf)]].map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl>
      <div className="sb-actions"><button type="button" className="sb-button" onClick={() => startTask({ kind: "index", label: `Rebuilding the ${brain.name} index`, steps: ["Scanning folder", "Extracting text", "Building passages", "Publishing generation"],  })}><RefreshCw size={14} />Rebuild index</button><button type="button" className="sb-button" onClick={() => navigate("health")}><FileWarning size={14} />Library health</button></div>
      <p className="sb-hint">Rebuilding replaces only the cache. Documents, citations and report evidence are kept.</p>
    </section>
    <section className="sb-danger-zone"><h4>Remove brain</h4><p className="sb-hint">Removes {brain.name} from Raticode’s list. The folder and every file in it stay where they are.</p><button type="button" className="sb-button" data-danger onClick={() => openDialog("remove", { brainId: brain.id })}><HardDrive size={14} />Remove from list…</button></section>
  </div>;
}
