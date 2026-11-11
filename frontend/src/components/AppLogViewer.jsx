import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_LEVELS,
  LOG_LEVELS,
  appendEntries,
  countLevels,
  entryAsText,
  entryKey,
  firstLine,
  formatLogTime,
  groupRepeats,
  keyRange,
  levelName,
  logTimeParts,
  logSelectionForRem,
  matchesFilter,
  selectionAsText,
  sourceName,
  sourcesOf,
  uniqueEntries,
  withHistoryKeys,
} from "../lib/appLog.js";
import { askRemAboutText } from "../lib/fileAttachments.js";
import WorktreeContextMenu from "./WorktreeContextMenu.jsx";

const POLL_MS = 1000;
const PAGE = 200;
// Dragging within this distance of the list's top or bottom edge scrolls it.
const EDGE = 28;
const EMPTY = new Set();

function ancestor(node, test) {
  for (let current = node; current; current = current.parentNode) if (test(current)) return current;
  return null;
}
function rowKeyFrom(target) {
  return ancestor(target, (node) => node.getAttribute?.("data-log-key"))?.getAttribute("data-log-key") || null;
}
// The details toggle and text inside an opened entry keep their own click behavior.
function ownsPointer(target) {
  return Boolean(ancestor(target, (node) => node.getAttribute?.("data-log-toggle") || String(node.getAttribute?.("class") || "").split(" ").includes("app-log-detail")));
}

// One log entry. Long single lines wrap. Entries with more lines, such as a
// traceback, open to show the rest. The source's tooltip names the code that logged it.
// A repeated message shows its newest entry with the count, which opens the group
// to show each entry with its own time and details.
function LogRow({ entry, rowKey, rowId, selected, active, expanded, onToggle, repeats = 0, oldest = null, member = false }) {
  const summary = firstLine(entry.message);
  const rest = entry.message.slice(summary.length).replace(/^\n/, "");
  const lineCount = rest ? rest.split("\n").length : 0;
  // A group's own toggle opens its entries; each entry then opens its own lines.
  const expandable = Boolean(rest) && !repeats;
  const { day, time } = logTimeParts(entry.time);
  const span = repeats ? `${repeats} times, ${formatLogTime(oldest.time)} to ${formatLogTime(entry.time)}` : "";
  return <div id={rowId} role="option" aria-selected={selected} data-log-key={rowKey} aria-expanded={repeats ? expanded : undefined}
    className={`app-log-row app-log-row-${entry.level}${selected ? " app-log-row-selected" : ""}${active ? " app-log-row-active" : ""}${member ? " app-log-row-member" : ""}`}>
    <div className="app-log-line">
      <time className="app-log-time" dateTime={entry.time} title={repeats ? `Newest of ${span}` : entry.time}>{day ? <span className="app-log-day">{day}</span> : null}<span>{time}</span></time>
      <span className={`app-log-level app-log-level-${entry.level}`}>{levelName(entry.level)}</span>
      <span className="app-log-source" title={entry.component ? `Logged by ${entry.component}` : undefined}>{sourceName(entry.source)}</span>
      <span className="app-log-message">{repeats ? <button type="button" tabIndex={-1} className="app-log-repeat" aria-expanded={expanded} data-log-toggle="true" onClick={onToggle}
        title={`${span}. ${expanded ? "Hide" : "Show"} each entry.`} aria-label={`Repeated ${span}. ${expanded ? "Hide" : "Show"} each entry`}>
        <span aria-hidden="true" className="app-log-repeat-caret">{expanded ? "▾" : "▸"}</span>{repeats}
      </button> : null}{summary}{expandable ? <button type="button" tabIndex={-1} className="app-log-more" aria-expanded={expanded} data-log-toggle="true" onClick={onToggle}>
        {expanded ? "Hide lines" : `+${lineCount} ${lineCount === 1 ? "line" : "lines"}`}
      </button> : null}</span>
    </div>
    {expandable && expanded ? <div className="app-log-detail">
      {entry.component ? <p className="app-log-component">Logged by {entry.component}</p> : null}
      <pre>{rest}</pre>
    </div> : null}
  </div>;
}

export default function AppLogViewer({ bridge, logFile, version = "", platform = "" }) {
  const [live, setLive] = useState([]);
  const [history, setHistory] = useState(null);
  const [missed, setMissed] = useState(0);
  const [error, setError] = useState("");
  const [levels, setLevels] = useState(DEFAULT_LEVELS);
  const [source, setSource] = useState("all");
  const [query, setQuery] = useState("");
  const [pausedAt, setPausedAt] = useState(null);
  const [shown, setShown] = useState(PAGE);
  const [showHistory, setShowHistory] = useState(true);
  const [notice, setNotice] = useState("");
  // Selection is kept by entry, so rows stay selected while filters and new entries move them.
  const [selected, setSelected] = useState(EMPTY);
  const [anchor, setAnchor] = useState(null);
  const [activeKey, setActiveKey] = useState(null);
  const [expanded, setExpanded] = useState(EMPTY);
  const [menu, setMenu] = useState(null);
  // Selecting pauses the list so rows don't move under the pointer; clearing resumes it.
  const autoPaused = useRef(false);
  const drag = useRef(null);
  const listRef = useRef(null);
  const cursor = useRef(0);

  const poll = useCallback(async (first = false) => {
    if (!bridge?.entries) return;
    try {
      for (let round = 0; round < 5; round += 1) {
        const result = await bridge.entries({ after: cursor.current, limit: 1000, history: first && round === 0 });
        if (first && round === 0) {
          const loaded = result.history || { entries: [], truncated: false };
          setHistory({ ...loaded, entries: withHistoryKeys(loaded.entries) });
        }
        if (result.missed) setMissed((count) => count + result.missed);
        if (result.entries?.length) setLive((current) => appendEntries(current, result.entries));
        cursor.current = result.cursor ?? cursor.current;
        setError("");
        if (!result.more) break;
      }
    } catch (cause) {
      setError(cause?.message || "The log could not be read.");
    }
  }, [bridge]);

  useEffect(() => {
    if (!bridge?.entries) return undefined;
    let stopped = false;
    let timer;
    const tick = async (first) => {
      if (stopped) return;
      if (first || globalThis.document?.visibilityState !== "hidden") await poll(first);
      if (!stopped) timer = window.setTimeout(() => void tick(false), POLL_MS);
    };
    void tick(true);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [bridge, poll]);

  const visibleLive = useMemo(() => (pausedAt === null ? live : live.filter((entry) => entry.seq <= pausedAt)), [live, pausedAt]);
  const waiting = pausedAt === null ? 0 : live.length - visibleLive.length;
  const earlier = useMemo(() => (showHistory ? history?.entries || [] : []), [showHistory, history]);
  const matchingLive = useMemo(() => visibleLive.filter((entry) => matchesFilter(entry, { levels, source, query })).reverse(), [visibleLive, levels, source, query]);
  const matchingHistory = useMemo(() => earlier.filter((entry) => matchesFilter(entry, { levels, source, query })).reverse(), [earlier, levels, source, query]);
  const counts = useMemo(() => countLevels(visibleLive, earlier), [visibleLive, earlier]);
  const sources = useMemo(() => sourcesOf(live, history?.entries), [live, history]);
  const total = matchingLive.length + matchingHistory.length;
  // Repeats of one message in a row share a row; pages count rows, not entries.
  const liveGroups = useMemo(() => groupRepeats(matchingLive), [matchingLive]);
  const historyGroups = useMemo(() => groupRepeats(matchingHistory), [matchingHistory]);
  const liveShown = liveGroups.slice(0, shown);
  const historyShown = historyGroups.slice(0, Math.max(0, shown - liveShown.length));
  const shownEntries = [...liveShown, ...historyShown].reduce((sum, group) => sum + group.entries.length, 0);
  const more = liveGroups.length + historyGroups.length > liveShown.length + historyShown.length;
  // Rows in display order, newest first: each group, then its entries while it is
  // open. Selection ranges follow this order; a selected group stands for all its entries.
  const rows = [];
  for (const group of [...liveShown, ...historyShown]) {
    rows.push(group);
    if (group.entries.length > 1 && expanded.has(group.key)) for (const entry of group.entries) rows.push({ key: entryKey(entry), entry, entries: [entry] });
  }
  const order = rows.map((row) => row.key);
  const byKey = new Map(rows.map((row) => [row.key, row]));
  // Actions use selected rows that are still shown, each entry once.
  const chosen = uniqueEntries(rows.filter((row) => selected.has(row.key)).flatMap((row) => row.entries));
  const rowId = (key) => `app-log-row-${key}`;

  const flash = (text) => { setNotice(text); window.setTimeout(() => setNotice(""), 2500); };
  const count = (n) => `${n} ${n === 1 ? "entry" : "entries"}`;

  function select(keys, { from = anchor, active } = {}) {
    if (keys.length && pausedAt === null) { setPausedAt(live.at(-1)?.seq ?? 0); autoPaused.current = true; }
    setSelected(new Set(keys));
    if (from !== undefined) setAnchor(from);
    if (active) setActiveKey(active);
  }

  function clearSelection() {
    setSelected(EMPTY);
    setAnchor(null);
    if (autoPaused.current) { autoPaused.current = false; setPausedAt(null); }
  }

  async function copy(entries, what) {
    try {
      await navigator.clipboard.writeText(what === "shown" ? [...entries].reverse().map(entryAsText).join("\n") : selectionAsText(entries));
      flash(`Copied ${count(entries.length)}`);
    } catch { flash("Copy failed"); }
  }

  // Attaches the entries and puts the cursor in Rem's message box; the person
  // writes the question, and anything already typed there is kept.
  function askRem(entries) {
    if (!entries.length) return;
    askRemAboutText({
      name: entries.length === 1 ? "raticode-log-entry.txt" : `raticode-log-${entries.length}-entries.txt`,
      text: logSelectionForRem(entries, { version, platform, logFile }),
    });
  }

  function toggleExpanded(key) {
    setExpanded((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  }

  // Scroll the list while a drag is held near its edge, extending the selection.
  function stopAutoScroll() { if (drag.current?.timer) { window.clearInterval(drag.current.timer); drag.current.timer = null; } }
  useEffect(() => {
    const end = () => { stopAutoScroll(); drag.current = null; };
    window.addEventListener("mouseup", end);
    window.addEventListener("blur", end);
    return () => { end(); window.removeEventListener("mouseup", end); window.removeEventListener("blur", end); };
  }, []);


  function extendTo(key) {
    if (!drag.current || !key || key === drag.current.last) return;
    drag.current.last = key;
    const range = keyRange(order, drag.current.from, key);
    select(drag.current.base ? [...new Set([...drag.current.base, ...range])] : range, { active: key });
  }

  function onMouseDown(event) {
    if (event.button !== 0) return;
    const key = rowKeyFrom(event.target);
    if (!key || ownsPointer(event.target)) return;
    event.preventDefault();
    listRef.current?.focus({ preventScroll: true });
    setMenu(null);
    if (event.shiftKey && anchor) { select(keyRange(order, anchor, key), { from: anchor, active: key }); return; }
    if (event.metaKey || event.ctrlKey) {
      const next = new Set(selected);
      if (next.has(key)) next.delete(key); else next.add(key);
      select([...next], { from: key, active: key });
      drag.current = { from: key, base: [...next], last: key };
      return;
    }
    select([key], { from: key, active: key });
    drag.current = { from: key, base: null, last: key };
  }

  function onMouseMove(event) {
    if (!drag.current) return;
    if (!(event.buttons & 1)) { stopAutoScroll(); drag.current = null; return; }
    extendTo(rowKeyFrom(event.target));
    const list = listRef.current;
    const box = list?.getBoundingClientRect?.();
    if (!box) return;
    const speed = event.clientY < box.top + EDGE ? -12 : event.clientY > box.bottom - EDGE ? 12 : 0;
    drag.current.speed = speed;
    drag.current.x = event.clientX;
    drag.current.y = Math.min(Math.max(event.clientY, box.top + 2), box.bottom - 2);
    if (!speed) { stopAutoScroll(); return; }
    if (!drag.current.timer) {
      drag.current.timer = window.setInterval(() => {
        if (!drag.current) return;
        list.scrollTop += drag.current.speed;
        extendTo(rowKeyFrom(document.elementFromPoint?.(drag.current.x, drag.current.y)));
      }, 30);
    }
  }

  function openMenu(event) {
    const key = rowKeyFrom(event.target);
    if (!key) return;
    event.preventDefault();
    if (!selected.has(key)) select([key], { from: key, active: key });
    setMenu({ x: event.clientX, y: event.clientY, key });
  }

  function openMenuForKeyboard() {
    const key = activeKey && byKey.has(activeKey) ? activeKey : order.find((row) => selected.has(row)) || order[0];
    if (!key) return;
    if (!selected.has(key)) select([key], { from: key, active: key });
    const box = document.getElementById(rowId(key))?.getBoundingClientRect?.() || listRef.current?.getBoundingClientRect?.() || { left: 0, top: 0 };
    setMenu({ x: box.left + 24, y: box.top + 16, key });
  }

  function onKeyDown(event) {
    const mod = event.metaKey || event.ctrlKey;
    if (mod && event.key.toLowerCase() === "a") { event.preventDefault(); select(order, { from: order[0], active: order[0] }); return; }
    if (mod && event.key.toLowerCase() === "c") { if (chosen.length) { event.preventDefault(); void copy(chosen); } return; }
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); openMenuForKeyboard(); return; }
    if (event.key === "Escape" && selected.size) { event.preventDefault(); event.stopPropagation(); clearSelection(); return; }
    const current = activeKey && order.includes(activeKey) ? order.indexOf(activeKey) : -1;
    if (event.key === "Enter" && current !== -1) { event.preventDefault(); toggleExpanded(activeKey); return; }
    if (event.key === " " && current !== -1) {
      event.preventDefault();
      const next = new Set(selected);
      if (next.has(activeKey)) next.delete(activeKey); else next.add(activeKey);
      select([...next], { from: activeKey, active: activeKey });
      return;
    }
    const moves = { ArrowDown: current + 1, ArrowUp: Math.max(0, current - 1), Home: 0, End: order.length - 1, PageDown: current + 10, PageUp: Math.max(0, current - 10) };
    if (!(event.key in moves) || !order.length) return;
    event.preventDefault();
    const key = order[Math.min(order.length - 1, Math.max(0, moves[event.key]))];
    if (event.shiftKey) select(keyRange(order, anchor || key, key), { from: anchor || key, active: key });
    else select([key], { from: key, active: key });
    document.getElementById(rowId(key))?.scrollIntoView?.({ block: "nearest" });
  }

  function menuAction(kind) {
    setMenu(null);
    if (kind === "copy") void copy(chosen);
    else if (kind === "ask") askRem(chosen);
    else if (kind === "all") select(order, { from: order[0], active: order[0] });
    else if (kind === "clear") clearSelection();
  }

  function resetView() { setShown(PAGE); }

  function renderRow({ key, entry }, { repeats = 0, oldest = null, member = false } = {}) {
    return <LogRow key={key} rowKey={key} entry={entry} rowId={rowId(key)} selected={selected.has(key)} active={activeKey === key}
      expanded={expanded.has(key)} onToggle={() => toggleExpanded(key)} repeats={repeats} oldest={oldest} member={member} />;
  }

  function renderGroup(group) {
    if (group.entries.length === 1) return renderRow(group);
    const open = expanded.has(group.key);
    return <div key={group.key} className="app-log-repeats">
      {renderRow(group, { repeats: group.entries.length, oldest: group.entries.at(-1) })}
      {open ? <div role="group" aria-label={`Each of the ${group.entries.length} entries`}>
        {group.entries.map((entry) => renderRow({ key: entryKey(entry), entry }, { member: true }))}
      </div> : null}
    </div>;
  }

  if (!bridge?.entries) return null;
  const n = chosen.length;
  return <section aria-labelledby="app-log-heading" className="app-log space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <h3 id="app-log-heading" className="mr-auto text-xs font-semibold">Application log</h3>
      <button type="button" className="app-log-button" aria-pressed={pausedAt === null} onClick={() => { autoPaused.current = false; setPausedAt(pausedAt === null ? (live.at(-1)?.seq ?? 0) : null); }}>
        <span aria-hidden="true" className={pausedAt === null ? "app-log-live-dot" : "app-log-paused-dot"} />
        {pausedAt === null ? "Following" : waiting ? `Paused, ${waiting} new` : "Paused"}
      </button>
      <button type="button" className="app-log-button" disabled={!total} onClick={() => void copy([...matchingLive, ...matchingHistory], "shown")}>Copy shown</button>
    </div>
    <p className="text-[11px] text-muted">Messages from the desktop app, its backend and phone connections as they happen, newest first. The same message repeated in a row shows once with a count; click the count to see each entry. Drag across rows to select them, then right-click to copy them or ask Rem.</p>
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Levels to show">
      {LOG_LEVELS.map(({ id, label }) => <button key={id} type="button" title={`Show ${label.toLowerCase()}`} aria-pressed={levels[id]} className={`app-log-chip app-log-chip-${id}`} onClick={() => { setLevels({ ...levels, [id]: !levels[id] }); resetView(); }}>
        {label}<span className="app-log-count">{counts[id]}</span>
      </button>)}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-[11px] text-muted">Source
        <select className="app-log-field" value={source} onChange={(event) => { setSource(event.target.value); resetView(); }}>
          <option value="all">All sources</option>
          {sources.map((id) => <option key={id} value={id}>{sourceName(id)}</option>)}
        </select>
      </label>
      <input type="search" aria-label="Search the log" placeholder="Search messages" className="app-log-field min-w-0 flex-1" value={query} onChange={(event) => { setQuery(event.target.value); resetView(); }} />
    </div>
    {error ? <p role="alert" className="text-xs text-red-700">{error}</p> : null}
    {missed ? <p className="app-log-note">{missed} {missed === 1 ? "entry" : "entries"} arrived faster than this view could keep them. They are in the log file.</p> : null}
    <div className="app-log-selection" aria-live="polite">
      {n ? <>
        <span className="mr-auto font-semibold">{count(n)} selected</span>
        <button type="button" className="app-log-button" onClick={() => void copy(chosen)}>Copy</button>
        <button type="button" className="app-log-button" onClick={() => askRem(chosen)}>Ask Rem</button>
        <button type="button" className="app-log-button" onClick={clearSelection}>Clear</button>
      </> : notice ? <span>{notice}</span> : null}
      {n && notice ? <span className="sr-only">{notice}</span> : null}
    </div>
    <div ref={listRef} className="app-log-list" tabIndex={0} role="listbox" data-settings-keep-open={selected.size ? "true" : undefined} aria-multiselectable="true" aria-label="Application log entries"
      aria-activedescendant={activeKey && byKey.has(activeKey) ? rowId(activeKey) : undefined}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove} onContextMenu={openMenu} onKeyDown={onKeyDown}
      onDoubleClick={(event) => { const key = rowKeyFrom(event.target); if (key && !ownsPointer(event.target)) toggleExpanded(key); }}>
      {total === 0 ? <p className="app-log-empty">{live.length || earlier.length ? "No entries match these filters." : "No log entries yet."}</p> : null}
      {liveShown.length ? <div role="group" aria-label="This session">{liveShown.map(renderGroup)}</div> : null}
      {historyShown.length ? <div role="group" aria-labelledby="app-log-earlier">
        <p id="app-log-earlier" className="app-log-divider">Before this launch{history?.truncated ? " (most recent part of the log file)" : ""}</p>
        {historyShown.map(renderGroup)}
      </div> : null}
    </div>
    {menu ? <WorktreeContextMenu source="log entries" label={`Actions for ${count(n || 1)}`} layer="z-[130]" x={menu.x} y={menu.y} trigger={listRef.current} onClose={() => setMenu(null)} onSelect={menuAction}
      operations={[["copy", n > 1 ? `Copy ${count(n)}` : "Copy entry"], ["ask", n > 1 ? `Ask Rem about ${count(n)}` : "Ask Rem about this entry"], ["all", "Select all shown", !order.length], ["clear", "Clear selection", !n]]} /> : null}
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
      <span className="mr-auto">{more ? `Showing the newest ${shownEntries} of ${total} matching entries.` : `${total} matching ${total === 1 ? "entry" : "entries"}.`}</span>
      {more ? <button type="button" className="app-log-button" onClick={() => setShown(shown + PAGE)}>Show {PAGE} more</button> : null}
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={showHistory} onChange={(event) => setShowHistory(event.target.checked)} />Include earlier launches</label>
    </div>
    <details className="app-log-about text-[11px] text-muted">
      <summary className="cursor-pointer text-ink">What this log includes</summary>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        <li>Included: desktop app warnings and errors, backend messages at every level, phone and paired-desktop connections, window errors and crashes, and built-in browser problems.</li>
        <li>Debug is hidden by default. It adds detail such as connections that closed before identifying themselves.</li>
        <li>Left out on purpose: message and conversation content, pairing codes, keys and phone addresses. Tokens, passwords and other credentials are replaced with [redacted]. An error&apos;s details can still quote a file path or a value from the step that failed, so read entries before sharing them.</li>
        <li>Ask Rem attaches the selected entries to Rem&apos;s message box and puts the cursor there for your question; nothing is sent until you send it. Copy puts the same text on the clipboard. Selecting a repeated message takes in every entry it counts, each with its own time.</li>
        <li>Not included: Electron and Chromium&apos;s own native messages, such as GPU or graphics warnings. They only reach a terminal. On a Mac, quit Raticode and start it from Terminal with <code>/Applications/Raticode.app/Contents/MacOS/Raticode</code> to see them.</li>
        <li>Phone problems that never reach this computer, such as a wrong Wi-Fi network or a firewall that blocks the connection, appear only on the phone. If the phone times out and nothing appears here, check the computer&apos;s firewall (on a Mac: System Settings › Network › Firewall).</li>
        <li>Kept: the newest 5,000 entries of this session in memory, and up to four 5 MB files on disk{logFile ? <> at <span className="select-text break-all">{logFile}</span></> : null}.</li>
      </ul>
    </details>
  </section>;
}
