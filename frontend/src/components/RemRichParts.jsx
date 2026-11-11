// Rich parts of a Rem message: result card, details, follow-ups, message actions and
// live progress. The phone draws the same parts from the same fields (RemRich.kt).
import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, Check, ChevronDown, ChevronUp, Copy, ExternalLink, FileText, MessageSquare, Reply, X } from "lucide-react";
import ChatSources from "./ChatSources.jsx";
import MarkdownContent from "./MarkdownContent.jsx";
import { openRemThread, retryRemReview, runRemTaskNow, suggestMessage } from "../lib/remHomeStore.js";
import { replyToRemMessage } from "../lib/remComposer.js";
import { artifactLine, collapseLimits, resultActions, checksSummary, elapsedLabel, messageCopyText, referenceLines, replyMeta, replyTarget, runSteps, stepText } from "../lib/remRich.js";
import "./RemRich.css";

// Messages you opened or folded stay that way while this window is open.
const foldChoices = new Map();
// Folded messages on screen, by message ID, so a search result can open its message.
const foldOpeners = new Map();
// Opens a long message's text, such as when a search result jumps to a match in its folded part.
export function revealFold(id) {
  foldChoices.set(id, true);
  foldOpeners.get(id)?.();
}
// The conversation passes a function that keeps it at the newest message, when you are
// there, after a message finds it is long and gains its Show more or Show less.
export const FoldPinContext = createContext(null);

// A long message's text folds to a preview with Show more under it. Only the text folds;
// quotes, files, cards and actions stay visible. The whole text stays in the page, so
// screen readers, copy and find still get all of it, and tabbing to a link in the
// hidden part opens the message.
export function FoldableText({ id, startOpen = false, children }) {
  const contentRef = useRef(null);
  const toggleRef = useRef(null);
  const keepToggleInView = useRef(false);
  const contentId = useId();
  const [limits, setLimits] = useState(() => collapseLimits(typeof window === "undefined" ? 0 : window.innerHeight));
  const [long, setLong] = useState(false);
  const [open, setOpen] = useState(() => foldChoices.get(id) ?? startOpen);
  const keepPinned = useContext(FoldPinContext);
  useEffect(() => {
    if (!id) return undefined;
    const reveal = () => setOpen(true);
    foldOpeners.set(id, reveal);
    return () => { if (foldOpeners.get(id) === reveal) foldOpeners.delete(id); };
  }, [id]);
  // Measured before paint, so a long message never shows unfolded first.
  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return undefined;
    const measure = () => {
      const next = collapseLimits(window.innerHeight);
      setLimits(current => current.preview === next.preview ? current : next);
      setLong(content.offsetHeight > next.threshold);
    };
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(content);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  useLayoutEffect(() => { keepPinned?.(); }, [long, keepPinned]);
  // Folding a message you scrolled down through brings its preview and Show more back into view.
  useLayoutEffect(() => {
    if (open || !keepToggleInView.current) return;
    keepToggleInView.current = false;
    document.getElementById(contentId)?.scrollIntoView?.({ block: "nearest" });
    toggleRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, contentId]);
  const choose = next => {
    foldChoices.set(id, next);
    keepToggleInView.current = !next;
    setOpen(next);
  };
  const folded = long && !open;
  return <>
    <div id={contentId} className="rem-fold" data-folded={folded || undefined} style={folded ? { maxHeight: limits.preview } : undefined}
      onFocus={event => {
        if (!folded || event.target === event.currentTarget) return;
        if (event.target.getBoundingClientRect().bottom > event.currentTarget.getBoundingClientRect().bottom) choose(true);
      }}>
      <div ref={contentRef}>{children}</div>
    </div>
    {long ? <button ref={toggleRef} type="button" className="rem-fold-toggle" aria-expanded={open} aria-controls={contentId} onClick={() => choose(!open)}>
      {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      <span>{open ? "Show less" : "Show more"}</span>
    </button> : null}
  </>;
}

// Extra depth under a short reply. Closed by default so the reply stays the lead.
export function MessageDetails({ details, fileMentions = null, tickets = null, onOpenLink }) {
  if (!details) return null;
  return <details className="rem-details">
    <summary>Details</summary>
    <MarkdownContent onOpenRelativeLink={onOpenLink} compact className="rem-markdown" fileMentions={fileMentions} tickets={tickets} value={details} />
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

// Copy, reply, and how long the reply took. Shown on hover or focus.
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
  const text = messageCopyText(message);
  return <div className="rem-msg-actions">
    {/* A message that is still sending has no ID in the chat yet. */}
    {!message.pending && !message.failed ? <button type="button" onClick={() => replyToRemMessage(replyTarget(message))} aria-label="Reply to this message" title="Reply to this message">
      <Reply size={13} aria-hidden="true" /><span>Reply</span>
    </button> : null}
    {text ? <button type="button" onClick={copy} aria-label={copied ? "Copied" : "Copy message"} title="Copy message">
      {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}<span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button> : null}
    {meta ? <small className="rem-msg-meta" title="Chat model and reply time">{meta}</small> : null}
  </div>;
}

// The message a reply refers to: above the reply's own words, and above the message
// box while you write one. onShow scrolls to it when it is in the chat; onCancel unlinks.
export function ReplyQuote({ reference, onShow, onCancel, composing = false }) {
  const lines = referenceLines(reference);
  if (!lines) return null;
  const label = `${composing ? "Replying to" : "In reply to"} ${lines.author}`;
  const body = <>
    <span className="rem-quote-author">{label}</span>
    <span className="rem-quote-text">{lines.text}</span>
  </>;
  return <div className="rem-quote" data-composing={composing || undefined} data-unavailable={reference.unavailable || undefined}>
    {onShow ? <button type="button" className="rem-quote-main" onClick={onShow} title="Show this message" aria-label={`${label}: ${lines.text}. Show this message`}>{body}</button>
      : <div className="rem-quote-main" aria-label={`${label}: ${lines.text}`} role="group">{body}</div>}
    {onCancel ? <button type="button" className="rem-quote-cancel" onClick={onCancel} aria-label="Don't reply to this message" title="Don't reply to this message"><X size={14} aria-hidden="true" /></button> : null}
  </div>;
}

// Plan · Work · Check · Report for a task card, with how long it has run.
export function ProgressSteps({ status, now }) {
  const run = status?.run;
  const steps = runSteps(status?.state, { reachedCheck: Boolean(run?.verification_thread_id), step: status?.step });
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
