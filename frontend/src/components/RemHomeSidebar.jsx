import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, MoreHorizontal, Plus } from "lucide-react";
import { groupTasks, statusLine } from "../lib/remHome.js";
import { connectRemHome, deleteRemTask, openNewTask, openTaskSheet, showRemMessage, useRemHome } from "../lib/remHomeStore.js";
import { RemMenu, TaskGlyph, taskActions, typeLabel } from "./RemHomeParts.jsx";
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
  return <li className="rem-task-row" data-kind={activity.kind} data-tone={activity.tone}>
    <button type="button" className="rem-task-open" title={`${typeLabel(task.type)} · ${task.title}`} onClick={open}>
      <span className="rem-task-glyph"><TaskGlyph activity={activity} type={task.type} /></span>
      <span className="rem-task-text"><strong>{task.title}</strong><small>{activity.label}</small></span>
    </button>
    <RemMenu label={`${task.title} actions`} className="rem-task-menu" trigger={<MoreHorizontal size={15} />} items={taskActions(task, activity, { onEdit: open, onDelete: () => setConfirming(true) })} />
  </li>;
}

function Group({ group }) {
  const [open, setOpen] = useState(group.id !== "done");
  return <section className="rem-side-group" aria-label={group.label}>
    <button type="button" className="rem-side-group-heading" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
      <span>{group.label}</span><small>{group.items.length}</small>
    </button>
    {open ? <ul>{group.items.map(({ task, activity }) => <TaskRow key={task.id} task={task} activity={activity} />)}</ul> : null}
  </section>;
}

export default function RemHomeSidebar({ active, open, onOpen, projectOptions = [] }) {
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
    <div className="rem-side-scroll">
      {store.status === "loading" && !store.tasks.length ? <p role="status" className="rem-side-hint">Checking in with Rem…</p>
        : groups.length ? groups.map(group => <Group key={group.id} group={group} />)
          : <div className="rem-side-empty">
            <p>Nothing on Rem’s plate yet.</p>
            <p>Ask in the conversation, or add a task that runs on a schedule.</p>
            <button type="button" onClick={openNewTask}><Plus size={14} aria-hidden="true" />New task</button>
          </div>}
    </div>
  </section>;
}
