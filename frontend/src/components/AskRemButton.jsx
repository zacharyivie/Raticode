import { useState } from "react";
import RemActionIcon from "./RemActionIcon.jsx";

// The small "Ask Rem" button on failed runs, timeline rows and Problems entries. The
// full action ("Ask Rem about this failure") is its accessible name and tooltip.
// `onAsk` may return a promise: the button stays busy until it settles, and a failure
// shows its message under the button's tooltip instead of being lost.
export default function AskRemButton({ label = "Ask Rem about this failure", onAsk, className = "" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function ask(event) {
    event.stopPropagation();
    if (busy) return;
    setBusy(true);
    setError("");
    try { await onAsk?.(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not ask Rem."); }
    finally { setBusy(false); }
  }
  return <><button
    type="button"
    aria-label={label}
    aria-busy={busy || undefined}
    title={error ? `${label}. ${error}` : `${label}: attach it to Rem's message box. Nothing is sent until you send it.`}
    data-testid="ask-rem-failure"
    className={`rem-action-button inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 font-sans text-[11px] font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-50 ${error ? "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300" : "border-line text-ink hover:border-brand/50 hover:text-brand"} ${className}`}
    disabled={busy}
    onClick={event => void ask(event)}
    onKeyDown={event => event.stopPropagation()}
  >
    <RemActionIcon size={13} />
    <span>{busy ? "Preparing…" : error ? "Try again" : "Ask Rem"}</span>
  </button>{error ? <span role="alert" className="sr-only">{error}</span> : null}</>;
}
