import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Brain, Check, FlaskConical, Play, GitBranch, Network, Plus, SquareKanban, Trash2, X } from "lucide-react";
import {
  COLUMN_LABELS, EVIDENCE_LABELS, KINDS, describeHours, entity, epicProgress, epicsOfMilestone, evidenceState, formatDate, knowledgeEdges,
  milestoneProgress, milestonesOfProject, milestonesServing, openTickets, orgMilestoneProgress, reviewCaution, runSteps,
  startHint, ticketsOfEpic, zoneTime, ago, timeAgo, usedBy, workerStatus,
} from "../lib/organizationModel.js";
import { dangerousPermission, organizationRequest, plural, runProvenance, transcriptEntries } from "../lib/organizations.js";
import { providerPermissionDefault, providerPermissionOptions } from "../lib/providerPermissions.js";
import { ProviderModelEffortFields, useProviderCapabilities } from "./ProviderModelEffortFields.jsx";
import { Avatar, Bar, Chip, Pill } from "./OrganizationViews.jsx";

const pct = value => Math.round(value * 100);
const Rec = ({ label, children }) => <div className="oh-rec"><span className="lbl">{label}</span><div>{children}</div></div>;
const Muted = ({ children }) => <span className="oh-muted">{children}</span>;

function Confirm({ label, message, confirmLabel, onConfirm, busy, className = "oh-btn", danger = false }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button className={className} type="button" disabled={busy} onClick={() => setAsking(true)}>{label}</button>;
  return <span className="oh-confirm" role="group" aria-label={`Confirm: ${label}`}><span role="status">{message}</span>
    <button className={`oh-btn ${danger ? "danger" : "primary"}`} type="button" disabled={busy} onClick={async () => { setAsking(false); await onConfirm(); }}>{confirmLabel}</button>
    <button className="oh-btn" type="button" onClick={() => setAsking(false)}>Keep as is</button></span>;
}

function useLoad(load, deps) {
  const [state, setState] = useState({ data: null, error: "" });
  useEffect(() => {
    let cancelled = false;
    setState(current => ({ data: current.data, error: "" }));
    Promise.resolve(load()).then(data => { if (!cancelled) setState({ data, error: "" }); }, error => { if (!cancelled) setState({ data: null, error: error.message }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

/* ---------------------------------------------------------------- Evidence */

function EvidenceButton({ claim, onOpen }) {
  const state = evidenceState(claim);
  const kind = claim.evidence?.kind;
  const label = EVIDENCE_LABELS[state] || state;
  const tone = { ok: "ok", warn: "warn", bad: "bad", queued: "pending", running: "pending" }[state] || "none";
  const Icon = state === "ok" ? Check : state === "bad" ? X : AlertTriangle;
  if (!kind) return <span className="oh-evbtn none">No evidence</span>;
  return <button className={`oh-evbtn ${tone}`} type="button" onClick={() => onOpen(`claim:${claim.id}`)}><Icon size={11} />{label}: {kind}</button>;
}

function ClaimSheet({ model, claim, ctx }) {
  const evidence = claim.evidence || {};
  const ticket = model.tickets[claim.taskId];
  const state = evidenceState(claim);
  return <>
    <Rec label="Claim"><p>{claim.text}</p>{ticket && <Chip model={model} refId={`ticket:${ticket.id}`} onOpen={ctx.onOpen} />}</Rec>
    <Rec label="State"><Pill state={{ ok: "done", warn: "review", bad: "blocked" }[state] || "todo"}>{EVIDENCE_LABELS[state] || state}</Pill>{evidence.note && <> <Muted>{evidence.note}</Muted></>}</Rec>
    {evidence.kind === "check" && <>
      <Rec label="Command"><code>{evidence.command}</code>{evidence.checkName ? <small className="oh-faint"> Project check “{evidence.checkName}”</small> : <small className="oh-faint"> Not one of the project&apos;s checks, so Raticode didn&apos;t run it on its own.</small>}</Rec>
      {evidence.reported && <Rec label="Agent said"><Muted>{evidence.reported}</Muted></Rec>}
      {"exitCode" in evidence && <Rec label="Result"><span className={`oh-ev ${evidence.exitCode ? "bad" : "ok"}`}>Exit {evidence.exitCode}</span> <Muted>{evidence.ranBy === "you" ? "You ran it" : "Raticode reran it"} in the ticket&apos;s worktree, {ago(evidence.ranAt)}{evidence.seconds != null ? `, ${evidence.seconds}s` : ""}</Muted></Rec>}
      {evidence.output && <Rec label="Output"><pre className="oh-out">{evidence.output}</pre></Rec>}
      {state !== "running" && <Rec label="You"><Confirm busy={ctx.busy} label={<><FlaskConical size={14} />{"exitCode" in evidence ? "Run again" : "Run this check"}</>} confirmLabel="Run it"
        message={`Runs “${evidence.command}” in the ticket's worktree as you. The worker wrote this branch's code.`} onConfirm={() => ctx.onAct("check_run", { claimId: claim.id }, "Running the check. Its result appears here.")} /></Rec>}
    </>}
    {evidence.kind === "quote" && <>
      <Rec label="Source"><Chip model={model} refId={`kb:${evidence.entryId}`} onOpen={ctx.onOpen} /></Rec>
      <Rec label="Quoted"><p className={`oh-quote ${state === "warn" ? "old" : ""}`}>{evidence.quote}</p><small className="oh-faint">Version {evidence.version}, quoted {ago(claim.at)}</small></Rec>
      {state === "warn" && <Rec label="Now says"><p className="oh-quote new">{evidence.now}</p><small className="oh-faint">The note changed after the agent quoted it.</small></Rec>}
    </>}
    {evidence.kind === "commit" && <>
      <Rec label="Commit"><span className="oh-key">{evidence.sha?.slice(0, 12)}</span>{evidence.onBranch === false && <> <Muted>not on the ticket&apos;s branch</Muted></>}</Rec>
      {evidence.files?.length > 0 && <Rec label="Files"><Files files={evidence.files} /></Rec>}
    </>}
    {evidence.kind === "file" && <Rec label="File"><code>{evidence.path}</code><br /><small className="oh-faint">Fingerprint {evidence.sha256?.slice(0, 12)}{state === "warn" ? "; the file has changed since" : "; unchanged"}</small></Rec>}
  </>;
}

function Files({ files }) {
  return <ul className="oh-files">{files.slice(0, 40).map(([file, added, deleted]) => <li key={file}><span className="add">+{added}</span><span className="del">−{deleted}</span><span>{file}</span></li>)}{files.length > 40 && <li><Muted>{files.length - 40} more files</Muted></li>}</ul>;
}

/* ------------------------------------------------------------------- Ticket */

function TicketSheet({ model, ticket, ctx }) {
  const epic = model.epics[ticket.epicId];
  const milestone = epic && model.milestones[epic.milestoneId];
  const [comment, setComment] = useState("");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const changes = useLoad(() => (ticket.worktree?.branch || ticket.releasedWorktree?.branch || ticket.runs.length ? organizationRequest("", "ticket_changes", ctx.organizationId, { taskId: ticket.id }) : null), [ticket.id, ticket.revision]);
  const update = (changesToSave, message) => ctx.onAct("task_update", { taskId: ticket.id, expectedRevision: ticket.revision, changes: changesToSave }, message);
  const caution = reviewCaution(ticket.claims);
  const ready = ticket.claims.filter(claim => claim.evidence?.kind === "check" && claim.evidence.state === "queued");
  const review = decision => ctx.onAct("review", { taskId: ticket.id, expectedRevision: ticket.revision, decision, body: note.trim() || (decision === "accept" ? "Accepted" : "Please revise") },
    decision === "accept" ? `${ticket.key} accepted. Its epic and milestones now count it.` : `${ticket.key} sent back to To do with your note. Its claims were cleared for the next run.`);
  const branch = changes.data?.branch || ticket.worktree?.branch || ticket.releasedWorktree?.branch;
  const files = changes.data?.files || [];
  const startEdit = () => { setDraft({ title: ticket.title, description: ticket.description, epicId: ticket.epicId || "", assignee: ticket.assignee || "", doneWhen: ticket.doneWhen, priority: ticket.priority }); setEditing(true); };
  if (editing) {
    return <form className="oh-form" onSubmit={async event => { event.preventDefault(); const next = { ...draft, epicId: draft.epicId || null, assignee: draft.assignee || null }; const diff = Object.fromEntries(Object.entries(next).filter(([key, value]) => (ticket[key] ?? "") !== (value ?? ""))); if (!Object.keys(diff).length || await update(diff, `${ticket.key} saved`)) setEditing(false); }}>
      <label>Title<input value={draft.title} required maxLength={500} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} /></label>
      <label>Done when <small>One line in your words. The worker sees it.</small><textarea maxLength={1000} value={draft.doneWhen} onChange={e => setDraft(d => ({ ...d, doneWhen: e.target.value }))} /></label>
      <EpicSelect model={model} value={draft.epicId} onChange={epicId => setDraft(d => ({ ...d, epicId }))} />
      <WorkerSelect model={model} label="Assignee" value={draft.assignee} onChange={assignee => setDraft(d => ({ ...d, assignee }))} />
      <label>Priority<select value={draft.priority} onChange={e => setDraft(d => ({ ...d, priority: e.target.value }))}>{["critical", "high", "medium", "low"].map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select></label>
      <label>Details<textarea rows={5} value={draft.description} onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} /></label>
      {ticket.approvalRequired && ticket.approvedRevision != null && <p className="oh-warn-line"><AlertTriangle size={14} />Changing the plan clears its approval.</p>}
      <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>Save ticket</button><button className="oh-btn" type="button" onClick={() => setEditing(false)}>Cancel</button></div>
    </form>;
  }
  const linkable = Object.values(model.knowledge).filter(entry => !ticket.used.includes(entry.id));
  return <>
    {(hint => hint && <Rec label="Next"><p className={`oh-next ${hint.tone}`}>{hint.text}</p></Rec>)(startHint(model, ticket))}
    <Rec label="Why">{ticket.doneWhen ? <><p>{ticket.doneWhen}</p><small className="oh-faint">Done when, in your words</small></> : <Muted>No done-when yet. <button className="oh-link" type="button" onClick={startEdit}>Add one</button></Muted>}
      {ticket.description && <details className="oh-fold"><summary>Details</summary><p className="oh-prose">{ticket.description}</p></details>}</Rec>
    <Rec label="Part of"><div className="oh-chips">{ticket.project && <Chip model={model} refId={`project:${ticket.project}`} onOpen={ctx.onOpen} />}{milestone && <Chip model={model} refId={`ms:${milestone.id}`} onOpen={ctx.onOpen} />}{epic ? <Chip model={model} refId={`epic:${epic.id}`} onOpen={ctx.onOpen} /> : <Muted>No epic</Muted>}</div></Rec>
    <Rec label="Used">{ticket.used.length ? <div className="oh-link-list">{ticket.used.map(id => <div className="oh-link-row" key={id}><span className="oh-link-kind">{ticket.observed.has(id) ? "Read in a run" : "Linked"}</span><Chip model={model} refId={`kb:${id}`} onOpen={ctx.onOpen} />
      {!ticket.observed.has(id) && <button className="oh-icon-btn" type="button" aria-label="Unlink note" title="Unlink note" onClick={() => update({ knowledge: (ticket.knowledge || []).filter(k => k !== id) }, "Note unlinked")}><X size={12} /></button>}</div>)}</div> : <Muted>No notes linked.</Muted>}
      {linkable.length > 0 && <select className="oh-inline-select" aria-label="Link a note" value="" onChange={event => event.target.value && update({ knowledge: [...(ticket.knowledge || []), event.target.value] }, "Note linked. The next run sees it.")}><option value="">Link a note…</option>{linkable.map(entry => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select>}
    </Rec>
    <Rec label="Did">{branch ? <><div className="oh-chips"><span className="oh-chip static"><GitBranch size={13} /><span className="oh-key">{branch}</span></span></div>
      {files.length > 0 ? <details className="oh-fold"><summary>{plural(files.length, "file")}, <span className="oh-add">+{files.reduce((sum, f) => sum + f[1], 0)}</span> <span className="oh-del">−{files.reduce((sum, f) => sum + f[2], 0)}</span></summary><Files files={files} /></details> : <Muted>{changes.error ? `Couldn't read the branch: ${changes.error}` : changes.data ? "No committed changes yet" : "Reading changes…"}</Muted>}</>
      : !ticket.runs.length && <Muted>Nothing yet.</Muted>}
      {changes.data?.shared?.files?.length > 0 && <details className="oh-fold"><summary>Shared repository: {plural(changes.data.shared.files.length, "file")} on <span className="oh-key">{changes.data.shared.branch}</span></summary><Files files={changes.data.shared.files} /><small className="oh-faint">Merged into the shared repository when you accept this ticket.</small></details>}
      {ticket.runs.length > 0 && <div className="oh-chips oh-gap">{ticket.runs.slice(-4).map(id => <Chip key={id} model={model} refId={`run:${id}`} onOpen={ctx.onOpen} />)}</div>}
    </Rec>
    <Rec label="Claims">{ticket.claims.length ? <><ul className="oh-claims">{ticket.claims.map(claim => <li key={claim.id}><span className="c-text">{claim.text}</span><EvidenceButton claim={claim} onOpen={ctx.onOpen} /></li>)}</ul>
      {ready.length > 0 && ticket.column !== "running" && <div className="oh-row"><Confirm busy={ctx.busy} label={<><FlaskConical size={14} />Run {plural(ready.length, "check")}</>} confirmLabel="Run them"
        message={`Runs ${[...new Set(ready.map(c => `“${c.evidence.command}”`))].join(", ")} in the ticket's worktree as you. The worker wrote this branch's code.`}
        onConfirm={() => ctx.onAct("check_run", { taskId: ticket.id }, `Running ${plural(ready.length, "check")}. Results appear on each claim.`)} /></div>}</>
      : <Muted>{ticket.column === "running" ? "Claims appear as the run posts them." : ticket.runs.length ? "The last run posted no claims." : "No run yet."}</Muted>}</Rec>
    <Rec label="You">
      {ticket.column === "review" ? <>
        {caution.risky && <p className="oh-caution"><AlertTriangle size={15} /><span>{caution.text}. Check before accepting.</span></p>}
        <label className="oh-sr" htmlFor="oh-review-note">Review note</label>
        <textarea id="oh-review-note" className="oh-note-input" rows={2} placeholder="Note for the worker (optional)" value={note} onChange={e => setNote(e.target.value)} />
        <div className="oh-decide">{caution.risky ? <>
          <button className="oh-btn primary" type="button" disabled={ctx.busy} onClick={() => review("revise")}>Request changes</button>
          <Confirm busy={ctx.busy} label="Accept anyway" confirmLabel="Accept" message="Some evidence doesn't hold up. Accepting is recorded with your name." onConfirm={() => review("accept")} />
        </> : <>
          <button className="oh-btn primary" type="button" disabled={ctx.busy} onClick={() => review("accept")}><Check size={14} />Accept</button>
          <button className="oh-btn" type="button" disabled={ctx.busy} onClick={() => review("revise")}>Request changes</button>
        </>}</div>
      </> : <div className="oh-decide">
        {ticket.approvalRequired && ticket.approvedRevision == null && ticket.column !== "done" && <button className="oh-btn primary" type="button" disabled={ctx.busy} onClick={() => ctx.onAct("approve", { taskId: ticket.id, expectedRevision: ticket.revision }, "Plan approved")}>Approve plan</button>}
        {["backlog", "todo"].includes(ticket.status) && !ticket.pulledAt && <button className="oh-btn" type="button" disabled={ctx.busy} title="Moves it to In progress. Its worker starts it as soon as they're free and working hours allow."
          onClick={() => update({ status: "in_progress" }, `${ticket.key} is next for ${model.workers[ticket.assignee]?.name || "its worker"}`)}><Play size={14} />Start next</button>}
        {["done", "cancelled", "blocked"].includes(ticket.status) ? <button className="oh-btn" type="button" disabled={ctx.busy} onClick={() => ctx.onAct("reopen", { taskId: ticket.id, expectedRevision: ticket.revision }, `${ticket.key} reopened in To do`)}>Reopen</button>
          : ticket.column !== "running" && <><label className="oh-sr" htmlFor="oh-move">Move to</label><select id="oh-move" className="oh-inline-select" value={ticket.status} onChange={event => update({ status: event.target.value }, `${ticket.key} moved to ${COLUMN_LABELS[event.target.value === "todo" ? "todo" : "backlog"]}`)}><option value="backlog">Backlog</option><option value="todo">To do</option></select></>}
        {ticket.column === "running" && <Muted>Running now. Pause the organization to stop it.</Muted>}
        <button className="oh-btn ghost" type="button" onClick={startEdit}>Edit ticket</button>
        {!["running", "done"].includes(ticket.column) && ticket.status !== "cancelled" && <Confirm busy={ctx.busy} label="Cancel ticket" confirmLabel="Cancel it" danger message="Cancelled tickets leave the board; their records stay." onConfirm={() => update({ status: "cancelled" }, `${ticket.key} cancelled`)} />}
      </div>}
    </Rec>
    <details className="oh-fold oh-conversation"><summary>Conversation ({ticket.comments.length})</summary>
      {ticket.comments.slice(-12).map((item, index) => <div key={index} className="oh-comment"><b>{actorName(model, item.actor)}</b> <time>{timeAgo(item.at)}</time>{item.decision && <> <Pill state={item.decision === "accept" ? "done" : "review"}>{item.decision}</Pill></>}<p className="oh-prose">{item.body}</p></div>)}
      <form className="oh-row" onSubmit={async event => { event.preventDefault(); if (comment.trim() && await ctx.onAct("comment", { taskId: ticket.id, body: comment.trim() }, "Comment saved")) setComment(""); }}>
        <label className="oh-sr" htmlFor="oh-comment">Comment</label><input id="oh-comment" className="oh-note-input" placeholder="Comment for the worker" value={comment} onChange={e => setComment(e.target.value)} /><button className="oh-btn small" type="submit" disabled={ctx.busy || !comment.trim()}>Send</button>
      </form>
    </details>
  </>;
}

function actorName(model, actor = "") {
  if (actor === "user") return "You";
  if (actor === "system") return "Raticode";
  if (actor.startsWith("Rem")) return "Rem";
  if (actor.startsWith("employee:")) return model.workers[actor.slice(9)]?.name || actor.slice(9);
  return actor;
}

/* ---------------------------------------------------------------------- Run */

function RunSheet({ model, run, ctx }) {
  const live = !run.finishedAt;
  const [tick, setTick] = useState(0);
  // A running run's steps refresh while the sheet is open.
  useEffect(() => { if (!live) return undefined; const timer = setInterval(() => setTick(n => n + 1), 3000); return () => clearInterval(timer); }, [live]);
  const events = useLoad(() => organizationRequest("", "run_events", ctx.organizationId, { taskId: run.taskId, turnId: run.id }), [run.id, run.finishedAt, tick]);
  const [input, setInput] = useState(null);
  const steps = runSteps(events.data?.events || []);
  return <>
    <Rec label="Ticket"><Chip model={model} refId={`ticket:${run.taskId}`} onOpen={ctx.onOpen} /></Rec>
    <Rec label="Steps">{events.error ? <Muted>{events.error}</Muted> : !events.data ? <Muted>Reading the run…</Muted> : steps.length ? <ol className="oh-steps">{steps.map((step, index) => <li key={index} className={live && index === steps.length - 1 ? "now" : ""}><span className="sdot" /><span>{step.text}</span></li>)}</ol> : <Muted>No steps recorded.</Muted>}
      {live && <p className="oh-muted"><i className="oh-dot pulse" style={{ color: "var(--run)" }} /> Running now</p>}</Rec>
    {run.error && <Rec label="Error"><p className="oh-bad-text">{run.error}</p></Rec>}
    <Rec label="Input">{run.inputSha256 ? <><span className="oh-key">{run.inputSha256.slice(0, 12)}</span> <Muted>fingerprint of everything it was given</Muted>
      <details className="oh-fold" onToggle={async event => { if (event.currentTarget.open && !input) { try { setInput(await organizationRequest("", "run_input", ctx.organizationId, { taskId: run.taskId, turnId: run.id })); } catch (failure) { setInput({ error: failure.message }); } } }}><summary>Show input</summary>
        {input?.error ? <Muted>{input.error}</Muted> : input ? <>{input.matchesRecord ? <span className="oh-ev ok"><Check size={12} />Matches the fingerprint</span> : <span className="oh-ev bad"><X size={12} />Does not match the fingerprint</span>}<pre className="oh-out">{input.input?.instructions}</pre><pre className="oh-out">{input.input?.messages?.[0]?.body}</pre></> : <Muted>Loading…</Muted>}</details></> : <Muted>Not recorded for this run.</Muted>}</Rec>
    <Rec label="Receipt"><div className="oh-chips">{runProvenance(run).map(item => <span className="oh-chip static" key={item}><span>{item}</span></span>)}</div></Rec>
    <details className="oh-fold"><summary>Open transcript</summary>{events.data ? <pre className="oh-out">{transcriptEntries(events.data.events.filter(e => !e.deltaStreamId)).map(entry => `${entry.label}: ${entry.text}`).join("\n").slice(-20000)}</pre> : <Muted>Loading…</Muted>}</details>
  </>;
}

/* ---------------------------------------------------------- Plan item forms */

function EpicSelect({ model, value, onChange, projectId }) {
  return <label>Epic<select value={value || ""} onChange={e => onChange(e.target.value)}><option value="">No epic</option>
    {Object.values(model.projects).filter(p => !projectId || p.id === projectId).map(project => <optgroup key={project.id} label={project.name}>{Object.values(model.epics).filter(epic => epic.projectId === project.id).map(epic => <option key={epic.id} value={epic.id}>{epic.title}</option>)}</optgroup>)}
  </select></label>;
}

function WorkerSelect({ model, label, value, onChange, empty = "Unassigned", exclude }) {
  return <label>{label}<select value={value || ""} onChange={e => onChange(e.target.value)}><option value="">{empty}</option>{Object.values(model.workers).filter(w => w.id !== exclude).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>;
}

function PlanForm({ kind, model, item, ctx, onDone, fixed = {} }) {
  const [draft, setDraft] = useState(() => ({ ...item }));
  const set = values => setDraft(current => ({ ...current, ...values }));
  const isNew = !item.id;
  async function save(event) {
    event.preventDefault();
    const clean = Object.fromEntries(Object.entries({ ...draft, ...fixed }).map(([key, value]) => [key, value === "" ? null : value]));
    const result = await ctx.onAct("plan_save", { kind, item: clean, expectedRevision: model.organization.revision }, isNew ? `${{ milestone: "Milestone", epic: "Epic", project: "Project" }[kind]} added` : "Saved as a new configuration revision");
    if (result) onDone?.(result);
  }
  const roots = model.config.projectRoots || [];
  return <form className="oh-form" onSubmit={save}>
    {kind === "project" ? <label>Name<input required maxLength={200} value={draft.name || ""} onChange={e => set({ name: e.target.value })} /></label>
      : <label>Title<input required maxLength={200} value={draft.title || ""} onChange={e => set({ title: e.target.value })} /></label>}
    {kind === "milestone" && !fixed.projectId && !draft.projectId && <label>Short label <small>Shown on the timeline</small><input maxLength={40} value={draft.short || ""} onChange={e => set({ short: e.target.value })} /></label>}
    {kind === "milestone" && <label>Due<input type="date" value={draft.dueAt || ""} onChange={e => set({ dueAt: e.target.value })} /></label>}
    {kind === "milestone" && (fixed.projectId || draft.projectId) && <label>Serves<select value={draft.parentId || ""} onChange={e => set({ parentId: e.target.value })}><option value="">No organization milestone</option>{Object.values(model.orgMilestones).map(m => <option key={m.id} value={m.id}>{m.title}</option>)}</select></label>}
    {kind === "epic" && <label>Milestone<select required value={draft.milestoneId || ""} onChange={e => set({ milestoneId: e.target.value })}><option value="" disabled>Choose a milestone</option>
      {Object.values(model.projects).map(project => <optgroup key={project.id} label={project.name}>{milestonesOfProject(model, project.id).map(m => <option key={m.id} value={m.id}>{m.title}</option>)}</optgroup>)}</select></label>}
    {kind === "epic" && <WorkerSelect model={model} label="Owner" value={draft.owner} onChange={owner => set({ owner })} empty="No owner" />}
    {kind === "project" && <WorkerSelect model={model} label="Lead" value={draft.owner} onChange={owner => set({ owner })} empty="No lead" />}
    {kind === "project" && <label>Folder <small>One of the organization&apos;s folders. Add folders in the Folders list on the Projects tab.</small><select value={draft.workspacePath || ""} onChange={e => set({ workspacePath: e.target.value })}><option value="">Organization default</option>{roots.map(root => <option key={root} value={root}>{root}</option>)}</select></label>}
    {kind === "project" && <label>Color<input type="color" value={draft.color || "#215879"} onChange={e => set({ color: e.target.value })} /></label>}
    {(kind === "epic" || kind === "project") && <label>Description<textarea maxLength={4000} value={draft.description || ""} onChange={e => set({ description: e.target.value })} /></label>}
    {kind !== "project" && !isNew && <label>Status<select value={draft.status || "active"} onChange={e => set({ status: e.target.value })}><option value="active">Active</option><option value="done">Done</option><option value="cancelled">Cancelled</option></select></label>}
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>{isNew ? "Add" : "Save"}</button>{onDone && <button className="oh-btn" type="button" onClick={() => onDone(null)}>Cancel</button>}</div>
  </form>;
}

function DeletePlan({ kind, id, label, model, ctx }) {
  return <Confirm busy={ctx.busy} label={<><Trash2 size={14} />Delete</>} confirmLabel="Delete" danger message={`Delete ${label}? The configuration history keeps it.`}
    onConfirm={async () => { if (await ctx.onAct("plan_delete", { kind, id, expectedRevision: model.organization.revision }, `${label} deleted`)) ctx.close(); }} />;
}

function EpicSheet({ model, epic, ctx }) {
  const [editing, setEditing] = useState(false);
  const tickets = ticketsOfEpic(model, epic.id);
  const progress = epicProgress(model, epic.id);
  if (editing) return <PlanForm kind="epic" model={model} item={epic} ctx={ctx} onDone={() => setEditing(false)} />;
  return <>
    <Rec label="Part of"><div className="oh-chips"><Chip model={model} refId={`project:${epic.projectId}`} onOpen={ctx.onOpen} />{epic.milestoneId && <Chip model={model} refId={`ms:${epic.milestoneId}`} onOpen={ctx.onOpen} />}</div></Rec>
    {epic.description && <Rec label="About"><p className="oh-prose">{epic.description}</p></Rec>}
    <Rec label="Progress"><Bar value={progress} /><small className="oh-faint">{pct(progress)}% of tickets accepted</small></Rec>
    <Rec label="Tickets">{tickets.length ? <div className="oh-stack">{tickets.map(t => <div className="oh-link-row" key={t.id}><Pill state={t.column} /><Chip model={model} refId={`ticket:${t.id}`} onOpen={ctx.onOpen} /></div>)}</div> : <Muted>No tickets yet.</Muted>}</Rec>
    <Rec label="Knowledge"><div className="oh-chips">{[...new Set(tickets.flatMap(t => t.used))].map(id => <Chip key={id} model={model} refId={`kb:${id}`} onOpen={ctx.onOpen} />)}{!tickets.some(t => t.used.length) && <Muted>None yet</Muted>}</div></Rec>
    <div className="oh-decide oh-pad"><button className="oh-btn" type="button" onClick={() => ctx.onOpen(`new:ticket:${epic.id}`)}><Plus size={14} />Add ticket</button><button className="oh-btn" type="button" onClick={() => ctx.board({ fProject: epic.projectId, fEpic: epic.id, board: "tickets" })}><SquareKanban size={14} />Show on board</button><button className="oh-btn ghost" type="button" onClick={() => setEditing(true)}>Edit</button><DeletePlan kind="epic" id={epic.id} label={epic.title} model={model} ctx={ctx} /></div>
  </>;
}

function MilestoneSheet({ model, milestone, ctx }) {
  const [editing, setEditing] = useState(false);
  const progress = milestoneProgress(model, milestone.id);
  if (editing) return <PlanForm kind="milestone" model={model} item={milestone} ctx={ctx} onDone={() => setEditing(false)} />;
  return <>
    <Rec label="Project"><Chip model={model} refId={`project:${milestone.projectId}`} onOpen={ctx.onOpen} /></Rec>
    <Rec label="Serves">{milestone.parentId ? <Chip model={model} refId={`orgms:${milestone.parentId}`} onOpen={ctx.onOpen} /> : <Muted>No organization milestone</Muted>}</Rec>
    <Rec label="Progress"><Bar value={progress} /><small className="oh-faint">{pct(progress)}%</small></Rec>
    <Rec label="Epics">{epicsOfMilestone(model, milestone.id).map(epic => <div className="oh-split" key={epic.id}><Chip model={model} refId={`epic:${epic.id}`} onOpen={ctx.onOpen} /><Bar value={epicProgress(model, epic.id)} /></div>)}
      <button className="oh-btn small ghost" type="button" onClick={() => ctx.onOpen(`new:epic:${milestone.id}`)}><Plus size={13} />Add epic</button></Rec>
    <div className="oh-decide oh-pad"><button className="oh-btn ghost" type="button" onClick={() => setEditing(true)}>Edit</button><DeletePlan kind="milestone" id={milestone.id} label={milestone.title} model={model} ctx={ctx} /></div>
  </>;
}

function OrgMilestoneSheet({ model, milestone, ctx }) {
  const [editing, setEditing] = useState(false);
  const progress = orgMilestoneProgress(model, milestone.id);
  if (editing) return <PlanForm kind="milestone" model={model} item={milestone} ctx={ctx} onDone={() => setEditing(false)} />;
  return <>
    <Rec label="Progress"><Bar value={progress} /><small className="oh-faint">{pct(progress)}%, averaged from its project milestones</small></Rec>
    <Rec label="Delivered by">{milestonesServing(model, milestone.id).map(m => <div className="oh-split" key={m.id}><Chip model={model} refId={`ms:${m.id}`} onOpen={ctx.onOpen} /><Bar value={milestoneProgress(model, m.id)} /></div>)}
      {!milestonesServing(model, milestone.id).length && <Muted>No project milestone serves this yet. Pick it under Serves when you add one.</Muted>}</Rec>
    <div className="oh-decide oh-pad"><button className="oh-btn ghost" type="button" onClick={() => setEditing(true)}>Edit</button><DeletePlan kind="milestone" id={milestone.id} label={milestone.title} model={model} ctx={ctx} /></div>
  </>;
}

function ChecksEditor({ model, project, ctx }) {
  const [checks, setChecks] = useState(project.checks || []);
  const [auto, setAuto] = useState(project.autoRunChecks === true);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const dirty = JSON.stringify(checks) !== JSON.stringify(project.checks || []) || auto !== (project.autoRunChecks === true);
  return <div className="oh-stack">
    {checks.map((check, index) => <div className="oh-check-row" key={check.name}><b>{check.name}</b><code>{check.command}</code><button className="oh-icon-btn" type="button" aria-label={`Remove ${check.name}`} onClick={() => setChecks(checks.filter((_, i) => i !== index))}><X size={12} /></button></div>)}
    <div className="oh-row"><label className="oh-sr" htmlFor="oh-check-name">Check name</label><input id="oh-check-name" className="oh-note-input short" placeholder="Name" value={name} onChange={e => setName(e.target.value)} />
      <label className="oh-sr" htmlFor="oh-check-command">Command</label><input id="oh-check-command" className="oh-note-input" placeholder="Command, such as npm test" value={command} onChange={e => setCommand(e.target.value)} />
      <button className="oh-btn small" type="button" disabled={!name.trim() || !command.trim() || checks.some(c => c.name === name.trim())} onClick={() => { setChecks([...checks, { name: name.trim(), command: command.trim() }]); setName(""); setCommand(""); }}>Add</button></div>
    {checks.length > 0 && <label className="oh-toggle"><input type="checkbox" checked={auto} onChange={event => setAuto(event.target.checked)} />Run them after every run without asking</label>}
    <small className="oh-faint">A check runs in the ticket&apos;s worktree as you, so it runs code the worker wrote, outside the worker&apos;s sandbox. {auto ? "Turn this on only for projects whose workers you trust to run code." : "Until you turn on automatic runs, start them from the ticket."}</small>
    {dirty && <><p className="oh-warn-line"><AlertTriangle size={14} />If Rem proposes checks or automatic runs, you approve them first.</p>
      <div className="oh-row"><button className="oh-btn primary small" type="button" disabled={ctx.busy} onClick={() => ctx.onAct("plan_save", { kind: "project", item: { id: project.id, checks, autoRunChecks: auto && checks.length > 0 }, expectedRevision: model.organization.revision }, "Checks saved")}>Save checks</button><button className="oh-btn small" type="button" onClick={() => { setChecks(project.checks || []); setAuto(project.autoRunChecks === true); }}>Discard</button></div></>}
  </div>;
}

function ProjectSheet({ model, project, ctx }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <PlanForm kind="project" model={model} item={{ id: project.id, name: project.name, owner: project.owner, workspacePath: project.workspacePath, color: project.color, description: project.description }} ctx={ctx} onDone={() => setEditing(false)} />;
  return <>
    {project.description && <Rec label="About"><p className="oh-prose">{project.description}</p></Rec>}
    <Rec label="Lead">{project.lead ? <Chip model={model} refId={`worker:${project.lead}`} onOpen={ctx.onOpen} /> : <Muted>No lead</Muted>}</Rec>
    <Rec label="Milestones">{milestonesOfProject(model, project.id).map(m => <div className="oh-split" key={m.id}><Chip model={model} refId={`ms:${m.id}`} onOpen={ctx.onOpen} /><Bar value={milestoneProgress(model, m.id)} /></div>)}
      <button className="oh-btn small ghost" type="button" onClick={() => ctx.onOpen(`new:ms:${project.id}`)}><Plus size={13} />Add milestone</button></Rec>
    <Rec label="Open"><span>{plural(openTickets(model, project.id).length, "ticket")}</span></Rec>
    <Rec label="Notes"><div className="oh-chips">{Object.values(model.knowledge).filter(k => k.projectId === project.id).map(k => <Chip key={k.id} model={model} refId={`kb:${k.id}`} onOpen={ctx.onOpen} />)}{!Object.values(model.knowledge).some(k => k.projectId === project.id) && <Muted>None yet</Muted>}</div></Rec>
    <Rec label="Checks"><ChecksEditor key={`${JSON.stringify(project.checks)}${project.autoRunChecks}`} model={model} project={project} ctx={ctx} /></Rec>
    <div className="oh-decide oh-pad"><button className="oh-btn" type="button" onClick={() => ctx.goProject(project.id)}>Open project</button><button className="oh-btn ghost" type="button" onClick={() => setEditing(true)}>Edit</button><DeletePlan kind="project" id={project.id} label={project.name} model={model} ctx={ctx} /></div>
  </>;
}

/* ---------------------------------------------------------------- Knowledge */

function EntryForm({ model, entry, ctx, onDone, fixedProject }) {
  const [draft, setDraft] = useState(() => ({ kind: "note", title: "", topic: "", summary: "", body: "", projectId: fixedProject || "", links: [], ...(entry || {}) }));
  const set = values => setDraft(current => ({ ...current, ...values }));
  const others = Object.values(model.knowledge).filter(item => item.id !== entry?.id);
  const topics = [...new Set(Object.values(model.knowledge).map(item => item.topic).filter(Boolean))];
  async function save(event) {
    event.preventDefault();
    const fields = entry?.source ? ["kind", "topic", "projectId", "links", "summary"] : ["kind", "title", "topic", "summary", "body", "projectId", "links"];
    const payload = Object.fromEntries(fields.map(key => [key, key === "projectId" ? draft[key] || null : draft[key]]));
    const result = await ctx.onAct("knowledge_save", { entry: entry ? { id: entry.id, ...payload } : payload, ...(entry ? { expectedVersion: entry.version } : {}) }, entry ? "Note saved" : "Note added");
    if (result) onDone(result);
  }
  return <form className="oh-form" onSubmit={save}>
    <div className="oh-two"><label>Kind<select value={draft.kind} onChange={e => set({ kind: e.target.value })}>{Object.entries(KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Topic<input list="oh-topics" maxLength={60} value={draft.topic} onChange={e => set({ topic: e.target.value })} /><datalist id="oh-topics">{topics.map(topic => <option key={topic} value={topic} />)}</datalist></label></div>
    {!entry?.source && <label>Title<input required maxLength={200} value={draft.title} onChange={e => set({ title: e.target.value })} /></label>}
    <label>Summary <small>One sentence. Empty uses the first paragraph.</small><textarea maxLength={400} rows={2} value={draft.summary} onChange={e => set({ summary: e.target.value })} /></label>
    {!entry?.source && <label>Note<textarea rows={8} maxLength={100000} value={draft.body} onChange={e => set({ body: e.target.value })} /></label>}
    <label>Project<select value={draft.projectId || ""} onChange={e => set({ projectId: e.target.value })}><option value="">Whole organization</option>{Object.values(model.projects).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {others.length > 0 && <label>Related notes <small>Written links, shown in the graph</small><select multiple size={Math.min(6, others.length)} value={draft.links} onChange={e => set({ links: [...e.target.selectedOptions].map(option => option.value) })}>{others.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>{entry ? "Save note" : "Add note"}</button><button className="oh-btn" type="button" onClick={() => onDone(null)}>Cancel</button></div>
  </form>;
}

function LinkForm({ model, entry, ctx, onDone }) {
  const [target, setTarget] = useState("");
  const [quote, setQuote] = useState("");
  return <form className="oh-form" onSubmit={async event => { event.preventDefault(); if (await ctx.onAct("knowledge_link", { from: entry.id, to: target, quote }, "Linked. The quote is checked whenever either note changes.")) onDone(); }}>
    <label>Link to<select required value={target} onChange={e => setTarget(e.target.value)}><option value="" disabled>Choose a note</option>{Object.values(model.knowledge).filter(k => k.id !== entry.id).map(k => <option key={k.id} value={k.id}>{k.title}</option>)}</select></label>
    <label>Quote <small>Exact words from either note that explain the link</small><textarea required minLength={8} maxLength={1000} rows={2} value={quote} onChange={e => setQuote(e.target.value)} /></label>
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>Link notes</button><button className="oh-btn" type="button" onClick={onDone}>Cancel</button></div>
  </form>;
}

function KnowledgeSheet({ model, entry, ctx }) {
  const [mode, setMode] = useState("view");
  if (mode === "edit") return <EntryForm model={model} entry={entry} ctx={ctx} onDone={() => setMode("view")} />;
  if (mode === "link") return <LinkForm model={model} entry={entry} ctx={ctx} onDone={() => setMode("view")} />;
  const edges = knowledgeEdges(model).filter(([a, b]) => a === entry.id || b === entry.id);
  const quotedIn = Object.values(model.claimsByTask).flat().filter(claim => claim.evidence?.kind === "quote" && claim.evidence.entryId === entry.id);
  const author = entry.author || "";
  return <>
    <Rec label="Gist"><p>{entry.summary || <Muted>No summary</Muted>}</p></Rec>
    {entry.body && <details className="oh-fold"><summary>Read the note</summary><div className="oh-prose oh-note-body">{entry.body}</div></details>}
    <Rec label="Made by"><div className="oh-link-row">{author.startsWith("employee:") ? <Chip model={model} refId={`worker:${author.slice(9)}`} onOpen={ctx.onOpen} /> : <span>{actorName(model, author)}</span>}
      {entry.runId && model.runs[entry.runId] && <><Chip model={model} refId={`run:${entry.runId}`} onOpen={ctx.onOpen} /><span className="oh-link-kind">observed</span></>}</div>
      {entry.source && <small className="oh-faint"><Brain size={12} /> Second Brain: {entry.source.path}{entry.sourceMissing ? " (missing)" : ""}</small>}
      {entry.mirror && <small className="oh-faint">Also saved to the Second Brain: {entry.mirror.path}</small>}
      <small className="oh-faint">Version {entry.version}, updated {ago(entry.updatedAt)}{entry.changedAt ? "; changed in the Second Brain" : ""}</small></Rec>
    <Rec label="Project">{entry.projectId ? <Chip model={model} refId={`project:${entry.projectId}`} onOpen={ctx.onOpen} /> : <Muted>Whole organization</Muted>}</Rec>
    <Rec label="Used by"><div className="oh-chips">{usedBy(model, entry.id).map(t => <Chip key={t.id} model={model} refId={`ticket:${t.id}`} onOpen={ctx.onOpen} />)}{!usedBy(model, entry.id).length && <Muted>No tickets yet</Muted>}</div></Rec>
    <Rec label="Links">{edges.length ? edges.map(([a, b, kind, link]) => <div className="oh-link-row" key={`${a}${b}${kind}`}><span className="oh-link-kind">{{ written: "In the note", asserted: link?.state === "warn" ? "Quote changed" : "Quoted link", suggested: "Suggested" }[kind]}</span><Chip model={model} refId={`kb:${a === entry.id ? b : a}`} onOpen={ctx.onOpen} />
      {kind === "asserted" && <button className="oh-icon-btn" type="button" aria-label="Remove link" title={link.quote} onClick={() => ctx.onAct("knowledge_link", { linkId: link.id, remove: true }, "Link removed")}><X size={12} /></button>}</div>) : <Muted>None</Muted>}</Rec>
    {quotedIn.length > 0 && <Rec label="Quoted in"><div className="oh-chips">{quotedIn.map(claim => <Chip key={claim.id} model={model} refId={`claim:${claim.id}`} onOpen={ctx.onOpen} />)}</div></Rec>}
    <div className="oh-decide oh-pad">
      <button className="oh-btn" type="button" onClick={() => ctx.locate(entry.id)}><Network size={14} />Show in graph</button>
      <button className="oh-btn ghost" type="button" onClick={() => setMode("edit")}>Edit</button>
      {Object.keys(model.knowledge).length > 1 && <button className="oh-btn ghost" type="button" onClick={() => setMode("link")}>Link to a note</button>}
      <Confirm busy={ctx.busy} label="Archive" confirmLabel="Archive" message="Archived notes leave the graph; tickets keep their history." onConfirm={async () => { if (await ctx.onAct("knowledge_save", { entry: { id: entry.id, archived: true }, expectedVersion: entry.version }, "Note archived")) ctx.close(); }} />
    </div>
  </>;
}

function ImportForm({ model, ctx }) {
  const [path, setPath] = useState("");
  const [kind, setKind] = useState("note");
  const [projectId, setProjectId] = useState("");
  const root = model.config.remSecondBrain?.root;
  return <form className="oh-form" onSubmit={async event => { event.preventDefault(); const result = await ctx.onAct("knowledge_import", { path, kind, projectId: projectId || null }, "Added from the Second Brain"); if (result?.id) ctx.replace(`kb:${result.id}`); }}>
    <p className="oh-muted">Add a Markdown, text or HTML note from {root ? <code>{root}</code> : "the organization's Second Brain"}. Raticode keeps a copy and flags it when the original changes.</p>
    <label>Note path <small>Relative to the Second Brain folder</small><input required placeholder="Research/logging.md" value={path} onChange={e => setPath(e.target.value)} /></label>
    <div className="oh-two"><label>Kind<select value={kind} onChange={e => setKind(e.target.value)}>{Object.entries(KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Project<select value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">Whole organization</option>{Object.values(model.projects).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>Add note</button><button className="oh-btn" type="button" onClick={ctx.close}>Cancel</button></div>
  </form>;
}

/* ------------------------------------------------------------------ Workers */

const TICKET_ACTIONS = [["create", "Create"], ["edit", "Edit"], ["move", "Move"], ["cancel", "Cancel"]];

// Per-project ticket permissions. Off means the worker isn't limited beyond its other settings.
function TicketAccess({ model, value, onChange }) {
  const rows = [...Object.values(model.projects).map(project => [project.id, project.name]), ["*", "Outside a project"]];
  const limited = value !== null;
  const toggle = (key, action) => {
    const current = new Set(value[key] || []);
    if (current.has(action)) current.delete(action); else current.add(action);
    onChange({ ...value, [key]: TICKET_ACTIONS.map(([id]) => id).filter(id => current.has(id)) });
  };
  const all = Object.fromEntries(rows.map(([key]) => [key, TICKET_ACTIONS.map(([id]) => id)]));
  return <div className="oh-stack">
    <label className="oh-switch">Limit ticket actions by project<input type="checkbox" checked={limited} onChange={event => onChange(event.target.checked ? all : null)} /></label>
    {limited ? <>
      <table className="oh-access"><thead><tr><th scope="col">Project</th>{TICKET_ACTIONS.map(([id, label]) => <th scope="col" key={id}>{label}</th>)}</tr></thead>
        <tbody>{rows.map(([key, name]) => <tr key={key}><th scope="row">{name}</th>{TICKET_ACTIONS.map(([id, label]) => <td key={id}><input type="checkbox" aria-label={`${label} tickets in ${name}`} checked={(value[key] || []).includes(id)} onChange={() => toggle(key, id)} /></td>)}</tr>)}</tbody></table>
      <small className="oh-faint">Move changes a ticket&apos;s column. Cancel takes it off the board; its record stays. Raticode refuses anything unticked, whatever the worker&apos;s tools allow.</small>
    </> : <small className="oh-faint">Can create, edit, move and cancel tickets in every project, within the usual owner and manager rules.</small>}
  </div>;
}

const CHECK_INS = [[0, "Off"], [900, "Every 15 minutes"], [1800, "Every 30 minutes"], [3600, "Every hour"], [14400, "Every 4 hours"], [86400, "Once a day"]];
// A saved interval that isn't one of the usual choices stays selectable.
const checkInOptions = current => (CHECK_INS.some(([value]) => value === current) ? CHECK_INS : [...CHECK_INS, [current, `Every ${Math.round(current / 60)} minutes`]]);
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const localZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
const zones = () => { try { return Intl.supportedValuesOf("timeZone"); } catch { return []; } };

// Working hours: around the clock, or weekly windows in one time zone.
function HoursEditor({ model, ctx }) {
  const saved = model.config.operatingHours || { mode: "always", timezone: "UTC", windows: [], atClose: "stop" };
  const start = () => ({ ...saved, timezone: saved.mode === "scheduled" ? saved.timezone : localZone(), windows: saved.windows?.length ? saved.windows : [{ days: [0, 1, 2, 3, 4], start: "09:00", end: "17:00" }], atClose: saved.atClose || "stop" });
  const [draft, setDraft] = useState(start);
  const [resume, setResume] = useState(model.config.resumeAfterRestart === true);
  const set = values => setDraft(current => ({ ...current, ...values }));
  const setWindow = (index, values) => set({ windows: draft.windows.map((w, i) => (i === index ? { ...w, ...values } : w)) });
  const toggleDay = (index, day) => { const days = draft.windows[index].days; setWindow(index, { days: days.includes(day) ? days.filter(d => d !== day) : [...days, day].sort() }); };
  const hours = draft.mode === "scheduled" ? { mode: "scheduled", timezone: draft.timezone, windows: draft.windows, atClose: draft.atClose } : { ...saved, mode: "always" };
  const problem = draft.mode !== "scheduled" ? "" : !draft.windows.length ? "Add at least one set of hours." : draft.windows.some(w => !w.days.length) ? "Each set of hours needs at least one day." : draft.windows.some(w => w.start === w.end) ? "Start and end can't be the same time." : "";
  const state = model.organization.diagnostics?.hours;
  async function save(event) {
    event.preventDefault();
    const item = { operatingHours: hours, resumeAfterRestart: resume };
    const result = await ctx.onAct("plan_save", { kind: "operations", item, expectedRevision: model.organization.revision }, `Working hours: ${describeHours(hours)}`);
    if (result) ctx.close();
  }
  return <form className="oh-form" onSubmit={save}>
    <p className="oh-muted">Now: {describeHours(saved)}{state?.mode === "scheduled" && (state.open ? `. Open until ${zoneTime(state.closesAt, state.timezone)}.` : `. Closed until ${zoneTime(state.opensAt, state.timezone)}.`)}</p>
    <fieldset><legend>When workers may work</legend>
      <label className="oh-radio"><input type="radio" name="oh-hours-mode" checked={draft.mode !== "scheduled"} onChange={() => set({ mode: "always" })} />Around the clock (24/7)</label>
      <label className="oh-radio"><input type="radio" name="oh-hours-mode" checked={draft.mode === "scheduled"} onChange={() => set({ mode: "scheduled" })} />Only during these hours</label>
    </fieldset>
    {draft.mode === "scheduled" && <>
      <label>Time zone<input list="oh-zones" value={draft.timezone} onChange={e => set({ timezone: e.target.value })} required /><datalist id="oh-zones">{zones().map(zone => <option key={zone} value={zone} />)}</datalist></label>
      <div><span className="oh-form-label">Hours</span>
        {draft.windows.map((window, index) => <div className="oh-window" key={index}>
          <div className="oh-days" role="group" aria-label={`Days for hours ${index + 1}`}>{DAYS.map((name, day) => <button key={name} type="button" aria-pressed={window.days.includes(day)} onClick={() => toggleDay(index, day)}>{name}</button>)}</div>
          <input type="time" aria-label={`Start of hours ${index + 1}`} value={window.start} required onChange={e => setWindow(index, { start: e.target.value })} />
          <input type="time" aria-label={`End of hours ${index + 1}`} value={window.end} required onChange={e => setWindow(index, { end: e.target.value })} />
          <span className="night">{window.end <= window.start && window.end !== window.start ? "Ends the next morning" : ""}</span>
          <button className="oh-icon-btn" type="button" aria-label={`Remove hours ${index + 1}`} disabled={draft.windows.length < 2} onClick={() => set({ windows: draft.windows.filter((_, i) => i !== index) })}><X size={12} /></button>
        </div>)}
        <button className="oh-btn small ghost" type="button" disabled={draft.windows.length >= 28} onClick={() => set({ windows: [...draft.windows, { days: [5, 6], start: "10:00", end: "14:00" }] })}><Plus size={14} />Add hours</button>
        <small className="oh-faint">An end at or before the start runs past midnight, so 22:00 to 06:00 on Fri covers Friday night into Saturday morning.</small></div>
      <label>At closing time<select value={draft.atClose} onChange={e => set({ atClose: e.target.value })}>
        <option value="stop">Stop running work. It returns to To do and runs first when hours open</option>
        <option value="finish">Let running work finish. Nothing new starts</option></select></label>
    </>}
    <label className="oh-switch">Keep running after Raticode restarts<input type="checkbox" checked={resume} onChange={e => setResume(e.target.checked)} /></label>
    <small className="oh-faint">{resume ? "After a restart the organization carries on by itself. Work that was mid-run is flagged for you to reopen." : "After a restart the organization pauses until you start it."}</small>
    {problem && <p className="oh-warn-line"><AlertTriangle size={14} />{problem}</p>}
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy || Boolean(problem)}>Save hours</button><button className="oh-btn" type="button" onClick={ctx.close}>Cancel</button></div>
  </form>;
}

function WorkerEditor({ model, worker, ctx, isNew }) {
  const { capabilities, loading, refresh } = useProviderCapabilities();
  const base = worker || { id: crypto.randomUUID(), name: "", title: "", role: "", reportsTo: null, provider: "codex", model: "cli-default", effort: null, permissionMode: providerPermissionDefault("codex"), paused: true, persona: "", responsibilities: [], instructions: "", resources: { shell: true, web: false, skills: [], mcpServers: [] }, skills: [], memory: "", metadata: {}, heartbeatSeconds: 0 };
  const [draft, setDraft] = useState(() => structuredClone(base));
  const [responsibility, setResponsibility] = useState("");
  const set = values => setDraft(current => ({ ...current, ...values }));
  const fields = ["name", "title", "persona", "reportsTo", "provider", "model", "effort", "permissionMode", "paused", "responsibilities", "instructions", "ticketAccess", "heartbeatSeconds"];
  const changed = fields.filter(key => JSON.stringify(base[key] ?? null) !== JSON.stringify(draft[key] ?? null));
  const dirty = isNew || changed.length > 0;
  const capability = capabilities.find(item => item.id === draft.provider);
  const danger = dangerousPermission(draft.permissionMode) && !dangerousPermission(base.permissionMode);
  const managers = Object.values(model.workers).filter(w => w.id !== draft.id);
  async function save() {
    const clean = { ...draft };
    delete clean.color;
    const employees = isNew ? [...model.config.employees, clean] : model.config.employees.map(e => (e.id === clean.id ? { ...e, ...Object.fromEntries(fields.map(key => [key, clean[key]])) } : e));
    const reason = isNew ? `Added ${clean.name || "a worker"}, held until you start them` : `Updated ${clean.name}: ${changed.map(key => ({ reportsTo: "reports to", permissionMode: "permissions", paused: "hold", ticketAccess: "ticket permissions", heartbeatSeconds: "check-ins" }[key] || key)).join(", ")}`;
    const result = await ctx.onAct("configure", { config: { ...model.config, employees }, expectedRevision: model.organization.revision, reason }, `${reason}. Recorded as a new revision.`);
    if (result && isNew) ctx.replace(`worker:${clean.id}`);
  }
  async function remove() {
    // Their reports move up to their manager, so the chart stays connected.
    const id = base.id, config = model.config;
    const employees = config.employees.filter(e => e.id !== id).map(e => (e.reportsTo === id ? { ...e, reportsTo: base.reportsTo || null } : e));
    const without = (item, key) => { if (item[key] !== id) return item; const copy = { ...item }; delete copy[key]; return copy; };
    const next = {
      ...config, employees,
      projects: config.projects.map(item => without(item, "owner")),
      teams: (config.teams || []).map(item => without(item, "manager")),
      epics: (config.epics || []).map(item => (item.owner === id ? { ...item, owner: null } : item)),
      goalRecords: (config.goalRecords || []).map(item => (item.owner === id ? { ...item, owner: null } : item)),
      executionTargets: (config.executionTargets || []).map(item => ({ ...item, employees: item.employees.filter(e => e !== id) })).filter(item => item.employees.length),
    };
    const result = await ctx.onAct("configure", { config: next, expectedRevision: model.organization.revision, reason: `Removed ${base.name}` }, `Removed ${base.name}. Their open tickets are blocked until you reassign them.`);
    if (result) ctx.close();
  }
  const doing = worker ? Object.values(model.tickets).filter(t => t.assignee === worker.id && t.column !== "done") : [];
  return <>
    {worker && <Rec label="Working on"><div className="oh-chips">{doing.map(t => <Chip key={t.id} model={model} refId={`ticket:${t.id}`} onOpen={ctx.onOpen} />)}{!doing.length && <Muted>Nothing open</Muted>}</div></Rec>}
    <form className="oh-form" onSubmit={event => event.preventDefault()}>
      <label>Name<input required maxLength={150} value={draft.name} onChange={e => set({ name: e.target.value })} /></label>
      <label>Title<input maxLength={200} value={draft.title} onChange={e => set({ title: e.target.value })} /></label>
      <label>Persona <small>How this worker thinks and works. Keep it to two sentences.</small><textarea maxLength={500} value={draft.persona} onChange={e => set({ persona: e.target.value })} /><small>{draft.persona.length}/500</small></label>
      <div><span className="oh-form-label">Responsibilities</span><div className="oh-resp">{draft.responsibilities.map((item, index) => <span key={`${item}-${index}`}>{item}<button type="button" aria-label={`Remove ${item}`} onClick={() => set({ responsibilities: draft.responsibilities.filter((_, i) => i !== index) })}><X size={11} /></button></span>)}
        <input aria-label="Add a responsibility" placeholder="Add one, then Enter" maxLength={200} value={responsibility} onChange={e => setResponsibility(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (responsibility.trim() && draft.responsibilities.length < 20) { set({ responsibilities: [...draft.responsibilities, responsibility.trim()] }); setResponsibility(""); } } }} /></div></div>
      <label>Reports to<select value={draft.reportsTo || ""} onChange={e => set({ reportsTo: e.target.value || null })}><option value="">You</option>{managers.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
      <ProviderModelEffortFields className="oh-provider-fields" capabilities={capabilities} loading={loading} provider={draft.provider} model={draft.model} effort={draft.effort} onRefresh={refresh}
        onChange={values => set({ ...values, ...(values.provider && values.provider !== draft.provider ? { permissionMode: providerPermissionDefault(values.provider, capabilities.find(item => item.id === values.provider)) } : {}) })} />
      <label>Permissions<select value={draft.permissionMode} onChange={e => set({ permissionMode: e.target.value })}>{providerPermissionOptions(draft.provider, capability).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      {danger && <p className="oh-warn-line"><AlertTriangle size={14} />This skips the provider&apos;s sandbox or approvals. Saving asks you to confirm.</p>}
      <details className="oh-fold"><summary>Detailed instructions</summary><textarea rows={6} maxLength={100000} value={draft.instructions} onChange={e => set({ instructions: e.target.value })} aria-label="Detailed instructions" /></details>
      <label>Check-ins <small>A short run where the worker reviews their tickets, plans work into To do and picks what to do next. Only during working hours.</small>
        <select value={draft.heartbeatSeconds || 0} onChange={e => set({ heartbeatSeconds: Number(e.target.value) })}>{checkInOptions(draft.heartbeatSeconds || 0).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <TicketAccess model={model} value={draft.ticketAccess ?? null} onChange={ticketAccess => set({ ticketAccess })} />
      <label className="oh-switch">Hold this worker<input type="checkbox" checked={draft.paused} onChange={e => set({ paused: e.target.checked })} /></label>
      <div className="oh-form-actions">{danger ? <Confirm busy={ctx.busy} className="oh-btn primary" label="Save worker" confirmLabel="Save with these permissions" danger message={`${draft.name || "This worker"} could run commands without approval.`} onConfirm={save} />
        : <button className="oh-btn primary" type="button" disabled={!dirty || ctx.busy || !draft.name.trim()} onClick={save}>{isNew ? "Add worker" : "Save worker"}</button>}
        {isNew ? <button className="oh-btn" type="button" onClick={ctx.close}>Cancel</button> : <button className="oh-btn" type="button" disabled={!dirty} onClick={() => setDraft(structuredClone(base))}>Discard</button>}
        {dirty && !isNew && <span className="oh-dirty">Unsaved changes</span>}
        {!isNew && <span className="oh-push"><Confirm busy={ctx.busy} danger label={<><Trash2 size={14} />Remove worker</>} confirmLabel="Remove" message={`Remove ${base.name}? ${plural(Object.values(model.tickets).filter(t => t.assignee === base.id && t.column !== "done").length, "open ticket")} will be blocked until reassigned. History is kept.`} onConfirm={remove} /></span>}</div>
    </form>
  </>;
}

function TicketForm({ model, epicId, ctx }) {
  const epic = model.epics[epicId];
  const [draft, setDraft] = useState({ title: "", epicId: epicId || "", assignee: epic?.owner || "", doneWhen: "", status: "backlog" });
  const set = values => setDraft(current => ({ ...current, ...values }));
  return <form className="oh-form" onSubmit={async event => { event.preventDefault(); const result = await ctx.onAct("task_create", { ...draft, epicId: draft.epicId || null, assignee: draft.assignee || null }, task => `${task.key} added to ${draft.status === "todo" ? "To do" : "Backlog"}`); if (result?.id) ctx.replace(`ticket:${result.id}`); }}>
    <label>Title<input required autoFocus maxLength={500} value={draft.title} onChange={e => set({ title: e.target.value })} /></label>
    <label>Done when <small>One line in your words</small><textarea maxLength={1000} rows={2} value={draft.doneWhen} onChange={e => set({ doneWhen: e.target.value })} /></label>
    <EpicSelect model={model} value={draft.epicId} onChange={value => set({ epicId: value, assignee: draft.assignee || model.epics[value]?.owner || "" })} />
    <WorkerSelect model={model} label="Assignee" value={draft.assignee} onChange={assignee => set({ assignee })} />
    <label>Column<select value={draft.status} onChange={e => set({ status: e.target.value })}><option value="backlog">Backlog (won&apos;t run yet)</option><option value="todo">To do (runs when the organization runs)</option></select></label>
    <div className="oh-form-actions"><button className="oh-btn primary" type="submit" disabled={ctx.busy}>Add ticket</button><button className="oh-btn" type="button" onClick={ctx.close}>Cancel</button></div>
  </form>;
}

/* -------------------------------------------------------------------- Sheet */

function renderSheet(model, ref, ctx) {
  const index = ref.indexOf(":");
  const type = ref.slice(0, index), id = ref.slice(index + 1);
  if (type === "ticket") { const t = model.tickets[id]; return t && { title: t.title, sub: <><span className="oh-key">{t.key}</span><Pill state={t.column} /><Avatar model={model} id={t.assignee} /><span>{model.workers[t.assignee]?.name || "Unassigned"}</span></>, body: <TicketSheet key={id} model={model} ticket={t} ctx={ctx} /> }; }
  if (type === "run") { const r = model.runs[id]; const t = r && model.tickets[r.taskId]; return r && { title: `${model.workers[r.employeeId]?.name?.split(" ")[0] || "A worker"}'s run on ${t?.key || "a ticket"}`, sub: <><Avatar model={model} id={r.employeeId} /><span>{new Date(r.startedAt).toLocaleString()}</span>{r.finishedAt ? <><span>{Math.round((r.elapsedSeconds || 0) / 60)} min</span>{r.usage?.total_tokens ? <span>{Math.round(r.usage.total_tokens / 1000)}k tokens</span> : null}<Pill state={r.status === "completed" ? "done" : "blocked"}>{r.status}</Pill></> : <Pill state="running">Running</Pill>}</>, body: <RunSheet key={id} model={model} run={r} ctx={ctx} /> }; }
  if (type === "claim") { const c = model.runtime.claims?.[id]; return c && { title: `${{ check: "Check", quote: "Quote", commit: "Commit", file: "File" }[c.evidence?.kind] || "Claim"} for ${model.tickets[c.taskId]?.key || "a ticket"}`, sub: <span>Evidence</span>, body: <ClaimSheet model={model} claim={c} ctx={ctx} /> }; }
  if (type === "epic") { const e = model.epics[id]; return e && { title: e.title, sub: <><span>Epic</span><Avatar model={model} id={e.owner} /><span>{pct(epicProgress(model, id))}% accepted</span></>, body: <EpicSheet key={id} model={model} epic={e} ctx={ctx} /> }; }
  if (type === "ms") { const m = model.milestones[id]; return m && { title: m.title, sub: <><span>Milestone</span>{m.dueAt && <span>Due {formatDate(m.dueAt)}</span>}<span>{pct(milestoneProgress(model, id))}%</span></>, body: <MilestoneSheet key={id} model={model} milestone={m} ctx={ctx} /> }; }
  if (type === "orgms") { const m = model.orgMilestones[id]; return m && { title: m.title, sub: <><span>Organization milestone</span>{m.dueAt && <span>Due {formatDate(m.dueAt)}</span>}</>, body: <OrgMilestoneSheet key={id} model={model} milestone={m} ctx={ctx} /> }; }
  if (type === "project") { const p = model.projects[id]; return p && { title: p.name, sub: <><span>Project</span>{p.workspacePath && <code>{p.workspacePath}</code>}</>, body: <ProjectSheet key={id} model={model} project={p} ctx={ctx} /> }; }
  if (type === "kb") { const k = model.knowledge[id]; return k && { title: k.title, sub: <><span className="oh-kind"><i style={{ background: `var(--k-${k.kind})` }} />{KINDS[k.kind]}</span>{k.topic && <span>{k.topic}</span>}{k.changedAt && <span className="oh-ev warn"><AlertTriangle size={12} />Changed {ago(k.changedAt)}</span>}</>, body: <KnowledgeSheet key={id} model={model} entry={k} ctx={ctx} /> }; }
  if (type === "worker") { const w = model.workers[id]; const [state, label] = workerStatus(model, id); const runs = Object.values(model.runs).filter(r => r.employeeId === id).length; return w && { title: w.name, sub: <><Avatar model={model} id={id} /><span>{w.title || w.role}</span><Pill state={state}>{label}</Pill><span>{plural(runs, "run")}</span></>, body: <WorkerEditor key={`${id}-${model.organization.revision}`} model={model} worker={w} ctx={ctx} /> }; }
  if (type === "new") {
    const [kind, context] = [id.split(":")[0], id.split(":").slice(1).join(":")];
    const forms = {
      ticket: () => <TicketForm model={model} epicId={context} ctx={ctx} />,
      epic: () => <PlanForm kind="epic" model={model} item={{ milestoneId: context || "" }} ctx={ctx} onDone={ctx.close} />,
      ms: () => <PlanForm kind="milestone" model={model} item={{}} fixed={{ projectId: context }} ctx={ctx} onDone={ctx.close} />,
      orgms: () => <PlanForm kind="milestone" model={model} item={{}} ctx={ctx} onDone={ctx.close} />,
      project: () => <PlanForm kind="project" model={model} item={{}} ctx={ctx} onDone={ctx.close} />,
      kb: () => <EntryForm model={model} ctx={ctx} onDone={result => (result ? ctx.replace(`kb:${result.id}`) : ctx.close())} />,
      worker: () => <WorkerEditor model={model} ctx={ctx} isNew />,
      import: () => <ImportForm model={model} ctx={ctx} />,
      hours: () => <HoursEditor key={model.organization.revision} model={model} ctx={ctx} />,
    };
    return forms[kind] && { title: entity(model, ref)?.label || "New", sub: kind === "worker" ? <span>New workers start held. Start them when they&apos;re ready.</span> : kind === "ms" ? <span>{model.projects[context]?.name}</span> : kind === "hours" ? <span>When workers may start and keep working</span> : null, body: forms[kind]() };
  }
  return null;
}

// Details fill the main content: a back control at the top left, the path that led here,
// then the record. Escape (handled by the organization view) returns to the list.
export default function OrganizationDetail({ model, trail, setTrail, ctx, listLabel }) {
  const heading = useRef(null);
  const ref = trail.at(-1);
  const content = ref ? renderSheet(model, ref, ctx) : null;
  useEffect(() => { if (ref) setTimeout(() => heading.current?.focus(), 30); }, [ref]);
  if (!ref) return null;
  const previous = trail.length > 1 ? entity(model, trail.at(-2)) : null;
  const backLabel = previous ? (previous.type === "ticket" ? previous.key : previous.label) : listLabel;
  const back = () => (trail.length > 1 ? setTrail(trail.slice(0, -1)) : ctx.close());
  return <article className="oh-detail" aria-label="Details">
    <div className="oh-detail-bar">
      <button className="oh-back" type="button" onClick={back} aria-label={`Back to ${backLabel}`}><ArrowLeft size={16} /><span>{backLabel}</span></button>
      {trail.length > 1 && <nav className="oh-trail" aria-label="How you got here">{trail.map((item, index) => { const e = entity(model, item); return <span key={`${item}-${index}`}>{index > 0 && <span className="sep">›</span>}<button type="button" aria-current={index === trail.length - 1 ? "true" : undefined} onClick={() => setTrail(trail.slice(0, index + 1))}>{e ? (e.type === "ticket" ? e.key : e.label) : "Removed"}</button></span>; })}</nav>}
      <span className="grow" />
      <span className="oh-esc-hint"><kbd>Esc</kbd> back to {listLabel}</span>
    </div>
    {content ? <>
      <header className="oh-detail-head">
        <h2 ref={heading} tabIndex={-1}>{content.title}</h2>
        {content.sub && <div className="oh-sheet-sub">{content.sub}</div>}
      </header>
      <div className="oh-detail-body">{content.body}</div>
    </> : <div className="oh-panel oh-blank"><h2>This item is gone</h2><p>It was removed or renamed since you opened it.</p><button className="oh-btn" type="button" onClick={ctx.close}>Back to {listLabel}</button></div>}
  </article>;
}
