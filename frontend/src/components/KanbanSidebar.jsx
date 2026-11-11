import { useEffect, useRef } from "react";
import { Settings2, SquareKanban } from "lucide-react";
import { openRemThread } from "../lib/remHomeStore.js";
import { activityLine, cappedThreads, markReportSeen, modeLabel, openSelfWorkSettings, selfWorkLine, useKanban } from "../lib/remKanban.js";
import SelfWorkReport from "./SelfWorkReport.jsx";
import "./Kanban.css";

// Self-work's status, and what Rem did while the user was away.
export default function KanbanSidebar({ active, open, onOpen }) {
  const { selfWork, error, run } = useKanban(active, 5000);
  const seenRef = useRef("");
  const updated = selfWork?.report.updated_at;
  // Opening this panel with something new is how the report gets read on the desktop.
  useEffect(() => {
    if (!active || !selfWork?.report.unseen || seenRef.current === updated) return;
    seenRef.current = updated;
    run(() => markReportSeen(updated));
  }, [active, selfWork?.report.unseen, updated, run]);
  return <section id="sidebar-panel-kanban" role="tabpanel" aria-labelledby="sidebar-tab-kanban" hidden={!active} className="kb-side min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
    <div className="kb-side-head"><strong className="ui-section-title">Kanban</strong><button className="kb-open" type="button" aria-pressed={open} onClick={onOpen}><SquareKanban size={14} />Open board</button></div>
    {error ? <p role="alert" className="kb-side-error">{error}</p> : null}
    {selfWork ? <>
      <SelfWorkPanel view={selfWork} />
      <SelfWorkReport report={selfWork.report} />
    </> : <p role="status" className="kb-side-note">Loading…</p>}
  </section>;
}

// Self-work's status. Its settings are in Settings > Rem, so they work without the board.
function SelfWorkPanel({ view }) {
  const { state } = view;
  const capped = cappedThreads(view);
  return <div className="kb-card">
    <h3>Self-work <span className="kb-mode" data-mode={state.mode}>{modeLabel(state.mode)}</span></h3>
    <p className="kb-side-note">{selfWorkLine(view)}</p>
    {state.activity?.length ? <ul className="kb-activity" aria-label="Now working on">{state.activity.map(item => <li key={item.thread_id || activityLine(item)}>
      {item.thread_id ? <button type="button" onClick={() => openRemThread(item.thread_id)}>{activityLine(item)}</button> : <span>{activityLine(item)}</span>}
    </li>)}</ul> : null}
    {capped ? <p className="kb-hint">Limited to {capped} threads by Rem task workers in Settings.</p> : null}
    {state.outside_projects ? <p className="kb-hint">{state.outside_projects} {state.outside_projects === 1 ? "ticket is" : "tickets are"} outside Rem&rsquo;s projects. Assign projects under People on the board.</p> : null}
    {state.proposals_waiting ? <p className="kb-side-note">{state.proposals_waiting} {state.proposals_waiting === 1 ? "idea waits" : "ideas wait"} for your approval.</p> : null}
    <button type="button" className="kb-open kb-settings-link" onClick={openSelfWorkSettings}><Settings2 size={14} aria-hidden="true" />Self-work settings</button>
  </div>;
}
