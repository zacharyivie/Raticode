/* Rem thread search extends the existing compact chat header. Indigo focus,
   flat pane borders and familiar rows keep search beside the conversation.
   The search button expands a focused field; results show titles and evidence
   from saved messages across every thread, with explicit loading and errors. */
import { useEffect, useRef, useState } from "react";
import { Loader2, Search, X } from "lucide-react";

export default function ThreadSearch({ repository, loadThreads, onOpen, onClose }) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState({ results: [], loading: false, error: "", hasMore: false });
  const [limit, setLimit] = useState(30);
  const inputRef = useRef(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    let active = true;
    if (!query.trim()) { setState({ results: [], loading: false, error: "", hasMore: false }); return; }
    setState({ results: [], loading: true, error: "", hasMore: false });
    const timer = setTimeout(async () => {
      try {
        const result = await repository.search(loadThreads(), query, { limit });
        if (active) setState({ ...result, loading: false, error: "" });
      } catch (error) {
        if (active) setState({ results: [], loading: false, error: error.message || "Thread search failed. Try again.", hasMore: false });
      }
    }, 180);
    return () => { active = false; clearTimeout(timer); };
  }, [query, limit, repository, loadThreads]);
  return (
    <section aria-label="Search threads" className="rem-thread-search shrink-0 border-b border-line px-3.5 py-3">
      <div className="flex items-center gap-2 rounded-md border border-line bg-white px-2 focus-within:ring-2 focus-within:ring-brand">
        <Search aria-hidden="true" size={15} className="shrink-0 text-muted" />
        <input ref={inputRef} aria-label="Search all thread history" className="studio-search-input h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-muted" placeholder="Search all threads…" type="search" value={query}
          onChange={event => { setQuery(event.target.value); setLimit(30); }} onKeyDown={event => { if (event.key === "Escape") onClose(); }} />
        <button aria-label="Close thread search" className="studio-icon-button grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:text-ink" onClick={onClose} type="button"><X aria-hidden="true" size={15} /></button>
      </div>
      <div aria-live="polite" className="pt-2 text-[11px] text-muted">
        {state.loading ? <span className="flex items-center gap-2" role="status"><Loader2 aria-hidden="true" size={13} className="motion-safe:animate-spin" />Searching saved history…</span> : state.error ? <span role="alert">{state.error}</span> : !query.trim() ? "Search titles and the full text of saved messages." : !state.results.length ? "No threads match your search." : `${state.results.length}${state.hasMore ? "+" : ""} matching ${state.results.length === 1 && !state.hasMore ? "thread" : "threads"}`}
      </div>
      {state.results.length ? <ul aria-label="Thread search results" className="workflow-scrollbar mt-2 max-h-64 overflow-y-auto divide-y divide-line">
        {state.results.map(({ thread, snippet, messageId }) => <li key={thread.id}><button className="w-full rounded px-2 py-2 text-left hover:bg-slate-50 focus-visible:outline-brand" type="button" onClick={() => onOpen(thread.id, { messageId, query: query.trim() })}>
          <span className="block truncate text-xs font-semibold text-ink">{thread.title}</span>
          <span className="mt-1 block line-clamp-2 break-words text-[11px] leading-4 text-muted">{snippet}</span>
          <span className="mt-1 block truncate text-[10px] text-muted">{thread.projectName || thread.projectRoot || "No project"}</span>
        </button></li>)}
      </ul> : null}
      {state.hasMore ? <button className="mt-2 rounded px-2 py-1 text-xs text-brand focus-visible:outline-brand" type="button" onClick={() => setLimit(value => value + 30)}>Show more matches</button> : null}
    </section>
  );
}
