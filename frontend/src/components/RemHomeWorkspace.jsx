import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Brain, MessageSquare, MoreHorizontal, Paperclip, ShieldCheck, Settings, Sparkles, Trash2, X } from "lucide-react";
import ChatComposer, { MessageAttachments } from "./ChatComposer.jsx";
import MarkdownContent from "./MarkdownContent.jsx";
import RatFaceIcon from "./RatFaceIcon.jsx";
import RemAvatar from "./RemAvatar.jsx";
import RemMemoryPanel, { MemoryNote } from "./RemMemoryPanel.jsx";
// Files on a reply; a paired phone that asked receives them too.
import { ReplyFiles } from "./RemMedia.jsx";
import { OutcomeGlyph, RemMenu, typeLabel } from "./RemHomeParts.jsx";
import { FoldPinContext, FoldableText, FollowUps, MessageActions, MessageDetails, NewMessagesPill, ProgressSteps, ReplyQuote, ResultActions, ResultCard } from "./RemRichParts.jsx";
import { bodyWithoutCardLinks, latestRemMessageId, latestSuggestionMessageId, remPresence } from "../lib/remRich.js";
import { criteriaSummary, conversationMessages, messageStamp, messagesByDay, statusLine, latestWorkMessageIds, workStatus } from "../lib/remHome.js";
import { clearRemHistory, connectRemHome, dismissNotice, editRemMessage, loadRemHomeFixture, openRemThread, openTaskSheet, resetRemHomeStore, retryRemMessage, sendRemMessage, suggestMessage, useRemHome } from "../lib/remHomeStore.js";
import { applyRemComposerRequest, attachToRemComposer, updateRemComposer, useRemComposerState } from "../lib/remComposer.js";
import { clipboardAttachmentFiles, largePasteFile, transferContainsFiles } from "../lib/chatAttachments.js";
import { copiedFilePaths, readFileAttachments } from "../lib/fileAttachments.js";
import "./RemHome.css";

const SUGGESTIONS = [
  "What's running right now?",
  "Research a topic and save a report",
  "Every Monday, check for failed workflow runs",
  "Remind me Friday to send the release notes",
];
const devPreview = Boolean(import.meta.env?.DEV);

// Rows of messages without a task card get this instead of the task list, so a
// task update doesn't re-render them.
const NO_TASKS = [];

// Each task-card row gets a list holding only its own task, kept while that task
// is unchanged, so a poll re-renders just the rows whose task moved.
function useTaskLists(tasks) {
  const cache = useRef(new Map());
  return useMemo(() => {
    const next = new Map();
    for (const task of tasks) {
      const old = cache.current.get(task.id);
      next.set(task.id, old?.[0] === task ? old : [task]);
    }
    cache.current = next;
    return next;
  }, [tasks]);
}

export default function RemHomeWorkspace({ avatar = {}, onOpenLink, onOpenSettings }) {
  const store = useRemHome();
  // The app passes a new function on every render; a stable one keeps message rows cached.
  const openLinkRef = useRef(onOpenLink);
  useLayoutEffect(() => { openLinkRef.current = onOpenLink; });
  const openLink = useCallback(href => openLinkRef.current?.(href), []);
  const [confirmClear, setConfirmClear] = useState(false);
  const [showMemories, setShowMemories] = useState(false);
  const [messageFocus, setMessageFocus] = useState(null);
  const showMessage = useCallback(id => setMessageFocus({ id }), []);
  // "View in conversation" in an opened task (in the Rem sidebar) lands here.
  useEffect(() => { if (store.messageRequest) setMessageFocus({ id: store.messageRequest.id }); }, [store.messageRequest]);
  useEffect(() => connectRemHome(), []);
  const now = useNow();
  const replying = store.conversation?.turn_state === "replying";
  const presence = remPresence({ offline: store.status === "offline", replying, turnLabel: store.conversation?.turn_label, tasks: store.tasks, status: statusLine(store.tasks, now) });
  const status = presence.label;
  return <section className="rem-home" aria-label="Rem">
    <header className="rem-home-header">
      <span className="rem-face" data-size="lg" data-presence={presence.state}><RatFaceIcon size={22} /></span>
      <div className="rem-home-title"><h2>Rem</h2><p role="status">{status}</p></div>
      {store.fixture ? <span className="rem-preview-pill">Preview<button type="button" onClick={() => { resetRemHomeStore(); connectRemHome(); }}>Exit</button></span> : null}
      <RemMenu label="Rem options" className="rem-home-options" trigger={<MoreHorizontal size={18} />} items={[
        { label: "Provider, model and effort…", icon: Settings, onSelect: onOpenSettings },
        { label: "What Rem remembers…", icon: Brain, disabled: store.fixture || store.status !== "ready", onSelect: () => setShowMemories(true) },
        { label: "Clear history and tasks…", icon: Trash2, danger: true, disabled: store.resetting || store.status !== "ready", onSelect: () => setConfirmClear(true) },
      ]} />
    </header>
    {confirmClear ? <div className="rem-clear-confirm" role="alertdialog" aria-labelledby="rem-clear-title" aria-describedby="rem-clear-description">
      <h3 id="rem-clear-title">Start fresh with Rem?</h3>
      <p id="rem-clear-description">Stop Rem’s running work and permanently remove its conversation, tasks, memories, and task threads, including archived copies. Your own threads, project files, and saved reports stay.</p>
      <div><button type="button" disabled={store.resetting} onClick={() => setConfirmClear(false)}>Keep history</button><button type="button" data-danger="true" disabled={store.resetting} onClick={async () => { if (await clearRemHistory()) setConfirmClear(false); }}>{store.resetting ? "Clearing…" : "Clear history and tasks"}</button></div>
    </div> : null}
    {showMemories ? <RemMemoryPanel now={now} onClose={() => setShowMemories(false)} /> : null}
    {store.notice ? <div role="status" className="rem-home-notice"><span>{store.notice}</span><button type="button" aria-label="Dismiss" title="Dismiss" onClick={dismissNotice}><X size={14} /></button></div> : null}
    {store.status === "error" && store.error ? <div role="alert" className="rem-home-notice" data-tone="danger"><span>{store.error}</span></div> : null}
    {store.contextIssue ? <div role="status" className="rem-home-notice" data-tone="attention"><span>{store.contextIssue}</span></div> : null}
    {/* An opened task shows in the Rem sidebar, so the conversation keeps its width. */}
    <div className="rem-home-main">
      <Conversation key={store.conversation?.id || "loading"} store={store} now={now} replying={replying} avatar={avatar} onOpenLink={openLink} messageFocus={messageFocus} onShowMessage={showMessage} />
    </div>
  </section>;
}

function useNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = window.setInterval(() => setNow(new Date()), 30000); return () => window.clearInterval(timer); }, []);
  return now;
}

function Conversation({ store, now, replying, avatar, onOpenLink, messageFocus, onShowMessage }) {
  const scrollRef = useRef(null);
  const pinnedRef = useRef(true);
  const days = useMemo(() => messagesByDay(conversationMessages(store.messages), now), [store.messages, now]);
  const liveWork = useMemo(() => latestWorkMessageIds(store.messages), [store.messages]);
  const taskLists = useTaskLists(store.tasks);
  const followUpsFor = latestSuggestionMessageId(store.messages);
  const newestRem = latestRemMessageId(store.messages);
  // A reply's quote scrolls to the message it refers to when that message is in the chat.
  const shown = useMemo(() => new Set(store.messages.map(message => message.id)), [store.messages]);
  const [unseen, setUnseen] = useState(0);
  const seenCount = useRef(store.messages.length);
  const offline = store.status === "offline";
  const last = store.messages.at(-1);
  // Follow new messages unless the person has scrolled up to read.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedRef.current) element.scrollTop = element.scrollHeight;
    // While you read further up, count what arrived instead of jumping to it.
    if (pinnedRef.current) seenCount.current = store.messages.length;
    setUnseen(pinnedRef.current ? 0 : Math.max(0, store.messages.length - seenCount.current));
  }, [store.messages.length, last?.id, replying]);
  const keepPinned = useCallback(() => {
    const element = scrollRef.current;
    if (element && pinnedRef.current) element.scrollTop = element.scrollHeight;
  }, []);
  // Messages lay out when they scroll near (content-visibility), images load and
  // the pane can change width, so the history grows after it was scrolled to the
  // end. While following the newest message, keep following it.
  useEffect(() => {
    const inner = scrollRef.current?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(keepPinned);
    observer.observe(inner);
    return () => observer.disconnect();
  }, [keepPinned]);
  const jumpToLatest = () => {
    const element = scrollRef.current;
    pinnedRef.current = true;
    seenCount.current = store.messages.length;
    setUnseen(0);
    element?.scrollTo({ top: element.scrollHeight, behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  };
  useLayoutEffect(() => {
    if (!messageFocus) return;
    const message = [...(scrollRef.current?.querySelectorAll?.("[data-message-id]") || [])]
      .find(element => element.dataset.messageId === messageFocus.id);
    if (!message) return;
    pinnedRef.current = false;
    message.focus({ preventScroll: true });
    message.scrollIntoView({ block: "center" });
  }, [messageFocus]);
  const disabled = offline || store.status === "loading" || store.resetting;
  const drop = useRemDropZone();
  return <div className="rem-convo" {...drop.handlers}>
    {drop.dragging ? <div className="rem-drop" role="status" aria-live="polite"><Paperclip aria-hidden="true" size={20} /><span>Drop files to attach</span></div> : null}
    <div ref={scrollRef} className="rem-stream" onScroll={event => {
      const element = event.currentTarget;
      pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
      if (pinnedRef.current && unseen) { seenCount.current = store.messages.length; setUnseen(0); }
    }}>
      <div className="rem-stream-inner" role="log" aria-label="Conversation with Rem" aria-live="polite">
        <FoldPinContext.Provider value={keepPinned}>
          {store.messages.length ? days.map(day => <div key={day.key} className="rem-day">
            <div className="rem-day-label" role="separator"><span>{day.label}</span></div>
            {day.messages.map((message, index) => <Message key={message.id} message={message} first={day.messages[index - 1]?.role !== message.role} now={now} tasks={message.work ? taskLists.get(message.work.task_id) || NO_TASKS : NO_TASKS} onOpenLink={onOpenLink} live={liveWork.has(message.id)} followUps={message.id === followUpsFor} newest={message.id === newestRem} onShowMessage={message.refers_to && shown.has(message.refers_to) ? onShowMessage : null} />)}
          </div>) : <Welcome offline={offline} loading={store.status === "loading"} avatar={avatar} />}
        </FoldPinContext.Provider>
        {replying ? <div className="rem-msg" data-role="rem"><span className="rem-face"><RatFaceIcon size={18} /></span><div className="rem-msg-body">
          {/* The reply as it is written. Screen readers hear the finished message instead. */}
          {store.conversation?.draft_reply ? <div className="rem-draft" aria-hidden="true"><MarkdownContent compact className="rem-markdown" value={store.conversation.draft_reply} /></div> : null}
          <div className="rem-turn" role="status" aria-label={store.conversation?.turn_label ? `Rem is ${store.conversation.turn_label.toLowerCase()}` : "Rem is replying"}>
            <span className="rem-typing" aria-hidden="true"><i /><i /><i /></span>
            {store.conversation?.turn_label ? <span className="rem-turn-label" aria-hidden="true">{store.conversation.turn_label}…</span> : null}
          </div>
        </div></div> : null}
      </div>
    </div>
    <NewMessagesPill count={unseen} onClick={jumpToLatest} />
    <Composer disabled={disabled} replying={replying} request={store.composerRequest} offline={offline} onShowMessage={onShowMessage} shown={shown} />
  </div>;
}

// Rem's animated sprite greets you here; the rat face stands in when the sprite is turned off.
function Welcome({ offline, loading, avatar }) {
  return <div className="rem-welcome">
    {avatar.enabled !== false
      ? <RemAvatar visible animated={avatar.animated !== false} reducedMotion={avatar.reducedMotion} />
      : <span className="rem-welcome-face"><RatFaceIcon size={44} strokeWidth={1.4} /></span>}
    <h3>{loading ? "Just a sec…" : "Hi, I’m Rem."}</h3>
    {offline ? <>
      <p>My task service isn’t running in this version yet, so I can’t take on work here. Your threads still work in the Rem pane.</p>
      {devPreview ? <button type="button" className="rem-quiet-button" onClick={() => void loadRemHomeFixture()}><Sparkles size={14} aria-hidden="true" />Preview with sample data</button> : null}
    </> : <>
      <p>Tell me what you need. I’ll start the work, check it, and let you know when it’s done.</p>
      {!loading ? <div className="rem-suggestions" aria-label="Ideas">
        {SUGGESTIONS.map(text => <button key={text} type="button" onClick={() => suggestMessage(text)}>{text}</button>)}
      </div> : null}
    </>}
  </div>;
}

// Rows re-render only when their own props change, not on every keystroke or poll.
// [newest] opens a long reply in full when it first shows; older long messages start folded.
const Message = memo(function Message({ message, first, now, tasks, onOpenLink, live = true, followUps = false, newest = false, onShowMessage = null }) {
  const stamp = <MessageStamp iso={message.created_at} now={now} />;
  const quote = message.referenced ? <ReplyQuote reference={message.referenced} onShow={onShowMessage ? () => onShowMessage(message.refers_to) : null} /> : null;
  if (message.role === "user") {
    return <div className="rem-msg" data-message-id={message.id} tabIndex={-1} data-role="user" data-first={first || undefined} data-pending={message.pending || undefined}>
      <div className="rem-bubble">
        {quote}
        {message.attachments?.length ? <div className="rem-bubble-files"><MessageAttachments attachments={message.attachments} inverse onOpenLink={onOpenLink} /></div> : null}
        {message.body ? <FoldableText id={message.id}>{message.body}</FoldableText> : null}
      </div>
      {message.failed ? <p className="rem-msg-failed">Didn’t send. <button type="button" onClick={() => retryRemMessage(message.id)}>Try again</button> <button type="button" onClick={() => editRemMessage(message.id)}>Edit</button></p>
        : message.pending && message.waiting ? <p className="rem-msg-waiting" role="status">Sends when Rem finishes replying</p>
        : <div className="rem-msg-foot">{message.pending ? null : <MessageActions message={message} />}{stamp}</div>}
    </div>;
  }
  // A Rem message right after another Rem message gets a faint rule above it (data-follows).
  return <div className="rem-msg" data-message-id={message.id} tabIndex={-1} data-role="rem" data-first={first || undefined} data-follows={first ? undefined : true}>
    {first ? <span className="rem-face"><RatFaceIcon size={18} /></span> : <span className="rem-face-space" aria-hidden="true" />}
    <div className="rem-msg-body">
      {quote}
      <FoldableText id={message.id} startOpen={newest}>
        <MarkdownContent onOpenRelativeLink={onOpenLink} compact className="rem-markdown" fileMentions={message.file_mentions} value={message.result ? bodyWithoutCardLinks(message.body, message.result) : message.body} />
      </FoldableText>
      <MessageDetails details={message.details} fileMentions={message.file_mentions} onOpenLink={onOpenLink} />
      <ResultCard result={message.result} body={message.body} onOpenLink={onOpenLink} />
      {message.files?.length ? <ReplyFiles files={message.files} onOpenLink={onOpenLink} /> : null}
      {message.work ? <WorkCard work={message.work} tasks={tasks} live={live} /> : null}
      <ResultActions message={message} tasks={tasks} live={live} onOpenLink={onOpenLink} />
      <MemoryNote messageId={message.id} memory={message.memory} />
      {followUps ? <FollowUps suggestions={message.suggestions} /> : null}
      {/* The time and the message's actions (Reply, Copy, model and time) share one line. */}
      <div className="rem-msg-foot">{stamp}<MessageActions message={message} /></div>
    </div>
  </div>;
});

// Shows nothing when the message has no usable date.
function MessageStamp({ iso, now }) {
  const stamp = messageStamp(iso, now);
  if (!stamp) return null;
  return <time className="rem-msg-time" dateTime={stamp.iso} title={stamp.full}>
    <span aria-hidden="true">{stamp.label}</span><span className="sr-only">{stamp.full}</span>
  </time>;
}

// Live view of the thread Rem started for a request. The thread link is always shown.
// Earlier cards for the same run point to the newest one instead of repeating its state.
function WorkCard({ work, tasks, live = true }) {
  const current = workStatus(work, tasks);
  const status = live ? current : { ...current, tone: "neutral", spinning: false, state: "earlier", label: "Latest update below", verificationThreadId: null };
  const checks = !live || status.state === "succeeded" ? "" : criteriaSummary(status.criteria);
  return <div className="rem-work" data-tone={status.tone} data-spinning={status.spinning || undefined} data-earlier={live ? undefined : true}>
    <span className="rem-work-glyph"><OutcomeGlyph status={status} /></span>
    <button type="button" className="rem-work-main" disabled={!status.task} onClick={() => status.task && openTaskSheet(status.task.id)} title={status.task ? "Task details" : status.deleted ? "This task was deleted" : undefined}>
      <strong>{status.title}</strong>
      <small>{[status.type ? typeLabel(status.type) : "", status.label, checks].filter(Boolean).join(" · ")}</small>
    </button>
    {live ? <ProgressSteps status={status} now={new Date()} /> : null}
    <div className="rem-work-links">
      {status.threadId ? <button type="button" onClick={() => openRemThread(status.threadId)}><MessageSquare size={13} aria-hidden="true" />Open thread</button> : <span className="rem-work-pending">Thread starting…</span>}
      {status.verificationThreadId && status.verificationThreadId !== status.threadId && status.state !== "succeeded" ? <button type="button" onClick={() => openRemThread(status.verificationThreadId)} aria-label="Open review thread" title="Open review thread"><ShieldCheck size={13} aria-hidden="true" /><span>Review</span></button> : null}
    </div>
  </div>;
}

// The same composer as threads: files can be attached, pasted or dropped, and long
// pasted text becomes a text attachment. Files copied in Raticode paste as attachments.
// The draft lives outside this view so it survives switching to another plugin.
// Only the composer reads it, so typing doesn't re-render the conversation.
function useRemDropZone() {
  const [dragging, setDragging] = useState(false);
  const dragDepthRef = useRef(0);
  const add = files => attachToRemComposer(files);
  const handlers = {
    onPaste(event) {
      const files = clipboardAttachmentFiles(event.clipboardData);
      const pastedText = event.clipboardData?.getData?.("text/plain") || "";
      const copiedPaths = files.length ? [] : copiedFilePaths(pastedText);
      if (copiedPaths.length) {
        event.preventDefault();
        void readFileAttachments(copiedPaths).then(add, cause => updateRemComposer({ error: cause.message }));
        return;
      }
      const textFile = largePasteFile(pastedText);
      if (!files.length && !textFile) return;
      event.preventDefault();
      add(textFile ? [...files, textFile] : files);
    },
    onDragEnter(event) {
      if (!transferContainsFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepthRef.current += 1;
      setDragging(true);
    },
    onDragOver(event) {
      if (!transferContainsFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    },
    onDragLeave(event) {
      if (!transferContainsFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (!dragDepthRef.current) setDragging(false);
    },
    onDrop(event) {
      if (!transferContainsFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepthRef.current = 0;
      setDragging(false);
      add(event.dataTransfer.files);
    },
  };
  return { dragging, handlers };
}

function Composer({ disabled, replying, request, offline, onShowMessage, shown }) {
  const { draft, attachments, error: attachmentError, replyTo } = useRemComposerState();
  const [focusRequest, setFocusRequest] = useState(0);
  const send = async () => {
    const text = draft.trim();
    if ((!text && !attachments.length) || disabled) return;
    updateRemComposer({ draft: "", attachments: [], error: "", replyTo: null });
    // A message that doesn't send stays in the conversation with Try again and Edit.
    await sendRemMessage(text, attachments, null, replyTo);
  };
  useEffect(() => {
    if (applyRemComposerRequest(request)) setFocusRequest(current => current + 1);
  }, [request]);
  return <div className="rem-composer">
    {replyTo ? <ReplyQuote composing reference={replyTo} onShow={shown.has(replyTo.id) ? () => onShowMessage(replyTo.id) : null}
      onCancel={() => { updateRemComposer({ replyTo: null }); setFocusRequest(current => current + 1); }} /> : null}
    <ChatComposer
      inputLabel="Message Rem"
      placeholder={offline ? "Rem isn't connected yet" : replying ? "Rem is replying… you can keep typing" : attachments.length ? "Add a note about the attached file" : "Message Rem"}
      sendLabel="Send"
      attachments={attachments}
      attachmentError={attachmentError}
      draft={draft}
      focusRequest={focusRequest}
      permissionOptions={[]}
      sendDisabled={disabled}
      onAddAttachments={files => attachToRemComposer(files)}
      onAttachmentErrorChange={error => updateRemComposer({ error })}
      onAttachmentsChange={attachments => updateRemComposer({ attachments })}
      onDraftChange={draft => updateRemComposer({ draft })}
      onSend={() => void send()}
    />
  </div>;
}
