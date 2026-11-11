import { useState } from "react";
import { Check, Circle, CircleHelp, X } from "lucide-react";
import { checkGroups } from "../lib/remHome.js";

// Reasons longer than this, or over three lines, start folded.
const LONG_REASON = 240;

const OUTCOMES = {
  failed: { icon: X, heading: "Failed" },
  unresolved: { icon: CircleHelp, heading: "Unresolved" },
  unchecked: { icon: Circle, heading: "Not checked" },
  passed: { icon: Check, heading: "Passed" },
};

function Reason({ text }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const long = text.length > LONG_REASON || text.split("\n").length > 3;
  return <>
    <p className="rem-check-reason" data-folded={long && !open ? "true" : undefined}>{text}</p>
    {long ? <button type="button" className="rem-check-more" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "Show less" : "Show more"}</button> : null}
  </>;
}

function CheckList({ items, outcome }) {
  const Icon = OUTCOMES[outcome].icon;
  return <ul className="rem-check-list">
    {items.map(item => <li key={item.key} className="rem-check" data-outcome={outcome}>
      <Icon size={14} className="rem-check-icon" aria-hidden="true" />
      <div className="rem-check-body">
        <span className="rem-check-title">{item.title}</span>
        <Reason text={item.reason || (outcome === "failed" ? "The review gave no reason." : "")} />
      </div>
    </li>)}
  </ul>;
}

// Which checks of one run failed or stayed unresolved, each with the reviewer's
// reason, apart from the run's one-line summary. Passed checks fold away below.
// Nothing shows before a review has looked at any check: the count line above
// already says the checks haven't run.
export default function TaskChecks({ criteria, label = "Checks" }) {
  const groups = checkGroups(criteria);
  if (!groups.reviewed) return null;
  const open = ["failed", "unresolved", "unchecked"].filter(outcome => groups[outcome].length);
  return <section className="rem-checks" aria-label={label}>
    {open.map(outcome => <div key={outcome} className="rem-check-group" data-outcome={outcome}>
      <h4>{OUTCOMES[outcome].heading} · {groups[outcome].length}</h4>
      <CheckList items={groups[outcome]} outcome={outcome} />
    </div>)}
    {groups.passed.length ? <details className="rem-check-group" data-outcome="passed">
      <summary>{OUTCOMES.passed.heading} · {groups.passed.length}</summary>
      <CheckList items={groups.passed} outcome="passed" />
    </details> : null}
  </section>;
}
