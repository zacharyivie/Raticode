import { useEffect, useMemo, useState } from "react";
import { CheckCheck, ChevronDown, ChevronRight, MoreHorizontal, Play, Plus, Square, Target } from "lucide-react";
import { dismissibleUpdates, groupTasks, statusLine } from "../lib/remHome.js";
import { acknowledgeAllRemRuns, connectRemHome, deleteRemTask, openNewTask, openTaskSheet, showRemMessage, useRemHome } from "../lib/remHomeStore.js";
import { modeLabel, saveSelfWork, selfWorkToggle, useKanban } from "../lib/remKanban.js";
import { taskObjective } from "../lib/remObjectives.js";
import { RemMenu, TaskGlyph, taskActions, typeLabel } from "./RemHomeParts.jsx";
import RemObjectives from "./RemObjectives.jsx";
import RemDecisions from "./RemDecisions.jsx";
import TaskSheet from "./RemTaskSheet.jsx";
import RatFaceIcon from "./RatFaceIcon.jsx";
import "./RemHome.css";

// Re-render relative times ("in 5 min") without waiting for new server data.
function useMinuteClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = window.setInterval(() => setNow(new Date()), 30000); return () => window.clearInterval(timer); }, []);
  return now;
}

// Opening a task replaces this list with its details; the main area stays as it is.
function TaskRow({ task, activity }) {
  const [confirming, setConfirming] = useState(false);
  const open = () => openTaskSheet(task.id);
  if (confirming) {
    return <li className="rem-task-row" data-confirming="true">
      <p>Delete <strong>{task.title}</strong>?{activity.kind === "running" || activity.kind === "queued" ? " Its current run stops." : ""}</p>
      <div className="rem-task-confirm">
        <button type="button" data-danger="true" autoFocus onClick={() => { setConfirming(false); void deleteRemTask(task); }}>Delete</button>
        <button type="button" onClick={() => setConfirming(false)}>Keep</button>
      </div>
    </li>;
  }
  // The self-work objective this task's run serves, if any: one short line, full text on hover.
  const objective = taskObjective(task);
  return <li className="rem-task-row" data-kind={activity.kind} data-tone={activity.tone}>
    <button type="button" className="rem-task-open" title={`${typeLabel(task.type)} · ${task.title}${objective ? `\nFor objective: ${objective.text}` : ""}`} onClick={open}>
      <span className="rem-task-glyph"><TaskGlyph activity={activity} type={task.type} /></span>
      <span className="rem-task-text"><strong>{task.title}</strong><small>{activity.label}</small>
        {objective ? <small className="rem-task-objective"><Target size={10} aria-hidden="true" /><span>{objective.text}</span></small> : null}
      </span>
    </button>
    <RemMenu label={`${task.title} actions`} className="rem-task-menu" trigger={<MoreHorizontal size={15} />} items={taskActions(task, activity, { onEdit: open, onDelete: () => setConfirming(true) })} />
  </li>;
}

// Dismiss all in the Needs you / Updates heading: every update at once. Runs that ask
// the user something and tasks on hold aren't counted and stay in the group.
function DismissAll({ group, now }) {
  const [busy, setBusy] = useState(false);
  const updates = dismissibleUpdates(group.items.map(item => item.task), now);
  if (!updates.length) return null;
  const staying = group.items.length - updates.length;
  const what = `${updates.length} task ${updates.length === 1 ? "update" : "updates"}`;
  const title = `Dismiss ${what}.${staying ? ` ${staying} that ${staying === 1 ? "needs" : "need"} your answer or ${staying === 1 ? "is" : "are"} on hold ${staying === 1 ? "stays" : "stay"}.` : ""} Nothing is stopped or deleted.`;
  return <button type="button" className="rem-side-dismiss-all" disabled={busy} title={title} aria-label={`Dismiss all: ${what}`}
    onClick={async () => { setBusy(true); try { await acknowledgeAllRemRuns(updates); } finally { setBusy(false); } }}>
    <CheckCheck size={12} aria-hidden="true" /><span>Dismiss all</span><small>{updates.length}</small>
  </button>;
}

function Group({ group, now }) {
  const [open, setOpen] = useState(group.id !== "done");
  return <section className="rem-side-group" aria-label={group.label}>
    <div className="rem-side-group-head">
      <button type="button" className="rem-side-group-heading" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
        <span>{group.label}</span><small>{group.items.length}</small>
      </button>
      {group.id === "attention" ? <DismissAll group={group} now={now} /> : null}
    </div>
    {open ? <ul>{group.items.map(({ task, activity }) => <TaskRow key={task.id} task={task} activity={activity} />)}</ul> : null}
  </section>;
}

// Start or stop self-work, under Rem's name so it's always in view. Off, Start begins work now;
// on, it shows what self-work is doing and stops it, and while self-work waits for quiet
// minutes, Start now skips the wait. A failed poll keeps the last state quietly; only a failed
// click shows its error.
function SelfWorkToggle({ active }) {
  const { selfWork, error, run } = useKanban(active, 5000);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const toggle = selfWorkToggle(selfWork);
  if (!toggle) return null;
  const Icon = toggle.on ? Square : Play;
  const send = async change => {
    setBusy(true);
    try { setFailed(!(await run(() => saveSelfWork(change)))); } finally { setBusy(false); }
  };
  return <div className="rem-self-work" data-on={toggle.on || undefined} data-mode={toggle.mode} data-testid="rem-self-work">
    <button type="button" className="rem-self-work-toggle" disabled={busy} onClick={() => send(toggle.change)}
      title={toggle.on ? "Turn self-work off. Work already running finishes." : "Start self-work now, without waiting for quiet minutes. It keeps going until you stop it, and your objectives stay as they are."}>
      <Icon size={12} fill="currentColor" aria-hidden="true" />
      <span>{toggle.label}</span>
      {toggle.on ? <small className="rem-self-work-mode">{modeLabel(toggle.mode)}</small> : null}
    </button>
    {toggle.detail || toggle.startNow ? <p className="rem-self-work-line" title={toggle.detail}>
      {toggle.detail}
      {toggle.startNow ? <> <button type="button" className="rem-self-work-now" disabled={busy} onClick={() => send(toggle.startNow)}
        title="Start self-work now instead of waiting for quiet minutes">Start now</button></> : null}
    </p> : null}
    {failed && error ? <p role="alert" className="rem-self-work-error">{error}</p> : null}
  </div>;
}

export default function RemHomeSidebar({ active, open, onOpen, projectOptions = [], projectRoot = "" }) {
  const store = useRemHome();
  const now = useMinuteClock();
  useEffect(() => (active ? connectRemHome() : undefined), [active]);
  const groups = useMemo(() => groupTasks(store.tasks, now), [store.tasks, now]);
  const tickets = useMemo(() => ({ ...store.taskTickets, ...store.chatTickets }), [store.taskTickets, store.chatTickets]);
  const panel = `rem-side min-h-0 min-w-0 flex-1 flex-col text-ink ${active ? "flex" : "hidden"}`;
  if (store.sheet) {
    return <section id="sidebar-panel-rem" role="tabpanel" aria-labelledby="sidebar-tab-rem" hidden={!active} className={`${panel} rem-side-task`}>
      <TaskSheet key={store.sheet.mode === "task" ? store.sheet.taskId : "new"} sheet={store.sheet} tasks={store.tasks} projectOptions={projectOptions} now={now} messages={store.messages} onShowMessage={showRemMessage} active={active} tickets={tickets} />
    </section>;
  }
  return <section id="sidebar-panel-rem" role="tabpanel" aria-labelledby="sidebar-tab-rem" hidden={!active} className={panel}>
    <div className="rem-side-heading">
      <button type="button" className="rem-side-home" aria-current={open ? "true" : undefined} onClick={() => onOpen?.()}>
        <span className="rem-side-avatar"><RatFaceIcon size={20} /></span>
        <span><strong>Rem</strong><small>{store.status === "offline" ? "Not connected" : statusLine(store.tasks, now)}</small></span>
      </button>
      <button type="button" className="rem-side-icon" aria-label="New task" title="New task" onClick={openNewTask}><Plus size={16} /></button>
    </div>
    <SelfWorkToggle active={active} />
    <div className="rem-side-scroll">
      {store.status === "ready" || store.objectives.items.length ? <RemObjectives objectives={store.objectives} tasks={store.tasks} /> : null}
      {store.decisions.items.length ? <RemDecisions decisions={store.decisions} project={projectRoot} /> : null}
      {store.status === "loading" && !store.tasks.length ? <p role="status" className="rem-side-hint">Checking in with Rem…</p>
        : groups.length ? groups.map(group => <Group key={group.id} group={group} now={now} />)
          : <div className="rem-side-empty">
            <p>Nothing on Rem’s plate yet.</p>
            <p>Ask in the conversation, or add a task that runs on a schedule.</p>
            <button type="button" onClick={openNewTask}><Plus size={14} aria-hidden="true" />New task</button>
          </div>}
    </div>
  </section>;
}
