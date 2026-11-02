// Rich parts of a Rem message: result card, details, follow-ups, message actions and
// live progress. The phone draws the same parts from the same fields (RemRich.kt).
import { useEffect, useState } from "react";
import { ArrowDown, Check, Copy, ExternalLink, FileText, MessageSquare, MessageSquareQuote } from "lucide-react";
import ChatSources from "./ChatSources.jsx";
import MarkdownContent from "./MarkdownContent.jsx";
import { openRemThread, retryRemReview, runRemTaskNow, suggestMessage } from "../lib/remHomeStore.js";
import { artifactLine, bodyWithoutCardLinks, resultActions, checksSummary, elapsedLabel, messageCopyText, quoteForReply, replyMeta, runSteps, stepText } from "../lib/remRich.js";
import "./RemRich.css";

// Extra depth under a short reply. Closed by default so the reply stays the lead.
export function MessageDetails({ details, onOpenLink }) {
  if (!details) return null;
  return <details className="rem-details">
    <summary>Details</summary>
    <MarkdownContent onOpenRelativeLink={onOpenLink} compact className="rem-markdown" value={details} />
  </details>;
}

// What a finished task produced: checks, saved reports and the web pages it used.
export function ResultCard({ result, body, onOpenLink }) {
  if (!result) return null;
  const checks = checksSummary(result.checks);
  const artifacts = result.artifacts || [];
  return <section className="rem-result" aria-label={`Result: ${result.title}`}>
    {checks ? <p className="rem-result-checks" data-tone={checks.tone}><span aria-hidden="true">{checks.symbol}</span> {checks.text}</p> : null}
    {artifacts.length ? <ul className="rem-result-files" aria-label="Saved files">
      {artifacts.map(artifact => <li key={artifact.path}>
        <span className="rem-result-thumb" data-kind={artifact.kind} aria-hidden="true"><FileText size={18} /></span>
        <span className="rem-result-file">
          <strong title={artifact.name}>{artifact.title || artifact.name}</strong>
          <small>{artifactLine(artifact)}</small>
        </span>
        <button type="button" className="rem-result-open" onClick={() => onOpenLink?.(artifact.path)} aria-label={`Open ${artifact.title || artifact.name}`}>
          <ExternalLink size={13} aria-hidden="true" />Open
        </button>
      </li>)}
    </ul> : null}
    {result.sources?.length ? <ChatSources body={body} sources={result.sources} /> : null}
    {result.thread_id && !artifacts.length ? <button type="button" className="rem-quiet-button rem-result-thread" onClick={() => openRemThread(result.thread_id)}>
      <MessageSquare size={13} aria-hidden="true" />Open thread
    </button> : null}
  </section>;
}

// Open report, Retry review and Run again under the newest update about a finished task.
// Each calls the same control as the task sheet; none comes from Rem's own text.
export function ResultActions({ message, tasks, live, onOpenLink }) {
  const [busy, setBusy] = useState("");
  const actions = resultActions(message, tasks, { live });
  if (!actions.length) return null;
  const act = async action => {
    if (action.id === "open") { onOpenLink?.(action.artifact.path); return; }
    setBusy(action.id);
    try { await (action.id === "retry" ? retryRemReview(action.run) : runRemTaskNow(action.task)); } finally { setBusy(""); }
  };
  return <div className="rem-result-actions" role="group" aria-label="Task actions">
    {actions.map(action => <button key={action.id} type="button" data-action={action.id} disabled={Boolean(busy)} onClick={() => void act(action)}>
      {busy === action.id ? `${action.label}…` : action.label}
    </button>)}
  </div>;
}

// Likely next messages. A tap only fills the message box; nothing is sent until you send it.
export function FollowUps({ suggestions }) {
  if (!suggestions?.length) return null;
  return <div className="rem-followups" role="group" aria-label="Suggested replies">
    {suggestions.map(text => <button key={text} type="button" onClick={() => suggestMessage(text)} title="Put this in the message box">{text}</button>)}
  </div>;
}

// Copy, quote into a reply, and how long the reply took. Shown on hover or focus.
export function MessageActions({ message }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const meta = replyMeta(message.meta);
  const copy = async () => {
    try { await navigator.clipboard.writeText(messageCopyText(message)); setCopied(true); } catch { /* the button stays as it was */ }
  };
  const quote = quoteForReply(bodyWithoutCardLinks(message.body, message.result));
  return <div className="rem-msg-actions">
    <button type="button" onClick={copy} aria-label={copied ? "Copied" : "Copy message"} title="Copy message">
      {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}<span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
    {quote ? <button type="button" onClick={() => suggestMessage(quote)} title="Quote this in a new message">
      <MessageSquareQuote size={13} aria-hidden="true" /><span>Ask about this</span>
    </button> : null}
    {meta ? <small className="rem-msg-meta" title="Chat model and reply time">{meta}</small> : null}
  </div>;
}

// Plan · Work · Check · Report for a task card, with how long it has run.
export function ProgressSteps({ status, now }) {
  const run = status?.run;
  const steps = runSteps(status?.state, { reachedCheck: Boolean(run?.verification_thread_id) });
  if (!steps.length) return null;
  const elapsed = elapsedLabel(run?.created_at, run?.finished_at || now);
  return <div className="rem-progress">
    <ol className="rem-progress-steps" aria-label="Progress">
      {steps.map(step => <li key={step.key} data-state={step.state} aria-current={step.state === "current" ? "step" : undefined}>
        <span aria-hidden="true">{{ done: "✓", current: "▶", stopped: "■", todo: "○" }[step.state]}</span>
        <span className="sr-only">{stepText(step)}</span>
        <span aria-hidden="true">{step.label}</span>
      </li>)}
    </ol>
    {elapsed ? <small className="rem-progress-time">{run?.finished_at ? `Took ${elapsed}` : `${elapsed} so far`}</small> : null}
  </div>;
}

// Shown when new messages arrive while you're reading further up.
export function NewMessagesPill({ count, onClick }) {
  if (!count) return null;
  return <button type="button" className="rem-new-pill" onClick={onClick}>
    <ArrowDown size={13} aria-hidden="true" />{count === 1 ? "1 new message" : `${count} new messages`}
  </button>;
}
