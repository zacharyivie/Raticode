import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { messageStamp } from "../lib/remHome.js";
import { searchRemHistory } from "../lib/remHomeStore.js";
import { MAX_QUERY, createRemSearch, partsText, resultLabel, searchSummary } from "../lib/remSearch.js";

// Search Rem's whole saved chat from the chat header. The panel covers the top of the
// conversation without replacing it, so closing it leaves the chat where it was and
// the message box keeps its draft. Choosing a result jumps to that message.
export default function RemChatSearch({ now, onJump, onClose }) {
  const [view, setView] = useState(() => ({ query: "", status: "idle", total: 0, results: [], next: null, error: "", loadingMore: false }));
  const searchRef = useRef(null);
  if (!searchRef.current) searchRef.current = createRemSearch({ search: (query, before) => searchRemHistory(query, { before }), onChange: setView });
  const inputRef = useRef(null);
  const listRef = useRef(null);
  useEffect(() => {
    inputRef.current?.focus();
    const search = searchRef.current;
    return () => search.dispose();
  }, []);
  const results = view.results;
  const summary = searchSummary(view);
  const resultButtons = () => [...(listRef.current?.querySelectorAll?.("[data-search-result]") || [])];
  const focusResult = index => resultButtons()[index]?.focus();
  const onKeyDown = event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    const items = resultButtons();
    const at = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" && items.length) { event.preventDefault(); focusResult(Math.min(items.length - 1, at + 1)); }
    else if (event.key === "ArrowUp" && at >= 0) { event.preventDefault(); if (at === 0) inputRef.current?.focus(); else focusResult(at - 1); }
    else if (event.key === "Enter" && event.target === inputRef.current && results[0]) { event.preventDefault(); onJump(results[0].id, view.query); }
  };
  return <div className="rem-search" role="search" aria-label="Chat search" onKeyDown={onKeyDown}>
    <div className="rem-search-bar">
      <Search size={15} aria-hidden="true" />
      <input ref={inputRef} type="search" value={view.query} maxLength={MAX_QUERY} placeholder="Search messages, task updates and file names"
        aria-label="Search Rem's chat" aria-describedby="rem-search-summary" autoComplete="off" spellCheck={false}
        onChange={event => searchRef.current.setQuery(event.target.value)} />
      {view.query ? <button type="button" className="rem-search-clear" aria-label="Clear search" title="Clear search"
        onClick={() => { searchRef.current.setQuery(""); inputRef.current?.focus(); }}><X size={14} aria-hidden="true" /></button> : null}
      <button type="button" className="rem-search-close" onClick={onClose}>Done</button>
    </div>
    <p id="rem-search-summary" className="rem-search-summary" role="status" aria-live="polite">{summary}</p>
    {view.status === "error" ? <div className="rem-search-state" role="alert">
      <span>{view.error}</span>
      <button type="button" onClick={() => searchRef.current.retry()}>Try again</button>
    </div> : null}
    {view.status === "idle" ? <p className="rem-search-state">Finds words in your whole conversation with Rem, newest first.</p> : null}
    {view.status === "ready" && !view.total ? <p className="rem-search-state">Nothing in Rem’s chat matches “{view.query.trim()}”.</p> : null}
    {results.length ? <ol ref={listRef} className="rem-search-results" aria-label="Search results" data-busy={view.status === "loading" || undefined}>
      {results.map(result => <li key={result.id}><SearchResult result={result} now={now} onJump={id => onJump(id, view.query)} /></li>)}
    </ol> : null}
    {view.next != null && view.status === "ready" ? <button type="button" className="rem-search-more" disabled={view.loadingMore} onClick={() => searchRef.current.more()}>
      {view.loadingMore ? "Loading…" : "Show more results"}
    </button> : null}
    {view.error && view.status === "ready" ? <p className="rem-search-state" role="alert">{view.error}</p> : null}
  </div>;
}

function SearchResult({ result, now, onJump }) {
  const { who, where } = resultLabel(result);
  const stamp = messageStamp(result.created_at, now);
  const title = result.title?.length ? partsText(result.title) : "";
  const label = [who, title, partsText(result.snippet), where, stamp?.full].filter(Boolean).join(". ");
  return <button type="button" className="rem-search-result" data-search-result data-role={result.role} aria-label={label} onClick={() => onJump(result.id)}>
    <span className="rem-search-meta">
      <strong>{who}</strong>
      {title ? <span className="rem-search-title"><Highlighted parts={result.title} /></span> : null}
      {stamp ? <time dateTime={stamp.iso}>{stamp.label}</time> : null}
    </span>
    <span className="rem-search-snippet"><Highlighted parts={result.snippet} /></span>
    {where ? <span className="rem-search-where">{where}</span> : null}
  </button>;
}

function Highlighted({ parts = [] }) {
  return parts.map((part, index) => part.match ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>);
}
