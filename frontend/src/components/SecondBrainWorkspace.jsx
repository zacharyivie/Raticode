import { useEffect, useMemo } from "react";
import { ArrowLeft, CircleAlert, CircleCheck, Download, FolderOpen, HardDrive, HeartPulse, Inbox, Info, Library, Loader2, Repeat, ScrollText, Search, Settings2, Trash2, Upload, X } from "lucide-react";
import { useSecondBrain, viewFor, navigate, openDialog, cancelTask, dismissTask, dismissToast, dismissError, adoptLegacyRoot, ensureLibrary, setResearchProvider, refreshLibrary, startTask } from "../lib/secondBrainStore.js";
import { BrainMark, Pill, ProgressBar } from "./SecondBrainParts.jsx";
import SearchView from "./SecondBrainSearch.jsx";
import { CollectionsView } from "./SecondBrainCollections.jsx";
import { ReportsView } from "./SecondBrainReports.jsx";
import { InboxView, HealthView, JobsView, BrainSettingsView } from "./SecondBrainLibrary.jsx";
import SecondBrainDialogs from "./SecondBrainDialogs.jsx";
import "./SecondBrainWorkspace.css";
import { useProviderCapabilities } from "./ProviderModelEffortFields.jsx";
import { ResearchProviderContext, researchProviderDetails } from "./SecondBrainProvider.jsx";

const SECTIONS = [["search", "Search", Search], ["collections", "Collections", Library], ["reports", "Reports", ScrollText], ["inbox", "Inbox", Inbox], ["health", "Library health", HeartPulse], ["jobs", "Research jobs", Repeat]];

const TOAST_ICONS = { success: CircleCheck, danger: CircleAlert, warning: CircleAlert, info: Info };

// Task receipts and notifications float in one corner stack so they never push the page content around.
function TaskToast({ task }) {
  const step = task.steps[task.step] || "";
  const tone = task.state === "done" ? "success" : task.state === "failed" ? "danger" : task.state === "cancelled" ? "info" : "progress";
  // Finished and cancelled receipts clear themselves; failures stay until dismissed so the error can be read.
  useEffect(() => {
    if (task.state !== "done" && task.state !== "cancelled") return undefined;
    const timer = setTimeout(() => dismissTask(task.id), task.state === "done" ? 10000 : 6000);
    return () => clearTimeout(timer);
  }, [task.id, task.state]);
  const Icon = TOAST_ICONS[tone];
  return <div className="sb-toast" data-tone={tone} role={tone === "danger" ? "alert" : "status"}>
    {task.state === "running" ? <Loader2 size={15} className="sb-spin" aria-hidden="true" /> : <Icon size={15} aria-hidden="true" />}
    <p className="sb-toast-text"><strong>{task.label}</strong><small>{task.state === "running" ? `${step}${step ? " · " : ""}${task.progress}%` : task.state === "done" ? task.result?.path || task.result?.reason || "Finished" : task.state === "failed" ? task.error : "Cancelled. Files already saved are kept."}</small></p>
    <div className="sb-toast-actions">
      {task.state === "running" ? <button type="button" className="sb-mini-button" onClick={() => cancelTask(task.id)}>Cancel</button>
        : <>{task.state === "failed" && task.request ? <button type="button" className="sb-mini-button" onClick={async () => { if (await startTask(task.request)) dismissTask(task.id); }}>Retry</button> : null}<button type="button" className="sb-icon-button" aria-label="Dismiss" title="Dismiss" onClick={() => dismissTask(task.id)}><X size={14} /></button></>}
    </div>
    {task.state === "running" ? <ProgressBar value={task.progress} label={`${task.label} progress`} /> : null}
  </div>;
}

function Toast({ tone = "success", message, action, onDismiss }) {
  const Icon = TOAST_ICONS[tone] || CircleCheck;
  return <div className="sb-toast" data-tone={tone} role={tone === "danger" ? "alert" : "status"}>
    <Icon size={15} aria-hidden="true" />
    <p className="sb-toast-text">{message}</p>
    <div className="sb-toast-actions">{action}<button type="button" className="sb-icon-button" aria-label="Dismiss notification" title="Dismiss" onClick={onDismiss}><X size={14} /></button></div>
  </div>;
}

export default function SecondBrainWorkspace({ onClose, assistant = {}, memorySettings = {} }) {
  const providerState = useProviderCapabilities();
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const view = viewFor(store);
  useEffect(() => { void ensureLibrary().then(() => adoptLegacyRoot(memorySettings.secondBrainRoot)); }, [memorySettings.secondBrainRoot]);
  useEffect(() => {
    if (!store.toast) return undefined;
    // Errors stay longer so they can be read; confirmations get out of the way quickly.
    const timer = setTimeout(dismissToast, store.toast.tone === "danger" ? 12000 : 4000);
    return () => clearTimeout(timer);
  }, [store.toast]);
  const counts = useMemo(() => {
    const documents = store.documents.filter(doc => doc.brainId === brain?.id);
    const ids = new Set(documents.map(doc => doc.id));
    return {
      inbox: documents.filter(doc => doc.inbox).length,
      health: store.health.filter(issue => ids.has(issue.docId)).length,
    };
  }, [store.documents, store.health, brain?.id]);
  const tasks = store.tasks.filter(task => task.brainId === brain?.id || !task.brainId);
  const researchProfile = store.capabilities?.researchProfiles?.find(item => item.name === brain?.settings.researchProfile);
  const provider = useMemo(() => researchProviderDetails(brain?.settings.researchProvider || (researchProfile ? { id: researchProfile.id, profile: researchProfile.name, model: researchProfile.model || "cli-default", effort: researchProfile.effort || "" } : { id: assistant.provider || "codex", profile: assistant.profile || null, model: assistant.model || "cli-default", effort: assistant.effort || "" }), providerState.capabilities, store.capabilities?.researchProfiles), [brain?.settings.researchProvider, researchProfile, assistant.provider, assistant.profile, assistant.model, assistant.effort, providerState.capabilities, store.capabilities?.researchProfiles]);
  useEffect(() => { setResearchProvider(provider); }, [provider]);
  return <ResearchProviderContext.Provider value={{ ...providerState, provider, profiles: store.capabilities?.researchProfiles || [] }}><section className="sb-workspace" aria-label="Second Brain workspace">
    <header className="sb-toolbar">
      <button className="sb-icon-button" type="button" aria-label="Back to editor" title="Back to editor" onClick={onClose}><ArrowLeft size={17} /></button>
      {brain ? <div className="sb-toolbar-title"><BrainMark brain={brain} /><div><h2>{brain.name}</h2><small className="sb-mono" title={brain.root}>{brain.root}</small></div></div> : <div className="sb-toolbar-title"><h2>Second Brain</h2></div>}
      {brain && brain.state === "available" ? <div className="sb-toolbar-actions">
        {brain.index.state === "updating" ? <Pill tone="brand" title={`${brain.index.pending} documents waiting for extraction`}>Indexing · {brain.index.progress}%</Pill> : brain.index.state === "failed" ? <Pill tone="warning" title={brain.index.error}>Index needs attention</Pill> : null}
        <button className="sb-button" type="button" onClick={() => openDialog("export")}><Download size={14} />Export</button>
        <button className="sb-icon-button" type="button" aria-label="Brain settings" title="Brain settings" aria-pressed={view.section === "settings"} onClick={() => navigate("settings")}><Settings2 size={17} /></button>
      </div> : null}
    </header>
    {brain && brain.state === "available" ? <nav className="sb-tabs" aria-label="Second Brain sections">
      {SECTIONS.map(([id, label, Icon]) => <button key={id} type="button" aria-current={view.section === id ? "page" : undefined} onClick={() => navigate(id, id === "collections" ? { collectionId: "" } : id === "reports" ? { reportId: "" } : {})}><Icon size={15} aria-hidden="true" />{label}{counts[id] ? <span className="sb-count" aria-label={`${counts[id]} items`}>{counts[id]}</span> : null}</button>)}
    </nav> : null}
    <div className="sb-body">
      {!store.loaded ? <p role="status" className="sb-center sb-hint">Loading Second Brain…</p> : !brain ? <div className="sb-center"><div className="sb-welcome"><Library size={30} aria-hidden="true" /><h3>Open a Second Brain</h3><p>A brain is an ordinary folder of notes, reports and documents. Raticode indexes it so you can search, read and cite it with Rem. Files stay usable in other apps.</p>
        <div className="sb-empty-actions"><button className="sb-button sb-primary" type="button" onClick={() => openDialog("open-folder")}><FolderOpen size={14} />Open existing folder</button><button className="sb-button" type="button" onClick={() => openDialog("create")}>Create brain</button><button className="sb-button" type="button" onClick={() => openDialog("import")}><Upload size={14} />Import package</button></div></div></div>
        : brain.state === "unavailable" ? <div className="sb-center"><div className="sb-welcome"><HardDrive size={30} aria-hidden="true" /><h3>{brain.name} is unavailable</h3><p>Raticode can’t reach <code className="sb-mono">{brain.root}</code>. Connect the drive that holds it, or locate the folder if it moved. Collections, citations and saved searches are kept until you remove the brain.</p>
          <div className="sb-empty-actions"><button className="sb-button sb-primary" type="button" onClick={() => openDialog("locate", { brainId: brain.id })}><FolderOpen size={14} />Locate folder</button><button className="sb-button" type="button" onClick={() => openDialog("remove", { brainId: brain.id })}><Trash2 size={14} />Remove from list</button></div></div></div>
        : view.section === "search" ? <SearchView key={brain.id} brain={brain} />
        : view.section === "collections" ? <CollectionsView brain={brain} provider={provider} />
        : view.section === "reports" ? <ReportsView brain={brain} />
        : view.section === "inbox" ? <InboxView brain={brain} />
        : view.section === "health" ? <HealthView key={brain.id} brain={brain} />
        : view.section === "jobs" ? <JobsView brain={brain} />
        : view.section === "settings" ? <BrainSettingsView brain={brain} />
        : null}
    </div>
    {tasks.length || store.toast || store.error ? <div className="sb-toasts">
      {store.error ? <Toast tone="danger" message={store.error} action={<button type="button" className="sb-mini-button" onClick={() => refreshLibrary()}>Retry</button>} onDismiss={dismissError} /> : null}
      {tasks.map(task => <TaskToast key={task.id} task={task} />)}
      {store.toast ? <Toast key={store.toast.id} tone={store.toast.tone} message={store.toast.message} onDismiss={dismissToast} /> : null}
    </div> : null}
    <SecondBrainDialogs provider={provider} />
  </section></ResearchProviderContext.Provider>;
}
