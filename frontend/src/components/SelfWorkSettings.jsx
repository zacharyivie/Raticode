import { useEffect, useRef, useState } from "react";
import { formatWhen } from "../lib/remHome.js";
import { openRemThread } from "../lib/remHomeStore.js";
import { activityLine, autoApproveHint, autoApproveWarning, autoPullHint, boardOn, cancelSelfWorkWindow, cappedThreads, hasAutoApprove, modeLabel, remProjectChoices, saveParticipant, saveSelfWork, savedSwitchNote, selfWorkLimitsLine, selfWorkLine, selfWorkWindows, useKanban, windowDetail, withProject } from "../lib/remKanban.js";
import { NumberControl, SwitchControl } from "./SettingControls.jsx";
import SelfWorkReport from "./SelfWorkReport.jsx";
import "./Kanban.css";

// Settings > Rem: self-work's switch, thread limit, backlog pulling and Rem's projects. They
// live in Rem's backend, so they apply on every device and don't need the Kanban board.
export default function SelfWorkSettings() {
  const { board, selfWork: view, error, run } = useKanban(true, 5000);
  if (!view) {
    return <section id="settings-self-work" aria-label="Self-work" className="space-y-2 pb-3">
      <h3 className="text-xs font-semibold">Self-work</h3>
      {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : <p role="status" className="text-xs text-muted">Loading…</p>}
    </section>;
  }
  const { settings, state, limits } = view;
  const tickets = boardOn(view);
  const capped = cappedThreads(view);
  const save = change => run(() => saveSelfWork({ ...change, expected_revision: settings.revision }));
  const rem = board?.participants?.find(person => person.kind === "rem") || null;
  const choices = remProjectChoices(board);
  const windows = selfWorkWindows(view);
  const switchNote = savedSwitchNote(view);
  return <section id="settings-self-work" aria-label="Self-work" className="space-y-3 pb-3" data-testid="self-work-settings">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold">Self-work</h3>
      <span className="kb-mode text-xs" data-mode={state.mode}>{modeLabel(state.mode)}</span>
    </div>
    <p className="text-xs text-muted" data-testid="self-work-line">{selfWorkLine(view)}</p>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Work on my own after {settings.idle_minutes} quiet minutes</p>
        <p className="text-[11px] text-muted">{tickets ? "Rem works through its To do tickets first, then the backlog or research, within its projects." : "The Kanban board is off, so Rem leaves tickets alone. It researches its projects and lists the ideas in the report below."}</p>
        <p className="text-[11px] text-muted" data-testid="self-work-priority-hint">Work you ask for goes first: self-work pauses to make room and picks up again later. You can also tell Rem &ldquo;turn on self-work&rdquo;, &ldquo;stop self-work&rdquo;{windows ? <> or &ldquo;self-work from 11 PM to 7 AM&rdquo;</> : null}.</p>
        {switchNote ? <p className="text-[11px] text-ink" data-testid="self-work-switch-note">{switchNote}</p> : null}
      </div>
      <SwitchControl label="Self-work" checked={settings.enabled} onChange={value => save({ enabled: value })} />
    </div>
    {windows ? <SelfWorkWindows windows={windows} onCancel={id => run(() => cancelSelfWorkWindow(id))} /> : null}
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Max threads</p>
        <p className="text-[11px] text-muted">{capped ? `Limited to ${capped} by Rem task workers below.` : "How many tasks Rem keeps going at once while you're away."}</p>
      </div>
      <div className="w-20 shrink-0"><NumberControl value={settings.max_threads} min={limits.max_threads_min} max={limits.max_threads_max} onCommit={value => { if (value !== settings.max_threads) save({ max_threads: Math.round(value) }); }} /></div>
    </div>
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Pull backlog tickets when threads are free</p>
        <p className="text-[11px] text-muted" data-testid="self-work-auto-pull-hint">{autoPullHint(view)}</p>
      </div>
      <SwitchControl label="Pull backlog tickets" checked={settings.auto_pull_backlog} disabled={!tickets} onChange={value => save({ auto_pull_backlog: value })} />
    </div>
    {hasAutoApprove(view) ? <AutoApprove view={view} disabled={!tickets} save={save} /> : null}
    {"session_token_limit" in settings ? <SelfWorkLimits settings={settings} limits={limits} reason={state.limit_reason} save={save} /> : null}
    {rem ? <fieldset className="space-y-1" data-testid="self-work-projects">
      <legend className="text-xs font-medium text-ink">Rem&rsquo;s projects</legend>
      <p className="text-[11px] text-muted">Rem works on its own only in these. Work you ask for isn&rsquo;t limited.</p>
      {choices.length ? choices.map(choice => <label key={choice.root} className="flex items-center gap-2 text-xs text-ink" title={choice.root}>
        <input type="checkbox" checked={choice.checked} disabled={!choice.checked && !choice.assignable}
          onChange={event => run(() => saveParticipant(rem, withProject(rem.projects, choice.root, event.target.checked)))} />
        <span className="min-w-0 truncate">{choice.name}</span>
        {choice.unavailable ? <span className="text-[11px] text-amber-700">Not available right now</span> : null}
      </label>) : <p className="text-[11px] text-muted">Open a project in Raticode to choose it here.</p>}
    </fieldset> : null}
    {state.activity?.length ? <ul className="kb-activity text-xs" aria-label="Now working on">{state.activity.map(item => <li key={item.thread_id || activityLine(item)}>
      {item.thread_id ? <button type="button" onClick={() => openRemThread(item.thread_id)}>{activityLine(item)}</button> : <span>{activityLine(item)}</span>}
    </li>)}</ul> : null}
    {tickets && state.outside_projects ? <p className="text-[11px] text-muted">{state.outside_projects} {state.outside_projects === 1 ? "ticket is" : "tickets are"} outside Rem&rsquo;s projects.</p> : null}
    {tickets && state.proposals_waiting ? <p className="text-[11px] text-muted">{state.proposals_waiting} {state.proposals_waiting === 1 ? "idea waits" : "ideas wait"} for your approval on the board.</p> : null}
    <div className="text-xs"><SelfWorkReport report={view.report} title="Latest report" limit={12} /></div>
  </section>;
}

// Approve its own backlog tickets: off by default and only ever turned on here, by the user,
// after reading what it allows. Turning it off needs no confirmation.
function AutoApprove({ view, disabled, save }) {
  const on = view.settings.auto_approve_proposals;
  const [asking, setAsking] = useState(false);
  const confirm = useRef(null);
  useEffect(() => { if (asking) confirm.current?.focus(); }, [asking]);
  useEffect(() => { if (on) setAsking(false); }, [on]);
  return <div className="space-y-2" data-testid="self-work-auto-approve">
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Approve its own backlog tickets</p>
        <p className="text-[11px] text-muted" data-testid="self-work-auto-approve-hint">{autoApproveHint(view)}</p>
      </div>
      <SwitchControl label="Approve its own backlog tickets" checked={on || asking} disabled={disabled}
        onChange={value => { if (value) setAsking(true); else if (asking) setAsking(false); else save({ auto_approve_proposals: false }); }} />
    </div>
    {asking ? <div role="group" aria-label="Confirm auto-approval" className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-[11px] text-ink" data-testid="self-work-auto-approve-confirm">
      <p className="font-medium">Let Rem approve tickets it proposes?</p>
      <ul className="list-disc space-y-1 pl-4">{autoApproveWarning(view).map(line => <li key={line}>{line}</li>)}</ul>
      <div className="flex gap-2">
        <button ref={confirm} type="button" className="h-7 rounded-md border border-brand bg-brand px-2 text-[11px] font-semibold text-white outline-none transition hover:opacity-90 focus-visible:ring-2 focus-visible:ring-brand/40" onClick={() => { setAsking(false); save({ auto_approve_proposals: true, confirm_auto_approve: true }); }}>Turn on</button>
        <button type="button" className="h-7 rounded-md border border-line bg-white px-2 text-[11px] font-semibold text-ink outline-none transition hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-brand/40" onClick={() => setAsking(false)}>Keep asking me</button>
      </div>
    </div> : null}
  </div>;
}

// Timed self-work: the window running now and the ones coming up, each with a button that
// ends or drops it, then what the last period did. Rem sets windows from the user's words.
function SelfWorkWindows({ windows, onCancel }) {
  const { active, upcoming, last } = windows;
  if (!active && !upcoming.length && !last) return null;
  const row = (item, current) => <li key={item.id} className="flex items-start gap-2" data-window={current ? "active" : "upcoming"}>
    <div className="min-w-0 flex-1">
      <p className="text-xs text-ink">{current ? <span className="font-medium">Now: </span> : null}{item.label}</p>
      {windowDetail(item) ? <p className="text-[11px] text-muted">{windowDetail(item)}</p> : null}
    </div>
    <button type="button" className="kb-open shrink-0 text-[11px]" title={current ? "Self-work stops now and Rem posts what it did" : "Drop this window"} onClick={() => onCancel(item.id)}>{current ? "End now" : "Cancel"}</button>
  </li>;
  const ended = last ? formatWhen(last.ended_at, new Date(), { past: true }) : "";
  return <div className="space-y-2" data-testid="self-work-windows">
    {active || upcoming.length ? <ul className="space-y-2" aria-label="Self-work windows">
      {active ? row(active, true) : null}
      {upcoming.map(item => row(item, false))}
    </ul> : null}
    {last ? <div data-testid="self-work-last-period">
      <p className="text-xs font-medium text-ink">Last self-work period{ended ? <span className="font-normal text-muted"> · ended {ended}</span> : null}</p>
      <p className="text-[11px] text-muted">{last.summary}</p>
    </div> : null}
  </div>;
}

// Limits on what self-work spends: tokens per session and per day, and how full a provider's
// rate-limit window may be. Past one, Rem starts nothing new; work already running finishes.
function SelfWorkLimits({ settings, limits, reason, save }) {
  const tokenMax = limits.token_limit_max || 1e12;
  const row = (key, label, hint, max, suffix) => <div className="flex items-start gap-3" key={key}>
    <div className="min-w-0 flex-1">
      <p className="text-xs font-medium text-ink">{label}</p>
      <p className="text-[11px] text-muted">{hint}</p>
    </div>
    <div className="w-28 shrink-0"><NumberControl value={settings[key]} min={0} max={max} suffix={suffix} onCommit={value => { const next = Math.round(value); if (next !== settings[key]) save({ [key]: next }); }} /></div>
  </div>;
  return <fieldset className="space-y-2" data-testid="self-work-limits">
    <legend className="text-xs font-medium text-ink">Limits</legend>
    <p className="text-[11px] text-muted" data-testid="self-work-limits-line">{selfWorkLimitsLine(settings)}</p>
    {reason ? <p role="status" className="text-[11px] text-amber-700" data-testid="self-work-limit-reason">{reason}</p> : null}
    {row("session_token_limit", "Tokens per session", "From when self-work starts until you\u2019re back. 0 means no limit.", tokenMax, "")}
    {row("daily_token_limit", "Tokens per day", "Self-work's tokens since midnight on this computer. 0 means no limit.", tokenMax, "")}
    {row("rate_limit_percent", "Rate limit use", "Stops when a stored rate-limit window of Rem\u2019s task provider is this full. 0 means no limit.", limits.rate_limit_percent_max || 100, "%")}
    <p className="text-[11px] text-muted">Token limits count what providers report. Calls a provider doesn&rsquo;t report don&rsquo;t count toward them.</p>
  </fieldset>;
}
