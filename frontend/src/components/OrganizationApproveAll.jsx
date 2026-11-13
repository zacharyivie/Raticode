import { useEffect, useRef, useState } from "react";
import { Check, CheckCheck, Clock, ShieldCheck, X } from "lucide-react";
import { APPROVE_RESULT, approvalOrder, approveAllSummary, plural } from "../lib/organizations.js";
import "./OrganizationApproveAll.css";

const ICONS = { approved: Check, failed: X, skipped: Clock };

// Approve all of Rem's waiting requests. The first click only shows the exact list; the
// second approves those requests and nothing that arrived after the list opened.
export default function ApproveAllRequests({ requests, busy, onApprove, buttonClass = "oh-btn small", primaryClass = "oh-btn small primary" }) {
  const [list, setList] = useState(null);
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);
  const panel = useRef(null);
  useEffect(() => { if (list) panel.current?.focus(); }, [list]);
  if (!list && !results && requests.length < 2) return null;
  const listed = new Set((list || []).map(request => request.id));
  const newer = list ? requests.filter(request => !listed.has(request.id)).length : 0;

  async function approve() {
    setRunning(true);
    try {
      const data = await onApprove(list.map(request => request.id));
      // On an error the list stays open; the error shows above it.
      if (data?.results) { setResults(data.results); setList(null); }
    } finally { setRunning(false); }
  }

  if (results) {
    const summaries = Object.fromEntries((list || []).map(request => [request.id, request.summary]));
    return <div className="org-approve-all" role="status" aria-label="Approve all results">
      <p className="org-approve-all-head"><CheckCheck size={15} aria-hidden="true" /><strong>{approveAllSummary(results)}</strong></p>
      <ul className="org-approve-all-list">{results.map(item => {
        const Icon = ICONS[item.status] || Clock;
        return <li key={item.requestId} data-status={item.status}>
          <span className="org-approve-all-state"><Icon size={13} aria-hidden="true" />{APPROVE_RESULT[item.status] || item.status}</span>
          <span><span className="org-approve-all-summary">{item.summary || summaries[item.requestId] || "A request from Rem"}</span>{item.message && <small>{item.message}</small>}</span>
        </li>;
      })}</ul>
      <div className="org-approve-all-actions"><button className={buttonClass} type="button" onClick={() => setResults(null)}>Done</button></div>
    </div>;
  }

  if (!list) return <div className="org-approve-all-bar">
    <span>{plural(requests.length, "request")} from Rem {requests.length === 1 ? "is" : "are"} waiting.</span>
    <button className={primaryClass} type="button" disabled={busy} onClick={() => setList(approvalOrder(requests))}><CheckCheck size={14} aria-hidden="true" />Approve all {requests.length}</button>
  </div>;

  return <div className="org-approve-all" role="group" aria-labelledby="org-approve-all-title" tabIndex={-1} ref={panel}
    onKeyDown={event => { if (event.key === "Escape" && !running) { event.preventDefault(); event.stopPropagation(); setList(null); } }}>
    <p className="org-approve-all-head" id="org-approve-all-title"><ShieldCheck size={15} aria-hidden="true" /><strong>Approve these {plural(list.length, "request")} from Rem?</strong></p>
    <ol className="org-approve-all-list">{list.map(request => <li key={request.id}>
      <span><span className="org-approve-all-summary">{request.summary}</span><small>Asked {new Date(request.at).toLocaleString()}</small></span>
    </li>)}</ol>
    <p className="org-approve-all-note">They apply in this order: changes first, starting the organization last. A change made for an older version of the organization is left waiting instead of undoing newer changes. Requests that arrive after you opened this list aren&apos;t included.</p>
    {newer > 0 && <p className="org-approve-all-note" role="status">{plural(newer, "newer request")} arrived and {newer === 1 ? "isn't" : "aren't"} in this list.</p>}
    <div className="org-approve-all-actions">
      <button className={primaryClass} type="button" disabled={busy || running} onClick={approve}>{running ? "Approving…" : `Approve these ${list.length}`}</button>
      <button className={buttonClass} type="button" disabled={running} onClick={() => setList(null)}>Cancel</button>
    </div>
  </div>;
}
