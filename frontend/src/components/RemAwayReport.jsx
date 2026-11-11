import { boardOn, markReportSeen, openSelfWorkSettings, useKanban } from "../lib/remKanban.js";
import "./Kanban.css";

// What Rem did while the user was away, shown once at the top of Rem's chat.
export default function RemAwayReport() {
  const { selfWork, run } = useKanban(true, 8000);
  const report = selfWork?.report;
  if (!report?.unseen) return null;
  // With the board off, the full report is in Settings > Rem with self-work's settings.
  const tickets = boardOn(selfWork);
  const open = () => tickets ? window.dispatchEvent(new CustomEvent("gofer:kanban-open")) : openSelfWorkSettings();
  return <section className="rem-away" aria-label="While you were away" data-testid="rem-away-report">
    <h2>While you were away</h2>
    <p>{report.summary}</p>
    <div className="rem-away-actions">
      <button type="button" onClick={() => { open(); run(() => markReportSeen(report.updated_at)); }}>{tickets ? "Open the board" : "See the report"}</button>
      <button type="button" onClick={() => run(() => markReportSeen(report.updated_at))}>Got it</button>
    </div>
  </section>;
}
