import { useEffect, useState } from "react";
import { fetchAllWorkflowRuns, openWorkflowRuns } from "../lib/allWorkflowRuns.js";
import { boardOn, markReportSeen, openSelfWorkSettings, useKanban } from "../lib/remKanban.js";
import "./Kanban.css";

// The workflow runs since `since`, as one line such as "6 workflow runs: 5 passed,
// 1 failed · est. $0.42", or null when none ran or the list can't be read.
export function useAwayWorkflowRuns(since, refreshKey, load = fetchAllWorkflowRuns) {
  const [totals, setTotals] = useState(null);
  useEffect(() => {
    if (!since) { setTotals(null); return undefined; }
    let current = true;
    load({ since, limit: 1 }).then(
      payload => { if (current) setTotals(payload?.totals?.runs ? payload.totals : null); },
      () => { if (current) setTotals(null); },
    );
    return () => { current = false; };
  }, [since, refreshKey, load]);
  return totals;
}

// What Rem did while the user was away, shown once at the top of Rem's chat.
export default function RemAwayReport({ loadRuns = fetchAllWorkflowRuns }) {
  const { selfWork, run } = useKanban(true, 8000);
  const report = selfWork?.report;
  const since = report?.unseen ? report.since : "";
  const workflows = useAwayWorkflowRuns(since, report?.updated_at, loadRuns);
  if (!report?.unseen) return null;
  // With the board off, the full report is in Settings > Rem with self-work's settings.
  const tickets = boardOn(selfWork);
  const open = () => tickets ? window.dispatchEvent(new CustomEvent("gofer:kanban-open")) : openSelfWorkSettings();
  return <section className="rem-away" aria-label="While you were away" data-testid="rem-away-report">
    <h2>While you were away</h2>
    <p>{report.summary}</p>
    {workflows ? <p className="rem-away-runs"><button type="button" title="Open these runs in Workflow runs" onClick={() => openWorkflowRuns({ since })}>{workflows.summary}</button></p> : null}
    <div className="rem-away-actions">
      <button type="button" onClick={() => { open(); run(() => markReportSeen(report.updated_at)); }}>{tickets ? "Open the board" : "See the report"}</button>
      <button type="button" onClick={() => run(() => markReportSeen(report.updated_at))}>Got it</button>
    </div>
  </section>;
}
