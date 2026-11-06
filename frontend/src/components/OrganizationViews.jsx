import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Check, Clock, HeartPulse, Flag, Folder, FolderPlus, Maximize2, Minus, Pencil, Plus, ShieldCheck, SquareKanban, X } from "lucide-react";
import {
  COLUMNS, COLUMN_LABELS, activityFeed, claimSummary, describeHours, entity, epicProgress, epicState, epicsOfMilestone, formatDate,
  initialsOf, milestoneProgress, milestonesOfProject, milestonesServing, moveRule, needsYou, openTickets,
  orgMilestoneProgress, projectProgress, startHint, zoneTime, ticketsOfEpic, ago, timeAgo, workerStatus,
} from "../lib/organizationModel.js";
import { monthUsage, organizationRequest, plural } from "../lib/organizations.js";

const pct = value => Math.round(value * 100);

// Click and drag a scroll area to move around it. Starting on an element that matches `skip`
// leaves that element alone; a drag that moved suppresses the click it would end with.
export function useDragScroll(ref, skip = "input, select, textarea") {
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    let drag = null;
    const down = event => {
      if (event.button !== 0 || event.target.closest(skip)) return;
      drag = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop, moved: false, id: event.pointerId };
    };
    const move = event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 5) return;
      if (!drag.moved) { drag.moved = true; element.classList.add("panning"); try { element.setPointerCapture(drag.id); } catch { /* pointer already gone */ } }
      element.scrollLeft = drag.left - dx; element.scrollTop = drag.top - dy;
    };
    const up = () => {
      if (drag?.moved) {
        element.classList.remove("panning");
        const stop = event => { event.stopPropagation(); event.preventDefault(); };
        element.addEventListener("click", stop, { capture: true, once: true });
        setTimeout(() => element.removeEventListener("click", stop, { capture: true }), 0);
      }
      drag = null;
    };
    element.addEventListener("pointerdown", down);
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerup", up);
    element.addEventListener("pointercancel", up);
    return () => { element.removeEventListener("pointerdown", down); element.removeEventListener("pointermove", move); element.removeEventListener("pointerup", up); element.removeEventListener("pointercancel", up); };
  }, [ref, skip]);
}

const ZOOM_MIN = 0.4, ZOOM_MAX = 2;
const clampZoom = value => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 100) / 100));

// Ctrl and the mouse wheel, or Ctrl with + - 0, zoom around the pointer or the middle.
export function useZoom(ref) {
  const [scale, setScale] = useState(1);
  const anchor = useRef(null);
  const zoomAt = (next, clientX, clientY) => {
    const element = ref.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const x = clientX == null ? rect.width / 2 : clientX - rect.left, y = clientY == null ? rect.height / 2 : clientY - rect.top;
    setScale(current => {
      const target = clampZoom(typeof next === "function" ? next(current) : next);
      anchor.current = { x, y, cx: (element.scrollLeft + x) / current, cy: (element.scrollTop + y) / current, scale: target };
      return target;
    });
  };
  useLayoutEffect(() => {
    const element = ref.current, point = anchor.current;
    if (!element || !point || point.scale !== scale) return;
    element.scrollLeft = point.cx * scale - point.x; element.scrollTop = point.cy * scale - point.y;
    anchor.current = null;
  }, [scale, ref]);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const wheel = event => { if (!event.ctrlKey && !event.metaKey) return; event.preventDefault(); zoomAt(current => current * (event.deltaY < 0 ? 1.12 : 1 / 1.12), event.clientX, event.clientY); };
    const key = event => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (["=", "+"].includes(event.key)) { event.preventDefault(); zoomAt(current => current * 1.2); }
      else if (["-", "_"].includes(event.key)) { event.preventDefault(); zoomAt(current => current / 1.2); }
      else if (event.key === "0") { event.preventDefault(); zoomAt(1); }
    };
    element.addEventListener("wheel", wheel, { passive: false });
    window.addEventListener("keydown", key);
    return () => { element.removeEventListener("wheel", wheel); window.removeEventListener("keydown", key); };
    // zoomAt only reads the ref and the state setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref]);
  return { scale, zoomAt };
}

export function Bar({ value, tone = "" }) {
  return <div className={`oh-bar ${tone}`} role="img" aria-label={`${pct(value)} percent`}><i style={{ width: `${pct(value)}%` }} /></div>;
}

export function Pill({ state, children }) {
  return <span className={`oh-pill st-${state}`}><i className="oh-dot" />{children || COLUMN_LABELS[state] || state}</span>;
}

export function Avatar({ model, id, size }) {
  const worker = model.workers[id];
  if (!worker) {
    const other = { you: ["You", "var(--accent)"], Raticode: ["R", "var(--run)"], Rem: ["Re", "var(--k-decision)"] }[id];
    return other ? <span className={`oh-av ${size || ""} other`} style={{ background: other[1] }} title={id === "you" ? "You" : id}>{other[0]}</span>
      : <span className="oh-av" style={{ background: "var(--faint)" }} title="Unassigned">?</span>;
  }
  return <span className={`oh-av ${size || ""}`} style={{ background: worker.color }} title={worker.name}>{initialsOf(worker.name)}</span>;
}

export function Chip({ model, refId, onOpen, extra = null }) {
  const item = entity(model, refId);
  if (!item) return null;
  return <button className="oh-chip" type="button" title={`${item.sub}: ${item.label}`} onClick={event => { event.stopPropagation(); onOpen(refId); }}>
    {extra}<i className="k" style={{ background: item.color }} /><span>{item.type === "ticket" && <span className="oh-key">{item.key} </span>}{item.label}</span>
  </button>;
}

export function EvidenceBadges({ claims }) {
  const summary = claimSummary(claims);
  return <>
    {summary.ok > 0 && <span className="oh-ev ok" title={`${summary.ok} verified`}><Check size={12} />{summary.ok}</span>}
    {summary.warn > 0 && <span className="oh-ev warn" title={`${summary.warn} source changed`}><AlertTriangle size={12} />{summary.warn}</span>}
    {summary.bad > 0 && <span className="oh-ev bad" title={`${summary.bad} failed`}><X size={12} />{summary.bad}</span>}
    {summary.pending > 0 && <span className="oh-ev pending" title={`${summary.pending} being checked`}>…{summary.pending}</span>}
    {summary.none > 0 && <span className="oh-ev none" title={`${summary.none} without verified evidence`}>?{summary.none}</span>}
  </>;
}

/* ------------------------------------------------------------- Organization */

function Timeline({ model, onOpen }) {
  const milestones = Object.values(model.orgMilestones).sort((a, b) => (a.dueAt || "9").localeCompare(b.dueAt || "9"));
  const dated = milestones.filter(m => m.dueAt);
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const last = dated.length ? new Date(`${dated.at(-1).dueAt}T12:00:00`) : new Date(today.getFullYear(), today.getMonth() + 4, 1);
  const end = new Date(Math.max(last.getTime(), start.getTime()) + 0);
  end.setMonth(end.getMonth() + 1, 1);
  const span = Math.max(end - start, 1);
  const at = date => Math.max(0, Math.min(100, ((date - start) / span) * 100));
  const months = [];
  for (let month = new Date(start); month < end && months.length < 14; month.setMonth(month.getMonth() + 1)) months.push(new Date(month));
  return <section className="oh-panel oh-timeline" aria-labelledby="oh-ms">
    <div className="oh-section-head"><h2 id="oh-ms">Milestones</h2><span className="oh-muted">Progress counts accepted tickets only</span><span className="grow" /><button className="oh-btn small ghost" type="button" onClick={() => onOpen("new:orgms")}><Plus size={14} />Milestone</button></div>
    {milestones.length ? <>
      <div className="oh-track" aria-hidden="true"><div className="line" />
        {months.map(month => <span key={month.toISOString()} className="month" style={{ left: `${at(month)}%` }}>{month.toLocaleDateString(undefined, { month: "short" })}</span>)}
        <div className="today" style={{ left: `${at(today)}%` }}><span>Today</span></div>
        {dated.map(m => <div key={m.id} className="pin" style={{ left: `${at(new Date(`${m.dueAt}T12:00:00`))}%` }}><span>{m.short || m.title}</span><i /></div>)}
      </div>
      <div className="oh-ms-rows">{milestones.map(m => {
        const progress = orgMilestoneProgress(model, m.id);
        return <button key={m.id} className="oh-ms-row" type="button" onClick={() => onOpen(`orgms:${m.id}`)}>
          <b>{m.title}</b><span className="oh-muted">{m.dueAt ? formatDate(m.dueAt) : "No date"}</span>
          <span className="oh-inline"><Bar value={progress} /><span className="oh-muted oh-num">{pct(progress)}%</span></span>
          <span className="oh-chips">{milestonesServing(model, m.id).map(pm => <span className="oh-chip static" key={pm.id}><i className="k" style={{ background: model.projects[pm.projectId]?.color }} /><span>{pm.title}</span></span>)}</span>
        </button>;
      })}</div>
    </> : <p className="oh-empty">Add the outcomes the whole organization is working toward. Project milestones then serve them.</p>}
  </section>;
}

function NeedsYou({ model, busy, onOpen, onAct, onBoard }) {
  const [all, setAll] = useState(false);
  const everything = needsYou(model);
  const items = all ? everything : everything.slice(0, 6);
  return <section className="oh-panel oh-needs" aria-labelledby="oh-needs">
    <div className="oh-section-head"><h2 id="oh-needs">Needs you</h2><span className="oh-muted">{everything.length}</span></div>
    {items.length ? items.map(item => {
      if (item.type === "request") return <div className="oh-need" key={item.request.id}><span className="oh-icon-badge st-review"><ShieldCheck size={16} /></span>
        <span className="what"><b>Rem asks to: {item.request.summary}</b><small>Asked {ago(item.request.at)}. Nothing changes until you approve. Requests expire after a day.</small></span>
        <span className="acts"><button className="oh-btn small primary" type="button" disabled={busy} onClick={() => onAct("request_decide", { requestId: item.request.id, decision: "approve" }, "Approved Rem's request")}>Approve</button><button className="oh-btn small" type="button" disabled={busy} onClick={() => onAct("request_decide", { requestId: item.request.id, decision: "decline" }, "Declined. Rem will see it was declined.")}>Decline</button></span></div>;
      if (item.type === "heartbeat") {
        const worker = model.workers[item.worker];
        return <div className="oh-need" key={`heartbeat:${item.worker}`}><span className="oh-icon-badge st-blocked"><HeartPulse size={15} /></span>
          <span className="what"><b>{worker.name}&apos;s check-ins stopped</b><small>The last three failed. {item.stall.reason}</small></span>
          <span className="acts">{model.tickets[item.stall.taskId] && <button className="oh-btn small" type="button" onClick={() => onOpen(`ticket:${item.stall.taskId}`)}>Open</button>}
            <button className="oh-btn small primary" type="button" disabled={busy} onClick={() => onAct("heartbeat_resume", { employeeId: item.worker }, `${worker.name}'s check-ins resume now.`)}>Resume check-ins</button></span></div>;
      }
      const ticket = model.tickets[item.ref.slice(7)];
      return <div className="oh-need" key={item.ref}><span className={`oh-icon-badge ${item.type === "flag" ? "st-blocked" : "st-review"}`}>{item.type === "flag" ? <Flag size={15} /> : <Check size={15} />}</span>
        <span className="what"><b><span className="oh-key oh-muted">{ticket.key}</span> {ticket.title}</b><small>{item.type === "flag" ? ticket.flag : <>Ready for review <EvidenceBadges claims={ticket.claims} /></>}</small></span>
        <span className="acts"><button className="oh-btn small" type="button" onClick={() => onOpen(item.ref)}>{item.type === "flag" ? "Open" : "Review"}</button></span></div>;
    }) : <p className="oh-empty">Nothing waiting on you.</p>}
    {everything.length > items.length && <div className="oh-row"><button className="oh-btn small ghost" type="button" onClick={() => setAll(true)}>Show all {everything.length}</button><button className="oh-btn small ghost" type="button" onClick={onBoard}>Open them on the board</button></div>}
  </section>;
}

function Feed({ model, events, onOpen, onBoard }) {
  const items = activityFeed(model, events);
  const name = who => model.workers[who]?.name.split(" ")[0] || (who === "you" ? "You" : who);
  return <section className="oh-panel oh-feed" aria-labelledby="oh-feed">
    <div className="oh-section-head"><h2 id="oh-feed">Recent activity</h2><span className="grow" /><button className="oh-btn small ghost" type="button" onClick={onBoard}>Open board</button></div>
    {items.length ? <ul>{items.map(item => <li key={item.key}><Avatar model={model} id={item.who} />
      <span><b>{name(item.who)}</b> {item.text} {item.ref && <Chip model={model} refId={item.ref} onOpen={onOpen} />}{item.bad && <span className="oh-ev bad"><X size={12} />check failed</span>}</span>
      <time dateTime={item.at}>{timeAgo(item.at)}</time></li>)}</ul> : <p className="oh-empty">Runs, reviews and new notes will show up here.</p>}
  </section>;
}

export function OverviewView({ model, events, busy, onOpen, onAct, go }) {
  const { config, runtime } = model;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(config.mission || "");
  const workers = Object.values(model.workers);
  const usage = monthUsage(runtime);
  async function saveMission() {
    const ok = await onAct("plan_save", { kind: "mission", item: { mission: draft }, expectedRevision: model.organization.revision }, "Mission saved as a new configuration revision");
    if (ok) setEditing(false);
  }
  return <>
    <div className="oh-panel oh-mission">
      <div>{editing ? <>
        <label className="oh-sr" htmlFor="oh-mission">Mission</label>
        <textarea id="oh-mission" rows={2} maxLength={500} value={draft} autoFocus onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setEditing(false); if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) saveMission(); }} />
        <div className="oh-row"><button className="oh-btn primary small" type="button" disabled={busy} onClick={saveMission}>Save mission</button><button className="oh-btn small" type="button" onClick={() => setEditing(false)}>Cancel</button></div>
      </> : <p className={`statement ${config.mission ? "" : "placeholder"}`}>{config.mission || "What is this organization for? Write it in one line."}</p>}</div>
      {!editing && <button className="oh-btn small ghost" type="button" onClick={() => { setDraft(config.mission || ""); setEditing(true); }}><Pencil size={14} />{config.mission ? "Edit" : "Write mission"}</button>}
      <div className="facts">
        <span><b>{Object.keys(model.projects).length}</b>projects</span>
        <span><b>{openTickets(model).length}</b>open tickets</span>
        <span><b>{workers.filter(w => !w.paused).length}/{workers.length}</b>workers active</span>
        <span><b>{usage.runs}</b>runs this month</span>
        {usage.tokens != null && <span><b>{usage.tokens >= 1000 ? `${Math.round(usage.tokens / 1000)}k` : usage.tokens}</b>tokens</span>}
        <span><b>{Object.keys(model.knowledge).length}</b>notes</span>
      </div>
    </div>
    <Timeline model={model} onOpen={onOpen} />
    <div className="oh-grid-2">
      <NeedsYou model={model} busy={busy} onOpen={onOpen} onAct={onAct} onBoard={() => go("issues", { mine: true })} />
      <Feed model={model} events={events} onOpen={onOpen} onBoard={() => go("issues")} />
    </div>
    <div className="oh-proj-strip">
      {Object.values(model.projects).map(project => <button key={project.id} className="oh-panel oh-proj-card" style={{ "--pc": project.color }} type="button" onClick={() => go("projects", { project: project.id })}>
        <b>{project.name}</b><Bar value={projectProgress(model, project.id)} />
        <span className="meta"><span>{plural(milestonesOfProject(model, project.id).length, "milestone")}</span><span>{openTickets(model, project.id).length} open</span>{project.lead && <span>Lead {model.workers[project.lead]?.name.split(" ")[0]}</span>}</span>
      </button>)}
      <button className="oh-panel oh-proj-card oh-new-card" type="button" onClick={() => onOpen("new:project")}><Plus size={16} /><b>New project</b><span className="meta"><span>Group milestones, epics and tickets</span></span></button>
    </div>
    <WorkingHours model={model} onOpen={onOpen} />
    <SharedRepository model={model} busy={busy} onAct={onAct} />
  </>;
}

// When workers may work, in words, with what that means right now.
export function WorkingHours({ model, onOpen }) {
  const hours = model.config.operatingHours;
  const state = model.organization.diagnostics?.hours;
  const scheduled = hours?.mode === "scheduled";
  const now = !state || !scheduled ? "Workers may work at any time while the organization runs."
    : state.open ? `Open now${state.closesAt ? ` until ${zoneTime(state.closesAt, state.timezone)}` : ""}. ${hours.atClose === "finish" ? "Work that is running at closing time finishes." : "At closing time, running work stops and returns to To do; it runs first when hours open."}`
      : `Closed now${state.opensAt ? `, opens ${zoneTime(state.opensAt, state.timezone)}` : ""}. Nothing starts, and check-ins wait, until then.`;
  return <section className="oh-panel oh-hours" aria-labelledby="oh-hours-h">
    <div className="oh-section-head"><h2 id="oh-hours-h">Working hours</h2><span className="oh-muted">{describeHours(hours)}</span><span className="grow" />
      <button className="oh-btn small ghost" type="button" onClick={() => onOpen("new:hours")}><Clock size={14} />Change</button></div>
    <p className="oh-muted">{now}</p>
    <small className="oh-faint">{model.config.resumeAfterRestart ? "Keeps running after Raticode restarts." : "Pauses when Raticode restarts; you start it again."}</small>
  </section>;
}

// The organization's shared Git repository: off, or where it is and what changed lately.
export function SharedRepository({ model, busy, onAct }) {
  const [state, setState] = useState(null);
  const [copied, setCopied] = useState(false);
  const oid = model.organization.id, version = model.organization.version;
  useEffect(() => {
    let cancelled = false;
    organizationRequest("", "repository", oid).then(result => { if (!cancelled) setState(result); }, () => {});
    return () => { cancelled = true; };
  }, [oid, version]);
  const enabled = Boolean(model.config.sharedRepository);
  const turn = on => onAct("plan_save", { kind: "repository", item: { enabled: on }, expectedRevision: model.organization.revision },
    on ? "Shared repository is on. Runs get their own branch of it." : "Shared repository is off. Its folder and history stay on disk.");
  return <section className="oh-panel oh-repo" aria-labelledby="oh-repo-h">
    <div className="oh-section-head"><h2 id="oh-repo-h">Shared repository</h2><span className="oh-muted">{enabled ? (state?.exists ? plural(state.count, "commit") : "Starting…") : "Off"}</span><span className="grow" />
      {enabled ? <button className="oh-btn small ghost" type="button" disabled={busy} onClick={() => turn(false)}>Turn off</button> : <button className="oh-btn small primary" type="button" disabled={busy} onClick={() => turn(true)}>Turn on</button>}</div>
    {!enabled ? <p className="oh-muted" style={{ margin: 0 }}>One Git repository the whole organization shares, for knowledge, decisions and files every project uses. Each run changes it on its ticket&apos;s own branch, and accepting the ticket merges the changes.</p> : state?.exists ? <>
      <div className="path"><Folder size={14} /><code>{state.path}</code><button className="oh-btn small ghost" type="button" onClick={async () => { try { await navigator.clipboard.writeText(state.path); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ } }}>{copied ? "Copied" : "Copy path"}</button></div>
      <div className="oh-chips">{state.files.map(name => <span className="oh-chip static" key={name}><span>{name}</span></span>)}</div>
      {state.openBranches.length > 0 && <small className="oh-faint">{plural(state.openBranches.length, "ticket branch", "ticket branches")} with shared changes not merged yet</small>}
      <ul aria-label="Recent changes">{state.commits.slice(0, 5).map(([sha, at, author, subject]) => <li key={sha} title={`${sha.slice(0, 10)} by ${author}`}><span>{subject}</span><time dateTime={at}>{timeAgo(at)}</time></li>)}</ul>
    </> : <p className="oh-muted" style={{ margin: 0 }}>Setting up the repository…</p>}
  </section>;
}

/* ------------------------------------------------------------------ Projects */

const folderName = value => value.split(/[\\/]/).filter(Boolean).at(-1) || value;

// The folders the organization owns. Workers can only work inside these and their worktrees.
export function FoldersPanel({ model, onAct, busy }) {
  const roots = model.config.projectRoots || [];
  const [typing, setTyping] = useState(false);
  const [path, setPath] = useState("");
  const [removing, setRemoving] = useState(null);
  const save = (next, reason, message) => onAct("configure", { config: { ...model.config, projectRoots: next }, expectedRevision: model.organization.revision, reason }, message);
  async function add(value) {
    const folder = value.trim();
    if (!folder || roots.includes(folder)) return;
    if (await save([...roots, folder], `Added folder ${folder}`, `Added ${folderName(folder)}. Start the organization again when you're ready.`)) { setPath(""); setTyping(false); }
  }
  async function choose() {
    const picker = window.goferDesktop?.workspace?.selectPath;
    if (!picker) { setTyping(true); return; }
    const picked = await picker({ directoryOnly: true, currentPath: roots[0] || "" });
    const value = typeof picked === "string" ? picked : picked?.path;
    if (value) await add(value);
  }
  return <section className="oh-panel oh-folders" aria-labelledby="oh-folders-h">
    <h3 id="oh-folders-h">Folders<span className="oh-muted">{roots.length}</span></h3>
    {roots.map((root, index) => <div className="oh-folder" key={root} title={root}><Folder size={15} /><span><b>{folderName(root)}</b><small><bdi>{root}</bdi></small></span>
      {removing === root ? <span className="oh-row" style={{ marginTop: 0 }}><button className="oh-btn small danger" type="button" disabled={busy} onClick={async () => { if (await save(roots.filter(item => item !== root), `Removed folder ${root}`, `Removed ${folderName(root)}. Its files are untouched.`)) setRemoving(null); }}>Remove</button><button className="oh-btn small" type="button" onClick={() => setRemoving(null)}>Keep</button></span>
        : <button className="oh-icon-btn" type="button" aria-label={`Remove ${folderName(root)}`} title={index === 0 ? "Default folder. Remove it from the organization" : "Remove from the organization"} onClick={() => setRemoving(root)}><X size={12} /></button>}
    </div>)}
    {!roots.length && <p className="oh-muted" style={{ margin: 0 }}>Add a folder before workers can start. Each folder belongs to one organization.</p>}
    {typing ? <form className="oh-folder-add" onSubmit={event => { event.preventDefault(); add(path); }}>
      <label className="oh-sr" htmlFor="oh-folder-path">Folder path</label><input id="oh-folder-path" autoFocus placeholder="/home/you/Projects/app" value={path} onChange={event => setPath(event.target.value)} />
      <span className="oh-row" style={{ marginTop: 0 }}><button className="oh-btn small primary" type="submit" disabled={busy || !path.trim()}>Add folder</button><button className="oh-btn small" type="button" onClick={() => { setTyping(false); setPath(""); }}>Cancel</button></span>
    </form> : <span className="oh-row" style={{ marginTop: 0 }}><button className="oh-btn small" type="button" disabled={busy} onClick={choose}><FolderPlus size={14} />Add folder</button><button className="oh-btn small ghost" type="button" onClick={() => setTyping(true)}>Type a path</button></span>}
    {removing && <small className="oh-faint">Removing a folder pauses the organization and keeps its files.</small>}
  </section>;
}

export function ProjectsView({ model, projectId, onProject, onOpen, onBoard, onAct, busy }) {
  const projects = Object.values(model.projects);
  const project = model.projects[projectId] || projects[0];
  if (!project) return <div className="oh-projects"><nav className="oh-plist" aria-label="Projects"><FoldersPanel model={model} onAct={onAct} busy={busy} /></nav><div className="oh-panel oh-blank"><Folder size={26} /><h2>Start with a project</h2><p>A project holds milestones, each with a few epics. Tickets on the board belong to epics.</p><button className="oh-btn primary" type="button" onClick={() => onOpen("new:project")}><Plus size={14} />New project</button></div></div>;
  const tickets = Object.values(model.tickets).filter(ticket => ticket.project === project.id);
  const people = [...new Set(tickets.map(ticket => ticket.assignee).filter(Boolean))];
  const folder = project.workspacePath || model.config.projectRoots?.[0] || "";
  const unsorted = tickets.filter(ticket => !ticket.epicId);
  return <div className="oh-projects">
    <nav className="oh-plist" aria-label="Projects">
      {projects.map(item => <button key={item.id} className="p" type="button" style={{ "--pc": item.color }} aria-current={item.id === project.id ? "true" : undefined} onClick={() => onProject(item.id)}>
        <b>{item.name}</b><Bar value={projectProgress(model, item.id)} /><small>{plural(milestonesOfProject(model, item.id).length, "milestone")}, {plural(openTickets(model, item.id).length, "open ticket")}</small>
      </button>)}
      <button className="oh-btn" type="button" onClick={() => onOpen("new:project")}><Plus size={14} />New project</button>
      <FoldersPanel model={model} onAct={onAct} busy={busy} />
    </nav>
    <div>
      <div className="oh-panel oh-phead"><span className="oh-mark" style={{ background: project.color }}>{initialsOf(project.name)}</span>
        <div className="oh-phead-text"><h2>{project.name}</h2><div className="meta">{folder && <span><Folder size={13} /> <code>{folder}</code></span>}{project.lead && <span>Lead {model.workers[project.lead]?.name}</span>}{project.checks.length > 0 && <span>{plural(project.checks.length, "check")}{project.autoRunChecks ? ", rerun after every run" : ", run from tickets"}</span>}</div></div>
        <span className="oh-chips">{people.map(id => <Avatar key={id} model={model} id={id} />)}</span>
        <button className="oh-btn" type="button" onClick={() => onOpen(`project:${project.id}`)}><Pencil size={14} />Details</button>
        <button className="oh-btn" type="button" onClick={() => onBoard({ fProject: project.id })}><SquareKanban size={14} />Open on board</button>
      </div>
      {milestonesOfProject(model, project.id).map(milestone => {
        const progress = milestoneProgress(model, milestone.id);
        return <section className="oh-panel oh-milestone" key={milestone.id}>
          <header><h3><Flag size={15} /><button type="button" onClick={() => onOpen(`ms:${milestone.id}`)}>{milestone.title}</button></h3>
            <span className="sub">{milestone.dueAt ? `Due ${formatDate(milestone.dueAt)}` : "No date"}</span>
            <span className="sub">{milestone.parentId && model.orgMilestones[milestone.parentId] ? <>Serves <Chip model={model} refId={`orgms:${milestone.parentId}`} onOpen={onOpen} /></> : "Serves no organization milestone"}</span>
            <span className="sub end"><Bar value={progress} /><span className="oh-num">{pct(progress)}%</span></span>
          </header>
          {epicsOfMilestone(model, milestone.id).map(epic => <EpicRow key={epic.id} model={model} epic={epic} onOpen={onOpen} />)}
          <button className="oh-add-row" type="button" onClick={() => onOpen(`new:epic:${milestone.id}`)}><Plus size={14} />Add epic</button>
        </section>;
      })}
      {Object.values(model.epics).filter(epic => epic.projectId === project.id && !epic.milestoneId).length > 0 && <section className="oh-panel oh-milestone">
        <header><h3><Flag size={15} />Not scheduled</h3></header>
        {Object.values(model.epics).filter(epic => epic.projectId === project.id && !epic.milestoneId).map(epic => <EpicRow key={epic.id} model={model} epic={epic} onOpen={onOpen} />)}
      </section>}
      <div className="oh-row"><button className="oh-btn" type="button" onClick={() => onOpen(`new:ms:${project.id}`)}><Plus size={14} />Add milestone</button>
        {unsorted.length > 0 && <button className="oh-btn ghost" type="button" onClick={() => onBoard({ fProject: project.id, fEpic: "none" })}>{plural(unsorted.length, "ticket")} without an epic</button>}</div>
    </div>
  </div>;
}

function EpicRow({ model, epic, onOpen }) {
  const tickets = ticketsOfEpic(model, epic.id);
  const progress = epicProgress(model, epic.id);
  return <button className="oh-epic-row" type="button" onClick={() => onOpen(`epic:${epic.id}`)}><b>{epic.title}</b>
    <span className="oh-inline"><Bar value={progress} tone={progress === 1 ? "done" : ""} /><span className="oh-muted oh-num">{tickets.filter(t => t.column === "done").length}/{tickets.length}</span></span>
    <span className="oh-mini-states" aria-label="Ticket states">{tickets.map(ticket => <i key={ticket.id} className={`st-${ticket.column}`} title={`${ticket.key}: ${COLUMN_LABELS[ticket.column]}`} />)}</span>
    <Avatar model={model} id={epic.owner} />
  </button>;
}

/* -------------------------------------------------------------------- Issues */

export function filterTickets(model, filters) {
  return Object.values(model.tickets).filter(ticket => (filters.fProject === "all" || ticket.project === filters.fProject)
    && (filters.fWorker === "all" || ticket.assignee === filters.fWorker)
    && (filters.fEpic === "all" || (filters.fEpic === "none" ? !ticket.epicId : ticket.epicId === filters.fEpic))
    && (!filters.mine || ticket.column === "review" || (ticket.flag && ticket.column !== "done")));
}

function Card({ model, ticket, onOpen, onDrag }) {
  const epic = model.epics[ticket.epicId];
  const project = model.projects[ticket.project];
  const running = ticket.column === "running";
  return <article className={`oh-card ${running ? "is-running" : ""} ${ticket.column === "review" ? "is-review" : ""} ${ticket.flag && ticket.column !== "done" ? "flagged" : ""}`}
    draggable={!running} tabIndex={0} aria-label={`${ticket.key} ${ticket.title}`} data-ticket={ticket.id}
    onClick={() => onOpen(`ticket:${ticket.id}`)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(`ticket:${ticket.id}`); } }}
    onDragStart={event => { event.dataTransfer.setData("text/plain", ticket.id); event.dataTransfer.effectAllowed = "move"; onDrag(true); }} onDragEnd={() => onDrag(false)}>
    <div className="row1"><span className="oh-key">{ticket.key}</span><span className="grow" />{(epic || project) && <span title={epic?.title || project?.name}><i className="oh-dot" style={{ color: project?.color || "var(--faint)" }} /> {epic?.title || project?.name}</span>}</div>
    <div className="title">{ticket.title}</div>
    {ticket.flag && ticket.column !== "done" && <div className="flag"><Flag size={12} />{ticket.flag}</div>}
    {ticket.column === "todo" && !ticket.flag && (hint => hint && hint.tone !== "run" && <div className={`oh-wait ${hint.tone}`} title={hint.text}><Clock size={12} /><span>{hint.text}</span></div>)(startHint(model, ticket))}
    {running && <div className="livebar" aria-hidden="true"><i /></div>}
    <div className="row3"><Avatar model={model} id={ticket.assignee} /><span className="grow" />
      {running ? <span className="oh-ev run"><i className="oh-dot pulse" />Working</span> : <EvidenceBadges claims={ticket.claims} />}
      {ticket.used.length > 0 && <span title={`${ticket.used.length} linked notes`}><BookOpen size={12} /> {ticket.used.length}</span>}
    </div>
  </article>;
}

export function IssuesView({ model, filters, setFilters, busy, onOpen, onMove, onQuickAdd }) {
  const wrap = useRef(null);
  useDragScroll(wrap, ".oh-card, .oh-epic-card, button, a, input, select, textarea, label");
  const [over, setOver] = useState(null);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [dragging, setDragging] = useState(false);
  const tickets = filterTickets(model, filters);
  const epicsInScope = Object.values(model.epics).filter(epic => filters.fProject === "all" || epic.projectId === filters.fProject);
  const set = values => setFilters(current => ({ ...current, ...values }));
  const drop = column => event => {
    event.preventDefault(); setOver(null); setDragging(false);
    const ticket = model.tickets[event.dataTransfer.getData("text/plain")];
    if (ticket) onMove(ticket, column);
  };
  const columnBody = (column, list, lane) => <div className={`col-body ${over === `${lane || ""}${column}` ? "over" : ""}`} data-col={column}
    onDragOver={event => { event.preventDefault(); setOver(`${lane || ""}${column}`); }} onDragLeave={() => setOver(null)} onDrop={drop(column)}>
    {list.map(ticket => <Card key={ticket.id} model={model} ticket={ticket} onOpen={onOpen} onDrag={setDragging} />)}
    {column === "backlog" && !lane && (adding
      ? <input className="oh-add-input" autoFocus placeholder="Ticket title, then Enter" aria-label="New ticket title" value={title} disabled={busy} onChange={event => setTitle(event.target.value)}
          onKeyDown={async event => { if (event.key === "Escape") { setAdding(false); setTitle(""); } if (event.key === "Enter" && title.trim()) { if (await onQuickAdd(title.trim())) { setTitle(""); setAdding(false); } } }} onBlur={() => { if (!title.trim()) setAdding(false); }} />
      : <button className="oh-add-card" type="button" onClick={() => setAdding(true)}><Plus size={14} />Add ticket</button>)}
  </div>;
  const toolbar = <div className="oh-toolbar">
    <div className="oh-seg" role="group" aria-label="Board type"><button type="button" aria-pressed={filters.board === "tickets"} onClick={() => set({ board: "tickets" })}>Tickets</button><button type="button" aria-pressed={filters.board === "epics"} onClick={() => set({ board: "epics" })}>Epics</button></div>
    <label className="oh-sr" htmlFor="oh-f-project">Project</label>
    <select id="oh-f-project" value={filters.fProject} onChange={event => set({ fProject: event.target.value, fEpic: "all" })}><option value="all">All projects</option>{Object.values(model.projects).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
    {filters.board === "tickets" && <>
      <label className="oh-sr" htmlFor="oh-f-worker">Worker</label>
      <select id="oh-f-worker" value={filters.fWorker} onChange={event => set({ fWorker: event.target.value })}><option value="all">Everyone</option>{Object.values(model.workers).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
      <label className="oh-sr" htmlFor="oh-f-epic">Epic</label>
      <select id="oh-f-epic" value={filters.fEpic} onChange={event => set({ fEpic: event.target.value })}><option value="all">All epics</option><option value="none">No epic</option>{epicsInScope.map(e => <option key={e.id} value={e.id}>{e.title}</option>)}</select>
      <label className="oh-toggle"><input type="checkbox" checked={filters.mine} onChange={event => set({ mine: event.target.checked })} />Needs me</label>
      <label className="oh-toggle"><input type="checkbox" checked={filters.group} onChange={event => set({ group: event.target.checked })} />Lanes by epic</label>
    </>}
    <span className="grow" /><span className="oh-muted">{filters.board === "tickets" ? plural(tickets.length, "ticket") : ""}</span>
  </div>;
  if (filters.board === "epics") {
    const columns = [["planned", "Planned"], ["active", "In progress"], ["done", "Done"]];
    return <>{toolbar}<div className="oh-board-wrap" ref={wrap} data-board="epics"><div className="oh-board epics">{columns.map(([state, label]) => {
      const epics = epicsInScope.filter(epic => epicState(model, epic.id) === state);
      return <section className="col" key={state}><header>{label}<span className="n">{epics.length}</span></header><div className="col-body">
        {epics.map(epic => { const progress = epicProgress(model, epic.id); const milestone = model.milestones[epic.milestoneId]; return <button key={epic.id} className="oh-epic-card" type="button" style={{ "--pc": model.projects[epic.projectId]?.color }} onClick={() => onOpen(`epic:${epic.id}`)}>
          <b>{epic.title}</b><span className="row"><span>{model.projects[epic.projectId]?.name}</span>{milestone && <span>{milestone.title}</span>}</span><Bar value={progress} tone={progress === 1 ? "done" : ""} />
          <span className="row"><span className="oh-mini-states">{ticketsOfEpic(model, epic.id).map(ticket => <i key={ticket.id} className={`st-${ticket.column}`} />)}</span><span className="grow" /><Avatar model={model} id={epic.owner} /></span>
        </button>; })}
        {!epics.length && <p className="oh-empty">None</p>}
      </div></section>;
    })}</div></div></>;
  }
  const header = column => <header><Pill state={column} /><span className="n">{tickets.filter(t => t.column === column).length}</span></header>;
  if (!filters.group) return <>{toolbar}<div className={`oh-board-wrap ${dragging ? "dragging" : ""}`} ref={wrap} data-board="tickets"><div className="oh-board">{COLUMNS.map(column => <section className="col" key={column} aria-label={COLUMN_LABELS[column]}>{header(column)}{columnBody(column, tickets.filter(t => t.column === column))}</section>)}</div></div>
    {!Object.keys(model.tickets).length && <p className="oh-hint-line">Add a ticket to Backlog, then move it to To do when it should run. Workers pick up To do tickets while the organization runs.</p>}</>;
  const lanes = [...new Set(tickets.map(t => t.epicId || ""))];
  return <>{toolbar}<div className="oh-board-wrap" ref={wrap} data-board="lanes"><div className="oh-board">
    {COLUMNS.map(column => <div className="col lane-head" key={column}>{header(column)}</div>)}
    {lanes.map(epicId => <LaneRow key={epicId || "none"} model={model} epicId={epicId} tickets={tickets} columnBody={columnBody} onOpen={onOpen} />)}
  </div></div></>;
}

function LaneRow({ model, epicId, tickets, columnBody, onOpen }) {
  const epic = model.epics[epicId];
  return <>
    <div className="oh-lane-label">{epic ? <button type="button" onClick={() => onOpen(`epic:${epicId}`)}>{epic.title}</button> : <span>No epic</span>}{epic && <Bar value={epicProgress(model, epicId)} />}<span className="oh-muted">{model.projects[epic?.projectId]?.name}</span></div>
    {COLUMNS.map(column => <section className="col lane" key={column} aria-label={`${epic?.title || "No epic"}: ${COLUMN_LABELS[column]}`}>{columnBody(column, tickets.filter(t => (t.epicId || "") === epicId && t.column === column), epicId || "none")}</section>)}
  </>;
}

export { moveRule };

/* ------------------------------------------------------------------- Workers */

export function WorkersView({ model, onOpen }) {
  const workers = Object.values(model.workers);
  const chart = useRef(null);
  useDragScroll(chart);
  const { scale, zoomAt } = useZoom(chart);
  const node = worker => {
    const [state, label] = workerStatus(model, worker.id);
    const doing = Object.values(model.tickets).find(t => t.assignee === worker.id && t.column === "running") || Object.values(model.tickets).find(t => t.assignee === worker.id && t.column === "review");
    const reports = workers.filter(w => w.reportsTo === worker.id);
    return <li key={worker.id}><button className="oh-node" type="button" onClick={() => onOpen(`worker:${worker.id}`)}>
      <span className="who"><Avatar model={model} id={worker.id} /><span><b>{worker.name}</b><small>{worker.title || worker.role || "Worker"}</small></span></span>
      <span className="doing"><Pill state={state}>{label}</Pill>{doing ? <span className="oh-key">{doing.key}</span> : <span>{plural(worker.responsibilities.length, "responsibility", "responsibilities")}</span>}</span>
    </button>{reports.length > 0 && <ul>{reports.map(node)}</ul>}</li>;
  };
  const roots = workers.filter(worker => !worker.reportsTo || !model.workers[worker.reportsTo]);
  return <>
    <div className="oh-toolbar"><div className="oh-legend"><Pill state="working">Working</Pill><Pill state="needs">To review</Pill><Pill state="idle">Idle</Pill><Pill state="paused">Held</Pill></div><span className="grow" /><span className="oh-muted oh-hint-small">Drag to move around, Ctrl and scroll to zoom</span>
      <span className="oh-zoom" role="group" aria-label="Zoom"><button type="button" aria-label="Zoom out" title="Zoom out (Ctrl -)" disabled={scale <= ZOOM_MIN} onClick={() => zoomAt(current => current / 1.2)}><Minus size={14} /></button><output aria-live="polite">{Math.round(scale * 100)}%</output><button type="button" aria-label="Zoom in" title="Zoom in (Ctrl +)" disabled={scale >= ZOOM_MAX} onClick={() => zoomAt(current => current * 1.2)}><Plus size={14} /></button><button type="button" aria-label="Actual size" title="Actual size (Ctrl 0)" onClick={() => zoomAt(1)}><Maximize2 size={13} /></button></span>
      <button className="oh-btn" type="button" onClick={() => onOpen("new:worker")}><Plus size={14} />Add worker</button></div>
    <div className="oh-panel oh-chart-wrap" ref={chart} data-zoom={scale}><div className="oh-chart-canvas" style={{ zoom: scale }}><ul className="oh-tree"><li><div className="oh-node owner"><span className="who"><span className="oh-av big" style={{ background: "var(--accent)", color: "var(--on-accent)" }}>You</span><span><b>You</b><small>Owner, with Rem coordinating</small></span></span></div>
      {roots.length > 0 && <ul>{roots.map(node)}</ul>}</li></ul></div>
      {!workers.length && <p className="oh-empty">Add the first worker. Give them a persona and a few responsibilities.</p>}
    </div>
  </>;
}
