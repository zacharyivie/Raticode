import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Building2, Clock, Folder, Network, Pause, Play, Search, Settings, SquareKanban, Users, X } from "lucide-react";
import { KINDS, buildModel, hoursStatus, initialsOf, moveRule, needsYou, reviewCaution, searchEntities } from "../lib/organizationModel.js";
import { organizationRequest } from "../lib/organizations.js";
import { startPolling } from "../lib/refresh.js";
import OrganizationWorkspace, { StartSheet } from "./OrganizationWorkspace.jsx";
import OrganizationDetail from "./OrganizationSheet.jsx";
import { KnowledgeView } from "./OrganizationKnowledge.jsx";
import { IssuesView, OverviewView, ProjectsView, WorkersView } from "./OrganizationViews.jsx";
import "./OrganizationHome.css";

const TABS = [["org", "Organization", Building2], ["projects", "Projects", Folder], ["issues", "Issues", SquareKanban], ["workers", "Workers", Users], ["knowledge", "Knowledge", Network]];
const POLL_ERROR = "Couldn't refresh: ";
const BOARD = { board: "tickets", group: false, fProject: "all", fWorker: "all", fEpic: "all", mine: false };
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

export default function OrganizationHome(props) {
  const { organizationId, onClose } = props;
  const [mode, setMode] = useState("home");
  if (organizationId === "new" || mode === "settings") {
    return <OrganizationWorkspace {...props} onClose={organizationId === "new" ? onClose : () => setMode("home")} />;
  }
  return <Home {...props} openSettings={() => setMode("settings")} />;
}

function Home({ organizationId, onClose, openSettings }) {
  const [organization, setOrganization] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("org");
  const [projectId, setProjectId] = useState(null);
  const [trail, setTrail] = useState([]);
  const [palette, setPalette] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [starting, setStarting] = useState(false);
  const [filters, setFilters] = useState(BOARD);
  const [kb, setKb] = useState(KNOWLEDGE);
  const [kbFocus, setKbFocus] = useState(null);
  const version = useRef("");
  const view = useRef(null);
  const model = useMemo(() => buildModel(organization), [organization]);

  const toast = useCallback((text, action) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(current => [...current.slice(-1), { id, text, action }]);
    setTimeout(() => setToasts(current => current.filter(item => item.id !== id)), action ? 6000 : 3500);
  }, []);

  const refresh = useCallback(async (force = false) => {
    const data = await organizationRequest("", "read", organizationId, force ? {} : { sinceVersion: version.current });
    if (!data.notModified) {
      version.current = data.version;
      setOrganization(data);
      organizationRequest("", "events", organizationId, { recent: 60 }).then(setEvents, () => {});
    }
  }, [organizationId]);

  useEffect(() => {
    version.current = ""; setOrganization(null); setTrail([]);
    let cancelled = false;
    const stop = startPolling(async () => {
      try { if (!cancelled) { await refresh(); setError(current => (current.startsWith(POLL_ERROR) ? "" : current)); } } catch (failure) { if (!cancelled) setError(`${POLL_ERROR}${failure.message}`); }
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
  }, []);

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

  const ctx = {
    model, busy, organizationId, onAct: act,
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

  if (!organization) return <section className="oh"><div className="oh-main"><p className="oh-loading" role="status">{error || "Loading organization…"}</p></div></section>;
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
        <span className={`oh-pill st-${archived ? "paused" : running ? "running" : "paused"}`}><i className="oh-dot" />{archived ? "Archived" : running ? "Running" : "Paused"}</span>
        {hours && !archived && <button className={`oh-pill oh-hours-pill ${hours.open ? "open" : "closed"}`} type="button" title="Working hours. Click to change them." onClick={() => open("new:hours", true)}><Clock size={13} />{hours.text}</button>}
        {focusBlock && !archived && <button className="oh-pill oh-block-pill" type="button" title={`${focusBlock.label}: ${focusBlock.focus || "the current schedule block"}. Click to change the schedule.`} onClick={() => open("new:schedule", true)}>{focusBlock.label}</button>}
        <span className="grow" />
        <button className="oh-search" type="button" onClick={() => setPalette(true)} aria-label="Find anything (Ctrl K)"><Search size={15} /><span className="lbl">Find a ticket, note, worker…</span><kbd>Ctrl K</kbd></button>
        {!archived && (running
          ? <button className="oh-btn" type="button" disabled={busy} onClick={() => act("control", { state: "paused" }, "Paused. Running tickets return to To do.")}><Pause size={14} />Pause</button>
          : <button className="oh-btn primary" type="button" disabled={busy || starting} onClick={() => setStarting(true)}><Play size={14} />Start</button>)}
        <button className="oh-btn ghost icon" type="button" aria-label="Settings, history and operations" title="Settings, history and operations" onClick={openSettings}><Settings size={16} /></button>
      </header>
      <nav className="oh-tabs" role="tablist" aria-label="Organization sections">{TABS.map(([id, label, Icon]) => <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => go(id)}><Icon size={15} />{label}{counts[id] ? <span className="oh-count" title={`${counts[id]} need you`}>{counts[id]}</span> : null}</button>)}</nav>
      <section className={`oh-view ${!detail && ["issues", "workers"].includes(tab) ? "fill" : ""}`} ref={view} tabIndex={-1}>
        {error && <div className="oh-banner bad" role="alert"><span>{error}</span><button className="oh-icon-btn" type="button" aria-label="Dismiss error" onClick={() => setError("")}><X size={13} /></button></div>}
        {!running && runtime.pauseReason && !archived && runtime.pauseReason !== "Paused by you" && <div className="oh-banner" role="status"><Pause size={14} /><span>{runtime.pauseReason}</span></div>}
        {starting && <div className="oh-start"><StartSheet organization={organization} busy={busy} onCancel={() => setStarting(false)} onStart={async () => { if (await act("control", { state: "running" }, "Started. Workers pick up To do tickets.")) setStarting(false); }} /></div>}
        {detail ? <OrganizationDetail model={model} trail={trail} setTrail={setTrail} ctx={ctx} listLabel={tabLabel} /> : <>
          {tab === "org" && <OverviewView model={model} events={events} busy={busy} onOpen={ctx.onOpen} onAct={act} go={go} />}
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
