import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, ExternalLink, MessageSquare, X } from "lucide-react";
import { checksLine, entryHeadline, entryTone, readTimeline, sendComment, STATUS_LABELS, visibleEntries } from "../lib/remKanban.js";
import { ago, formatWhen } from "../lib/organizationModel.js";
import { openRemThread } from "../lib/remHomeStore.js";
import { startPolling } from "../lib/refresh.js";

// A ticket's history under its details: each status change with the reason taken from its run
// or review, and comments from you, Rem and workers, oldest first.
export default function TicketTimeline({ ticket, interval = 5000 }) {
  const [view, setView] = useState(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const address = `${ticket.scope}:${ticket.organization_id || ""}:${ticket.ticket_id}`;
  const ticketRef = useRef(ticket);
  ticketRef.current = ticket;
  const refresh = useCallback(async () => {
    try {
      setView(await readTimeline(ticketRef.current));
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }, []);
  useEffect(() => {
    setView(null);
    setExpanded(false);
    return startPolling(refresh, { interval, immediate: true, label: "the ticket's history" });
  }, [address, interval, refresh]);
  // A status change shows up in the timeline as soon as the board sees it.
  useEffect(() => { refresh(); }, [ticket.revision, ticket.status, refresh]);
  const submit = async event => {
    event.preventDefault();
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      await sendComment(ticketRef.current, body);
      setDraft("");
      await refresh();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSending(false);
    }
  };
  const entries = view?.comments || [];
  const { shown, hidden } = visibleEntries(entries, expanded);
  const older = (view?.earlier || 0) + hidden;
  return <section className="kb-section kb-timeline" aria-label="History and comments">
    <h3>History and comments</h3>
    {error ? <p className="kb-timeline-error" role="status">{error}</p> : null}
    {!view && !error ? <p className="oh-muted" role="status">Loading its history…</p> : null}
    {view && !entries.length ? <p className="oh-muted">Nothing yet. Status changes and comments appear here.</p> : null}
    {older ? <button type="button" className="oh-btn small ghost kb-timeline-more" disabled={!hidden} onClick={() => setExpanded(true)}>
      {hidden ? `Show ${hidden} earlier ${hidden === 1 ? "entry" : "entries"}` : `${older} older ${older === 1 ? "entry isn't" : "entries aren't"} shown`}
    </button> : null}
    {shown.length ? <ol className="kb-timeline-list">
      {shown.map(entry => <TimelineEntry key={entry.id || `${entry.at}-${entry.title}`} entry={entry} />)}
    </ol> : null}
    {view?.can_comment !== false ? <form className="kb-comment" onSubmit={submit}>
      <textarea aria-label="Add a comment" className="kb-input" rows={2} maxLength={20000} placeholder="Add a comment" value={draft}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit(event); }} />
      <button className="oh-btn small" type="submit" disabled={sending || !draft.trim()}><MessageSquare size={13} />Comment</button>
    </form> : null}
  </section>;
}

function TimelineEntry({ entry }) {
  const tone = entryTone(entry);
  const headline = entryHeadline(entry);
  const summary = checksLine(entry.checks);
  return <li className={`kb-entry tone-${tone}`}>
    <span className="kb-entry-dot" aria-hidden="true" />
    <div className="kb-entry-main">
      <div className="kb-entry-meta">
        <span className="kb-entry-who">{entry.author_name || "Raticode"}</span>
        {entry.at ? <time dateTime={entry.at} title={formatWhen(entry.at)}>{ago(entry.at) || formatWhen(entry.at)}</time> : null}
      </div>
      {headline ? <p className="kb-entry-title">{headline}</p> : null}
      {entry.kind === "status" && entry.from && entry.to ? <p className="kb-entry-move" aria-label={`From ${STATUS_LABELS[entry.from] || entry.from} to ${STATUS_LABELS[entry.to] || entry.to}`}>
        <span>{STATUS_LABELS[entry.from] || entry.from}</span><ArrowRight size={11} aria-hidden="true" /><span>{STATUS_LABELS[entry.to] || entry.to}</span>
      </p> : null}
      {entry.body ? <p className="kb-entry-body">{entry.body}</p> : null}
      {entry.checks?.length ? <div className="kb-entry-checks">
        <p>{summary}</p>
        <ul>{entry.checks.map(check => <li key={check.name} className={check.result === "pass" ? "pass" : check.result === "fail" ? "fail" : "open"}>
          {check.result === "pass" ? <Check size={12} aria-label="Passed" /> : check.result === "fail" ? <X size={12} aria-label="Failed" /> : <span aria-label="Not checked">·</span>}
          <span>{check.name}</span>
        </li>)}</ul>
      </div> : null}
      {entry.details ? <details className="kb-entry-details"><summary>Show the full text</summary><p>{entry.details}</p></details> : null}
      {entry.thread_id ? <button type="button" className="oh-btn small ghost" onClick={() => openRemThread(entry.thread_id)}><ExternalLink size={12} />Open its thread</button> : null}
    </div>
  </li>;
}
