import { useEffect, useRef, useState } from "react";
import { formatWhen } from "../lib/remHome.js";
import { openRemThread } from "../lib/remHomeStore.js";
import { ALLOWANCE_MODES, activityLine, allowanceMode, allowancePaceLine, autoApproveHint, dailyAllowanceLine, hasAllowanceModes, paceRateWarning, autoApproveWarning, autoPullHint, boardOn, cancelSelfWorkWindow, cappedThreads, hasAutoApprove, modeLabel, remProjectChoices, saveParticipant, saveSelfWork, savedSwitchNote, saveSelfWorkStop, selfWorkAccountChange, selfWorkAccountRows, selfWorkLimitsLine, selfWorkLine, selfWorkStop, selfWorkWindows, lastStopLine, stopJudgmentLine, stopPayload, useKanban, windowDetail, withProject } from "../lib/remKanban.js";
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
    {selfWorkStop(view) ? <SelfWorkStop stop={selfWorkStop(view)} on={settings.enabled} send={change => run(() => saveSelfWorkStop(change))} /> : null}
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Max threads</p>
        <p className="text-[11px] text-muted">{capped ? `Limited to ${capped} by the Worker limit in Settings › Computers.` : "How many tasks Rem keeps going at once while you're away."}</p>
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
    <SelfWorkAccounts view={view} save={save} />
    {"session_token_limit" in settings ? <SelfWorkLimits settings={settings} limits={limits} reason={state.limit_reason} today={state.daily_allowance} paced={state.allowance_pace} save={save} /> : null}
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

// Which account the runs self-work starts use, per provider with accounts. Chat, threads and
// tasks you create keep following Rem's setting in Settings > Rem > Accounts.
function SelfWorkAccounts({ view, save }) {
  const rows = selfWorkAccountRows(view);
  if (!rows.length) return null;
  return <fieldset className="space-y-2" data-testid="self-work-accounts">
    <legend className="text-xs font-medium text-ink">Accounts</legend>
    <p className="text-[11px] text-muted">The account self-work runs on. A task with its own account keeps it. Chat, threads and tasks you create use Rem&rsquo;s setting.</p>
    {rows.map(row => <div key={row.provider} className="flex items-center gap-3">
      <label htmlFor={`self-work-account-${row.provider}`} className="min-w-0 flex-1 text-xs text-ink">{row.label}</label>
      <select id={`self-work-account-${row.provider}`} data-provider={row.provider}
        className="h-8 w-48 shrink-0 rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition focus:border-brand"
        value={row.choice} onChange={event => { if (event.target.value !== row.choice) save(selfWorkAccountChange(row.provider, event.target.value)); }}>
        {row.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </div>)}
  </fieldset>;
}

// When self-work stops: the user's condition ("done when") and a latest time, Rem's last
// check of the condition, and Edit and Clear. Rem judges the condition after each self-work
// task finishes; the time stops self-work whatever the condition says.
function SelfWorkStop({ stop, on, send }) {
  const [editing, setEditing] = useState(false);
  const [condition, setCondition] = useState("");
  const [time, setTime] = useState("");
  const [day, setDay] = useState("");
  const keepTime = Boolean(stop.active && stop.no_later_than);
  const open = () => { setCondition(stop.active ? stop.condition : ""); setTime(""); setDay(""); setEditing(true); };
  const save = event => {
    event.preventDefault();
    send(stopPayload(stop, { condition, time, day, keepTime }));
    setEditing(false);
  };
  const last = lastStopLine(stop.last);
  if (!stop.active && !on && !last) return null;
  return <div className="space-y-2" data-testid="self-work-stop">
    <p className="text-xs font-medium text-ink">When to stop</p>
    {stop.active ? <div>
      <p className="text-xs text-ink" data-testid="self-work-stop-label">{stop.label}</p>
      {stopJudgmentLine(stop) ? <p className="text-[11px] text-muted" role="status" data-testid="self-work-stop-judgment">{stopJudgmentLine(stop)}</p> : null}
    </div> : <>
      {last ? <p className="text-[11px] text-muted" data-testid="self-work-last-stop">{last}</p> : null}
      {on ? <p className="text-[11px] text-muted">Stop self-work when something is done, or by a time at the latest. You can also tell Rem &ldquo;stop when REM-12 is done, but no later than midnight&rdquo;.</p> : null}
    </>}
    {editing ? <form className="space-y-2" onSubmit={save} aria-label="When to stop self-work">
      <label className="block text-[11px] text-muted">Stop when
        <textarea className="mt-1 w-full rounded border border-line bg-transparent p-1 text-xs text-ink" rows={2} maxLength={500} value={condition}
          placeholder="REM-205 to REM-212 are done" onChange={event => setCondition(event.target.value)} data-testid="self-work-stop-condition" />
      </label>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-[11px] text-muted">No later than
          <input type="time" className="ml-1 rounded border border-line bg-transparent px-1 text-xs text-ink" value={time}
            onChange={event => setTime(event.target.value)} data-testid="self-work-stop-time" />
        </label>
        <select className="rounded border border-line bg-transparent px-1 text-xs text-ink" aria-label="Which day" value={day}
          onChange={event => setDay(event.target.value)} data-testid="self-work-stop-day">
          <option value="">Next time it comes round</option>
          <option value="today">Today</option>
          <option value="tomorrow">Tomorrow</option>
        </select>
      </div>
      {keepTime ? <p className="text-[11px] text-muted">Now {stop.no_later_than_label}. Leave the time empty to keep it.</p> : null}
      <div className="flex gap-2">
        <button type="submit" className="kb-open text-[11px]" disabled={!condition.trim() && !time && !keepTime} data-testid="self-work-stop-save">Save</button>
        <button type="button" className="kb-open text-[11px]" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </form> : <div className="flex gap-2">
      {stop.active || on ? <button type="button" className="kb-open text-[11px]" onClick={open} data-testid="self-work-stop-edit">{stop.active ? "Edit" : "Set when to stop"}</button> : null}
      {stop.active ? <button type="button" className="kb-open text-[11px]" title="Self-work runs until you turn it off or its window ends"
        onClick={() => send({ clear: "both", expected_revision: stop.revision })} data-testid="self-work-stop-clear">Clear</button> : null}
    </div>}
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
function SelfWorkLimits({ settings, limits, reason, today, paced, save }) {
  const tokenMax = limits.token_limit_max || 1e12;
  const row = (key, label, hint, max, suffix, min = 0) => <div className="flex items-start gap-3" key={key}>
    <div className="min-w-0 flex-1">
      <p className="text-xs font-medium text-ink">{label}</p>
      <p className="text-[11px] text-muted">{hint}</p>
    </div>
    <div className="w-28 shrink-0"><NumberControl value={settings[key]} min={min} max={max} suffix={suffix} onCommit={value => { const next = Math.round(value); if (next !== settings[key]) save({ [key]: next }); }} /></div>
  </div>;
  return <fieldset className="space-y-2" data-testid="self-work-limits">
    <legend className="text-xs font-medium text-ink">Limits</legend>
    <p className="text-[11px] text-muted" data-testid="self-work-limits-line">{selfWorkLimitsLine(settings)}</p>
    {reason ? <p role="status" className="text-[11px] text-amber-700" data-testid="self-work-limit-reason">{reason}</p> : null}
    {row("session_token_limit", "Tokens per session", "From when self-work starts until you\u2019re back. 0 means no limit.", tokenMax, "")}
    {row("daily_token_limit", "Tokens per day", "Self-work's tokens since midnight on this computer. 0 means no limit.", tokenMax, "")}
    {row("rate_limit_percent", "Rate limit use", "Stops when a stored rate-limit window of the account self-work would run on is this full. 0 means no limit.", limits.rate_limit_percent_max || 100, "%")}
    {hasAllowanceModes(settings) ? <AllowanceUse settings={settings} limits={limits} today={today} paced={paced} row={row} save={save} /> : "daily_allowance_percent" in settings ? <>
      {row("daily_allowance_percent", "Weekly allowance per day", "How much of a weekly allowance self-work may use each day, from midnight, counted for each account it runs on. 0 means no cap.", limits.daily_allowance_percent_max || 100, "%")}
      {dailyAllowanceLine(today) ? <p role="status" className={`text-[11px] ${today?.cap_percent && !today?.available ? "text-amber-700" : "text-muted"}`} data-testid="self-work-daily-allowance">{dailyAllowanceLine(today)}</p> : null}
      {settings.daily_allowance_percent ? <p className="text-[11px] text-muted">Providers report one percent for a whole account, so this counts how much it rose while self-work was running on it. Your own use of the same account at the same time counts too.</p> : null}
    </> : null}
    <p className="text-[11px] text-muted">Token limits count what providers report. Calls a provider doesn&rsquo;t report don&rsquo;t count toward them.</p>
  </fieldset>;
}

// Allowance use: Off, Daily cap or Pace to reset. One choice, because a daily cap under a pace
// line would hold self-work below the line every day.
function AllowanceUse({ settings, limits, today, paced, row, save }) {
  const mode = allowanceMode(settings);
  const status = mode === "pace" ? allowancePaceLine(paced) : dailyAllowanceLine(today);
  const holding = mode === "pace" ? Boolean(paced?.starts === 0) : Boolean(today?.cap_percent && !today?.available);
  const warning = paceRateWarning(settings);
  return <div className="space-y-2" data-testid="self-work-allowance-use">
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Allowance use</p>
        <p className="text-[11px] text-muted">{mode === "pace"
          ? "Starts new work only while the account is under a line that reaches the aim shortly before its weekly reset. Running work and your own work are never held."
          : mode === "daily" ? "Caps how much of a weekly allowance self-work may use each day."
            : "Self-work uses the allowance whenever threads are free."}</p>
      </div>
      <div role="radiogroup" aria-label="Allowance use" className="inline-flex shrink-0 overflow-hidden rounded-md border border-line">
        {ALLOWANCE_MODES.map(([value, label]) => <button key={value} type="button" role="radio" aria-checked={mode === value} data-mode={value}
          className={`h-7 px-2 text-[11px] font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-brand/40 ${mode === value ? "bg-brand text-white" : "bg-white text-muted hover:text-ink"}`}
          onClick={() => { if (value !== mode) save({ allowance_mode: value }); }}>{label}</button>)}
      </div>
    </div>
    {mode === "daily" ? row("daily_allowance_percent", "Weekly allowance per day", "How much of a weekly allowance self-work may use each day, from midnight, counted for each account it runs on.", limits.daily_allowance_percent_max || 100, "%", 1) : null}
    {mode === "pace" ? <>
      {row("pace_aim_percent", "Aim by reset", "How much of the allowance to have used when it resets. The rest is left for you.", limits.pace_aim_percent_max || 100, "%", limits.pace_aim_percent_min || 1)}
      {row("pace_lead_hours", "Finish before reset", "Reach the aim this many hours before the reset, so work started late spends the new week.", limits.pace_lead_hours_max || 24, "h")}
    </> : null}
    {status ? <p role="status" className={`text-[11px] ${holding ? "text-amber-700" : "text-muted"}`} data-testid={mode === "pace" ? "self-work-allowance-pace" : "self-work-daily-allowance"}>{status}</p> : null}
    {warning ? <p className="text-[11px] text-amber-700" data-testid="self-work-pace-rate-warning">{warning}</p> : null}
    {mode === "daily" ? <p className="text-[11px] text-muted">Providers report one percent for a whole account, so this counts how much it rose while self-work was running on it. Your own use of the same account at the same time counts too.</p> : null}
    {mode === "pace" ? <p className="text-[11px] text-muted">Reads only the percent and reset time the provider reports, for each account self-work runs on. Your own use counts too: self-work simply waits longer.</p> : null}
  </div>;
}
