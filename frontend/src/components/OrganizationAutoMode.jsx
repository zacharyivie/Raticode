import { useEffect, useState } from "react";
import { AlertTriangle, Bot } from "lucide-react";
import { AUTO_DECISION_LABEL, AUTO_MODE_EXPLAINED, AUTO_MODE_STAYS_YOURS, autoDecisions, plural, reviewingRequests } from "../lib/organizations.js";
import "./OrganizationAutoMode.css";

// The Auto badge beside an organization's name.
export function AutoBadge({ organization }) {
  if (!organization?.config?.autoMode) return null;
  const halted = Boolean(organization.runtime?.autoHalt);
  return <span className={`org-auto-badge${halted ? " halted" : ""}`} title={halted ? "Auto mode stopped the organization; it needs you" : "Auto mode: Rem decides this organization's requests"}>
    <Bot size={12} aria-hidden="true" />{halted ? "Auto · stopped" : "Auto"}</span>;
}

// Why Auto mode stopped the organization and what the user must do. The request it stopped
// for waits in Needs you; answering it, or starting the organization, lifts the stop.
export function AutoHalt({ organization }) {
  const halt = organization?.runtime?.autoHalt;
  if (!halt) return null;
  return <div className="org-auto-halt" role="alert">
    <AlertTriangle size={16} aria-hidden="true" />
    <span><strong>Auto mode stopped the organization{halt.summary ? `: “${halt.summary}”` : ""}.</strong>{" "}
      {halt.kind === "impossible" ? "It can't go on without you." : "It's dangerous, and Rem couldn't make it safe without stopping everything."}{" "}
      {halt.reason}<br /><b>To continue:</b> {halt.userAction} Then approve or decline the request, or start the organization again.</span>
  </div>;
}

// Auto mode on the organization page and in settings: the switch, with a confirmation that
// says what it allows, the stop reason, and Rem's recent decisions with their reasons.
export default function AutoMode({ organization, busy, onAct, className = "oh-panel" }) {
  const [confirming, setConfirming] = useState(false);
  const on = Boolean(organization?.config?.autoMode);
  useEffect(() => {
    if (!confirming) return undefined;
    const close = event => { if (event.key === "Escape") setConfirming(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [confirming]);
  if (!organization) return null;
  const runtime = organization.runtime || {};
  const decisions = autoDecisions(runtime);
  const reviewing = reviewingRequests(runtime);
  const turn = enabled => onAct("auto_mode", { enabled, confirm: enabled, expectedRevision: organization.revision },
    enabled ? "Auto mode is on. Rem decides this organization's requests." : "Auto mode is off. Requests wait for you again.");
  return <section className={`${className} org-auto`} aria-labelledby="org-auto-h">
    <div className="oh-section-head"><h2 id="org-auto-h">Auto mode</h2><span className="oh-muted">{on ? "On" : "Off"}</span><span className="grow" />
      {on ? <button className="oh-btn small ghost" type="button" disabled={busy} onClick={() => turn(false)}>Turn off</button>
        : !confirming && <button className="oh-btn small primary" type="button" disabled={busy} onClick={() => setConfirming(true)}>Turn on…</button>}</div>
    {confirming && !on && <div className="org-auto-confirm" role="alertdialog" aria-labelledby="org-auto-confirm-h" aria-describedby="org-auto-confirm-d">
      <b id="org-auto-confirm-h">Turn on Auto mode for {organization.config.name}?</b>
      <p id="org-auto-confirm-d">{AUTO_MODE_EXPLAINED}</p>
      <p>{AUTO_MODE_STAYS_YOURS} You can turn it off at any time.</p>
      <div className="org-auto-row"><button className="oh-btn small primary" type="button" disabled={busy} onClick={() => { setConfirming(false); turn(true); }}>Turn on Auto mode</button>
        <button className="oh-btn small" type="button" onClick={() => setConfirming(false)}>Cancel</button></div>
    </div>}
    <AutoHalt organization={organization} />
    <p className="oh-muted" style={{ margin: 0 }}>{on
      ? `Rem decides requests here instead of you${reviewing.length ? `; reviewing ${plural(reviewing.length, "request")} now` : ""}. It stops only for danger it can't make safe, or for something only you can do.`
      : "Requests wait for you to approve or decline them."}</p>
    {decisions.length > 0 && <ul className="org-auto-decisions" aria-label="Rem's recent decisions">
      {decisions.map(item => <li key={`${item.requestId}-${item.at}`} className={`d-${item.decision}`}>
        <span className="org-auto-label">{AUTO_DECISION_LABEL[item.decision] || item.decision}{item.error ? " · didn't apply" : ""}</span>
        <span><b>{item.summary || "A request"}</b>{item.reason && <> · {item.reason}</>}{item.recommendation && <> <i>Recommended instead: {item.recommendation}</i></>}{item.error && <> <small className="oh-faint">{item.error}</small></>}</span>
      </li>)}
    </ul>}
  </section>;
}
