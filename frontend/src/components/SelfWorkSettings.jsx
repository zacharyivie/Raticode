import { openRemThread } from "../lib/remHomeStore.js";
import { activityLine, autoPullHint, boardOn, cappedThreads, modeLabel, remProjectChoices, saveParticipant, saveSelfWork, selfWorkLine, useKanban, withProject } from "../lib/remKanban.js";
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
      </div>
      <SwitchControl label="Self-work" checked={settings.enabled} onChange={value => save({ enabled: value })} />
    </div>
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
