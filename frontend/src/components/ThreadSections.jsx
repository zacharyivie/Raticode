import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Archive, Check, CheckCheck, ChevronRight, Loader2, MoreVertical, Pin, Trash2, X } from "lucide-react";
import { archiveRetentionDays, cachedThreadScopes, inspectThreadScopes, organizeThreads, READ_THREAD_LIMIT, threadIndexEntry, threadScopeBadge, threadScopeKey } from "../lib/threadActivity.js";
import { emptyThreadSelection, nextThreadSelection, presentThreadSelection, selectThreadSection, threadSelectionActions } from "../lib/threadSelection.js";

// Thread history helpers come from App so this chunk doesn't import it back.
const defaultFormatDate = value => value || "No messages yet";

export function ThreadSections({ threads, activityByThread = {}, launchedThreadId, onLaunchAnimationEnd, onClearArchive, onThreadsAction, archiveRetention = "never", threadIndex = () => [], loadStoredThread = () => null, ...props }) {
  const [runningOpen, setRunningOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveCount, setArchiveCount] = useState(10);
  const [selection, setSelection] = useState(emptyThreadSelection);
  const [menu, setMenu] = useState(null);
  const [scopeState, setScopeState] = useState(() => cachedThreadScopes(window.goferDesktop?.workspace));
  const entries = useMemo(() => [...new Map([...threadIndex(), ...threads.map(threadIndexEntry)].map(entry => [entry.id, entry])).values()].filter(entry => entry.managedRole !== "verify"), [threads, threadIndex]);
  const scopeKey = threadScopeKey(entries);
  useEffect(() => {
    let cancelled = false;
    let running = false;
    const refresh = async (force = false) => {
      if (running) return;
      running = true;
      const result = await inspectThreadScopes(JSON.parse(scopeKey), window.goferDesktop?.workspace, { force: Boolean(force) });
      running = false;
      if (!cancelled) setScopeState(result);
    };
    void refresh();
    const timer = window.setInterval(() => refresh(true), 60000);
    window.addEventListener("focus", refresh);
    window.addEventListener("gofer:git-files-changed", refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("gofer:git-files-changed", refresh);
    };
  }, [scopeKey]);
  const activityIds = state => new Set(Object.keys(activityByThread).filter(id => state(activityByThread[id])));
  const groups = organizeThreads(entries, {
    running: activityIds(state => state?.sending || state?.deviceRunning),
    unread: activityIds(state => state?.hasNewResponse),
    ...scopeState,
  });
  const loaded = useMemo(() => new Map(threads.map(thread => [thread.id, thread])), [threads]);
  const rowCache = useRef(new Map());
  const loadThread = entry => {
    if (loaded.has(entry.id)) return loaded.get(entry.id);
    const cached = rowCache.current.get(entry.id);
    if (cached?.entry === entry) return cached.thread;
    const thread = loadStoredThread(entry.id);
    rowCache.current.set(entry.id, { entry, thread });
    return thread;
  };
  const isUnread = thread => Boolean(thread.unread || activityByThread[thread.id]?.hasNewResponse);
  const present = new Set(entries.map(entry => entry.id));
  const current = presentThreadSelection(selection, present);
  const selectedEntries = entries.filter(entry => current.ids.has(entry.id));
  // Display order for Shift ranges: only threads in open sections.
  const order = [...groups.pinned, ...groups.unread, ...groups.read, ...(runningOpen ? groups.running : []),
    ...(archiveOpen ? groups.archived.slice(0, archiveCount) : [])].map(entry => entry.id);
  const clearSelection = () => { setSelection(emptyThreadSelection); setMenu(null); };
  const select = (id, event) => {
    const toggle = event.ctrlKey || event.metaKey;
    if (!toggle && !event.shiftKey) { if (current.ids.size) clearSelection(); return false; }
    setSelection(nextThreadSelection(current, id, order, { toggle, range: event.shiftKey }));
    return true;
  };
  const openMenu = (id, point) => {
    if (id && !current.ids.has(id)) setSelection({ ids: new Set([id]), anchor: id });
    setMenu(point);
  };
  const run = (action, ids) => {
    // Threads usually change sections after an action, so the selection ends with it.
    clearSelection();
    onThreadsAction?.(action, ids);
  };
  const selectSection = (name, list) => {
    if (name === "running") setRunningOpen(true);
    if (name === "archived") setArchiveOpen(true);
    setSelection(selectThreadSection(current, list.map(entry => entry.id)));
  };
  const sectionProps = (name, list) => ({
    onSelectAll: onThreadsAction && list.length ? () => selectSection(name, list) : undefined,
    allSelected: list.length > 0 && list.every(entry => current.ids.has(entry.id)),
  });
  const listProps = { ...props, activityByThread, launchedThreadId, onLaunchAnimationEnd, loadThread, sort: false,
    selectedIds: current.ids, onSelect: onThreadsAction ? select : undefined, onMenu: onThreadsAction ? openMenu : undefined,
    onMarkRead: onThreadsAction ? (id, read) => run(read ? "read" : "unread", [id]) : undefined };
  const runningLaunch = !runningOpen && groups.running.some(entry => entry.id === launchedThreadId);
  const clearable = groups.archived.filter(entry => !entry.managedRole);
  const visibleCount = groups.pinned.length + groups.unread.length + groups.read.length;
  const actions = threadSelectionActions(selectedEntries.map(entry => loadThread(entry) || entry), { isUnread });
  const ids = selectedEntries.map(entry => entry.id);
  const quick = actions.filter(item => item.action === "read" || item.action === "archive");
  return <div className="rem-thread-sections" onKeyDown={event => { if (event.key === "Escape" && current.ids.size && !menu) clearSelection(); }}>
    {current.ids.size ? <div role="toolbar" aria-label="Selected threads" className="rem-thread-selection">
      <span role="status">{current.ids.size} selected</span>
      {quick.map(item => <button key={item.action} type="button" onClick={() => run(item.action, ids)}>
        {item.action === "read" ? <CheckCheck aria-hidden="true" size={12} /> : <Archive aria-hidden="true" size={12} />}{item.label}
      </button>)}
      <button type="button" aria-haspopup="menu" aria-expanded={Boolean(menu)} onClick={event => {
        const box = event.currentTarget.getBoundingClientRect?.() || { left: 0, bottom: 0 };
        openMenu(null, { x: box.left, y: box.bottom + 4 });
      }}>More actions</button>
      <button type="button" className="rem-thread-selection-clear" aria-label="Clear selection" title="Clear selection (Esc)" onClick={clearSelection}><X aria-hidden="true" size={13} /></button>
    </div> : null}
    {menu && actions.length ? <ThreadSelectionMenu point={menu} count={ids.length} actions={actions}
      onAction={action => run(action, ids)} onClose={() => setMenu(null)} /> : null}
    {groups.pinned.length ? <ThreadGroup label="Pinned" count={groups.pinned.length} icon={<Pin aria-hidden="true" size={12} />} {...sectionProps("pinned", groups.pinned)}>
      <ThreadList {...listProps} threads={groups.pinned} />
    </ThreadGroup> : null}
    {groups.unread.length ? <ThreadGroup label="Unread" count={groups.unread.length}
      icon={<span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-blue-500" />} {...sectionProps("unread", groups.unread)}
      action={onThreadsAction ? <button type="button" className="rem-thread-group-action" title="Mark all as read"
        aria-label={`Mark all ${groups.unread.length} unread ${groups.unread.length === 1 ? "thread" : "threads"} as read`}
        onClick={() => run("read", groups.unread.map(entry => entry.id))}><CheckCheck aria-hidden="true" size={12} />Mark all read</button> : null}>
      <ThreadList {...listProps} threads={groups.unread} />
    </ThreadGroup> : null}
    {groups.read.length || !visibleCount ? <ThreadGroup label="Read" count={groups.read.length} icon={<Check aria-hidden="true" size={12} />}
      description={`Your ${READ_THREAD_LIMIT} most recently read threads. Older ones move to the archive.`} {...sectionProps("read", groups.read)}>
      <ThreadList {...listProps} threads={groups.read} pageSize={READ_THREAD_LIMIT}
        emptyText={groups.running.length || groups.archived.length ? "No read threads." : "No thread history yet."} />
    </ThreadGroup> : null}
    {groups.running.length ? <ThreadGroup label="Running" count={groups.running.length} open={runningOpen}
      launching={runningLaunch} onAnimationEnd={runningLaunch ? onLaunchAnimationEnd : undefined}
      icon={<Loader2 aria-hidden="true" className="text-brand motion-safe:animate-spin" size={12} />}
      onToggle={() => setRunningOpen(open => !open)} {...sectionProps("running", groups.running)}>
      <ThreadList {...listProps} threads={groups.running} />
    </ThreadGroup> : null}
    <ThreadGroup label="Archived" count={groups.archived.length} open={archiveOpen}
      icon={<Archive aria-hidden="true" size={12} />}
      onToggle={() => { setArchiveOpen(open => !open); setArchiveCount(10); }} {...sectionProps("archived", groups.archived)}>
      {groups.archived.length ? <div className="rem-archive-toolbar">
        <span>{archiveRetentionDays(archiveRetention) ? `Deleted after ${archiveRetentionDays(archiveRetention)} days without activity` : "Kept until you clear them"}</span>
        {clearable.length && onClearArchive ? <button type="button"
          className="flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted hover:bg-red-50 hover:text-red-600 focus-visible:outline-brand"
          title="Delete every archived thread" aria-label={`Clear archive, ${clearable.length} ${clearable.length === 1 ? "thread" : "threads"}`}
          onClick={() => onClearArchive(clearable)}>
          <Trash2 aria-hidden="true" size={12} />Clear archive
        </button> : null}
      </div> : null}
      <ThreadList {...listProps} archived key="archive" threads={groups.archived.slice(0, archiveCount)}
        pageSize={10} totalCount={groups.archived.length} onLoadOlder={setArchiveCount} emptyText="The archive is empty." />
    </ThreadGroup>
  </div>;
}

// Actions for the selected threads, opened by right-click, the context-menu
// key, Shift+F10 or the selection toolbar.
function ThreadSelectionMenu({ point, count, actions, onAction, onClose }) {
  const root = useRef(null);
  const [position, setPosition] = useState(point);
  useLayoutEffect(() => {
    const box = root.current?.getBoundingClientRect?.();
    const width = window.innerWidth || 0, height = window.innerHeight || 0;
    setPosition({ x: box && width ? Math.max(4, Math.min(point.x, width - box.width - 4)) : point.x,
      y: box && height ? Math.max(4, Math.min(point.y, height - box.height - 4)) : point.y });
    root.current?.querySelector?.("[role=menuitem]")?.focus?.();
  }, [point]);
  useEffect(() => {
    const dismiss = event => { if (!root.current?.contains(event.target)) onClose(); };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("blur", onClose);
    return () => { window.removeEventListener("pointerdown", dismiss); window.removeEventListener("blur", onClose); };
  }, [onClose]);
  const move = (event, step) => {
    const items = [...(root.current?.querySelectorAll?.("[role=menuitem]") || [])];
    const index = items.indexOf(document.activeElement);
    items[(index + step + items.length) % items.length]?.focus();
    event.preventDefault();
  };
  return <div ref={root} role="menu" aria-label={`Actions for ${count} selected ${count === 1 ? "thread" : "threads"}`}
    className="rem-thread-menu" style={{ left: position.x, top: position.y }}
    onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
      else if (event.key === "ArrowDown") move(event, 1);
      else if (event.key === "ArrowUp") move(event, -1);
    }}>
    <div className="rem-thread-menu-heading" aria-hidden="true">{count} {count === 1 ? "thread" : "threads"}</div>
    {actions.map(item => <button key={item.action} type="button" role="menuitem" data-danger={item.danger || undefined}
      onClick={() => onAction(item.action)}>{item.label}</button>)}
  </div>;
}

// Collapsible groups keep their header visible so their count and status stay
// readable. Activating the name selects every thread in the group; the
// chevron opens and closes it.
function ThreadGroup({ label, count, icon = null, description, open, onToggle, onSelectAll, allSelected = false, action = null, launching = false, onAnimationEnd, children }) {
  const collapsible = Boolean(onToggle);
  const bodyId = useId();
  const noun = `${label.toLowerCase()} ${count === 1 ? "thread" : "threads"}`;
  const heading = <>
    <span className="grid h-3.5 w-3.5 shrink-0 place-items-center">{icon}</span>
    <span>{label}</span>
    <span className="rem-thread-count">{count}<span className="sr-only">{count === 1 ? " thread" : " threads"}</span></span>
  </>;
  return <section aria-label={`${label} threads`} className="rem-thread-group" data-thread-group={label.toLowerCase()}>
    <div className={`rem-thread-group-header ${launching ? "rem-thread-launch" : ""}`} onAnimationEnd={onAnimationEnd}>
      {onSelectAll
        ? <button type="button" className="rem-thread-group-toggle" aria-pressed={allSelected}
          aria-label={`${label}, ${count} ${count === 1 ? "thread" : "threads"}. ${allSelected ? "Clear selection" : `Select all ${noun}`}`}
          title={description || `Select all ${noun}`} onClick={onSelectAll}>{heading}</button>
        : <h3 className="rem-thread-group-title" title={description}>{heading}</h3>}
      {action}
      {collapsible ? <button type="button" aria-expanded={Boolean(open)} aria-controls={bodyId}
        aria-label={`${open ? "Hide" : "Show"} ${label.toLowerCase()} threads`} className="rem-thread-group-chevron" onClick={onToggle}>
        <ChevronRight aria-hidden="true" size={13} className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      </button> : null}
    </div>
    {!collapsible || open ? <div id={bodyId}>{children}</div> : null}
  </section>;
}

function ThreadActions({ thread, unread, onPin, onRename, onDelete, onMarkRead }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const trigger = useRef(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", dismiss); window.removeEventListener("keydown", escape); };
  }, [open]);
  const item = (label, action, className = "text-ink hover:bg-slate-50") => <button type="button" className={`w-full rounded px-2 py-2 text-left text-xs ${className}`}
    onClick={() => { setOpen(false); trigger.current?.focus(); action(); }}>{label}</button>;
  return <div ref={root} className="relative" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button ref={trigger} title="Thread options" aria-label={`Thread options for ${thread.title}`} aria-expanded={open}
      className="grid h-8 w-8 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
      type="button" onClick={() => setOpen(value => !value)}><MoreVertical aria-hidden="true" size={15} /></button>
    {open ? <div aria-label={`Actions for ${thread.title}`} className="absolute right-0 top-8 z-50 w-36 rounded-lg border border-line bg-white p-1 shadow-panel">
      {item("Rename Thread", () => onRename?.(thread))}
      {onMarkRead ? item(unread ? "Mark as read" : "Mark as unread", () => onMarkRead(thread.id, unread)) : null}
      {item(thread.pinned ? "Unpin thread" : "Pin thread", () => onPin?.(thread.id, !thread.pinned))}
      {item("Delete thread", () => onDelete(thread.id), "text-red-600 hover:bg-red-50")}
    </div> : null}
  </div>;
}

export function ThreadList({ launchedThreadId, onLaunchAnimationEnd, activeThreadId, activityByThread = {}, onArchive, onPin, onRename, onDelete, onOpen, onSelect, onMenu, onMarkRead, selectedIds = emptyThreadSelection.ids, formatDate = defaultFormatDate, archived = false, threads, totalCount = threads.length, onLoadOlder, pageSize = 15, loadThread = entry => entry, sort = true, emptyText = "No thread history yet." }) {
  const [visibleCount, setVisibleCount] = useState(pageSize);
  const sortedThreads = sort ? [...threads].sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""))) : threads;
  if (threads.length) {
    return (
      <div className="space-y-1">
          {sortedThreads.slice(0, visibleCount).map(loadThread).filter(Boolean).map((thread) => {
            const state = activityByThread[thread.id];
            const running = Boolean(state?.sending || state?.deviceRunning);
            const unread = Boolean(thread.unread || state?.hasNewResponse);
            const selected = selectedIds.has(thread.id);
            return <div
              key={thread.id}
              data-thread-id={thread.id}
              data-thread-selected={selected || undefined}
              data-thread-launching={thread.id === launchedThreadId || undefined}
              onAnimationEnd={thread.id === launchedThreadId ? onLaunchAnimationEnd : undefined}
              onContextMenu={onMenu ? event => { event.preventDefault(); onMenu(thread.id, { x: event.clientX, y: event.clientY }); } : undefined}
              className={`group flex items-center gap-1 rounded-lg px-1 transition ${thread.id === launchedThreadId ? "rem-thread-launch" : ""} ${
                selected ? "rem-thread-selected" : thread.id === activeThreadId ? "bg-indigo-50" : "hover:bg-slate-50"
              }`}
            >
              <button
                className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left"
                type="button"
                title={onSelect ? "Ctrl or Shift-click to select, right-click for actions" : undefined}
                onClick={event => { if (!onSelect?.(thread.id, event)) onOpen(thread.id); }}
                onKeyDown={onMenu ? event => {
                  if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                  event.preventDefault();
                  const box = event.currentTarget.getBoundingClientRect?.() || { left: 0, bottom: 0 };
                  onMenu(thread.id, { x: box.left + 16, y: box.bottom });
                } : undefined}
              >
                <div className="min-w-0 flex-1">
                  <div className={`truncate text-xs text-ink ${unread ? "font-semibold" : "font-medium"}`}>
                    {thread.title}
                  </div>
                  <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[10px] text-muted">
                    <span className="shrink-0">{formatDate(thread.updatedAt)}</span>
                    <ThreadScopeBadge thread={thread} />
                  </div>
                </div>
                {selected ? <span className="sr-only">Selected</span> : null}
                <ThreadActivityIndicator state={state} unread={thread.unread} />
              </button>
              {onMarkRead && unread && !running ? <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
                title="Mark as read" aria-label={`Mark ${thread.title} as read`} type="button"
                onClick={() => onMarkRead(thread.id, true)}>
                <CheckCheck aria-hidden="true" size={15} />
              </button> : null}
              {archived ? <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-red-50 hover:text-red-600"
                title="Delete thread" aria-label={`Delete thread ${thread.title}`} type="button"
                onClick={() => onDelete(thread.id)}>
                <Trash2 aria-hidden="true" size={15} />
              </button> : <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
                title="Archive thread" aria-label={`Archive thread ${thread.title}`} type="button"
                onClick={() => onArchive?.(thread.id)}>
                <Archive aria-hidden="true" size={15} />
              </button>}
              <ThreadActions thread={thread} unread={unread} onPin={onPin} onRename={onRename} onDelete={onDelete} onMarkRead={onMarkRead} />
            </div>;
          })}
          {totalCount > visibleCount ? (
            <button className="w-full rounded-md px-2 py-2 text-left text-xs text-muted hover:bg-slate-50 focus-visible:outline" type="button" onClick={() => { const nextCount = visibleCount + pageSize; onLoadOlder?.(nextCount); setVisibleCount(nextCount); }}>
              Show older threads ({totalCount - visibleCount})
            </button>
          ) : null}
          {visibleCount > pageSize ? <button className="px-2 py-2 text-xs text-muted" type="button" onClick={() => setVisibleCount(pageSize)}>Collapse older threads</button> : null}
      </div>
    );
  }

  return (
    <p className="px-2 py-4 text-center text-xs text-muted">{emptyText}</p>
  );
}

function ThreadScopeBadge({ thread }) {
  const badge = threadScopeBadge(thread);
  return badge ? <span className="rem-thread-scope" data-thread-scope={thread.scopeMode === "global" ? "global" : "project"} title={badge.title}>
    <span className="sr-only">Scope: </span>{badge.label}
  </span> : null;
}

function ThreadActivityIndicator({ state, unread = false }) {
  if (state?.sending || state?.deviceRunning) {
    return (
      <span
        className="grid h-4 w-4 shrink-0 place-items-center text-brand"
        title="Rem response running"
      >
        <Loader2 aria-hidden="true" className="animate-spin" size={13} />
        <span className="sr-only">Running</span>
      </span>
    );
  }

  if (state?.hasNewResponse || unread) {
    return (
      <span
        className="grid h-4 w-4 shrink-0 place-items-center"
        title="Rem response complete"
      >
        <span aria-hidden="true" className="h-2 w-2 rounded-full bg-blue-500" />
        <span className="sr-only">Completed</span>
      </span>
    );
  }

  return null;
}
