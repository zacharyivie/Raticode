import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Clock3, Download, Eye, FolderOpen, FolderPlus, HardDrive, MoreHorizontal, PackageOpen, Plus, Quote, ScrollText, Search, Settings2, TriangleAlert, Trash2, Upload, Library } from "lucide-react";
import { searchLibrary, formatDay } from "../lib/secondBrain.js";
import { useSecondBrain, setActiveBrain, applySavedSearch, navigate, openDialog, openDocument, updateSearch, libraryFor, viewFor, ensureLibrary } from "../lib/secondBrainStore.js";
import { BrainMark, Menu, TypeIcon, ProgressBar, useDismiss } from "./SecondBrainParts.jsx";
import "./SecondBrainSidebar.css";

const VIEW_ICONS = { clock: Clock3, eye: Eye, report: ScrollText, quote: Quote, warning: TriangleAlert };

function Group({ title, count, action, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return <section className="sb-side-group" aria-label={title}>
    <div className="sb-side-group-heading">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}<span>{title}</span>{count != null ? <small>{count}</small> : null}</button>
      {action}
    </div>
    {open ? <div className="sb-side-rows">{children}</div> : null}
  </section>;
}

function BrainSwitcher({ brains, active, onOpen }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef(null);
  const panel = useRef(null);
  const refs = useRef([trigger, panel]).current;
  useDismiss(open, refocus => { setOpen(false); if (refocus) trigger.current?.focus(); }, refs);
  const choose = id => { setActiveBrain(id); setOpen(false); onOpen(); };
  return <div className="sb-switcher">
    <button ref={trigger} type="button" className="sb-switcher-trigger" aria-haspopup="listbox" aria-expanded={open} aria-label={`Current brain: ${active?.name || "None"}. Switch brain`} onClick={() => setOpen(!open)}>
      <BrainMark brain={active} />
      <span className="sb-switcher-text"><strong>{active?.name || "No brain open"}</strong><small title={active?.root}>{active ? (active.state === "unavailable" ? "Folder unavailable" : active.root.replace(/^\/home\/[^/]+/, "~")) : "Open or create a brain"}</small></span>
      <ChevronDown size={14} aria-hidden="true" />
    </button>
    {open ? <div ref={panel} className="sb-switcher-panel" role="dialog" aria-label="Opened brains">
      <p className="sb-switcher-label">Opened brains</p>
      <ul role="listbox" aria-label="Opened brains">
        {brains.map(brain => <li key={brain.id} role="option" aria-selected={brain.id === active?.id}>
          <button type="button" className="sb-switcher-option" onClick={() => choose(brain.id)}>
            <BrainMark brain={brain} size="sm" />
            <span className="sb-switcher-text"><strong>{brain.name}</strong><small className="sb-mono">{brain.root.replace(/^\/home\/[^/]+/, "~")}</small>
              <small>{brain.state === "unavailable" ? <span className="sb-unavailable"><HardDrive size={11} aria-hidden="true" />Unavailable · last opened {formatDay(brain.lastOpened, { relative: true })}</span> : <>Opened {formatDay(brain.lastOpened, { relative: true }).toLowerCase()} · {brain.index.documents.toLocaleString()} documents</>}</small></span>
            {brain.id === active?.id ? <Check size={14} aria-label="Current brain" /> : null}
          </button>
          <Menu label={`${brain.name} actions`} triggerClassName="sb-side-icon" trigger={<MoreHorizontal size={14} />} items={[
            brain.state === "unavailable" ? { label: "Locate folder…", icon: FolderOpen, onSelect: () => { setOpen(false); openDialog("locate", { brainId: brain.id }); } } : null,
            { label: "Brain settings", icon: Settings2, disabled: brain.state === "unavailable", onSelect: () => { setActiveBrain(brain.id); navigate("settings"); setOpen(false); onOpen(); } },
            { label: "Export…", icon: Download, disabled: brain.state === "unavailable", onSelect: () => { setActiveBrain(brain.id); setOpen(false); onOpen(); openDialog("export"); } },
            { separator: true, key: "sep" },
            { label: "Remove from list…", icon: Trash2, danger: true, onSelect: () => { setOpen(false); openDialog("remove", { brainId: brain.id }); } },
          ]} />
        </li>)}
      </ul>
      <div className="sb-switcher-actions">
        <button type="button" onClick={() => { setOpen(false); openDialog("open-folder"); onOpen(); }}><FolderOpen size={14} aria-hidden="true" />Open folder</button>
        <button type="button" onClick={() => { setOpen(false); openDialog("create"); onOpen(); }}><FolderPlus size={14} aria-hidden="true" />Create</button>
        <button type="button" onClick={() => { setOpen(false); openDialog("import"); onOpen(); }}><PackageOpen size={14} aria-hidden="true" />Import</button>
      </div>
    </div> : null}
  </div>;
}

export default function SecondBrainSidebar({ active, open, onOpen }) {
  const store = useSecondBrain();
  useEffect(() => { if (active) void ensureLibrary(); }, [active]);
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const view = viewFor(store);
  const library = useMemo(() => libraryFor(store), [store]);
  const [query, setQuery] = useState("");
  const savedSearches = store.savedSearches.filter(item => item.brainId === "*" || item.brainId === brain?.id);
  const counts = useMemo(() => Object.fromEntries(savedSearches.map(item => [item.id, searchLibrary(library.documents, { text: item.text, filters: { ...item.filters, brains: [] } }, library).results.length])), [savedSearches, library]);
  const collections = store.collections.filter(item => item.brainId === brain?.id);
  const reports = store.reports.filter(item => item.brainId === brain?.id);
  const recent = view.tabs.length ? view.tabs.map(tab => library.documents.find(doc => doc.id === tab.docId)).filter(Boolean) : [...library.documents].sort((a, b) => (b.dates.modified || "").localeCompare(a.dates.modified || "")).slice(0, 5);
  const current = (section, id = "") => open && view.section === section && (!id || view.collectionId === id || view.reportId === id || view.search.savedId === id);
  const go = (section, extra) => { navigate(section, extra); onOpen(); };
  const unavailable = brain?.state === "unavailable";
  return <section id="sidebar-panel-second-brain" role="tabpanel" aria-labelledby="sidebar-tab-second-brain" hidden={!active} className={`sb-sidebar min-h-0 min-w-0 flex-1 flex-col text-ink ${active ? "flex" : "hidden"}`}>
    <div className="sb-sidebar-heading ui-panel-header"><strong className="ui-section-title">Second Brain</strong>
      <Menu label="Add a brain" triggerClassName="sb-side-icon" trigger={<Plus size={15} />} items={[
        { label: "Open existing folder…", icon: FolderOpen, onSelect: () => { openDialog("open-folder"); onOpen(); } },
        { label: "Create brain…", icon: FolderPlus, onSelect: () => { openDialog("create"); onOpen(); } },
        { label: "Import brain package…", icon: Upload, onSelect: () => { openDialog("import"); onOpen(); } },
      ]} />
    </div>
    {!store.loaded ? <p role="status" className="sb-side-hint">Loading brains…</p> : null}
    {store.brains.length ? <BrainSwitcher brains={store.brains} active={brain} onOpen={onOpen} /> : null}
    {!store.loaded ? null : !store.brains.length ? <div className="sb-side-empty"><Library size={24} aria-hidden="true" /><p>A brain is a folder of notes, reports and documents you can search, read and cite with Rem.</p>
      <button type="button" onClick={() => { openDialog("open-folder"); onOpen(); }}><FolderOpen size={14} />Open folder</button>
      <button type="button" onClick={() => { openDialog("create"); onOpen(); }}><FolderPlus size={14} />Create brain</button></div> : unavailable ? <div className="sb-side-empty"><HardDrive size={22} aria-hidden="true" /><p>This brain’s folder is not available. Connect the drive or locate the folder. Its history is kept.</p><button type="button" onClick={() => { openDialog("locate", { brainId: brain.id }); onOpen(); }}><FolderOpen size={14} />Locate folder</button></div> : <>
      <form role="search" className="sb-side-search" onSubmit={event => { event.preventDefault(); updateSearch({ text: query }); go("search"); }}>
        <Search size={14} aria-hidden="true" />
        <input aria-label={`Search ${brain?.name}`} placeholder={`Search ${brain?.name}`} value={query} onChange={event => setQuery(event.target.value)} />
      </form>
      <div className="sb-side-scroll">
        <Group title="Saved searches">
          {savedSearches.map(item => { const Icon = item.builtIn ? VIEW_ICONS[item.icon] || Search : Search; return <button key={item.id} type="button" className="sb-side-row" aria-current={current("search", item.id) ? "page" : undefined} onClick={() => { applySavedSearch(item.id); onOpen(); }}><Icon size={14} aria-hidden="true" /><span>{item.name}</span><small>{counts[item.id]}</small></button>; })}
        </Group>
        <Group title="Collections" count={collections.length} action={<button type="button" className="sb-side-icon" aria-label="New collection" title="New collection" onClick={() => { openDialog("new-collection"); onOpen(); }}><Plus size={14} /></button>}>
          {collections.map(item => <button key={item.id} type="button" className="sb-side-row" aria-current={current("collections", item.id) ? "page" : undefined} onClick={() => go("collections", { collectionId: item.id })}><Library size={14} aria-hidden="true" /><span>{item.name}</span><small>{item.sections.reduce((total, section) => total + section.items.length, 0)}</small></button>)}
          {!collections.length ? <p className="sb-side-hint">Collect documents and passages for a question.</p> : null}
        </Group>
        <Group title="Reports" count={reports.length}>
          {reports.map(item => { const changed = item.citations.some(citation => citation.state === "changed" || citation.state === "relocate"); return <button key={item.id} type="button" className="sb-side-row" aria-current={current("reports", item.id) ? "page" : undefined} onClick={() => go("reports", { reportId: item.id })}><ScrollText size={14} aria-hidden="true" /><span>{item.title}</span>{changed ? <TriangleAlert size={13} className="sb-side-warn" aria-label="Sources changed" /> : null}</button>; })}
        </Group>
        <Group title={view.tabs.length ? "Open documents" : "Recent documents"}>
          {recent.map(doc => <button key={doc.id} type="button" className="sb-side-row" title={doc.path} aria-current={open && view.section === "search" && view.activeDocId === doc.id ? "page" : undefined} onClick={() => { openDocument(doc.id, { preview: false }); onOpen(); }}><TypeIcon type={doc.type} size={14} /><span>{doc.title}</span></button>)}
        </Group>
      </div>
      {brain ? <button type="button" className="sb-side-status" onClick={() => go("settings")} aria-label={`Index status: ${brain.index.state === "updating" ? `updating, ${brain.index.progress}% complete` : "up to date"}. Open brain settings`}>
        <span>{brain.index.state === "updating" ? `Index updating · ${brain.index.progress}%` : "Index up to date"}</span>
        <small>{brain.index.documents.toLocaleString()} documents · as of {new Date(brain.index.asOf).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>
        {brain.index.state === "updating" ? <ProgressBar value={brain.index.progress} label="Index progress" /> : null}
      </button> : null}
    </>}
  </section>;
}
