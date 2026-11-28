import { useEffect, useState } from "react";
import { contextRows, contextSummary, currentRulesLine, ESTIMATE_NOTE, formatChars, formatTokens, loadThreadContext, remCallRows, remCallTokensLine, standingRows } from "../lib/contextSent.js";
import "./ContextSent.css";

// One turn's prompt, part by part: how many characters each part was and roughly how
// many tokens. Sizes only; Raticode never keeps the text itself.
function ContextSentBreakdown({ turn }) {
  const rows = contextRows(turn);
  if (!rows.length) return null;
  return <div className="context-sent-turn">
    <p className="context-sent-summary">{contextSummary(turn)}</p>
    <ul className="context-sent-parts" aria-label="Parts of the newest turn">
      {rows.map(row => <li key={row.key} data-kept={row.sent || row.file ? undefined : "true"} data-file={row.file ? "true" : undefined}>
        <span className="context-sent-label">{row.label}{row.file ? <em> · in a file, read when needed</em> : row.sent ? null : <em> · kept</em>}</span>
        <span className="context-sent-size" title={formatChars(row.chars)}>{formatTokens(row.tokens)}</span>
        <span className="context-sent-bar" aria-hidden="true"><span style={{ width: `${row.width}%` }} /></span>
      </li>)}
    </ul>
  </div>;
}

// The newest turn of a thread, read when shown. ``refreshKey`` reads it again, such as
// after the thread's next turn.
export function ThreadContextSent({ threadId, refreshKey = "", heading = "Context sent", headingLevel = 4 }) {
  const [state, setState] = useState({ status: "loading" });
  useEffect(() => {
    if (!threadId) return undefined;
    const controller = new AbortController();
    setState(previous => (previous.threadId === threadId && previous.status === "ready" ? previous : { status: "loading", threadId }));
    loadThreadContext(threadId, { limit: 1, signal: controller.signal })
      .then(found => setState({ status: "ready", threadId, found }))
      .catch(error => { if (error?.name !== "AbortError") setState({ status: "error", threadId, message: error.message }); });
    return () => controller.abort();
  }, [threadId, refreshKey]);
  const Heading = `h${headingLevel}`;
  const turn = state.found?.turns?.[0];
  const count = state.found?.turn_count || 0;
  return <section className="context-sent" aria-label={heading}>
    <Heading className="context-sent-heading">{heading}</Heading>
    {state.status === "loading" ? <p className="context-sent-note" role="status">Reading sizes…</p> : null}
    {state.status === "error" ? <p className="context-sent-note" role="alert">{state.message}</p> : null}
    {state.status === "ready" && !turn ? <p className="context-sent-note">No turns recorded yet. Sizes appear after this thread's next turn.</p> : null}
    {turn ? <>
      <ContextSentBreakdown turn={turn} />
      <p className="context-sent-note">Newest of {count} {count === 1 ? "turn" : "turns"}. {ESTIMATE_NOTE} Only sizes are kept, never the text.</p>
    </> : null}
  </section>;
}

function counted(count, unit) {
  return `${count} ${count === 1 ? unit : `${unit}s`}`;
}

// Settings > Usage: the parts of Rem's prompts that repeat every turn, ranked by how
// much text they sent over the last week. Tells which standing part to trim first.
// Rem's own calls (chat replies, answers to workers and the like) rank with the
// threads' turns, and a short list below gives each kind's call count and size, how
// many of its calls were re-asked or still failed (with a line for the current fixed
// rules version alone), and the input and cache tokens its providers reported.
export function StandingParts({ standing }) {
  if (!standing) return null;
  const rows = standingRows(standing);
  const calls = remCallRows(standing);
  const days = standing.days || 7;
  const across = [standing.turns ? counted(standing.turns, "turn") : "", standing.calls ? `${counted(standing.calls, "call")} of Rem's own` : ""].filter(Boolean).join(" and ");
  return <section className="context-sent usage-standing" aria-labelledby="usage-standing-title">
    <h3 id="usage-standing-title" className="context-sent-heading">Biggest standing parts, last {days} days</h3>
    {rows.length ? <>
      <p className="context-sent-summary">Parts of Rem&apos;s prompts that repeat every time, by how much text they sent across {across || "0 turns"}.</p>
      <ol className="context-sent-parts">
        {rows.map(row => <li key={row.key}>
          <span className="context-sent-label">{row.label}<small>{formatTokens(row.averageTokens)} each · sent in {row.sentTurns} of {counted(row.turns, row.unit)}</small></span>
          <span className="context-sent-size">{formatTokens(row.sentTokens)} sent</span>
          <span className="context-sent-bar" aria-hidden="true"><span style={{ width: `${row.width}%` }} /></span>
        </li>)}
      </ol>
    </> : <p className="context-sent-note">No Rem turns recorded in the last {days} days.</p>}
    {calls.length ? <>
      <h4 className="context-sent-subheading">Rem&apos;s own calls</h4>
      <ul className="context-sent-parts" aria-label="Rem's own calls by kind">
        {calls.map(row => <li key={row.key}>
          <span className="context-sent-label">{row.label}<small>{counted(row.calls, "call")} · {formatTokens(row.averageTokens)} each · largest {formatTokens(row.largestTokens)}</small><small>{row.reasked} re-asked · {row.failed} failed</small>{row.current ? <small title={`Fixed rules version ${row.current.version}, counted over every kept call sent with it`}>{currentRulesLine(row.current)}</small> : null}{remCallTokensLine(row.tokens) ? <small title={`Tokens the provider reported for ${counted(row.tokens.calls, "call")}. Input includes what was read from and written to the cache.`}>{remCallTokensLine(row.tokens)}</small> : null}</span>
          <span className="context-sent-size">{formatTokens(row.sentTokens)} sent</span>
          <span className="context-sent-bar" aria-hidden="true"><span style={{ width: `${row.width}%` }} /></span>
        </li>)}
      </ul>
    </> : null}
    {rows.length || calls.length ? <p className="context-sent-note">{ESTIMATE_NOTE} A resumed session keeps its instructions, so a part it didn&apos;t send again counts toward its size but not toward what was sent. Rem&apos;s own calls have no session and send every part. A re-asked call asked again because the answer wasn&apos;t the requested format or didn&apos;t validate; a failed one still couldn&apos;t be used. Current rules counts only the calls sent with the fixed rules a kind uses now, so a change to those rules starts again from zero.</p> : null}
  </section>;
}
