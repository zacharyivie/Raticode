import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Building2, Clock, Folder, Network, Pause, Play, RefreshCw, Search, Settings, SquareKanban, Users, X } from "lucide-react";
import { KINDS, buildModel, hoursStatus, initialsOf, moveRule, needsYou, reviewCaution, searchEntities } from "../lib/organizationModel.js";
import { organizationRequest } from "../lib/organizations.js";
import { ORGANIZATION_BOARD_FILTERS, liveOrganizationFilters, organizationBoard, useBoardFilters } from "../lib/boardFilters.js";
import { startPolling } from "../lib/refresh.js";
import OrganizationWorkspace, { StartSheet } from "./OrganizationWorkspace.jsx";
import OrganizationDetail from "./OrganizationSheet.jsx";
import { KnowledgeView } from "./OrganizationKnowledge.jsx";
import { IssuesView, OverviewView, ProjectsView, WorkersView } from "./OrganizationViews.jsx";
import "./OrganizationHome.css";
import { AutoBadge } from "./OrganizationAutoMode.jsx";

const TABS = [["org", "Organization", Building2], ["projects", "Projects", Folder], ["issues", "Issues", SquareKanban], ["workers", "Workers", Users], ["knowledge", "Knowledge", Network]];
const clock = at => new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const BOARD = ORGANIZATION_BOARD_FILTERS;
const KNOWLEDGE = () => ({ view: "graph", project: "all", kinds: new Set(Object.keys(KINDS)), suggested: true, tickets: false, query: "" });

function Palette({ model, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const items = searchEntities(model, query);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  return <div className="oh-scrim" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="oh-palette" role="dialog" aria-label="Find anything">
      <input ref={input} placeholder="Find a ticket, epic, note, worker or project" aria-label="Search" autoComplete="off" value={query}
        onChange={event => { setQuery(event.target.value); setIndex(0); }}
        onKeyDown={event => {
          if (event.key === "Escape") onClose();
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setIndex(current => (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1)); }
          if (event.key === "Enter" && items[index]) onPick(`${items[index].type}:${items[index].id}`);
        }} />
      <ul role="listbox">{items.map((item, position) => <li key={`${item.type}:${item.id}`}><button type="button" role="option" aria-selected={position === index} onMouseEnter={() => setIndex(position)} onClick={() => onPick(`${item.type}:${item.id}`)}>
        <span className="type">{item.type === "ticket" ? "Ticket" : item.sub}</span><span>{item.type === "ticket" && <span className="oh-key oh-muted">{item.key} </span>}{item.label}</span><i className="oh-dot" style={{ color: item.color }} /></button></li>)}
        {!items.length && <li className="oh-empty">Nothing matches</li>}</ul>
    </div>
  </div>;
}

// The first read: a quiet outline of the page while it loads, or a way out when it can't.
function FirstLoad({ failure, onRetry, onClose }) {
  if (failure) return <section className="oh" aria-label="Organization">
    <div className="oh-main"><div className="oh-view"><div className="oh-panel oh-blank oh-fail" role="alert">
      <AlertTriangle size={26} aria-hidden="true" />
      <h2>Couldn&apos;t open this organization</h2>
      <p>{failure}</p>
      <p className="oh-muted">Raticode keeps trying every few seconds.</p>
      <div className="oh-row"><button className="oh-btn primary" type="button" onClick={onRetry}><RefreshCw size={14} />Try again</button><button className="oh-btn" type="button" onClick={onClose}><ArrowLeft size={14} />Back to organizations</button></div>
    </div></div></div>
  </section>;
  return <section className="oh" aria-label="Organization" aria-busy="true">
    <div className="oh-main">
      <header className="oh-top"><span className="oh-skel" style={{ width: 30, height: 30 }} /><span className="oh-skel" style={{ width: 180, height: 16 }} /><span className="grow" /><span className="oh-skel" style={{ width: 72, height: 30 }} /></header>
      <div className="oh-tabs oh-skel-tabs" aria-hidden="true">{TABS.map(([id]) => <span key={id} className="oh-skel" style={{ width: 92, height: 14 }} />)}</div>
      <div className="oh-view">
        <p className="oh-sr" role="status">Loading organization…</p>
        <div className="oh-panel oh-skel-panel" aria-hidden="true"><span className="oh-skel" style={{ width: "55%", height: 22 }} /><span className="oh-skel" style={{ width: "35%", height: 14 }} /></div>
        <div className="oh-grid-2" aria-hidden="true"><div className="oh-panel oh-skel-panel tall"><span className="oh-skel" style={{ width: "40%", height: 14 }} /><span className="oh-skel" /><span className="oh-skel" /></div><div className="oh-panel oh-skel-panel tall"><span className="oh-skel" style={{ width: "40%", height: 14 }} /><span className="oh-skel" /><span className="oh-skel" /><span className="oh-skel" /></div></div>
      </div>
    </div>
  </section>;
}

export default function OrganizationHome(props) {
  const { organizationId, onClose } = props;
  const [mode, setMode] = useState("home");
  if (organizationId === "new" || mode === "settings") {
    return <OrganizationWorkspace {...props} onClose={organizationId === "new" ? onClose : () => setMode("home")} />;
  }
  return <Home {...props} openSettings={() => setMode("settings")} />;
}

function Home({ organizationId, onClose, openSettings, defaults = {} }) {
  const [organization, setOrganization] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  // A failed background read. The page keeps what it last loaded and says how old it is.
  const [stale, setStale] = useState(null);
  const [loadedAt, setLoadedAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("org");
  const [projectId, setProjectId] = useState(null);
  const [trail, setTrail] = useState([]);
  const [palette, setPalette] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [starting, setStarting] = useState(false);
  const [kb, setKb] = useState(KNOWLEDGE);
  const [kbFocus, setKbFocus] = useState(null);
  const version = useRef("");
  const view = useRef(null);
  const model = useMemo(() => buildModel(organization), [organization]);
  // This organization's board filters, kept on this computer across visits and restarts.
  const liveFilters = useCallback(value => organization ? liveOrganizationFilters(value, model) : value, [organization, model]);
  const [filters, setFilters] = useBoardFilters(organizationBoard(organizationId), BOARD, liveFilters);

  const toast = useCallback((text, action) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(current => [...current.slice(-1), { id, text, action }]);
    setTimeout(() => setToasts(current => current.filter(item => item.id !== id)), action ? 6000 : 3500);
  }, []);

  const refresh = useCallback(async (force = false) => {
    const data = await organizationRequest("", "read", organizationId, force ? {} : { sinceVersion: version.current });
    setStale(null); setLoadedAt(Date.now());
    if (!data.notModified) {
      version.current = data.version;
      setOrganization(data);
      organizationRequest("", "events", organizationId, { recent: 60 }).then(setEvents, () => {});
    }
  }, [organizationId]);

  useEffect(() => {
    version.current = ""; setOrganization(null); setTrail([]); setStale(null);
    let cancelled = false;
    const stop = startPolling(async () => {
      try { if (!cancelled) await refresh(); } catch (failure) { if (!cancelled) setStale(failure.message || String(failure)); }
    }, { interval: 3000, immediate: true });
    return () => { cancelled = true; stop(); };
  }, [refresh]);

  const act = useCallback(async (action, params = {}, message) => {
    setBusy(true); setError("");
    try {
      const data = await organizationRequest("", action, organizationId, params);
      if (data?.needsUserConsent) toast("Waiting for approval. Nothing changed yet.");
      else if (message) toast(typeof message === "function" ? message(data) : message);
      await refresh(true);
      window.dispatchEvent(new CustomEvent("gofer:organizations-changed"));
      return data || true;
    } catch (failure) {
      setError(failure.message); toast(failure.message);
      return null;
    } finally { setBusy(false); }
  }, [organizationId, refresh, toast]);

  const open = useCallback((ref, fresh = false) => {
    setTrail(current => {
      if (fresh || !current.length) return [ref];
      const index = current.indexOf(ref);
      return index >= 0 ? current.slice(0, index + 1) : [...current, ref].slice(-6);
    });
  }, []);
  const go = useCallback((next, options = {}) => {
    setTab(next);
    setTrail([]);
    if (options.project) setProjectId(options.project);
    if (options.mine) setFilters({ ...BOARD, mine: true });
    view.current?.scrollTo(0, 0);
  }, [setFilters]);

  useEffect(() => {
    const onKey = event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPalette(true); }
      if (event.key !== "Escape" || palette || !trail.length || event.defaultPrevented) return;
      if (document.querySelector(".oh-confirm")) return;
      // The first Escape leaves a field, the next one returns to the list.
      const active = document.activeElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(active?.tagName) && active.closest(".oh-detail")) { active.blur(); return; }
      setTrail([]);
      setTimeout(() => view.current?.focus(), 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [palette, trail.length]);

  // Arrow keys, Home and End move between the five sections, as in any tab strip.
  const onTabKey = event => {
    const index = TABS.findIndex(([id]) => id === tab);
    const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key];
    if (next == null) return;
    event.preventDefault();
    const id = TABS[(next + TABS.length) % TABS.length][0];
    go(id);
    setTimeout(() => document.getElementById(`oh-tab-${id}`)?.focus(), 0);
  };

  const ctx = {
    model, busy, organizationId, onAct: act, defaults,
    // After a change made outside an organization action, such as landing from the patch.
    refresh: () => { void refresh(true).catch(() => {}); window.dispatchEvent(new CustomEvent("gofer:organizations-changed")); },
    onOpen: ref => open(ref),
    close: () => setTrail([]),
    replace: ref => setTrail(current => [...current.slice(0, -1), ref]),
    board: values => { setFilters(current => ({ ...BOARD, ...current, fWorker: "all", mine: false, ...values })); setTrail([]); go("issues"); },
    goProject: id => { setProjectId(id); setTrail([]); go("projects"); },
    locate: id => { setKb(current => ({ ...current, view: "graph", project: "all", kinds: new Set(Object.keys(KINDS)) })); setKbFocus(id); go("knowledge"); },
  };

  async function move(ticket, column) {
    const rule = moveRule(ticket, column);
    if (!rule.ok) { if (rule.reason) toast(rule.reason); return; }
    if (rule.review) {
      const caution = reviewCaution(ticket.claims);
      if (caution.risky) { toast(`${ticket.key}: ${caution.text}. Review it here first.`); open(`ticket:${ticket.id}`, true); return; }
      await act("review", { taskId: ticket.id, expectedRevision: ticket.revision, decision: "accept", body: "Accepted from the board" }, `${ticket.key} accepted. Its epic and milestones now count it.`);
      return;
    }
    if (rule.reopen) {
      // One call, straight into the chosen column: a ticket reopened for Backlog never sits in To do where a worker could start it.
      await act("reopen", { taskId: ticket.id, expectedRevision: ticket.revision, status: column }, `${ticket.key} reopened in ${column === "backlog" ? "Backlog" : "To do"}`);
      return;
    }
    if (rule.pull) {
      const name = model.workers[ticket.assignee]?.name || "its worker";
      const hours = hoursStatus(model);
      const when = !ticket.assignee ? "Assign a worker so it can run." : model.runtime.state !== "running" ? "It starts when you start the organization." : hours && !hours.open ? `${hours.text}; it starts first when they begin.` : `${name} starts it as soon as they're free.`;
      const result = await act("task_update", { taskId: ticket.id, expectedRevision: ticket.revision, changes: { status: "in_progress" } }, null);
      if (result) toast(`${ticket.key} is next for ${name}. ${when}`, { label: "Undo", run: () => act("task_update", { taskId: ticket.id, expectedRevision: result.revision, changes: { status: ticket.status } }, `${ticket.key} moved back`) });
      return;
    }
    const from = ticket.status;
    const result = await act("task_update", { taskId: ticket.id, expectedRevision: ticket.revision, changes: { status: rule.status } }, null);
    const when = rule.status !== "todo" ? "It won't run from Backlog." : !ticket.assignee ? "Assign a worker so it can run." : model.runtime.state !== "running" ? "It starts when you start the organization." : `${model.workers[ticket.assignee]?.name || "Its worker"} picks it up next.`;
    if (result) toast(`${ticket.key} moved to ${rule.status === "todo" ? "To do" : "Backlog"}. ${when}`, { label: "Undo", run: () => act("task_update", { taskId: ticket.id, expectedRevision: result.revision, changes: { status: from } }, `${ticket.key} moved back`) });
  }

  async function quickAdd(title) {
    const epicId = filters.fEpic !== "all" && filters.fEpic !== "none" ? filters.fEpic : null;
    const project = !epicId && filters.fProject !== "all" ? filters.fProject : null;
    const result = await act("task_create", { title, status: "backlog", epicId, project, assignee: model.epics[epicId]?.owner || (filters.fWorker !== "all" ? filters.fWorker : null) });
    if (result?.id) toast(`${result.key} added to Backlog${epicId ? ` under ${model.epics[epicId].title}` : ""}`, { label: "Open", run: () => open(`ticket:${result.id}`, true) });
    return Boolean(result?.id);
  }

  const retry = () => { void refresh(true).catch(failure => setStale(failure.message || String(failure))); };
  if (!organization) return <FirstLoad failure={stale} onRetry={retry} onClose={onClose} />;
  const { config, runtime } = organization;
  const running = runtime.state === "running";
  const archived = runtime.archived === true;
  const waiting = needsYou(model);
  const counts = { org: waiting.length, issues: waiting.filter(item => item.ref).length };
  const tabLabel = TABS.find(([id]) => id === tab)[1];
  const detail = trail.length > 0;
  const hours = hoursStatus(model);
  const focusBlock = organization.diagnostics?.schedule?.current;

  return <section className="oh" aria-label={`${config.name} organization`}>
    <div className="oh-main">
      <header className="oh-top">
        <button className="oh-btn ghost icon" type="button" aria-label="Close organization" title="Close organization" onClick={onClose}><ArrowLeft size={16} /></button>
        <span className="oh-mark" aria-hidden="true">{initialsOf(config.name)}</span>
        <h1>{config.name}</h1>
        <AutoBadge organization={organization} />
        <span className={`oh-pill oh-state st-${archived ? "archived" : running ? "running" : "paused"}`}><i className="oh-dot" aria-hidden="true" />{archived ? "Archived" : running ? "Running" : "Paused"}</span>
        {hours && !archived && <button className={`oh-pill oh-hours-pill ${hours.open ? "open" : "closed"}`} type="button" title="Working hours. Click to change them." onClick={() => open("new:hours", true)}><Clock size={13} />{hours.text}</button>}
        {focusBlock && !archived && <button className="oh-pill oh-block-pill" type="button" title={`${focusBlock.label}: ${focusBlock.focus || "the current schedule block"}. Click to change the schedule.`} onClick={() => open("new:schedule", true)}>{focusBlock.label}</button>}
        <span className="grow" />
        <button className="oh-search" type="button" onClick={() => setPalette(true)} aria-label="Find anything (Ctrl K)"><Search size={15} /><span className="lbl">Find a ticket, note, worker…</span><kbd>Ctrl K</kbd></button>
        {!archived && (running
          ? <button className="oh-btn" type="button" disabled={busy} onClick={() => act("control", { state: "paused" }, "Paused. Running tickets return to To do.")}><Pause size={14} />Pause</button>
          : <button className="oh-btn primary" type="button" disabled={busy || starting} onClick={() => setStarting(true)}><Play size={14} />Start</button>)}
        <button className="oh-btn ghost icon" type="button" aria-label="Settings, history and operations" title="Settings, history and operations" onClick={openSettings}><Settings size={16} /></button>
      </header>
      <nav className="oh-tabs" role="tablist" aria-label="Organization sections" onKeyDown={onTabKey}>{TABS.map(([id, label, Icon]) => <button key={id} id={`oh-tab-${id}`} role="tab" type="button" aria-selected={tab === id} aria-controls="oh-panel" tabIndex={tab === id ? 0 : -1} title={label} onClick={() => go(id)}><Icon size={15} aria-hidden="true" /><span className="lbl">{label}</span>{counts[id] ? <span className="oh-count" title={`${counts[id]} need you`}>{counts[id]}<span className="oh-sr"> need you</span></span> : null}</button>)}</nav>
      <section id="oh-panel" role="tabpanel" aria-labelledby={`oh-tab-${tab}`} className={`oh-view ${!detail && ["issues", "workers"].includes(tab) ? "fill" : ""}`} ref={view} tabIndex={-1}>
        {stale && <div className="oh-banner stale" role="status"><AlertTriangle size={14} aria-hidden="true" /><span><b>Couldn&apos;t refresh.</b> {loadedAt ? `This is how things looked at ${clock(loadedAt)}. ` : ""}Raticode keeps trying. <small>{stale}</small></span><button className="oh-btn small" type="button" onClick={retry}><RefreshCw size={13} />Try again</button></div>}
        {error && <div className="oh-banner bad" role="alert"><AlertTriangle size={14} aria-hidden="true" /><span>{error}</span><button className="oh-icon-btn" type="button" aria-label="Dismiss error" onClick={() => setError("")}><X size={13} /></button></div>}
        {!running && runtime.pauseReason && !archived && runtime.pauseReason !== "Paused by you" && <div className="oh-banner" role="status"><Pause size={14} /><span>{runtime.pauseReason}</span></div>}
        {starting && <div className="oh-start"><StartSheet organization={organization} busy={busy} onCancel={() => setStarting(false)} onStart={async () => { if (await act("control", { state: "running" }, "Started. Workers pick up To do tickets.")) setStarting(false); }} /></div>}
        {detail ? <OrganizationDetail model={model} trail={trail} setTrail={setTrail} ctx={ctx} listLabel={tabLabel} /> : <>
          {tab === "org" && <OverviewView model={model} events={events} busy={busy} onOpen={ref => open(ref, true)} onAct={act} go={go} onStart={() => { setStarting(true); view.current?.scrollTo(0, 0); }} />}
          {tab === "projects" && <ProjectsView model={model} projectId={projectId} onProject={setProjectId} onOpen={ctx.onOpen} onBoard={ctx.board} onAct={act} busy={busy} />}
          {tab === "issues" && <IssuesView model={model} filters={filters} setFilters={setFilters} busy={busy} onOpen={ref => open(ref, true)} onMove={move} onQuickAdd={quickAdd} />}
          {tab === "workers" && <WorkersView model={model} onOpen={ref => open(ref, true)} />}
          {tab === "knowledge" && <KnowledgeView model={model} state={kb} setState={setKb} selected={kbFocus} onOpen={ref => { setKbFocus(ref.startsWith("kb:") ? ref.slice(3) : null); open(ref, true); }} onAct={act} busy={busy} />}
        </>}
      </section>
    </div>
    {palette && <Palette model={model} onClose={() => setPalette(false)} onPick={ref => { setPalette(false); open(ref, true); }} />}
    <div className="oh-toasts" aria-live="polite">{toasts.map(item => <div className="oh-toast" key={item.id}><span>{item.text}</span>{item.action && <button type="button" onClick={() => { item.action.run(); setToasts(current => current.filter(t => t.id !== item.id)); }}>{item.action.label}</button>}</div>)}</div>
  </section>;
}
