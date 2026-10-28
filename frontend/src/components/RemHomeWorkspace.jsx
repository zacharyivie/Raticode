import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, ChevronRight, MessageSquare, MoreHorizontal, Plus, ShieldCheck, Settings, Sparkles, Trash2, X } from "lucide-react";
import MarkdownContent from "./MarkdownContent.jsx";
import RatFaceIcon from "./RatFaceIcon.jsx";
import RemAvatar from "./RemAvatar.jsx";
import { OutcomeGlyph, RemMenu, TaskGlyph, TaskTypeIcon, taskActions, typeLabel } from "./RemHomeParts.jsx";
import {
  CREATABLE_TYPES, TASK_TYPES, WEEKDAYS, changesActiveWork, criteriaSummary, describeSchedule, draftTitle, draftToRequest,
  conversationMessages, formatWhen, messageTime, messagesByDay, newTaskDraft, projectLabel, runState, scheduleIsEditable, statusLine, taskActivity, taskMessages, taskRecap,
  latestWorkMessageIds, taskToDraft, validateDraft, weekdayShort, workStatus,
} from "../lib/remHome.js";
import {
  clearRemHistory, closeTaskSheet, connectRemHome, createRemTask, deleteRemTask, dismissNotice, loadRemHomeFixture, openRemThread,
  openTaskSheet, resetRemHomeStore, retryRemMessage, sendRemMessage, suggestMessage, updateRemTask, useRemHome,
} from "../lib/remHomeStore.js";
import "./RemHome.css";

const SUGGESTIONS = [
  "What's running right now?",
  "Research a topic and save a report",
  "Every Monday, check for failed workflow runs",
  "Remind me Friday to send the release notes",
];
const devPreview = Boolean(import.meta.env?.DEV);

export default function RemHomeWorkspace({ projectOptions = [], avatar = {}, onOpenLink, onOpenSettings }) {
  const store = useRemHome();
  const [confirmClear, setConfirmClear] = useState(false);
  const [messageFocus, setMessageFocus] = useState(null);
  useEffect(() => connectRemHome(), []);
  const now = useNow();
  const replying = store.conversation?.turn_state === "replying";
  const status = store.status === "offline" ? "Not connected" : replying ? "Thinking…" : statusLine(store.tasks, now);
  return <section className="rem-home" aria-label="Rem">
    <header className="rem-home-header">
      <span className="rem-face" data-size="lg"><RatFaceIcon size={22} /></span>
      <div className="rem-home-title"><h2>Rem</h2><p role="status">{status}</p></div>
      {store.fixture ? <span className="rem-preview-pill">Preview<button type="button" onClick={() => { resetRemHomeStore(); connectRemHome(); }}>Exit</button></span> : null}
      <RemMenu label="Rem options" className="rem-home-options" trigger={<MoreHorizontal size={18} />} items={[
        { label: "Provider, model and effort…", icon: Settings, onSelect: onOpenSettings },
        { label: "Clear history and tasks…", icon: Trash2, danger: true, disabled: store.resetting || store.status !== "ready", onSelect: () => setConfirmClear(true) },
      ]} />
    </header>
    {confirmClear ? <div className="rem-clear-confirm" role="alertdialog" aria-labelledby="rem-clear-title" aria-describedby="rem-clear-description">
      <h3 id="rem-clear-title">Start fresh with Rem?</h3>
      <p id="rem-clear-description">Stop Rem’s running work and permanently remove its conversation, tasks, memories, and task threads, including archived copies. Your own threads, project files, and saved reports stay.</p>
      <div><button type="button" disabled={store.resetting} onClick={() => setConfirmClear(false)}>Keep history</button><button type="button" data-danger="true" disabled={store.resetting} onClick={async () => { if (await clearRemHistory()) setConfirmClear(false); }}>{store.resetting ? "Clearing…" : "Clear history and tasks"}</button></div>
    </div> : null}
    {store.notice ? <div role="status" className="rem-home-notice"><span>{store.notice}</span><button type="button" aria-label="Dismiss" title="Dismiss" onClick={dismissNotice}><X size={14} /></button></div> : null}
    {store.status === "error" && store.error ? <div role="alert" className="rem-home-notice" data-tone="danger"><span>{store.error}</span></div> : null}
    <div className="rem-home-main" data-sheet={store.sheet ? "open" : undefined}>
      <Conversation key={store.conversation?.id || "loading"} store={store} now={now} replying={replying} avatar={avatar} onOpenLink={onOpenLink} messageFocus={messageFocus} />
      {store.sheet ? <>
        <button type="button" className="rem-sheet-scrim" aria-label="Close task" tabIndex={-1} onClick={closeTaskSheet} />
        <TaskSheet key={store.sheet.mode === "task" ? store.sheet.taskId : "new"} sheet={store.sheet} tasks={store.tasks} projectOptions={projectOptions} now={now} messages={store.messages} onShowMessage={id => { setMessageFocus({ id }); closeTaskSheet(); }} />
      </> : null}
    </div>
  </section>;
}

function useNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = window.setInterval(() => setNow(new Date()), 30000); return () => window.clearInterval(timer); }, []);
  return now;
}

function Conversation({ store, now, replying, avatar, onOpenLink, messageFocus }) {
  const scrollRef = useRef(null);
  const pinnedRef = useRef(true);
  const days = useMemo(() => messagesByDay(conversationMessages(store.messages), now), [store.messages, now]);
  const liveWork = useMemo(() => latestWorkMessageIds(store.messages), [store.messages]);
  const offline = store.status === "offline";
  const last = store.messages.at(-1);
  // Follow new messages unless the person has scrolled up to read.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedRef.current) element.scrollTop = element.scrollHeight;
  }, [store.messages.length, last?.id, replying]);
  useLayoutEffect(() => {
    if (!messageFocus) return;
    const message = [...(scrollRef.current?.querySelectorAll("[data-message-id]") || [])]
      .find(element => element.dataset.messageId === messageFocus.id);
    if (!message) return;
    pinnedRef.current = false;
    message.focus({ preventScroll: true });
    message.scrollIntoView({ block: "center" });
  }, [messageFocus]);
  return <div className="rem-convo">
    <div ref={scrollRef} className="rem-stream" onScroll={event => { const element = event.currentTarget; pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
      <div className="rem-stream-inner" role="log" aria-label="Conversation with Rem" aria-live="polite">
        {store.messages.length ? days.map(day => <div key={day.label} className="rem-day">
          <div className="rem-day-label" role="separator"><span>{day.label}</span></div>
          {day.messages.map((message, index) => <Message key={message.id} message={message} first={day.messages[index - 1]?.role !== message.role} tasks={store.tasks} onOpenLink={onOpenLink} live={liveWork.has(message.id)} />)}
        </div>) : <Welcome offline={offline} loading={store.status === "loading"} avatar={avatar} />}
        {replying ? <div className="rem-msg" data-role="rem"><span className="rem-face"><RatFaceIcon size={18} /></span><div className="rem-typing" role="status" aria-label="Rem is replying"><i /><i /><i /></div></div> : null}
      </div>
    </div>
    <Composer disabled={offline || store.status === "loading" || store.resetting} replying={replying} request={store.composerRequest} offline={offline} />
  </div>;
}

// Rem's animated sprite greets you here; the rat face stands in when the sprite is turned off.
function Welcome({ offline, loading, avatar }) {
  return <div className="rem-welcome">
    {avatar.enabled !== false
      ? <RemAvatar visible animated={avatar.animated !== false} reducedMotion={avatar.reducedMotion} />
      : <span className="rem-welcome-face"><RatFaceIcon size={44} strokeWidth={1.4} /></span>}
    <h3>{loading ? "Just a sec…" : "Hi, I’m Rem."}</h3>
    {offline ? <>
      <p>My task service isn’t running in this version yet, so I can’t take on work here. Your threads still work in the Rem pane.</p>
      {devPreview ? <button type="button" className="rem-quiet-button" onClick={() => void loadRemHomeFixture()}><Sparkles size={14} aria-hidden="true" />Preview with sample data</button> : null}
    </> : <>
      <p>Tell me what you need. I’ll start the work, check it, and let you know when it’s done.</p>
      {!loading ? <div className="rem-suggestions" aria-label="Ideas">
        {SUGGESTIONS.map(text => <button key={text} type="button" onClick={() => suggestMessage(text)}>{text}</button>)}
      </div> : null}
    </>}
  </div>;
}

function Message({ message, first, tasks, onOpenLink, live = true }) {
  if (message.role === "user") {
    return <div className="rem-msg" data-message-id={message.id} tabIndex={-1} data-role="user" data-first={first || undefined} data-pending={message.pending || undefined}>
      <div className="rem-bubble" title={messageTime(message.created_at)}>{message.body}</div>
      {message.failed ? <p className="rem-msg-failed">Didn’t send. <button type="button" onClick={() => retryRemMessage(message.id)}>Try again</button></p> : null}
    </div>;
  }
  return <div className="rem-msg" data-message-id={message.id} tabIndex={-1} data-role="rem" data-first={first || undefined}>
    {first ? <span className="rem-face"><RatFaceIcon size={18} /></span> : <span className="rem-face-space" aria-hidden="true" />}
    <div className="rem-msg-body" title={messageTime(message.created_at)}>
      <MarkdownContent onOpenRelativeLink={onOpenLink} compact className="rem-markdown" value={message.body} />
      {message.work ? <WorkCard work={message.work} tasks={tasks} live={live} /> : null}
    </div>
  </div>;
}

// Live view of the thread Rem started for a request. The thread link is always shown.
// Earlier cards for the same run point to the newest one instead of repeating its state.
function WorkCard({ work, tasks, live = true }) {
  const current = workStatus(work, tasks);
  const status = live ? current : { ...current, tone: "neutral", spinning: false, state: "earlier", label: "Latest update below", verificationThreadId: null };
  const checks = !live || status.state === "succeeded" ? "" : criteriaSummary(status.criteria);
  return <div className="rem-work" data-tone={status.tone} data-spinning={status.spinning || undefined} data-earlier={live ? undefined : true}>
    <span className="rem-work-glyph"><OutcomeGlyph status={status} /></span>
    <button type="button" className="rem-work-main" disabled={!status.task} onClick={() => status.task && openTaskSheet(status.task.id)} title={status.task ? "Task details" : status.deleted ? "This task was deleted" : undefined}>
      <strong>{status.title}</strong>
      <small>{[status.type ? typeLabel(status.type) : "", status.label, checks].filter(Boolean).join(" · ")}</small>
    </button>
    <div className="rem-work-links">
      {status.threadId ? <button type="button" onClick={() => openRemThread(status.threadId)}><MessageSquare size={13} aria-hidden="true" />Open thread</button> : <span className="rem-work-pending">Thread starting…</span>}
      {status.verificationThreadId && status.verificationThreadId !== status.threadId && status.state !== "succeeded" ? <button type="button" onClick={() => openRemThread(status.verificationThreadId)} aria-label="Open review thread" title="Open review thread"><ShieldCheck size={13} aria-hidden="true" /><span>Review</span></button> : null}
    </div>
  </div>;
}

function Composer({ disabled, replying, request, offline }) {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef(null);
  useEffect(() => {
    if (!request) return;
    setDraft(request.text);
    textareaRef.current?.focus();
  }, [request]);
  useLayoutEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [draft]);
  const send = async () => {
    const text = draft.trim();
    if (!text || disabled) return;
    setDraft("");
    if (!(await sendRemMessage(text))) setDraft(current => current || text);
  };
  return <form className="rem-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
    <div className="rem-composer-box" data-disabled={disabled || undefined}>
      <textarea ref={textareaRef} rows={1} aria-label="Message Rem" placeholder={offline ? "Rem isn't connected yet" : replying ? "Rem is replying… you can keep typing" : "Message Rem"} value={draft}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
      <button type="submit" aria-label="Send" title="Send" disabled={disabled || !draft.trim()}><ArrowUp size={16} strokeWidth={2.25} /></button>
    </div>
  </form>;
}

// One task: live status above a small editor with the four task sections plus
// "Done when". Edits stay local until saved, then go to the server as a revision.
function TaskSheet({ sheet, tasks, projectOptions, now, messages, onShowMessage }) {
  const task = sheet.mode === "task" ? tasks.find(item => item.id === sheet.taskId) : null;
  const projects = useMemo(() => [...new Set([task?.project_dir, ...projectOptions].filter(Boolean))], [task?.project_dir, projectOptions]);
  const [baseline, setBaseline] = useState(() => (task ? taskToDraft(task) : newTaskDraft({ project_dir: "" })));
  const [draft, setDraft] = useState(baseline);
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const promptRef = useRef(null);
  const baselineRef = useRef(baseline);
  baselineRef.current = baseline;
  const dirtyRef = useRef(false);
  const revision = task?.revision;
  // Adopt server edits while the form is untouched; keep the person's draft otherwise.
  useEffect(() => {
    if (!task) return;
    const previous = baselineRef.current;
    const next = taskToDraft(task);
    setDraft(current => (JSON.stringify(current) === JSON.stringify(previous) ? next : current));
    setBaseline(next);
  }, [revision]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!task) promptRef.current?.focus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Escape closes an unchanged sheet; it never throws away typed edits.
  useEffect(() => {
    const key = event => { if (event.key === "Escape" && !event.defaultPrevented && !dirtyRef.current) closeTaskSheet(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  if (sheet.mode === "task" && !task) {
    return <aside className="rem-sheet" aria-label="Task">
      <header className="rem-sheet-header"><h3>Task</h3><button type="button" className="rem-sheet-close" aria-label="Close" title="Close" onClick={closeTaskSheet}><X size={16} /></button></header>
      <p className="rem-sheet-gone">This task was deleted. Its threads and results stay in your thread history.</p>
    </aside>;
  }

  const activity = task ? taskActivity(task, now) : null;
  const running = activity?.kind === "running" || activity?.kind === "queued";
  const patch = task ? draftToRequest(draft, task) : null;
  const dirty = task ? Object.keys(patch).length > 0 : Boolean(draft.prompt.trim());
  dirtyRef.current = dirty;
  const steering = running && patch && changesActiveWork(patch);
  const update = changes => { setDraft(current => ({ ...current, ...changes })); setErrors({}); };
  const updateSchedule = changes => update({ schedule: { ...draft.schedule, ...changes } });

  async function save(event) {
    event?.preventDefault();
    const found = validateDraft(draft);
    setErrors(found);
    if (Object.keys(found).length || saving) return;
    setSaving(true);
    const result = task ? await updateRemTask(task, patch) : await createRemTask(draftToRequest(draft));
    setSaving(false);
    if (!result) return;
    if (task) setBaseline(draft);
    else if (result?.id) openTaskSheet(result.id);
    else closeTaskSheet();
  }

  return <aside className="rem-sheet" aria-label={task ? `Task: ${task.title}` : "New task"}>
    <header className="rem-sheet-header">
      <span className="rem-sheet-type" data-type={draft.type}><TaskTypeIcon type={draft.type} size={16} /></span>
      <input className="rem-sheet-title" aria-label="Task name" value={draft.title} placeholder={draftTitle({ ...draft, title: "" }) || (task ? task.title : "New task")} onChange={event => update({ title: event.target.value })} />
      <button type="button" className="rem-sheet-close" aria-label="Close" title="Close" onClick={closeTaskSheet}><X size={16} /></button>
    </header>
    <div className="rem-sheet-scroll">
      {task ? <TaskStatus task={task} activity={activity} now={now} /> : null}
      {task ? <TaskUpdates messages={taskMessages(messages, task.id)} onShowMessage={onShowMessage} dirty={dirty} now={now} /> : null}
      <form id="rem-task-form" className="rem-sheet-form" onSubmit={save}>
        <Field label="What should Rem do?" error={errors.prompt} htmlFor="rem-task-prompt">
          <textarea ref={promptRef} id="rem-task-prompt" rows={4} value={draft.prompt} placeholder="Describe the outcome you want, in plain words." onChange={event => update({ prompt: event.target.value })} aria-invalid={Boolean(errors.prompt) || undefined} />
        </Field>
        <Field label="Kind of work">
          {CREATABLE_TYPES.includes(draft.type) ? <div className="rem-types" role="radiogroup" aria-label="Kind of work">
            {CREATABLE_TYPES.map(type => <button key={type} type="button" role="radio" aria-checked={draft.type === type} data-type={type} onClick={() => update({ type })}>
              <TaskTypeIcon type={type} size={16} /><span>{TASK_TYPES[type].label}</span>
            </button>)}
          </div> : <p className="rem-static-type"><TaskTypeIcon type={draft.type} size={15} />{typeLabel(draft.type)}</p>}
          <p className="rem-field-note">{TASK_TYPES[draft.type]?.blurb}</p>
        </Field>
        <Field label="Project" error={errors.project_dir} htmlFor="rem-task-project">
          <select id="rem-task-project" value={draft.project_dir} onChange={event => update({ project_dir: event.target.value })} aria-invalid={Boolean(errors.project_dir) || undefined}>
            <option value="">{draft.type === "research" ? "No project · save to Second Brain" : "Choose a project"}</option>
            {projects.map(path => <option key={path} value={path} title={path}>{projectLabel(path)}</option>)}
          </select>
        </Field>
        <Field label="When" error={errors.schedule}>
          {scheduleIsEditable(draft.schedule) ? <ScheduleFields schedule={draft.schedule} onChange={updateSchedule} isNew={!task} /> : <p className="rem-field-note">{describeSchedule(draft.schedule)}. Ask Rem to change this schedule.</p>}
        </Field>
        <Field label="Done when">
          <Criteria criteria={draft.criteria} onChange={criteria => update({ criteria })} />
        </Field>
      </form>
    </div>
    <footer className="rem-sheet-footer">
      {confirmDelete ? <div className="rem-sheet-confirm" role="alertdialog" aria-label="Delete task">
        <p>Delete this task?{running ? " Its current run stops." : ""} Finished threads stay in your history.</p>
        <div><button type="button" className="rem-button" data-variant="danger" autoFocus onClick={() => { setConfirmDelete(false); closeTaskSheet(); void deleteRemTask(task); }}>Delete task</button><button type="button" className="rem-button" onClick={() => setConfirmDelete(false)}>Keep</button></div>
      </div> : <>
        {steering ? <p className="rem-sheet-steer"><Sparkles size={13} aria-hidden="true" />Rem will update the running thread with these changes.</p> : null}
        <div className="rem-sheet-actions">
          <button type="submit" form="rem-task-form" className="rem-button" data-variant="primary" disabled={!dirty || saving}>{saving ? "Saving…" : task ? (steering ? "Save and update" : "Save changes") : draft.schedule.kind === "once" ? "Start task" : "Save task"}</button>
          {task && dirty ? <button type="button" className="rem-button" onClick={() => { setDraft(baseline); setErrors({}); }}>Discard</button> : null}
          <span className="rem-spacer" />
          {task ? <button type="button" className="rem-button" data-variant="quiet-danger" aria-label="Delete task" title="Delete task" onClick={() => setConfirmDelete(true)}><Trash2 size={14} aria-hidden="true" /></button> : null}
        </div>
      </>}
    </footer>
  </aside>;
}

// Every task reply stays accessible, including earlier runs and their own threads.
function TaskUpdates({ messages, onShowMessage, dirty, now }) {
  if (!messages.length) return null;
  return <details className="rem-task-updates">
    <summary>Conversation · {messages.length} {messages.length === 1 ? "update" : "updates"}</summary>
    <ol>{messages.map(message => <li key={message.id}>
      <time dateTime={message.created_at}>{formatWhen(message.created_at, now, { past: true })}</time>
      <button type="button" className="rem-update-message" disabled={dirty} title={dirty ? "Save or discard your edits to view this message" : "View in conversation"} onClick={() => onShowMessage(message.id)}>{taskRecap(message.body)}</button>
      {message.work?.thread_id ? <button type="button" className="rem-quiet-button" onClick={() => openRemThread(message.work.thread_id)}><MessageSquare size={13} aria-hidden="true" />Open thread</button> : null}
    </li>)}</ol>
  </details>;
}

function Field({ label, error, htmlFor, children }) {
  return <div className="rem-field" data-invalid={error ? "true" : undefined}>
    {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span className="rem-field-label">{label}</span>}
    {children}
    {error ? <p className="rem-field-error" role="alert">{error}</p> : null}
  </div>;
}

const SCHEDULE_KINDS = [["once", "Once"], ["daily", "Daily"], ["weekly", "Weekly"], ["cron", "Custom"]];
function ScheduleFields({ schedule, onChange, isNew }) {
  const kind = schedule.kind === "now" ? "once" : schedule.kind;
  return <div className="rem-schedule">
    <div className="rem-segments" role="radiogroup" aria-label="How often">
      {SCHEDULE_KINDS.map(([value, label]) => <button key={value} type="button" role="radio" aria-checked={kind === value} onClick={() => onChange({ kind: value })}>{label}</button>)}
    </div>
    {kind === "weekly" ? <div className="rem-weekdays" role="group" aria-label="Days">
      {WEEKDAYS.map(day => {
        const on = schedule.weekdays.includes(day);
        return <button key={day} type="button" aria-pressed={on} onClick={() => onChange({ weekdays: on ? schedule.weekdays.filter(item => item !== day) : [...schedule.weekdays, day] })}>{weekdayShort(day).slice(0, 2)}</button>;
      })}
    </div> : null}
    {kind === "daily" || kind === "weekly" ? <label className="rem-inline-field"><span>At</span><input type="time" value={schedule.local_time} onChange={event => onChange({ local_time: event.target.value })} /></label> : null}
    {kind === "cron" ? <label className="rem-inline-field"><span>Cron</span><input className="rem-mono" value={schedule.cron} placeholder="0 9 * * 1" spellCheck={false} onChange={event => onChange({ cron: event.target.value })} /></label> : null}
    <p className="rem-field-note">{kind === "once" ? (isNew ? "Starts as soon as you save." : "Runs once. Run it again any time.") : `${describeSchedule({ ...schedule, kind })}${schedule.timezone ? ` · ${schedule.timezone.replace(/_/g, " ")}` : ""}`}</p>
  </div>;
}

function Criteria({ criteria, onChange }) {
  const [next, setNext] = useState("");
  const add = () => { const text = next.trim(); if (!text) return; onChange([...criteria, { description: text, authored_by: "user" }]); setNext(""); };
  return <div className="rem-criteria">
    {criteria.length ? <ul>{criteria.map((item, index) => <li key={item.id || index}>
      <ShieldCheck size={14} aria-hidden="true" />
      <input aria-label={`Check ${index + 1}`} value={item.description} onChange={event => onChange(criteria.map((entry, at) => at === index ? { ...entry, description: event.target.value, authored_by: entry.authored_by === "rem_recommended" ? "user" : entry.authored_by } : entry))} />
      {item.authored_by === "rem_recommended" ? <span className="rem-tag" title="Rem suggested this check">Suggested</span> : item.authored_by === "project_rules" ? <span className="rem-tag" title="Required by the project's instructions">Project</span> : null}
      <button type="button" aria-label={`Remove check ${index + 1}`} title="Remove" onClick={() => onChange(criteria.filter((_, at) => at !== index))}><X size={13} /></button>
    </li>)}</ul> : <p className="rem-field-note">Leave this empty and Rem will suggest checks before it starts.</p>}
    <div className="rem-criteria-add">
      <input aria-label="Add a check" value={next} placeholder="Add a check, like “all tests pass”" onChange={event => setNext(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); add(); } }} />
      <button type="button" aria-label="Add check" title="Add check" disabled={!next.trim()} onClick={add}><Plus size={14} /></button>
    </div>
  </div>;
}

const STEPS = [["executing", "Working"], ["verifying", "Checking"], ["succeeded", "Done"]];
function stepIndex(state) { return state === "repairing" ? 1 : state === "succeeded" ? 2 : Math.max(0, STEPS.findIndex(([key]) => key === state)); }

// Live state of the task with the controls that fit that state, next to its threads.
function TaskStatus({ task, activity, now }) {
  const run = task.active_run && runState(task.active_run.state).active ? task.active_run : null;
  const last = task.last_run;
  const controls = taskActions(task, activity, { onEdit: () => {}, onDelete: () => {} }).filter(item => item.id !== "edit" && item.id !== "delete");
  if (run) {
    const current = stepIndex(run.state);
    return <section className="rem-status" aria-label="Current run" data-tone={activity.tone}>
      <div className="rem-status-line"><TaskGlyph activity={activity} type={task.type} size={16} /><strong>{activity.label}</strong>{run.started_at ? <small>started {formatWhen(run.started_at, now, { past: true })}</small> : null}</div>
      {run.state !== "queued" && run.state !== "cancelling" ? <ol className="rem-steps" aria-label="Progress">
        {STEPS.map(([key, label], index) => <li key={key} data-state={index < current ? "done" : index === current ? "current" : "next"} aria-current={index === current ? "step" : undefined}>{index === 1 && run.state === "repairing" ? "Fixing" : label}</li>)}
      </ol> : null}
      <StatusActions run={run} controls={controls} />
    </section>;
  }
  if (!last) {
    return <section className="rem-status" aria-label="Schedule" data-tone={activity.tone}>
      <div className="rem-status-line"><TaskGlyph activity={activity} type={task.type} size={16} /><strong>{activity.kind === "idle" ? "Hasn’t run yet" : activity.label}</strong></div>
      <StatusActions controls={controls} />
    </section>;
  }
  const info = runState(last.state, last.needs_user);
  const tone = activity.kind === "attention" ? info.tone : activity.kind === "paused" ? "neutral" : info.tone === "attention" ? "neutral" : info.tone;
  return <section className="rem-status" aria-label="Last run" data-tone={tone}>
    <div className="rem-status-line"><OutcomeGlyph status={{ ...info, tone, state: last.state, type: task.type }} /><strong>{last.state === "succeeded" ? taskRecap(last.summary_short || info.label) : last.summary_short || info.label}</strong>{last.finished_at ? <small>{formatWhen(last.finished_at, now, { past: true })}</small> : null}</div>
    {criteriaSummary(last.criteria) ? <p className="rem-status-note">{criteriaSummary(last.criteria)}</p> : null}
    {task.state === "paused" ? <p className="rem-status-note">Schedule paused</p> : task.next_occurrence_at ? <p className="rem-status-note">Next run {formatWhen(task.next_occurrence_at, now)}</p> : null}
    <StatusActions run={last} controls={controls} />
  </section>;
}

function StatusActions({ run, controls }) {
  return <div className="rem-status-actions">
    {run?.thread_id ? <button type="button" className="rem-status-link" onClick={() => openRemThread(run.thread_id)}><MessageSquare size={13} aria-hidden="true" />Work thread<ChevronRight size={13} aria-hidden="true" /></button> : null}
    {run?.verification_thread_id && run.verification_thread_id !== run.thread_id ? <button type="button" className="rem-status-link" onClick={() => openRemThread(run.verification_thread_id)}><ShieldCheck size={13} aria-hidden="true" />Review<ChevronRight size={13} aria-hidden="true" /></button> : null}
    <span className="rem-spacer" />
    {controls.map(item => <button key={item.id} type="button" className="rem-status-control" data-action={item.id} disabled={item.disabled} onClick={item.onSelect}><item.icon size={13} aria-hidden="true" />{item.label}</button>)}
  </div>;
}
