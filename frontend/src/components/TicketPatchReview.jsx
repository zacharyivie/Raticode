import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, GitBranch, GitMerge, MessageSquare, RotateCcw } from "lucide-react";
import CommitDiff from "./CommitDiff.jsx";
import { landTicket, readPatch, requestChanges } from "../lib/remKanban.js";
import { acceptLabel, commentKey, landedMessage, mergeStatus, patchSize } from "../lib/ticketReview.js";
import "./TicketPatchReview.css";

const themeNow = () => (globalThis.document?.documentElement?.classList?.contains?.("dark") ? "dark" : "light");
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// A ticket's full patch in its sheet, with the user's decision on it: Accept lands the
// branch in its base after a clean-merge check; line comments and a note go back to the
// same worker as a request for changes; a conflict offers asking the worker to rebase.
// Every request here is the user's own click; Rem and self-work can't land anything.
export default function TicketPatchReview({ ticket, worker = "the worker", decide = true, caution = "", expanded = false, onExpand, onUpdated }) {
  const [patch, setPatch] = useState(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [comments, setComments] = useState([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const address = { ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id, revision: ticket.revision };

  useEffect(() => {
    let cancelled = false;
    setError("");
    readPatch(address).then(data => { if (!cancelled) setPatch(data); }, failure => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
    // Reread when the ticket changes, so Accept always names what's on the branch now.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket.ticket_id, ticket.scope, ticket.organization_id, ticket.revision, reload]);

  const addComment = useCallback(item => setComments(current => [...current, { ...item, key: commentKey() }]), []);
  const removeComment = useCallback(key => setComments(current => current.filter(item => item.key !== key)), []);

  const send = async (action, after) => {
    setBusy(true); setError("");
    try {
      const response = await action();
      after?.(response);
      onUpdated?.(response);
      return response;
    } catch (failure) {
      setError(failure.message);
      return null;
    } finally { setBusy(false); }
  };
  const land = () => send(() => landTicket(address, patch, note), response => {
    const result = response?.result;
    setOutcome(result || null);
    setConfirming(false);
    if (result?.landed) { setNote(""); setComments([]); }
    setReload(value => value + 1);
  });
  const askChanges = () => send(() => requestChanges(address, { note: note.trim(), comments: comments.map(({ path, line, side, body }) => ({ path, line, side, body })) }), () => {
    setOutcome({ sent: true, text: `Sent to ${worker} as a request for changes.` });
    setNote(""); setComments([]);
  });
  const askRebase = conflicts => send(() => requestChanges(address, { note: note.trim(), rebase: { branch: patch.branch, base_branch: patch.baseBranch, conflicts } }), () => {
    setOutcome({ sent: true, text: `Asked ${worker} to rebase ${patch.branch} onto ${patch.baseBranch}.` });
    setNote("");
  });

  if (error && !patch) return <section className="tpr" aria-label="Changes"><p className="tpr-error" role="alert">Couldn&apos;t read the changes: {error}</p><button className="oh-btn small" type="button" onClick={() => setReload(value => value + 1)}><RotateCcw size={13} />Try again</button></section>;
  if (!patch) return <section className="tpr" aria-label="Changes"><p className="tpr-muted" role="status">Reading the changes…</p></section>;
  if (!patch.branch) return null;
  const status = mergeStatus(patch);
  const size = patchSize(patch.files);
  const conflicts = outcome?.conflict ? outcome.conflicts : status.conflicts;
  const canRequest = Boolean(note.trim() || comments.length);
  return <section className="tpr" aria-label="Changes">
    <div className="tpr-head">
      <span className="tpr-branch"><GitBranch size={13} aria-hidden="true" /><span className="oh-key">{patch.branch}</span>{patch.baseBranch ? <><span aria-hidden="true">→</span><span className="oh-key">{patch.baseBranch}</span></> : null}</span>
      <span className="tpr-size">{plural(size.files, "file")}, <span className="oh-add">+{size.added}</span> <span className="oh-del">−{size.removed}</span>{patch.truncated ? " (first 300 files)" : ""}</span>
      {status.text ? <span className={`tpr-state ${status.tone}`} role="status">{status.tone === "bad" ? <AlertTriangle size={12} aria-hidden="true" /> : status.tone === "ok" || status.tone === "done" ? <Check size={12} aria-hidden="true" /> : null}{status.text}</span> : null}
    </div>
    {patch.baseBranchGuessed ? <p className="tpr-muted">Raticode didn&apos;t record where this work started, so it lands in {patch.baseBranch}, the branch the project is on now.</p> : null}
    {patch.uncommitted?.length ? <p className="tpr-muted">{plural(patch.uncommitted.length, "file")} not committed yet. Accepting commits them on {patch.branch} first.</p> : null}
    <button className="oh-btn tpr-toggle" type="button" aria-expanded={expanded} onClick={() => onExpand?.(!expanded)}>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{expanded ? "Hide changes" : "Review changes"}{comments.length ? ` · ${plural(comments.length, "comment")}` : ""}</button>
    {expanded ? <div className="tpr-diff">
      <CommitDiff projectRoot={patch.repository || ""} payload={patch} theme={themeNow()} label={`Changes on ${patch.branch}`}
        subheading={decide ? "Click a line number, or use a file's Comment button, to leave a line comment for the worker." : ""}
        comments={comments} onComment={decide ? addComment : null} onRemoveComment={decide ? removeComment : null} />
    </div> : null}
    {outcome && !outcome.conflict ? <p className="tpr-outcome" role="status">{outcome.sent ? outcome.text : landedMessage(outcome)}</p> : null}
    {error ? <p className="tpr-error" role="alert">{error}</p> : null}
    {decide ? <div className="tpr-decide">
      {caution ? <p className="oh-caution"><AlertTriangle size={15} /><span>{caution}</span></p> : null}
      {conflicts?.length ? <div className="tpr-conflict" role="alert">
        <p><b>{patch.branch}</b> conflicts with <b>{patch.baseBranch}</b>, so it can&apos;t land yet. Nothing was changed.</p>
        <ul>{conflicts.slice(0, 12).map(path => <li key={path}><code>{path}</code></li>)}{conflicts.length > 12 ? <li>and {conflicts.length - 12} more</li> : null}</ul>
        <button className="oh-btn primary" type="button" disabled={busy} onClick={() => askRebase(conflicts)}>Ask {worker} to rebase</button>
      </div> : null}
      <label className="oh-sr" htmlFor={`tpr-note-${ticket.ticket_id}`}>Note for the worker</label>
      <textarea id={`tpr-note-${ticket.ticket_id}`} aria-label="Note for the worker" className="oh-note-input" rows={2} maxLength={4000} placeholder="Note for the worker (optional)" value={note} onChange={event => setNote(event.target.value)} />
      <div className="tpr-actions">
        {status.landed && !status.canLand ? null : confirming
          ? <span className="oh-confirm" role="group" aria-label="Confirm accept"><span role="status">{status.landed ? `Accept ${patch.branch}? It's already in ${patch.baseBranch}.` : `Land ${patch.branch} in ${patch.baseBranch}? Nothing is pushed.`}</span>
            <button className="oh-btn primary" type="button" disabled={busy} onClick={land}><GitMerge size={14} />{status.landed ? "Accept" : "Land it"}</button>
            <button className="oh-btn" type="button" onClick={() => setConfirming(false)}>Keep as is</button></span>
          : <button className="oh-btn primary" type="button" disabled={busy || !status.canLand} onClick={() => setConfirming(true)}><Check size={14} />{acceptLabel(patch)}</button>}
        <button className="oh-btn" type="button" disabled={busy || !canRequest} title={canRequest ? "" : "Write a note or add a line comment first"} onClick={askChanges}><MessageSquare size={14} />Request changes{comments.length ? ` (${plural(comments.length, "comment")})` : ""}</button>
      </div>
    </div> : null}
  </section>;
}
