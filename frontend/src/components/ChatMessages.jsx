// One message in Rem's chat pane: its bubble, the thoughts and tool calls behind a reply,
// and the summary of what a turn changed.
import { lazy, memo, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Check, ChevronDown, ChevronRight, ChevronUp, Copy, FileDiff, GitBranch, Loader2, PencilLine, Redo2, Smartphone, Undo2, UserRound } from "lucide-react";
import { clipboardAttachmentFiles, isChatImage, largePasteFile, readChatAttachments } from "../lib/chatAttachments.js";
import { buildThoughtTrace } from "../lib/chatThreads.js";
import { isForkableMessage } from "../lib/forkThread.js";
import { MessageAttachments } from "./ChatComposer.jsx";
import MarkdownContent from "./MarkdownContent.jsx";

const ChatSources = lazy(() => import("./ChatSources.jsx"));

function ForkButton({ messageId, onFork }) {
  return onFork ? <button type="button" title="Fork thread from here" aria-label="Fork thread from here"
    className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-[10px] text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline-brand"
    onClick={() => void onFork(messageId)}><GitBranch aria-hidden="true" size={12} /><span>Fork</span></button> : null;
}

export const ForkableMessage = memo(function ForkableMessage({ onFork, canFork, ...props }) {
  return <div>
    {props.message.role === "user" && props.message.modelSwitchBanner ? (
      <div className="rem-model-switch" role="note" aria-label="Model changed">
        <span>{props.message.modelSwitchBanner}</span>
      </div>
    ) : null}
    <ChatMessageBubble {...props} />
    {(canFork ?? isForkableMessage(props.message)) ? <div className="flex justify-end"><ForkButton messageId={props.message.id} onFork={onFork} /></div> : null}
  </div>;
});

export const ChatMessageBubble = memo(function ChatMessageBubble({ canEdit = false, message, onEdit, onResume, onOpenLink, onUndoChanges, sourcePath, supportsImages = true, providerName = "This provider" }) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState(message.body);
  const [editAttachments, setEditAttachments] = useState([]);
  const [editAttachmentError, setEditAttachmentError] = useState("");
  const copyResetTimerRef = useRef(null);

  useEffect(() => () => window.clearTimeout(copyResetTimerRef.current), []);

  if (message.kind === "turn-summary") {
    return <TurnSummaryCard message={message} onUndo={() => onUndoChanges(message)} />;
  }
  if (message.kind === "error") {
    return <div data-message-id={message.id} data-history-anchor={message.id}
      role="alert" aria-live="assertive"
      className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
      {message.body}
      {message.resumeAvailable && onResume ? <div className="mt-2"><button type="button" className="rounded border border-current px-2 py-1 text-xs focus-visible:outline-brand" onClick={onResume}>Resume task</button></div> : null}
    </div>;
  }
  const isSystem = message.role === "system" || message.kind === "system";
  const isUser = message.role === "user";
  // Rem's task threads say who wrote each instruction: Rem or the user. Both
  // reach the worker as user messages; here they must not look the same.
  const author = isUser && (message.sender === "rem" || message.sender === "user") ? message.sender : "";
  const fromRem = author === "rem";
  const headerButton = fromRem
    ? "grid h-5 w-5 place-items-center rounded text-muted outline-none transition hover:bg-brand/15 hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/50"
    : "grid h-5 w-5 place-items-center rounded text-white/80 outline-none transition hover:bg-white/15 hover:text-white focus-visible:ring-2 focus-visible:ring-white/70";

  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(message.body);
      setCopied(true);
      window.clearTimeout(copyResetTimerRef.current);
      copyResetTimerRef.current = window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function beginEditing() {
    setEditDraft(message.body);
    setEditAttachments(message.attachments || []);
    setEditAttachmentError("");
    setEditing(true);
  }

  function cancelEditing() {
    setEditDraft(message.body);
    setEditAttachments([]);
    setEditAttachmentError("");
    setEditing(false);
  }

  function saveEdit() {
    const body = editDraft.trim();
    if (!body && !editAttachments.length) return;
    onEdit?.(message.id, body, editAttachments);
    setEditing(false);
  }

  return (
    <div
      data-message-id={message.id}
      data-history-anchor={message.id}
      data-author={author || undefined}
      className={`flex ${
        isSystem ? "justify-center" : message.role === "user" ? "justify-end" : "justify-start"
      }`}
    >
      <div
        className={`${isSystem ? "max-w-[86%]" : "w-full min-w-0"} rounded-lg px-3 py-2 text-sm leading-6 ${
          isSystem
            ? "border border-line bg-slate-50 text-xs font-medium text-muted"
            : fromRem
            ? "border border-brand/40 bg-brand/10 text-ink"
            : message.role === "user"
            ? "bg-brand text-white"
            : "border border-line bg-white text-slate-700 shadow-sm"
        }`}
      >
        {isSystem ? (
          <span className="whitespace-pre-wrap break-words">{message.body}</span>
        ) : (
          <>
            {isUser ? (
              <div className="mb-1 flex h-5 items-center justify-end gap-0.5">
                {author ? (
                  <span className={`mr-auto inline-flex items-center gap-1 text-[11px] font-semibold ${fromRem ? "text-brand" : "text-white/90"}`}>
                    {fromRem ? <Bot aria-hidden="true" size={12} /> : <UserRound aria-hidden="true" size={12} />}
                    <span className="sr-only">Written by </span>{fromRem ? "Rem" : "You"}
                  </span>
                ) : null}
                {message.origin === "phone" && <span title="Sent from your phone" aria-label="Sent from your phone" className="mr-1 inline-flex"><Smartphone aria-hidden="true" size={12} /></span>}
                {canEdit && !fromRem ? (
                  <button
                    aria-label="Edit message"
                    className={headerButton}
                    title="Edit message"
                    type="button"
                    onClick={beginEditing}
                  >
                    <PencilLine aria-hidden="true" size={11} />
                  </button>
                ) : null}
                <button
                  aria-label={copied ? "Message copied" : "Copy message"}
                  className={headerButton}
                  title={copied ? "Message copied" : "Copy message"}
                  type="button"
                  onClick={() => void copyMessage()}
                >
                  {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
                </button>
              </div>
            ) : null}
            {isUser ? <MessageAttachments attachments={editing ? editAttachments : message.attachments} inverse={!fromRem} onOpenLink={onOpenLink} /> : null}
            {isUser && editing ? (
              <div className="space-y-2">
                <textarea
                  aria-label="Edit message text"
                  autoFocus
                  className="workflow-scrollbar max-h-48 min-h-20 w-full resize-y rounded-md border border-white/35 bg-white/10 px-2.5 py-2 text-sm leading-5 text-white outline-none placeholder:text-indigo-200 focus:border-white/70 focus:ring-2 focus:ring-white/25"
                  value={editDraft}
                  onPaste={event => {
                    const files = clipboardAttachmentFiles(event.clipboardData);
                    const textFile = largePasteFile(event.clipboardData?.getData?.("text/plain") || "");
                    if (!files.length && !textFile) return;
                    event.preventDefault();
                    event.stopPropagation();
                    const incoming = textFile ? [...files, textFile] : files;
                    const result = readChatAttachments(incoming.filter(file => supportsImages || !isChatImage(file)), editAttachments);
                    setEditAttachments(result.attachments);
                    setEditAttachmentError(!supportsImages && incoming.some(isChatImage)
                      ? `${providerName} can't accept images with this selection.` : result.error);
                  }}
                  onChange={(event) => setEditDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") cancelEditing();
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) saveEdit();
                  }}
                />
                {editAttachmentError ? <p role="alert">{editAttachmentError}</p> : null}
                <div className="flex justify-end gap-1.5">
                  <button
                    className="h-7 rounded-md px-2 text-xs font-medium text-white/80 outline-none transition hover:bg-white/15 hover:text-white focus-visible:ring-2 focus-visible:ring-white/70"
                    type="button"
                    onClick={cancelEditing}
                  >
                    Cancel
                  </button>
                  <button
                    className="h-7 rounded-md bg-white px-2.5 text-xs font-semibold text-brand outline-none transition hover:bg-indigo-50 focus-visible:ring-2 focus-visible:ring-white/70 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!editDraft.trim() && !editAttachments.length}
                    type="button"
                    onClick={saveEdit}
                  >
                    Send again
                  </button>
                </div>
              </div>
            ) : message.body ? (
              <>
                {message.kind === "search-source" ? <div className="whitespace-pre-wrap break-words">{message.body}</div>
                  : <>{message.interrupted ? <span className="mb-2 block text-xs text-muted">Partial response. Rem was interrupted.</span> : null}<MarkdownMessage inverse={isUser && !fromRem} loadWebImages={Boolean(message.sources?.length)} sourcePath={sourcePath} onOpenLink={onOpenLink} value={message.body} /></>}
                {!isUser && message.kind !== "streaming" && (message.sources?.length || /youtu\.?be/.test(message.body))
                  ? <Suspense fallback={null}><ChatSources body={message.body} sources={message.sources} /></Suspense> : null}
                {!isUser && message.kind !== "streaming" ? (
                  <div className="mt-1 flex justify-end border-t border-line/70 pt-1">
                    <button
                      aria-label={copied ? "Response copied" : "Copy response as Markdown"}
                      className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-[10px] leading-none text-muted outline-none transition hover:bg-slate-50 hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
                      title={copied ? "Response copied" : "Copy response as Markdown"}
                      type="button"
                      onClick={() => void copyMessage()}
                    >
                      {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
                      <span>{copied ? "Copied" : "Copy Markdown"}</span>
                    </button>
                  </div>
                ) : null}
              </>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
});

function TurnSummaryCard({ message, onUndo }) {
  const [reviewing, setReviewing] = useState(false);
  const [showAllFiles, setShowAllFiles] = useState(false);
  const changes = message.changes;
  const files = Array.isArray(changes?.files) ? changes.files : [];
  const visibleFiles = showAllFiles ? files : files.slice(0, 3);
  const liveDurationMs = useLiveDuration(message.startedAt, message.running);
  const timing = message.running
    ? `Running for ${formatAssistantDuration(liveDurationMs)}`
    : formatAssistantTurnTiming(message.completedAt, message.durationMs);

  if (!changes) {
    return (
      <div
        aria-label="Rem running"
        className="flex items-center gap-1.5 px-1 text-[10px] text-muted"
        data-message-id={message.id}
      data-history-anchor={message.id}
      >
        {message.running ? <Loader2 aria-hidden="true" className="animate-spin" size={11} /> : null}
        <span>{timing}</span>
      </div>
    );
  }

  return (
    <div className="space-y-1.5" data-message-id={message.id}>
      <section
        aria-label="Rem file changes"
        className="overflow-hidden rounded-lg border border-line bg-white dark:bg-[#181818]"
      >
        <div className="flex min-h-12 items-center gap-2.5 px-3 py-2">
          <FileDiff aria-hidden="true" className="shrink-0 text-muted" size={16} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                {message.running ? <Loader2 aria-hidden="true" className="animate-spin text-brand" size={12} /> : null}
                {message.running ? assistantLiveChangeLabel(files) : assistantChangeLabel(files)}
              </span>
              {files.some(file => !file.binary) ? <><span className="font-medium text-emerald-600">+{changes.additions ?? 0}</span><span className="font-medium text-red-500">-{changes.deletions ?? 0}</span></> : null}
              {files.some(file => file.binary) ? <span className="text-muted">{files.filter(file => file.binary).length} without line counts</span> : null}
            </div>
          </div>
          <button
            className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted outline-none transition hover:bg-slate-50 hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#242426]"
            disabled={message.running || !changes.undoable || changes.changing}
            title={!changes.undoable ? changes.undoUnavailableReason : undefined}
            type="button"
            onClick={onUndo}
          >
            {changes.changing ? (
              <Loader2 aria-hidden="true" className="animate-spin" size={13} />
            ) : changes.undone ? (
              <Redo2 aria-hidden="true" size={13} />
            ) : (
              <Undo2 aria-hidden="true" size={13} />
            )}
            {changes.changing ? (changes.undone ? "Redoing" : "Undoing") : changes.undone ? "Redo" : "Undo"}
          </button>
          <button
            aria-expanded={reviewing}
            className="h-8 rounded-md border border-line px-2.5 text-xs font-medium text-ink outline-none transition hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
            type="button"
            onClick={() => setReviewing((current) => !current)}
          >
            {reviewing ? "Close" : "Review"}
          </button>
        </div>
        <div className="border-t border-line px-3 py-2">
          <div className="space-y-1.5">
            {visibleFiles.map((file) => (
              <div className="flex min-w-0 items-center gap-2 text-[11px]" key={file.path}>
                <span className="min-w-0 flex-1 truncate text-muted" title={file.path}>{file.path}</span>
                {file.binary ? <span className="shrink-0 text-muted">{file.diff?.includes("too large") ? "Too large to count" : "Binary"}</span> : <><span className="shrink-0 font-medium text-emerald-600">+{file.additions ?? 0}</span><span className="shrink-0 font-medium text-red-500">-{file.deletions ?? 0}</span></>}
              </div>
            ))}
          </div>
          {files.length > 3 ? (
            <button
              className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted outline-none transition hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30"
              type="button"
              onClick={() => setShowAllFiles((current) => !current)}
            >
              {showAllFiles ? "Show fewer files" : `Show ${files.length - 3} more files`}
              <ChevronDown aria-hidden="true" className={`transition-transform ${showAllFiles ? "rotate-180" : ""}`} size={13} />
            </button>
          ) : null}
        </div>
        {reviewing ? (
          <div className="max-h-80 space-y-3 overflow-auto border-t border-line bg-slate-50 px-3 py-3 dark:bg-[#111113]">
            {files.map((file) => <AssistantDiffPreview file={file} key={file.path} />)}
          </div>
        ) : null}
        {changes.changeError ? (
          <p className="border-t border-red-200 bg-red-50 px-3 py-2 text-[11px] leading-4 text-red-700">
            {changes.changeError}
          </p>
        ) : null}
      </section>
      <div className="px-1 text-[10px] text-muted">{timing}</div>
    </div>
  );
}

function AssistantDiffPreview({ file }) {
  return (
    <section aria-label={`Diff for ${file.path}`}>
      <h4 className="mb-1.5 truncate font-mono text-[10px] font-semibold text-ink" title={file.path}>
        {file.path}
      </h4>
      <pre className="workflow-scrollbar overflow-x-auto rounded-md border border-line bg-white py-1 font-mono text-[10px] leading-4 dark:bg-[#181818]">
        {String(file.diff || "No text preview available.").split("\n").map((line, index) => (
          <span
            className={`block min-w-max px-2 ${
              line.startsWith("+") && !line.startsWith("+++")
                ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300"
                : line.startsWith("-") && !line.startsWith("---")
                ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300"
                : "text-slate-600 dark:text-[#b9b9b9]"
            }`}
            key={`${index}-${line}`}
          >
            {line || " "}
          </span>
        ))}
      </pre>
    </section>
  );
}

export function assistantChangeLabel(files) {
  const count = files.length;
  const noun = count === 1 ? "file" : "files";
  if (count && files.every((file) => file.status === "added")) return `Created ${count} ${noun}`;
  if (count && files.every((file) => file.status === "deleted")) return `Deleted ${count} ${noun}`;
  return `Edited ${count} ${noun}`;
}

function assistantLiveChangeLabel(files) {
  const count = files.length;
  const noun = count === 1 ? "file" : "files";
  if (count && files.every((file) => file.status === "added")) return `Creating ${count} ${noun}`;
  if (count && files.every((file) => file.status === "deleted")) return `Deleting ${count} ${noun}`;
  return `Editing ${count} ${noun}`;
}

function useLiveDuration(startedAt, running) {
  const [durationMs, setDurationMs] = useState(() => (
    running ? Math.max(0, Date.now() - Number(startedAt || Date.now())) : 0
  ));

  useEffect(() => {
    if (!running) return undefined;
    const update = () => setDurationMs(Math.max(0, Date.now() - Number(startedAt || Date.now())));
    update();
    const intervalId = window.setInterval(update, 1000);
    return () => window.clearInterval(intervalId);
  }, [running, startedAt]);

  return durationMs;
}

export function formatAssistantDuration(durationMs) {
  const milliseconds = Math.max(0, Number(durationMs) || 0);
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function formatAssistantTurnTiming(completedAt, durationMs) {
  const date = new Date(completedAt);
  const completed = Number.isNaN(date.getTime())
    ? "Completed"
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const milliseconds = Math.max(0, Number(durationMs) || 0);
  const seconds = Math.round(milliseconds / 1000);
  const duration = milliseconds < 1000
    ? "<1s"
    : formatAssistantDuration(seconds * 1000);
  return `${completed} · Ran for ${duration}`;
}

export function MarkdownMessage({ compact = false, inverse = false, loadWebImages = false, onOpenLink, sourcePath, value }) {
  return (
    <MarkdownContent
      compact={compact}
      inverse={inverse}
      loadWebImages={loadWebImages}
      value={value}
      onOpenRelativeLink={onOpenLink}
      sourcePath={sourcePath}
    />
  );
}

const NO_THOUGHTS = [];
// A `lazy` group has only a placeholder until expanding it loads `loaded.thoughts`.
export const ThoughtGroup = memo(function ThoughtGroup({ groupId, replyId, expanded, onOpenFile, onOpenLink, onToggle, sourcePath, thoughts: groupThoughts, searchMessageId, lazy, loaded }) {
  const thoughts = lazy ? loaded?.thoughts || NO_THOUGHTS : groupThoughts;
  const trace = useMemo(() => buildThoughtTrace(thoughts), [thoughts]);
  const count = trace.length;
  const match = thoughts.find(thought => String(thought.id) === String(searchMessageId));
  const matchKey = match?.trace?.id ? `trace-${match.groupId || ""}-${match.trace.id}` : match?.id;

  if (!count && (!lazy || loaded?.thoughts)) return null;

  return (
    <div className="flex justify-start" data-message-id={groupId}>
      <div className="w-full min-w-0 text-sm text-slate-700 dark:text-[#d4d4d4]">
        <button
          className="flex w-full items-center justify-between gap-3 rounded-md px-1 py-1.5 text-left transition hover:text-ink"
          type="button"
          aria-expanded={expanded}
          onClick={() => onToggle(groupId, replyId, expanded, !expanded && lazy && !loaded?.thoughts ? lazy : null)}
        >
          <span className="min-w-0 text-xs font-semibold text-ink">
            {expanded ? "Hide thoughts" : "Show thoughts"}
            <span className="ml-1.5 font-normal text-muted">{count || null}</span>
          </span>
          <span className="grid h-6 w-6 place-items-center rounded-md text-muted transition">
            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </span>
        </button>
        <div
          className={`grid transition-all duration-200 ease-out ${
            expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}
        >
          <div className="overflow-hidden">
            <div className="px-1 pb-1 pt-2">
              {expanded && !count ? <p className="text-xs text-muted" role={loaded?.error ? "alert" : "status"}>{loaded?.error || "Loading thoughts…"}</p> : null}
              {expanded && trace.map((entry, index) => (
                <div
                  key={entry.key}
                  data-history-anchor={`${entry.groupId || ""}:${entry.key}`}
                  data-thread-search-match={entry.key === matchKey ? "true" : undefined}
                  tabIndex={entry.key === matchKey ? -1 : undefined}
                  className={`relative ml-1 border-l border-line pl-5 ${
                    index === trace.length - 1 ? "pb-1" : "pb-4"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`absolute -left-[4.5px] top-1 h-2 w-2 rounded-full border border-white dark:border-[#18181a] ${
                      entry.kind === "tool" && entry.status !== "error"
                        ? "bg-emerald-500"
                        : entry.status === "error"
                        ? "bg-red-500"
                        : "bg-slate-400"
                    }`}
                  />
                  {shellTraceDetails(entry) ? (
                    <ShellCommandDisclosure entry={entry} initiallyExpanded={entry.key === matchKey} />
                  ) : editTraceDetails(entry) ? (
                    <EditDisclosure entry={entry} onOpenFile={onOpenFile} initiallyExpanded={entry.key === matchKey} />
                  ) : entry.kind === "tool" && (entry.input || entry.output) ? (
                    <ToolTraceDisclosure entry={entry} initiallyExpanded={entry.key === matchKey} />
                  ) : entry.kind === "tool" || !entry.body ? (
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-xs leading-5">
                      {entry.title ? (
                        <span className="font-semibold text-ink">{entry.title}</span>
                      ) : null}
                      {entry.detail ? (
                        <span className="min-w-0 break-words text-muted">{entry.detail}</span>
                      ) : null}
                    </div>
                  ) : null}
                  {entry.kind === "summary" && entry.body ? (
                    <div className="text-xs leading-5 text-slate-600 dark:text-[#b9b9b9]">
                      <MarkdownMessage compact sourcePath={sourcePath} onOpenLink={onOpenLink} value={entry.body} />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});

function ToolTraceDisclosure({ entry, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const detail = toolTraceDisclosureDetail(entry);
  const isSearch = entry.category === "search"
    || String(entry.title || "").trim().toLowerCase().includes("search");

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group -ml-1 flex min-h-7 w-[calc(100%+0.25rem)] items-center gap-2 rounded-md px-1 text-left text-xs outline-none transition-colors hover:bg-slate-100/70 focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="shrink-0 font-semibold text-ink group-hover:text-brand">
          {entry.title || "Tool"}
        </span>
        {detail && (!isSearch || expanded) ? (
          <span className="min-w-0 flex-1 truncate text-muted" title={detail}>{detail}</span>
        ) : (
          <span className="flex-1" />
        )}
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 group-hover:text-ink ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            <div className="mt-1 overflow-hidden rounded-md border border-line bg-white dark:bg-[#181818]">
              {entry.input ? <TracePayload label="In" value={entry.input} /> : null}
              {entry.output ? <TracePayload label="Out" value={entry.output} divided={Boolean(entry.input)} /> : null}
            </div>
          </> : null}
        </div>
      </div>
    </div>
  );
}

export function toolTraceDisclosureDetail(entry) {
  const detail = String(entry?.detail || "").trim();
  if (detail) return detail;

  const title = String(entry?.title || "").trim().toLowerCase();
  if (entry?.category !== "search" && !title.includes("search")) return "";

  const input = String(entry?.input || "").trim();
  if (!input) return "";
  try {
    const parsed = JSON.parse(input);
    const query = parsed?.query
      ?? parsed?.q
      ?? parsed?.search_query?.[0]?.q
      ?? parsed?.image_query?.[0]?.q;
    return typeof query === "string" ? query.trim() : "";
  } catch {
    return input.includes("\n") ? "" : input;
  }
}

function EditDisclosure({ entry, onOpenFile, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const details = editTraceDetails(entry);
  if (!details) return null;

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group flex min-h-7 max-w-full items-center gap-1 rounded-md pr-1 text-left text-xs font-semibold text-ink outline-none transition hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/30"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="truncate">Editing files</span>
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            {details.path ? (
              <button
                aria-label={`Open ${details.path} in code editor`}
                className="mt-0.5 block w-full min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-left text-[10px] text-muted outline-none transition hover:text-brand focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-brand/30"
                style={{ direction: "rtl" }}
                title={details.path}
                type="button"
                onClick={() => onOpenFile?.(details.path)}
              >
                {details.path}
              </button>
            ) : null}
            <pre className="workflow-scrollbar mt-1 max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-2.5 py-2 font-mono text-[10px] leading-4 text-slate-600 dark:bg-[#111113] dark:text-[#b9b9b9]">
              {details.input || "Waiting for edit details..."}
            </pre>
          </> : null}
        </div>
      </div>
    </div>
  );
}

export function editTraceDetails(entry) {
  if (!entry || entry.kind !== "tool" || String(entry.title || "").trim().toLowerCase() !== "edit") {
    return null;
  }
  let path = String(entry.detail || "").trim();
  const input = String(entry.input || "").trim();
  if (!path && input) {
    try {
      const parsed = JSON.parse(input);
      path = String(parsed?.path || parsed?.file_path || "").trim();
    } catch {
      path = "";
    }
  }
  return { input, path };
}

function ShellCommandDisclosure({ entry, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const details = shellTraceDetails(entry);
  if (!details) return null;

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group flex min-h-7 max-w-full items-center gap-1 rounded-md pr-1 text-left text-xs font-semibold text-ink outline-none transition hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/30"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="truncate">Running {details.shell} commands</span>
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            <pre className="workflow-scrollbar mt-1 max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-2.5 py-2 font-mono text-[10px] leading-4 text-slate-600 dark:bg-[#111113] dark:text-[#b9b9b9]">
              {details.command || "Waiting for command..."}
            </pre>
          </> : null}
        </div>
      </div>
    </div>
  );
}


export function shellTraceDetails(entry) {
  if (!entry || entry.kind !== "tool") return null;
  const title = String(entry.title || "").trim();
  const normalizedTitle = title.toLowerCase();
  const shellTitles = new Map([
    ["bash", "bash"],
    ["sh", "sh"],
    ["zsh", "zsh"],
    ["fish", "fish"],
    ["powershell", "PowerShell"],
    ["pwsh", "PowerShell"],
    ["cmd", "Command Prompt"],
    ["command prompt", "Command Prompt"],
    ["shell", "shell"],
    ["terminal", "terminal"],
  ]);
  if (entry.category !== "shell" && !shellTitles.has(normalizedTitle)) return null;

  let command = String(entry.command || "").trim();
  if (!command && entry.input) {
    try {
      const parsed = JSON.parse(entry.input);
      command = String(parsed?.command || parsed?.cmd || parsed?.script || "").trim();
    } catch {
      command = String(entry.input).trim();
    }
  }
  return {
    command,
    shell: String(entry.shell || shellTitles.get(normalizedTitle) || "shell"),
  };
}

function TracePayload({ divided = false, label, value }) {
  return (
    <div className={`grid grid-cols-[2rem_minmax(0,1fr)] gap-2 px-2.5 py-2 ${divided ? "border-t border-line" : ""}`}>
      <span className="pt-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <pre className="workflow-scrollbar max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-4 text-slate-600 dark:text-[#b9b9b9]">
        {value}
      </pre>
    </div>
  );
}
