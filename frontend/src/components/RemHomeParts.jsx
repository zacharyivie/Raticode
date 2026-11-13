import { useEffect, useRef, useState } from "react";
import { AlarmClock, AlertCircle, BellRing, Check, CheckCircle2, Clock3, Hammer, Hourglass, Loader2, Moon, MousePointerClick, Pause, PauseCircle, PencilLine, Play, RotateCw, StopCircle, Telescope, Trash2, Wrench, XCircle } from "lucide-react";
import { TASK_TYPES, canRetryPrerequisite, runControls } from "../lib/remHome.js";
import { acknowledgeRemRun, cancelRemRun, pauseRemRun, pauseRemTask, resumeRemRun, resumeRemTask, runRemTaskNow, wakeRemRun } from "../lib/remHomeStore.js";

const TYPE_ICONS = { research: Telescope, maintain: Wrench, develop: Hammer, operations: MousePointerClick, reminder: BellRing };
export function TaskTypeIcon({ type, size = 15, ...props }) {
  const Icon = TYPE_ICONS[type] || Wrench;
  return <Icon size={size} aria-hidden="true" strokeWidth={1.75} {...props} />;
}
export const typeLabel = type => TASK_TYPES[type]?.label || "Task";

// A task's leading glyph: a spinner while work is running, otherwise its outcome or type.
export function TaskGlyph({ activity, type, size = 15 }) {
  if (activity.spinning) return <Loader2 size={size} className="rem-spin" aria-label={activity.label} role="img" />;
  if (activity.sleeping) return <Moon size={size} aria-hidden="true" strokeWidth={1.75} />;
  if (activity.kind === "queued") return <Clock3 size={size} aria-hidden="true" strokeWidth={1.75} />;
  if (activity.kind === "attention") return <AlertCircle size={size} aria-hidden="true" strokeWidth={1.75} />;
  if (activity.kind === "paused") return <PauseCircle size={size} aria-hidden="true" strokeWidth={1.75} />;
  if (activity.kind === "waiting") return <Hourglass size={size} aria-hidden="true" strokeWidth={1.75} />;
  return <TaskTypeIcon type={type} size={size} />;
}

const OUTCOME_ICONS = { success: CheckCircle2, attention: AlertCircle, danger: XCircle };
export function OutcomeGlyph({ status, size = 16 }) {
  if (status.spinning) return <Loader2 size={size} className="rem-spin" aria-hidden="true" />;
  if (status.state === "queued") return <Clock3 size={size} aria-hidden="true" />;
  if (status.state === "paused") return <PauseCircle size={size} aria-hidden="true" />;
  const Icon = OUTCOME_ICONS[status.tone];
  return Icon ? <Icon size={size} aria-hidden="true" /> : <TaskTypeIcon type={status.type} size={size} />;
}

// Small action menu. Escape or an outside press closes it and returns focus.
export function RemMenu({ label, trigger, items, className = "" }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    panelRef.current?.querySelector("[role=menuitem]:not(:disabled)")?.focus();
    const close = refocus => { setOpen(false); if (refocus) triggerRef.current?.focus(); };
    const press = event => { if (!triggerRef.current?.contains(event.target) && !panelRef.current?.contains(event.target)) close(false); };
    const key = event => {
      if (event.key === "Escape") { event.stopPropagation(); close(true); return; }
      if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
      event.preventDefault();
      const options = [...(panelRef.current?.querySelectorAll("[role=menuitem]:not(:disabled)") ?? [])];
      const index = options.indexOf(document.activeElement);
      options[(index + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length]?.focus();
    };
    document.addEventListener("pointerdown", press, true);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("pointerdown", press, true); document.removeEventListener("keydown", key, true); };
  }, [open]);
  const visible = items.filter(Boolean);
  return <div className={`rem-menu-anchor ${className}`} data-open={open || undefined}>
    <button ref={triggerRef} type="button" className="rem-menu-trigger" aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label} onClick={event => { event.stopPropagation(); setOpen(!open); }}>{trigger}</button>
    {open ? <div ref={panelRef} role="menu" aria-label={label} className="rem-menu">
      {visible.map(item => <button key={item.label} type="button" role="menuitem" data-danger={item.danger || undefined} disabled={item.disabled} onClick={() => { setOpen(false); item.onSelect(); }}>
        {item.icon ? <item.icon size={14} aria-hidden="true" /> : null}<span>{item.label}</span>
      </button>)}
    </div> : null}
  </div>;
}

// Task controls shared by the sidebar menu and the task sheet footer.
export function taskActions(task, activity, { onEdit, onDelete }) {
  const running = activity.kind === "running" || activity.kind === "queued";
  // A task waiting for others can be started now, which drops what it waits for; a held
  // one also offers to run each named task that didn't succeed again. Times, the self-work
  // window and runs that were going when the user asked can't be retried.
  const waiting = Boolean(task.dependency);
  const retries = (task.dependency?.prerequisites || []).filter(canRetryPrerequisite).map(item => ({
    id: `retry-${item.task_id}`, label: `Retry “${item.title}”`, icon: RotateCw, onSelect: () => void runRemTaskNow({ id: item.task_id }),
  }));
  // Pausing a run keeps its thread and worktree; pausing a schedule only skips future runs.
  const controls = runControls(task);
  const pausedRun = Boolean(controls.resume);
  return [
    { id: "edit", label: "Edit", icon: PencilLine, onSelect: onEdit },
    ...retries,
    activity.sleeping ? { id: "wake", label: "Wake now", icon: AlarmClock, onSelect: () => void wakeRemRun(task) } : null,
    controls.pause ? { id: "pause-run", label: "Pause", icon: Pause, onSelect: () => void pauseRemRun(task) } : null,
    pausedRun ? { id: "resume-run", label: "Resume", icon: Play, onSelect: () => void resumeRemRun(task) } : null,
    running ? { id: "stop", label: "Stop current run", icon: StopCircle, disabled: task.active_run?.state === "cancelling", onSelect: () => void cancelRemRun(task) }
      : pausedRun ? { id: "stop", label: "Stop", icon: StopCircle, onSelect: () => void cancelRemRun({ ...task, active_run: task.last_run }) }
        : { id: "run", label: waiting ? "Start now" : task.last_run ? "Run again" : "Run now", icon: waiting ? Play : RotateCw, onSelect: () => void runRemTaskNow(task) },
    task.state === "paused" ? { id: "resume", label: "Resume schedule", icon: Play, onSelect: () => void resumeRemTask(task) } : task.schedule?.kind && task.schedule.kind !== "once" ? { id: "pause", label: "Pause schedule", icon: Pause, onSelect: () => void pauseRemTask(task) } : null,
    activity.kind === "attention" && !activity.held ? { id: "dismiss", label: "Dismiss", icon: Check, onSelect: () => void acknowledgeRemRun(task) } : null,
    { id: "delete", label: "Delete…", icon: Trash2, danger: true, onSelect: onDelete },
  ].filter(Boolean);
}
