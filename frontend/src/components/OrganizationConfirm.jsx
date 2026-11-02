import { useState } from "react";

// Two-step button for consequential actions: the first click states the effect.
export default function ConfirmButton({ label, confirmLabel, message, onConfirm, busy = false, danger = false, className = "org-button", icon = null }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className={className} disabled={busy} onClick={() => setAsking(true)}>{icon}{label}</button>;
  return <span className="org-confirm" role="group" aria-label={`Confirm: ${label}`}>
    <span role="status">{message}</span>
    <button type="button" className={`org-button ${danger ? "org-danger" : "org-primary"}`} disabled={busy} onClick={async () => { setAsking(false); await onConfirm(); }}>{confirmLabel}</button>
    <button type="button" className="org-button" onClick={() => setAsking(false)}>Keep as is</button>
  </span>;
}
